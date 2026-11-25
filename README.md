# Post-flood water-quality virtual edge device

Python edge half of the ITS67404 Assessment Task 3 prototype: three virtual ESP32-S3
water-source nodes generate coupled contamination telemetry, process it locally and
store every reading in SQLite before an optional MQTT/TLS publish. The cloud side is
a separate track: `cloud/` holds **importable configuration templates** and measurement
tools. Nothing is provisioned by default; live tenant changes require an explicit
`--apply` or `--device-setup` with credentials supplied by the user.

## Quickstart

Requires Python 3.10+ and the packages in `requirements.txt`. The dry-run path uses
only the standard library and needs no broker or credentials.

```bash
python -m pip install -r requirements.txt
python -m edge.cli --dry-run
```

`--dry-run` is also the default when neither transport flag is given; `NullPublisher`
logs each batch and the process exits on `q` or `Ctrl+C`. Logs go to the console and
`logs/edge.log`; the durable queue is `edge.db`.

To publish to ThingsBoard, configure `.env` from `.env.example` and opt in:

```bash
python -m edge.cli --mqtt
```

See [INTEGRATION.md](INTEGRATION.md) for the exact topic, payload, token names and
alarm contract consumed by the Cloud Track.

### CLI options

| Flag | Default | Meaning |
| --- | --- | --- |
| `--scenario PATH` | `scenarios/demo.json` | Scripted phase timeline and per-node event parameters |
| `--dry-run` / `--mqtt` | dry-run | Console-only sink or per-node MQTT/TLS publishers (mutually exclusive) |
| `--tick-seconds N` | `5.0` | Sampling and flush interval |
| `--batch-size N` | `50` | Maximum rows claimed per flush, shared across nodes |
| `--log-level LEVEL` | `INFO` | Root logging level |
| `--db PATH` | `edge.db` | SQLite store-and-forward database |

## Interactive keys

Commands are read from stdin while the simulator runs. Phase keys log a
`STATE: <old> -> <new>` transition and restart that phase's local clock.

| Key | Action |
| --- | --- |
| `b` | Force `baseline` phase |
| `s` | Force `silt_intrusion` phase |
| `i` | Force `supply_interruption` phase |
| `r` | Force `recovery` phase |
| `o` | Toggle network outage (sampling and buffering continue) |
| `k` / `K` | Power `node-a` off / on (demonstrates `Node Inactive`) |
| `f` / `F` | Inject / clear a stuck-sensor fault on `node-a` |
| `a` | Retry authentication for all publishers |
| `p` | Pause / resume sampling |
| `q` | Quit; stops threads and closes the store without a final publish, so pending rows stay in `edge.db` |
| `h` | Log the help line |

## Module and owner map

| Path | Responsibility | Owner |
| --- | --- | --- |
| `edge/models.py`, `edge/config.py` | Shared data contracts; `.env`/environment/CLI configuration | Edge Track |
| `edge/sensors.py` | Deterministic virtual sensors and coupled intrusion model (Bijee: multi-node wiring; Saugat: model equations) | Edge Track |
| `edge/dsp.py`, `edge/validity.py` | Validity gates, Hampel filter, Nernst/TDS compensation, Brown WQI | Saugat (algorithms) |
| `edge/hysteresis.py` | Debounce, deadbands, severity aggregation, derived alarms | Saugat (algorithms) |
| `edge/storage.py`, `edge/sql/schema.sql` | WAL SQLite buffer, oldest-first eviction, ack/fail replay | Saugat (resilience) |
| `edge/publisher.py` | `NullPublisher` and per-node `MqttPublisher` with TLS/QoS 1 | Saugat (transport) |
| `edge/scenarios.py`, `scenarios/demo.json` | Repeatable phase timeline and manual phase forcing | Bijee (scenarios) |
| `edge/runtime.py`, `edge/cli.py` | Per-node sampling threads, command handling, flush loop, entry point | Bijee (telemetry flow/orchestration) |
| `tools/benchmark_batch_size.py` | Store-and-forward batch-size throughput measurement for the manuscript | Edge Track; results consumed by Cloud Track benchmarking |
| `INTEGRATION.md` | Cloud handoff contract | Edge → Cloud Track |
| `docs/DEMO_RUNBOOK.md` | 15-minute demonstration script mapped to the rubric | Both tracks |
| `docs/APPENDIX_A.md` | Individual task allocation matrix for the implementation ZIP | Both tracks |
| `docs/HANDOFF.md` | Continuation guide: setup, bugs/fixes, benchmarks, evidence and open items | Both tracks |
| `docs/TENANT_CHECKLIST.md` | Manual tenant steps for dashboard, profile, inactivity attribute, rule chain and evidence | Cloud Track |
| `cloud/artifacts/` | Device profile, nine per-rule Alarm `CalculatedField` import files, devices, verified-importable Cloud dashboard and WQI rule chain templates | Cloud Track |
| `cloud/provision.py`, `cloud/benchmark.py` | REST provisioning helper and Task 3 metric measurement | Cloud Track |
| `cloud/MANUSCRIPT.md` | Cloud Track manuscript sections with final measured values and log-derived figure references | Both tracks |
| `paper/` | Standalone IEEEtran source, final PDF and evidence-derived figures | Both tracks |
| `tools/build_paper_figures.py` | Regenerates manuscript figures from saved logs and benchmark outputs | Both tracks |
| `tools/build_submission_zip.py` | Assembles the group submission ZIP and rejects secrets | Both tracks |
| `evidence/` | Tenant captures, saved console logs and benchmark outputs included in the ZIP | Both tracks |
| `tests/` | pytest suite; no broker required | Both tracks |

Cloud Track owners (Anushree: ThingsBoard provisioning/dashboard; Sambid: alarm rules,
rule chain and broader benchmarking) import the templates into their own instance.
`python -m cloud.provision --dry-run` and `python -m cloud.benchmark` are broker-free;
`--apply` imports the committed artifacts and `--device-setup` creates the profile and
devices. Both live modes need a real ThingsBoard instance and user-supplied
credentials. Nothing here provisions or configures ThingsBoard automatically.

## Tests

Inside an activated environment with `requirements.txt` installed:

```bash
python -m pytest
```

The suite covers sensors, DSP, validity, hysteresis, storage, publishers, scenarios
and end-to-end runtime behaviour without network access.

## Batch-size benchmark

```bash
python tools/benchmark_batch_size.py
```

For each batch size (1, 10, 25, 50, 100, 200), this times 500 rows through the full
store-and-forward path: `enqueue()` into SQLite → `claim()` → `NullPublisher.publish_batch()`
→ `ack()`. Database setup is excluded from the timer and each size uses a fresh database.
It writes `tools/batch_results.csv` with the columns
`batch_size,rows_per_second,elapsed_s`. The committed CSV is a sample result; rerun
the script on the target hardware for manuscript numbers.

## Agent skills

Project-scoped OpenCode skills are installed in `.agents/skills/` and pinned in
`skills-lock.json` (copied files, not symlinks, so the submission ZIP stays portable).
Refresh them with `npx skills update -p -y`.

| Skill | Use it for |
| --- | --- |
| `matplotlib` | Publication-quality plots for the six mandatory benchmark metrics |
| `paper-writing-studio` | IEEE-profile polishing of the technical manuscript sections |
| `latex-paper-en` | IEEE LaTeX/Overleaf formatting, citations, tables and compile checks |
| `mqtt-development` | MQTT/TLS QoS 1, topic and reconnection best practices |
| `systematic-debugging` | Root-cause investigation for broker, schema and rule-chain faults |
| `verification-before-completion` | Evidence (tests, logs, measurements) before claiming a task is done |

## Submission package

Build the archive after the tenant captures, saved console logs and benchmark outputs
are in `evidence/`:

```bash
python tools/build_submission_zip.py --group <GROUP_NUMBER>
```

This writes `<GROUP_NUMBER>_Assessment-03_Implementation.zip` at the repository root.
The script includes source, schema, JSON artifacts, manuscript, IEEE source and PDF,
generated figures, tests, Appendix A and every file under `evidence/`. It refuses to
package `.env` files or JWT-like strings and prints a warning if no evidence files are
present. The current build uses `GroupNo` as the placeholder group identifier.
