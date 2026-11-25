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

## 0. Live publisher fix (2026-11-25, this session)

The three-node MQTT run initially stalled with `disconnect rc=7` and no PUBACK.
Two concrete defects were fixed in `edge/publisher.py` (with tests in
`tests/test_publisher.py`):

1. **Broker payload ceiling.** ThingsBoard Cloud closed the connection when a
   single MQTT telemetry packet exceeded roughly 2 KiB. `publish_batch()` now
   serialises rows into multiple `<=1800`-byte JSON arrays and requires a PUBACK
   for every chunk before the batch is acknowledged. Rows are still replayed in
   original timestamp order; a failed chunk leaves the whole batch buffered.
2. **Session/client identity and shutdown.** Each node now connects with a
   stable unique client id (`post-flood-node-a|b|c`) so the three publishers do
   not contend for an empty MQTT id. `disconnect()` also stops the paho network
   loop, preventing reconnect threads from outliving the CLI.

After the fix, live runs produced `PUBLISH` lines for all three nodes and drained
the buffer to zero. The server-side WQI keys `wqi_server`, `wqi_band_server` and
`server_received_ms` were confirmed in node-a Latest Telemetry; screenshot:
`evidence/wqi_server_telemetry.png`.

The benchmark REST client was also adapted to the current tenant API: raw
telemetry queries require `agg=NONE` (lowercase is rejected), the per-query limit
must not exceed 50000, and alarm history is read from
`GET /api/alarm/DEVICE/{uuid}` rather than the retired tenant-wide endpoints.
Without these changes `--apply` silently reported N/A for latency and alarms.

---

## 1. What changed in this update

The edge runtime behaviour was not changed. The work completed the **Cloud Track** by
adding importable ThingsBoard artifacts, provisioning/benchmark tooling, docs and tests,
then doing live verification against ThingsBoard Cloud.

| Area | Files | What was updated |
| --- | --- | --- |
| Contract / docs | `INTEGRATION.md`, `README.md`, `cloud/README.md` | Linked the edge payload contract to the Cloud artifacts; added native-Tab workflow, version caveats and checklist hints. |
| Manuscript | `cloud/MANUSCRIPT.md` | Section-by-section Cloud Track draft with final two-run measured values and log-derived figure references. |
| Alarm spec | `cloud/artifacts/alarm_rules.json` | Declarative nine-rule specification (thresholds, deadbands, debounce). This is **documentation**, not a ThingsBoard import file. |
| Alarm imports | `cloud/artifacts/alarm_rules_import/`, `tools/build_alarm_rule_imports.py`, `tests/test_alarm_rule_imports.py` | Nine ThingsBoard-importable `CalculatedField` documents of type `ALARM`, one per rule, for current Cloud tenants where the legacy profile alarm list is hidden. Import them individually through **Alarm rules → Import**; keep thresholds/debounce unchanged. |
| Device profile | `cloud/artifacts/device_profile.json` | Importable v3.x `DeviceProfile` with **all nine** alarm rules under `profileData.alarms` (six direct thresholds + three `active_alarms CONTAINS` mirrors). |
| Devices | `cloud/artifacts/devices.json` | `node-a/b/c` inventory with `${TB_TOKEN_NODE_*}` placeholders; `type` corrected to `post-flood-water-quality` to match the profile name. Template for `cloud.provision`, not a UI import file. |
| WQI evidence | `cloud/artifacts/wqi_rule_chain.json`, `cloud/artifacts/wqi_server_script.js` | Native rule-chain template: JS filter → transform → save timeseries. Writes `wqi_server`, `wqi_band_server` and `server_received_ms`; edge `wqi`/`wqi_band` remain authoritative. |
| Dashboard | `cloud/artifacts/dashboard_triage.json` | Eight-widget triage dashboard (pH/turbidity radial gauges, digital TDS gauge, multi-parameter chart, temperature/flow chart, severity map, alarms table, node-status entity table), derived from the user's verified Cloud export. Charts retain direct `deviceId` bindings, raw `NONE` aggregation and a 24-hour timewindow. After tenant imports showed blank widgets and then a map with no markers, the map was rebuilt as three direct-device `system.map` markers; its colour falls back to raw alarm channels because this tenant has no `severity` telemetry key. The alarms table uses an entity alarm source, and the gauges/tables use deterministic aliases. `cloud/artifacts/dashboard_thingsboard_cloud.json` remains the confirmed six-chart fallback. |
| Provisioning | `cloud/provision.py` | REST provisioning; `--dry-run` default, `--apply` credential-gated; fills tokens from env, creates profile/devices/dashboard/rule chain, sets device credentials and server-scope `inactivityTimeout`; loads `dashboard_triage.json` by default and falls back to the verified six-chart Cloud export; the alias-based `dashboard.json` was rejected by Cloud import; two-step rule-chain save; opt-in `--set-root`; `--inactivity-ms`. |
| Benchmarking | `cloud/benchmark.py`, `cloud/benchmark/README.md`, `cloud/benchmark/.gitignore` | Six Task 3 metrics from `edge.db`, logs and optional ThingsBoard REST; JSON/CSV/SVG outputs; both `Authorization` and `X-Authorization` headers for Cloud compatibility. |
| IEEE source | `paper/main.tex`, `paper/references.bib`, `paper/README.md`, `paper/figures/` | Standalone IEEEtran two-column source; 12-page PDF, final two-run metrics, log-derived figures, and no quantitative placeholders. Compiles with `IEEEtran.cls`/`.bst` present. |
| Submission tooling | `tools/build_submission_zip.py`, `evidence/README.md`, `docs/TENANT_CHECKLIST.md` | ZIP builder with secret rejection and evidence inclusion, evidence folder convention, and manual tenant checklist. |
| Tests | `tests/test_cloud_benchmark.py`, `tests/test_cloud_provision.py`, `tests/test_alarm_rule_imports.py`, `tests/test_publisher.py`, `tests/test_build_submission_zip.py` | Added cloud helper tests; fixed brittle paho callback-version assertion; added eight-widget triage, alarm-import and ZIP-builder validation. After merging the dashboard workstream's provisioning tests the full suite is **141 passed**. |

Debug dashboards (`dashboard.json`, `dashboard_minimal.json`,
`dashboard_diagnostic_node_b.json`) were used during live troubleshooting but are not
part of the final committed set. The preferred committed dashboard is
`dashboard_triage.json`; `dashboard_thingsboard_cloud.json` is the confirmed
six-chart fallback.

### Where each engine lives (added vs pre-existing)

The edge runtime was **not** changed by this update. Some engines were already present
and were only exercised during the live runs; the Cloud-side equivalents were added here.

| Engine / tool | Location | Provenance |
| --- | --- | --- |
| Hysteresis rule engine | `edge/hysteresis.py` (`HysteresisEngine.evaluate`), wired into `edge/runtime.py` | **Pre-existing** (`a34cc13`, debounce fix in `d2c1a98`). Not added by this update. |
| Edge WQI engine | `edge/dsp.py` (`brown_wqi`, `process_reading`) | **Pre-existing** (`0e1712d`). Live payloads contained `wqi` / `wqi_band`. |
| Server-side WQI engine | `cloud/artifacts/wqi_rule_chain.json`, `cloud/artifacts/wqi_server_script.js` | **Added** in `5da96aa`. Writes `wqi_server`, `wqi_band_server` and `server_received_ms`; tenant export confirms the chain is linked beside the root chain. |
| Server-side alarm / debounce rules | `cloud/artifacts/device_profile.json`, `cloud/artifacts/alarm_rules.json`, `cloud/artifacts/alarm_rules_import/` | **Added** in `5da96aa`; per-rule import files added afterwards. Nine native Alarm Rules with deadbands and `REPEATING` / `DURATION` debounce specs. Use the import directory on current Cloud tenants where the legacy profile alarm list is hidden. |
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
   - **Confirmed:** the user confirmed all six charts in `dashboard_thingsboard_cloud.json` show lines. The derived eight-widget `dashboard_triage.json` is committed with direct device-ID bindings and explicit aliases, and its tenant rendering is captured in `evidence/Dashboard_8_widgets.png` and `evidence/Last_widget.png`.

4. **Tenant status update**: the user imported `device_profile.json` successfully, but its
   **Alarm Rules** tab was empty and `alarmrules.json` was rejected. This is the expected
   Cloud-version mismatch: the profile's `profileData.alarms` list is deprecated/hidden,
   while the nine-rule specification is not an importable entity. The repository now
   provides `cloud/artifacts/alarm_rules_import/` with one ALARM `CalculatedField` per
   rule. Tenant confirmation of those imports, the WQI chain root/link and the
   server-scope `inactivityTimeout` attribute is still pending.

---

## 3. Contract reminders (do not drift from these)

- MQTT over TLS, port **8883**, topic `v1/devices/me/telemetry`, **QoS 1**; rows are marked synced only after PUBACK.
- Payload: `{"ts": <epoch ms>, "values": {...}}`; batches are JSON arrays. Original timestamps survive outage replay.
- Nodes/coordinates (Rasuwa flood corridor): node-a Timure 28.253742/85.366481, node-b Syaphru (Syabru Besi) 28.163522/85.336325, node-c Dhunche 28.111670/85.297780.
- WQI constants: standards `(8.5, 5.0, 1000.0)`, ideals `(7.0, 0.0, 0.0)`, weights `(0.3692, 0.6277, 0.0031)`; bands `≤25 Excellent`, `≤50 Good`, `≤75 Poor`, `≤100 Very poor`, `>100 Unsuitable`.
- Alarm thresholds/deadbands: High Turbidity `>50/<40`; Gross `>200/<150`; pH `<6.5 or >8.5`, clear `6.7–8.3`; TDS `>500/<450`; flow zero 30 s demo; Node Inactive 90 s demo; severe pH `<6.0 or >9.0`, clear `6.2–8.8`; Sensor Fault 3/3; rapid rise `>30 NTU` in 50 s.
- `RSSI` is **N/A** (virtual nodes publish no radio telemetry). Do not invent values.
- In ThingsBoard CE 3.x / Cloud, `inactivityTimeout` is a **server-scope device attribute in ms** (180 min = `10800000`, 45 min burst = `2700000`), not a profile field. The edge's 90 s `Node Inactive` alarm is a separate mechanism.

---

## 4. Verification commands

```bash
python -m pytest -q                    # 141 passed
python -m cloud.provision --dry-run    # validates all four importable JSON artifacts
python -m cloud.benchmark              # offline metrics smoke test
```

Artifact validation is offline by default. `--apply` is explicit and credential-gated;
never commit `.env`, tokens or JWTs.

---

## 5. Open items for the next agent / teammate

1. Import `cloud/artifacts/dashboard_triage.json` and confirm all eight widgets render; if it fails, keep the confirmed six-chart fallback and add the extra widgets manually.
2. Keep the imported `cloud/artifacts/device_profile.json` for the profile name and
   assign node-a/b/c to it from each device's **Details → Device profile** autocomplete,
   then save. The empty Alarm Rules tab is expected on the current tenant;
   import the nine files in `cloud/artifacts/alarm_rules_import/` one at a time through
   **Alarm rules → Import** and choose that profile as the target. If the tenant has no
   Import action, create the rules manually from those files. Do not try to import
   `alarm_rules.json` or a file named `alarmrules.json`. Tenant confirmation of the
   nine-row imported table was not retained, so the repository supplies the import
   documents and generated tests as evidence of the rule definitions.
3. Add server-scope attribute `inactivityTimeout` to each device if `cloud.provision --apply` was not used.
4. Keep the imported `cloud/artifacts/wqi_rule_chain.json` linked from the existing root
   chain (add a **Rule Chain** node targeting `Post-flood WQI rule chain`, connect a
   **Success** output, then Save; use **Publish** only if the tenant shows that button).
   Do not set it as root on a tenant with
   existing rule handling. Otherwise `server_received_ms` is never written and latency
   metrics cannot be collected. Verify `wqi_server`, `wqi_band_server` and
   `server_received_ms` in Latest Telemetry and export both chains.
5. The two saved runs cover silt intrusion, interruption and stuck-sensor injection; their logs and raw benchmark reports are in `evidence/` and `cloud/benchmark/`. No quantitative placeholder remains in `cloud/MANUSCRIPT.md` or `paper/main.tex`.
6. Missing tenant UI screenshots are represented by figures generated from those saved logs; `paper/README.md` labels each figure as a tenant capture or a log-derived reconstruction.
7. `paper/main.pdf` is compiled from `paper/main.tex` and included in the archive. Author, institution and group identifiers remain placeholders by submission convention.
8. Run `python tools/build_submission_zip.py --group <GROUP_NUMBER>` and inspect the archive. The current placeholder build uses `GroupNo`.

---

## 6. Security note

No access tokens, JWTs, passwords or `.env` files are committed. The tokens were passed
only via environment variables during live runs. If you need to publish again, ask the
user to set them in the environment; do not paste them into files or chat logs that get
committed.
