# Post-Flood Edge Track Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the Python edge half of the post-flood water contamination monitor: coupled virtual sensors, DSP, hysteresis, SQLite store-and-forward, a swappable MQTT/console transport, scripted scenarios and a demo CLI, with the cloud side left as a configuration seam.

**Architecture:** Small `edge` package with one responsibility per module. Per-node sampling threads write into one locked SQLite WAL buffer; a single flush loop partitions pending rows by `node_id` and publishes each node's batch through that node's `Publisher`. Default publisher is a log-only sink; `MqttPublisher` is opt-in via `--mqtt` plus tokens in `.env`.

**Tech Stack:** Python 3.10+, stdlib (`sqlite3`, `threading`, `logging`, `random`, `statistics`), `paho-mqtt`, `pytest`.

**Spec:** `docs/superpowers/specs/2026-11-24-post-flood-edge-design.md`

## Global Constraints

- Python 3.10+; runtime dependency is `paho-mqtt` only; `pytest` for tests.
- SQLite `sqlite3` with `PRAGMA journal_mode=WAL`; schema columns exactly `id, timestamp, payload, status, retry_count, created_at`.
- MQTT TLS port 8883, QoS 1, PUBACK confirmation before a row is marked `synced`.
- Payload form `{"ts": <epoch ms>, "values": {...}}`; batch form is a JSON array of these.
- Three nodes: `node-a` (27.7172, 85.3240), `node-b` (27.6790, 85.3190), `node-c` (27.7500, 85.3700).
- Event constants: 12× accelerated demo (`τ_rise=10 s`, `τ_wash=120 s`); amplitudes 500 / 825 / 1250; onset offsets 0 / 20 / 40 s.
- Coupling: `tds += 0.45·u`; `ph = base − 0.90·(1−e^(−u/150))`, floor 6.2; Hampel `W=9`, `k=3`, `σ=1.4826·MAD`.
- Debounce: turbidity 1/1, gross 1/1, pH 3/3, TDS 3/3, node inactive 1/1, severe pH 3/3, sensor fault 3/3; flow sustained 30 s demo / 1800 s production, wall-clock.
- Derivative rule: `Δturbidity > 30 NTU` over 50 s demo / 600 s production, computed on Hampel-cleaned series; raises `Rapid Turbidity Rise` (Warning), escalates on absolute rung.
- Severe pH rule: outside 6.0–9.0 for 3 samples raises `Severe pH Persistence` (Critical), clears at 6.2–8.8.
- Validity gates: saturation, open/short, stuck-value (9-sample) and sanity; 3 consecutive failures raise `Fault`, 3 valid samples clear it. Severity order `Normal < Warning < Major < Critical < Fault`.
- Buffer capacity 6048 rows; evict oldest `pending` rows first with an `EVICT` log line.
- Backoff min 1 s, max 60 s; auth failure (rc 5) suspends retries but never stops buffering.
- Repo at `~/Documents/Github/post-flood-edge/`, `git init` on `main`, no remote, no committed `.env`.

## Review Focus

- Mixed-node pending rows must reach the correct device token, and a partial batch failure must ack only the successful node — Task 8 `test_mixed_nodes_partial_failure_acks_only_success`.
- Original acquisition timestamps must survive an outage and replay in chronological order — Task 5 `test_replay_preserves_epoch_ms_order`; Task 8 end-to-end test.
- Auth failure must be distinguishable from network failure and must not stop local buffering — Task 6 `test_auth_failure_suspends_retries_and_logs`; Task 8 test.
- Buffer overflow must never evict or overwrite `inflight` rows — Task 5 `test_eviction_skips_inflight_rows`.
- Node Inactive must not fire merely because the link is down (sampling continues during an outage) — Task 4 `test_no_inactive_while_samples_continue`; Task 8 test.
- Invalid raw values and fast ramps must not be silently published or lost: validity gates must flag/hold `Fault`, and the derivative rule must raise below the absolute 50 NTU threshold — Task 3 `test_validity_*`; Task 4 `test_derivative_rule_*`; Task 8 `test_validity_fault_wiring`.

---

### Task 1: Repository scaffolding, config and models

**Files:**
- Create: `.gitignore`, `.env.example`, `requirements.txt`, `edge/__init__.py`, `edge/models.py`, `edge/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `Reading(node_id: str, ts_ms: int, values: dict)`, `NodeConfig(node_id, latitude, longitude, seed, amplitude, onset_offset_s)`, `EdgeConfig(nodes, tick_seconds, batch_size, buffer_max_rows, flow_zero_sustained_s, node_inactive_s, derivative_window_s, validity_fault_samples, db_path, use_mqtt, tb_host, tb_ca_cert, tokens: dict[str, str], log_level)`; `load_config(env_path: Path | None = None, **overrides) -> EdgeConfig`.

- [ ] **Step 1: Write the failing config test**

```python
def test_defaults_three_nodes_and_demo_values(tmp_path):
    cfg = load_config(tmp_path / "missing.env", db_path=tmp_path / "b.db")
    assert [n.node_id for n in cfg.nodes] == ["node-a", "node-b", "node-c"]
    assert cfg.tick_seconds == 5.0
    assert cfg.batch_size == 50
    assert cfg.buffer_max_rows == 6048
    assert cfg.flow_zero_sustained_s == 30.0
    assert cfg.node_inactive_s == 90.0
    assert cfg.derivative_window_s == 50.0
    assert cfg.validity_fault_samples == 3
    assert cfg.use_mqtt is False

def test_env_tokens_are_read(tmp_path):
    (tmp_path / ".env").write_text("TB_HOST=demo.thingsboard.io\nTB_TOKEN_NODE_A=abc\n")
    cfg = load_config(tmp_path / ".env")
    assert cfg.tb_host == "demo.thingsboard.io"
    assert cfg.tokens["node-a"] == "abc"
```

- [ ] **Step 2: Run it and confirm it fails**

Run: `cd ~/Documents/Github/post-flood-edge && pytest tests/test_config.py -v`
Expected: FAIL, `load_config` not defined.

- [ ] **Step 3: Implement models/config plus scaffolding**

`git init -b main` in the project root. `.gitignore` excludes `.env`, `__pycache__/`, `.pytest_cache/`, `logs/`, `*.db`, `*.db-wal`, `*.db-shm`. `.env.example` lists `TB_HOST`, `TB_CA_CERT`, `TB_TOKEN_NODE_A/B/C`, `EDGE_BATCH_SIZE`, `EDGE_BUFFER_MAX_ROWS`, `EDGE_FLOW_ZERO_SUSTAINED_S`, `EDGE_NODE_INACTIVE_S`, `EDGE_DERIVATIVE_WINDOW_S`, `EDGE_VALIDITY_FAULT_SAMPLES`, `EDGE_TICK_SECONDS`, `EDGE_DB_PATH`, `EDGE_LOG_LEVEL` with blank/placeholder values. `requirements.txt` pins `paho-mqtt>=2,<3` and `pytest>=8,<9`.

- [ ] **Step 4: Run the test and confirm it passes**

Run: `pytest tests/test_config.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

`git add .gitignore .env.example requirements.txt edge/ tests/ docs/` then `git commit -m "feat: scaffold edge project with config and models"`.

---

### Task 2: Coupled sensor simulator

**Files:**
- Create: `edge/sensors.py`
- Test: `tests/test_sensors.py`

**Interfaces:**
- Consumes: `NodeConfig`, `Reading` (Task 1).
- Produces: `class SensorSimulator` with `__init__(self, node: NodeConfig, tick_seconds: float, glitch_rate: float = 0.005, rng: random.Random | None = None)` and `sample(self, phase: str, elapsed_s: float) -> Reading`; `values` contains the five numeric keys plus `latitude`, `longitude`, `node_id` and `event_u`.

- [ ] **Step 1: Write failing tests for baseline, coupling and glitches**

```python
def test_baseline_ranges():
    sim = SensorSimulator(NODE_A, 5.0, glitch_rate=0.0, rng=random.Random(1))
    for i in range(200):
        v = sim.sample("baseline", i * 5).values
        assert 6.8 <= v["ph"] <= 7.6
        assert 2 <= v["turbidity_ntu"] <= 8
        assert 120 <= v["tds_ppm"] <= 250
        assert 16 <= v["water_temp_c"] <= 24
        assert 8 <= v["flow_lpm"] <= 18

def test_coupled_intrusion_moves_all_parameters():
    base = SensorSimulator(NODE_A, 5.0, 0.0, random.Random(1)).sample("baseline", 0).values
    peak = max(
        (SensorSimulator(NODE_A, 5.0, 0.0, random.Random(1)).sample("silt_intrusion", t).values
         for t in range(0, 60, 2)),
        key=lambda v: v["turbidity_ntu"],
    )
    assert peak["turbidity_ntu"] > 250
    assert peak["tds_ppm"] > base["tds_ppm"] + 50
    assert peak["ph"] < base["ph"] - 0.5
    assert peak["ph"] >= 6.2

def test_supply_interruption_sets_flow_zero():
    v = SensorSimulator(NODE_A, 5.0, 0.0, random.Random(1)).sample("supply_interruption", 10).values
    assert v["flow_lpm"] == 0.0
```

- [ ] **Step 2: Run them, expect failures**

Run: `pytest tests/test_sensors.py -v` → FAIL (module missing).

- [ ] **Step 3: Implement `SensorSimulator`**

`u(t) = amplitude·(1−exp(−Δt/10))·exp(−Δt/120)` with `Δt = elapsed_s − onset_offset_s`, clamped to ≥0; `Δt<0` means no event. `ph = 7.2 − 0.90·(1−exp(−u/150))` floored at 6.2; TDS 25 °C-equivalent `base_tds + 0.45·u`, converted to ambient with `t25*(1+0.02*(T−25))` (Task 3 moves this formula into `ambient_tds()` so it has one home). Flow gets a +2–4 L/min transient for the first 10 s of the event, then normal variation; `phase == "supply_interruption"` forces `0.0`. Rare glitches add `±(200..500)` NTU to turbidity at `glitch_rate` probability and must set `values["glitch"] = True`. Round outputs per Global Constraints.

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest tests/test_sensors.py -v` → PASS.

- [ ] **Step 5: Commit**

`git add edge/sensors.py tests/test_sensors.py && git commit -m "feat: add coupled virtual sensor simulator"`.

---

### Task 3: DSP — validity gates, Hampel, compensation, WQI

**Files:**
- Create: `edge/dsp.py`, `edge/validity.py`
- Test: `tests/test_dsp.py`, `tests/test_validity.py`

**Interfaces:**
- Consumes: `Reading` (Task 1).
- Produces: `HampelFilter.update(value: float) -> float`; `nernst_slope(temp_c: float) -> float`; `compensate_ph(raw_ph: float, temp_c: float, e7: float = 0.0) -> tuple[float, float]`; `ambient_tds(tds_25: float, temp_c: float) -> float`; `compensate_tds(raw_tds: float, temp_c: float) -> float`; `brown_wqi(ph, turbidity_ntu, tds_ppm) -> tuple[float, str]`; `process_reading(reading: Reading, filters: dict[str, HampelFilter]) -> Reading`; `class ValidityGates` with `__init__(self, fault_samples: int = 3)` and `check(values: dict, history: list[dict]) -> tuple[dict, list[str]]` returning substituted values and flags from `{"SATURATION", "OPEN_SHORT", "STUCK", "SANITY"}`.

- [ ] **Step 1: Write failing DSP tests**

```python
def test_hampel_rejects_spike_but_keeps_step():
    f = HampelFilter()
    out = [f.update(v) for v in [10]*9 + [500] + [10]*3 + [60]*9]
    assert out[9] == 10                 # spike replaced by median
    assert out[-1] == 60                # genuine step preserved

def test_nernst_slope_at_25c():
    assert nernst_slope(25.0) == pytest.approx(59.16, abs=0.05)

def test_tds_round_trip_and_compensation():
    assert compensate_tds(ambient_tds(300.0, 35.0), 35.0) == pytest.approx(300.0, abs=1)

def test_wqi_no_chlorine_weights():
    # q_pH=0, q_turbidity=100 (w=0.6277), q_TDS=30 (w=0.0031) -> ~62.9
    wqi, band = brown_wqi(7.0, 5.0, 300.0)
    assert 62 <= wqi <= 64
    assert band == "Poor"

def test_process_reading_adds_audit_keys():
    filters = {k: HampelFilter() for k in ["ph", "turbidity_ntu", "tds_ppm", "water_temp_c", "flow_lpm"]}
    out = process_reading(READING, filters)
    assert "ph_25" in out.values and "wqi" in out.values and "wqi_band" in out.values
```

- [ ] **Step 2: Write failing validity-gate tests**

```python
def test_saturation_flag_and_last_valid_substitution():
    g = ValidityGates()
    clean, flags = g.check({**BASE, "turbidity_ntu": 99999}, [])
    assert "SATURATION" in flags and clean["turbidity_ntu"] < 99999

def test_open_short_and_stuck_value():
    g = ValidityGates()
    _, flags = g.check({**BASE, "ph": 14.0}, [])
    assert "OPEN_SHORT" in flags
    _, flags = g.check({**BASE, "flow_lpm": 12.0}, [BASE]*9)
    assert "STUCK" in flags

def test_valid_baseline_has_no_flags():
    assert ValidityGates().check(BASE, [])[1] == []
```

- [ ] **Step 3: Run all Task 3 tests, expect failures**

Run: `pytest tests/test_dsp.py tests/test_validity.py -v` → FAIL.

- [ ] **Step 4: Implement `edge/dsp.py` and `edge/validity.py`**

Hampel: rolling deque of last 9 raw values; `MAD = median(|x−median|)`, `σ = 1.4826·MAD`, replace with median when `|x−median| > 3σ` and `σ > 0`. `compensate_ph`: `s = nernst_slope(T)`, `e = −s·raw_ph`, return `(7 − e/s, 7 − e/nernst_slope(25))`. `ambient_tds = tds_25·(1+0.02·(T−25))`; `compensate_tds = raw/(1+0.02·(T−25))`. `brown_wqi`: `q = max(0, 100·(V−V0)/(S−V0))` with `S=(8.5,5,1000)`, `V0=(7,0,0)`, weights `(0.3692,0.6277,0.0031)`; bands per spec. `process_reading` runs Hampel per numeric channel, replaces `ph` with compensated `pH_T`, stores `ph_25`, replaces `tds_ppm` with compensated value, adds WQI keys.

`ValidityGates.check`: physical ranges `ph 0–14`, `turbidity 0–4000`, `tds 0–5000`, `water_temp −5–60`, `flow 0–500` flag `SATURATION`; exact endpoint values (`ph` 0/14, turbidity 0/4000, temp −5/60) flag `OPEN_SHORT`; a channel unchanged across the last 9 history entries flags `STUCK`; impossible pH–TDS combinations flag `SANITY`. All failures substitute the last valid value (or the channel baseline for the first sample), log `VALIDITY: <flag> channel=<name>`, and are passed to the hysteresis engine by Task 8.

- [ ] **Step 5: Replace the temporary sensor helper with `ambient_tds`**

Edit `edge/sensors.py` to import and call `ambient_tds`; re-run `pytest tests/test_sensors.py tests/test_dsp.py tests/test_validity.py -v` → PASS.

- [ ] **Step 6: Commit**

`git add edge/dsp.py edge/validity.py edge/sensors.py tests/ && git commit -m "feat: add validity gates, Hampel filter, compensation and WQI"`.

---

### Task 4: Hysteresis state machine

**Files:**
- Create: `edge/hysteresis.py`
- Test: `tests/test_hysteresis.py`

**Interfaces:**
- Consumes: telemetry `values` dict (Task 3).
- Produces: `class HysteresisEngine` with `__init__(self, flow_zero_sustained_s: float = 30.0, node_inactive_s: float = 90.0, derivative_window_s: float = 50.0, derivative_delta_ntu: float = 30.0, fault_samples: int = 3)`, `note_activity(now_s: float) -> None`, `evaluate(values: dict, now_s: float, validity_flags: list[str] | None = None) -> tuple[str, list[str]]`.

- [ ] **Step 1: Write failing tests for deadbands, inactivity, derivative, severe pH and fault**

```python
def test_flapping_does_not_change_state():
    e = HysteresisEngine()
    for t in [48, 52, 48, 52]:
        e.note_activity(t)
        sev, alarms = e.evaluate({"turbidity_ntu": t, "ph": 7.2, "tds_ppm": 200, "flow_lpm": 12}, t)
    assert sev == "Normal" and "High Turbidity" not in alarms

def test_raise_and_clear_deadbands():
    e = HysteresisEngine()
    e.note_activity(0); sev, alarms = e.evaluate(BASE, 0)
    e.note_activity(1); sev, alarms = e.evaluate({**BASE, "turbidity_ntu": 55}, 1)
    assert sev == "Warning" and "High Turbidity" in alarms
    e.note_activity(2); _, alarms = e.evaluate({**BASE, "turbidity_ntu": 45}, 2)
    assert "High Turbidity" in alarms
    e.note_activity(3); _, alarms = e.evaluate({**BASE, "turbidity_ntu": 39}, 3)
    assert "High Turbidity" not in alarms

def test_ph_and_tds_debounce_three_samples():
    e = HysteresisEngine()
    for t in [0, 1]:
        e.note_activity(t); _, alarms = e.evaluate({**BASE, "ph": 8.7}, t)
        assert "pH Out of Range" not in alarms
    e.note_activity(2); _, alarms = e.evaluate({**BASE, "ph": 8.7}, 2)
    assert "pH Out of Range" in alarms

def test_no_inactive_while_samples_continue():
    e = HysteresisEngine(node_inactive_s=90)
    for t in range(0, 300, 5):
        e.note_activity(t)
        _, alarms = e.evaluate(BASE, t)
        assert "Node Inactive" not in alarms

def test_inactive_after_timeout():
    e = HysteresisEngine(node_inactive_s=90)
    e.note_activity(0)
    sev, alarms = e.evaluate(BASE, 95)
    assert sev == "Critical" and "Node Inactive" in alarms

def test_derivative_rule_fires_below_absolute_threshold():
    e = HysteresisEngine(derivative_window_s=50)
    e.note_activity(0); e.evaluate({**BASE, "turbidity_ntu": 10}, 0)
    e.note_activity(50); sev, alarms = e.evaluate({**BASE, "turbidity_ntu": 45}, 50)
    assert sev == "Warning" and "Rapid Turbidity Rise" in alarms

def test_severe_ph_persistence_requires_three_samples():
    e = HysteresisEngine()
    for t in [0, 1]:
        e.note_activity(t); _, alarms = e.evaluate({**BASE, "ph": 9.2}, t)
        assert "Severe pH Persistence" not in alarms
    e.note_activity(2); sev, alarms = e.evaluate({**BASE, "ph": 9.2}, 2)
    assert sev == "Critical" and "Severe pH Persistence" in alarms

def test_fault_held_until_three_valid_samples():
    e = HysteresisEngine(fault_samples=3)
    for t in [0, 1]:
        e.note_activity(t); sev, _ = e.evaluate(BASE, t, ["STUCK"])
        assert sev != "Fault"
    e.note_activity(2); sev, alarms = e.evaluate(BASE, 2, ["STUCK"])
    assert sev == "Fault" and "Sensor Fault" in alarms
    for t in [3, 4]:
        e.note_activity(t); sev, _ = e.evaluate(BASE, t, [])
        assert sev == "Fault"
    e.note_activity(5); sev, alarms = e.evaluate(BASE, 5, [])
    assert sev != "Fault" and "Sensor Fault" not in alarms
```

- [ ] **Step 2: Run, expect failures**

Run: `pytest tests/test_hysteresis.py -v` → FAIL.

- [ ] **Step 3: Implement `HysteresisEngine`**

Per-rule counters with exact deadbands from Global Constraints; `flow_lpm == 0` starts a wall-clock timer on first zero sample and raises when `now_s − zero_since >= flow_zero_sustained_s`, clears immediately when flow > 0; node inactive from `now_s − last_activity_s`. Derivative: keep a deque of `(now_s, turbidity_ntu)` and raise when the oldest entry inside `derivative_window_s` differs by more than `derivative_delta_ntu`; clear when the rate falls back below the delta. Severe pH: outside 6.0–9.0 for 3 samples, clear at 6.2–8.8. Fault: `validity_flags` non-empty raises a counter, empty samples decrement it; hold `Fault` until `fault_samples` consecutive clean samples. Severity ordering `Normal < Warning < Major < Critical < Fault`; return sorted active alarm names.

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest tests/test_hysteresis.py -v` → PASS.

- [ ] **Step 5: Commit**

`git add edge/hysteresis.py tests/test_hysteresis.py && git commit -m "feat: add hysteresis alarm state machine"`.

---

### Task 5: SQLite store-and-forward buffer

**Files:**
- Create: `edge/sql/schema.sql`, `edge/storage.py`
- Test: `tests/test_storage.py`

**Interfaces:**
- Consumes: `Reading` (Task 1).
- Produces: `class BufferStore` with `__init__(self, db_path: Path, max_rows: int, batch_size: int)`, `enqueue(reading: Reading) -> None`, `depth(node_id: str | None = None) -> int`, `claim() -> list[tuple[str, list[dict]]]`, `ack(rows: list[dict]) -> None`, `fail(rows: list[dict]) -> None`, `close() -> None`. Claimed row dicts contain `id`, `ts`, `values`.

- [ ] **Step 1: Write failing storage tests**

```python
def test_wal_enabled_and_write_before_publish(tmp_path):
    s = BufferStore(tmp_path/"a.db", max_rows=100, batch_size=10)
    assert s._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    s.enqueue(READING)
    assert s.depth() == 1
    assert s._conn.execute("SELECT status FROM telemetry_buffer").fetchone()[0] == "pending"

def test_replay_preserves_epoch_ms_order(tmp_path):
    s = BufferStore(tmp_path/"b.db", max_rows=100, batch_size=10)
    for ts in [3000, 1000, 2000]:
        s.enqueue(replace(READING, ts_ms=ts))
    batches = s.claim()
    rows = [r for _, group in batches for r in group]
    assert [r["ts"] for r in rows] == [1000, 2000, 3000]
    assert all(set(r) == {"id", "ts", "values"} for r in rows)

def test_ack_and_fail(tmp_path):
    s = BufferStore(tmp_path/"c.db", max_rows=100, batch_size=2)
    s.enqueue(READING)
    row = s.claim()[0][1][0]
    s.ack([row])
    assert s.depth() == 0
    assert s._conn.execute("SELECT status FROM telemetry_buffer").fetchone()[0] == "synced"
    s.enqueue(READING)
    row = s.claim()[0][1][0]
    s.fail([row])
    assert s._conn.execute("SELECT status, retry_count FROM telemetry_buffer").fetchone() == ("pending", 1)

def test_eviction_skips_inflight_rows(tmp_path, caplog):
    s = BufferStore(tmp_path/"d.db", max_rows=2, batch_size=10)
    s.enqueue(replace(READING, ts_ms=1))
    inflight = s.claim()[0][1][0]
    s.enqueue(replace(READING, ts_ms=2))
    s.enqueue(replace(READING, ts_ms=3))
    assert s.depth() == 2
    assert s._conn.execute("SELECT status FROM telemetry_buffer WHERE id=?", (inflight["id"],)).fetchone()[0] == "inflight"
    assert "EVICT" in caplog.text
```

- [ ] **Step 2: Run, expect failures**

Run: `pytest tests/test_storage.py -v` → FAIL.

- [ ] **Step 3: Implement schema and `BufferStore`**

DDL exactly as the spec. Connection `check_same_thread=False` plus an `RLock` around every method. `enqueue` stores `json.dumps({"ts": reading.ts_ms, "values": reading.values})` with `timestamp=ts_ms`, `created_at=time.time_ns()//1_000_000`, status `pending`. `claim` selects oldest `pending` up to `batch_size`, marks `inflight`, groups by `values["node_id"]`. `ack` sets `synced`; `fail` restores `pending` and increments `retry_count`. Overflow: `DELETE` oldest `pending` rows until under capacity, log `EVICT ...`; if no pending rows remain, return without inserting and log `DROP NEW ...`.

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest tests/test_storage.py -v` → PASS.

- [ ] **Step 5: Commit**

`git add edge/sql/schema.sql edge/storage.py tests/test_storage.py && git commit -m "feat: add SQLite WAL store-and-forward buffer"`.

---

### Task 6: Publisher seam (console + MQTT/TLS)

**Files:**
- Create: `edge/publisher.py`
- Test: `tests/test_publisher.py`, `tests/conftest.py` (define `fake_client_factory` here)

**Interfaces:**
- Consumes: nothing beyond config values.
- Produces: `class Publisher(Protocol)` with `is_connected: bool`, `connect() -> bool`, `disconnect() -> None`, `publish_batch(batch: list[dict]) -> bool`; `NullPublisher`; `MqttPublisher(node_id, host, token, port=8883, ca_cert=None, client_factory=None)`.

- [ ] **Step 1: Write failing publisher tests using an injected fake client**

```python
def test_null_publisher_logs_and_acks(caplog):
    p = NullPublisher("node-a")
    assert p.connect() and p.publish_batch([{"ts": 1, "values": {}}])
    assert "PUBLISH" in caplog.text

def test_auth_failure_suspends_retries_and_logs(fake_client_factory, caplog):
    p = MqttPublisher("node-a", "host", "bad", client_factory=fake_client_factory)
    fake = p._client
    p.connect()
    fake.fire_connect(5)
    assert p.is_connected is False
    assert p._retries_suspended is True
    assert "AUTH_FAILURE" in caplog.text

def test_network_failure_logs_and_backs_off(fake_client_factory, caplog):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect(); p._client.fire_connect(1)
    assert "NETWORK_FAILURE" in caplog.text
    assert p._backoff_s in (1.0, 2.0)
```

- [ ] **Step 2: Run, expect failures**

Run: `pytest tests/test_publisher.py -v` → FAIL.

- [ ] **Step 3: Implement publishers**

`NullPublisher` logs `PUBLISH node=... rows=N ts=[...]` and returns True. `MqttPublisher.connect()` creates the paho client via `client_factory or mqtt.Client`, sets `tls_set(ca_certs=ca_cert or None)`, `username_pw_set(token, None)`, registers `on_connect`/`on_disconnect`, connects to `host:port`, starts the loop; `publish_batch` publishes to `v1/devices/me/telemetry` with `qos=1` and waits for PUBACK via `wait_for_publish(timeout=10)`, returning bool. Backoff doubles from 1 s to 60 s on network failures and resets on connect; rc 5 sets `_retries_suspended=True` with `AUTH_FAILURE` log, other non-zero rc logs `NETWORK_FAILURE`. Expose `retry_auth()` clearing the suspension.

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest tests/test_publisher.py -v` → PASS.

- [ ] **Step 5: Commit**

`git add edge/publisher.py tests/test_publisher.py && git commit -m "feat: add pluggable console and MQTT/TLS publishers"`.

---

### Task 7: Scenario controller and demo timeline

**Files:**
- Create: `scenarios/demo.json`, `edge/scenarios.py`
- Test: `tests/test_scenarios.py` (define `fake_clock` in `tests/conftest.py`)

**Interfaces:**
- Consumes: nothing.
- Produces: `load_scenario(path: Path) -> dict`; `class ScenarioController` with `__init__(self, scenario: dict, now_fn: Callable[[], float] = time.monotonic)`, `phase -> str` (property), `elapsed_s -> float` (property), `tick() -> str`, `set_phase(name: str) -> str`.

- [ ] **Step 1: Write failing tests**

```python
def test_timeline_advances_phases(fake_clock):
    c = ScenarioController(load_scenario(DEMO), now_fn=fake_clock)
    assert c.phase == "baseline"
    fake_clock.advance(61)
    assert c.tick() == "silt_intrusion"
    fake_clock.advance(241)
    assert c.tick() == "supply_interruption"

def test_manual_override_and_reset_elapsed(fake_clock):
    c = ScenarioController(load_scenario(DEMO), now_fn=fake_clock)
    fake_clock.advance(30)
    assert c.set_phase("silt_intrusion") == "silt_intrusion"
    assert c.elapsed_s == 0
```

- [ ] **Step 2: Run, expect failures**

Run: `pytest tests/test_scenarios.py -v` → FAIL.

- [ ] **Step 3: Implement scenario files**

`demo.json` contains `phases` with names/durations 60/240/120/180 s and `nodes` with `onset_offset_s` 0/20/40 and `amplitude` 500/825/1250. `ScenarioController` tracks `phase_start` from `now_fn`, `tick()` advances through the ordered phases and loops, `set_phase()` resets `phase_start`, both log `STATE: <old> -> <new>`.

- [ ] **Step 4: Run tests, expect pass**

Run: `pytest tests/test_scenarios.py -v` → PASS.

- [ ] **Step 5: Commit**

`git add scenarios/ edge/scenarios.py tests/test_scenarios.py && git commit -m "feat: add scripted scenario timeline controller"`.

---

### Task 8: Runtime orchestration and CLI

**Files:**
- Create: `edge/runtime.py`, `edge/cli.py`
- Test: `tests/test_runtime.py` (uses `fake_clock`, `build_runtime`, `failing_publisher` from `tests/conftest.py`)

**Interfaces:**
- Consumes: `EdgeConfig` (T1), `SensorSimulator` (T2), `process_reading` and `ValidityGates` (T3), `HysteresisEngine` (T4), `BufferStore` (T5), publishers (T6), `ScenarioController` (T7).
- Produces: `class EdgeRuntime` with `__init__(config, scenario, store, publishers: dict[str, Publisher], engines: dict[str, HysteresisEngine])`, `start()`, `stop()`, `handle_command(command: str) -> None`, `step_once() -> None` (one sampling tick + flush, used by tests), `published: list[dict]` recording each batch handed to a publisher with keys `node_id`, `ts` (first row's timestamp), `values` (first row's telemetry dict), `acked`, and `node_state: dict[str, dict]` holding per-node demo flags including `stuck_fault`; `main(argv: list[str] | None = None) -> int` in `edge/cli.py`.

- [ ] **Step 1: Write failing end-to-end runtime tests**

```python
def test_outage_buffers_then_replays_with_original_timestamps(tmp_path, fake_clock):
    rt = build_runtime(tmp_path, fake_clock)
    rt.step_once()                      # baseline, online
    rt.handle_command("o")              # OUTAGE START
    fake_clock.advance(10)
    rt.step_once()
    assert rt.store.depth() >= 1
    rt.handle_command("o")              # OUTAGE END
    rt.step_once()
    assert rt.store.depth() == 0
    assert rt.published[-1]["ts"] == expected_first_ts

def test_mixed_nodes_partial_failure_acks_only_success(tmp_path, fake_clock, failing_publisher, caplog):
    rt = build_runtime(tmp_path, fake_clock)
    rt.publishers["node-b"] = failing_publisher
    rt.step_once()
    assert all(r["values"]["node_id"] != "node-b" for r in rt.published if r.get("acked"))
    assert "NETWORK_FAILURE" in caplog.text

def test_auth_failure_keeps_buffering(tmp_path, fake_clock, caplog):
    rt = build_runtime(tmp_path, fake_clock)
    rt.publishers["node-a"]._retries_suspended = True
    rt.step_once(); fake_clock.advance(5); rt.step_once()
    assert rt.store.depth() >= 1
    assert all(b["node_id"] != "node-a" or b["acked"] is False for b in rt.published)

def test_validity_fault_wiring(tmp_path, fake_clock, caplog):
    rt = build_runtime(tmp_path, fake_clock)
    rt.handle_command("f")
    for t in [0, 5, 10, 15, 20]:
        fake_clock.advance(5); rt.step_once()
    assert "VALIDITY:" in caplog.text
    assert any(b.get("values", {}).get("severity") == "Fault" for b in rt.published)
    rt.handle_command("F")
    assert "stuck_fault" not in rt.node_state["node-a"]
```

- [ ] **Step 2: Run, expect failures**

Run: `pytest tests/test_runtime.py -v` → FAIL.

- [ ] **Step 3: Implement runtime**

Per-node `threading.Thread` sampling loop in `start()`; `step_once()` performs one sample per powered node, calls `ValidityGates.check()` first (log `VALIDITY:`), applies `process_reading`, evaluates its `HysteresisEngine` with the returned flags, appends `severity`, `active_alarms`, `buffer_depth`, `node_inactive`, `validity_flags` and `sensor_fault` to `values`, then `store.enqueue()` **before** any publish. Keep `self.node_state[node_id]["stuck_fault"]` for the `f`/`F` injection. Flush calls `store.claim()`, partitions per node (store already groups), skips all publishing while `outage_active` (log `OUTAGE: START/END`), calls `publisher.publish_batch`, then `store.ack` or `store.fail`. `stop()` sets a `threading.Event` and joins threads. `handle_command` supports `b/s/i/r`, `o`, `k`/`K`, `f`/`F`, `a`, `p`, `q`, `h`. CLI parses `--scenario`, `--mqtt`/`--dry-run`, `--tick-seconds`, `--batch-size`, `--log-level`, `--db`, configures `logging` with a file handler under `logs/`, loads config and scenario, constructs publishers from tokens (NullPublisher unless `--mqtt`), and runs until `KeyboardInterrupt` or `q`.

- [ ] **Step 4: Run the full test suite**

Run: `pytest -v` → PASS (all tests).

- [ ] **Step 5: Commit**

`git add edge/runtime.py edge/cli.py tests/test_runtime.py && git commit -m "feat: add multi-node runtime and demo CLI"`.

---

### Task 9: Integration docs and batch-size benchmark

**Files:**
- Create: `README.md`, `INTEGRATION.md`, `docs/DEMO_RUNBOOK.md`, `tools/benchmark_batch_size.py`

**Interfaces:**
- Consumes: `BufferStore`, `NullPublisher`, config from earlier tasks.
- Produces: a CSV at `tools/batch_results.csv` with columns `batch_size,rows_per_second,elapsed_s` when the benchmark is run.

- [ ] **Step 1: Write the benchmark and run it**

Implement `benchmark_batch_size(db_path, sizes=(1,10,25,50,100,200), rows_per_size=500) -> list[dict]`. Run `python tools/benchmark_batch_size.py` and confirm `tools/batch_results.csv` has six data rows and a non-zero rows/s for each.

- [ ] **Step 2: Write the three documents**

`README.md`: quickstart (`python -m edge.cli --dry-run`), key bindings (`b/s/i/r/o/k/K/f/F/a/p/q/h`), module/owner map, test command. `INTEGRATION.md`: topic `v1/devices/me/telemetry`, exact payload keys (`ph`, `turbidity_ntu`, `tds_ppm`, `water_temp_c`, `flow_lpm`, `latitude`, `longitude`, `node_id`, `ph_25`, `wqi`, `wqi_band`, `severity`, `active_alarms`, `buffer_depth`, `node_inactive`, `validity_flags`, `sensor_fault`), `{"ts": epoch_ms, "values": {...}}` form, env vars/token names, device names, eight alarm/deband definitions including `Rapid Turbidity Rise`, `Severe pH Persistence` and `Fault`, and the statement that no ThingsBoard configuration is created here. `docs/DEMO_RUNBOOK.md`: 15-minute sequence mapping rubric steps 1–7 to commands, expected log markers (including `VALIDITY:`/`FAULT`) and phase timings.

- [ ] **Step 3: Commit**

`git add README.md INTEGRATION.md docs/DEMO_RUNBOOK.md tools/ && git commit -m "docs: add integration contract, demo runbook and batch benchmark"`.

---

### Task 10: Final verification and demo smoke run

**Files:**
- Modify: nothing; verify all.

**Interfaces:**
- Consumes: all previous tasks.

- [ ] **Step 1: Run full verification**

Run: `pytest -q && python -m compileall -q edge && python -m edge.cli --dry-run --scenario scenarios/demo.json --tick-seconds 0.1 --log-level WARNING` (send `s`, wait 3 s, send `f`, wait 1 s, send `F`, send `o`, wait 1 s, send `o`, send `q`).
Expected: all tests pass; compile clean; CLI exits 0; `logs/edge.log` contains `STATE`, `VALIDITY:`, `FAULT`, `OUTAGE: START`, `OUTAGE: END`, `PUBLISH`, `BUFFER` markers and the database file returns to depth 0 after replay.

- [ ] **Step 2: Commit any fixes found**

If changes were needed, `git add -A && git commit -m "fix: address final verification findings"`; otherwise state that no commit was needed.
