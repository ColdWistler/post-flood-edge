# Cloud Track integration contract

This document is the handoff from the Python edge simulator to the Cloud Track. It
states what this repository actually emits and consumes. **No ThingsBoard
configuration is created here**: device provisioning, dashboards, alarm rules, rule
chains and Node-RED flows are entirely Cloud Track work. The edge code exposes a
transport seam and a documented payload so that work can proceed independently.

## Transport

| Property | Value |
| --- | --- |
| Protocol | MQTT over TLS (`paho-mqtt`, callback API v1) |
| Broker | `TB_HOST` (from `.env` or environment; no default) |
| Port | `8883` |
| Topic | `v1/devices/me/telemetry` |
| QoS | `1` |
| Authentication | Each node has its own paho client; access token is the MQTT username, password is `None`/empty |
| Certificate | `tls_set(ca_certs=TB_CA_CERT or None)`; blank `TB_CA_CERT` uses system CAs |
| Delivery confirmation | `wait_for_publish(timeout=10)` followed by `is_published()` (PUBACK) before rows are marked `synced` |
| Opt-in | `python -m edge.cli --mqtt`; default `--dry-run` emits no network traffic |

Failures are classified as `AUTH_FAILURE` (rc 5 / reason code 135, retries suspended
for that node until `a` or restart) or `NETWORK_FAILURE` with exponential backoff
clamped to 1–60 s. Neither stops local sampling or buffering.

## Devices and credentials

| Node id / ThingsBoard device name | Token variable | Latitude | Longitude |
| --- | --- | --- | --- |
| `node-a` | `TB_TOKEN_NODE_A` | 27.7172 | 85.3240 |
| `node-b` | `TB_TOKEN_NODE_B` | 27.6790 | 85.3190 |
| `node-c` | `TB_TOKEN_NODE_C` | 27.7500 | 85.3700 |

The token variable is derived as `TB_TOKEN_` + upper-cased node id with `-` replaced
by `_`; the three names above are the supported nodes. An empty token will produce an
auth failure, not a silent fallback. Copy `.env.example` to `.env` and fill in the
values; `.env` is git-ignored and must never be committed.

| Environment variable | Config field | Default |
| --- | --- | --- |
| `TB_HOST` | `tb_host` | empty |
| `TB_CA_CERT` | `tb_ca_cert` | empty (system CAs) |
| `TB_TOKEN_NODE_A` / `TB_TOKEN_NODE_B` / `TB_TOKEN_NODE_C` | `tokens` per node | empty |
| `EDGE_TICK_SECONDS` | `tick_seconds` | `5.0` |
| `EDGE_BATCH_SIZE` | `batch_size` | `50` |
| `EDGE_BUFFER_MAX_ROWS` | `buffer_max_rows` | `6048` |
| `EDGE_FLOW_ZERO_SUSTAINED_S` | `flow_zero_sustained_s` | `30.0` |
| `EDGE_NODE_INACTIVE_S` | `node_inactive_s` | `90.0` |
| `EDGE_DERIVATIVE_WINDOW_S` | `derivative_window_s` | `50.0` |
| `EDGE_VALIDITY_FAULT_SAMPLES` | `validity_fault_samples` | `3` |
| `EDGE_DB_PATH` | `db_path` | `edge.db` |
| `EDGE_LOG_LEVEL` | `log_level` | `INFO` |

Precedence is `.env` file → real environment → CLI overrides (`--tick-seconds`,
`--batch-size`, `--db`, `--log-level`). The `--mqtt` / `--dry-run` choice is a CLI
flag and is not read from the environment.

## Payload

Every row has the timestamped ThingsBoard form, with `ts` in **Unix epoch
milliseconds** taken at acquisition. The timestamp is stored in SQLite and retained
unchanged through outage replay, so the cloud chart can backfill the real gap.

```json
{"ts": 1767225600123, "values": {"node_id": "node-a", "ph": 7.2, "...": "..."}}
```

A successful MQTT publish sends a JSON **array** of these objects (one array per node
per flush), e.g. `[{"ts": ..., "values": {...}}, {"ts": ..., "values": {...}}]`.
Rows are claimed oldest-first by `(timestamp, id)`, grouped by `values.node_id` and
routed to that node's publisher; each node is acked or retried independently.

### `values` keys

| Key | Type | Meaning |
| --- | --- | --- |
| `ph` | float | Temperature-compensated pH (the simulator rounds the raw value to 2 dp before DSP) |
| `turbidity_ntu` | float | Hampel-filtered turbidity in NTU (raw simulator value rounded to 1 dp) |
| `tds_ppm` | float | Temperature-compensated TDS at 25 °C in ppm (raw generator value rounded to 0 dp) |
| `water_temp_c` | float | Ambient water temperature in °C (raw value rounded to 1 dp) |
| `flow_lpm` | float | Flow in L/min (raw value rounded to 1 dp); `0.0` during supply interruption |
| `latitude` | float | Node site latitude |
| `longitude` | float | Node site longitude |
| `node_id` | string | `node-a`, `node-b` or `node-c`; routes the row to the matching device |
| `ph_25` | float | pH audit channel referenced to 25 °C; also used for WQI |
| `wqi` | float | Brown WQI from `ph_25`, turbidity and TDS (no-chlorine fallback weights) |
| `wqi_band` | string | `Excellent` (≤25), `Good` (≤50), `Poor` (≤75), `Very poor` (≤100) or `Unsuitable` (>100) |
| `severity` | string | Max severity across active alarms: `Normal`, `Warning`, `Major`, `Critical` or `Fault` |
| `active_alarms` | string | Comma-separated alarm names, e.g. `"Gross Contamination,High Turbidity"`; empty when none |
| `buffer_depth` | int | Pending + inflight rows for **this node**, including the row being enqueued (not the fleet total) |
| `node_inactive` | bool | True when `Node Inactive` is in `active_alarms` |
| `validity_flags` | string | Comma-separated raw-signal flags on this sample: `SATURATION`, `OPEN_SHORT`, `STUCK`, `SANITY`; empty when clean |
| `sensor_fault` | bool | True when this sample has any validity flags (the `Sensor Fault` alarm itself is debounced) |

Live samples additionally carry two diagnostic keys that the Cloud Track may ignore:
`event_u` (float, simulated intrusion strength in NTU) and `glitch` (bool, injected
spike marker). A powered-off node publishes a status row derived from its last
processed values, so those two keys may be absent there.

Severity ordering is `Normal < Warning < Major < Critical < Fault`; `severity` is the
maximum of the active alarm severities, while `active_alarms` is a sorted name list,
not a severity list. The fleet-wide queue total and per-node depths are logged as
`BUFFER: total=... per_node=...`; only the per-node number is published as telemetry.

## Alarm rules and deadbands

`edge/hysteresis.py` implements nine alarm rules: six direct threshold rules plus
three derived rules used in the manuscript/dashboard. Debounce is expressed as
consecutive raise / clear samples; deadband samples hold the current state without
advancing progress.

| Alarm name (`active_alarms`) | Raise | Clear | Debounce | Severity |
| --- | --- | --- | --- | --- |
| High Turbidity | `turbidity_ntu > 50` | `< 40` NTU | 1 / 1 | Warning |
| Gross Contamination | `turbidity_ntu > 200` | `< 150` NTU | 1 / 1 | Critical |
| pH Out of Range | `ph < 6.5` or `> 8.5` | `6.7 ≤ ph ≤ 8.3` | 3 / 3 | Critical |
| High TDS | `tds_ppm > 500` | `< 450` ppm | 3 / 3 | Critical |
| Supply Interruption | `flow_lpm == 0` sustained for `EDGE_FLOW_ZERO_SUSTAINED_S` (30 s demo) | flow resumes (`> 0`) | time-based | Major |
| Node Inactive | no generated reading for `EDGE_NODE_INACTIVE_S` (90 s demo) | telemetry resumes | time-based | Critical |
| **Rapid Turbidity Rise** (derived) | rise `> 30` NTU within `EDGE_DERIVATIVE_WINDOW_S` (50 s demo), on Hampel-cleaned values | in-window rise `< 30` NTU | 1 / 1 | Warning; an absolute rung raises the node severity |
| **Severe pH Persistence** (derived) | `ph < 6.0` or `> 9.0` | `6.2 ≤ ph ≤ 8.8` | 3 / 3 | Critical |
| **Sensor Fault** (derived) | 3 consecutive validity-gate failures (`EDGE_VALIDITY_FAULT_SAMPLES`) | 3 consecutive valid samples | 3 / 3 | **Fault** |

`Fault` is the highest severity value and is held until the clear debounce completes;
the corresponding alarm name in `active_alarms` is `Sensor Fault`, not `Fault`.
Validity failures log `VALIDITY:`, and fault transitions/injection log `FAULT:`.

## Edge behaviour the dashboard can rely on

- `scenarios/demo.json` runs `baseline` 60 s → `silt_intrusion` 240 s →
  `supply_interruption` 120 s → `recovery` 180 s, looping; the keys `b/s/i/r` force a
  phase at any time. Event rise/washout uses cumulative simulation time, so forcing a
  phase does not restart the physical event.
- `o` pauses all publish attempts while sampling continues; on restore the runtime
  logs `OUTAGE: END`, then `REPLAY: node=... rows=...` and drains buffered batches with
  their original timestamps.
- Buffer capacity is `EDGE_BUFFER_MAX_ROWS` (default 6048 rows). At capacity, oldest
  pending rows are evicted first with an `EVICT:` log; if none are pending, the new
  reading is dropped with a distinct log line.
- Structured log markers are `STATE`, `BUFFER`, `OUTAGE`, `PUBLISH`, `EVICT`,
  `BACKOFF`, `AUTH_FAILURE`, `NETWORK_FAILURE`, `REPLAY`, `VALIDITY` and `FAULT`.

Again: this repository only produces this contract and the telemetry that satisfies
it. ThingsBoard setup is out of scope here and remains the Cloud Track's
responsibility.
