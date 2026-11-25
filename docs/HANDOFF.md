# Continuation handoff — Cloud Track / Assessment Task 3

- **Prepared:** 2026-11-25
- **Owner of completed work:** Sambid Shrestha (0371697) — hysteresis alarms, server-side WQI, benchmarking and quantitative evaluation. Anushree Pradhan's dashboard provisioning deliverables are merged into this same branch.
- **Repository:** `https://github.com/Raspberry025/post-flood-edge.git`
- **Branch:** `main`
- **Placeholder group id:** `GroupNo` (replace only when the real group number is supplied).

This document is for the next group member and their Big Pickle agent. It records what
already works, what was fixed, how to set the project up from a fresh clone, and which
items still require a human tenant action. Do **not** rebuild the artifacts from
scratch, and do **not** ask for credentials unless a new live publish/import is
actually requested.

---

## 1. Read these files first

1. `docs/HANDOFF.md` — this file.
2. `docs/TENANT_CHECKLIST.md` — manual ThingsBoard UI steps and evidence capture.
3. `docs/DEMO_RUNBOOK.md` — scripted demo key sequence and expected log lines.
4. `INTEGRATION.md` — edge/cloud payload contract, alarm thresholds and buffer rules.
5. `cloud/README.md` — artifact inventory and tenant workflow.
6. `cloud/ANUSHREE.md` and `cloud/ANUSHREE_MANUSCRIPT.md` — dashboard provisioning workstream.
7. `cloud/MANUSCRIPT.md` — Cloud Track manuscript sections and measured values.
8. `paper/README.md` — IEEE source compile and evidence rules.

The IEEE report is frozen after the final compile. Author, institution and group
identifiers are intentionally placeholders; Sambid will fill them later. Do not edit
`paper/main.tex`, `paper/references.bib`, `paper/main.pdf` or `paper/figures/` unless
explicitly asked.

---

## 2. What was completed and verified

| Area | Status | Artifact / evidence |
| --- | --- | --- |
| Nine hysteresis alarm rules | Complete | `cloud/artifacts/alarm_rules.json` (spec) and `cloud/artifacts/alarm_rules_import/*.json` (nine importable `ALARM` documents) |
| Server-side WQI | Complete | `cloud/artifacts/wqi_rule_chain.json`, `cloud/artifacts/wqi_server_script.js`, tenant export `evidence/post-flood_wqi_rule_chain.json` |
| WQI tenant evidence | Complete | `evidence/wqi_server_telemetry.png` confirms `wqi_server`, `wqi_band_server`, `server_received_ms` |
| Eight-widget triage dashboard | Complete and visually evidenced | `cloud/artifacts/dashboard_triage.json`; tenant captures `evidence/Dashboard_8_widgets.png` and `evidence/Last_widget.png`; six-chart fallback `cloud/artifacts/dashboard_thingsboard_cloud.json` |
| Device profile / devices | Complete | `cloud/artifacts/device_profile.json`, `cloud/artifacts/devices.json`; assignment capture `evidence/device_profile_assignment.png` |
| Rule-chain exports | Complete | `evidence/root_rule_chain.json`, `evidence/post-flood_wqi_rule_chain.json` |
| Dashboard build/export tooling | Complete | `cloud/build_dashboard.py`, `cloud/tune_dashboard.py`, `cloud/exports/`, `cloud/evidence/` |
| Benchmark pipeline | Complete | `cloud/benchmark.py`, `cloud/benchmark/README.md`, `cloud/benchmark/metrics*.json`, `metrics.csv` |
| Batch-size sweep | Complete | `tools/benchmark_batch_size.py`, `tools/batch_results.csv` |
| Log-derived figures | Complete | `tools/build_paper_figures.py`, `paper/figures/*.svg` and `*.pdf` |
| IEEE source and final PDF | Complete | `paper/main.tex`, `paper/references.bib`, `paper/main.pdf` (12 pages) |
| Submission archive | Complete | `GroupNo_Assessment-03_Implementation.zip` (112 files, integrity checked) |
| Automated tests | Complete | 147 tests pass |

**Fresh verification run used for this handoff:**

```text
python -m pytest -q                       -> 147 passed
python -m cloud.provision --dry-run       -> 4 artifacts validated; no network calls
python -m compileall -q edge cloud tools tests -> clean
git diff --check                          -> clean
pdfinfo paper/main.pdf                    -> 12 pages
unzip -t GroupNo_Assessment-03_Implementation.zip -> no errors
```

The archive was also inspected programmatically: all required PDF, manuscript,
artifact, metric, log and evidence entries are present; no `.env`, secret-named file
or JWT-like string was found.

---

## 3. Fresh setup from a clone

### 3.1 Python environment

Use Python 3.10+.

```bash
git clone https://github.com/Raspberry025/post-flood-edge.git
cd post-flood-edge
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
```

In the previous working session the interpreter was
`/tmp/opencode/pfvenv/bin/python`; use your own virtual environment instead.

### 3.2 Environment variables

Copy the template and fill values only for live work:

```bash
cp .env.example .env
```

Variables in `.env.example`, plus optional live-work variables read directly from
the environment:

```text
TB_HOST
TB_CA_CERT
TB_JWT
TB_URL
TB_AUTH
TB_TOKEN_NODE_A
TB_TOKEN_NODE_B
TB_TOKEN_NODE_C
EDGE_BATCH_SIZE
EDGE_BUFFER_MAX_ROWS
EDGE_FLOW_ZERO_SUSTAINED_S
EDGE_NODE_INACTIVE_S
EDGE_DERIVATIVE_WINDOW_S
EDGE_VALIDITY_FAULT_SAMPLES
EDGE_TICK_SECONDS
EDGE_DB_PATH
EDGE_LOG_LEVEL
```

Important rules:

- `TB_HOST` is the host only, e.g. `thingsboard.cloud`, **without** `https://`.
  `TB_URL` is the full URL used only by `--device-setup` (default
  `https://eu.thingsboard.cloud`).
- `TB_JWT` and `TB_AUTH` are live-work environment variables, not template values.
  `--device-setup` also accepts an API key from the ignored `.thingsboard-auth` file.
- The tenant JWT is **not** in `.env.example`. Supply it per benchmarking run with
  `export TB_JWT=...` from the user's environment. It is short-lived and must never
  be written to a file, chat message, log or test fixture.
- `.env`, `edge.db*`, `logs/` and `paper/main.pdf` are git-ignored.
- Never commit or paste real device access tokens or a JWT.

### 3.3 Broker-free smoke checks

```bash
python -m pytest -q
python -m cloud.provision --dry-run
python -m cloud.benchmark
python -m edge.cli --dry-run
```

`--dry-run` and the default transport paths make no network calls. `q` exits the CLI;
`p` pauses; `b/s/i/r` force phases; `o` toggles outage; `k/K` powers node-a off/on;
`f/F` injects/clears a stuck fault; `a` retries auth; `h` prints help.

### 3.4 Live edge publishing

Only after the user has supplied tokens through the environment:

```bash
python -m edge.cli --mqtt
```

The publisher uses MQTT/TLS on port 8883, topic `v1/devices/me/telemetry`, QoS 1,
and marks rows synced only after a PUBACK for every chunk.

---

## 4. ThingsBoard tenant setup (human steps)

The tenant work is done in the user's UI. Do **not** run
`python -m cloud.provision --apply` or `python -m cloud.provision --device-setup`
unless the user explicitly asks for it. The two modes are intentionally separate:
`--apply` imports the four committed artifact files with `TB_HOST` plus `TB_JWT`
(or admin login), while `--device-setup` runs Anushree's version-compatible
profile/device creation with `TB_URL` plus `TB_AUTH`/`.thingsboard-auth` and writes
device tokens only to the ignored local `.env`. The dashboard helper scripts are
`cloud/build_dashboard.py` and `cloud/tune_dashboard.py`.

### 4.1 Fixed identities

| Device | Device ID | Coordinates |
| --- | --- | --- |
| node-a | `81e5b1f0-e883-11f1-ab4e-dfd4fe9de952` | 27.7172, 85.3240 |
| node-b | `01f214e0-e886-11f1-b8ac-4bd49425d19e` | 27.6790, 85.3190 |
| node-c | `0bb52a80-e886-11f1-86e5-9755011de358` | 27.7500, 85.3700 |

Device profile name: `post-flood-water-quality`.

`RSSI` is intentionally **N/A**: these are virtual nodes and publish no radio
telemetry. Do not invent a value.

### 4.2 Import order

1. Import `cloud/artifacts/device_profile.json` and assign node-a/b/c to the profile.
   The **Alarm Rules** tab may appear empty on current Cloud tenants; this is expected.
2. Import the nine files in `cloud/artifacts/alarm_rules_import/` one by one via
   **Alarm rules → Import**, targeting `post-flood-water-quality`. If the tenant has
   no Import action, create the rules manually from those files. Do **not** try to
   import `alarm_rules.json` or a file named `alarmrules.json`.
3. Import `cloud/artifacts/dashboard_triage.json`. If it fails, keep
   `cloud/artifacts/dashboard_thingsboard_cloud.json` as the confirmed six-chart
   fallback and add the extra widgets manually.
4. Import `cloud/artifacts/wqi_rule_chain.json` and link it **from** the existing root
   chain with a Rule Chain node and a Success connection. Save and exit. Do **not**
   set it as root on a tenant with existing rule handling. Some tenants have no
   Publish button; Save-and-exit is sufficient.
5. Verify Latest Telemetry for node-a contains `wqi_server`, `wqi_band_server` and
   `server_received_ms`. Without `server_received_ms` the latency metric is unavailable.
6. Add server-scope device attribute `inactivityTimeout` (ms) if not already present:
   `10800000` for 180 min normal, `2700000` for 45 min burst. This remains an open
   tenant-evidence item.
7. Export both rule chains and save captures into `evidence/`.

See `docs/TENANT_CHECKLIST.md` for the full click-by-click workflow and evidence
capture requirements.

---

## 5. Threshold and algorithm contract

Do not change these without updating the edge code, the import files, the manuscript
and the tests together.

| Rule | Raise | Clear | Severity | Debounce |
| --- | --- | --- | --- | --- |
| High Turbidity | `>50` NTU | `<40` NTU | Warning | 1/1 |
| Gross Contamination | `>200` NTU | `<150` NTU | Critical | 1/1 |
| pH Out of Range | `<6.5` or `>8.5` | `6.7–8.3` | Critical | 3/3 |
| High TDS | `>500` ppm | `<450` ppm | Critical | 3/3 |
| Supply Interruption | `flow == 0` for 30 s | non-zero flow | Major | demo value |
| Node Inactive | 90 s without samples | new sample | Warning | demo value |
| Severe pH Persistence | `<6.0` or `>9.0` | `6.2–8.8` | Critical | 3/3 |
| Rapid Turbidity Rise | `>30` NTU in 50 s | window expires | Warning | derivative |
| Sensor Fault | stuck-sensor flag | valid variation | Fault | 3/3 |

WQI constants:

```text
standards S      = (8.5, 5.0, 1000.0)
ideals   V0      = (7.0, 0.0, 0.0)
weights  w       = (0.3692, 0.6277, 0.0031)
bands            = <=25 Excellent, <=50 Good, <=75 Poor,
                   <=100 Very poor, >100 Unsuitable
```

Edge DSP uses a nine-sample Hampel window, `k = 3`, MAD scale `1.4826`, Nernst
temperature compensation and TDS correction `alpha = 0.02 / deg C`. The hysteresis
engine is a three-state counter: consecutive breaches raise the latch, consecutive
clear-eligible samples lower it, and deadband samples hold the state while resetting
clear progress.

---

## 6. Bugs found and fixes already applied

| Symptom | Root cause | Fix |
| --- | --- | --- |
| MQTT `disconnect rc=7`, no PUBACK | ThingsBoard Cloud rejected telemetry packets above roughly 2 KiB | `edge/publisher.py` serialises into `<=1800`-byte JSON arrays and requires a PUBACK for every chunk before acknowledging the batch |
| Three publishers took over each other's sessions | Empty/stable-less MQTT client IDs | Stable IDs `post-flood-node-a`, `post-flood-node-b`, `post-flood-node-c` |
| Paho reconnect threads outlived the CLI | `disconnect()` stopped the loop too late or not at all | `loop_stop()` is called before `disconnect()` |
| Tenant HTTP replay returned 500 for larger payloads | This Cloud tenant accepted only small HTTP batches | HTTP fallback splits replay into 5-point chunks; the MQTT path uses the 1800-byte ceiling |
| Benchmark reported N/A for latency | Tenant rejected lowercase `agg=avg`; raw telemetry needs `agg=NONE` | Benchmark now sends `agg=NONE` and caps the query at 50000 points |
| Benchmark reported N/A for alarms | Tenant-wide alarm endpoints retired | Alarm history is read with `GET /api/alarm/DEVICE/{uuid}` |
| JWT became invalid between runs | Short-lived tenant token | JWT is read from the environment per run and refreshed before the second run; never persisted |
| Dashboard widgets rendered blank | `AVG` aggregation plus sparse points; unresolved `stateEntity`/`deviceType` aliases | `NONE`, 24-hour timewindow, `showPoints: true`, direct `deviceId` bindings and explicit `entityList` aliases |
| Map showed no markers | Legacy `system.maps_v2.openstreetmap` widget; no `severity` key in Latest Telemetry | Current `system.map` direct-device markers with colour fallback from `active_alarms`, turbidity, pH, TDS and flow |
| Imported profile had an empty Alarm Rules tab | Current tenant hides deprecated `profileData.alarms` | Nine per-rule `CalculatedField` documents of type `ALARM` are supplied for native import |
| Tenant UI had no Publish button after chain edit | Tenant/save workflow difference | Save-and-exit is sufficient; Publish is used only if the UI exposes it |
| Outage-run latency looked like a huge network delay | Formula counted time spent in the local buffer | Final reporting splits outage metrics from the separate no-outage live run |
| Virtual nodes had no RSSI | No radio hardware exists | Reported as N/A, never fabricated |
| Two `cloud.provision` CLI implementations collided during merge | Both tracks independently replaced the same module | One module now exposes both APIs; artifact work stays in `--apply` and Anushree's device setup is `--device-setup`; both original test suites pass |

---

## 7. Benchmarking and measurement reproduction

### 7.1 Final measured values

These are the authoritative numbers in `cloud/benchmark/metrics.json`:

| Metric | Value | Provenance |
| --- | --- | --- |
| End-to-end latency | n=528; mean 1457.17 ms; median 1444.00; P95 2364.55; max 2939.00 | no-outage live run |
| Message delivery ratio | 99.858757% (707/708) | 32 s outage run |
| Alarm activation delay | n=18; mean 1329.22 ms; median 1288.5; P95 2239.35; max 2519.0 | no-outage live run |
| Buffer-flush throughput | 1.549812 rows/s (96 replay rows) | outage run |
| Recovery time | 61.943 s | outage run |
| Data loss | 1 row (0.141243%) | outage run |

The outage latency (n=709, mean 270767.5 ms) is a resilience bound, not a live
network latency, and is reported separately in the paper.

Raw reports:

```text
cloud/benchmark/metrics_outage.json  window 1795599520559..1795606398195, 708 rows, 1 outage
cloud/benchmark/metrics_live.json    window 1795607091933..1795607268807, 530 rows, 0 outages
```

### 7.2 Re-running the benchmark

`--out` is an **output directory**; the script writes `metrics.json`, `metrics.csv`
and optionally `latency_scatter.svg` there.

```bash
# Dry-run smoke test, no tenant calls:
python -m cloud.benchmark

# With a fresh tenant JWT in the environment:
export TB_JWT=...   # supplied by the user, never committed

python -m cloud.benchmark --apply --plot \
  --log evidence/edge_outage_final.log \
  --start-ts 1795599520559 --end-ts 1795606398195 \
  --out /tmp/opencode/pf-bench-outage

python -m cloud.benchmark --apply --plot \
  --log evidence/edge_clean_final.log \
  --start-ts 1795607091933 --end-ts 1795607268807 \
  --out /tmp/opencode/pf-bench-live
```

Copy the two generated `metrics.json` files to
`cloud/benchmark/metrics_outage.json` and `cloud/benchmark/metrics_live.json` when
re-measuring. The consolidated `cloud/benchmark/metrics.json` is a manual merge that
takes delivery/throughput/recovery/loss from the outage report and latency/alarm
delay from the live report. Do not replace it with a single raw run.

### 7.3 Batch-size sweep

```bash
python tools/benchmark_batch_size.py
```

It writes `tools/batch_results.csv`. The committed host-only results are:

```text
batch 1   -> 12992.94 rows/s
batch 10  -> 22203.64 rows/s
batch 25  -> 23001.16 rows/s
batch 50  -> 23543.50 rows/s
batch 100 -> 24330.85 rows/s
batch 200 -> 23668.94 rows/s
```

These are local storage-path numbers, not broker throughput. The live publisher is
constrained by the 1800-byte packet ceiling and PUBACK latency.

---

## 8. Figure and PDF regeneration

The report is frozen, but these are the commands used if the group later asks for a
fresh build.

```bash
python tools/build_paper_figures.py

for f in paper/figures/*.svg; do
  rsvg-convert -f pdf -o "${f%.svg}.pdf" "$f"
done

export TEXINPUTS=/path/to/IEEEtran:
export BSTINPUTS=/path/to/IEEEtran:
cd paper
pdflatex -interaction=nonstopmode -halt-on-error main.tex
bibtex main
pdflatex -interaction=nonstopmode -halt-on-error main.tex
pdflatex -interaction=nonstopmode -halt-on-error main.tex
```

The local compile helper was also used successfully:

```bash
/tmp/opencode/pfvenv/bin/python -B \
  ../.agents/skills/latex-paper-en/scripts/compile.py \
  main.tex --recipe pdflatex-bibtex
```

Final compile result: exit 0, 12 pages, no undefined references/citations and no
overfull boxes. Keep author/group placeholders until Sambid supplies the final
values.

---

## 9. Submission archive

```bash
python tools/build_submission_zip.py --group <GROUP_NUMBER>
```

This writes `<GROUP_NUMBER>_Assessment-03_Implementation.zip` at the repository root.
The current archive uses the placeholder id:

```bash
python tools/build_submission_zip.py --group GroupNo
```

The builder includes source, schema, JSON artifacts, both workstreams' manuscript
sources, final PDF, generated figures, tests, Appendix A, all files under
`evidence/`, and Anushree's `cloud/exports/` and `cloud/evidence/` captures. It
rejects `.env`, token/JWT files and JWT-like content before writing the ZIP.

Current archive: `GroupNo_Assessment-03_Implementation.zip`, 112 files, integrity
checked. When the real group number is known, rebuild the ZIP with that value and
delete or replace the placeholder archive.

---

## 10. Evidence inventory

| Group | Files |
| --- | --- |
| Tenant captures | `evidence/device_profile_assignment.png`, `evidence/wqi_server_telemetry.png`, `evidence/Dashboard_8_widgets.png`, `evidence/Last_widget.png` |
| Tenant exports | `evidence/root_rule_chain.json`, `evidence/post-flood_wqi_rule_chain.json`, `cloud/exports/*.json` |
| Dashboard workstream captures | `cloud/evidence/00-devices-profile.png`, `01-dashboard-first-render.png`, `02-source-first-render.png`, `03-dashboard-refreshed.png` |
| Runtime logs | `evidence/edge_outage_final.log`, `evidence/edge_clean_final.log` plus earlier bench-run logs |
| Benchmark outputs | `cloud/benchmark/metrics.json`, `metrics.csv`, `metrics_outage.json`, `metrics_live.json`, `latency_scatter.svg` |
| Cloud artifacts | `cloud/artifacts/dashboard_triage.json`, nine `alarm_rules_import/*.json`, profile/devices/WQI chain |
| Generated figures | `paper/figures/*.svg` and matching `*.pdf`; two tenant PNGs copied into the same directory |

The available tenant screenshots are listed above. Missing UI captures were not
fabricated; the paper uses explicitly labelled log-derived figures for those phases.

---

## 11. Open items / boundaries

These are not code defects:

1. Replace `GroupNo` and the author/institution/email placeholders when the group
   supplies the final submission details.
2. Confirm and capture the server-scope `inactivityTimeout` attribute on each device.
3. The final eight-widget dashboard is visually confirmed by
   `evidence/Dashboard_8_widgets.png` and `evidence/Last_widget.png`. A screenshot of
   the nine-row imported alarm-rules table was not retained; the committed import
   documents and tests define those rules.
4. Other members' manuscript review is pending; Sambid said he will handle the report
   later. Do not edit `paper/` in the meantime.
5. A longer field trial with real sensors and multiple outages is needed before any
   production claim; current results are from virtual nodes and one completed outage.

---

## 12. Rules for the next agent

- Read before acting; do not recreate the repository or regenerate artifacts
  unnecessarily.
- Use dry-run by default. `--apply` and `--device-setup` require explicit user
  approval and credentials from the environment.
- Never print, commit or paste tokens/JWTs.
- Preserve the two-run metric split; do not average outage latency into live latency.
- Preserve the log-derived-figure labelling; do not present generated plots as tenant
  screenshots.
- Run `python -m pytest -q`, `python -m cloud.provision --dry-run` and
  `git diff --check` after any change.
- Commit only intentional source/artifact changes; keep `.env`, `edge.db*` and `logs/`
  ignored.
