import math
import random

from edge.sensors import SensorSimulator

from conftest import NODE_A


def test_baseline_ranges():
    sim = SensorSimulator(NODE_A, 5.0, glitch_rate=0.0, rng=random.Random(1))
    for i in range(200):
        v = sim.sample("baseline", i * 5).values
        assert 6.8 <= v["ph"] <= 7.6
        assert 2 <= v["turbidity_ntu"] <= 8
        assert 120 <= v["tds_ppm"] <= 250
        assert 16 <= v["water_temp_c"] <= 24
        assert 8 <= v["flow_lpm"] <= 18


def test_coupled_intrusion_moves_all_parameters():
    base = SensorSimulator(NODE_A, 5.0, 0.0, random.Random(1)).sample("baseline", 0).values
    peak = max(
        (SensorSimulator(NODE_A, 5.0, 0.0, random.Random(1)).sample("silt_intrusion", t).values
         for t in range(0, 60, 2)),
        key=lambda v: v["turbidity_ntu"],
    )
    assert peak["turbidity_ntu"] > 250
    assert peak["tds_ppm"] > base["tds_ppm"] + 50
    assert peak["ph"] < base["ph"] - 0.5
    assert peak["ph"] >= 6.2


def test_phase_change_continues_cumulative_washout():
    sim = SensorSimulator(NODE_A, 5.0, 0.0, random.Random(1))
    early = sim.sample("silt_intrusion", 10).values
    peak = sim.sample("silt_intrusion", 60).values
    later = sim.sample("recovery", 120).values
    assert later["turbidity_ntu"] < peak["turbidity_ntu"]
    assert early["event_u"] < peak["event_u"]
    assert 0 < later["event_u"] < peak["event_u"]
    assert later["flow_lpm"] <= 18  # no new transient when the phase changes


def test_supply_interruption_sets_flow_zero():
    v = SensorSimulator(NODE_A, 5.0, 0.0, random.Random(1)).sample("supply_interruption", 10).values
    assert v["flow_lpm"] == 0.0


def test_glitch_is_flagged_and_changes_only_turbidity():
    for seed in (3, 5, 7):  # cover both signs of the +/- magnitude
        clean = SensorSimulator(NODE_A, 5.0, 0.0, random.Random(seed)).sample("baseline", 10).values
        glitched = SensorSimulator(NODE_A, 5.0, 1.0, random.Random(seed)).sample("baseline", 10).values
        assert glitched["glitch"] is True
        assert clean["glitch"] is False
        assert 199.9 <= abs(glitched["turbidity_ntu"] - clean["turbidity_ntu"]) <= 500.1
        assert glitched["ph"] == clean["ph"]
        assert glitched["tds_ppm"] == clean["tds_ppm"]


def test_values_include_metadata_event_u_and_expected_rounding():
    reading = SensorSimulator(NODE_A, 5.0, 0.0, random.Random(1)).sample("silt_intrusion", 5)
    v = reading.values
    assert reading.node_id == "node-a"
    assert v["node_id"] == "node-a"
    assert v["latitude"] == 27.7172
    assert v["longitude"] == 85.3240
    expected_u = 500 * (1 - math.exp(-5 / 10)) * math.exp(-5 / 120)
    assert v["event_u"] == round(expected_u, 2)
    for key, places in (
        ("ph", 2),
        ("turbidity_ntu", 1),
        ("tds_ppm", 0),
        ("water_temp_c", 1),
        ("flow_lpm", 1),
    ):
        assert v[key] == round(v[key], places)
