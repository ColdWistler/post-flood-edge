"""Tests for the submission archive builder."""

from __future__ import annotations

import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "tools" / "build_submission_zip.py"


def run_builder(tmp_path: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    output = tmp_path / "submission.zip"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--group",
            "07",
            "--output",
            str(output),
            *extra,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result


def test_archive_contains_required_artifacts(tmp_path: Path) -> None:
    result = run_builder(tmp_path)
    assert result.returncode == 0, result.stderr
    output = tmp_path / "submission.zip"
    with zipfile.ZipFile(output) as zf:
        names = set(zf.namelist())
    assert "cloud/artifacts/dashboard_triage.json" in names
    assert "cloud/artifacts/alarm_rules_import/README.md" in names
    assert "cloud/artifacts/alarm_rules_import/high_turbidity.json" in names
    assert "cloud/artifacts/wqi_rule_chain.json" in names
    assert "cloud/ANUSHREE.md" in names
    assert "cloud/build_dashboard.py" in names
    assert "cloud/exports/post-flood-dashboard.json" in names
    assert "cloud/evidence/01-dashboard-first-render.png" in names
    assert "tools/build_alarm_rule_imports.py" in names
    assert "tools/build_paper_figures.py" in names
    assert "paper/main.tex" in names
    assert "paper/main.pdf" in names
    assert "paper/figures/event_timeline.pdf" in names
    assert "cloud/benchmark/metrics_outage.json" in names
    assert "cloud/benchmark/metrics_live.json" in names
    assert "docs/APPENDIX_A.md" in names
    assert "docs/HANDOFF.md" in names
    assert ".env" not in names


def test_archive_includes_evidence_directory(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "alarm_table.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (evidence / "console.log").write_text("OUTAGE: START\n", encoding="utf-8")
    result = run_builder(tmp_path, "--evidence-dir", str(evidence))
    assert result.returncode == 0, result.stderr
    with zipfile.ZipFile(tmp_path / "submission.zip") as zf:
        names = set(zf.namelist())
    assert "evidence/alarm_table.png" in names
    assert "evidence/console.log" in names


def test_archive_rejects_jwt_like_evidence(tmp_path: Path) -> None:
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    (evidence / "token_dump.txt").write_text(
        "Authorization: eyJhbGciOiJIUzI1NiJ9.payload.signature", encoding="utf-8"
    )
    result = run_builder(tmp_path, "--evidence-dir", str(evidence))
    assert result.returncode == 2
    assert "JWT-like" in result.stderr
