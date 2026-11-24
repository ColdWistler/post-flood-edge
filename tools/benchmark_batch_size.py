"""Measure store-and-forward throughput for several SQLite claim batch sizes.

Run `python tools/benchmark_batch_size.py` to write `tools/batch_results.csv`.
The benchmark uses the real BufferStore plus NullPublisher, so it needs no
broker and no third-party packages.
"""

from __future__ import annotations

import csv
import sys
import tempfile
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from edge.publisher import NullPublisher  # noqa: E402
from edge.storage import BufferStore  # noqa: E402

_FIELDS = ("batch_size", "rows_per_second", "elapsed_s")
_BASE_TS_MS = 1_700_000_000_000


def benchmark_batch_size(
    db_path,
    sizes=(1, 10, 25, 50, 100, 200),
    rows_per_size=500,
) -> list[dict]:
    """Time the drain of ``rows_per_size`` buffered rows at each batch size.

    Each size gets its own database file derived from ``db_path``; only the
    claim/publish/ack path is timed, not the setup enqueues.
    """
    if rows_per_size <= 0:
        raise ValueError("rows_per_size must be positive")
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    for size in sizes:
        size = int(size)
        if size < 1:
            raise ValueError("batch sizes must be positive")
        run_db = db_path.with_name(f"{db_path.stem}_{size}{db_path.suffix}")
        for suffix in ("", "-wal", "-shm"):
            Path(f"{run_db}{suffix}").unlink(missing_ok=True)

        store = BufferStore(run_db, max_rows=rows_per_size, batch_size=size)
        publisher = NullPublisher("node-a")
        try:
            for index in range(rows_per_size):
                store.enqueue(
                    {
                        "ts": _BASE_TS_MS + index,
                        "values": {
                            "node_id": "node-a",
                            "turbidity_ntu": float(index % 100),
                        },
                    }
                )

            started = time.perf_counter()
            while True:
                batches = store.claim()
                if not batches:
                    break
                for _node_id, rows in batches:
                    if not publisher.publish_batch(rows):
                        store.fail(rows)
                        raise RuntimeError("NullPublisher rejected a benchmark batch")
                    store.ack(rows)
            elapsed = time.perf_counter() - started
        finally:
            store.close()

        results.append(
            {
                "batch_size": size,
                "rows_per_second": round(rows_per_size / elapsed, 2) if elapsed else 0.0,
                "elapsed_s": round(elapsed, 6),
            }
        )
    return results


def main() -> None:
    """Write the benchmark results next to this script and print them."""
    with tempfile.TemporaryDirectory(prefix="post-flood-bench-") as tmp:
        results = benchmark_batch_size(Path(tmp) / "benchmark.db")

    output = Path(__file__).resolve().parent / "batch_results.csv"
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(results)

    for row in results:
        print(
            f"batch_size={row['batch_size']} "
            f"rows_per_second={row['rows_per_second']:.2f} "
            f"elapsed_s={row['elapsed_s']:.6f}"
        )
    print(f"wrote {output}")


if __name__ == "__main__":
    main()
