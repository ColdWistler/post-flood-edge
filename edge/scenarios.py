"""Scripted demo timeline: loading and advancing ordered scenario phases."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)


def load_scenario(path: Path) -> dict[str, Any]:
    """Read a JSON scenario file into the dict consumed by ScenarioController."""
    return json.loads(Path(path).read_text(encoding="utf-8"))


class ScenarioController:
    """Track the phase due on a clock, with manual phase forcing and looping."""

    def __init__(
        self,
        scenario: dict[str, Any],
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._phases: list[dict[str, Any]] = list(scenario["phases"])
        # Retain the scenario's per-node demo constants for the runtime so it
        # does not have to duplicate amplitudes and onset offsets in code.
        self.nodes: list[dict[str, Any]] = list(scenario.get("nodes", []))
        if not self._phases:
            raise ValueError("scenario has no phases")
        self._durations = [float(phase["duration_s"]) for phase in self._phases]
        if any(duration <= 0 for duration in self._durations):
            raise ValueError("phase durations must be positive")
        self._cycle_s = sum(self._durations)
        self._now_fn = now_fn
        self._index = 0
        self.phase_start = float(now_fn())

    @property
    def phase(self) -> str:
        """Name of the current phase."""
        return str(self._phases[self._index]["name"])

    @property
    def elapsed_s(self) -> float:
        """Seconds spent in the current phase (resets on every transition)."""
        return self._now_fn() - self.phase_start

    def tick(self) -> str:
        """Advance to the phase due now, logging transitions and looping."""
        elapsed = self.elapsed_s
        if elapsed >= self._cycle_s:
            cycles = int(elapsed // self._cycle_s)
            self.phase_start += cycles * self._cycle_s
            elapsed -= cycles * self._cycle_s
        while elapsed >= self._durations[self._index]:
            elapsed -= self._durations[self._index]
            self.phase_start += self._durations[self._index]
            previous = self.phase
            self._index = (self._index + 1) % len(self._phases)
            logger.info("STATE: %s -> %s", previous, self.phase)
        return self.phase

    def set_phase(self, name: str) -> str:
        """Force a named phase and restart its elapsed time from zero."""
        if name not in [phase["name"] for phase in self._phases]:
            raise ValueError(f"unknown phase: {name}")
        previous = self.phase
        self._index = next(
            index for index, phase in enumerate(self._phases) if phase["name"] == name
        )
        self.phase_start = float(self._now_fn())
        logger.info("STATE: %s -> %s", previous, self.phase)
        return self.phase
