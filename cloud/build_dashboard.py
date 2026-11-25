"""Build and install Anushree's ThingsBoard operational dashboard.

Uses widget definitions returned by the target tenant, avoiding a hard-coded
version-specific widget schema. The output is an inspectable ThingsBoard JSON
configuration. The script does not create alarms (Sambid's responsibility).

Run: python -m cloud.build_dashboard --apply
Requires the ignored .thingsboard-auth file created for cloud.provision.
"""

from __future__ import annotations

import argparse
import copy
import json
import uuid
from pathlib import Path

from cloud.provision import DEFAULT_URL, REPO_ROOT, ThingsBoard
from cloud.tune_dashboard import tune


TITLE = "Post-Flood Water Sources - Duty Officer"
COLORS = {
    "ph": "#2563eb", "turbidity_ntu": "#c2410c", "tds_ppm": "#7c3aed",
    "water_temp_c": "#0891b2", "flow_lpm": "#16a34a", "severity": "#dc2626",
    "buffer_depth": "#d97706", "active": "#64748b", "lastActivityTime": "#334155",
}


def uid() -> str:
    return str(uuid.uuid4())


def data_key(name: str, label: str | None = None, kind: str = "timeseries") -> dict:
    return {
        "name": name, "type": kind, "label": label or name,
        "color": COLORS.get(name, "#475569"), "settings": {},
        "_hash": 0.5,
    }


def datasource(alias: str, keys: list[dict]) -> dict:
    return {"type": "entity", "name": None, "entityAliasId": alias,
            "filterId": None, "dataKeys": keys}


def nav_action(target: str, name: str = "Open source") -> dict:
    return {
        "id": uid(), "name": name, "icon": "open_in_new",
        "type": "openDashboardState", "targetDashboardStateId": target,
        "setEntityId": target == "source", "stateEntityParamName": None,
        "openInSeparateDialog": False, "dialogTitle": "",
        "dialogHideDashboardToolbar": False, "openRightLayout": False,
    }


def widget(tb: ThingsBoard, fqn: str, title: str, alias: str | None = None,
           keys: list[dict] | None = None) -> dict:
    definition = tb.request("GET", "/api/widgetType?fqn=system." + fqn)
    descriptor = definition["descriptor"]
    config = json.loads(descriptor["defaultConfig"])
    config["title"] = title
    config["showTitle"] = True
    config["backgroundColor"] = "#ffffff"
    if alias and keys is not None:
        config["datasources"] = [datasource(alias, keys)]
    return {
        "id": uid(), "type": descriptor["type"],
        "typeFullFqn": "system." + fqn,
        "sizeX": descriptor.get("sizeX", 6),
        "sizeY": descriptor.get("sizeY", 4),
        "row": 0, "col": 0, "config": config,
    }


def add_widget(config: dict, state: str, item: dict, col: int, row: int,
               width: int, height: int) -> None:
    item.update(sizeX=width, sizeY=height, row=row, col=col)
    wid = item["id"]
    config["widgets"][wid] = item
    config["states"][state]["layouts"]["main"]["widgets"][wid] = {
        "sizeX": width, "sizeY": height, "row": row, "col": col,
    }


def grid() -> dict:
    return {"widgets": {}, "gridSettings": {
        "backgroundColor": "#f1f5f9", "columns": 24, "margin": 10,
        "backgroundSizeMode": "100%", "autoFillHeight": True,
        "mobileAutoFillHeight": True, "mobileRowHeight": 70,
        "outerMargin": True,
    }}


def make_chart(tb: ThingsBoard, title: str, alias: str,
               series: list[tuple[str, str, str, str]]) -> dict:
    chart = widget(tb, "time_series_chart", title, alias,
                   [data_key(key, label) for key, label, _, _ in series])
    settings = chart["config"]["settings"]
    axes = {}
    template = settings["yAxes"]["default"]
    for order, (key, label, unit, side) in enumerate(series):
        axis_id = "default" if order == 0 else f"axis{order}"
        axis = copy.deepcopy(template)
        axis.update(id=axis_id, order=order, label=f"{label} ({unit})",
                    units=unit, position=side, show=True,
                    showSplitLines=order == 0,
                    labelColor=COLORS[key], tickLabelColor=COLORS[key],
                    lineColor=COLORS[key])
        axes[axis_id] = axis
        key_config = chart["config"]["datasources"][0]["dataKeys"][order]
        key_config["settings"]["yAxisId"] = axis_id
        key_config["units"] = unit
        key_config["decimals"] = 2 if key == "ph" else 1
    settings["yAxes"] = axes
    settings["yAxis"]["label"] = axes["default"]["label"]
    settings["showLegend"] = True
    settings["legendConfig"]["showAvg"] = False
    settings["xAxis"]["label"] = "Acquisition time"
    chart["config"]["useDashboardTimewindow"] = True
    return chart


def make_gauge(tb: ThingsBoard, title: str, alias: str, key: str,
               minimum: int, maximum: int, bands: list[tuple[int, int, str]]) -> dict:
    gauge = widget(tb, "analogue_gauges.radial_gauge_canvas_gauges", title,
                   alias, [data_key(key, title)])
    settings = gauge["config"]["settings"]
    settings.update(minValue=minimum, maxValue=maximum,
                    highlights=[{"from": low, "to": high, "color": color}
                                for low, high, color in bands],
                    valueInt=2 if key == "ph" else 1)
    gauge["config"]["decimals"] = 2 if key == "ph" else 1
    return gauge


def make_tds(tb: ThingsBoard, alias: str) -> dict:
    gauge = widget(tb, "digital_gauges.simple_gauge_justgage",
                   "TDS (ppm) | 500 warning, 1000 critical", alias,
                   [data_key("tds_ppm", "TDS (ppm)")])
    gauge["config"]["settings"].update(
        minValue=0, maxValue=1500, decimals=0,
        levelColors=["#16a34a", "#d97706", "#dc2626"],
    )
    return gauge


def make_table(tb: ThingsBoard, alias: str) -> dict:
    columns = [
        data_key("severity", "Severity"),
        data_key("buffer_depth", "Buffer depth"),
        data_key("active", "Active", "attribute"),
        data_key("lastActivityTime", "Last activity", "attribute"),
    ]
    table = widget(tb, "cards.entities_table", "Fleet status - select a source", alias, columns)
    table["config"]["settings"].update(
        displayEntityName=True, entitiesTitle="Water sources",
        displayPagination=False, defaultPageSize=10,
    )
    table["config"]["actions"] = {"actionCellButton": [nav_action("source", "Details")]}
    return table


def make_map(tb: ThingsBoard, alias: str) -> dict:
    map_widget = widget(tb, "map", "Water-source map - click marker for details")
    config = map_widget["config"]
    config["datasources"] = []
    settings = config["settings"]
    marker = copy.deepcopy(settings["markers"][0])
    marker.update(dsType="entity", dsLabel="Water sources", dsDeviceId=None,
                  dsEntityAliasId=alias, dsFilterId=None)
    marker["xKey"] = data_key("latitude", "Latitude")
    marker["yKey"] = data_key("longitude", "Longitude")
    marker["additionalDataKeys"] = [
        data_key("severity", "Severity"),
        data_key("buffer_depth", "Buffer depth"),
        data_key("active", "Active", "attribute"),
    ]
    marker["label"] = {"show": True, "type": "pattern", "pattern": "${entityName}: ${severity}"}
    marker["tooltip"] = {
        "show": True, "trigger": "click", "autoclose": True,
        "type": "pattern",
        "pattern": "<b>${entityName}</b><br/>Severity: ${severity}<br/>Buffer: ${buffer_depth}",
        "offsetX": 0, "offsetY": -1,
    }
    marker["markerShape"]["color"] = {
        "type": "function", "color": "#16a34a",
        "colorFunction": (
            "if (data.active === false || data.active === 'false') return '#64748b';\n"
            "var s = String(data.severity || 'Normal');\n"
            "if (s === 'Fault') return '#7c3aed';\n"
            "if (s === 'Critical') return '#dc2626';\n"
            "if (s === 'Major') return '#ea580c';\n"
            "if (s === 'Warning') return '#d97706';\n"
            "return '#16a34a';"
        ),
    }
    marker["click"] = nav_action("source", "Open source")
    settings["markers"] = [marker]
    config["actions"] = {"markerClick": [nav_action("source", "Open source")]}
    return map_widget


def make_alarms(tb: ThingsBoard, alias: str) -> dict:
    item = widget(tb, "alarm_widgets.alarms_table", "Active and cleared alarms")
    source = item["config"]["alarmSource"]
    source.update(type="entity", name="Water sources", entityAliasId=alias)
    source["dataKeys"].append(data_key("clearTs", "Cleared time", "alarm"))
    # The empty status list plus ANY search status includes active and cleared alarms.
    # Avoid passing combined alarm states as a query status filter.
    item["config"]["alarmStatusList"] = []
    item["config"]["alarmSearchStatus"] = "ANY"
    item["config"]["settings"]["displayActivity"] = True
    return item


def build(tb: ThingsBoard) -> dict:
    nodes = {d["name"]: d for d in tb.pages("/api/tenant/devices")
             if d["name"] in ("node-a", "node-b", "node-c")}
    if len(nodes) != 3:
        raise RuntimeError("Provision node-a, node-b and node-c before building dashboard")
    fleet, first, selected = uid(), uid(), uid()
    aliases = {
        fleet: {"id": fleet, "alias": "Water sources", "filter": {
            "type": "entityList", "resolveMultiple": True, "entityType": "DEVICE",
            "entityList": [nodes[n]["id"]["id"] for n in sorted(nodes)],
        }},
        first: {"id": first, "alias": "Node A summary", "filter": {
            "type": "singleEntity", "resolveMultiple": False,
            "singleEntity": {"entityType": "DEVICE", "id": nodes["node-a"]["id"]["id"]},
        }},
        selected: {"id": selected, "alias": "Selected source", "filter": {
            "type": "stateEntity", "resolveMultiple": False,
            "stateEntityParamName": None, "defaultStateEntity": None,
        }},
    }
    settings = {
        "stateControllerId": "entity", "showTitle": True,
        "showDashboardsSelect": False, "showEntitiesSelect": False,
        "showDashboardTimewindow": True, "showDashboardExport": True,
        "toolbarAlwaysOpen": True,
    }
    config = {
        "description": "Duty-officer fleet triage for three virtual post-flood water sources.",
        "widgets": {}, "entityAliases": aliases, "filters": {},
        "states": {
            "default": {"name": "Fleet", "root": True, "layouts": {"main": grid()}},
            "source": {"name": "${entityName} details", "root": False,
                       "layouts": {"main": grid()}},
        },
        "timewindow": {
            "displayValue": "", "selectedTab": 0,
            "realtime": {"realtimeType": 0, "interval": 5000, "timewindowMs": 3600000},
            "history": {"historyType": 0, "interval": 5000, "timewindowMs": 3600000},
            "aggregation": {"type": "NONE", "limit": 2000},
        },
        "settings": settings,
    }
    add_widget(config, "default", make_table(tb, fleet), 0, 0, 12, 6)
    add_widget(config, "default", make_map(tb, fleet), 12, 0, 12, 12)
    add_widget(config, "default", make_alarms(tb, fleet), 0, 6, 12, 6)

    def details(state: str, alias: str, row: int) -> None:
        add_widget(config, state, make_gauge(tb, "pH", alias, "ph", 0, 14,
            [(0, 6.5, "#dc2626"), (6.5, 8.5, "#16a34a"), (8.5, 14, "#dc2626")]),
            0, row, 8, 5)
        add_widget(config, state, make_gauge(tb, "Turbidity (NTU)", alias,
            "turbidity_ntu", 0, 800, [(0, 5, "#16a34a"),
            (5, 50, "#d97706"), (50, 200, "#ea580c"), (200, 800, "#dc2626")]),
            8, row, 8, 5)
        add_widget(config, state, make_tds(tb, alias), 16, row, 8, 5)
        add_widget(config, state, make_chart(tb, "Intrusion: turbidity, pH and TDS", alias,
            [("turbidity_ntu", "Turbidity", "NTU", "left"),
             ("ph", "pH", "pH", "right"),
             ("tds_ppm", "TDS", "ppm", "right")]), 0, row + 5, 16, 8)
        add_widget(config, state, make_chart(tb, "Temperature and supply flow", alias,
            [("water_temp_c", "Water temperature", "°C", "left"),
             ("flow_lpm", "Flow", "L/min", "right")]), 16, row + 5, 8, 8)

    details("default", first, 12)
    details("source", selected, 0)
    add_widget(config, "source", make_alarms(tb, fleet), 0, 13, 24, 6)
    return tune({"title": TITLE, "name": TITLE, "image": None,
                 "mobileHide": False, "mobileOrder": None,
                 "configuration": config})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="save the dashboard to ThingsBoard")
    args = parser.parse_args()
    key_path = REPO_ROOT / ".thingsboard-auth"
    if not key_path.exists():
        parser.error("Save an API key in ignored .thingsboard-auth first")
    tb = ThingsBoard(DEFAULT_URL, "ApiKey " + key_path.read_text().strip())
    dashboard = build(tb)
    export_dir = REPO_ROOT / "cloud" / "exports"
    export_dir.mkdir(exist_ok=True)
    output = export_dir / "post-flood-dashboard.json"
    output.write_text(json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n")
    print(f"Built {len(dashboard['configuration']['widgets'])} widget instances in two states: {output}")
    if not args.apply:
        print("Local export only; use --apply to save to tenant")
        return
    existing = next((d for d in tb.pages("/api/tenant/dashboards") if d.get("title") == TITLE), None)
    if existing:
        raise RuntimeError("Dashboard already exists; inspect it before updating to avoid overwriting UI edits")
    saved = tb.request("POST", "/api/dashboard", dashboard)
    dashboard_id = saved["id"]["id"]
    actual = tb.request("GET", "/api/dashboard/" + dashboard_id)
    output.write_text(json.dumps(actual, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved and read back dashboard id={dashboard_id}")
    print(f"Open {DEFAULT_URL}/dashboards/all/{dashboard_id} (or find by title in Dashboards)")


if __name__ == "__main__":
    main()
