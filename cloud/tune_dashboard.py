"""Apply small, repeatable layout fixes to the existing duty-officer dashboard.

Preview: python -m cloud.tune_dashboard
Apply:   python -m cloud.tune_dashboard --apply

This reads the current tenant dashboard before changing it, preserving its widget
IDs, aliases, actions, and any unrelated UI edits. It never prints credentials.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from cloud.provision import DEFAULT_URL, REPO_ROOT, ThingsBoard, entity_id


TITLE = "Post-Flood Water Sources - Duty Officer"


def tds_ranges() -> list[dict]:
    def boundary(value: float) -> dict:
        return {"valueSource": "predefinedValue", "value": value}

    return [
        {"from": boundary(-1), "to": boundary(math.nextafter(500, -math.inf)), "color": "#16a34a"},
        {"from": boundary(500), "to": boundary(math.nextafter(1000, -math.inf)), "color": "#d97706"},
        {"from": boundary(1000), "to": boundary(1500), "color": "#dc2626"},
    ]


def tune(dashboard: dict) -> dict:
    config = dashboard["configuration"]
    states = config["states"]
    widgets = config["widgets"]
    expected = {"default": 8, "source": 6}
    for state, count in expected.items():
        if len(states[state]["layouts"]["main"]["widgets"]) != count:
            raise ValueError(f"Unexpected {state} widget count; inspect UI edits first")

    positions = {
        "default": {
            "system.cards.entities_table": (0, 8),
            "system.map": (0, 14),
            "system.alarm_widgets.alarms_table": (8, 6),
            "system.analogue_gauges.radial_gauge_canvas_gauges": (14, 7),
            "system.digital_gauges.simple_gauge_justgage": (14, 7),
            "system.time_series_chart": (21, 9),
        },
        "source": {
            "system.analogue_gauges.radial_gauge_canvas_gauges": (0, 7),
            "system.digital_gauges.simple_gauge_justgage": (0, 7),
            "system.time_series_chart": (7, 9),
            "system.alarm_widgets.alarms_table": (16, 6),
        },
    }

    for state, definition in states.items():
        layout = definition["layouts"]["main"]["widgets"]
        for widget_id, placement in layout.items():
            widget = widgets[widget_id]
            row, height = positions[state][widget["typeFullFqn"]]
            widget["row"] = placement["row"] = row
            widget["sizeY"] = placement["sizeY"] = height
            if state == "source" and widget["typeFullFqn"] in {
                "system.analogue_gauges.radial_gauge_canvas_gauges",
                "system.digital_gauges.simple_gauge_justgage",
            }:
                base_title = widget["config"]["title"].split(" — ")[0]
                widget["config"]["title"] = base_title + " — ${entityName}"
            if widget["typeFullFqn"] == "system.cards.entities_table":
                keys = widget["config"]["datasources"][0]["dataKeys"]
                depth = next(key for key in keys if key["name"] == "buffer_depth")
                depth["label"] = "Queue at sample"
                activity = next(key for key in keys if key["name"] == "lastActivityTime")
                activity["label"] = "Last activity (local)"
                activity["settings"].update({
                    "useCellContentFunction": True,
                    "cellContentFunction": (
                        "return value ? ctx.date.transform(Number(value), "
                        "'yyyy-MM-dd HH:mm:ss') : '';"
                    ),
                })
            if widget["typeFullFqn"] == "system.alarm_widgets.alarms_table":
                widget["config"]["alarmStatusList"] = []
                widget["config"]["alarmSearchStatus"] = "ANY"
            if widget["typeFullFqn"] == "system.digital_gauges.simple_gauge_justgage":
                gauge_settings = widget["config"]["settings"]
                gauge_settings["maxValue"] = 1500
                gauge_settings["useFixedLevelColor"] = True
                gauge_settings["fixedLevelColors"] = tds_ranges()
                gauge_settings.pop("barColor", None)
            if widget["typeFullFqn"] == "system.map":
                widget["config"]["settings"]["layers"] = [
                    {"label": "OpenStreetMap", "provider": "openstreet",
                     "layerType": "OpenStreetMap.Mapnik"},
                    {"label": "OpenFreeMap", "provider": "openfreemap",
                     "layerType": "bright"},
                ]
            if widget["typeFullFqn"] == "system.time_series_chart":
                axes = widget["config"]["settings"].get("yAxes", {})
                for axis in axes.values():
                    if axis.get("units") == "pH":
                        axis["decimals"] = 2
                        axis["label"] = "pH"
    return dashboard


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    key_path = REPO_ROOT / ".thingsboard-auth"
    if not key_path.exists():
        parser.error("Save the tenant API key in ignored .thingsboard-auth first")
    tb = ThingsBoard(DEFAULT_URL, "ApiKey " + key_path.read_text().strip())
    listed = next((d for d in tb.pages("/api/tenant/dashboards")
                   if d.get("title") == TITLE), None)
    if listed is None:
        raise RuntimeError(f"Dashboard not found: {TITLE}")
    dashboard_id = entity_id(listed)
    actual = tb.request("GET", "/api/dashboard/" + dashboard_id)
    tuned = tune(actual)
    print("Prepared activity date, queue label, source titles, axes, layout, alarm filter, TDS ranges and map layers")
    if not args.apply:
        print("Preview only; use --apply to save")
        return
    tb.request("POST", "/api/dashboard", tuned)
    readback = tb.request("GET", "/api/dashboard/" + dashboard_id)
    output = REPO_ROOT / "cloud/exports/post-flood-dashboard.json"
    output.write_text(json.dumps(readback, indent=2, ensure_ascii=False) + "\n")
    print(f"Saved and read back {output}")


if __name__ == "__main__":
    main()
