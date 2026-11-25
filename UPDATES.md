# Updates / handoff — ThingsBoard Cloud Track

**Date:** 2026-11-25  
**Repo:** `https://github.com/Raspberry025/post-flood-edge.git`  
**Commit covered:** `5da96aa Added Thingsboard connection` (previous baseline: `8dc0170`)

> **For the second Big Pickle agent picking this up:** this is a continuation handoff.
> Read this file first, then `INTEGRATION.md` and `cloud/README.md`. Do **not**
> re-create the artifacts from scratch and do **not** ask for credentials unless a new
> live publish/import is actually required. Device access tokens and JWTs are
> intentionally **not** stored in this repository.

---

## 1. What changed in this update

The edge runtime behaviour was not changed. The work completed the **Cloud Track** by
adding importable ThingsBoard artifacts, provisioning/benchmark tooling, docs and tests,
then doing live verification against ThingsBoard Cloud.

| Area | Files | What was updated |
| --- | --- | --- |
| Contract / docs | `INTEGRATION.md`, `README.md`, `cloud/README.md` | Linked the edge payload contract to the Cloud artifacts; added native-Tab workflow, version caveats and checklist hints. |
| Manuscript | `cloud/MANUSCRIPT.md` | Section-by-section draft with evidence index and explicit placeholders for measured values/screenshots. |
| Alarm spec | `cloud/artifacts/alarm_rules.json` | Declarative nine-rule specification (thresholds, deadbands, debounce). This is **documentation**, not a ThingsBoard import file. |
| Device profile | `cloud/artifacts/device_profile.json` | Importable v3.x `DeviceProfile` with **all nine** alarm rules under `profileData.alarms` (six direct thresholds + three `active_alarms CONTAINS` mirrors). |
| Devices | `cloud/artifacts/devices.json` | `node-a/b/c` inventory with `${TB_TOKEN_NODE_*}` placeholders; `type` corrected to `post-flood-water-quality` to match the profile name. Template for `cloud.provision`, not a UI import file. |
| WQI evidence | `cloud/artifacts/wqi_rule_chain.json`, `cloud/artifacts/wqi_server_script.js` | Native rule-chain template: JS filter → transform → save timeseries. Writes `wqi_server`, `wqi_band_server` and `server_received_ms`; edge `wqi`/`wqi_band` remain authoritative. |
| Dashboard | `cloud/artifacts/dashboard_thingsboard_cloud.json` | Final Cloud-importable dashboard generated from a real export from the user's tenant: six native line charts, three device datasources per widget, raw `NONE` aggregation, 24-hour widget timewindow, visible points. |
| Provisioning | `cloud/provision.py` | REST provisioning; `--dry-run` default, `--apply` credential-gated; fills tokens from env, creates profile/devices/dashboard/rule chain, sets device credentials and server-scope `inactivityTimeout`; two-step rule-chain save; opt-in `--set-root`; `--inactivity-ms`. |
| Benchmarking | `cloud/benchmark.py`, `cloud/benchmark/README.md`, `cloud/benchmark/.gitignore` | Six Task 3 metrics from `edge.db`, logs and optional ThingsBoard REST; JSON/CSV/SVG outputs; both `Authorization` and `X-Authorization` headers for Cloud compatibility. |
| Tests | `tests/test_cloud_benchmark.py`, `tests/test_cloud_provision.py`, `tests/test_publisher.py` | Added cloud helper tests; fixed brittle paho callback-version assertion. Full suite: **124 passed**. |

Debug dashboards (`dashboard.json`, `dashboard_minimal.json`,
`dashboard_diagnostic_node_b.json`) were used during live troubleshooting but are not
part of the final committed set. The committed dashboard is
`dashboard_thingsboard_cloud.json`.

### Where each engine lives (added vs pre-existing)

The edge runtime was **not** changed by this update. Some engines were already present
and were only exercised during the live runs; the Cloud-side equivalents were added here.

| Engine / tool | Location | Provenance |
| --- | --- | --- |
| Hysteresis rule engine | `edge/hysteresis.py` (`HysteresisEngine.evaluate`), wired into `edge/runtime.py` | **Pre-existing** (`a34cc13`, debounce fix in `d2c1a98`). Not added by this update. |
| Edge WQI engine | `edge/dsp.py` (`brown_wqi`, `process_reading`) | **Pre-existing** (`0e1712d`). Live payloads contained `wqi` / `wqi_band`. |
| Server-side WQI engine | `cloud/artifacts/wqi_rule_chain.json`, `cloud/artifacts/wqi_server_script.js` | **Added** in `5da96aa`. Writes `wqi_server`, `wqi_band_server`, `server_received_ms`; still needs import/link from root in the tenant. |
| Server-side alarm / debounce rules | `cloud/artifacts/device_profile.json`, `cloud/artifacts/alarm_rules.json` | **Added** in `5da96aa`. Nine native Alarm Rules with deadbands and `REPEATING` / `DURATION` debounce specs. |
| Batch-size benchmark | `tools/benchmark_batch_size.py`, `tools/batch_results.csv` | **Pre-existing** (`5193603`, `6704b09`). |
| Cloud six-metric benchmark | `cloud/benchmark.py`, `cloud/benchmark/README.md`, `cloud/benchmark/.gitignore` | **Added** in `5da96aa`. Computes throughput, recovery time, latency, delivery ratio, alarm delay and data loss. |

---

## 2. Live ThingsBoard Cloud status — what was actually done

These actions happened in the user's tenant, not in CI:

1. **Devices** were created by the user. Device IDs used in the dashboard are:
   - node-a: `81e5b1f0-e883-11f1-ab4e-dfd4fe9de952`
   - node-b: `01f214e0-e886-11f1-b8ac-4bd49425d19e`
   - node-c: `0bb52a80-e886-11f1-86e5-9755011de358`

   Access tokens were supplied by the user and used through environment variables
   only. They are deliberately omitted here.

2. **Telemetry publishing was verified**:
   - Single-node MQTT/TLS run: 14 batches, all PUBACK-acknowledged.
   - Three-node MQTT/TLS run: 73 batches, **73/73 PUBACK-acknowledged**, zero buffered rows left.
   - Device HTTP telemetry API (`POST /api/v1/<token>/telemetry`) returned **HTTP 200** for all three nodes.
   - A replay of 29 buffered points per node was accepted in 5-point chunks (larger chunks returned HTTP 500 on this Cloud instance).

3. **Dashboard import troubleshooting**:
   - The original alias-based v3 dashboard was silently rejected by Cloud UI import.
   - `dashboard_minimal.json` imported successfully, proving permissions/import worked.
   - A real widget export from the user's tenant was used as the template.
   - A one-widget diagnostic (`node-b` / `flow_lpm`, raw aggregation) displayed a line around **10:04 local** — proving telemetry reached the correct device.
   - Root cause of the empty charts: `AVG` aggregation plus sparse points. Final dashboard uses `NONE`, a 24-hour widget timewindow and `showPoints: true`.
   - **Pending confirmation:** the user has not yet confirmed that the final rebuilt `dashboard_thingsboard_cloud.json` was re-imported and is showing lines on all six widgets.

4. **Not yet confirmed applied in the tenant**: `device_profile.json` (nine Alarm Rules), `wqi_rule_chain.json` (and making it root/linked from root), and the server-scope `inactivityTimeout` attribute. The user was given UI steps for these.

---

## 3. Contract reminders (do not drift from these)

- MQTT over TLS, port **8883**, topic `v1/devices/me/telemetry`, **QoS 1**; rows are marked synced only after PUBACK.
- Payload: `{"ts": <epoch ms>, "values": {...}}`; batches are JSON arrays. Original timestamps survive outage replay.
- Nodes/coordinates: node-a 27.7172/85.3240, node-b 27.6790/85.3190, node-c 27.7500/85.3700.
- WQI constants: standards `(8.5, 5.0, 1000.0)`, ideals `(7.0, 0.0, 0.0)`, weights `(0.3692, 0.6277, 0.0031)`; bands `≤25 Excellent`, `≤50 Good`, `≤75 Poor`, `≤100 Very poor`, `>100 Unsuitable`.
- Alarm thresholds/deadbands: High Turbidity `>50/<40`; Gross `>200/<150`; pH `<6.5 or >8.5`, clear `6.7–8.3`; TDS `>500/<450`; flow zero 30 s demo; Node Inactive 90 s demo; severe pH `<6.0 or >9.0`, clear `6.2–8.8`; Sensor Fault 3/3; rapid rise `>30 NTU` in 50 s.
- `RSSI` is **N/A** (virtual nodes publish no radio telemetry). Do not invent values.
- In ThingsBoard CE 3.x / Cloud, `inactivityTimeout` is a **server-scope device attribute in ms** (180 min = `10800000`, 45 min burst = `2700000`), not a profile field. The edge's 90 s `Node Inactive` alarm is a separate mechanism.

---

## 4. Verification commands

```bash
python -m pytest -q                    # 124 passed
python -m cloud.provision --dry-run    # validates all four importable JSON artifacts
python -m cloud.benchmark              # offline metrics smoke test
```

Artifact validation is offline by default. `--apply` is explicit and credential-gated;
never commit `.env`, tokens or JWTs.

---

## 5. Open items for the next agent / teammate

1. Confirm the final raw-aggregation dashboard is imported and all six widgets show lines.
2. Import `cloud/artifacts/device_profile.json` via **Device Profiles → Import**, verify all nine rules in its **Alarm Rules** subtab, and assign node-a/b/c to that profile.
3. Add server-scope attribute `inactivityTimeout` to each device if `cloud.provision --apply` was not used.
4. Import `cloud/artifacts/wqi_rule_chain.json` and either set it as root or link it from the existing root chain; otherwise `server_received_ms` is never written and latency metrics cannot be collected.
5. Run a demo phase that actually breaches thresholds (silt intrusion / interruption / fault injection), capture Alarms-tab screenshots and export measurements, then replace the placeholders in `cloud/MANUSCRIPT.md`.

---

## 6. Security note

No access tokens, JWTs, passwords or `.env` files are committed. The tokens were passed
only via environment variables during live runs. If you need to publish again, ask the
user to set them in the environment; do not paste them into files or chat logs that get
committed.
