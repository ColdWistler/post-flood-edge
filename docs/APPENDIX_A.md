# Appendix A — individual task allocation

**Assessment:** ITS67404 Internet of Things, Assessment Task 3 — Post-Flood Water
Contamination Monitoring System.
**Group number:** [insert group number]
**Submission package:** `GroupNo_Assessment-03_Implementation.zip`

## A.1 Task allocation matrix

| S/N | Student name and ID | Track and primary ownership | Python script / resilience | SQLite | ThingsBoard | IEEE paper | Live demo |
| ---: | --- | --- | :---: | :---: | :---: | :---: | :---: |
| 1 | Saugat Chaudhary Tharu — 0371451 (Leader) | Edge Track: edge algorithms, SQLite store-and-forward and MQTT/TLS | ✓ | ✓ | — | ✓ | ✓ |
| 2 | Bijee Dangol — 0372053 | Edge Track: end-to-end telemetry data flow, multi-node pipeline and scenarios | ✓ | ✓ | — | ✓ | ✓ |
| 3 | Anushree Pradhan — 0372148 | Cloud Track: ThingsBoard provisioning, operational dashboard and UX | — | — | ✓ | ✓ | ✓ |
| 4 | Sambid Shrestha — 0371697 | Cloud Track: hysteresis rule engine, WQI extension and benchmarking | ✓ | — | ✓ | ✓ | ✓ |

## A.2 Individual responsibilities

### Saugat Chaudhary Tharu (0371451)

Implemented the coupled silt/sewage intrusion models, Hampel filter, Nernstian
pH and TDS temperature compensation, Brown WQI sub-indices and the edge
hysteresis/debounce state machine. Built the SQLite `telemetry_buffer` layer with
WAL, PUBACK-confirmed sync, chronological replay with preserved epoch-ms
timestamps, oldest-first eviction logging, and MQTT/TLS transport with reconnect
and outage controls. Manuscript: Sections 1, 2, 6.1, 6.2 and 7.2. Demo: outage
trigger, queue accumulation and historical backfill.

### Bijee Dangol (0372053)

Architected the acquisition-to-ingestion pipeline for three virtual ESP32-S3
water-source nodes, including sensor streams, static Nepal site coordinates and
per-node `buffer_depth`. Managed concurrent sampling, JSON payload serialization
for live and replay traffic, and the repeatable Baseline → Silt Intrusion →
Supply Interruption → Recovery scenario controller. Manuscript: Sections 3, 4, 5
and 8.5. Demo: multi-node launch, payload walkthrough and scenario triggering.

### Anushree Pradhan (0372148)

Provisioned the ThingsBoard device profile, three devices and access tokens with
180 min normal / 45 min burst inactivity settings. Designed the operational
dashboard: pH and turbidity gauges, digital TDS gauge, multi-parameter
time-series, temperature/flow chart, severity-coloured OpenStreetMap widget,
alarms table and node-status fleet table. Exported the device-profile and
dashboard JSON for the submission archive. Manuscript: Sections 6.3 Part A, 7.1
and 8.1. Demo: live dashboard, gauge response, GIS severity colour and backfill.

### Sambid Shrestha (0371697)

Specified the six mandatory hysteresis alarm rules with exact deadbands and three
derived alarm mirrors, and shipped nine importable per-rule `ALARM` documents for
current Cloud tenants where the legacy device-profile Alarm Rules tab is hidden.
Built the server-side Brown WQI rule chain using the Task 2 no-chlorine fallback
weights and exported its JSON. Developed the Python benchmark for the six
mandatory metrics and the empirical batch-size sweep. Manuscript: Sections 6.3
Part B, 8.2–8.4, 9 and 10. Demo: live alarm escalation, hysteresis clearing and
quantitative benchmark results.

## A.3 Completion note

This matrix is reproduced from the group work-division PDF. The group number is
retained as a placeholder until the group supplies its submission identifier. The
two saved evidence runs are dated 2026-11-25, with their wall-clock timestamps
recorded in the IEEE manuscript's evidence-run table; no measurement placeholder
remains in the submitted artifacts.
