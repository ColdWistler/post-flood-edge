# Real-broker benchmark evidence

`python -m cloud.benchmark` produces the Task 3 cloud metrics from the same
`edge.db` and `logs/edge.log` that the simulator writes. Output files in this
directory are generated and git-ignored:

| File | Contents |
| --- | --- |
| `metrics.json` | Full report: window, mode, six metrics, sample counts, caveats |
| `metrics.csv` | Flat table for the manuscript/spreadsheet |
| `latency_scatter.svg` | Optional latency-over-time plot (`--plot`) |

## Run

From the repository root, with the virtual environment active:

```bash
# Offline: buffer replay, recovery time and local sync ratio only
python -m cloud.benchmark

# With a real ThingsBoard instance (JWT from an admin login)
export TB_HOST=demo.thingsboard.io
export TB_JWT='<accessToken from POST /api/auth/login>'
python -m cloud.benchmark --apply --plot \
    --start-ts 1767225600000 --end-ts 1767229200000
```

`TB_CA_CERT` is honoured for a private CA; blank uses system CAs. Credentials are
never written to the report. The window defaults to the min/max acquisition
timestamp in `edge.db`; pass an explicit window for a measured demo run.

## Metric definitions

| Metric | Computation | Source |
| --- | --- | --- |
| End-to-end latency | `server_received_ms - ts` per message; mean, median, P95, max over ≥ 200 messages | `wqi_rule_chain.json` writes `server_received_ms` with `new Date().getTime()`; edge `ts` is acquisition time. The chain must be root or linked from root — see `cloud/README.md`. |
| Message delivery ratio | generated rows whose original `ts` appears in ThingsBoard `ph` telemetry / generated rows × 100 | `edge.db` compared with `/api/plugins/telemetry/DEVICE/.../values/timeseries` |
| Alarm activation delay | edge `active_alarms` rise → ThingsBoard alarm `createdTime`, matched by device and alarm type | `edge.db` plus `GET /api/alarm/DEVICE/{uuid}` (falls back to `/api/alarm/search` on older releases) |
| Buffer flush throughput | `REPLAY:` rows / seconds from `OUTAGE: END` to the drain point (falls back to `PUBLISH` rows for older logs without a `REPLAY:` marker) | `logs/edge.log` |
| Recovery time | `OUTAGE: END` → `BUFFER: total=0` (fallback: last replay `PUBLISH`) | `logs/edge.log` |
| Data loss | generated rows absent from ThingsBoard after full sync, count and % | `edge.db` compared with telemetry presence |

A metric is reported as `N/A` with a note when its source is missing — for
example when the rule chain has not been imported (no `server_received_ms`) or
when the log has no completed outage. Do not fill those cells with estimates.

## Measurement caveats

- Client and server clocks must be NTP-synchronised; otherwise the latency
  difference is meaningless. State the clock source in the manuscript.
- ThingsBoard stores the **client** timestamp. Presence matching uses
  `(node_id, ts)`, not server receipt order, so outage backfill counts correctly.
- Alarm matching uses the first edge sample where an alarm name appears and the
  nearest server alarm creation at or after that time. Cleared alarms already
  matched are removed, so one server event is not counted twice.
- Current Cloud tenants reject `agg=none` (lowercase) and query limits above
  50000; the client uses `agg=NONE` and a 50000-row limit. Alarm history is read
  per device because the tenant-wide endpoints return HTTP 405.
- Replayed rows legitimately include buffering time in `server_received_ms - ts`.
  For a representative live latency, pass `--start-ts`/`--end-ts` covering a run
  with no outage; report replay latency separately if it is discussed at all.
- The local `edge.db` log is authoritative for generation counts; the cloud
  telemetry API is authoritative for delivery. A run with no broker cannot
  produce delivery, latency or data-loss numbers — use dry-run evidence only for
  buffer behaviour.
- Batch-size throughput from `tools/benchmark_batch_size.py` is host-only. Re-run
  the real-broker benchmark for each candidate batch size when reporting an
  empirically chosen optimum.
