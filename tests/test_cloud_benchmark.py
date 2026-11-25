"""Tests for the Cloud Track benchmark measurement helpers."""

from __future__ import annotations

import json
import sqlite3
import urllib.error
from pathlib import Path

import pytest

from cloud import benchmark
from cloud.benchmark import LogEvent, ThingsBoardClient


def _row(node: str, ts: int, status: str = "synced", alarms: str = "") -> dict:
    return {
        "id": ts,
        "ts_ms": ts,
        "status": status,
        "node_id": node,
        "values": {"node_id": node, "active_alarms": alarms},
    }


def test_percentile_and_summary():
    assert benchmark.percentile([1, 2, 3, 4], 0.5) == pytest.approx(2.5)
    assert benchmark.percentile([5], 0.95) == 5.0
    assert benchmark.percentile([], 0.5) is None
    summary = benchmark.summarise([10, 20, 30, 40])
    assert summary["count"] == 4
    assert summary["mean"] == pytest.approx(25.0)
    assert summary["median"] == pytest.approx(25.0)
    assert summary["p95"] == pytest.approx(38.5)
    assert summary["max"] == 40.0


def test_replay_metrics_from_log_events():
    base = 1_700_000_000.0
    events = [
        LogEvent(base, "WARNING", "edge.runtime", "OUTAGE: START; continuing local sampling"),
        LogEvent(base + 1, "INFO", "edge.runtime", "BUFFER: total=20 per_node={}"),
        LogEvent(base + 2, "WARNING", "edge.runtime", "OUTAGE: END; draining buffered rows"),
        LogEvent(base + 2, "INFO", "edge.runtime", "REPLAY: node=node-a rows=20"),
        LogEvent(base + 3, "INFO", "edge.publisher", "PUBLISH node=node-a rows=10 ts=[1]"),
        LogEvent(base + 4, "INFO", "edge.publisher", "PUBLISH node=node-a rows=15 ts=[2]"),
        LogEvent(base + 5, "INFO", "edge.runtime", "BUFFER: total=0 per_node={}"),
    ]
    results = benchmark.replay_metrics(events)
    assert len(results) == 1
    assert results[0]["recovery_s"] == pytest.approx(3.0)
    # REPLAY is the exact buffered count. The two PUBLISH lines total 25 rows
    # because live samples published during the drain are included there.
    assert results[0]["rows"] == 20
    assert results[0]["row_count_source"] == "REPLAY"
    assert results[0]["throughput_rps"] == pytest.approx(20 / 3)
    assert results[0]["fully_drained"] is True


def test_replay_metrics_falls_back_to_publish_rows_for_older_logs():
    base = 1_700_000_000.0
    events = [
        LogEvent(base, "WARNING", "edge.runtime", "OUTAGE: START; continuing local sampling"),
        LogEvent(base + 1, "WARNING", "edge.runtime", "OUTAGE: END; draining buffered rows"),
        LogEvent(base + 2, "INFO", "edge.publisher", "PUBLISH node=node-a rows=12 ts=[1]"),
        LogEvent(base + 3, "INFO", "edge.runtime", "BUFFER: total=0 per_node={}"),
    ]
    result = benchmark.replay_metrics(events)[0]
    assert result["rows"] == 12
    assert result["row_count_source"] == "PUBLISH"


def test_alarm_transitions_are_per_node_and_only_on_rise():
    rows = [
        _row("node-a", 100, alarms="High Turbidity"),
        _row("node-a", 105, alarms="High Turbidity,Gross Contamination"),
        _row("node-a", 110, alarms="High Turbidity"),
        _row("node-b", 108, alarms="High Turbidity"),
    ]
    assert benchmark.alarm_transitions(rows) == [
        ("node-a", "High Turbidity", 100),
        ("node-a", "Gross Contamination", 105),
        ("node-b", "High Turbidity", 108),
    ]


def test_load_edge_rows_preserves_timestamps(tmp_path: Path):
    db = tmp_path / "edge.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE telemetry_buffer (id INTEGER PRIMARY KEY, timestamp INTEGER, "
            "status TEXT, payload TEXT)"
        )
        con.executemany(
            "INSERT INTO telemetry_buffer VALUES (?, ?, ?, ?)",
            [
                (1, 120, "synced", json.dumps({"ts": 120, "values": {"node_id": "node-a"}})),
                (2, 110, "pending", json.dumps({"ts": 110, "values": {"node_id": "node-b"}})),
            ],
        )
    rows = benchmark.load_edge_rows(db)
    assert [row["ts_ms"] for row in rows] == [110, 120]
    assert benchmark.load_edge_rows(db, start_ms=120)[0]["node_id"] == "node-a"


def test_build_report_marks_unavailable_metrics():
    rows = [_row("node-a", 100), _row("node-a", 105, status="pending")]
    report = benchmark.build_report(rows, [], None, None, "dry-run")
    assert report["mode"] == "dry-run"
    assert report["window"] == {"start_ms": 100, "end_ms": 105}
    assert report["metrics"]["buffer_flush_throughput_rps"]["value"] is None
    assert report["notes"]


class FakeClient:
    """Deterministic stand-in for ThingsBoardClient."""

    def __init__(self) -> None:
        self.series = {
            "uuid-a": {
                "server_received_ms": [
                    {"ts": 100, "value": 180},
                    {"ts": 105, "value": 155},
                ],
                "ph": [{"ts": 100}, {"ts": 105}],
            },
            "uuid-b": {"ph": [{"ts": 108}]},
        }

    def device_id(self, name: str) -> str | None:
        return {"node-a": "uuid-a", "node-b": "uuid-b"}.get(name)

    def timeseries(self, device_uuid: str, keys: str, start_ms: int, end_ms: int):
        return self.series.get(device_uuid, {})

    def alarms(self, start_ms: int, end_ms: int, device_ids=None):
        return [
            {
                "type": "High Turbidity",
                "originatorId": "uuid-a",
                "createdTime": 260,
            }
        ]


def test_cloud_metrics_compute_latency_delivery_and_alarm_delay():
    rows = [
        _row("node-a", 100, alarms="High Turbidity"),
        _row("node-a", 105, status="pending", alarms="High Turbidity"),
        _row("node-b", 108),
    ]
    metrics, points = benchmark.cloud_metrics(FakeClient(), rows, 0, 1000)
    assert metrics["end_to_end_latency_ms"]["value"]["median"] == pytest.approx(65.0)
    assert metrics["message_delivery_ratio_pct"]["value"] == 100.0
    assert metrics["locally_synced_pct"]["value"] == pytest.approx(100 * 2 / 3)
    assert metrics["data_loss"]["value"] == {"rows": 0, "pct": 0.0}
    assert metrics["alarm_activation_delay_ms"]["value"]["mean"] == pytest.approx(160.0)
    assert len(points) == 2


def test_write_outputs_creates_csv_json_and_svg(tmp_path: Path):
    report = benchmark.build_report([_row("node-a", 100)], [], None, None, "dry-run")
    benchmark.write_outputs(report, tmp_path, [(100, 12.5)])
    assert json.loads((tmp_path / "metrics.json").read_text())["mode"] == "dry-run"
    assert "metric,value,unit,samples,note" in (tmp_path / "metrics.csv").read_text()
    assert "<svg" in (tmp_path / "latency_scatter.svg").read_text()


def test_client_requires_credentials():
    with pytest.raises(ValueError):
        ThingsBoardClient("", "")


def test_alarms_uses_per_device_endpoint_on_current_tenants(monkeypatch):
    client = ThingsBoardClient("https://tb.example", "jwt")
    calls: list[str] = []

    def fake_request(method, path, payload=None):
        calls.append(path)
        if "/api/alarm/DEVICE/" in path:
            return {
                "data": [
                    {
                        "type": "High Turbidity",
                        "originator": {"entityType": "DEVICE", "id": "uuid-a"},
                        "createdTime": 260,
                    }
                ]
            }
        raise urllib.error.HTTPError(path, 405, "no", {}, None)

    monkeypatch.setattr(client, "request", fake_request)
    alarms = client.alarms(0, 1000, {"node-a": "uuid-a"})
    assert alarms is not None
    assert alarms[0]["type"] == "High Turbidity"
    assert calls == ["/api/alarm/DEVICE/uuid-a?pageSize=5000&page=0&startTime=0&endTs=1000".replace("endTs", "endTime")]
