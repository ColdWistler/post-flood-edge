"""Safety checks and artifact validation for tenant provisioning.

Covers both the version-compatible device setup used by the dashboard workstream
and the committed Cloud Track artifacts used by the benchmarking and alarm work.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from cloud import provision
from cloud.provision import (
    Provisioner,
    ensure_device,
    ensure_profile,
    redact_sensitive,
)

ARTIFACTS = Path(__file__).resolve().parents[1] / "cloud" / "artifacts"


class FakeTenant:
    def __init__(self):
        self.calls = []
        self.profiles = [{"id": {"id": "default-id"}, "name": "default", "default": True}]
        self.devices = []

    def pages(self, path):
        if path == "/api/deviceProfiles":
            return iter(self.profiles)
        if path == "/api/tenant/devices":
            return iter(self.devices)
        raise AssertionError(path)

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        if (method, path) == ("GET", "/api/deviceProfile/default-id"):
            return {
                "id": {"id": "default-id"}, "tenantId": {"id": "tenant-id"},
                "name": "default", "default": True,
                "profileData": {"transportConfiguration": {"type": "DEFAULT"}},
            }
        if (method, path) == ("POST", "/api/deviceProfile"):
            assert "id" not in body and "tenantId" not in body and "default" not in body
            return {"id": {"id": "new-profile"}, **body}
        if (method, path) == ("POST", "/api/device"):
            return {"id": {"id": "new-device"}, **body}
        raise AssertionError((method, path))


def test_profile_clone_does_not_overwrite_default():
    tenant = FakeTenant()
    created = ensure_profile(tenant)
    assert created["id"]["id"] == "new-profile"
    assert created["profileData"]["transportConfiguration"]["type"] == "DEFAULT"
    assert created["profileData"]["alarms"] == []


def test_existing_device_under_other_profile_is_not_changed():
    tenant = FakeTenant()
    tenant.devices = [{
        "id": {"id": "existing"}, "name": "node-a",
        "deviceProfileId": {"id": "someone-elses-profile"},
    }]
    with pytest.raises(RuntimeError, match="another profile"):
        ensure_device(tenant, "node-a", "new-profile")
    assert not any(method == "POST" for method, _, _ in tenant.calls)


def test_snapshot_redacts_nested_credentials():
    snapshot = redact_sensitive({
        "name": "Post-Flood Water Source",
        "profileData": {"provisionConfiguration": {
            "provisionDeviceKey": "key-value", "provisionDeviceSecret": "secret-value",
        }},
    })
    assert "key-value" not in str(snapshot)
    assert "secret-value" not in str(snapshot)
    assert snapshot["name"] == "Post-Flood Water Source"


def test_fill_tokens_substitutes_all_three_nodes():
    env = {
        "TB_TOKEN_NODE_A": "token-a",
        "TB_TOKEN_NODE_B": "token-b",
        "TB_TOKEN_NODE_C": "token-c",
    }
    filled = provision.fill_tokens(
        {"devices": [{"name": "node-a", "accessToken": "${TB_TOKEN_NODE_A}"}]}, env
    )
    assert filled["devices"][0]["accessToken"] == "token-a"


def test_fill_tokens_refuses_missing_credentials():
    with pytest.raises(ValueError, match="missing access tokens"):
        provision.fill_tokens({"devices": []}, {})
    with pytest.raises(ValueError, match="missing access tokens: TB_TOKEN_NODE_C"):
        provision.fill_tokens(
            {"devices": []},
            {
                "TB_TOKEN_NODE_A": "a",
                "TB_TOKEN_NODE_B": "b",
                "TB_TOKEN_NODE_C": "",
            },
        )


def test_devices_artifact_matches_edge_contract():
    devices = json.loads((ARTIFACTS / "devices.json").read_text())["devices"]
    assert [device["name"] for device in devices] == ["node-a", "node-b", "node-c"]
    for device in devices:
        assert device["type"] == "post-flood-water-quality"
        assert device["accessToken"].startswith("${TB_TOKEN_")


def test_alarm_rules_match_integration_thresholds():
    rules = json.loads((ARTIFACTS / "alarm_rules.json").read_text())["rules"]
    by_name = {rule["name"]: rule for rule in rules}
    assert len(by_name) == 9
    assert by_name["High Turbidity"]["raise"] == "turbidity_ntu > 50"
    assert by_name["High Turbidity"]["clear"] == "turbidity_ntu < 40"
    assert by_name["Gross Contamination"]["clear"] == "turbidity_ntu < 150"
    assert by_name["pH Out of Range"]["debounce"] == {
        "raise_samples": 3,
        "clear_samples": 3,
    }
    assert by_name["Sensor Fault"]["severity"] == "Fault"


def test_wqi_script_uses_task2_constants():
    script = (ARTIFACTS / "wqi_server_script.js").read_text()
    for fragment in ("0.3692", "0.6277", "0.0031", "server_received_ms", "new Date().getTime()"):
        assert fragment in script


def test_device_profile_uses_thingsboard_v3_alarm_format():
    profile = json.loads((ARTIFACTS / "device_profile.json").read_text())
    assert profile["type"] == "DEFAULT"
    assert profile["transportType"] == "DEFAULT"
    alarms = profile["profileData"]["alarms"]
    assert len(alarms) == 9
    severities = {item["alarmType"]: next(iter(item["createRules"])) for item in alarms}
    assert severities == {
        "High Turbidity": "WARNING",
        "Gross Contamination": "CRITICAL",
        "pH Out of Range": "CRITICAL",
        "High TDS": "CRITICAL",
        "Supply Interruption": "MAJOR",
        "Node Inactive": "CRITICAL",
        "Rapid Turbidity Rise": "WARNING",
        "Severe pH Persistence": "CRITICAL",
        "Sensor Fault": "CRITICAL",
    }
    for rule in alarms:
        assert rule["clearRule"]["condition"]["condition"]
        assert "script" not in json.dumps(rule)
    mirrors = [
        rule for rule in alarms if rule["alarmType"] in (
            "Rapid Turbidity Rise", "Severe pH Persistence", "Sensor Fault"
        )
    ]
    assert all(
        item["createRules"][next(iter(item["createRules"]))]["condition"]["condition"][0]
        ["predicate"]["operation"] == "CONTAINS"
        for item in mirrors
    )


def test_rule_chain_is_thingsboard_v3_importable():
    chain = json.loads((ARTIFACTS / "wqi_rule_chain.json").read_text())
    assert set(chain) == {"ruleChain", "metadata"}
    assert chain["ruleChain"]["type"] == "CORE"
    nodes = chain["metadata"]["nodes"]
    assert [node["type"] for node in nodes] == [
        "org.thingsboard.rule.engine.filter.TbJsFilterNode",
        "org.thingsboard.rule.engine.transform.TbTransformMsgNode",
        "org.thingsboard.rule.engine.telemetry.TbMsgTimeseriesNode",
    ]
    assert nodes[1]["configuration"]["scriptLang"] == "JS"
    assert "wqi_server" in nodes[1]["configuration"]["jsScript"]
    assert nodes[2]["configuration"]["useServerTs"] is False
    assert chain["metadata"]["connections"] == [
        {"fromIndex": 0, "toIndex": 1, "type": "True"},
        {"fromIndex": 1, "toIndex": 2, "type": "Success"},
    ]


def test_load_dashboard_prefers_eight_widget_triage():
    dashboard, name = provision.load_dashboard()
    assert name == provision.DASHBOARD_ARTIFACT
    assert len(dashboard["configuration"]["widgets"]) == 8


def test_triage_dashboard_has_eight_native_widgets():
    dashboard, _ = provision.load_dashboard()
    configuration = dashboard["configuration"]
    widgets = configuration["widgets"]
    placements = configuration["states"]["default"]["layouts"]["main"]["widgets"]
    assert set(widgets) == set(placements)
    assert len(widgets) == 8
    assert configuration["timewindow"]["aggregation"]["type"] == "NONE"
    assert configuration["timewindow"]["realtime"]["timewindowMs"] == 24 * 60 * 60 * 1000
    assert set(configuration["entityAliases"]) == {
        "postflood-fleet",
        "postflood-node-a",
    }

    fqns = {widget["typeFullFqn"] for widget in widgets.values()}
    assert fqns == {
        "system.analogue_gauges.radial_gauge_canvas_gauges",
        "system.digital_gauges.gauge_justgage",
        "system.line_chart",
        "system.map",
        "system.alarm_widgets.alarms_table",
        "system.cards.entities_table",
    }

    by_fqn: dict[str, list[dict]] = {}
    for widget in widgets.values():
        by_fqn.setdefault(widget["typeFullFqn"], []).append(widget)

    assert len(by_fqn["system.analogue_gauges.radial_gauge_canvas_gauges"]) == 2
    assert len(by_fqn["system.digital_gauges.gauge_justgage"]) == 1
    assert len(by_fqn["system.line_chart"]) == 2
    for gauge in (
        by_fqn["system.analogue_gauges.radial_gauge_canvas_gauges"]
        + by_fqn["system.digital_gauges.gauge_justgage"]
    ):
        datasources = gauge["config"]["datasources"]
        assert len(datasources) == 1
        assert datasources[0]["entityAliasId"] == "postflood-node-a"
        assert len(datasources[0]["dataKeys"]) == 1

    alarm_table = by_fqn["system.alarm_widgets.alarms_table"][0]
    assert alarm_table["type"] == "alarm"
    alarm_source = alarm_table["config"]["alarmSource"]
    assert alarm_source["type"] == "entity"
    assert alarm_source["entityAliasId"] == "postflood-fleet"
    assert alarm_source["dataKeys"]

    entity_table = by_fqn["system.cards.entities_table"][0]
    assert entity_table["config"]["datasources"][0]["entityAliasId"] == "postflood-fleet"

    node_map = by_fqn["system.map"][0]
    assert node_map["config"]["datasources"] == []
    settings = node_map["config"]["settings"]
    assert settings["fitMapBounds"] is True
    assert settings["useDefaultCenterPosition"] is True
    assert settings["defaultCenterPosition"] == "27.7172,85.3240"
    markers = settings["markers"]
    assert len(markers) == 3
    assert {marker["dsLabel"] for marker in markers} == {"node-a", "node-b", "node-c"}
    assert {marker["dsDeviceId"] for marker in markers} == {
        "81e5b1f0-e883-11f1-ab4e-dfd4fe9de952",
        "01f214e0-e886-11f1-b8ac-4bd49425d19e",
        "0bb52a80-e886-11f1-86e5-9755011de358",
    }
    for marker in markers:
        assert marker["dsType"] == "device"
        assert marker["dsEntityAliasId"] is None
        assert marker["xKey"]["name"] == "latitude"
        assert marker["xKey"]["label"] == "latitude"
        assert marker["xKey"]["type"] == "timeseries"
        assert marker["yKey"]["name"] == "longitude"
        assert marker["yKey"]["label"] == "longitude"
        assert marker["yKey"]["type"] == "timeseries"
        assert {key["name"] for key in marker["additionalDataKeys"]} >= {
            "severity",
            "wqi",
            "turbidity_ntu",
            "ph",
            "tds_ppm",
            "flow_lpm",
        }
        color_function = marker["markerShape"]["color"]["colorFunction"]
        assert "data['severity']" in color_function
        assert "turbidity" in color_function
        assert "Gross Contamination" in color_function

    for chart in by_fqn["system.line_chart"]:
        datasources = chart["config"]["datasources"]
        assert len(datasources) == 3
        assert {item["name"] for item in datasources} == {"node-a", "node-b", "node-c"}
        for datasource in datasources:
            assert datasource["type"] == "device"
            assert datasource["deviceId"]
            assert datasource["dataKeys"]


def test_cloud_dashboard_widgets_are_bound_to_devices_and_layout():
    """The six-chart Cloud export keeps direct device bindings in every widget."""
    dashboard = provision.load_json(provision.CLOUD_DASHBOARD_ARTIFACT)
    configuration = dashboard["configuration"]
    widgets = configuration["widgets"]
    placements = configuration["states"]["default"]["layouts"]["main"]["widgets"]
    assert set(widgets) == set(placements)
    assert len(widgets) >= 6
    for widget in widgets.values():
        assert widget["typeFullFqn"] == "system.line_chart"
        datasources = widget["config"]["datasources"]
        assert [item["name"] for item in datasources] == ["node-a", "node-b", "node-c"]
        assert len({item["deviceId"] for item in datasources}) == 3
        assert all(item["deviceId"] for item in datasources)
        assert widget["config"]["timewindow"]["aggregation"]["type"] == "NONE"
        assert widget["config"]["timewindow"]["realtime"]["timewindowMs"] == 86400000


def test_verified_cloud_dashboard_fallback_has_six_charts():
    dashboard = provision.load_json(provision.CLOUD_DASHBOARD_ARTIFACT)
    widgets = dashboard["configuration"]["widgets"]
    assert len(widgets) == 6
    assert {widget["typeFullFqn"] for widget in widgets.values()} == {"system.line_chart"}


class RecordingClient:
    """Capture REST calls without network access."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.dashboard: dict[str, Any] | None = None

    def request(self, method: str, path: str, payload: dict | None = None):
        self.calls.append((method, path, payload))
        if path.startswith("/api/deviceProfiles?"):
            return {"data": []}
        if path == "/api/deviceProfile":
            return {"id": {"id": "profile-1"}}
        if path.startswith("/api/tenant/devices?"):
            return {}
        if path == "/api/device":
            return {"id": {"id": "device-a"}}
        if path == "/api/ruleChains":
            return {"data": []}
        if path == "/api/ruleChain":
            return {"id": {"id": "chain-1"}}
        return {}


def test_provisioner_uses_v3_endpoints_and_sets_device_credentials():
    client = RecordingClient()
    p = Provisioner(client)
    profile_id = p.device_profile(
        json.loads((ARTIFACTS / "device_profile.json").read_text())
    )
    assert profile_id == "profile-1"
    p.devices(
        {
            "devices": [
                {"name": "node-a", "type": "water-quality-node", "accessToken": "token-a"}
            ]
        },
        profile_id,
        10800000,
    )
    p.dashboard(provision.load_dashboard()[0])
    p.rule_chain(json.loads((ARTIFACTS / "wqi_rule_chain.json").read_text()), True)

    paths = [path.split("?")[0] for _, path, _ in client.calls]
    assert "/api/device/device-a/credentials" in paths
    assert "/api/plugins/telemetry/DEVICE/device-a/attributes/SERVER_SCOPE" in paths
    assert "/api/dashboard" in paths
    assert "/api/ruleChain/metadata" in paths
    assert "/api/ruleChain/chain-1/root" in paths
    metadata_body = next(
        payload for method, path, payload in client.calls
        if path == "/api/ruleChain/metadata"
    )
    assert metadata_body["ruleChainId"]["id"] == "chain-1"
