# 15-minute demonstration runbook

Maps the Assessment Task 3 live-demonstration rubric (steps 1–7) to commands and
observable evidence. The edge half of the demo is entirely local: the Cloud Track
presents the ThingsBoard dashboard, alarm table and map. **This repository creates no
ThingsBoard configuration** (no devices, dashboards, alarm rules or rule chains).

## Before the demo

- From the repository root, confirm `python -m pytest` passes and note the machine:
  `python -m edge.cli --dry-run` requires no broker. Use `python -m edge.cli --mqtt`
  only when `.env` has `TB_HOST` and the three node tokens from `INTEGRATION.md`.
- Keep one terminal running the CLI at default `--log-level INFO` (the `BUFFER:` lines
  are INFO). Logs are also appended to `logs/edge.log`.
- Terminal commands below read `edge.db` with the Python standard library; run them
  from the repository root in a second terminal.
- The scripted timeline is `baseline` 60 s → `silt_intrusion` 240 s →
  `supply_interruption` 120 s → `recovery` 180 s, looping (600 s per cycle). The `o`,
  `f`/`F`, `k`/`K`, `s` and `i` keys let the presenter force every graded behaviour
  without editing code mid-demo.

## Timeline

| Clock | Rubric step | Presenter action / command | Expected evidence |
| --- | --- | --- | --- |
| 0:00–1:00 | 1. Start the simulator | `python -m edge.cli --dry-run` (or `--mqtt` with credentials). Press `h` once. | `STATE: runtime started nodes=node-a,node-b,node-c`, `HELP:` line, repeating `PUBLISH node=... rows=...` and `BUFFER: total=... per_node=...`. Severity is `Normal` at rest but is only logged when it changes. Three independent nodes appear in the logs. |
| 1:00–2:00 | 2. Live dashboard update | Cloud Track shows gauges/charts. Edge side: query the last synced rows (below). | New `PUBLISH` lines every 5 s per node; dashboard values change within seconds. If forced to dry-run, the DB query shows the same live `values` the broker would receive. |
| 2:00–4:30 | 3. Trigger contamination | Press `s` to force `silt_intrusion` (or wait for the scripted 60 s transition). Show the DB query of `ph`, `turbidity_ntu`, `tds_ppm`. | `STATE: baseline -> silt_intrusion`; turbidity climbs while pH falls and TDS rises together. Roughly 5 s after a node's onset: `STATE: node=... severity=Warning`; ~15 s: `Critical`. Node-c crosses 500 ppm TDS around 25 s after its 40 s onset offset, plus the 3-sample TDS debounce. |
| 4:30–6:30 | 4. Alarm firing / faults | Cloud Track shows the alarm table and map colour. Edge side: press `f` on `node-a`, wait ~15 s (3 ticks), then `F`. Optional: `k`, wait ~90 s (18 ticks), then `K`. | `STATE: node=... severity=Warning` then `Critical`; `active_alarms` in the payload contains the rule names. Injection gives `FAULT: node=node-a stuck_fault injected`, `VALIDITY: STUCK channel=...`, `FAULT: node=node-a severity=Fault`; clearing plus three valid samples returns severity. `k`/`K` gives `STATE: node=node-a POWER_OFF/POWER_ON` and later `Node Inactive` (`node_inactive=true`) if left off long enough. |
| 6:30–8:30 | 5. Simulated network outage | Press `o`; wait 60–90 s; run the DB status-count query. | `OUTAGE: START; continuing local sampling`, no `PUBLISH`, growing `BUFFER: total=... per_node=...`, increasing `pending` count in SQLite. Sampling/severity log lines continue: the script does not crash or block. |
| 8:30–10:00 | 6. Restore and replay | Press `o` again; show the DB timestamps of replayed rows. | `OUTAGE: END; draining buffered rows`, then `REPLAY: node=... rows=...` followed by `PUBLISH node=... rows=N ts=[...]` with the original (older) epoch-ms timestamps. `BUFFER:` totals fall; the Cloud Track chart backfills the gap in chronological order. |
| 10:00–12:00 | Contingency / depth | Press `i` for `supply_interruption`, then `r` for `recovery`; optional `a` after an auth demo. | `STATE: silt_intrusion -> supply_interruption` etc. After ≥30 s of `flow_lpm=0`, `Supply Interruption` (Major) appears; flow resuming clears it. `a` always logs `STATE: auth retry requested for all nodes` and, in MQTT mode, `RETRY_AUTH: node=...`. Also use this slot to finish the Node Inactive wait if step 4 was shortened. |
| 12:00–15:00 | 7. Questions | Leave the terminal and DB query visible; assign answers by owner. | Saugat: DSP, hysteresis, SQLite replay, MQTT/TLS. Bijee: multi-node data flow, payload, scenarios. Cloud Track: dashboard, alarm rules, rule chain, broader benchmarks. |

If the natural timeline is allowed to run instead of forcing phases, `supply_interruption`
begins at about 5:00 and `recovery` at about 7:00 of simulator time; the cycle repeats
at 10:00. Forcing a phase restarts only that phase's local clock — the physical
rise/washout keeps using cumulative simulation time.

## Inspection commands

Latest delivered telemetry (shows the coupled parameters and alarm audit keys):

```bash
python - <<'PY'
import json, sqlite3
con = sqlite3.connect("edge.db")
for (payload,) in con.execute(
    "SELECT payload FROM telemetry_buffer WHERE status='synced' ORDER BY id DESC LIMIT 6"
):
    row = json.loads(payload)
    v = row["values"]
    print(row["ts"], v["node_id"], v["ph"], v["turbidity_ntu"], v["tds_ppm"],
          v["severity"], v["active_alarms"], v["buffer_depth"])
PY
```

Queue growth during the outage, and original timestamps after replay:

```bash
python - <<'PY'
import sqlite3
con = sqlite3.connect("edge.db")
for status in ("pending", "inflight", "synced"):
    print(status, con.execute(
        "SELECT COUNT(*) FROM telemetry_buffer WHERE status=?", (status,)
    ).fetchone()[0])
print("oldest pending ts", con.execute(
    "SELECT MIN(timestamp) FROM telemetry_buffer WHERE status='pending'"
).fetchone()[0])
PY
```

## Log marker guide

| Marker | When it appears |
| --- | --- |
| `STATE` | Startup/shutdown, phase transitions, severity changes, power/pause/quit keys, auth retry |
| `BUFFER` | Every flush: fleet total plus per-node pending+inflight depth; also rows returned to pending on publish failure |
| `OUTAGE` | `o` toggle: `OUTAGE: START` / `OUTAGE: END` |
| `PUBLISH` | Each batch handed to `NullPublisher` (dry-run) or confirmed by PUBACK (MQTT) |
| `REPLAY` | First flush after an outage that actually has buffered rows |
| `VALIDITY` | `f` injection and any raw-signal gate failure (`SATURATION`, `OPEN_SHORT`, `STUCK`, `SANITY`) |
| `FAULT` | Stuck-fault injection/clear and transition to `severity=Fault` |
| `NETWORK_FAILURE`, `BACKOFF` | MQTT connect/publish/disconnect failures only (not expected in dry-run) |
| `AUTH_FAILURE` | rc 5 / reason code 135 on connect, or a publish with suspended retries; `a` retries |
| `EVICT` | Only when the buffer reaches `EDGE_BUFFER_MAX_ROWS` (default 6048); unlikely in a 15-minute demo |

Severity order is `Normal < Warning < Major < Critical < Fault`, and `active_alarms`
is a comma-separated string of alarm names. `buffer_depth` in the payload is per
node; the log's `BUFFER:` total is fleet-wide. See `INTEGRATION.md` for the full
contract used by the Cloud Track.
