"""Multi-node orchestration: sample, guard, buffer and fan out telemetry."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import replace
from typing import Any, Callable

from .dsp import HampelFilter, process_reading
from .hysteresis import HysteresisEngine
from .models import EdgeConfig, NodeConfig, Reading
from .publisher import Publisher
from .sensors import SensorSimulator
from .storage import BufferStore
from .validity import ValidityGates

logger = logging.getLogger(__name__)

_CHANNELS = ("ph", "turbidity_ntu", "tds_ppm", "water_temp_c", "flow_lpm")
_PHASE_KEYS = {
    "b": "baseline",
    "s": "silt_intrusion",
    "i": "supply_interruption",
    "r": "recovery",
}
_HELP = (
    "b/s/i/r: force phase | o: toggle outage | k/K: node-a off/on | "
    "f/F: inject/clear stuck fault | a: retry auth | p: pause | q: quit | h: help"
)


class EdgeRuntime:
    """Own the per-node sampling loops and the shared store-and-forward path."""

    def __init__(
        self,
        config: EdgeConfig,
        scenario: Any,
        store: BufferStore,
        publishers: dict[str, Publisher],
        engines: dict[str, HysteresisEngine],
        now_fn: Callable[[], float] | None = None,
    ) -> None:
        self.config = config
        self.scenario = scenario
        self.store = store
        self.publishers = publishers
        self.engines = engines
        self._now_fn = time.monotonic if now_fn is None else now_fn
        self._start_s = float(self._now_fn())
        self._stop_event = threading.Event()
        self.threads: list[threading.Thread] = []
        self.published: list[dict[str, Any]] = []
        self.node_state: dict[str, dict[str, Any]] = {
            node_id: {"powered_off": False} for node_id in publishers
        }

        self.outage_active = False
        self.paused = False
        self._replay_pending = False
        self._last_severity: dict[str, str] = {}
        self._stuck_snapshots: dict[str, dict[str, Any]] = {}
        self._raw_history: dict[str, list[dict]] = {
            node_id: [] for node_id in publishers
        }
        self._last_values: dict[str, dict[str, Any]] = {}

        scenario_nodes = {
            str(node["node_id"]): node for node in getattr(scenario, "nodes", [])
        }
        self.node_configs: dict[str, NodeConfig] = {}
        self.simulators: dict[str, SensorSimulator] = {}
        self.validators: dict[str, ValidityGates] = {}
        self.filters: dict[str, dict[str, HampelFilter]] = {}
        for node in config.nodes:
            override = scenario_nodes.get(node.node_id, {})
            effective = replace(
                node,
                amplitude=float(override.get("amplitude", node.amplitude)),
                onset_offset_s=float(override.get("onset_offset_s", node.onset_offset_s)),
            )
            self.node_configs[node.node_id] = effective
            self.simulators[node.node_id] = SensorSimulator(
                effective, config.tick_seconds
            )
            self.validators[node.node_id] = ValidityGates(
                fault_samples=config.validity_fault_samples
            )
            self.filters[node.node_id] = {
                channel: HampelFilter() for channel in _CHANNELS
            }

    @property
    def stopped(self) -> threading.Event:
        """Event consumed by the CLI to end its command loop."""
        return self._stop_event

    @property
    def elapsed_s(self) -> float:
        """Cumulative seconds since simulation start, never phase-local."""
        return max(0.0, float(self._now_fn()) - self._start_s)

    def start(self) -> None:
        """Start one sampling thread per node plus the flush/timeline thread."""
        if self.threads:
            return
        self._stop_event.clear()
        tick = max(0.01, float(self.config.tick_seconds))
        for node_id in self.publishers:
            thread = threading.Thread(
                target=self._sampling_loop,
                args=(node_id, tick),
                name=f"sample-{node_id}",
                daemon=True,
            )
            thread.start()
            self.threads.append(thread)
        flusher = threading.Thread(
            target=self._flush_loop, args=(tick,), name="flush", daemon=True
        )
        flusher.start()
        self.threads.append(flusher)
        logger.info("STATE: runtime started nodes=%s", ",".join(self.publishers))

    def stop(self) -> None:
        """Signal every loop to stop and wait for all of them to exit.

        Publish batches can block for the full PUBACK timeout (10 s), so a
        bounded join could return while a flush thread still owns the store
        and publishers. Callers may therefore clean up only after stop().
        """
        self._stop_event.set()
        for thread in self.threads:
            thread.join()
        logger.info("STATE: runtime stopped")

    def _sampling_loop(self, node_id: str, tick: float) -> None:
        while not self._stop_event.wait(tick):
            if not self.paused:
                self._sample_node(node_id)

    def _flush_loop(self, tick: float) -> None:
        while not self._stop_event.wait(tick):
            self.scenario.tick()
            self.flush()

    def step_once(self) -> None:
        """Take one tick for every node and flush the buffer (test seam)."""
        if self._stop_event.is_set():
            return
        self.scenario.tick()
        if not self.paused:
            for node_id in self.publishers:
                self._sample_node(node_id)
        self.flush()

    def _sample_node(self, node_id: str) -> None:
        """Generate one reading (or a power-off status row) and buffer it."""
        now = self.elapsed_s
        if self.node_state[node_id].get("powered_off"):
            self._buffer_inactive_status(node_id, now)
            return

        raw = self.simulators[node_id].sample(self.scenario.phase, now)
        raw.values.setdefault("node_id", node_id)
        stuck_injected = bool(
            self.node_state[node_id].get("stuck_fault")
            or node_id in self._stuck_snapshots
        )
        if stuck_injected:
            self._apply_stuck(node_id, raw)

        # The STUCK gate only applies during an event or an injected fault:
        # baseline pH is rounded to 2 dp and would otherwise look frozen.
        event_active = float(raw.values.get("event_u", 0.0)) > 0.0
        history = self._raw_history[node_id]
        check_history = history if event_active or stuck_injected else []
        clean, flags = self.validators[node_id].check(raw.values, check_history)
        if flags:
            logger.info("VALIDITY: node=%s flags=%s", node_id, ",".join(flags))
        if check_history is history:
            history.append(dict(raw.values))
            del history[:-9]
        else:
            history.clear()

        self.engines[node_id].note_activity(now)
        processed = process_reading(
            Reading(node_id=node_id, ts_ms=raw.ts_ms, values=clean),
            self.filters[node_id],
        )
        severity, alarms = self.engines[node_id].evaluate(
            processed.values, now, flags
        )
        self._buffer(node_id, raw.ts_ms, processed.values, severity, alarms, flags)
        self._last_values[node_id] = dict(processed.values)
        self._log_severity(node_id, severity)

    def _buffer_inactive_status(self, node_id: str, now: float) -> None:
        """Keep publishing status for a powered-off node without sampling it."""
        ts_ms = int(time.time() * 1000)
        raw = {
            "node_id": node_id,
            "latitude": self.node_configs[node_id].latitude,
            "longitude": self.node_configs[node_id].longitude,
            "ph": 7.2,
            "turbidity_ntu": 5.0,
            "tds_ppm": 180.0,
            "water_temp_c": 20.0,
            "flow_lpm": 12.0,
        }
        # Derive ph_25/wqi/wqi_band with the same DSP function as live samples.
        # Empty filters avoid polluting the per-node Hampel windows with a
        # synthetic status reading.
        processed = process_reading(
            Reading(node_id=node_id, ts_ms=ts_ms, values=raw), {}
        )
        values = dict(self._last_values.get(node_id, processed.values))
        severity, alarms = self.engines[node_id].evaluate(values, now, [])
        self._buffer(node_id, ts_ms, values, severity, alarms, [])
        self._log_severity(node_id, severity)

    def _apply_stuck(self, node_id: str, raw: Reading) -> None:
        """Freeze raw channels and pre-seed the 9-sample STUCK window."""
        snapshot = self._stuck_snapshots.get(node_id)
        if snapshot is None:
            snapshot = {
                channel: raw.values[channel]
                for channel in _CHANNELS
                if channel in raw.values
            }
            self._stuck_snapshots[node_id] = snapshot
            self._raw_history[node_id] = [dict(snapshot) for _ in range(9)]
            logger.warning("FAULT: node=%s stuck sensor active", node_id)
        for channel, value in snapshot.items():
            raw.values[channel] = value

    def _buffer(
        self,
        node_id: str,
        ts_ms: int,
        values: dict[str, Any],
        severity: str,
        alarms: list[str],
        flags: list[str],
    ) -> None:
        """Add audit keys and persist the row before any publish attempt."""
        values["severity"] = severity
        values["active_alarms"] = ",".join(alarms)
        values["buffer_depth"] = self.store.depth(node_id) + 1
        values["node_inactive"] = "Node Inactive" in alarms
        values["validity_flags"] = ",".join(flags)
        values["sensor_fault"] = bool(flags)
        self.store.enqueue({"ts": ts_ms, "values": values})

    def _log_severity(self, node_id: str, severity: str) -> None:
        previous = self._last_severity.get(node_id, "Normal")
        if severity == previous:
            return
        self._last_severity[node_id] = severity
        if severity == "Fault":
            logger.warning("FAULT: node=%s severity=Fault", node_id)
        else:
            logger.info("STATE: node=%s severity=%s", node_id, severity)

    def flush(self) -> None:
        """Claim pending rows and publish each node's batch independently."""
        if self.outage_active:
            return
        for node_id, rows in self.store.claim():
            if self._replay_pending:
                logger.info("REPLAY: node=%s rows=%d", node_id, len(rows))
            publisher = self.publishers.get(node_id)
            if publisher is None:
                logger.warning("NETWORK_FAILURE: node=%s has no publisher", node_id)
                self.store.fail(rows)
                continue
            if getattr(publisher, "_retries_suspended", False):
                logger.warning("AUTH_FAILURE: node=%s retries suspended", node_id)
            acked = bool(publisher.publish_batch(rows))
            self.published.append(
                {
                    "node_id": node_id,
                    "ts": rows[0]["ts"],
                    "values": dict(rows[0]["values"]),
                    "acked": acked,
                }
            )
            if acked:
                self.store.ack(rows)
            else:
                self.store.fail(rows)
                logger.warning(
                    "BUFFER: node=%s rows=%d returned to pending", node_id, len(rows)
                )
        self._replay_pending = False
        depths = {node_id: self.store.depth(node_id) for node_id in self.publishers}
        logger.info("BUFFER: total=%d per_node=%s", self.store.depth(), depths)

    def handle_command(self, command: str) -> None:
        """Apply an interactive single-key demo command."""
        key = command.strip()
        if not key:
            return
        key = key[0]
        if key in _PHASE_KEYS:
            self.scenario.set_phase(_PHASE_KEYS[key])
        elif key == "o":
            self._toggle_outage()
        elif key == "k":
            self._power("node-a", False)
        elif key == "K":
            self._power("node-a", True)
        elif key == "f":
            self._inject_fault("node-a")
        elif key == "F":
            self._clear_fault("node-a")
        elif key == "a":
            self._retry_auth()
        elif key == "p":
            self.paused = not self.paused
            logger.info("STATE: %s", "PAUSED" if self.paused else "RESUMED")
        elif key == "q":
            self._stop_event.set()
            logger.info("STATE: QUIT requested")
        elif key == "h":
            logger.info("HELP: %s", _HELP)
        else:
            logger.warning("STATE: unknown command %r", key)

    def _toggle_outage(self) -> None:
        self.outage_active = not self.outage_active
        if self.outage_active:
            logger.warning("OUTAGE: START; continuing local sampling")
            return
        logger.warning("OUTAGE: END; draining buffered rows")
        self._replay_pending = self.store.depth() > 0
        self.flush()

    def _power(self, node_id: str, on: bool) -> None:
        self.node_state[node_id]["powered_off"] = not on
        logger.info("STATE: node=%s %s", node_id, "POWER_ON" if on else "POWER_OFF")

    def _inject_fault(self, node_id: str) -> None:
        self.node_state[node_id]["stuck_fault"] = True
        self._stuck_snapshots.pop(node_id, None)
        logger.warning("FAULT: node=%s stuck_fault injected", node_id)

    def _clear_fault(self, node_id: str) -> None:
        self.node_state[node_id].pop("stuck_fault", None)
        self._stuck_snapshots.pop(node_id, None)
        self._raw_history[node_id].clear()
        logger.warning("FAULT: node=%s stuck_fault cleared", node_id)

    def _retry_auth(self) -> None:
        for node_id, publisher in self.publishers.items():
            retry = getattr(publisher, "retry_auth", None)
            if callable(retry):
                retry()
            connect = getattr(publisher, "connect", None)
            if (
                callable(connect)
                and not getattr(publisher, "is_connected", True)
                and not getattr(publisher, "_retries_suspended", False)
            ):
                connect()
        self.flush()
        logger.info("STATE: auth retry requested for all nodes")
