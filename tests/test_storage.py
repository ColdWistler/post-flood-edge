"""Tests for the SQLite store-and-forward buffer."""

from __future__ import annotations

from dataclasses import replace

from edge.models import Reading
from edge.storage import BufferStore

READING = Reading("node-a", 1000, {"node_id": "node-a", "ph": 7.2})


def test_wal_enabled_and_write_before_publish(tmp_path):
    s = BufferStore(tmp_path / "a.db", max_rows=100, batch_size=10)
    assert s._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    s.enqueue(READING)
    assert s.depth() == 1
    assert s._conn.execute("SELECT status FROM telemetry_buffer").fetchone()[0] == "pending"


def test_replay_preserves_epoch_ms_order(tmp_path):
    s = BufferStore(tmp_path / "b.db", max_rows=100, batch_size=10)
    for ts in [3000, 1000, 2000]:
        s.enqueue(replace(READING, ts_ms=ts))
    batches = s.claim()
    rows = [r for _, group in batches for r in group]
    assert [r["ts"] for r in rows] == [1000, 2000, 3000]
    assert all(set(r) == {"id", "ts", "values"} for r in rows)


def test_ack_and_fail(tmp_path):
    s = BufferStore(tmp_path / "c.db", max_rows=100, batch_size=2)
    s.enqueue(READING)
    row = s.claim()[0][1][0]
    s.ack([row])
    assert s.depth() == 0
    assert s._conn.execute("SELECT status FROM telemetry_buffer").fetchone()[0] == "synced"
    s.enqueue(READING)
    row = s.claim()[0][1][0]
    s.fail([row])
    # The synced row remains as history; order explicitly so this checks the
    # newly enqueued and failed row rather than relying on unspecified row order.
    assert s._conn.execute(
        "SELECT status, retry_count FROM telemetry_buffer ORDER BY id DESC"
    ).fetchone() == ("pending", 1)


def test_eviction_skips_inflight_rows(tmp_path, caplog):
    s = BufferStore(tmp_path / "d.db", max_rows=2, batch_size=10)
    s.enqueue(replace(READING, ts_ms=1))
    inflight = s.claim()[0][1][0]
    s.enqueue(replace(READING, ts_ms=2))
    s.enqueue(replace(READING, ts_ms=3))
    assert s.depth() == 2
    assert s._conn.execute("SELECT status FROM telemetry_buffer WHERE id=?", (inflight["id"],)).fetchone()[0] == "inflight"
    assert "EVICT" in caplog.text


def test_claim_batches_by_size_and_groups_by_node(tmp_path):
    s = BufferStore(tmp_path / "e.db", max_rows=100, batch_size=2)
    for node_id, ts in [("node-b", 2), ("node-a", 3), ("node-a", 4), ("node-b", 5)]:
        s.enqueue(Reading(node_id, ts, {"node_id": node_id, "ts_seq": ts}))
    batches = s.claim()
    assert [node for node, _ in batches] == ["node-b", "node-a"]
    assert [[r["ts"] for r in group] for _, group in batches] == [[2], [3]]
    # depth includes inflight rows, so claiming does not reduce it.
    assert s.depth() == 4
    # batch_size is a global limit, so the second-oldest pair is claimed next.
    second = s.claim()
    assert [
        (node, [r["ts"] for r in group]) for node, group in second
    ] == [("node-a", [4]), ("node-b", [5])]
    assert s.claim() == []
    s.ack([r for _, group in second for r in group])
    assert s.depth() == 2


def test_depth_can_filter_by_node(tmp_path):
    s = BufferStore(tmp_path / "f.db", max_rows=100, batch_size=10)
    s.enqueue(READING)
    s.enqueue(Reading("node-b", 2000, {"node_id": "node-b"}))
    assert s.depth() == 2
    assert s.depth("node-a") == 1
    assert s.depth("node-b") == 1
    assert s.depth("node-c") == 0


def test_enqueue_accepts_payload_dict(tmp_path):
    s = BufferStore(tmp_path / "g.db", max_rows=100, batch_size=10)
    s.enqueue({"ts": 1234, "values": {"node_id": "node-a", "ph": 7.1}})
    assert s.depth() == 1
    assert s.claim()[0][1][0]["ts"] == 1234


def test_drop_new_when_no_pending_rows_can_be_evicted(tmp_path, caplog):
    s = BufferStore(tmp_path / "h.db", max_rows=2, batch_size=10)
    for ts in [1, 2]:
        s.enqueue(replace(READING, ts_ms=ts))
    assert len(s.claim()[0][1]) == 2
    s.enqueue(replace(READING, ts_ms=3))
    assert "DROP NEW" in caplog.text
    assert s.depth() == 2
    assert [r["ts"] for _, group in s.claim() for r in group] == []


def test_buffered_rows_survive_close_and_reopen(tmp_path):
    path = tmp_path / "i.db"
    s = BufferStore(path, max_rows=100, batch_size=10)
    s.enqueue(READING)
    s.close()
    reopened = BufferStore(path, max_rows=100, batch_size=10)
    assert [(r["ts"], r["values"]["node_id"]) for _, group in reopened.claim() for r in group] == [
        (1000, "node-a")
    ]
    reopened.close()
