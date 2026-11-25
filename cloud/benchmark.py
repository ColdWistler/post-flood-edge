"""Cloud-side Task 3 benchmarks.

Offline by default: local SQLite rows and structured edge logs provide the
buffer-replay metrics, while metrics that need a broker are reported as
unavailable. With ``--apply`` the same report is enriched from a ThingsBoard
REST API using a JWT supplied via ``TB_JWT`` (never written to disk).

Run from the repository root::

    python -m cloud.benchmark
    python -m cloud.benchmark --apply --jwt "$TB_JWT" --plot
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sqlite3
import ssl
import statistics
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

DEFAULT_DB = Path("edge.db")
DEFAULT_LOG = Path("logs/edge.log")
DEFAULT_OUT = Path("cloud/benchmark")
SERVER_RECEIVED_KEY = "server_received_ms"  # written by cloud/artifacts/wqi_rule_chain.json

_LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) "
    r"(?P<level>[A-Z]+) (?P<name>\S+) (?P<msg>.*)$"
)
_PUBLISH_RE = re.compile(r"PUBLISH node=(\S+) rows=(\d+)")
_REPLAY_RE = re.compile(r"REPLAY: node=(\S+) rows=(\d+)")
_BUFFER_TOTAL_RE = re.compile(r"BUFFER: total=(\d+)")


@dataclass(frozen=True)
class LogEvent:
    """One parsed line from the edge's structured log."""

    at: float
    level: str
    name: str
    message: str


def percentile(values: list[float], fraction: float) -> float | None:
    """Linearly interpolated percentile; ``fraction`` is in ``[0, 1]``."""
    if not values:
        return None
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction must be between 0 and 1")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def summarise(values: Iterable[float]) -> dict[str, float | int | None]:
    """Mean/median/P95/max summary used by the manuscript tables."""
    samples = [float(value) for value in values]
    if not samples:
        return {"count": 0, "mean": None, "median": None, "p95": None, "max": None}
    return {
        "count": len(samples),
        "mean": statistics.fmean(samples),
        "median": statistics.median(samples),
        "p95": percentile(samples, 0.95),
        "max": max(samples),
    }


def metric(
    value: Any, unit: str, samples: int, note: str
) -> dict[str, Any]:
    """Build one report entry, including an explicit unavailable marker."""
    return {"value": value, "unit": unit, "samples": samples, "note": note}


def load_edge_rows(
    db_path: Path, start_ms: int | None = None, end_ms: int | None = None
) -> list[dict[str, Any]]:
    """Read buffered rows, preserving their original acquisition timestamps."""
    if not db_path.exists():
        raise FileNotFoundError(f"edge database not found: {db_path}")
    query = "SELECT id, timestamp, status, payload FROM telemetry_buffer"
    conditions: list[str] = []
    params: list[int] = []
    if start_ms is not None:
        conditions.append("timestamp >= ?")
        params.append(int(start_ms))
    if end_ms is not None:
        conditions.append("timestamp <= ?")
        params.append(int(end_ms))
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY timestamp, id"

    rows: list[dict[str, Any]] = []
    with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as con:
        for row_id, ts_ms, status, payload in con.execute(query, params):
            body = json.loads(payload)
            values = body.get("values", {}) if isinstance(body, dict) else {}
            rows.append(
                {
                    "id": row_id,
                    "ts_ms": int(ts_ms),
                    "status": status,
                    "node_id": values.get("node_id", ""),
                    "values": values,
                }
            )
    return rows


def parse_log(path: Path) -> list[LogEvent]:
    """Parse ``%(asctime)s %(levelname)s %(name)s %(message)s`` log lines."""
    if not path.exists():
        return []
    events: list[LogEvent] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _LOG_RE.match(line)
        if match is None:
            continue
        try:
            at = datetime.strptime(match["ts"], "%Y-%m-%d %H:%M:%S,%f").timestamp()
        except ValueError:
            continue
        events.append(
            LogEvent(at, match["level"], match["name"], match["msg"])
        )
    return events


def replay_metrics(events: list[LogEvent]) -> list[dict[str, Any]]:
    """Compute one throughput/recovery measurement per completed outage."""
    outages: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for event in events:
        if "OUTAGE: START" in event.message:
            current = {"start": event.at, "end": None, "tail": []}
            outages.append(current)
            continue
        if current is not None:
            current["tail"].append(event)
            if "OUTAGE: END" in event.message:
                current["end"] = event.at

    measurements: list[dict[str, Any]] = []
    for outage in outages:
        if outage["end"] is None:
            continue
        replay_rows = 0
        publish_rows = 0
        last_publish: float | None = None
        drained_at: float | None = None
        for event in outage["tail"]:
            if event.at < outage["end"]:
                continue
            replayed = _REPLAY_RE.search(event.message)
            if replayed:
                replay_rows += int(replayed.group(2))
            published = _PUBLISH_RE.search(event.message)
            if published:
                publish_rows += int(published.group(2))
                last_publish = event.at
            buffered = _BUFFER_TOTAL_RE.search(event.message)
            if buffered and int(buffered.group(1)) == 0:
                drained_at = event.at
                break
        # REPLAY rows are the exact buffered count logged at claim time. PUBLISH
        # rows also include live samples taken during the drain, so they are only
        # a fallback for older logs that predate the REPLAY marker.
        rows = replay_rows if replay_rows else publish_rows
        count_source = "REPLAY" if replay_rows else "PUBLISH"
        finished = drained_at if drained_at is not None else last_publish
        if finished is None:
            continue
        seconds = max(0.0, finished - outage["end"])
        measurements.append(
            {
                "recovery_s": seconds,
                "rows": rows,
                "row_count_source": count_source,
                "throughput_rps": rows / seconds if seconds > 0 else None,
                "fully_drained": drained_at is not None,
                "started_at": outage["start"],
            }
        )
    return measurements


def alarm_transitions(rows: list[dict[str, Any]]) -> list[tuple[str, str, int]]:
    """Find ``(node, alarm, ts_ms)`` where an alarm name first appears."""
    previous: dict[str, set[str]] = {}
    transitions: list[tuple[str, str, int]] = []
    for row in sorted(rows, key=lambda item: (item["node_id"], item["ts_ms"])):
        active = {
            name.strip()
            for name in str(row["values"].get("active_alarms", "")).split(",")
            if name.strip()
        }
        before = previous.get(row["node_id"], set())
        for name in sorted(active - before):
            transitions.append((row["node_id"], name, row["ts_ms"]))
        previous[row["node_id"]] = active
    return transitions


class ThingsBoardClient:
    """Minimal authenticated REST client using only the standard library."""

    def __init__(
        self,
        host: str,
        jwt: str | None = None,
        ca_cert: str | None = None,
        timeout: float = 20.0,
        api_key: str | None = None,
    ) -> None:
        secret = (api_key or jwt or "").strip()
        if not host or not secret:
            raise ValueError("ThingsBoard host and JWT/API key are required")
        base = host if "://" in host else "https://" + host
        self.base = base.rstrip("/")
        self.jwt = jwt.strip() if jwt else None
        self.api_key = api_key.strip() if api_key else None
        self.ca_cert = ca_cert or None
        self.timeout = timeout

    def request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> Any:
        """Issue one authenticated request and decode its JSON response."""
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        if self.api_key:
            auth = "ApiKey " + self.api_key
        else:
            auth = "Bearer " + str(self.jwt)
        headers = {
            "Accept": "application/json",
            "Authorization": auth,
            "X-Authorization": auth,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base + path, data=data, headers=headers, method=method
        )
        context = ssl.create_default_context(cafile=self.ca_cert)
        with urllib.request.urlopen(request, timeout=self.timeout, context=context) as response:
            body = response.read().decode("utf-8")
        return json.loads(body) if body else {}

    def device_id(self, name: str) -> str | None:
        """Return the UUID of a device by name, or ``None`` when absent."""
        path = "/api/tenant/devices?" + urllib.parse.urlencode({"deviceName": name})
        found = self.request("GET", path)
        if not isinstance(found, dict):
            return None
        device = found
        if "id" not in found and isinstance(found.get("data"), list) and found["data"]:
            device = found["data"][0]
        identifier = device.get("id", {})
        return identifier.get("id") if isinstance(identifier, dict) else None

    def timeseries(
        self, device_uuid: str, keys: str, start_ms: int, end_ms: int, limit: int = 50000
    ) -> dict[str, list[dict[str, Any]]]:
        """Fetch raw (non-aggregated) telemetry for one device."""
        query = urllib.parse.urlencode(
            {
                "keys": keys,
                "startTs": start_ms,
                "endTs": end_ms,
                "limit": limit,
                # ThingsBoard's enum is case-sensitive; lowercase "none" is
                # rejected with HTTP 400 on current Cloud tenants.
                "agg": "NONE",
            }
        )
        data = self.request(
            "GET",
            f"/api/plugins/telemetry/DEVICE/{device_uuid}/values/timeseries?{query}",
        )
        if not isinstance(data, dict):
            return {}
        return {key: value for key, value in data.items() if isinstance(value, list)}

    def alarms(
        self,
        start_ms: int,
        end_ms: int,
        device_ids: dict[str, str] | None = None,
    ) -> list[dict[str, Any]] | None:
        """Query alarm history; return ``None`` if every endpoint is unavailable.

        Current Cloud tenants expose per-device alarms as
        ``GET /api/alarm/DEVICE/{uuid}``; older releases accept the tenant-wide
        ``/api/alarm/search`` POST. Try both so the benchmark degrades cleanly.
        """
        results: list[dict[str, Any]] = []
        if device_ids:
            for identifier in device_ids.values():
                query = urllib.parse.urlencode(
                    {
                        "pageSize": 5000,
                        "page": 0,
                        "startTime": start_ms,
                        "endTime": end_ms,
                    }
                )
                try:
                    result = self.request(
                        "GET", f"/api/alarm/DEVICE/{identifier}?{query}"
                    )
                except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
                    continue
                if isinstance(result, dict):
                    results.extend(
                        item for item in result.get("data", []) if isinstance(item, dict)
                    )
                elif isinstance(result, list):
                    results.extend(item for item in result if isinstance(item, dict))
            if results:
                return results

        body = {
            "startTime": start_ms,
            "endTime": end_ms,
            "status": ["ACTIVE", "CLEARED_ACK", "CLEARED_UNACK"],
            "pageSize": 5000,
            "page": 0,
        }
        try:
            result = self.request("POST", "/api/alarm/search", body)
        except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
            query = urllib.parse.urlencode(
                {"startTime": start_ms, "endTime": end_ms, "pageSize": 5000, "page": 0}
            )
            try:
                result = self.request("GET", "/api/alarm?" + query)
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, ValueError):
                return None
        if isinstance(result, dict):
            return [item for item in result.get("data", []) if isinstance(item, dict)]
        return [item for item in result if isinstance(item, dict)] if isinstance(result, list) else None


def _originator_id(alarm: dict[str, Any]) -> str | None:
    """Extract a device UUID from the two alarm shapes returned by ThingsBoard."""
    direct = alarm.get("originatorId")
    if isinstance(direct, str):
        return direct
    originator = alarm.get("originator")
    if isinstance(originator, dict):
        identifier = originator.get("id")
        return identifier if isinstance(identifier, str) else None
    return None


def cloud_metrics(
    client: ThingsBoardClient,
    rows: list[dict[str, Any]],
    start_ms: int,
    end_ms: int,
) -> tuple[dict[str, Any], list[tuple[int, float]]]:
    """Compute broker-dependent metrics and return latency points for plotting."""
    notes: list[str] = []
    device_ids: dict[str, str] = {}
    names = sorted({row["node_id"] for row in rows if row["node_id"]})
    for name in names:
        try:
            identifier = client.device_id(name)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            notes.append(f"device lookup failed for {name}: {exc.__class__.__name__}")
            continue
        if identifier:
            device_ids[name] = identifier
        else:
            notes.append(f"device {name} is not provisioned in ThingsBoard")

    latencies: list[float] = []
    latency_points: list[tuple[int, float]] = []
    for name, identifier in device_ids.items():
        try:
            series = client.timeseries(identifier, SERVER_RECEIVED_KEY, start_ms, end_ms)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            notes.append(f"latency telemetry unavailable for {name}: {exc.__class__.__name__}")
            continue
        for point in series.get(SERVER_RECEIVED_KEY, []):
            try:
                received = float(point["value"])
                sent = int(point["ts"])
            except (KeyError, TypeError, ValueError):
                continue
            latency = received - sent
            latencies.append(latency)
            latency_points.append((sent, latency))
    if latencies:
        latency_metric = metric(summarise(latencies), "ms", len(latencies),
                                "server_received_ms minus original telemetry ts (NTP-synced clocks)")
    else:
        latency_metric = metric(
            None,
            "ms",
            0,
            "deploy wqi_rule_chain.json (it writes server_received_ms) and re-run with --apply",
        )

    present: set[tuple[str, int]] = set()
    for name, identifier in device_ids.items():
        try:
            series = client.timeseries(identifier, "ph", start_ms, end_ms)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            notes.append(f"delivery telemetry unavailable for {name}: {exc.__class__.__name__}")
            continue
        for point in series.get("ph", []):
            try:
                present.add((name, int(point["ts"])))
            except (KeyError, TypeError, ValueError):
                continue

    generated = {(row["node_id"], row["ts_ms"]) for row in rows}
    confirmed = generated & present
    synced = {
        (row["node_id"], row["ts_ms"]) for row in rows if row["status"] == "synced"
    }
    ratio = 100.0 * len(confirmed) / len(generated) if generated else None
    loss = len(generated) - len(confirmed)

    alarms = client.alarms(start_ms, end_ms, device_ids) if device_ids else None
    delays: list[float] = []
    if alarms is not None:
        by_key: dict[tuple[str, str], list[int]] = {}
        for alarm in alarms:
            origin = _originator_id(alarm)
            kind = alarm.get("type")
            created = alarm.get("createdTime")
            if origin and kind and created is not None:
                try:
                    by_key.setdefault((origin, str(kind)), []).append(int(created))
                except (TypeError, ValueError):
                    continue
        for node, name, ts_ms in alarm_transitions(rows):
            key = (device_ids.get(node, ""), name)
            candidates = [created for created in by_key.get(key, []) if created >= ts_ms - 1000]
            if candidates:
                closest = min(candidates, key=lambda created: abs(created - ts_ms))
                by_key[key].remove(closest)
                delays.append(float(closest - ts_ms))
    else:
        notes.append("alarm history unavailable; alarm activation delay left blank")

    return {
        "end_to_end_latency_ms": latency_metric,
        "message_delivery_ratio_pct": metric(
            ratio,
            "%",
            len(confirmed),
            "local generated rows whose original ts appears in ThingsBoard ph telemetry",
        ),
        "alarm_activation_delay_ms": (
            metric(summarise(delays), "ms", len(delays),
                   "edge alarm transition to matching ThingsBoard alarm creation")
            if delays
            else metric(None, "ms", 0, "no paired edge/server alarm transitions found")
        ),
        "locally_synced_pct": metric(
            100.0 * len(synced) / len(generated) if generated else None,
            "%",
            len(synced),
            "edge PUBACK-confirmed rows / generated rows (no broker required)",
        ),
        "data_loss": metric(
            {"rows": loss, "pct": 100.0 * loss / len(generated) if generated else None},
            "rows",
            len(generated),
            "generated rows absent from ThingsBoard after full sync",
        ),
    }, latency_points


def build_report(
    rows: list[dict[str, Any]],
    events: list[LogEvent],
    start_ms: int | None,
    end_ms: int | None,
    mode: str,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the report from local rows, logs and (optionally) the cloud."""
    reports = list(notes or [])
    measurements = replay_metrics(events)
    recoveries = [
        item["recovery_s"] for item in measurements if item["recovery_s"] is not None
    ]
    throughputs = [
        item["throughput_rps"]
        for item in measurements
        if item["throughput_rps"] is not None
    ]
    if not measurements:
        reports.append("no completed OUTAGE START/END pair found in the edge log")
    if measurements and not any(item["fully_drained"] for item in measurements):
        reports.append("no BUFFER: total=0 line after replay; recovery time uses last PUBLISH")
    if any(item["row_count_source"] == "PUBLISH" for item in measurements):
        reports.append(
            "REPLAY row counts missing; throughput fell back to PUBLISH rows, "
            "which may include live samples taken during the drain"
        )

    metrics: dict[str, Any] = {
        "buffer_flush_throughput_rps": (
            metric(summarise(throughputs), "rows/s", len(throughputs),
                   "PUBLISH rows per second during replay after OUTAGE: END")
            if throughputs
            else metric(None, "rows/s", 0, "no replay batches found in the edge log")
        ),
        "recovery_time_s": (
            metric(summarise(recoveries), "s", len(recoveries),
                   "OUTAGE: END to BUFFER: total=0 (fallback: last replay PUBLISH)")
            if recoveries
            else metric(None, "s", 0, "no replay window found in the edge log")
        ),
    }
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": mode,
        "window": {
            "start_ms": start_ms if start_ms is not None else min(
                (row["ts_ms"] for row in rows), default=None
            ),
            "end_ms": end_ms if end_ms is not None else max(
                (row["ts_ms"] for row in rows), default=None
            ),
        },
        "rows_read": len(rows),
        "outages_observed": len(measurements),
        "metrics": metrics,
        "notes": reports,
    }


def write_outputs(
    report: dict[str, Any], out_dir: Path, latency_points: list[tuple[int, float]]
) -> None:
    """Write ``metrics.json``, ``metrics.csv`` and an optional latency plot."""
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "metrics.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    with (out_dir / "metrics.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["metric", "value", "unit", "samples", "note"])
        for name, entry in report["metrics"].items():
            value = entry["value"]
            writer.writerow(
                [
                    name,
                    json.dumps(value, sort_keys=True) if isinstance(value, dict) else (
                        "" if value is None else value
                    ),
                    entry["unit"],
                    entry["samples"],
                    entry["note"],
                ]
            )
    if latency_points:
        write_latency_scatter(latency_points, out_dir / "latency_scatter.svg")


def write_latency_scatter(points: list[tuple[int, float]], path: Path) -> None:
    """Render a dependency-free SVG scatter plot of latency over time."""
    width, height, pad = 640, 360, 56
    xs = [float(item[0]) for item in points]
    ys = [float(item[1]) for item in points]
    xmin, xmax = min(xs), max(xs)
    ymin, ymax = min(ys), max(ys)
    if xmax == xmin:
        xmax = xmin + 1.0
    if ymax == ymin:
        ymax = ymin + 1.0

    def px(x: float) -> float:
        return pad + (x - xmin) / (xmax - xmin) * (width - 2 * pad)

    def py(y: float) -> float:
        return height - pad - (y - ymin) / (ymax - ymin) * (height - 2 * pad)

    dots = "".join(
        f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="3" fill="#2563eb" opacity="0.75"/>'
        for x, y in zip(xs, ys)
    )
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">
  <rect width="{width}" height="{height}" fill="white"/>
  <line x1="{pad}" y1="{height - pad}" x2="{width - pad}" y2="{height - pad}" stroke="#334155"/>
  <line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" stroke="#334155"/>
  <text x="{width / 2:.0f}" y="{height - 12}" text-anchor="middle" font-size="13">acquisition ts (epoch ms)</text>
  <text x="14" y="{height / 2:.0f}" text-anchor="middle" font-size="13" transform="rotate(-90 14 {height / 2:.0f})">latency (ms)</text>
  <text x="{pad}" y="{pad - 12}" font-size="13">n={len(points)}  min={ymin:.1f} ms  max={ymax:.1f} ms</text>
  {dots}
</svg>
"""
    path.write_text(svg, encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Define the benchmark CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--start-ts", type=int, default=None, help="window start, epoch ms")
    parser.add_argument("--end-ts", type=int, default=None, help="window end, epoch ms")
    parser.add_argument("--apply", action="store_true", help="query ThingsBoard over REST")
    parser.add_argument("--host", default=os.environ.get("TB_HOST", os.environ.get("TB_BASE_URL", "")))
    parser.add_argument("--jwt", default=os.environ.get("TB_JWT", ""))
    parser.add_argument("--api-key", default=os.environ.get("TB_API_KEY", ""))
    parser.add_argument("--ca-cert", default=os.environ.get("TB_CA_CERT", ""))
    parser.add_argument("--plot", action="store_true", help="write latency_scatter.svg")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Generate the report; return a process exit status."""
    args = build_parser().parse_args(argv)
    try:
        rows = load_edge_rows(args.db, args.start_ts, args.end_ts)
    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
        print(f"benchmark: cannot read {args.db}: {exc}", file=sys.stderr)
        return 2

    events = parse_log(args.log)
    report = build_report(
        rows, events, args.start_ts, args.end_ts,
        "apply" if args.apply else "dry-run",
    )
    points: list[tuple[int, float]] = []
    if args.apply:
        if not args.host or not (args.jwt or args.api_key):
            print("benchmark: --apply needs TB_HOST/TB_BASE_URL and TB_JWT or TB_API_KEY", file=sys.stderr)
            return 2
        client = ThingsBoardClient(args.host, jwt=args.jwt, api_key=args.api_key, ca_cert=args.ca_cert or None)
        window = report["window"]
        if window["start_ms"] is None or window["end_ms"] is None:
            print("benchmark: no telemetry rows in the selected window", file=sys.stderr)
            return 2
        cloud, points = cloud_metrics(
            client, rows, int(window["start_ms"]), int(window["end_ms"])
        )
        report["metrics"].update(cloud)
    else:
        report["notes"].append(
            "dry-run: broker-dependent metrics need --apply with TB_HOST and TB_JWT/TB_API_KEY"
        )

    write_outputs(report, args.out, points if args.plot else [])
    for name, entry in report["metrics"].items():
        value = entry["value"]
        shown = json.dumps(value, sort_keys=True) if isinstance(value, dict) else value
        print(f"{name}: {shown if shown is not None else 'N/A'} {entry['unit']}")
    print(f"report: {args.out / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
