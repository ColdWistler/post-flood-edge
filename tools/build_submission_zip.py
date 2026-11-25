"""Build the Assessment Task 3 submission ZIP from repository sources.

Usage::

    python tools/build_submission_zip.py --group 07

The archive name follows ``<GROUP>_Assessment-03_Implementation.zip``. Optional
evidence (screenshots, full console log, benchmark CSV/JSON/SVG) is taken from
``evidence/`` and ``cloud/benchmark/`` when present. Secrets are never included:
``.env``, token/JWT files and files containing JWT-like strings are rejected.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SOURCE_FILES = [
    "README.md",
    "INTEGRATION.md",
    "UPDATES.md",
    "requirements.txt",
    "scenarios/demo.json",
    ".env.example",
    "edge/__init__.py",
    "edge/cli.py",
    "edge/config.py",
    "edge/dsp.py",
    "edge/hysteresis.py",
    "edge/models.py",
    "edge/publisher.py",
    "edge/runtime.py",
    "edge/scenarios.py",
    "edge/sensors.py",
    "edge/storage.py",
    "edge/validity.py",
    "edge/sql/schema.sql",
    "cloud/__init__.py",
    "cloud/provision.py",
    "cloud/benchmark.py",
    "cloud/build_dashboard.py",
    "cloud/tune_dashboard.py",
    "cloud/README.md",
    "cloud/ANUSHREE.md",
    "cloud/ANUSHREE_MANUSCRIPT.md",
    "cloud/MANUSCRIPT.md",
    "cloud/benchmark/README.md",
    "cloud/artifacts/alarm_rules.json",
    "cloud/artifacts/alarm_rules_import/README.md",
    "cloud/artifacts/alarm_rules_import/gross_contamination.json",
    "cloud/artifacts/alarm_rules_import/high_tds.json",
    "cloud/artifacts/alarm_rules_import/high_turbidity.json",
    "cloud/artifacts/alarm_rules_import/node_inactive.json",
    "cloud/artifacts/alarm_rules_import/ph_out_of_range.json",
    "cloud/artifacts/alarm_rules_import/rapid_turbidity_rise.json",
    "cloud/artifacts/alarm_rules_import/severe_ph_persistence.json",
    "cloud/artifacts/alarm_rules_import/sensor_fault.json",
    "cloud/artifacts/alarm_rules_import/supply_interruption.json",
    "cloud/artifacts/device_profile.json",
    "cloud/artifacts/devices.json",
    "cloud/artifacts/dashboard_triage.json",
    "cloud/artifacts/dashboard_thingsboard_cloud.json",
    "cloud/artifacts/wqi_rule_chain.json",
    "cloud/artifacts/wqi_server_script.js",
    "docs/APPENDIX_A.md",
    "docs/DEMO_RUNBOOK.md",
    "docs/HANDOFF.md",
    "docs/TENANT_CHECKLIST.md",
    "paper/README.md",
    "paper/main.tex",
    "paper/references.bib",
    "tools/batch_results.csv",
    "tools/benchmark_batch_size.py",
    "tools/build_alarm_rule_imports.py",
    "tools/build_paper_figures.py",
    "tools/build_submission_zip.py",
    "tests/conftest.py",
    "tests/test_alarm_rule_imports.py",
    "tests/test_cloud_benchmark.py",
    "tests/test_cloud_provision.py",
    "tests/test_config.py",
    "tests/test_dsp.py",
    "tests/test_hysteresis.py",
    "tests/test_publisher.py",
    "tests/test_runtime.py",
    "tests/test_scenarios.py",
    "tests/test_sensors.py",
    "tests/test_storage.py",
    "tests/test_validity.py",
]

SECRET_NAME_RE = re.compile(r"(\.env$|\.jwt$|\.token$|credentials|secrets?|id_rsa)", re.I)
JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.")


def collect(
    rel: Path,
    files: dict[str, Path],
    missing: list[str],
    source: Path | None = None,
) -> None:
    """Add one file to the archive map under its repository-relative name."""
    path = source if source is not None else ROOT / rel
    if not path.is_file():
        missing.append(str(rel))
        return
    if SECRET_NAME_RE.search(path.name):
        print(f"refusing to package potential secret: {rel}", file=sys.stderr)
        raise SystemExit(2)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = ""
    if JWT_RE.search(text):
        print(f"refusing to package JWT-like content: {rel}", file=sys.stderr)
        raise SystemExit(2)
    files[rel.as_posix()] = path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", required=True, help="Group number, e.g. 07")
    parser.add_argument("--output", type=Path, help="Output ZIP path")
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        default=Path("evidence"),
        help="Directory containing screenshots, logs and benchmark outputs",
    )
    args = parser.parse_args()

    group = args.group.strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]+", group):
        parser.error("--group must contain only letters, digits, '-' or '_'")

    files: dict[str, Path] = {}
    missing: list[str] = []
    for name in SOURCE_FILES:
        collect(Path(name), files, missing)

    # Dashboard workstream exports and screenshots are part of the group package.
    for directory in ("cloud/exports", "cloud/evidence"):
        source_dir = ROOT / directory
        if source_dir.is_dir():
            for path in sorted(source_dir.iterdir()):
                if path.is_file():
                    collect(path.relative_to(ROOT), files, missing)

    for name in (
        "paper/main.pdf",
        "cloud/benchmark/metrics.json",
        "cloud/benchmark/metrics.csv",
        "cloud/benchmark/metrics_outage.json",
        "cloud/benchmark/metrics_live.json",
        "cloud/benchmark/latency_scatter.svg",
    ):
        path = ROOT / name
        if path.is_file():
            collect(Path(name), files, missing)

    figures = ROOT / "paper" / "figures"
    if figures.is_dir():
        for path in sorted(figures.iterdir()):
            if path.is_file():
                collect(path.relative_to(ROOT), files, missing)

    evidence = ROOT / args.evidence_dir
    if evidence.is_dir():
        for path in sorted(evidence.rglob("*")):
            if not path.is_file():
                continue
            try:
                rel = path.relative_to(ROOT)
                source = None
            except ValueError:
                # An external evidence directory is archived under evidence/.
                rel = Path("evidence") / path.relative_to(evidence)
                source = path
            collect(rel, files, missing, source=source)

    output = args.output or ROOT / f"{group}_Assessment-03_Implementation.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for arcname, path in sorted(files.items()):
            zf.write(path, arcname)

    print(f"wrote {output} ({len(files)} files)")
    if missing:
        print("missing source files:", ", ".join(missing), file=sys.stderr)
    evidence_files = [
        name
        for name in files
        if name.startswith("evidence/") or name.startswith(args.evidence_dir.as_posix())
    ]
    if not evidence_files:
        print(
            "warning: no evidence/ files were included; add screenshots and the full "
            "console log before submission",
            file=sys.stderr,
        )
    return 0 if not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
