# ThingsBoard Cloud tenant checklist

Manual steps for the tenant owner. No credentials belong in the repository. After each
step, export the resulting JSON or take a screenshot into `evidence/`.

## 1. Import the eight-widget dashboard

1. In ThingsBoard Cloud, open **Dashboards → Import** and select
   `cloud/artifacts/dashboard_triage.json`.
2. Open the imported dashboard. The three gauges intentionally show node-a; no entity
   selection is required.
3. Confirm all eight widgets render: pH radial gauge, turbidity radial gauge, digital
   TDS gauge, multi-parameter time series, temperature/flow chart, severity map, alarms
   table, and node-status entity table.
4. Check that the charts and map show the three nodes. The revised map uses three direct
   `system.map` markers, one per verified device ID, so it does not depend on the missing
   `severity` key; colour is derived from the available raw alarm channels. If a marker is
   still absent, check **Latest Telemetry** on that device for numeric `latitude` and
   `longitude`. If a widget is still blank, use `dashboard_thingsboard_cloud.json` (the
   six-chart export already confirmed working) and add the missing widgets manually.
5. Export the tuned dashboard and save it as `evidence/dashboard_export.json`.

## 2. Apply the device profile and alarm rules

1. Open **Device Profiles → Import** and import `cloud/artifacts/device_profile.json`.
   This creates the profile and its name; on recent ThingsBoard Cloud versions the
   legacy `profileData.alarms` list is hidden/deprecated, so the profile's **Alarm
   Rules** tab may remain empty. That is expected.
2. Assign `node-a`, `node-b` and `node-c` to this profile:

   1. Open **Devices** and click `node-a`.
   2. On the device's **Details** tab, find the **Device profile** field. If it is not
      editable, enter edit mode with the pencil/Edit action.
   3. Replace the current value with `post-flood-water-quality` using the autocomplete.
   4. Click **Save**.
   5. Repeat for `node-b` and `node-c`.
   6. Back in **Devices**, confirm the **Device profile** column shows
      `post-flood-water-quality` for all three nodes. Access tokens and telemetry are
      unchanged by this operation.

3. Do **not** import `cloud/artifacts/alarm_rules.json`. That file is the human-readable
   rule specification, not a ThingsBoard import file.
4. Open the page that has the **Alarm rules → Import** action. In recent Cloud versions
   this is **Alarms → Alarm rules**; a profile-scoped Alarm Rules tab can also be used
   if it shows the Import action. Import these nine files one at a time from
   `cloud/artifacts/alarm_rules_import/`:

   | File | Alarm type | Server severity |
   | --- | --- | --- |
   | `high_turbidity.json` | High Turbidity | `WARNING` |
   | `gross_contamination.json` | Gross Contamination | `CRITICAL` |
   | `ph_out_of_range.json` | pH Out of Range | `CRITICAL` |
   | `high_tds.json` | High TDS | `CRITICAL` |
   | `supply_interruption.json` | Supply Interruption | `MAJOR` |
   | `node_inactive.json` | Node Inactive | `CRITICAL` |
   | `rapid_turbidity_rise.json` | Rapid Turbidity Rise | `WARNING` |
   | `severe_ph_persistence.json` | Severe pH Persistence | `CRITICAL` |
   | `sensor_fault.json` | Sensor Fault | `CRITICAL` |

   Each import opens the rule editor for confirmation. If asked for the entity, choose
   the `post-flood-water-quality` device profile (or the three devices if the page is
   device-scoped). Keep the thresholds and debounce values shown in the files.
   If the tenant has no **Alarm rules → Import** action, create the nine rules with
   **Add alarm rule** instead and copy the thresholds, severities and debounce values
   from these files; retain a screenshot of the resulting table as evidence.
5. Export the profile and save it as `evidence/device_profile_export.json`. Also export
   at least the three direct-threshold rules as `evidence/alarm_rule_*.json`, or save a
   screenshot of the nine-row Alarm rules table if individual export is unavailable.

## 3. Set server-scope inactivity timeout

For each of the three devices, open **Attributes → Server attributes** and add:

```
inactivityTimeout = 10800000
```

This is 180 min in milliseconds. For the accelerated burst run use `2700000`
(45 min). This value is distinct from the edge's 90 s `Node Inactive` alarm.

## 4. Link the server-side WQI rule chain

1. Open **Rule Chains → Import** and import `cloud/artifacts/wqi_rule_chain.json`.
   The imported chain is named `Post-flood WQI rule chain`.
2. **Recommended:** keep your existing root chain as root and link the imported chain
   from it. Do not use **Set as root** unless this tenant is disposable: demoting the
   current root can bypass its existing telemetry/alarm handling.
3. Open the current **root** rule chain in the rule-chain editor.
4. Add a **Rule Chain** node from the node palette and configure its target as
   `Post-flood WQI rule chain`.
5. Connect a **Success** output from the existing flow to that Rule Chain node. If the
   root chain already has a `Save Timeseries` node, connect that node's **Success**
   output; otherwise connect the **Input** node's **Success** output.
6. Click **Apply**/**Save**. Some Cloud builds also show **Publish**; use it if present.
   If there is no Publish button, **Save** is sufficient and the saved version becomes
   active when you exit the editor. Without the saved link, `server_received_ms` is never
   written and the latency metric stays unavailable.
7. Verify with one live telemetry message (or let the edge publish normally) and open
   **Devices → node-a → Latest Telemetry**. Confirm `wqi_server`, `wqi_band_server` and
   `server_received_ms` appear. If they do not, open the WQI chain's rule-engine debug
   events and check that `Has WQI inputs?` outputs **True** and both later nodes output
   **Success**. The filter requires `ph_25`, `turbidity_ntu` and `tds_ppm` in the message.
   **Status: verified on 2026-11-25.** See `evidence/wqi_server_telemetry.png`.
8. Export both the linked root chain and the WQI chain, saving them as
   `evidence/root_rule_chain_export.json` and `evidence/wqi_rule_chain_export.json`.

## 5. Collect benchmark evidence

The live publisher needs `paho-mqtt` and a local `.env` (or exported `TB_*`
variables). If `python -m edge.cli --mqtt` logs `No module named 'paho'`, either install
the dependencies into that interpreter with `python -m pip install -r requirements.txt`
or use an already-prepared environment. Current Cloud tenants close MQTT connections
for telemetry packets above roughly 2 KiB; `edge/publisher.py` therefore splits each
batch into `<=1800`-byte chunks and uses a unique client id per node. If it then reports
missing credentials, copy
`.env.example` to `.env` and fill `TB_HOST`, `TB_CA_CERT` and the three
`TB_TOKEN_NODE_*` values locally; `.env` is gitignored and must never be committed.

Once the chain writes `server_received_ms`, run from the repository root:

```bash
python -m cloud.benchmark --apply --plot
```

Then copy `cloud/benchmark/metrics.json`, `metrics.csv` and `latency_scatter.svg` into
`evidence/` and fill the bracketed values in `cloud/MANUSCRIPT.md` and `paper/main.tex`.
Latency needs at least 200 messages; alarm delay needs at least 10 alarm events; the
delivery ratio and recovery figures need a run that includes an outage and replay.

## 6. Build the submission ZIP

```bash
python tools/build_submission_zip.py --group <GROUP_NUMBER>
```

Open the ZIP and confirm the screenshots, full console log, benchmark outputs, exported
JSON artifacts and both manuscript sources are present. No `.env`, token or JWT may be
included.
