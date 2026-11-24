"""Physical validity gates applied to raw sensor values before DSP."""

from __future__ import annotations

import logging
import math
from typing import Any

logger = logging.getLogger(__name__)

_PHYSICAL_RANGES: dict[str, tuple[float, float]] = {
    "ph": (0.0, 14.0),
    "turbidity_ntu": (0.0, 4000.0),
    "tds_ppm": (0.0, 5000.0),
    "water_temp_c": (-5.0, 60.0),
    "flow_lpm": (0.0, 500.0),
}
_ENDPOINTS: dict[str, tuple[float, float]] = {
    "ph": (0.0, 14.0),
    "turbidity_ntu": (0.0, 4000.0),
    "water_temp_c": (-5.0, 60.0),
}
_BASELINE: dict[str, float] = {
    "ph": 7.2,
    "turbidity_ntu": 5.0,
    "tds_ppm": 180.0,
    "water_temp_c": 20.0,
    "flow_lpm": 12.0,
}
_STUCK_LOOKBACK = 9


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


class ValidityGates:
    """Flag impossible raw samples and substitute their last valid value."""

    def __init__(self, fault_samples: int = 3) -> None:
        self.fault_samples = fault_samples
        self._last_valid: dict[str, float] = {}

    def check(self, values: dict, history: list[dict]) -> tuple[dict, list[str]]:
        """Return a copy of ``values`` with invalid channels substituted."""
        clean = dict(values)
        flags: list[str] = []
        impossible_ph_tds = self._has_impossible_ph_tds(values)

        for channel, (low, high) in _PHYSICAL_RANGES.items():
            if channel not in values:
                continue
            raw = values[channel]
            if not _is_number(raw):
                flag = "SATURATION"
            elif raw < low or raw > high:
                flag = "SATURATION"
            elif channel in _ENDPOINTS and raw in _ENDPOINTS[channel]:
                flag = "OPEN_SHORT"
            elif impossible_ph_tds and channel == "ph":
                flag = "SANITY"
            elif self._is_stuck(channel, raw, history):
                flag = "STUCK"
            else:
                self._last_valid[channel] = float(raw)
                continue

            clean[channel] = self._substitute(channel, history)
            self._record(flags, flag, channel, raw, clean[channel])

        return clean, flags

    @staticmethod
    def _record(
        flags: list[str], flag: str, channel: str, raw: Any, replacement: Any
    ) -> None:
        if flag not in flags:
            flags.append(flag)
        logger.warning(
            "VALIDITY: %s channel=%s value=%s substituted=%s",
            flag,
            channel,
            raw,
            replacement,
        )

    @staticmethod
    def _is_stuck(channel: str, raw: float, history: list[dict]) -> bool:
        recent = [entry.get(channel) for entry in history[-_STUCK_LOOKBACK:]]
        return len(recent) == _STUCK_LOOKBACK and all(value == raw for value in recent)

    def _substitute(self, channel: str, history: list[dict]) -> float:
        low, high = _PHYSICAL_RANGES[channel]
        endpoints = _ENDPOINTS.get(channel, ())
        for prior in reversed(history):
            candidate = prior.get(channel)
            if (
                _is_number(candidate)
                and low <= candidate <= high
                and candidate not in endpoints
            ):
                return float(candidate)
        return self._last_valid.get(channel, _BASELINE[channel])

    @staticmethod
    def _has_impossible_ph_tds(values: dict) -> bool:
        ph = values.get("ph")
        tds = values.get("tds_ppm")
        if not (_is_number(ph) and _is_number(tds)):
            return False
        return (ph < 5.0 and tds > 1000.0) or (ph > 10.0 and tds < 20.0)
