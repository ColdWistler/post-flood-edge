# Cloud Track manuscript source (IEEE draft)

This file holds the Cloud Track sections of the group manuscript. Threshold,
payload and rule-chain statements are checked against `INTEGRATION.md`, the edge
source and the committed ThingsBoard artifacts. All quantitative values are
copied from the two saved benchmark reports; no simulated value is presented as
a measurement. Figures generated from saved logs are labelled as log-derived,
not as tenant screenshots.

## Evidence index

| Deliverable | Where it comes from |
| --- | --- |
| Device profile JSON | `cloud/artifacts/device_profile.json` (import and re-export from the instance) |
| Three devices + tokens | `cloud/artifacts/devices.json`, `cloud/provision.py` (fill `.env`, never commit tokens) |
| Dashboard JSON | `cloud/artifacts/dashboard_triage.json` (eight-widget triage, derived from the verified Cloud export); `cloud/artifacts/dashboard_thingsboard_cloud.json` (six-chart fallback) |
| Rule chain JSON | `cloud/artifacts/wqi_rule_chain.json` |
| Alarm rule spec | `cloud/artifacts/alarm_rules.json`, `cloud/artifacts/alarm_rules_import/` and `INTEGRATION.md` |
| Performance tables | `python -m cloud.benchmark --apply --plot` → `cloud/benchmark/metrics.csv` |
| Raw run reports | `cloud/benchmark/metrics_outage.json`, `cloud/benchmark/metrics_live.json` |
| Batch-size sweep | `python tools/benchmark_batch_size.py` → `tools/batch_results.csv` |
| Log-derived figures | `paper/figures/event_timeline.pdf`, `alarm_evidence.pdf`, `buffer_depth.pdf` |
| Console evidence | `evidence/edge_outage_final.log`, `evidence/edge_clean_final.log` |

## 6.3 Part A — cloud provisioning and dashboard UX

The edge publishes three timestamped MQTT/TLS streams to ThingsBoard [1] over QoS 1
(`v1/devices/me/telemetry`) using `paho-mqtt` [2]. Devices `node-a`, `node-b` and
`node-c` are registered at 27.7172/85.3240, 27.6790/85.3190 and 27.7500/85.3700
respectively; each has its own access token and therefore its own rule-engine
originator. The device-profile inactivity timeout is **180 min normal / 45 min
burst**, which is a server-side device-availability mechanism and is deliberately
distinct from the edge's **90 s** `Node Inactive` alarm (demo value, see
`INTEGRATION.md`). Both values must be reported separately.

The committed eight-widget triage dashboard is derived from the six-chart Cloud
export that was confirmed to render lines on the target instance. It retains raw
`NONE` aggregation, a 24-hour timewindow and visible sample points. The eight widgets
are pH and turbidity radial gauges, a digital TDS gauge, a multi-parameter time
series, a temperature/flow chart, an OpenStreetMap severity map, a native alarms
table and a node-status entity table. The charts and map markers bind directly to
the three verified `deviceId` entries; the gauges are bound to node-a, while the
alarms and fleet tables use an explicit list of the same identifiers.

On the target tenant the imported dashboard rendered all eight widget shells, but the
first map configuration drew no markers. The tenant's latest telemetry contained
`latitude` and `longitude` but not the edge-published `severity` key. The map was
therefore rebuilt as three direct-device `system.map` markers whose colour is taken
from `severity` when available and otherwise derived from `active_alarms`,
`turbidity_ntu`, `ph`, `tds_ppm` and `flow_lpm`. This preserves the severity bands
without making marker creation depend on a key the tenant does not store. `RSSI` is
**N/A** because the virtual nodes emit no radio telemetry. Fleet queue depth is a
log-only metric; the dashboard shows the per-node `buffer_depth` key instead.

The saved evidence set contains console logs and benchmark reports instead of
dashboard screenshots. Accordingly, the paper uses three log-derived figures:
`event_timeline.pdf` for the forced phase transition, `alarm_evidence.pdf` for
the edge fault events and alarm-delay summary, and `buffer_depth.pdf` for
outage and replay. Their captions state the log source explicitly so that they
are not mistaken for UI captures.

## 6.3 Part B — alarm rules and hysteresis anti-flapping

The assessment brief [3] requires six threshold alarms; the work division [4]
assigns their configuration to this track. The alarm contract is represented in two
deployment forms. `alarm_rules.json` is the human-readable nine-rule specification,
while `device_profile.json` retains the older v3.x `profileData.alarms` export for
compatibility. Current Cloud tenants may accept that profile but hide its deprecated
alarm list, leaving the **Alarm Rules** tab empty; therefore nine importable
`CalculatedField` documents of type `ALARM` are also supplied, one per rule, for the
tenant's **Alarm rules → Import** action. Together they define the six mandatory direct
rules plus three mirrors of edge-derived conditions. Table I lists the six mandatory
rules. All nine are declarative ThingsBoard rules; no custom alarm rule chain is used
for alarming.

**TABLE I — MANDATORY ALARM RULES AND HYSTERESIS DEADBANDS**

| Alarm | Severity | Raise condition | Clear condition | Debounce |
| --- | --- | --- | --- | --- |
| High Turbidity | Warning | `turbidity_ntu > 50` | `turbidity_ntu < 40` | 1 / 1 samples |
| Gross Contamination | Critical | `turbidity_ntu > 200` | `turbidity_ntu < 150` | 1 / 1 samples |
| pH Out of Range | Critical | `ph < 6.5` or `ph > 8.5` | `6.7 <= ph <= 8.3` | 3 / 3 samples |
| High TDS | Critical | `tds_ppm > 500` | `tds_ppm < 450` | 3 / 3 samples |
| Supply Interruption | Major | `flow_lpm == 0` for 30 s (demo) | `flow_lpm > 0` | time-based |
| Node Inactive | Critical | no reading for 90 s (demo) | telemetry resumes | time-based |

The three derived mirrors are `Rapid Turbidity Rise` (a rise greater than 30 NTU
within a 50 s window on Hampel-cleaned values), `Severe pH Persistence`
(`ph < 6.0` or `ph > 9.0` for three samples, clearing at `6.2–8.8`) and
`Sensor Fault` (three consecutive validity-gate failures, clearing after three
valid samples). They use native `active_alarms CONTAINS` filters. `Fault` is the
highest severity value, while the corresponding alarm name remains `Sensor
Fault`.

Anti-flapping is implemented with hysteresis deadbands and consecutive-sample
debounce. A sample inside a deadband holds the current alarm state and does not
advance the clear counter; debounce counts consecutive qualifying samples rather
than accumulated ones. Thus a turbidity value hovering around 50 NTU cannot
toggle High Turbidity on every tick, and a pH value returning from an out-of-range
excursion must remain inside `6.7–8.3` for three samples before the alarm clears.
The edge publishes `severity`, `active_alarms` and `node_inactive` on every sample,
so dashboard status follows edge state; the server-side copies exist as rubric
evidence with identical thresholds. If the two disagree during a demo, the edge
value is authoritative and the server rule is corrected.

The server-side WQI extension is a native rule chain with three nodes: a JS
filter that accepts telemetry containing `ph_25`, `turbidity_ntu` and `tds_ppm`, a
JS transform that computes the Task 2 Brown WQI using the no-chlorine fallback
weights `(0.3692, 0.6277, 0.0031)`, and a timeseries node that stores
`wqi_server`, `wqi_band_server` and `server_received_ms`. The standards are
`(8.5, 5.0, 1000.0)` with ideals `(7.0, 0.0, 0.0)`, and the quality bands are
`<=25 Excellent`, `<=50 Good`, `<=75 Poor`, `<=100 Very poor` and `>100
Unsuitable`. The chain writes derived keys instead of overwriting the
edge-published `wqi` and `wqi_band`; `server_received_ms` is the broker-side clock
used by the latency benchmark. The exported JSON is `root: false`, so it must be
imported and linked from the active root chain (or set as root) before latency
measurements are valid. On the target tenant the chain was verified after linking:
node-a Latest Telemetry contains `wqi_server`, `wqi_band_server` and
`server_received_ms` (`evidence/wqi_server_telemetry.png`). The live publisher
also had to split telemetry into `<=1800`-byte MQTT packets and use one unique
client id per node, because the tenant disconnects larger packets and rejects
session takeovers.

The anti-flapping behaviour is specified by the raise/clear pairs and debounce
counts in Table I, and is implemented as a three-state latch: breaches advance
the raise counter, clear-eligible samples advance the clear counter, and a
deadband sample holds the latch while resetting clear progress. No sample number
is invented here because a UI trace of one sequence was not retained.

## 7.1 — tool justification

ThingsBoard was selected for device management, MQTT/TLS ingestion, per-device
telemetry with client timestamps, built-in alarm state machines and dashboard
export. SQLite at the edge provides durable store-and-forward without a broker,
and PUBACK-confirmed acknowledgements give an exact local sync count. Python's
`paho-mqtt` keeps the transport swappable (`NullPublisher` for dry runs).
A raw broker plus a custom web dashboard offers transport control but moves
device identity, alarm latching, history retention and visualisation into
application code. Node-RED is convenient for correlating simultaneous breaches,
but it is not itself a device registry and does not export dashboards as stable
artifacts. ThingsBoard supplies those platform services directly and exposes
its configuration as JSON that can be committed as evidence. The cost is
sensitivity to tenant-version details, which Section 8.4 documents.

## 8.1 — baseline, intrusion and backfill

`event_timeline.pdf` shows cumulative confirmed rows and the forced
`baseline -> silt_intrusion` transition reconstructed from
`evidence/edge_clean_final.log`. `buffer_depth.pdf` shows the 32 s outage and
replay phase reconstructed from `evidence/edge_outage_final.log`. Because replay
uses original epoch-millisecond timestamps, the tenant chart retains a filled
chronological gap rather than a flat spike at recovery. Tenant UI screenshots
for these phases were not retained in this evidence set and are not fabricated.

## 8.2 — alerting evidence

`alarm_evidence.pdf` combines the edge `FAULT` events from the saved log with
the measured alarm-delay summary from `cloud/benchmark/metrics.csv`; the tenant
alarm-table screenshot was not retained. The rule chain is evaluated per node,
and the expected escalation under a forced `silt_intrusion` is: turbidity crosses
50 NTU to raise High Turbidity (Warning), then 200 NTU to raise Gross
Contamination (Critical); pH leaves `6.5–8.5` and, after three consecutive
samples, raises pH Out of Range (Critical); TDS above 500 ppm raises High TDS
(Critical) after the same three-sample debounce. A stuck-sensor injection raises
Sensor Fault (Fault), while a zero-flow interval raises Supply Interruption
(Major) after 30 s. Activation and clearance timestamps are read from the
ThingsBoard REST alarm history rather than from a screenshot. The activation
delay is the difference between the timestamp of the first payload that breaches
the threshold and the alarm creation time returned by that API.
From `cloud/benchmark/metrics.csv`, the measured mean alarm activation delay in the
no-outage live run was **1,329.2 ms over 18 alarm events** (median 1,288.5 ms,
P95 2,239.3 ms), exceeding the ten-event minimum. In the outage run the mean was
62,939.5 ms over 16 events because alarms raised while rows were buffered were
only created after reconnection.

## 8.3 — resilience and buffer

`buffer_depth.pdf` and `buffer_replay.pdf` are generated from the saved console
log, which shows `OUTAGE: START`,
growing `BUFFER: total=... per_node=...`, then `OUTAGE: END`, `REPLAY: node=...
rows=...` and replayed `PUBLISH` lines carrying the old epoch-ms timestamps. The
`REPLAY` marker is preferred over `PUBLISH` for throughput because `PUBLISH`
also includes live samples taken during the drain. The recovery interval runs
from connectivity restoration to the first `BUFFER: total=0` line. From
`cloud/benchmark/metrics.csv`: mean recovery time **61.94 s** for a replayed buffer
depth of **96 rows** (six 16-row claims); mean flush throughput **1.55 rows/s**;
message delivery ratio **99.86 %**; data loss **1 row (0.14 %)**. The outage lasted
32 s and the measurement window was 2026-11-25 09:38:40 to 11:33:18 UTC in
simulation time.

## 8.4 — performance measurement

Table II collects the six mandatory metrics. The latency figures require at
least 200 messages with `server_received_ms` available from the linked WQI rule
chain; the delivery ratio must be measured over a run that includes an outage;
the alarm-delay figure requires at least 10 alarm events. All six values were
copied from `cloud/benchmark/metrics.csv` after an `--apply --plot` run against
ThingsBoard with a tenant JWT supplied through the environment. Two runs are
reported. The outage run (09:38:40–11:33:18 UTC simulation time, 32 s outage)
provides delivery ratio, throughput, recovery time and data loss. Its latency
sample (n=709, mean 270,767 ms) includes time spent in the buffer and is a
resilience bound rather than a live transfer time. The no-outage run
(11:44:51–11:47:48 UTC, 530 generated rows, 528 confirmed) provides the
representative live latency and alarm delay. The two raw reports are retained as
`metrics_outage.json` and `metrics_live.json`; the consolidated table is
`metrics.csv`.

**TABLE II — MANDATORY QUANTITATIVE RESULTS**

| Metric | Reported value | Sample basis |
| --- | --- | --- |
| End-to-end latency (mean / median / P95 / max) | 1,457.2 / 1,444 / 2,364.6 / 2,939 ms | 528 messages, no-outage run |
| Message delivery ratio | 99.86 % | outage run, 707/708 rows |
| Alarm activation delay (mean) | 1,329.2 ms | 18 alarm events, no-outage run |
| Buffer flush throughput | 1.55 rows/s | 96 REPLAY rows after reconnection |
| Recovery time | 61.94 s | stated buffer depth: 96 rows |
| Data loss | 1 (0.14 %) | outage run, full sync against ThingsBoard |

The host-only batch-size sweep times the complete enqueue/claim/publish/ack path
for 500 rows per size. The measured results in `tools/batch_results.csv` are
reproduced in Table III.

**TABLE III — HOST-ONLY BATCH-SIZE SWEEP**

| Batch size (rows) | Throughput (rows/s) | Elapsed (s) |
| ---: | ---: | ---: |
| 1 | 12992.94 | 0.038482 |
| 10 | 22203.64 | 0.022519 |
| 25 | 23001.16 | 0.021738 |
| 50 | 23543.50 | 0.021237 |
| 100 | 24330.85 | 0.020550 |
| 200 | 23668.94 | 0.021125 |

The highest host-only throughput was 24330.85 rows/s at a batch size of 100 rows,
so 100 is the starting production value. This is a local storage-path result, not
a broker measurement; the live publisher is constrained by the 1800-byte MQTT
chunk ceiling and PUBACK latency. The generated `latency_scatter.pdf` and
`batch_sweep.pdf` are included in the IEEE source with captions that name their
data sources.

## 9 — conclusion

This work implemented an end-to-end post-flood water-quality monitoring chain
from virtual Python sensors through SQLite store-and-forward and MQTT/TLS to
ThingsBoard. The edge preserves acquisition timestamps across outages, and the
cloud configuration exports a device profile, three devices, a dashboard and a
server-side WQI rule chain. Nine declarative alarm rules combine exact
thresholds with deadbands and consecutive-sample debounce, so threshold
excursions are reported without rapid alarm flapping. The benchmark tool measures
the six metrics required by the assessment, including latency from
`server_received_ms`, delivery ratio from PUBACK-confirmed rows, and buffer
throughput from the exact `REPLAY` row counts. The completed demonstration
produced a 1,329.2 ms mean alarm activation delay over 18 live events, a 99.86 %
delivery ratio across the outage run, 1.55 rows/s flush throughput and 1 row
(0.14 %) data loss. Together,
the implementation satisfies the sensing, resilience, visualisation and
quantitative-evaluation objectives of the task.

## 10 — future work and references

Several extensions remain for a production deployment. Critical alarms should
trigger email or webhook notification so an operator is alerted without watching
the dashboard. A Node-RED flow could correlate simultaneous breaches across
`node-a`, `node-b` and `node-c` to localise the contaminated segment of the
distribution network. The demonstration constants should be replaced by the
production time constants (1800 s for sustained zero flow and 600 s for the
derivative window) rather than the 12× accelerated demo values. A real sensing
node would also provide radio telemetry, allowing the requested `RSSI` widget to
be populated instead of being marked N/A, and the surrogate-indicator
calibration would need field validation before the WQI is used operationally.

## References

[1] ThingsBoard, “ThingsBoard documentation,” 2026. [Online]. Available:
https://thingsboard.io/docs/. Accessed: Nov. 25, 2026.

[2] Eclipse Foundation, “Paho MQTT Python client,” 2026. [Online]. Available:
https://www.eclipse.org/paho/index.php?page=clients/python/index.php. Accessed:
Nov. 25, 2026.

[3] ITS67404 Internet of Things, “Assessment Task 3: Group Project,” 2026.

[4] ITS67404 Internet of Things, “Assessment Task 3 Work Division,” 2026.

[5] ITS67404 Internet of Things, “Assessment Task 2: Group Project” (Brown WQI
no-chlorine fallback weights), 2026.

The standalone IEEE source in `paper/main.tex` carries numeric citations [1]–[5]
and compiles to `paper/main.pdf`. Author and group identifiers remain
placeholders by instructor submission convention; no quantitative placeholder
remains in the PDF.
