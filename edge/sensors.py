"""Deterministic, physically coupled virtual sensor samples."""

from __future__ import annotations

import math
import random
import time

from .models import NodeConfig, Reading

_TAU_RISE_S = 10.0
_TAU_WASH_S = 120.0
_DAY_S = 86_400.0
_TDS_COUPLING_PPM_PER_NTU = 0.45
_PH_DROP = 0.90
_PH_FLOOR = 6.2
_TDS_ALPHA_PER_C = 0.02
_FLOW_TRANSIENT_MIN_LPM = 2.0
_FLOW_TRANSIENT_MAX_LPM = 4.0
_FLOW_TRANSIENT_S = 10.0
_GLITCH_MIN_NTU = 200.0
_GLITCH_MAX_NTU = 500.0


class SensorSimulator:
    """Generate baseline telemetry and coupled silt-intrusion events for one node."""

    def __init__(
        self,
        node: NodeConfig,
        tick_seconds: float,
        glitch_rate: float = 0.005,
        rng: random.Random | None = None,
    ) -> None:
        self.node = node
        self.tick_seconds = float(tick_seconds)
        if self.tick_seconds <= 0:
            raise ValueError("tick_seconds must be positive")
        self.glitch_rate = float(glitch_rate)
        self.rng = random.Random(node.seed) if rng is None else rng

    def sample(self, phase: str, elapsed_s: float) -> Reading:
        """Return one reading; ``elapsed_s`` is cumulative seconds since simulation start.

        It is deliberately not phase-local time: event rise, washout, the flow
        transient and the diurnal cycle must continue across phase changes.
        """
        dt_s = elapsed_s - self.node.onset_offset_s
        event_active = phase != "baseline" and dt_s >= 0.0
        event_u = 0.0
        if event_active:
            event_u = (
                self.node.amplitude
                * (1.0 - math.exp(-dt_s / _TAU_RISE_S))
                * math.exp(-dt_s / _TAU_WASH_S)
            )

        tick = elapsed_s / self.tick_seconds
        diurnal = math.sin(2.0 * math.pi * tick / (_DAY_S / self.tick_seconds))
        turbidity_base = 5.0 + 0.6 * diurnal + self.rng.uniform(-1.0, 1.0)
        tds_25_base = 185.0 + 8.0 * diurnal + self.rng.uniform(-12.0, 12.0)
        temp_c = 20.0 + 3.0 * diurnal + self.rng.uniform(-0.25, 0.25)
        flow = 13.0 + 0.8 * diurnal + self.rng.uniform(-1.7, 1.7)

        ph = max(_PH_FLOOR, 7.2 - _PH_DROP * (1.0 - math.exp(-event_u / 150.0)))
        turbidity = turbidity_base + event_u
        tds_25 = tds_25_base + _TDS_COUPLING_PPM_PER_NTU * event_u
        # Task 3 moves this conversion into dsp.ambient_tds() so it has one home.
        tds_ambient = tds_25 * (1.0 + _TDS_ALPHA_PER_C * (temp_c - 25.0))

        if event_active and dt_s <= _FLOW_TRANSIENT_S:
            flow += self.rng.uniform(_FLOW_TRANSIENT_MIN_LPM, _FLOW_TRANSIENT_MAX_LPM)
        if phase == "supply_interruption":
            flow = 0.0

        glitch = self.rng.random() < self.glitch_rate
        if glitch:
            magnitude = self.rng.uniform(_GLITCH_MIN_NTU, _GLITCH_MAX_NTU)
            turbidity += magnitude if self.rng.random() < 0.5 else -magnitude

        values = {
            "ph": round(ph, 2),
            "turbidity_ntu": round(turbidity, 1),
            "tds_ppm": round(tds_ambient, 0),
            "water_temp_c": round(temp_c, 1),
            "flow_lpm": round(flow, 1),
            "latitude": self.node.latitude,
            "longitude": self.node.longitude,
            "node_id": self.node.node_id,
            "event_u": round(event_u, 2),
            "glitch": glitch,
        }
        return Reading(
            node_id=self.node.node_id,
            ts_ms=int(time.time() * 1000),
            values=values,
        )
