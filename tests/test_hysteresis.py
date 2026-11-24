"""Tests for per-rule hysteresis, debounce and severity aggregation."""

from __future__ import annotations

from edge.hysteresis import HysteresisEngine

BASE = {
    "ph": 7.2,
    "turbidity_ntu": 5.0,
    "tds_ppm": 180.0,
    "water_temp_c": 20.0,
    "flow_lpm": 12.0,
}


def test_flapping_does_not_change_state():
    # The brief's [48, 52] flap includes 52 NTU, which is above the specified
    # >50 raise rung; 48/49 exercises the intended inside-deadband flap.
    e = HysteresisEngine()
    for t in [48, 49, 48, 49]:
        e.note_activity(t)
        sev, alarms = e.evaluate(
            {"turbidity_ntu": t, "ph": 7.2, "tds_ppm": 200, "flow_lpm": 12}, t
        )
    assert sev == "Normal" and "High Turbidity" not in alarms


def test_raise_and_clear_deadbands():
    e = HysteresisEngine()
    e.note_activity(0); sev, alarms = e.evaluate(BASE, 0)
    e.note_activity(1); sev, alarms = e.evaluate({**BASE, "turbidity_ntu": 55}, 1)
    assert sev == "Warning" and "High Turbidity" in alarms
    e.note_activity(2); _, alarms = e.evaluate({**BASE, "turbidity_ntu": 45}, 2)
    assert "High Turbidity" in alarms
    e.note_activity(3); _, alarms = e.evaluate({**BASE, "turbidity_ntu": 39}, 3)
    assert "High Turbidity" not in alarms


def test_ph_and_tds_debounce_three_samples():
    e = HysteresisEngine()
    for t in [0, 1]:
        e.note_activity(t); _, alarms = e.evaluate({**BASE, "ph": 8.7}, t)
        assert "pH Out of Range" not in alarms
    e.note_activity(2); _, alarms = e.evaluate({**BASE, "ph": 8.7}, 2)
    assert "pH Out of Range" in alarms


def test_no_inactive_while_samples_continue():
    e = HysteresisEngine(node_inactive_s=90)
    for t in range(0, 300, 5):
        e.note_activity(t)
        _, alarms = e.evaluate(BASE, t)
        assert "Node Inactive" not in alarms


def test_inactive_after_timeout():
    e = HysteresisEngine(node_inactive_s=90)
    e.note_activity(0)
    sev, alarms = e.evaluate(BASE, 95)
    assert sev == "Critical" and "Node Inactive" in alarms


def test_derivative_rule_fires_below_absolute_threshold():
    e = HysteresisEngine(derivative_window_s=50)
    e.note_activity(0); e.evaluate({**BASE, "turbidity_ntu": 10}, 0)
    e.note_activity(50); sev, alarms = e.evaluate({**BASE, "turbidity_ntu": 45}, 50)
    assert sev == "Warning" and "Rapid Turbidity Rise" in alarms


def test_severe_ph_persistence_requires_three_samples():
    e = HysteresisEngine()
    for t in [0, 1]:
        e.note_activity(t); _, alarms = e.evaluate({**BASE, "ph": 9.2}, t)
        assert "Severe pH Persistence" not in alarms
    e.note_activity(2); sev, alarms = e.evaluate({**BASE, "ph": 9.2}, 2)
    assert sev == "Critical" and "Severe pH Persistence" in alarms


def test_fault_held_until_three_valid_samples():
    e = HysteresisEngine(fault_samples=3)
    for t in [0, 1]:
        e.note_activity(t); sev, _ = e.evaluate(BASE, t, ["STUCK"])
        assert sev != "Fault"
    e.note_activity(2); sev, alarms = e.evaluate(BASE, 2, ["STUCK"])
    assert sev == "Fault" and "Sensor Fault" in alarms
    for t in [3, 4]:
        e.note_activity(t); sev, _ = e.evaluate(BASE, t, [])
        assert sev == "Fault"
    e.note_activity(5); sev, alarms = e.evaluate(BASE, 5, [])
    assert sev != "Fault" and "Sensor Fault" not in alarms


def test_gross_contamination_deadband_and_severity():
    e = HysteresisEngine()
    e.note_activity(0); e.evaluate(BASE, 0)
    e.note_activity(1); sev, alarms = e.evaluate({**BASE, "turbidity_ntu": 210}, 1)
    assert sev == "Critical" and "Gross Contamination" in alarms
    e.note_activity(2); sev, alarms = e.evaluate({**BASE, "turbidity_ntu": 140}, 2)
    assert sev == "Warning" and "Gross Contamination" not in alarms


def test_high_tds_debounce_and_deadband():
    e = HysteresisEngine()
    for t in [0, 1]:
        e.note_activity(t); _, alarms = e.evaluate({**BASE, "tds_ppm": 520}, t)
        assert "High TDS" not in alarms
    e.note_activity(2); sev, alarms = e.evaluate({**BASE, "tds_ppm": 520}, 2)
    assert sev == "Critical" and "High TDS" in alarms
    e.note_activity(3); _, alarms = e.evaluate({**BASE, "tds_ppm": 470}, 3)
    assert "High TDS" in alarms  # 450–500 is the clear deadband
    for t in [4, 5]:
        e.note_activity(t); _, alarms = e.evaluate({**BASE, "tds_ppm": 440}, t)
        assert "High TDS" in alarms
    e.note_activity(6); _, alarms = e.evaluate({**BASE, "tds_ppm": 440}, 6)
    assert "High TDS" not in alarms


def test_supply_interruption_is_time_based_and_clears_immediately():
    e = HysteresisEngine(flow_zero_sustained_s=30)
    e.note_activity(0); _, alarms = e.evaluate({**BASE, "flow_lpm": 0.0}, 0)
    assert "Supply Interruption" not in alarms
    e.note_activity(29); _, alarms = e.evaluate({**BASE, "flow_lpm": 0.0}, 29)
    assert "Supply Interruption" not in alarms
    e.note_activity(30); sev, alarms = e.evaluate({**BASE, "flow_lpm": 0.0}, 30)
    assert sev == "Major" and "Supply Interruption" in alarms
    e.note_activity(31); _, alarms = e.evaluate(BASE, 31)
    assert "Supply Interruption" not in alarms


def test_derivative_ignores_drops_and_clears_after_window():
    falling = HysteresisEngine(derivative_window_s=50)
    falling.note_activity(0); falling.evaluate({**BASE, "turbidity_ntu": 60}, 0)
    falling.note_activity(5); _, alarms = falling.evaluate({**BASE, "turbidity_ntu": 10}, 5)
    assert "Rapid Turbidity Rise" not in alarms

    e = HysteresisEngine(derivative_window_s=50, derivative_delta_ntu=30)
    e.note_activity(0); e.evaluate({**BASE, "turbidity_ntu": 10}, 0)
    e.note_activity(50); _, alarms = e.evaluate({**BASE, "turbidity_ntu": 45}, 50)
    assert "Rapid Turbidity Rise" in alarms
    e.note_activity(100); _, alarms = e.evaluate({**BASE, "turbidity_ntu": 45}, 100)
    assert "Rapid Turbidity Rise" not in alarms


def test_severe_ph_deadband_holds_and_clear_band_needs_three_samples():
    e = HysteresisEngine()
    for t in [0, 1, 2]:
        e.note_activity(t); e.evaluate({**BASE, "ph": 9.2}, t)
    e.note_activity(3); _, alarms = e.evaluate({**BASE, "ph": 8.9}, 3)
    assert "Severe pH Persistence" in alarms  # 8.9 is in the clear deadband
    for t in [4, 5]:
        e.note_activity(t); _, alarms = e.evaluate({**BASE, "ph": 8.7}, t)
        assert "Severe pH Persistence" in alarms
    e.note_activity(6); _, alarms = e.evaluate({**BASE, "ph": 8.7}, 6)
    assert "Severe pH Persistence" not in alarms


def test_fault_outweighs_critical_and_alarms_are_sorted():
    e = HysteresisEngine(fault_samples=1)
    e.note_activity(0)
    sev, alarms = e.evaluate({**BASE, "turbidity_ntu": 210}, 0, ["SANITY"])
    assert sev == "Fault"
    assert alarms == sorted(alarms)
    assert "Sensor Fault" in alarms and "Gross Contamination" in alarms
