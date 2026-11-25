# Manuscript working draft (fill-in, not final prose)

This file collects the Cloud Track deliverables listed in `cloud/README.md`.
Threshold and payload statements below are verified against `INTEGRATION.md`
and the edge source; every bracketed value must be replaced with a measurement
from a real ThingsBoard run before submission. Do not present simulated or
placeholder numbers as results.

## Evidence index

| Deliverable | Where it comes from |
| --- | --- |
| Device profile JSON | `cloud/artifacts/device_profile.json` (import and re-export from the instance) |
| Three devices + tokens | `cloud/artifacts/devices.json`, `cloud/provision.py` (fill `.env`, never commit tokens) |
| Dashboard JSON | `cloud/artifacts/dashboard.json` (import, then re-export after visual tuning) |
| Rule chain JSON | `cloud/artifacts/wqi_rule_chain.json` |
| Alarm rule spec | `cloud/artifacts/alarm_rules.json` and `INTEGRATION.md` |
| Performance tables | `python -m cloud.benchmark --apply --plot` → `cloud/benchmark/metrics.csv` |
| Batch-size sweep | `python tools/benchmark_batch_size.py` → `tools/batch_results.csv`, then re-measure with a broker |
| Demo screenshots | `docs/DEMO_RUNBOOK.md` timeline, phases forced with `s`, `i`, `r`, `o`, `f`, `k` |
| Console evidence | full `logs/edge.log` from one run containing outage and recovery |

## 6.3 Part A — cloud provisioning and dashboard UX

The edge publishes three timestamped MQTT/TLS streams to ThingsBoard over QoS 1
(`v1/devices/me/telemetry`). Devices `node-a`, `node-b` and `node-c` are created
at 27.7172/85.3240, 27.6790/85.3190 and 27.7500/85.3700 respectively; each has
its own access token and therefore its own rule-engine originator. The device
profile sets an inactivity timeout of **180 min normal / 45 min burst** (work
division), which is a *device-profile* mechanism and is deliberately distinct
from the edge's **90 s** `Node Inactive` alarm (demo value, see `INTEGRATION.md`).
Document both numbers; do not describe them as one setting.

The dashboard is organised as a five-second triage view: the node map and alarm
table answer "which source is unsafe?" first, gauges give the offending
parameter second, and time-series widgets provide context and the outage
backfill. pH is banded at 6.5/8.5, turbidity at 5/50/200 NTU, TDS at 500/1000 ppm.
`RSSI` is **N/A**: the virtual nodes emit no radio telemetry. Fleet queue depth
is a log-only metric; the dashboard shows the per-node `buffer_depth` key instead.

> Insert annotated screenshots: baseline, intrusion, and outage backfill.
> Caption each with the simulator phase, wall-clock time, and the telemetry
> values visible in the gauges.

## 6.3 Part B — alarm rules and hysteresis anti-flapping

All nine alarms are configured in the native ThingsBoard Device Profile → Alarm
Rules tab: six mirror the direct edge thresholds (Warning/Critical/Major severities)
and three (`Rapid Turbidity Rise`, `Severe pH Persistence`, `Sensor Fault`) mirror
the edge-computed names through `active_alarms CONTAINS` filters. No custom alarm
rule chain is used. The edge-published `severity` and `active_alarms` remain
authoritative on the dashboard; the server-side copies use the same deadbands,
so a value hovering at a threshold cannot flap between active and cleared.
Debounce is expressed as consecutive raise/clear samples: 1/1 for turbidity
rules, 3/3 for pH, TDS and severe-pH rules, and 3/3 for sensor faults. Deadband
samples hold the current alarm state without advancing a clear counter. The
derivative rule compares Hampel-cleaned turbidity across a 50 s window and
raises when the in-window rise exceeds 30 NTU, below the absolute 50 NTU
threshold. Supply interruption is time-based (30 s of zero flow) and node
inactivity is time-based (90 s at demo settings). Fault is the highest severity;
the alarm name remains `Sensor Fault`.

The edge publishes `severity`, `active_alarms` and `node_inactive` per sample, so
the dashboard marker colour follows edge state. Server-side alarms are retained
as rubric evidence with identical thresholds; if the two disagree, the edge
value wins and the server rule is corrected.

> Insert a short table or figure showing one raise → deadband → clear sequence
> with sample numbers, to demonstrate anti-flapping.

## 7.1 — tool justification

ThingsBoard was selected for device management, MQTT/TLS ingestion, per-device
telemetry with client timestamps, built-in alarm state machines and dashboard
export. SQLite at the edge provides durable store-and-forward without a broker,
and PUBACK-confirmed acknowledgements give an exact local sync count. Python's
`paho-mqtt` keeps the transport swappable (`NullPublisher` for dry runs).
[Add one paragraph comparing the rejected alternatives — e.g. raw MQTT broker
plus custom web dashboard — using the assignment's own evaluation criteria.]

## 8.1 — baseline, intrusion and backfill

[Baseline screenshot reference], [intrusion screenshot reference] and
[backfill screenshot reference] should show the same three gauges before,
during and after a forced `silt_intrusion`, with the time-series chart retaining
the original acquisition timestamps after an `o`/`o` outage cycle. The backfill
appears as a filled gap in chronological order, not as a flat spike at recovery.

## 8.2 — alerting

[Alarm table screenshot reference]. Describe the escalation observed in the
demo: turbidity crosses 50 NTU (Warning) and 200 NTU (Critical), pH exits
6.5–8.5 (Critical after three samples), TDS exceeds 500 ppm (Critical after
three samples), and an injected stuck sensor raises `Sensor Fault` (Fault).
Report mean alarm activation delay and sample count from
`cloud/benchmark/metrics.csv`: [mean] ms over [n] events.

## 8.3 — resilience and buffer

[Buffer/chart screenshot reference]. The log shows `OUTAGE: START`, growing
`BUFFER: total=... per_node=...`, then `OUTAGE: END`, `REPLAY: node=... rows=...`
and replayed `PUBLISH` lines carrying the old epoch-ms timestamps. From the
benchmark report: mean recovery time [value] s for a buffer depth of [rows];
mean flush throughput [value] rows/s; message delivery ratio [value] %;
data loss [rows] ([pct] %). State the outage duration and window explicitly.

## 8.4 — performance

Copy the six-metric table from `cloud/benchmark/metrics.csv`. State that the WQI
rule chain was root (or linked from root) so `server_received_ms` existed for the
latency figures. Batch-size results start from `tools/batch_results.csv` (1, 10,
25, 50, 100, 200 rows; 500 rows per size, host-only). The selected production
batch size is [value], chosen because [measured reason from the real-broker
re-run]. Include `latency_scatter.svg` or a buffer-depth plot here.

## 9 — conclusion

[Summarise the demonstrated sensing, DSP, hysteresis, store-and-forward and
ThingsBoard integration against the assignment objectives. Use measured numbers
only.]

## 10 — future work and references

[Email/webhook notification on Critical alarms; Node-RED cross-node correlation
to localise a breach; production time constants (1800 s flow, 600 s derivative)
instead of the 12× demo constants; radio telemetry for a real RSSI widget.]

References: format the Assessment Task 3 brief, the group work-division PDF, the
ThingsBoard documentation, paho-mqtt documentation, and the Brown WQI source in
the assignment's required IEEE style. [Add complete citations.]
