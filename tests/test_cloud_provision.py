"""Tests for provisioning helpers and the committed Cloud Track artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cloud import provision
from cloud.provision import Provisioner

ARTIFACTS = Path(__file__).resolve().parents[1] / "cloud" / "artifacts"


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


def test_dashboard_widgets_are_bound_to_alias_and_layout():
    dashboard = json.loads((ARTIFACTS / "dashboard.json").read_text())
    configuration = dashboard["configuration"]
    alias_id = next(iter(configuration["entityAliases"]))
    assert configuration["entityAliases"][alias_id]["filter"]["entityNameFilter"] == "node-"
    widgets = configuration["widgets"]
    placements = configuration["states"]["default"]["layouts"]["main"]["widgets"]
    assert set(widgets) == set(placements)
    assert len(widgets) >= 8
    fqns = {item["typeFullFqn"] for item in widgets.values()}
    assert "system.maps_v2.openstreetmap" in fqns
    assert "system.alarm_widgets.alarms_table" in fqns
    for widget in widgets.values():
        for datasource in widget["config"]["datasources"]:
            assert datasource["entityAliasId"] == alias_id


class RecordingClient:
    """Capture REST calls without network access."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []

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
    p.dashboard(json.loads((ARTIFACTS / "dashboard.json").read_text()))
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
