"""Generate paper figures from committed evidence and measured logs.

The generated SVGs are converted to PDF with ``rsvg-convert`` (see paper/README.md)
so that IEEEtran can include them without additional LaTeX packages. No figure
contains simulated data: the buffer plot reads the saved edge console log, the
batch plot reads the measured sweep, and the rule-chain diagram reads the tenant
export.
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "paper" / "figures"

_BUFFER_RE = re.compile(r"BUFFER: total=(\d+)")
_OUTAGE_RE = re.compile(r"OUTAGE: (START|END)")


def _svg_header(width: int, height: int, title: str) -> list[str]:
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width / 2:.0f}" y="24" text-anchor="middle" '
        f'font-family="sans-serif" font-size="17" font-weight="bold">{title}</text>',
    ]


def _axes(lines: list[str], width: int, height: int, pad: int,
          xlabel: str, ylabel: str) -> None:
    lines.append(
        f'<line x1="{pad}" y1="{height - pad}" x2="{width - pad}" '
        f'y2="{height - pad}" stroke="#334155" stroke-width="1.5"/>'
    )
    lines.append(
        f'<line x1="{pad}" y1="{pad}" x2="{pad}" y2="{height - pad}" '
        f'stroke="#334155" stroke-width="1.5"/>'
    )
    lines.append(
        f'<text x="{width / 2:.0f}" y="{height - 10}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="13">{xlabel}</text>'
    )
    lines.append(
        f'<text x="12" y="{height / 2:.0f}" text-anchor="middle" '
        f'font-family="sans-serif" font-size="13" transform="rotate(-90 12 {height / 2:.0f})">'
        f'{ylabel}</text>'
    )


def architecture() -> None:
    width, height = 900, 330
    lines = _svg_header(width, height, "End-to-end telemetry chain and trust boundary")
    stages = [
        ("Virtual nodes\nnode-a/b/c", 20),
        ("Validity gates\n+ Hampel", 165),
        ("Brown WQI\n+ hysteresis", 310),
        ("SQLite buffer\nWAL, 3 states", 455),
        ("MQTT/TLS\nQoS 1, chunks", 600),
        ("ThingsBoard\nrule engine", 745),
    ]
    for label, x in stages:
        lines.append(
            f'<rect x="{x}" y="80" width="125" height="70" rx="8" '
            f'fill="#e0f2fe" stroke="#0369a1" stroke-width="2"/>'
        )
        for row, text in enumerate(label.split("\n")):
            lines.append(
                f'<text x="{x + 62}" y="{106 + row * 22}" text-anchor="middle" '
                f'font-family="sans-serif" font-size="14">{text}</text>'
            )
    for x in (145, 290, 435, 580, 725):
        lines.append(
            f'<line x1="{x}" y1="115" x2="{x + 19}" y2="115" '
            f'stroke="#0369a1" stroke-width="2" marker-end="url(#arrow)"/>'
        )
    lines.append(
        '<defs><marker id="arrow" markerWidth="8" markerHeight="8" refX="6" refY="3" '
        'orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#0369a1"/></marker></defs>'
    )
    lines.append(
        '<rect x="745" y="200" width="125" height="52" rx="8" '
        'fill="#dcfce7" stroke="#15803d" stroke-width="2"/>'
    )
    lines.append(
        '<text x="807" y="220" text-anchor="middle" font-family="sans-serif" '
        'font-size="13">Dashboard, alarms,</text>'
    )
    lines.append(
        '<text x="807" y="239" text-anchor="middle" font-family="sans-serif" '
        'font-size="13">WQI rule chain</text>'
    )
    lines.append(
        '<line x1="807" y1="150" x2="807" y2="200" stroke="#15803d" '
        'stroke-width="2" marker-end="url(#arrow)"/>'
    )
    lines.append(
        '<rect x="20" y="200" width="420" height="52" rx="8" '
        'fill="#fef3c7" stroke="#b45309" stroke-width="2"/>'
    )
    lines.append(
        '<text x="230" y="221" text-anchor="middle" font-family="sans-serif" '
        'font-size="13">Local trust boundary: credentials and acquisition timestamps stay at the edge</text>'
    )
    lines.append(
        '<text x="230" y="240" text-anchor="middle" font-family="sans-serif" '
        'font-size="12">Sampling continues while the cloud is unreachable</text>'
    )
    lines.append("</svg>")
    (OUT / "architecture.svg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def buffer_depth(zoomed: bool) -> None:
    path = ROOT / "evidence" / "edge_outage_final.log"
    text = path.read_text(encoding="utf-8")
    base = datetime.strptime(
        re.search(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}", text, re.M).group(0),
        "%Y-%m-%d %H:%M:%S,%f",
    )
    points: list[tuple[float, int]] = []
    end_at: float | None = None
    for line in text.splitlines():
        match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})", line)
        if not match:
            continue
        at = (datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S,%f") - base).total_seconds()
        if "OUTAGE: END" in line:
            end_at = at
        buffered = _BUFFER_RE.search(line)
        if buffered:
            points.append((at, int(buffered.group(1))))
    if not points:
        return
    if zoomed and end_at is not None:
        points = [(t, value) for t, value in points if end_at - 10 <= t <= end_at + 75]
        base_t = end_at - 10
    else:
        base_t = points[0][0]
    rel = [(t - base_t, value) for t, value in points]
    width, height, pad = 900, 360, 62
    lines = _svg_header(
        width,
        height,
        "Buffered telemetry depth during outage and replay" if not zoomed
        else "Replay phase: buffer drains to zero",
    )
    _axes(lines, width, height, pad, "time relative to outage end (s)", "buffered rows")
    xmax = max(t for t, _ in rel)
    ymax = max(value for _, value in rel) or 1
    def px(t: float) -> float:
        return pad + (t / xmax) * (width - 2 * pad)
    def py(value: int) -> float:
        return height - pad - (value / ymax) * (height - 2 * pad)
    if end_at is not None and base_t <= end_at <= base_t + xmax:
        x = px(end_at - base_t)
        lines.append(
            f'<line x1="{x:.1f}" y1="{pad}" x2="{x:.1f}" y2="{height - pad}" '
            f'stroke="#dc2626" stroke-width="2" stroke-dasharray="6 4"/>'
        )
        lines.append(
            f'<text x="{x + 6:.1f}" y="{pad + 16}" font-family="sans-serif" '
            f'font-size="13" fill="#dc2626">connectivity restored</text>'
        )
    segments: list[list[tuple[float, int]]] = [[]]
    previous: float | None = None
    for t, value in rel:
        if previous is not None and t - previous > 5:
            segments.append([])
        segments[-1].append((t, value))
        previous = t
    for segment in segments:
        if len(segment) < 2:
            continue
        coords = " ".join(f"{px(t):.1f},{py(v):.1f}" for t, v in segment)
        lines.append(
            f'<polyline points="{coords}" fill="none" stroke="#2563eb" stroke-width="2"/>'
        )
    lines.append(
        f'<text x="{pad}" y="{pad - 10}" font-family="sans-serif" font-size="13">'
        f'peak={ymax} rows; final={rel[-1][1]} rows</text>'
    )
    lines.append("</svg>")
    name = "buffer_depth.svg" if not zoomed else "buffer_replay.svg"
    (OUT / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _parse_log(path: Path) -> tuple[datetime, list[tuple[datetime, str]]]:
    """Return the base timestamp and (timestamp, message) pairs from an edge log."""
    lines: list[tuple[datetime, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) (.*)$", line)
        if match:
            lines.append(
                (
                    datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S,%f"),
                    match.group(2),
                )
            )
    return (lines[0][0] if lines else datetime.now()), lines


def event_timeline() -> None:
    """Cumulative confirmed rows and phase transitions from the clean-run log."""
    base, events = _parse_log(ROOT / "evidence" / "edge_clean_final.log")
    points: list[tuple[float, int]] = []
    cumulative = 0
    phase_at: float | None = None
    for at, message in events:
        rel = (at - base).total_seconds()
        published = re.search(r"PUBLISH node=\S+ rows=(\d+)", message)
        if published:
            cumulative += int(published.group(1))
            points.append((rel, cumulative))
        if "STATE: baseline -> silt_intrusion" in message:
            phase_at = rel
    if not points:
        return
    width, height, pad = 900, 360, 62
    lines = _svg_header(width, height, "Clean-run publish timeline reconstructed from the edge log")
    _axes(lines, width, height, pad, "time from run start (s)", "confirmed rows")
    xmax = max(t for t, _ in points)
    ymax = max(value for _, value in points) or 1
    def px(t: float) -> float:
        return pad + (t / xmax) * (width - 2 * pad)
    def py(value: int) -> float:
        return height - pad - (value / ymax) * (height - 2 * pad)
    coords = " ".join(f"{px(t):.1f},{py(v):.1f}" for t, v in points)
    lines.append(
        f'<polyline points="{coords}" fill="none" stroke="#2563eb" stroke-width="2.5"/>'
    )
    if phase_at is not None:
        x = px(phase_at)
        lines.append(
            f'<line x1="{x:.1f}" y1="{pad}" x2="{x:.1f}" y2="{height - pad}" '
            f'stroke="#dc2626" stroke-width="2" stroke-dasharray="6 4"/>'
        )
        lines.append(
            f'<text x="{x + 6:.1f}" y="{pad + 16}" font-family="sans-serif" '
            f'font-size="12" fill="#dc2626">silt intrusion forced</text>'
        )
    lines.append(
        f'<text x="{pad}" y="{pad - 10}" font-family="sans-serif" font-size="13">'
        f'final={cumulative} confirmed rows; source: edge_clean_final.log</text>'
    )
    lines.append("</svg>")
    (OUT / "event_timeline.svg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def alarm_evidence() -> None:
    """Edge fault events plus alarm-delay summary, both from saved evidence."""
    base, events = _parse_log(ROOT / "evidence" / "edge_clean_final.log")
    faults: list[tuple[float, str]] = []
    phase_at: float | None = None
    for at, message in events:
        rel = (at - base).total_seconds()
        fault = re.search(r"FAULT: node=(\S+) severity=(\w+)", message)
        if fault:
            faults.append((rel, fault.group(1)))
        if "STATE: baseline -> silt_intrusion" in message:
            phase_at = rel
    with (ROOT / "cloud" / "benchmark" / "metrics.csv").open(encoding="utf-8") as handle:
        rows = {row["metric"]: row for row in csv.DictReader(handle)}
    delay = json.loads(rows["alarm_activation_delay_ms"]["value"])
    width, height, pad = 900, 380, 62
    lines = _svg_header(width, height, "Alarm evidence: edge fault events and measured activation delay")
    split = 150
    lines.append(
        f'<line x1="{pad}" y1="{split}" x2="{width - pad}" y2="{split}" '
        f'stroke="#cbd5e1" stroke-width="1"/>'
    )
    xmax = max((t for t, _ in faults), default=1.0) or 1.0
    node_y = {"node-a": 64, "node-b": 90, "node-c": 116}
    for node, y in node_y.items():
        lines.append(
            f'<text x="{pad + 4}" y="{y - 4}" font-family="sans-serif" '
            f'font-size="12">{node}</text>'
        )
        lines.append(
            f'<line x1="{pad + 74}" y1="{y}" x2="{width - pad}" y2="{y}" '
            f'stroke="#e2e8f0" stroke-width="1"/>'
        )
    if phase_at is not None:
        x = pad + (phase_at / xmax) * (width - pad - 74)
        lines.append(
            f'<line x1="{x:.1f}" y1="46" x2="{x:.1f}" y2="130" '
            f'stroke="#dc2626" stroke-width="2" stroke-dasharray="5 4"/>'
        )
    for rel, node in faults:
        x = pad + (rel / xmax) * (width - pad - 74)
        lines.append(
            f'<circle cx="{x:.1f}" cy="{node_y[node]}" r="5" fill="#dc2626"/>'
        )
    lines.append(
        f'<text x="{pad}" y="44" font-family="sans-serif" font-size="12">'
        f'FAULT events in saved edge log (red dashed line: forced intrusion)</text>'
    )
    stats = [
        ("mean", delay["mean"]),
        ("median", delay["median"]),
        ("P95", delay["p95"]),
        ("max", delay["max"]),
    ]
    bar_max = max(value for _, value in stats) or 1
    bar_y = [202, 240, 278, 316]
    for (label, value), y in zip(stats, bar_y):
        lines.append(
            f'<text x="{pad}" y="{y + 4}" font-family="sans-serif" '
            f'font-size="13">{label}</text>'
        )
        bar_w = (value / bar_max) * 520
        lines.append(
            f'<rect x="{pad + 80}" y="{y - 11}" width="{bar_w:.1f}" height="16" '
            f'fill="#7c3aed"/>'
        )
        lines.append(
            f'<text x="{pad + 88 + bar_w:.1f}" y="{y + 4}" font-family="sans-serif" '
            f'font-size="12">{value:.0f} ms</text>'
        )
    lines.append(
        f'<text x="{pad}" y="178" font-family="sans-serif" font-size="12">'
        f'Alarm activation delay from cloud/benchmark/metrics.csv (n={delay["count"]})</text>'
    )
    lines.append("</svg>")
    (OUT / "alarm_evidence.svg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def batch_sweep() -> None:
    with (ROOT / "tools" / "batch_results.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    width, height, pad = 900, 340, 70
    lines = _svg_header(width, height, "Host-only batch-size sweep (500 rows per size)")
    _axes(lines, width, height, pad, "batch size (rows)", "rows/s")
    xs = [float(row["batch_size"]) for row in rows]
    ys = [float(row["rows_per_second"]) for row in rows]
    ymin, ymax = min(ys) * 0.95, max(ys) * 1.03
    def px(value: float) -> float:
        return pad + (value / max(xs)) * (width - 2 * pad)
    def py(value: float) -> float:
        return height - pad - ((value - ymin) / (ymax - ymin)) * (height - 2 * pad)
    coords = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in zip(xs, ys))
    lines.append(
        f'<polyline points="{coords}" fill="none" stroke="#7c3aed" stroke-width="2.5"/>'
    )
    for x, y, row in zip(xs, ys, rows):
        lines.append(
            f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="4" fill="#7c3aed"/>'
        )
        lines.append(
            f'<text x="{px(x):.1f}" y="{py(y) - 10:.1f}" text-anchor="middle" '
            f'font-family="sans-serif" font-size="12">{y:.0f}</text>'
        )
    lines.append("</svg>")
    (OUT / "batch_sweep.svg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def rule_chain() -> None:
    data = json.loads(
        (ROOT / "evidence" / "post-flood_wqi_rule_chain.json").read_text(encoding="utf-8")
    )
    nodes = data["metadata"]["nodes"]
    connections = data["metadata"]["connections"]
    width, height = 900, 320
    lines = _svg_header(width, height, "Tenant export: Post-flood WQI rule chain")
    positions = {"Has WQI inputs?": 60, "Compute WQI server-side": 350,
                 "Save derived telemetry": 640}
    for node in nodes:
        name = node["name"]
        x = positions.get(name, 60 + list(positions).index(name) * 290)
        fill = "#dbeafe" if "Filter" in node["type"] else (
            "#dcfce7" if "Transform" in node["type"] else "#fef3c7")
        lines.append(
            f'<rect x="{x}" y="105" width="200" height="85" rx="8" '
            f'fill="{fill}" stroke="#334155" stroke-width="2"/>'
        )
        label = name if len(name) < 26 else name[:24] + "..."
        lines.append(
            f'<text x="{x + 100}" y="140" text-anchor="middle" '
            f'font-family="sans-serif" font-size="14" font-weight="bold">{label}</text>'
        )
        kind = node["type"].split(".")[-1].replace("Tb", "").replace("Node", "")
        lines.append(
            f'<text x="{x + 100}" y="163" text-anchor="middle" '
            f'font-family="sans-serif" font-size="12">{kind}</text>'
        )
    for name, x in positions.items():
        if x < 640:
            lines.append(
                f'<line x1="{x + 200}" y1="147" x2="{x + 290}" y2="147" '
                f'stroke="#334155" stroke-width="2" marker-end="url(#arrow2)"/>'
            )
    lines.append(
        '<defs><marker id="arrow2" markerWidth="8" markerHeight="8" refX="6" refY="3" '
        'orient="auto"><path d="M0,0 L6,3 L0,6 Z" fill="#334155"/></marker></defs>'
    )
    lines.append(
        f'<text x="{width / 2:.0f}" y="250" text-anchor="middle" '
        f'font-family="sans-serif" font-size="13">'
        f'{len(nodes)} nodes, {len(connections)} links; linked from the active root chain</text>'
    )
    lines.append("</svg>")
    (OUT / "rule_chain.svg").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    architecture()
    event_timeline()
    alarm_evidence()
    buffer_depth(zoomed=False)
    buffer_depth(zoomed=True)
    batch_sweep()
    rule_chain()
    print("wrote SVGs to", OUT.relative_to(ROOT))


if __name__ == "__main__":
    main()
