"""Tests for raw-value validity gates and last-valid substitution."""

from __future__ import annotations

import pytest

from edge.validity import ValidityGates

BASE = {
    "ph": 7.2,
    "turbidity_ntu": 5.0,
    "tds_ppm": 180.0,
    "water_temp_c": 20.0,
    "flow_lpm": 12.0,
}


def test_saturation_flag_and_last_valid_substitution():
    g = ValidityGates()
    clean, flags = g.check({**BASE, "turbidity_ntu": 99999}, [])
    assert "SATURATION" in flags and clean["turbidity_ntu"] < 99999


def test_saturation_substitutes_most_recent_valid_history_value():
    g = ValidityGates()
    clean, flags = g.check(
        {**BASE, "turbidity_ntu": 99999},
        [{**BASE, "turbidity_ntu": 6.0}, {**BASE, "turbidity_ntu": 7.0}],
    )
    assert "SATURATION" in flags
    assert clean["turbidity_ntu"] == 7.0


def test_open_short_and_stuck_value():
    g = ValidityGates()
    _, flags = g.check({**BASE, "ph": 14.0}, [])
    assert "OPEN_SHORT" in flags
    _, flags = g.check({**BASE, "flow_lpm": 12.0}, [BASE] * 9)
    assert "STUCK" in flags


def test_valid_baseline_has_no_flags():
    assert ValidityGates().check(BASE, [])[1] == []


def test_impossible_ph_tds_pair_flags_sanity_and_is_substituted():
    clean, flags = ValidityGates().check({**BASE, "ph": 2.0, "tds_ppm": 3000.0}, [])
    assert "SANITY" in flags
    assert clean["ph"] == BASE["ph"]


def test_invalid_channel_logs_validity_marker_with_channel(caplog):
    ValidityGates().check({**BASE, "turbidity_ntu": 99999}, [])
    assert "VALIDITY: SATURATION channel=turbidity_ntu" in caplog.text


@pytest.mark.parametrize("channel,bad", [("ph", -1.0), ("tds_ppm", 5001.0), ("flow_lpm", 501.0)])
def test_out_of_range_channels_flag_saturation(channel, bad):
    _, flags = ValidityGates().check({**BASE, channel: bad}, [])
    assert "SATURATION" in flags


def _frozen_history(channel: str, value: float) -> list[dict]:
    """Nine samples where only ``channel`` is frozen; other channels vary."""
    return [
        {
            **{key: val + i * 0.01 for key, val in BASE.items() if key != channel},
            channel: value,
        }
        for i in range(9)
    ]


def test_stuck_exempt_skips_stuck_gate_for_that_channel_only():
    frozen = {**BASE, "flow_lpm": 0.0}
    _, flags = ValidityGates().check(
        frozen, _frozen_history("flow_lpm", 0.0), stuck_exempt={"flow_lpm"}
    )
    assert flags == []


def test_stuck_exempt_does_not_suppress_other_gates_on_exempt_channel():
    g = ValidityGates()
    saturated = {**BASE, "flow_lpm": 501.0}
    _, flags = g.check(
        saturated, _frozen_history("flow_lpm", 501.0), stuck_exempt={"flow_lpm"}
    )
    assert "SATURATION" in flags
    assert "STUCK" not in flags

    endpoint = {**BASE, "ph": 14.0}
    _, flags = g.check(
        endpoint, _frozen_history("ph", 14.0), stuck_exempt={"ph"}
    )
    assert "OPEN_SHORT" in flags
    assert "STUCK" not in flags

