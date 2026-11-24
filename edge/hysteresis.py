"""Per-rule hysteresis, debounce and severity aggregation for one node."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

_ALARM_SEVERITY = {
    "High Turbidity": "Warning",
    "Rapid Turbidity Rise": "Warning",
    "Supply Interruption": "Major",
    "Gross Contamination": "Critical",
    "pH Out of Range": "Critical",
    "High TDS": "Critical",
    "Node Inactive": "Critical",
    "Severe pH Persistence": "Critical",
    "Sensor Fault": "Fault",
}
_SEVERITY_RANK = {
    "Normal": 0,
    "Warning": 1,
    "Major": 2,
    "Critical": 3,
    "Fault": 4,
}


def _number(values: dict, key: str) -> float | None:
    """Return a finite telemetry number, or None when the channel is absent."""
    value = values.get(key)
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    ):
        return float(value)
    return None


@dataclass
class _Debounce:
    """Latch driven by consecutive qualifying samples, not accumulated ones.

    While inactive, a breach increments the counter and any non-breach resets
    it; at ``samples`` breaches the latch raises and the same counter becomes
    the clear counter. While active, a clear-eligible sample increments that
    counter and any deadband or renewed breach resets it; at ``samples``
    clears the latch falls back to inactive. Deadband samples therefore hold
    the current state without advancing progress toward a change.
    """

    samples: int
    count: int = 0
    active: bool = False

    def __post_init__(self) -> None:
        if self.samples < 1:
            raise ValueError("samples must be at least 1")

    def observe(self, breached: bool, cleared: bool) -> bool:
        if not self.active:
            self.count = self.count + 1 if breached else 0
            if self.count >= self.samples:
                self.active = True
                self.count = 0
        elif cleared:
            self.count += 1
            if self.count >= self.samples:
                self.active = False
                self.count = 0
        else:
            # Deadband or renewed breach: the alarm holds, but clear progress
            # must restart from the next consecutive clear-eligible sample.
            self.count = 0
        return self.active


class HysteresisEngine:
    """Stateful alarm evaluator consuming one node's processed telemetry."""

    def __init__(
        self,
        flow_zero_sustained_s: float = 30.0,
        node_inactive_s: float = 90.0,
        derivative_window_s: float = 50.0,
        derivative_delta_ntu: float = 30.0,
        fault_samples: int = 3,
    ) -> None:
        if min(flow_zero_sustained_s, node_inactive_s, derivative_window_s) < 0:
            raise ValueError("time windows must be non-negative")
        if derivative_delta_ntu < 0:
            raise ValueError("derivative_delta_ntu must be non-negative")

        self.flow_zero_sustained_s = float(flow_zero_sustained_s)
        self.node_inactive_s = float(node_inactive_s)
        self.derivative_window_s = float(derivative_window_s)
        self.derivative_delta_ntu = float(derivative_delta_ntu)
        self.fault_samples = int(fault_samples)

        self._high_turbidity = _Debounce(1)
        self._gross = _Debounce(1)
        self._ph_out = _Debounce(3)
        self._high_tds = _Debounce(3)
        self._severe_ph = _Debounce(3)
        self._derivative = _Debounce(1)
        self._fault = _Debounce(self.fault_samples)

        self._last_activity_s: float | None = None
        self._flow_zero_since_s: float | None = None
        self._turbidity: deque[tuple[float, float]] = deque()

    def note_activity(self, now_s: float) -> None:
        """Record a generated reading; only the caller marks a node as live."""
        self._last_activity_s = float(now_s)

    def evaluate(
        self,
        values: dict,
        now_s: float,
        validity_flags: list[str] | None = None,
    ) -> tuple[str, list[str]]:
        """Advance every rule by one sample and return severity and alarms."""
        now = float(now_s)
        active: set[str] = set()

        turbidity = _number(values, "turbidity_ntu")
        ph = _number(values, "ph")
        tds = _number(values, "tds_ppm")
        flow = _number(values, "flow_lpm")

        if self._high_turbidity.observe(
            breached=turbidity is not None and turbidity > 50.0,
            cleared=turbidity is not None and turbidity < 40.0,
        ):
            active.add("High Turbidity")
        if self._gross.observe(
            breached=turbidity is not None and turbidity > 200.0,
            cleared=turbidity is not None and turbidity < 150.0,
        ):
            active.add("Gross Contamination")

        if self._ph_out.observe(
            breached=ph is not None and (ph < 6.5 or ph > 8.5),
            cleared=ph is not None and 6.7 <= ph <= 8.3,
        ):
            active.add("pH Out of Range")
        if self._high_tds.observe(
            breached=tds is not None and tds > 500.0,
            cleared=tds is not None and tds < 450.0,
        ):
            active.add("High TDS")
        if self._severe_ph.observe(
            breached=ph is not None and (ph < 6.0 or ph > 9.0),
            cleared=ph is not None and 6.2 <= ph <= 8.8,
        ):
            active.add("Severe pH Persistence")

        # A node that has never reported is treated as fresh at its first
        # evaluation instead of being declared dead before a clock exists.
        if self._last_activity_s is None:
            self._last_activity_s = now
        inactive_for = now - self._last_activity_s
        if inactive_for >= self.node_inactive_s:
            active.add("Node Inactive")

        if flow is not None and flow == 0.0:
            if self._flow_zero_since_s is None:
                self._flow_zero_since_s = now
            if now - self._flow_zero_since_s >= self.flow_zero_sustained_s:
                active.add("Supply Interruption")
        else:
            self._flow_zero_since_s = None

        if self._derivative.observe(
            breached=self._update_derivative(turbidity, now),
            cleared=(
                turbidity is None
                or self._turbidity_rise() < self.derivative_delta_ntu
            ),
        ):
            active.add("Rapid Turbidity Rise")

        if self._fault.observe(
            breached=bool(validity_flags),
            cleared=not validity_flags,
        ):
            active.add("Sensor Fault")

        alarms = sorted(active)
        severity = max(
            (_ALARM_SEVERITY[name] for name in alarms),
            key=_SEVERITY_RANK.__getitem__,
            default="Normal",
        )
        return severity, alarms

    def _update_derivative(self, turbidity: float | None, now: float) -> bool:
        """Append this sample, prune the lookback and report a fresh rise."""
        if turbidity is None:
            self._turbidity.clear()
            return False
        self._turbidity.append((now, turbidity))
        cutoff = now - self.derivative_window_s
        while self._turbidity and self._turbidity[0][0] < cutoff:
            self._turbidity.popleft()
        return self._turbidity_rise() > self.derivative_delta_ntu

    def _turbidity_rise(self) -> float:
        """Signed change from the oldest in-window sample to the newest."""
        if not self._turbidity:
            return 0.0
        return self._turbidity[-1][1] - self._turbidity[0][1]
