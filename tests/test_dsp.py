"""Tests for Hampel filtering, compensation, WQI and reading processing."""

from __future__ import annotations

from copy import deepcopy

import pytest

from edge.dsp import (
    HampelFilter,
    ambient_tds,
    brown_wqi,
    compensate_ph,
    compensate_tds,
    nernst_slope,
    process_reading,
)
from edge.models import Reading

READING = Reading(
    node_id="node-a",
    ts_ms=1_700_000_000_000,
    values={
        "ph": 7.2,
        "turbidity_ntu": 5.0,
        "tds_ppm": 153.0,  # ambient measurement; 25 °C-equivalent is 170 ppm
        "water_temp_c": 20.0,
        "flow_lpm": 12.0,
        "latitude": 27.7172,
        "longitude": 85.3240,
        "node_id": "node-a",
        "event_u": 0.0,
        "glitch": False,
    },
)

CHANNELS = ["ph", "turbidity_ntu", "tds_ppm", "water_temp_c", "flow_lpm"]


def test_hampel_rejects_spike_but_keeps_step():
    f = HampelFilter()
    out = [f.update(v) for v in [10] * 9 + [500] + [10] * 3 + [60] * 9]
    assert out[9] == 10  # spike replaced by median
    assert out[-1] == 60  # genuine step preserved


def test_nernst_slope_at_25c():
    assert nernst_slope(25.0) == pytest.approx(59.16, abs=0.05)


def test_compensate_ph_uses_raw_value_as_deviation_from_neutral():
    assert compensate_ph(0.2, 25.0) == pytest.approx((7.2, 7.2))
    ph_t, ph_25 = compensate_ph(0.2, 35.0)
    assert ph_t == pytest.approx(7.2)
    assert 7.20 < ph_25 < 7.22  # 0.2 * (308.15 / 298.15) at 35 °C


def test_tds_round_trip_and_compensation():
    assert compensate_tds(ambient_tds(300.0, 35.0), 35.0) == pytest.approx(300.0, abs=1)


def test_wqi_no_chlorine_weights():
    # q_pH=0, q_turbidity=100 (w=0.6277), q_TDS=30 (w=0.0031) -> ~62.9
    wqi, band = brown_wqi(7.0, 5.0, 300.0)
    assert 62 <= wqi <= 64
    assert band == "Poor"


def test_wqi_excellent_band_for_clean_water():
    wqi, band = brown_wqi(7.0, 0.0, 0.0)
    assert wqi == pytest.approx(0.0)
    assert band == "Excellent"


def test_process_reading_adds_audit_keys():
    filters = {k: HampelFilter() for k in CHANNELS}
    out = process_reading(READING, filters)
    assert "ph_25" in out.values and "wqi" in out.values and "wqi_band" in out.values


def test_process_reading_compensates_without_mutating_input_or_losing_metadata():
    original = deepcopy(READING)
    filters = {k: HampelFilter() for k in CHANNELS}

    out = process_reading(READING, filters)

    assert out.node_id == "node-a" and out.ts_ms == READING.ts_ms
    assert out.values["ph"] == pytest.approx(7.2, abs=0.01)
    assert out.values["ph_25"] == pytest.approx(7.2, abs=0.01)
    assert out.values["tds_ppm"] == pytest.approx(170.0, abs=0.01)
    assert out.values["latitude"] == 27.7172
    assert out.values["glitch"] is False
    assert READING == original
