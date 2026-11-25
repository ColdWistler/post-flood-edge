"""Tests for ThingsBoard-importable alarm rule JSON files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
IMPORT_DIR = ROOT / "cloud" / "artifacts" / "alarm_rules_import"

EXPECTED = {
    "high_turbidity.json": "High Turbidity",
    "gross_contamination.json": "Gross Contamination",
    "ph_out_of_range.json": "pH Out of Range",
    "high_tds.json": "High TDS",
    "supply_interruption.json": "Supply Interruption",
    "node_inactive.json": "Node Inactive",
    "rapid_turbidity_rise.json": "Rapid Turbidity Rise",
    "severe_ph_persistence.json": "Severe pH Persistence",
    "sensor_fault.json": "Sensor Fault",
}


def load(filename: str) -> dict[str, Any]:
    return json.loads((IMPORT_DIR / filename).read_text(encoding="utf-8"))


def predicates(rule: dict[str, Any]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "operation" in node and "value" in node and "type" in node:
                found.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(rule)
    return found


def predicate_triples(rule: dict[str, Any]) -> set[tuple[str, str, Any]]:
    return {
        (item["type"], item["operation"], item["value"]["staticValue"])
        for item in predicates(rule)
    }


def triples(document: dict[str, Any]) -> set[tuple[str, str, Any]]:
    rules = [document["configuration"]["clearRule"]]
    rules.extend(document["configuration"]["createRules"].values())
    return {
        triple
        for rule in rules
        for triple in predicate_triples(rule)
    }


def test_all_nine_import_files_have_valid_calculated_field_shape() -> None:
    assert {path.name for path in IMPORT_DIR.glob("*.json")} == set(EXPECTED)
    for filename, name in EXPECTED.items():
        document = load(filename)
        assert document["name"] == name
        assert document["type"] == "ALARM"
        configuration = document["configuration"]
        assert configuration["type"] == "ALARM"
        assert configuration["arguments"]
        assert configuration["createRules"]
        assert configuration["clearRule"]
        for argument in configuration["arguments"].values():
            assert argument["refEntityKey"]["type"] == "TS_LATEST"
            assert argument["refEntityKey"]["key"]
        for rule in list(configuration["createRules"].values()) + [configuration["clearRule"]]:
            condition = rule["condition"]
            assert condition["type"] in {"SIMPLE", "DURATION", "REPEATING"}
            assert condition["expression"]["type"] == "SIMPLE"
            assert condition["expression"]["filters"]
            for item in condition["expression"]["filters"]:
                assert item["argument"] in configuration["arguments"]
                assert item["valueType"] in {"NUMERIC", "STRING", "BOOLEAN"}
                assert item["predicates"]


def test_direct_numeric_thresholds_are_preserved() -> None:
    high_turbidity = triples(load("high_turbidity.json"))
    assert ("NUMERIC", "GREATER", 50) in high_turbidity
    assert ("NUMERIC", "LESS", 40) in high_turbidity

    gross = triples(load("gross_contamination.json"))
    assert ("NUMERIC", "GREATER", 200) in gross
    assert ("NUMERIC", "LESS", 150) in gross

    ph = triples(load("ph_out_of_range.json"))
    assert ("NUMERIC", "LESS", 6.5) in ph
    assert ("NUMERIC", "GREATER", 8.5) in ph
    assert ("NUMERIC", "GREATER_OR_EQUAL", 6.7) in ph
    assert ("NUMERIC", "LESS_OR_EQUAL", 8.3) in ph

    tds = triples(load("high_tds.json"))
    assert ("NUMERIC", "GREATER", 500) in tds
    assert ("NUMERIC", "LESS", 450) in tds

    flow = triples(load("supply_interruption.json"))
    assert ("NUMERIC", "EQUAL", 0) in flow
    assert ("NUMERIC", "GREATER", 0) in flow


def test_node_inactive_rule_uses_published_flag() -> None:
    configuration = load("node_inactive.json")["configuration"]
    assert list(configuration["arguments"]) == ["node_inactive"]
    assert predicate_triples(configuration["createRules"]["CRITICAL"]) == {
        ("BOOLEAN", "EQUAL", True)
    }
    assert predicate_triples(configuration["clearRule"]) == {
        ("BOOLEAN", "EQUAL", False)
    }


def test_debounce_and_duration_match_the_edge_contract() -> None:
    ph = load("ph_out_of_range.json")["configuration"]
    assert ph["createRules"]["CRITICAL"]["condition"]["type"] == "REPEATING"
    assert ph["createRules"]["CRITICAL"]["condition"]["count"] == {"staticValue": 3}
    assert ph["clearRule"]["condition"]["count"] == {"staticValue": 3}

    tds = load("high_tds.json")["configuration"]
    assert tds["createRules"]["CRITICAL"]["condition"]["count"] == {"staticValue": 3}
    assert tds["clearRule"]["condition"]["count"] == {"staticValue": 3}

    supply = load("supply_interruption.json")["configuration"]["createRules"]["MAJOR"]["condition"]
    assert supply["type"] == "DURATION"
    assert supply["unit"] == "SECONDS"
    assert supply["value"] == {"staticValue": 30}


def test_mirror_rules_use_active_alarms_contains() -> None:
    for filename, token in (
        ("rapid_turbidity_rise.json", "Rapid Turbidity Rise"),
        ("severe_ph_persistence.json", "Severe pH Persistence"),
        ("sensor_fault.json", "Sensor Fault"),
    ):
        values = triples(load(filename))
        assert ("STRING", "CONTAINS", token) in values
        assert ("STRING", "NOT_CONTAINS", token) in values
