# Demo TUI design (branch `demo-ui`)

## Goal

Give the presenter a single full-screen terminal view that makes the simulator's
behaviour visible during the 15-minute demo: what phase is running, what each node
is sensing, which alarms/severities are latched, how the buffer behaves during an
outage, and which action key does what. It must be the *same* runtime as
`python -m edge.cli` — no second simulation and no new command semantics.

## Approach

Terminal UI built with Python's stdlib `curses`, on the new `demo-ui` branch.
Entry point: `python -m edge.demo_ui`. Default transport is `--dry-run`; `--mqtt`
keeps the existing opt-in behaviour. No third-party packages, no browser, no port.

## Layout

```
┌──────────────────────────────────────────────────────────────────────┐
│ POST-FLOOD EDGE  phase: silt_intrusion [====----] t=01:15/04:00      │
│ outage: OFF  paused: NO  transport: DRY-RUN  fleet pending: 12      │
├──────────────────────┬──────────────────────┬────────────────────────┤
│ node-a  CRITICAL     │ node-b  WARNING      │ node-c  NORMAL         │
│ WQI 186 / Poor       │ WQI 92 / Fair        │ WQI 41 / Good          │
│ pH 6.21  turb 312    │ pH 6.80  turb 48     │ pH 7.20  turb 5        │
│ TDS 410  temp 22.1   │ TDS 320  temp 21.4   │ TDS 180  temp 20.2     │
│ flow 12.0            │ flow 11.8            │ flow 12.1              │
│ alarms: pH Out...,   │ alarms: High Turb... │ alarms: —              │
│   High Turbidity     │ flags: —             │ flags: —               │
│ flags: —  buffer: 4  │ buffer: 4            │ buffer: 4              │
├──────────────────────┴──────────────────────┴────────────────────────┤
│ ACTIONS  b baseline  s silt  i supply  r recovery | o outage         │
│          f/F fault on/off  k/K node-a off/on  a auth  p pause  q quit │
│ last: s (force silt_intrusion)                                        │
│ LOG  STATE: baseline -> silt_intrusion                                │
│      FAULT: node=node-a stuck_fault injected ...                      │
└──────────────────────────────────────────────────────────────────────┘
```

- Header: runtime clock, phase progress from `scenario.elapsed_s` and its configured
  duration, outage/pause badges, transport mode, fleet buffer counts.
- Node panels: three columns when the terminal is wide, stacked rows when narrow.
  Severity coloured (`Normal < Warning < Major < Critical < Fault`); plain text
  fallback when the terminal has no colour support. Fault/power-off badges are
  explicit so injected behaviours are unmistakable.
- Action menu: printed labels for every existing `handle_command()` key. The TUI
  does not add, rename or reinterpret commands.
- Log tail: bounded deque of the last N interesting log lines (`STATE`, `FAULT`,
  `VALIDITY`, `OUTAGE`, `REPLAY`, `AUTH_FAILURE`, `NETWORK_FAILURE`) captured by a
  dedicated logging handler.

## Data flow

- Extract session assembly from `edge/cli.py` into `build_session(args)` returning a
  small `Session` dataclass (config, controller, store, publishers, engines,
  runtime, log handlers). `cli.main()` and the TUI both call it; existing CLI
  monkeypatch-based tests keep working because the patched names stay module-level.
- Add two read-only helpers, with no behaviour change:
  - `BufferStore.status_counts()` → `pending`, `inflight`, `synced` counts.
  - `EdgeRuntime.view_state()` → copies of last values, node state, phase, phase
    elapsed, runtime elapsed, outage/pause flags, publisher transport, buffer
    depths and published count.
- `edge/demo_ui.py` holds pure functions `build_snapshot(view, counts, logs)` and
  `render_lines(snapshot, width, height)` returning a list of strings. The curses
  loop only paints those strings and reads keystrokes. This keeps the display
  testable without a TTY.
- Every keypress routes through `runtime.handle_command()`, exactly like the CLI.
  The UI never writes to the store or publishers directly.

## CLI and test seams

- `python -m edge.demo_ui` — interactive full-screen mode.
- `python -m edge.demo_ui --print-frame --warmup-ticks 6` — build a session, step
  the runtime N times, render one frame to stdout and exit. Useful for headless
  smoke tests, screenshots and manuscript evidence.
- Same flags as `edge.cli` (`--scenario`, `--mqtt`/`--dry-run`, `--tick-seconds`,
  `--batch-size`, `--log-level`, `--db`) plus the two UI-only flags.
- Console logging is replaced by the capture handler while the TUI is active; the
  `logs/edge.log` file handler stays, so the demo log evidence is unchanged.
  Shutdown follows `cli.main()` order: stop runtime → disconnect publishers →
  close store → remove/close handlers → restore curses.
- Terminal too small: show a short "resize to ≥100×30" message on stderr and exit
  rather than crash. Curses is restored on every exit path, including
  `KeyboardInterrupt`.

## Tests

- Unit tests for `build_snapshot` and `render_lines` from a fixed view: phase
  progress, severity colour class, alarm/flag text, outage badge, buffer counts,
  narrow-terminal layout, and action-menu completeness.
- Headless smoke test for `--print-frame` using the existing scenario and a temp
  DB; assert the frame contains all three node ids and the phase name.
- Regression: the existing 107 tests must still pass; CLI smoke still exits 0.

## Out of scope

- Browser UI, mouse input, mouse-driven buttons.
- ThingsBoard provisioning, dashboards, alarm rules or rule chains.
- New algorithms, scenarios, payload keys or command semantics.
- Merging `demo-ui` into `main`; it stays a branch until the demo is over.
- Live MQTT without credentials — default remains dry-run.
