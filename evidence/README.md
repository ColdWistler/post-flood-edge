# Evidence drop folder

Put the real demonstration and measurement files here before building the submission
ZIP. `tools/build_submission_zip.py` packages every file in this folder.

## Required evidence

| File name (suggested) | Content |
| --- | --- |
| `dashboard_baseline.png` | Eight-widget triage dashboard at baseline |
| `dashboard_intrusion.png` | Same widgets during forced `silt_intrusion` |
| `dashboard_backfill.png` | Multi-parameter chart after outage replay |
| `map_severity.png` | OpenStreetMap widget with severity-coloured markers |
| `alarms_table.png` | Native alarms table with active and cleared alarms |
| `buffer_replay.png` | Buffer growth, `REPLAY` lines and drain to zero |
| `edge_console.log` | Full console log of the run containing outage and recovery |
| `metrics.csv`, `metrics.json`, `latency_scatter.svg` | Copy from `cloud/benchmark/` after the real-broker run |

The benchmark outputs can also be left in `cloud/benchmark/`; the ZIP builder picks up
`metrics.json`, `metrics.csv` and `latency_scatter.svg` from either location.

## Rules

- Never place `.env`, access tokens, JWTs or signed URLs here. The ZIP builder rejects
  JWT-like content and files whose names contain `token`, `secret` or `credentials`.
- Record the phase, wall-clock time and visible telemetry values in the manuscript
  captions; a screenshot without those annotations is weak evidence.
- The six mandatory metrics must come from `cloud/benchmark/metrics.csv` produced
  against the deployed tenant. Host-only batch-size results are labelled separately.
