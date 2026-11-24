"""End-to-end tests for the multi-node runtime and demo CLI."""

from __future__ import annotations

import io
import logging
import sys
import threading
from pathlib import Path

import pytest

from edge import cli as cli_module
from edge.cli import main as cli_main
from edge.dsp import brown_wqi
from edge.publisher import NullPublisher
from edge.runtime import EdgeRuntime
from edge.storage import BufferStore

DEMO_SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "demo.json"
REQUIRED_TELEMETRY_KEYS = {
    "node_id", "ph", "turbidity_ntu", "tds_ppm", "water_temp_c", "flow_lpm",
    "latitude", "longitude", "ph_25", "wqi", "wqi_band", "severity",
    "active_alarms", "buffer_depth", "node_inactive", "validity_flags",
    "sensor_fault",
}


class BlockingPublisher(NullPublisher):
    """Sink whose publish blocks until released, recording cleanup ordering."""

    def __init__(self, node_id: str = "node-a") -> None:
        super().__init__(node_id)
        self.entered = threading.Event()
        self.release = threading.Event()
        self.events: list[str] = []

    def publish_batch(self, batch: list[dict]) -> bool:
        self.entered.set()
        if not self.release.wait(timeout=10):
            return False
        self.events.append("published")
        return True

    def disconnect(self) -> None:
        self.events.append("disconnect")
        super().disconnect()


def test_outage_buffers_then_replays_with_original_timestamps(
    tmp_path, fake_clock, expected_first_ts, build_runtime
):
    rt = build_runtime(tmp_path, fake_clock)
    rt.step_once()                      # baseline, online
    rt.handle_command("o")              # OUTAGE START
    fake_clock.advance(10)
    rt.step_once()
    assert rt.store.depth() >= 1
    rt.handle_command("o")              # OUTAGE END
    rt.step_once()
    assert rt.store.depth() == 0
    assert rt.published[-1]["ts"] == expected_first_ts


def test_mixed_nodes_partial_failure_acks_only_success(tmp_path, fake_clock, build_runtime, failing_publisher, caplog):
    rt = build_runtime(tmp_path, fake_clock)
    rt.publishers["node-b"] = failing_publisher
    rt.step_once()
    assert all(r["values"]["node_id"] != "node-b" for r in rt.published if r.get("acked"))
    assert "NETWORK_FAILURE" in caplog.text
    assert rt.store.depth("node-b") >= 1
    assert rt.store.depth("node-a") == 0
    assert any(
        row["acked"] is False and row["node_id"] == "node-b" for row in rt.published
    )


def test_auth_failure_keeps_buffering(tmp_path, fake_clock, build_runtime, caplog):
    rt = build_runtime(tmp_path, fake_clock)
    rt.publishers["node-a"]._retries_suspended = True
    rt.step_once(); fake_clock.advance(5); rt.step_once()
    assert rt.store.depth() >= 1
    assert all(b["node_id"] != "node-a" or b["acked"] is False for b in rt.published)
    assert "AUTH_FAILURE" in caplog.text
    assert rt.store.depth("node-b") == 0


def test_validity_fault_wiring(tmp_path, fake_clock, build_runtime, caplog):
    rt = build_runtime(tmp_path, fake_clock)
    rt.handle_command("f")
    for t in [0, 5, 10, 15, 20]:
        fake_clock.advance(5); rt.step_once()
    assert "VALIDITY:" in caplog.text
    assert any(b.get("values", {}).get("severity") == "Fault" for b in rt.published)
    rt.handle_command("F")
    assert "stuck_fault" not in rt.node_state["node-a"]


def test_sensors_receive_cumulative_time_across_phase_changes(
    tmp_path, fake_clock, build_runtime, monkeypatch
):
    rt = build_runtime(tmp_path, fake_clock)
    seen: list[tuple[str, float]] = []
    original = rt.simulators["node-a"].sample

    def spy(phase: str, elapsed_s: float):
        seen.append((phase, elapsed_s))
        return original(phase, elapsed_s)

    monkeypatch.setattr(rt.simulators["node-a"], "sample", spy)
    rt.step_once()
    rt.handle_command("s")              # scenario phase clock restarts here
    assert rt.scenario.elapsed_s == pytest.approx(0.0)
    fake_clock.advance(10)
    rt.step_once()

    assert [phase for phase, _ in seen] == ["baseline", "silt_intrusion"]
    assert seen[1][1] - seen[0][1] == pytest.approx(10.0)
    assert rt.scenario.elapsed_s == pytest.approx(10.0)


def test_baseline_without_fault_has_no_spurious_stuck_flags(
    tmp_path, fake_clock, build_runtime, caplog
):
    rt = build_runtime(tmp_path, fake_clock)
    for _ in range(10):
        rt.step_once()
    assert all(row["values"]["validity_flags"] == "" for row in rt.published)
    assert all(row["values"]["severity"] != "Fault" for row in rt.published)
    assert "VALIDITY:" not in caplog.text


def test_stuck_fault_flags_telemetry_and_clears_after_recovery(tmp_path, fake_clock, build_runtime):
    rt = build_runtime(tmp_path, fake_clock)
    rt.handle_command("f")
    assert rt.node_state["node-a"]["stuck_fault"] is True
    for _ in range(5):
        fake_clock.advance(5)
        rt.step_once()

    fault_rows = [
        row for row in rt.published
        if row["node_id"] == "node-a" and row["values"]["severity"] == "Fault"
    ]
    assert fault_rows
    assert all("STUCK" in row["values"]["validity_flags"] for row in fault_rows)
    assert all(row["values"]["sensor_fault"] is True for row in fault_rows)

    rt.handle_command("F")
    assert "stuck_fault" not in rt.node_state["node-a"]
    for _ in range(5):
        fake_clock.advance(5)
        rt.step_once()

    last_node_a = [row for row in rt.published if row["node_id"] == "node-a"][-1]
    assert last_node_a["values"]["severity"] != "Fault"
    assert last_node_a["values"]["sensor_fault"] is False


def test_power_off_raises_node_inactive_and_power_on_recovers(tmp_path, fake_clock, build_runtime):
    rt = build_runtime(tmp_path, fake_clock)
    rt.handle_command("k")
    assert rt.node_state["node-a"]["powered_off"] is True
    for _ in range(19):
        fake_clock.advance(5)
        rt.step_once()

    inactive_rows = [
        row for row in rt.published
        if row["node_id"] == "node-a"
        and row["values"]["node_inactive"] is True
        and "Node Inactive" in row["values"]["active_alarms"]
    ]
    assert inactive_rows

    rt.handle_command("K")
    fake_clock.advance(5)
    rt.step_once()
    last_node_a = [row for row in rt.published if row["node_id"] == "node-a"][-1]
    assert rt.node_state["node-a"]["powered_off"] is False
    assert last_node_a["values"]["node_inactive"] is False


def test_power_off_as_first_action_publishes_complete_telemetry(
    tmp_path, fake_clock, build_runtime
):
    rt = build_runtime(tmp_path, fake_clock)
    rt.handle_command("k")              # no powered-on sample has happened yet
    rt.step_once()

    rows = [row for row in rt.published if row["node_id"] == "node-a"]
    assert rows
    values = rows[0]["values"]
    assert REQUIRED_TELEMETRY_KEYS <= values.keys()
    wqi, band = brown_wqi(
        values["ph_25"], values["turbidity_ntu"], values["tds_ppm"]
    )
    assert values["wqi"] == pytest.approx(wqi)
    assert values["wqi_band"] == band


def test_auth_retry_resumes_publishing_and_drains_buffer(tmp_path, fake_clock, build_runtime):
    rt = build_runtime(tmp_path, fake_clock)
    rt.publishers["node-a"]._retries_suspended = True
    rt.step_once()
    fake_clock.advance(5)
    rt.step_once()
    assert rt.store.depth("node-a") >= 2

    rt.handle_command("a")
    assert rt.publishers["node-a"]._retries_suspended is False
    rt.step_once()
    assert rt.store.depth("node-a") == 0
    assert any(
        row["node_id"] == "node-a" and row["acked"] is True for row in rt.published
    )


def test_outage_pauses_publishing_but_not_sampling(tmp_path, fake_clock, build_runtime, caplog):
    caplog.set_level(logging.INFO)
    rt = build_runtime(tmp_path, fake_clock)
    rt.step_once()
    published_before = len(rt.published)

    rt.handle_command("o")
    assert "OUTAGE: START" in caplog.text
    for _ in range(3):
        fake_clock.advance(5)
        rt.step_once()
    assert len(rt.published) == published_before
    assert rt.store.depth() == 3 * len(rt.config.nodes)
    assert all(
        "Node Inactive" not in row["values"]["active_alarms"] for row in rt.published
    )

    rt.handle_command("o")
    assert "OUTAGE: END" in caplog.text
    assert "REPLAY" in caplog.text
    assert rt.store.depth() == 0
    assert "BUFFER" in caplog.text


def test_telemetry_contract_keys_and_per_node_depth(tmp_path, fake_clock, build_runtime):
    rt = build_runtime(tmp_path, fake_clock)
    rt.step_once()
    assert len(rt.published) == len(rt.config.nodes)
    required = {
        "node_id", "ph", "turbidity_ntu", "tds_ppm", "water_temp_c", "flow_lpm",
        "latitude", "longitude", "ph_25", "wqi", "wqi_band", "severity",
        "active_alarms", "buffer_depth", "node_inactive", "validity_flags",
        "sensor_fault",
    }
    for row in rt.published:
        assert required <= row["values"].keys()
        assert row["values"]["node_id"] == row["node_id"]
        assert isinstance(row["values"]["active_alarms"], str)
        assert isinstance(row["values"]["buffer_depth"], int)
        assert row["acked"] is True


def test_phase_commands_force_scenario_phase(tmp_path, fake_clock, build_runtime):
    rt = build_runtime(tmp_path, fake_clock)
    for key, phase in (("b", "baseline"), ("s", "silt_intrusion"),
                       ("i", "supply_interruption"), ("r", "recovery")):
        rt.handle_command(key)
        assert rt.scenario.phase == phase


def test_start_stop_joins_all_threads(tmp_path, fake_clock, build_runtime):
    rt = build_runtime(tmp_path, fake_clock)
    rt.config.tick_seconds = 0.02
    rt.start()
    assert rt.threads
    assert all(thread.is_alive() for thread in rt.threads)
    rt.stop()
    assert all(not thread.is_alive() for thread in rt.threads)


def test_stop_waits_for_blocked_flush_before_returning(
    tmp_path, fake_clock, build_runtime
):
    rt = build_runtime(tmp_path, fake_clock)
    blocker = BlockingPublisher()
    rt.publishers["node-a"] = blocker
    rt.config.tick_seconds = 0.01
    stop_done = threading.Event()
    try:
        rt.start()
        assert blocker.entered.wait(timeout=5)

        def do_stop() -> None:
            rt.stop()
            stop_done.set()

        stopper = threading.Thread(target=do_stop, name="test-stop")
        stopper.start()
        assert not stop_done.wait(0.2)   # stop() is still waiting on the flush
        assert "published" not in blocker.events
        assert "disconnect" not in blocker.events

        blocker.release.set()
        assert stop_done.wait(timeout=5)
        stopper.join(timeout=1)
        assert "published" in blocker.events
        assert all(not thread.is_alive() for thread in rt.threads)
    finally:
        blocker.release.set()
        rt.stop()


def test_cli_cleanup_waits_for_blocked_flush(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    blocker = BlockingPublisher()
    stores: list[BufferStore] = []
    stop_entered = threading.Event()

    class TrackingStore(BufferStore):
        def close(self) -> None:
            self.closed = True
            super().close()

    def store_factory(*args, **kwargs):
        store = TrackingStore(*args, **kwargs)
        store.closed = False
        stores.append(store)
        return store

    class TrackingRuntime(EdgeRuntime):
        def stop(self) -> None:
            stop_entered.set()
            super().stop()

    class GatedStdin(io.StringIO):
        def readline(self) -> str:
            assert blocker.entered.wait(timeout=10)
            return "q\n"

    monkeypatch.setattr(cli_module, "BufferStore", store_factory)
    monkeypatch.setattr(cli_module, "EdgeRuntime", TrackingRuntime)
    monkeypatch.setattr(
        cli_module,
        "_build_publishers",
        lambda node_ids, use_mqtt, config: {
            node_id: (
                blocker
                if node_id == "node-a"
                else NullPublisher(node_id)
            )
            for node_id in node_ids
        },
    )
    monkeypatch.setattr(sys, "stdin", GatedStdin())

    results: list[int] = []

    def run_cli() -> None:
        results.append(
            cli_main([
                "--scenario", str(DEMO_SCENARIO),
                "--dry-run",
                "--tick-seconds", "0.05",
                "--db", str(tmp_path / "cli-slow.db"),
                "--log-level", "INFO",
            ])
        )

    cli_thread = threading.Thread(target=run_cli, name="test-cli")
    cli_thread.start()
    try:
        assert blocker.entered.wait(timeout=10)
        assert stop_entered.wait(timeout=10)
        assert "published" not in blocker.events
        assert "disconnect" not in blocker.events
        assert stores and stores[0].closed is False

        blocker.release.set()
        cli_thread.join(timeout=10)
        assert results == [0]
        assert "published" in blocker.events
        assert blocker.events.index("published") < blocker.events.index("disconnect")
        assert stores[0].closed is True
    finally:
        blocker.release.set()
        cli_thread.join(timeout=10)


def test_cli_main_writes_log_and_exits_on_q(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "stdin", io.StringIO("q\n"))
    assert cli_main([
        "--scenario", str(DEMO_SCENARIO),
        "--dry-run",
        "--tick-seconds", "0.5",
        "--db", str(tmp_path / "cli.db"),
        "--log-level", "INFO",
    ]) == 0
    assert (tmp_path / "logs" / "edge.log").is_file()
