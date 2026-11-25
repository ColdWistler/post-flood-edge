# Anushree's cloud deliverables (0372148)

This is the implementation and verification sheet for ITS67404 Assessment Task 3, sections C.1–C.2 and Anushree's manuscript/demo role. It follows `INTEGRATION.md` and the work-division PDF. The tenant, devices, dashboard configuration and telemetry were created on 25 November 2026; visual dashboard checks and screenshots remain outstanding.

## 1. Tenant and device provisioning

The deployment is `https://eu.thingsboard.cloud`, accessed as tenant administrator on 25 November 2026. The first dashboard screenshot's footer identifies v4.4.1PAAS-EU. The Task 3 brief specifically names ThingsBoard CE (self-hosted or `demo.thingsboard.io`); this EU Cloud tenant is a different hosting option. Confirm acceptance with the lecturer or migrate the finished export to an allowed CE/demo tenant before submission. Do not call the EU Cloud deployment self-hosted CE in the manuscript.

The local `python -m cloud.provision` command previews the setup. With a tenant URL and administrator API key supplied through `TB_URL` and `TB_AUTH`, `python -m cloud.provision --apply` creates/reuses the profile and devices, sets normal inactivity and coordinates, and puts their tokens in ignored `.env`. It saves a **redacted API snapshot**, not the final UI export. Review the actual tenant before applying it. The steps below are also the manual setup and verification route.

1. In **Profiles → Device profiles**, create `Post-Flood Water Source`. Use the standard/default transport supporting MQTT device access tokens. Leave the alarm rules for Sambid to configure on this shared profile. Record a screenshot of the saved profile.
2. In **Devices**, create the following three devices, each assigned to that profile. Use the exact names, because the edge code selects a token by node ID.

   | Device | Site coordinates (latitude, longitude) | Local variable |
   | --- | --- | --- |
   | `node-a` | 27.7172, 85.3240 | `TB_TOKEN_NODE_A` |
   | `node-b` | 27.6790, 85.3190 | `TB_TOKEN_NODE_B` |
   | `node-c` | 27.7500, 85.3700 | `TB_TOKEN_NODE_C` |

3. Open each device's **Manage credentials** dialog and generate/copy its access token. Put tokens only in the local `.env` based on `.env.example`. Never place real tokens in screenshots, manuscript text, Git, or a profile/dashboard export. Set `TB_HOST` to the hostname only (no scheme or port); TLS MQTT uses port 8883.
4. Add `latitude` and `longitude` as device attributes as a static map fallback, or use the same named telemetry keys published by the edge. The map configuration below uses telemetry. Do not publish different coordinates from the device attributes.
5. Set device inactivity for normal reporting to **10,800,000 ms (180 min)**. For burst reporting, use **2,700,000 ms (45 min)**. Current ThingsBoard CE documentation places this override in each device's server-side `inactivityTimeout` attribute; it is not a Device Profile field. The platform-wide `DEFAULT_INACTIVITY_TIMEOUT` applies where no override exists. If a live burst switch is required, change the attribute on all three devices at burst entry and restore it at exit. Capture the three attribute values and test with an appropriate accelerated demo timeout separately. Do not claim 180/45-minute detection was observed during a 15-minute demo. The edge's 90-second `node_inactive` flag is a separate simulation signal.
6. Check **Latest telemetry** on every device after starting `python -m edge.cli --mqtt`. Confirm `ph`, `turbidity_ntu`, `tds_ppm`, `water_temp_c`, `flow_lpm`, `latitude`, `longitude`, `severity`, `buffer_depth`, and `wqi` are present. The names are case-sensitive.

**Export:** Use the Device Profile's export/version-control option available on the actual CE build and save the resulting JSON as `cloud/exports/post-flood-device-profile.json`. If the UI exposes a profile export but includes credentials/provisioning secrets, remove or redact those only from the submission copy and retain the usable original privately. Record the export route and exact version. Do not fabricate an export from this document.

## 2. Operational dashboard

Create `Post-Flood Water Sources — Duty Officer`. Use a 24-column desktop grid. Put the fleet health table and map in the top half, the selected-node gauges immediately beside or below them, and trends/alarms below. The top row must answer which source is currently in trouble, its severity, its last activity time, and where it is. Use text labels as well as colors.

Set the dashboard time window to a live interval that covers the complete demo (for example 1 hour), refresh around 5 seconds, and ensure the history charts use raw samples or a short aggregation interval so the outage backfill remains visible. At demonstration time, widen the interval if the event falls outside the initial window.

### Aliases and states

- `Water sources`: multi-entity alias selecting the three devices by device type/profile or an explicit entity list. Check that it resolves to exactly `node-a`, `node-b`, `node-c`.
- `Selected source`: single-entity alias from dashboard state parameter, used by the gauges and detail charts.
- `fleet` root state: map, node table, alarms, and selected-node summary. Set `node-a` as the initial selected source if the CE build needs a default.
- `source` detail state: selected-source gauges and long trends. Give map markers and node-table rows a **Navigate to new dashboard state** action that passes the clicked device as the selected entity. Add a visible return action to the fleet state. Verify all three map markers open the correct device.

### Eight required widgets and exact binding

| Priority | Widget | Datasource / keys | Configuration and expected use |
| --- | --- | --- | --- |
| 1 | **Node status** entity table | `Water sources`; latest `severity`, `buffer_depth`, server-side `active`, `lastActivityTime` | One row per node; sort Critical/Fault first, then Major, Warning, Normal. Display last activity in local time and explicit `Inactive` status. `RSSI` is **N/A**: the simulator has no radio, so no invented signal readings. Row click drills down. |
| 2 | **OpenStreetMap map** | `Water sources`; `latitude`, `longitude`, `severity`, `active` | Marker label = device name and severity. Fit bounds to all three coordinates. Marker color: Fault purple, Critical red, Major orange, Warning amber, Normal green; stale/inactive grey should take precedence. Marker click drills down. |
| 3 | **pH radial gauge** | `Selected source`; `ph` | Range 0–14; red below 6.5, green 6.5–8.5, red above 8.5; show 2 decimals and selected device name. |
| 4 | **Turbidity radial gauge** | `Selected source`; `turbidity_ntu` | 0–800 NTU or a supported segmented/log scale. Mark 5, 50, 200 NTU clearly. 0–5 green, 5–50 amber, 50–200 orange, above 200 red. |
| 5 | **TDS digital gauge** | `Selected source`; `tds_ppm` | Show ppm with 0 decimals. Below 500 normal, 500–1000 red/high, above 1000 dark red/very high; mark both 500 and 1000 ppm. |
| 6 | **Multi-parameter time-series** | `Selected source`; `turbidity_ntu`, `ph`, `tds_ppm` | Use the chart's independent Y-axis assignment: NTU left, pH right, and a third ppm axis if supported by the installed version. If only two axes are available, keep all three keys in the required chart and add a separate TDS trend directly below it rather than plotting pH and ppm against one unlabeled scale. Show threshold lines at 50 and 200 NTU. |
| 7 | **Temperature and flow chart** | `Selected source`; `water_temp_c`, `flow_lpm` | Two Y axes, °C left and L/min right. Zero-flow event must be visible; do not interpolate across missing data. |
| 8 | **Alarms table** | `Water sources`; ThingsBoard alarms | Show active and cleared alarms, device, severity, created time, cleared time, and duration. Sambid owns the server alarm rules; this widget should still be added and its datasource checked. |

The edge publishes `severity` as `Normal`, `Warning`, `Major`, `Critical`, or `Fault`. It also publishes `active_alarms` as comma-separated text. Use those for marker/status presentation; the ThingsBoard alarm table uses actual server alarms. If the two disagree, investigate the rule threshold or processing lag before the demo.

For the map's **Shape → Color function** editor, the current ThingsBoard CE example uses the `data` object. Include `severity` and server-side `active` as map data keys, then use:

```javascript
var active = data.active;
if (active === false || active === 'false') return '#64748b';
var severity = String(data.severity || 'Normal');
if (severity === 'Fault') return '#7c3aed';
if (severity === 'Critical') return '#dc2626';
if (severity === 'Major') return '#ea580c';
if (severity === 'Warning') return '#d97706';
return '#16a34a';
```

Check how the installed map widget exposes server attributes in `data`; if `active` is unavailable, use `lastActivityTime` and an explicit stale label rather than claiming a working grey marker. Avoid calling water *safe to drink*: these surrogate parameters do not establish microbiological safety.

**Export:** After saving and opening both states, use **Dashboards → Export dashboard** and store the actual JSON at `cloud/exports/post-flood-dashboard.json`. Re-import into a test tenant or inspect it in the UI, then rebind aliases if device UUIDs changed. The export captures widget settings, not telemetry or screenshots.

## 3. Evidence and live demonstration

| Evidence file (suggested) | Capture / annotation |
| --- | --- |
| `cloud/evidence/00-devices-profile.png` | Supplied ThingsBoard Devices screenshot: `node-a`, `node-b`, `node-c` appear in the list, and `node-b`'s detail panel names the `Post-Flood Water Source` profile. This is device provisioning evidence, not a dashboard screenshot. |
| `cloud/evidence/01-dashboard-first-render.png` | Supplied first dashboard screenshot on ThingsBoard v4.4.1PAAS-EU: three map markers, gauges and two charts render. It also records the issues found before tuning: raw activity timestamp, small gauges, a blank tile background and a transient “Failed to parse the payload” banner. It is a diagnostic screenshot, not the final baseline figure. |
| `cloud/evidence/02-source-first-render.png` | Supplied detail-state screenshot after the layout fix. The displayed pH 6.33, turbidity 521.1 NTU and rounded TDS 421 ppm match `node-b`'s latest ThingsBoard telemetry, confirming the selected-source binding. The alarm table was loading and the parse-error banner remained visible before the alarm filter was corrected. |
| `cloud/evidence/03-dashboard-refreshed.png` | Refreshed fleet screenshot after the map and alarm filter updates. Confirms OpenStreetMap tiles load, activity timestamps are formatted, three devices show Critical severity, TDS 302 ppm is green below 500 ppm, and the parse-error banner is gone. Marker labels overlap due to the close coordinates; the alarms area still shows a horizontal scrollbar/loading row. |
| `cloud/evidence/01-baseline.png` | Full dashboard: all three nodes, green/normal status, nominal pH/turbidity/TDS, timestamp and time zone. Circle the selected node and show the map legend. |
| `cloud/evidence/02-intrusion.png` | After `s` in the edge CLI: rising turbidity and TDS, falling pH, changed marker color. Annotate the approximate threshold crossing; do not infer an alarm from color alone. |
| `cloud/evidence/03-outage.png` | After `o`: console queue depth rising plus dashboard last activity becoming stale. Include the time interval and note that the cloud receives no new points during outage. |
| `cloud/evidence/04-backfill.png` | After `o` again: history chart populated at the original acquisition times; pair with a console timestamp or a queried telemetry timestamp. Annotate the outage interval and replayed points. |
| `cloud/evidence/05-drilldown.png` | Fleet map click opening one named source detail view with the matching gauges. |

Run the exact sequence in `docs/DEMO_RUNBOOK.md` with the edge team. Capture genuine screenshots from ThingsBoard, not mockups. Anushree's demo narration: identify the worst node in the fleet table; point to the same node on the map; open its detail state; show pH/turbidity/TDS gauges and the coupled trend; after the outage, show the gap filling at historical timestamps. Let Sambid explain server alarm rules.

## 4. Completion record

Verified on 25 November 2026: `Post-Flood Water Source` profile and three assigned devices exist; each has distinct credentials in ignored `.env`, matching coordinates and a 10,800,000 ms normal inactivity attribute. The supplied Devices screenshot is saved as `cloud/evidence/00-devices-profile.png` and visibly confirms the three names and the `node-b` profile assignment. All three published the required telemetry keys over MQTT/TLS. The dashboard `Post-Flood Water Sources - Duty Officer` was saved and read back from the API with eight fleet widgets and six detail widgets; its API JSON is `cloud/exports/post-flood-dashboard.json`. The redacted profile API snapshot is `cloud/exports/post-flood-device-profile-api.json`; it has **not** been checked as a UI-importable profile export. The historical query matched 35 of 35 original timestamps per node in the 11:51:05–11:52:15 NPT interval, with zero missing; see `cloud/exports/backfill-verification.json`. The local SQLite queue ended with 549 synced and zero pending rows.

The first browser screenshot confirms the fleet dashboard renders on ThingsBoard v4.4.1PAAS-EU, with three correctly placed markers, gauges and history charts. A layout update increased table/gauge height, formatted last activity as local date/time, and gave the pH axis two decimals. A later short MQTT run refreshed all three devices; their latest cloud values were Critical, and the queue-at-sample field was 2 while the local queue drained to zero. This field represents the depth at acquisition, not current unsent rows. Refresh the dashboard to see the updated layout and marker colors.

The supplied detail screenshot confirms the selected-source binding for `node-b`: the shown pH 6.33, turbidity 521.1 NTU and TDS 421 ppm matched the latest telemetry when captured. The refreshed fleet screenshot `cloud/evidence/03-dashboard-refreshed.png` confirms OpenStreetMap Mapnik tiles load, the timestamps are formatted, all three nodes show `Critical`, and the parse-error banner is gone. The TDS gauge shows 302 ppm in green, consistent with the configured 500 ppm warning threshold. The map labels overlap because the node coordinates are close; adjust label placement or turn labels off if a cleaner final capture is needed. The alarms widget still has a horizontal scrollbar and a loading row; Sambid's server alarm rules and actual alarm display need confirmation.

Still to verify: alarm widget completion, profile UI export/import, dashboard re-import and any final annotated screenshots. A 50-row total batch failed repeatedly on this Cloud tenant during replay; a 15-row total batch drained the queue. Use `--batch-size 15` in the live demo and record this as an observed tenant-specific limit, not a universal optimum.

### Platform documentation used

- [Device profiles](https://thingsboard.io/docs/user-guide/device-profiles/)
- [Connectivity status and inactivity override](https://thingsboard.io/docs/user-guide/connectivity-status/)
- [Dashboards and export](https://thingsboard.io/docs/user-guide/dashboards/)
- [Entity aliases and dashboard states](https://thingsboard.io/docs/user-guide/aliases/)
- [Map marker color function](https://thingsboard.io/docs/reference/widgets/maps/map/)
- [Time series chart and independent axes](https://thingsboard.io/docs/reference/widgets/charts/time-series-chart/)
