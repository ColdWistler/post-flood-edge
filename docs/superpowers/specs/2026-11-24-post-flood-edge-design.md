# Design: Post-Flood Water Contamination Monitoring — Edge Track

**Date:** 2026-11-24
**Course:** ITS67404 Internet of Things, Assessment Task 3 (September 2026)
**Owners:** Saugat Chaudhary Tharu (0371451) — algorithms / resilience / transport; Bijee Dangol (0372053) — telemetry pipeline / multi-node / scenarios
**Scope:** Python virtual edge device only. Cloud provisioning, dashboard, rule engine and benchmarking owned by the Cloud Track and deliberately left as an integration seam.

## 1. Intent and why

Task 3 requires a working end-to-end data path from a Python-based virtual edge device to a cloud platform, including the offline-resilience promised in Task 2. The network drops precisely during the flood event being monitored, so readings taken during an outage are the most valuable ones. A simulator that only publishes when the broker is reachable fails the requirement.

This repository implements only the edge half: physically coupled sensor simulation, local processing, SQLite store-and-forward, and a pluggable transport that can run standalone today and accept the Cloud Track's ThingsBoard credentials later without code changes.

Non-goals / anti-goals:

- No ThingsBoard device provisioning, dashboard JSON, alarm rules or rule chains (Cloud Track).
- No real hardware, no Node-RED, no Docker deployment.
- No mid-demo script editing: every scenario and outage action must be available from the running CLI.
- A demo that only works with a hard-coded high turbidity value is a failure.

## 2. Constraints

- Python 3.10+, standard library plus `paho-mqtt` only; no third-party simulation/DB frameworks.
- SQLite `sqlite3` with WAL; schema columns fixed by Task 2: `id, timestamp, payload, status, retry_count, created_at`.
- MQTT over TLS on port 8883, QoS 1, PUBACK before a row is marked synced.
- Timestamped payload form `{"ts": <epoch ms>, "values": {...}}`, including batch (array) form.
- ≥3 virtual nodes at distinct Nepal coordinates.
- Output must be ordinary editable source files for the submission ZIP.
- Project lives in `~/Documents/Github/post-flood-edge/`, initialised as a git repository with no remote configured.

## 3. Approaches considered

| Approach | Trade-off | Verdict |
| --- | --- | --- |
| Single monolithic simulator script | Fastest to write, but Saugat's and Bijee's responsibilities are tangled and hard to explain in the demo or manuscript. | Rejected |
| Small package with a `Publisher` interface and log-only default sink | Two people can own separate modules; edge demos work with zero cloud credentials; Cloud Track switches one config flag and supplies host/tokens. | **Selected** |
| Docker + Node-RED full stack | Owned by Cloud Track; adds setup friction to a 15-minute live demo. | Rejected |

## 4. Design

### 4.1 Layout

```
post-flood-edge/
├── .gitignore, .env.example, requirements.txt, README.md, INTEGRATION.md
├── edge/
│   ├── __init__.py, config.py, models.py, sensors.py, dsp.py, validity.py,
│   ├── hysteresis.py, storage.py, publisher.py, scenarios.py,
│   ├── runtime.py, cli.py
│   └── sql/schema.sql
├── scenarios/demo.json
├── tests/                      # pytest, no network required
├── tools/benchmark_batch_size.py
├── docs/DEMO_RUNBOOK.md
└── docs/superpowers/specs/2026-11-24-post-flood-edge-design.md
```

### 4.2 Node model (Bijee)

Three virtual ESP32-S3 nodes, one sampling thread each, at distinct Kathmandu-valley water-source coordinates (Sundarighat 27.7172/85.3240, Pulchowk 27.6790/85.3190, Suryabinayak 27.7500/85.3700). Per-node seed and event onset offset make cross-node comparison meaningful.

Every tick produces raw `ph`, `turbidity_ntu`, `tds_ppm`, `water_temp_c`, `flow_lpm` plus static `latitude`, `longitude`. Baseline ranges per the brief: pH 6.8–7.6, turbidity 2–8 NTU, TDS 120–250 ppm, temp 16–24 °C with a diurnal cycle, flow 8–18 L/min. Rounding: pH 2 dp, turbidity 1 dp, TDS 0 dp, temp 1 dp, flow 1 dp.

Dataset contract: `Reading(node_id, ts_ms, values)` where `values` is the flat dict of telemetry keys. `node_id` is also included as a `values` key in the published payload so the shared SQLite buffer can carry rows from several nodes without adding a column to the Task 2 schema.

### 4.3 Coupled intrusion model (Saugat)

Rainfall-triggered event with fast rise, peak and slow exponential washout:

```
u(t) = A · (1 − e^(−Δt/τ_rise)) · e^(−Δt/τ_wash)     Δt = t − t_onset ≥ 0
```

Physical constants `τ_rise = 2 min`, `τ_wash = 30 min`; the demo runs them 12× accelerated (`10 s` / `120 s` in `scenarios/demo.json`) so the full rise, peak, critical crossings and washout fit inside a 15-minute demonstration. `A` is per node: 500 / 825 / 1250, giving peaks of roughly 300 / 500 / 760 NTU. Per-node silt onsets are offset by 0 / 20 / 40 s after the silt phase begins. Derived from `u`:

- `turbidity_ntu = baseline + u`
- `tds_ppm = baseline + 0.45·u` (0.45 ppm per NTU: dissolved load scales with suspended silt load; a ~600 NTU event pushes TDS past the 500 ppm operational alarm)
- `ph = baseline − 0.90·(1 − e^(−u/150))`, floored at 6.2 (saturating/non-linear, no impossible negative pH)
- `flow_lpm`: brief +2–4 L/min pressure transient at the breach, then normal variation; forced to 0.0 during Supply Interruption

All five parameters therefore move together; no independent random spikes during an event.

### 4.4 Signal processing (Saugat)

- **Validity gates** (Task 2 §8.2.3), applied to raw values before Hampel: ADC saturation (physical range checks), open/short detection (impossible endpoint values), stuck-value variance (no variation across the last 9 samples while an event is active) and cross-parameter sanity. Failed channels are replaced by the last valid value, flagged in `validity_flags`, logged with a `VALIDITY:` marker, and escalated to `Fault` after 3 consecutive failures. A `f`/`F` CLI key injects/clears a stuck-value fault for demo purposes.
- **Hampel filter**, rolling `W=9`, `k=3`, `σ = 1.4826·MAD`; replace outlier with window median. Applied to the raw series of all five numeric channels before temperature compensation. Must reject a single 500 NTU spike while preserving a genuine 10→60 NTU step. The generator injects rare, explicitly labelled sensor glitches (~0.5% of samples, ±200–500 NTU on turbidity) to exercise the filter; the physical intrusion model itself adds no independent random spikes.
- **Nernstian pH**: `s(T) = 2.303·R·(T+273.15)/F` mV/pH (59.16 mV/pH at 25 °C); `E(T) = E7 − s(T)·pH`; `pH_T = 7 − (E(T)−E7)/s(T)`; audit channel `ph_25 = 7 − (E(T)−E7)/s(25)`. Published `ph` is the temperature-compensated measurement; `ph_25` is retained for audit/WQI.
- **TDS compensation**: `EC25 = EC_T / (1 + 0.02·(T−25))`, `TDS25 = k_EC·EC25` with `k_EC = 0.5` ppm per µS/cm. The generator computes the 25 °C-equivalent ppm (baseline + `0.45·u`), converts it to the ambient `EC_T` using the same α, and the DSP compensates it back; this keeps the coupling coefficient and the 500 ppm alarm boundary unambiguous. Published `tds_ppm` is the compensated value.
- **Brown WQI**, no-chlorine fallback normalised weights from Task 2 Table 12: pH 0.3692, turbidity 0.6277, TDS 0.0031; `q_i = 100·(V_i−V_0)/(S_i−V_0)` with `S = {8.5, 5, 1000}`, `V_0 = {7, 0, 0}`; `WQI = Σ q_i·w_i`, clamped `q_i ≥ 0`; bands 0–25 Excellent, 26–50 Good, 51–75 Poor, 76–100 Very poor, >100 Unsuitable. Published as `wqi`, `wqi_band`. Safety remains with per-parameter hysteresis, not WQI.

### 4.5 Hysteresis / debounce (Saugat)

Per-parameter state with consecutive-sample debounce; node severity is the max over active rules. Deadbands from the Task 3 brief:

| Rule | Raise | Clear | Severity |
| --- | --- | --- | --- |
| High Turbidity | >50 NTU | <40 NTU | Warning |
| Gross Contamination | >200 NTU | <150 NTU | Critical |
| pH Out of Range | <6.5 or >8.5 | 6.7–8.3 | Critical |
| High TDS | >500 ppm | <450 ppm | Critical |
| Supply Interruption | `flow_lpm == 0` sustained | `flow_lpm > 0` | Major |
| Node Inactive | timeout exceeded | telemetry resumes | Critical |
| Rapid Turbidity Rise (derivative, Task 2 Eq. 6) | `Δturbidity > 30 NTU` over the lookback window | rate falls below 30 NTU | Warning, escalates to Critical when an absolute rung is crossed |
| Severe pH Persistence (Task 2 Table 13) | pH outside 6.0–9.0 for 3 samples | 6.2 ≤ pH ≤ 8.8 | Critical |
| Sensor Fault | 3 consecutive validity-gate failures | 3 consecutive valid samples | Fault |

Debounce counts (my call, following Task 2 Table 13 where it applies): High Turbidity 1/1, Gross Contamination 1/1, pH 3/3, High TDS 3/3, Severe pH Persistence 3/3, Node Inactive 1/1, Sensor Fault 3/3. Supply Interruption is time-based on wall-clock seconds (not tick count): `flow_zero_sustained_s` defaults to 30 s for the accelerated demo (6 ticks), production value 1800 s via env; clear is immediate on `flow_lpm > 0`.

The derivative rule uses a 50 s lookback in the demo (10 ticks, equivalent to Task 2's 10 min under the 12× acceleration; 600 s via env in production) and is evaluated on the Hampel-cleaned series, never the EWMA. Severity ordering is `Normal < Warning < Major < Critical < Fault`; `Fault` is held until three consecutive valid samples confirm recovery, matching the Task 2 state-machine hold.

Node Inactive fires when a node has generated no reading for `EDGE_NODE_INACTIVE_S` (default 90 s = 18 demo ticks). The CLI `k`/`K` keys power a node off/on so this state is demonstrable; the Cloud Track keeps its own 180 min normal / 45 min burst profile timeout. Published severity keys: `severity`, `active_alarms` (comma-separated type names), `node_inactive`.

### 4.6 Storage and store-and-forward (Saugat)

`edge/sql/schema.sql`:

```sql
CREATE TABLE IF NOT EXISTS telemetry_buffer (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   INTEGER NOT NULL,                  -- original acquisition, epoch ms
    payload     TEXT    NOT NULL,                  -- {"ts": ..., "values": {...}}
    status      TEXT    NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','inflight','synced')),
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_buffer_status_ts ON telemetry_buffer(status, timestamp, id);
```

Connection pragmas: `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`. All access through one connection guarded by a re-entrant lock.

Lifecycle: `enqueue()` inserts `pending` **before any publish attempt**. `flush(batch_size)` selects oldest `pending` rows ordered by `(timestamp, id)`, marks `inflight`, partitions them by `values["node_id"]`, and hands each node's array `{"ts": ..., "values": {...}}` to that node's publisher. Rows are marked `synced` only on confirmed delivery; on failure that node's rows return to `pending` with `retry_count += 1`, processing of other nodes continues, and the failed node's batch loop stops and logs. Live and replay take the same path, so timestamp preservation is identical.

Capacity: `EDGE_BUFFER_MAX_ROWS` default 6048 (3 nodes × 2016 = 7 days at 5 min in the Task 2 design). On overflow, evict oldest `pending` rows first with an explicit `EVICT` warning; if none are pending, drop the new reading with a distinct log line. Justification: bounded disk and bounded backfill time; the capacity is sized to the full promised outage window.

Structured log markers: `STATE`, `BUFFER`, `OUTAGE`, `PUBLISH`, `EVICT`, `BACKOFF`, `AUTH_FAILURE`, `NETWORK_FAILURE`, `REPLAY`, `VALIDITY`, `FAULT`.

### 4.7 Transport seam (Saugat)

`Publisher` protocol: `connect()`, `disconnect()`, `publish_batch(rows) -> bool`, `is_connected`.

- `NullPublisher` (default): logs the batch and returns True; makes the whole edge demo runnable with no cloud. Also used by tests and the batch-size benchmark.
- `MqttPublisher` (opt-in `--mqtt`): one paho-mqtt client per node with its own access token; `tls_set` with system CAs or `TB_CA_CERT`; host `TB_HOST`, port 8883, username `TB_TOKEN_NODE_A` / `TB_TOKEN_NODE_B` / `TB_TOKEN_NODE_C`, empty password, topic `v1/devices/me/telemetry`; QoS 1 and `wait_for_publish()` for PUBACK; `on_connect` rc 5 logged as `AUTH_FAILURE` with retries suspended, other failures logged as `NETWORK_FAILURE` with exponential backoff min 1 s / max 60 s (bounded to avoid wasting cellular power on futile reconnects).

The runtime's outage toggle is independent of the publisher: while active it skips publish attempts and logs `OUTAGE: START`, leaving rows accumulating; on restore it logs `OUTAGE: END` and immediately drains batches. An `AUTH_FAILURE` never stops sampling or buffering; it suspends only that node's connection retries until the operator presses `a` (re-auth attempt) or the process restarts, so an auth problem is visible as buffer growth rather than data loss.

Node IDs, ThingsBoard device names and token variables are identical up to case: `node-a`/`node-b`/`node-c` ↔ `TB_TOKEN_NODE_A`/`TB_TOKEN_NODE_B`/`TB_TOKEN_NODE_C`. `buffer_depth` is published per node as telemetry (pending + inflight rows for that node), with the fleet total written to the log; this feeds the Cloud Track's Node Status widget without any code changes on their side.

### 4.8 Orchestration and scenarios (Bijee)

`SamplingWorker` thread per node; `BufferStore` shared with a lock; main loop flushes on an interval; separate stdin-reader thread feeds commands without blocking sampling. Shutdown via `threading.Event` on SIGINT/`q`.

`scenarios/demo.json` defines timed phases `baseline` (60 s) → `silt_intrusion` (240 s) → `supply_interruption` (120 s) → `recovery` (180 s), looping. With the accelerated event constants above, the 50 NTU warning occurs ~5 s after each node's onset, the 200 NTU critical ~15 s after onset, and the 500 ppm TDS critical on node C ~25 s after onset — all inside the silt phase. Interactive keys: `b`, `s`, `i`, `r` (force phase), `o` (toggle outage), `k`/`K` (power a node off/on for Node Inactive), `f`/`F` (inject/clear a stuck-value sensor fault), `a` (retry after auth failure), `p` (pause), `q` (quit), `h` (help). Any phase change writes a `STATE:` log line. The scripted timeline is the only way events are triggered — no literal hard-coded high values mid-run.

`--tick-seconds` (default 5) accelerates sampling for the demo while timestamps remain real epoch milliseconds, so replay genuinely backfills wall-clock time.

### 4.9 CLI, docs and tests

```
python -m edge.cli --scenario scenarios/demo.json [--mqtt|--dry-run] \
    [--tick-seconds 5] [--batch-size 50] [--log-level INFO]
```

- `README.md` — quickstart, module/ownership map, demo command.
- `INTEGRATION.md` — exact topic, payload keys, timestamp form, env vars/token names, device names, alarm deadbands for the Cloud Track, and what `buffer_depth`, `node_inactive`, `validity_flags`, `severity` (including `Fault`) and the two derived alarm names mean for their widgets. No ThingsBoard configuration is created here.
- `docs/DEMO_RUNBOOK.md` — 15-minute demonstration sequence mapped to the rubric steps and expected log lines.
- `tools/benchmark_batch_size.py` — measures rows/s for batch sizes 1/10/25/50/100/200 with `NullPublisher`, writes CSV for the manuscript's batch-size optimisation.
- `tests/` — pytest cases for Hampel spike-vs-step, Nernst/TDS compensation bounds, parameter coupling and pH floor, WQI worked example, validity gates and fault hold, derivative rule below the absolute threshold, severe-pH persistence, hysteresis flapping/clears, storage WAL/ordering/timestamp preservation/ack/retry/eviction, publisher protocol and backoff/auth classification, scenario transitions. No network required.

### 4.10 Repository hygiene

`git init` on `main` with an initial commit; no remote added. `.gitignore` excludes `.env`, `__pycache__/`, `.pytest_cache/`, `logs/`, `*.db`, `*.db-wal`, `*.db-shm`. `.env.example` documents all integration variables with blank/placeholder values; real `.env` is never committed.

## 5. Builder-check clarifications (my calls)

The following were ambiguous in the first draft and are settled here so no guess can change the build: per-node routing via `node_id` inside `values` with per-node ack handling; concrete demo timeline, amplitudes and 12× event acceleration; 90 s local Node Inactive with `k`/`K` demo controls; generator produces 25 °C-equivalent TDS and simulates the ambient EC measurement; debounce counts 1/1, 1/1, 3/3, 3/3, 1/1 with flow time measured in wall-clock seconds; node IDs ↔ device names ↔ token variables fixed; auth failure suspends only that node's retries while buffering continues; Hampel runs on raw channels with rare injected sensor glitches distinct from the physical event.

Added at the requester's direction: Task 2's derivative rule, validity gates, `Fault` severity and severe-pH persistence are brought into the edge scope (all Saugat's ownership). Chlorine co-breach, microbiological hold and the server-side WQI rule chain remain Cloud Track work; 7-sample burst acquisition remains out of scope because there is no physical ADC.

## 6. What is left to the builder

All implementation details not fixed above (concrete function signatures, exact log formatting, module-internal helpers, test fixtures) are the builder's call. The only external dependency is `paho-mqtt`, pinned in `requirements.txt` alongside `pytest` for development.
