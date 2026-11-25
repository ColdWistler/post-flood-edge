"""Generate ThingsBoard-importable alarm-rule JSON files.

``cloud/artifacts/alarm_rules.json`` is a human-readable specification. Recent
ThingsBoard Cloud versions expose ``Alarms -> Alarm rules`` with an Import button
that expects one ``CalculatedField`` of type ``ALARM`` per file, not the
specification document and not the deprecated ``device_profile.json`` alarm list.

Running this script writes one JSON file per rule to
``cloud/artifacts/alarm_rules_import/``. Each file can be imported with the
Alarm rules page's Import action; the dialog then assigns it to the current
entity (for example the ``post-flood-water-quality`` device profile).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "cloud" / "artifacts" / "alarm_rules_import"


def arg(key: str) -> dict[str, Any]:
    return {"refEntityKey": {"key": key, "type": "TS_LATEST"}}


def value(v: Any) -> dict[str, Any]:
    return {"staticValue": v}


def numeric(op: str, v: float) -> dict[str, Any]:
    return {"type": "NUMERIC", "operation": op, "value": value(v)}


def string(op: str, v: str) -> dict[str, Any]:
    return {
        "type": "STRING",
        "operation": op,
        "value": value(v),
        "ignoreCase": False,
    }


def boolean(op: str, v: bool) -> dict[str, Any]:
    return {"type": "BOOLEAN", "operation": op, "value": value(v)}


def filter_(key: str, vtype: str, predicates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "argument": key,
        "valueType": vtype,
        "operation": "AND",
        "predicates": predicates,
    }


def expression(filters: list[dict[str, Any]], operation: str = "AND") -> dict[str, Any]:
    return {"type": "SIMPLE", "filters": filters, "operation": operation}


def rule(
    kind: str,
    filters: list[dict[str, Any]],
    operation: str = "AND",
    *,
    count: int | None = None,
    duration_s: int | None = None,
) -> dict[str, Any]:
    condition: dict[str, Any] = {
        "type": kind,
        "expression": expression(filters, operation),
        "schedule": {"staticValue": {"type": "ANY_TIME"}},
    }
    if count is not None:
        condition["count"] = value(count)
    if duration_s is not None:
        condition["unit"] = "SECONDS"
        condition["value"] = value(duration_s)
    return {"condition": condition, "alarmDetails": ""}


def field(
    name: str,
    keys: list[str],
    create_rules: dict[str, dict[str, Any]],
    clear_rule: dict[str, Any],
    description: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "type": "ALARM",
        "configuration": {
            "type": "ALARM",
            "arguments": {key: arg(key) for key in keys},
            "createRules": create_rules,
            "clearRule": clear_rule,
            "propagate": False,
            "propagateToOwner": False,
            "propagateToOwnerHierarchy": False,
            "propagateToTenant": False,
            "propagateRelationTypes": [],
        },
        "additionalInfo": {"description": description},
    }


def build() -> dict[str, dict[str, Any]]:
    return {
        "high_turbidity.json": field(
            "High Turbidity",
            ["turbidity_ntu"],
            {"WARNING": rule("SIMPLE", [filter_("turbidity_ntu", "NUMERIC", [numeric("GREATER", 50)])])},
            rule("SIMPLE", [filter_("turbidity_ntu", "NUMERIC", [numeric("LESS", 40)])]),
            "Server-side copy of the edge rule: turbidity_ntu > 50 clears below 40.",
        ),
        "gross_contamination.json": field(
            "Gross Contamination",
            ["turbidity_ntu"],
            {"CRITICAL": rule("SIMPLE", [filter_("turbidity_ntu", "NUMERIC", [numeric("GREATER", 200)])])},
            rule("SIMPLE", [filter_("turbidity_ntu", "NUMERIC", [numeric("LESS", 150)])]),
            "Server-side copy of the edge rule: turbidity_ntu > 200 clears below 150.",
        ),
        "ph_out_of_range.json": field(
            "pH Out of Range",
            ["ph"],
            {
                "CRITICAL": rule(
                    "REPEATING",
                    [
                        filter_("ph", "NUMERIC", [numeric("LESS", 6.5)]),
                        filter_("ph", "NUMERIC", [numeric("GREATER", 8.5)]),
                    ],
                    "OR",
                    count=3,
                )
            },
            rule(
                "REPEATING",
                [
                    filter_("ph", "NUMERIC", [numeric("GREATER_OR_EQUAL", 6.7)]),
                    filter_("ph", "NUMERIC", [numeric("LESS_OR_EQUAL", 8.3)]),
                ],
                "AND",
                count=3,
            ),
            "Three-sample debounce: ph < 6.5 or > 8.5 clears inside 6.7-8.3.",
        ),
        "high_tds.json": field(
            "High TDS",
            ["tds_ppm"],
            {"CRITICAL": rule("REPEATING", [filter_("tds_ppm", "NUMERIC", [numeric("GREATER", 500)])], count=3)},
            rule("REPEATING", [filter_("tds_ppm", "NUMERIC", [numeric("LESS", 450)])], count=3),
            "Three-sample debounce: tds_ppm > 500 clears below 450.",
        ),
        "supply_interruption.json": field(
            "Supply Interruption",
            ["flow_lpm"],
            {
                "MAJOR": rule(
                    "DURATION",
                    [filter_("flow_lpm", "NUMERIC", [numeric("EQUAL", 0)])],
                    duration_s=30,
                )
            },
            rule("SIMPLE", [filter_("flow_lpm", "NUMERIC", [numeric("GREATER", 0)])]),
            "Demonstration rule: flow_lpm == 0 for 30 seconds; production window is 1800 seconds.",
        ),
        "node_inactive.json": field(
            "Node Inactive",
            ["node_inactive"],
            {"CRITICAL": rule("SIMPLE", [filter_("node_inactive", "BOOLEAN", [boolean("EQUAL", True)])])},
            rule("SIMPLE", [filter_("node_inactive", "BOOLEAN", [boolean("EQUAL", False)])]),
            "Mirrors the edge-computed 90 s demo flag node_inactive; the server-scope inactivityTimeout attribute is separate.",
        ),
        "rapid_turbidity_rise.json": field(
            "Rapid Turbidity Rise",
            ["active_alarms"],
            {
                "WARNING": rule(
                    "SIMPLE",
                    [filter_("active_alarms", "STRING", [string("CONTAINS", "Rapid Turbidity Rise")])],
                )
            },
            rule(
                "SIMPLE",
                [filter_("active_alarms", "STRING", [string("NOT_CONTAINS", "Rapid Turbidity Rise")])],
            ),
            "Mirrors the edge-derived rapid-rise alarm via active_alarms CONTAINS.",
        ),
        "severe_ph_persistence.json": field(
            "Severe pH Persistence",
            ["active_alarms"],
            {
                "CRITICAL": rule(
                    "SIMPLE",
                    [filter_("active_alarms", "STRING", [string("CONTAINS", "Severe pH Persistence")])],
                )
            },
            rule(
                "SIMPLE",
                [filter_("active_alarms", "STRING", [string("NOT_CONTAINS", "Severe pH Persistence")])],
            ),
            "Mirrors the edge-derived severe-pH alarm via active_alarms CONTAINS.",
        ),
        "sensor_fault.json": field(
            "Sensor Fault",
            ["active_alarms"],
            {
                "CRITICAL": rule(
                    "SIMPLE",
                    [filter_("active_alarms", "STRING", [string("CONTAINS", "Sensor Fault")])],
                )
            },
            rule(
                "SIMPLE",
                [filter_("active_alarms", "STRING", [string("NOT_CONTAINS", "Sensor Fault")])],
            ),
            "Mirrors the edge-derived Sensor Fault alarm; edge severity is Fault and server severity is CRITICAL.",
        ),
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for filename, document in build().items():
        (OUT_DIR / filename).write_text(
            json.dumps(document, indent=2) + "\n", encoding="utf-8"
        )
    print(f"wrote {len(build())} alarm-rule import files to {OUT_DIR.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
