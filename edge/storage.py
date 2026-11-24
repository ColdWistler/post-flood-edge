"""SQLite WAL store-and-forward buffer for telemetry readings."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from edge.models import Reading

logger = logging.getLogger(__name__)

_SCHEMA = (Path(__file__).resolve().parent / "sql" / "schema.sql").read_text(
    encoding="utf-8"
)


class BufferStore:
    """Bounded, durable queue shared by the sampling and publishing threads."""

    def __init__(self, db_path: Path, max_rows: int, batch_size: int) -> None:
        self._lock = threading.RLock()
        with self._lock:
            self.max_rows = max_rows
            self.batch_size = batch_size
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.executescript(_SCHEMA)

    def enqueue(self, reading: Reading | Mapping[str, Any]) -> None:
        """Persist a reading (or payload dict) before any publish attempt."""
        with self._lock:
            if isinstance(reading, Mapping):
                ts_ms = reading["ts"]
                values = reading["values"]
            else:
                ts_ms = reading.ts_ms
                values = reading.values
            payload = json.dumps({"ts": ts_ms, "values": values})

            with self._conn:
                total = self._conn.execute(
                    "SELECT COUNT(*) FROM telemetry_buffer"
                ).fetchone()[0]
                while total >= self.max_rows:
                    victim = self._conn.execute(
                        "SELECT id, timestamp FROM telemetry_buffer "
                        "WHERE status IN ('pending','synced') "
                        "ORDER BY CASE WHEN status='pending' THEN 0 ELSE 1 END, "
                        "timestamp, id LIMIT 1"
                    ).fetchone()
                    if victim is None:
                        logger.warning(
                            "DROP NEW: ts=%s max_rows=%d; no pending row to evict",
                            ts_ms,
                            self.max_rows,
                        )
                        return
                    self._conn.execute(
                        "DELETE FROM telemetry_buffer WHERE id=?", (victim[0],)
                    )
                    logger.warning(
                        "EVICT: id=%s ts=%s max_rows=%d",
                        victim[0],
                        victim[1],
                        self.max_rows,
                    )
                    total -= 1

                self._conn.execute(
                    "INSERT INTO telemetry_buffer "
                    "(timestamp, payload, status, retry_count, created_at) "
                    "VALUES (?, ?, 'pending', 0, ?)",
                    (ts_ms, payload, time.time_ns() // 1_000_000),
                )

    def depth(self, node_id: str | None = None) -> int:
        """Count undelivered rows (pending plus inflight), optionally per node."""
        with self._lock:
            if node_id is None:
                return self._conn.execute(
                    "SELECT COUNT(*) FROM telemetry_buffer "
                    "WHERE status IN ('pending', 'inflight')"
                ).fetchone()[0]
            return self._conn.execute(
                "SELECT COUNT(*) FROM telemetry_buffer "
                "WHERE status IN ('pending', 'inflight') "
                "AND json_extract(payload, '$.values.node_id')=?",
                (node_id,),
            ).fetchone()[0]

    def claim(
        self,
        exclude_nodes: set[str] | None = None,
        per_node_limit: int | None = None,
    ) -> list[tuple[str, list[dict]]]:
        """Mark pending rows inflight and group them by node id.

        ``exclude_nodes`` leaves a node's rows pending (e.g. while its
        publisher is suspended); ``per_node_limit`` stops one backlogged node
        from consuming the whole global batch. Both default to ``None`` for
        the original oldest-first claim behavior.
        """
        params: list[Any] = []
        conditions = ["status='pending'"]
        if exclude_nodes:
            placeholders = ",".join("?" for _ in exclude_nodes)
            conditions.append(
                f"json_extract(payload, '$.values.node_id') NOT IN ({placeholders})"
            )
            params.extend(sorted(exclude_nodes))
        where = " AND ".join(conditions)
        if per_node_limit is not None:
            sql = (
                "SELECT id, timestamp, payload FROM ("
                "SELECT id, timestamp, payload, "
                "ROW_NUMBER() OVER ("
                "PARTITION BY json_extract(payload,'$.values.node_id') "
                "ORDER BY timestamp, id) AS rn "
                f"FROM telemetry_buffer WHERE {where}"
                ") WHERE rn <= ? ORDER BY timestamp, id LIMIT ?"
            )
            params.extend([per_node_limit, self.batch_size])
        else:
            sql = (
                "SELECT id, timestamp, payload FROM telemetry_buffer "
                f"WHERE {where} ORDER BY timestamp, id LIMIT ?"
            )
            params.append(self.batch_size)
        with self._lock:
            with self._conn:
                claimed = self._conn.execute(sql, params).fetchall()
                self._conn.executemany(
                    "UPDATE telemetry_buffer SET status='inflight' WHERE id=?",
                    [(row_id,) for row_id, _, _ in claimed],
                )

            batches: dict[str, list[dict]] = {}
            for row_id, ts, payload in claimed:
                values = json.loads(payload)["values"]
                batches.setdefault(values["node_id"], []).append(
                    {"id": row_id, "ts": ts, "values": values}
                )
            return list(batches.items())

    def ack(self, rows: list[dict]) -> None:
        """Mark delivered rows synced."""
        with self._lock:
            with self._conn:
                self._conn.executemany(
                    "UPDATE telemetry_buffer SET status='synced' "
                    "WHERE id=? AND status='inflight'",
                    [(row["id"],) for row in rows],
                )

    def fail(self, rows: list[dict]) -> None:
        """Return failed rows to pending and record another retry."""
        with self._lock:
            with self._conn:
                self._conn.executemany(
                    "UPDATE telemetry_buffer "
                    "SET status='pending', retry_count=retry_count+1 "
                    "WHERE id=? AND status='inflight'",
                    [(row["id"],) for row in rows],
                )

    def close(self) -> None:
        """Close the connection; committed rows remain for the next process."""
        with self._lock:
            self._conn.close()
