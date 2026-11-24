"""Tests for the scripted demo timeline and its phase controller."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from edge.scenarios import ScenarioController, load_scenario

DEMO = Path(__file__).resolve().parents[1] / "scenarios" / "demo.json"


def test_timeline_advances_phases(fake_clock):
    c = ScenarioController(load_scenario(DEMO), now_fn=fake_clock)
    assert c.phase == "baseline"
    fake_clock.advance(61)
    assert c.tick() == "silt_intrusion"
    fake_clock.advance(241)
    assert c.tick() == "supply_interruption"


def test_manual_override_and_reset_elapsed(fake_clock):
    c = ScenarioController(load_scenario(DEMO), now_fn=fake_clock)
    fake_clock.advance(30)
    assert c.set_phase("silt_intrusion") == "silt_intrusion"
    assert c.elapsed_s == 0


def test_tick_keeps_elapsed_phase_local(fake_clock):
    c = ScenarioController(load_scenario(DEMO), now_fn=fake_clock)
    fake_clock.advance(65)
    assert c.tick() == "silt_intrusion"
    assert c.elapsed_s == 5
    fake_clock.advance(5)
    assert c.elapsed_s == 10


def test_timeline_loops_after_full_cycle(fake_clock):
    c = ScenarioController(load_scenario(DEMO), now_fn=fake_clock)
    fake_clock.advance(600)
    assert c.tick() == "baseline"
    fake_clock.advance(61)
    assert c.tick() == "silt_intrusion"


def test_phase_changes_log_state(fake_clock, caplog):
    c = ScenarioController(load_scenario(DEMO), now_fn=fake_clock)
    caplog.set_level(logging.INFO)
    fake_clock.advance(61)
    assert c.tick() == "silt_intrusion"
    assert "STATE: baseline -> silt_intrusion" in caplog.text
    assert c.set_phase("recovery") == "recovery"
    assert "STATE: silt_intrusion -> recovery" in caplog.text


def test_load_scenario_exposes_demo_constants():
    scenario = load_scenario(DEMO)
    assert scenario["phases"] == [
        {"name": "baseline", "duration_s": 60},
        {"name": "silt_intrusion", "duration_s": 240},
        {"name": "supply_interruption", "duration_s": 120},
        {"name": "recovery", "duration_s": 180},
    ]
    assert [(n["node_id"], n["amplitude"], n["onset_offset_s"]) for n in scenario["nodes"]] == [
        ("node-a", 500, 0),
        ("node-b", 825, 20),
        ("node-c", 1250, 40),
    ]
    assert scenario["tau_rise_s"] == 10
    assert scenario["tau_wash_s"] == 120


def test_set_phase_rejects_unknown_phase(fake_clock):
    c = ScenarioController(load_scenario(DEMO), now_fn=fake_clock)
    with pytest.raises(ValueError):
        c.set_phase("flood")
    assert c.phase == "baseline"


def test_empty_phase_list_is_rejected(fake_clock):
    with pytest.raises(ValueError):
        ScenarioController({"phases": []}, now_fn=fake_clock)
