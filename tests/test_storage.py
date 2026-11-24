"""Tests for the SQLite store-and-forward buffer."""

from __future__ import annotations

from dataclasses import replace

from edge.models import Reading
from edge.storage import BufferStore

READING = Reading("node-a", 1000, {"node_id": "node-a", "ph": 7.2})


def _ack_new(store: BufferStore, ts_ms: int) -> None:
    """Enqueue one reading and immediately acknowledge it as synced."""
    store.enqueue(replace(READING, ts_ms=ts_ms))
    store.ack(store.claim()[0][1])


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
    assert "EVICT" not in caplog.text
    assert s.depth() == 2
    assert [r["ts"] for _, group in s.claim() for r in group] == []


def test_synced_history_is_evicted_when_all_rows_are_acked(tmp_path, caplog):
    s = BufferStore(tmp_path / "j.db", max_rows=2, batch_size=10)
    _ack_new(s, 1)
    oldest_id = s._conn.execute("SELECT id FROM telemetry_buffer").fetchone()[0]
    _ack_new(s, 2)
    caplog.clear()
    s.enqueue(replace(READING, ts_ms=3))
    rows = s._conn.execute(
        "SELECT id, timestamp, status FROM telemetry_buffer ORDER BY timestamp"
    ).fetchall()
    assert [(ts, status) for _, ts, status in rows] == [(2, "synced"), (3, "pending")]
    assert all(row_id != oldest_id for row_id, _, _ in rows)
    assert "EVICT" in caplog.text
    assert "DROP NEW" not in caplog.text
    assert s.depth() == 1
    assert [r["ts"] for _, group in s.claim() for r in group] == [3]


def test_pending_victim_precedes_synced_and_synced_is_next(tmp_path, caplog):
    s = BufferStore(tmp_path / "k.db", max_rows=3, batch_size=10)
    _ack_new(s, 1)
    _ack_new(s, 2)
    s.enqueue(replace(READING, ts_ms=3))
    pending_id = s._conn.execute(
        "SELECT id FROM telemetry_buffer WHERE status='pending'"
    ).fetchone()[0]

    s.enqueue(replace(READING, ts_ms=4))
    # The pending row is evicted even though the synced ts=1 row is older.
    assert s._conn.execute(
        "SELECT id FROM telemetry_buffer WHERE id=?", (pending_id,)
    ).fetchone() is None
    assert s._conn.execute(
        "SELECT timestamp FROM telemetry_buffer WHERE status='synced' ORDER BY timestamp"
    ).fetchall() == [(1,), (2,)]
    assert s._conn.execute(
        "SELECT timestamp FROM telemetry_buffer WHERE status='pending'"
    ).fetchall() == [(4,)]

    # With no pending rows left, the next overflow must evict oldest synced history.
    s.ack(s.claim()[0][1])
    caplog.clear()
    s.enqueue(replace(READING, ts_ms=5))
    assert s._conn.execute(
        "SELECT timestamp, status FROM telemetry_buffer ORDER BY timestamp"
    ).fetchall() == [(2, "synced"), (4, "synced"), (5, "pending")]
    assert "EVICT" in caplog.text
    assert "DROP NEW" not in caplog.text
    assert s.depth() == 1


def test_inflight_row_is_never_evicted_when_synced_history_exists(tmp_path, caplog):
    s = BufferStore(tmp_path / "l.db", max_rows=2, batch_size=10)
    s.enqueue(replace(READING, ts_ms=1))
    inflight_id = s.claim()[0][1][0]["id"]
    _ack_new(s, 2)
    caplog.clear()
    s.enqueue(replace(READING, ts_ms=3))
    assert s._conn.execute(
        "SELECT status FROM telemetry_buffer WHERE id=?", (inflight_id,)
    ).fetchone() == ("inflight",)
    assert s._conn.execute(
        "SELECT timestamp, status FROM telemetry_buffer ORDER BY timestamp"
    ).fetchall() == [(1, "inflight"), (3, "pending")]
    assert "EVICT" in caplog.text
    assert "DROP NEW" not in caplog.text
    assert s.depth() == 2


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


def test_claim_excludes_named_nodes_without_losing_their_rows(tmp_path):
    s = BufferStore(tmp_path / "exclude.db", max_rows=100, batch_size=5)
    for ts in range(1, 13):
        node_id = "node-a" if ts <= 6 else "node-b"
        s.enqueue(Reading(node_id, ts, {"node_id": node_id}))

    claimed = s.claim(exclude_nodes={"node-a"})
    assert [(node, [r["ts"] for r in group]) for node, group in claimed] == [
        ("node-b", [7, 8, 9, 10, 11])
    ]
    s.ack(claimed[0][1])
    assert s.depth("node-a") == 6
    assert s._conn.execute(
        "SELECT status FROM telemetry_buffer "
        "WHERE json_extract(payload, '$.values.node_id')='node-a' "
        "GROUP BY status"
    ).fetchall() == [("pending",)]

    # Excluded rows remain pending, keeping their original order for later claims.
    resumed = s.claim()
    assert [(node, [r["ts"] for r in group]) for node, group in resumed] == [
        ("node-a", [1, 2, 3, 4, 5])
    ]
    s.ack(resumed[0][1])
    assert [r["ts"] for _, group in s.claim() for r in group] == [6, 12]


def test_claim_per_node_cap_keeps_oldest_rows_and_global_limit(tmp_path):
    s = BufferStore(tmp_path / "cap.db", max_rows=100, batch_size=4)
    for ts in range(1, 16):
        node_id = "node-a" if ts <= 10 else "node-b"
        s.enqueue(Reading(node_id, ts, {"node_id": node_id}))

    first = s.claim(per_node_limit=2)
    assert [(node, [r["ts"] for r in group]) for node, group in first] == [
        ("node-a", [1, 2]),
        ("node-b", [11, 12]),
    ]
    s.ack([r for _, group in first for r in group])

    second = s.claim(per_node_limit=2)
    assert [(node, [r["ts"] for r in group]) for node, group in second] == [
        ("node-a", [3, 4]),
        ("node-b", [13, 14]),
    ]
    s.ack([r for _, group in second for r in group])

    assert [r["ts"] for _, group in s.claim(per_node_limit=2) for r in group] == [5, 6, 15]
