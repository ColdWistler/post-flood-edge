"""Command-line entry point: `python -m edge.cli --dry-run`."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from .config import load_config
from .hysteresis import HysteresisEngine
from .models import EdgeConfig
from .publisher import MqttPublisher, NullPublisher, Publisher
from .runtime import EdgeRuntime
from .scenarios import ScenarioController, load_scenario
from .storage import BufferStore

logger = logging.getLogger(__name__)
_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"


def build_parser() -> argparse.ArgumentParser:
    """Define the demo CLI switches."""
    parser = argparse.ArgumentParser(description="Post-flood virtual edge device")
    parser.add_argument("--scenario", default="scenarios/demo.json")
    transport = parser.add_mutually_exclusive_group()
    transport.add_argument(
        "--mqtt", action="store_true", help="publish through per-node MQTT/TLS clients"
    )
    transport.add_argument(
        "--dry-run", action="store_true", help="log telemetry without a broker (default)"
    )
    parser.add_argument("--tick-seconds", type=float, default=5.0)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--db", default="edge.db")
    return parser


def _configure_logging(
    level_name: str, console_handler: bool = True
) -> list[logging.Handler]:
    """Attach console/file handlers and return them for cleanup."""
    level = getattr(logging, str(level_name).upper(), logging.INFO)
    formatter = logging.Formatter(_FORMAT)
    root = logging.getLogger()
    root.setLevel(level)
    added: list[logging.Handler] = []

    if console_handler and not any(
        isinstance(handler, logging.StreamHandler) for handler in root.handlers
    ):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)
        added.append(console)

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = logging.FileHandler(log_dir / "edge.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
    added.append(file_handler)
    return added


def _build_publishers(
    node_ids: list[str], use_mqtt: bool, config
) -> dict[str, Publisher]:
    """Create one sink per node; a suspended auth failure never blocks startup."""
    publishers: dict[str, Publisher] = {}
    for node_id in node_ids:
        if use_mqtt:
            publisher = MqttPublisher(
                node_id,
                config.tb_host,
                config.tokens.get(node_id, ""),
                ca_cert=config.tb_ca_cert or None,
            )
            publisher.connect()
        else:
            publisher = NullPublisher(node_id)
        publishers[node_id] = publisher
    return publishers


def _read_commands(runtime: EdgeRuntime) -> None:
    """Feed stdin lines to the runtime without blocking the main loop."""
    while not runtime.stopped.is_set():
        try:
            line = sys.stdin.readline()
        except (OSError, ValueError):
            break
        if not line:
            break
        runtime.handle_command(line.strip())


@dataclass
class Session:
    """The fully assembled demo runtime components handed to a driver."""

    config: EdgeConfig
    controller: ScenarioController
    store: BufferStore
    publishers: dict[str, Publisher]
    engines: dict[str, HysteresisEngine]
    runtime: EdgeRuntime
    handlers: list[logging.Handler]


def build_session(args: argparse.Namespace, console_handler: bool = True) -> Session:
    """Assemble config, controllers, sinks, engines and the runtime.

    Shared by the CLI (`console_handler=True`) and the demo TUI
    (`console_handler=False`, which keeps only the `logs/edge.log` file
    handler). The runtime is assembled but not started here; callers own the
    start/stop lifecycle.
    """
    handlers = _configure_logging(args.log_level, console_handler=console_handler)
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    config = load_config(
        None,
        tick_seconds=args.tick_seconds,
        batch_size=args.batch_size,
        db_path=db_path,
        use_mqtt=args.mqtt,
        log_level=args.log_level,
    )
    controller = ScenarioController(load_scenario(Path(args.scenario)))
    store = BufferStore(config.db_path, config.buffer_max_rows, config.batch_size)
    publishers = _build_publishers(
        [node.node_id for node in config.nodes], args.mqtt, config
    )
    engines = {
        node_id: HysteresisEngine(
            flow_zero_sustained_s=config.flow_zero_sustained_s,
            node_inactive_s=config.node_inactive_s,
            derivative_window_s=config.derivative_window_s,
            fault_samples=config.validity_fault_samples,
        )
        for node_id in publishers
    }
    runtime = EdgeRuntime(config, controller, store, publishers, engines)
    return Session(
        config=config,
        controller=controller,
        store=store,
        publishers=publishers,
        engines=engines,
        runtime=runtime,
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    """Run until `q` or KeyboardInterrupt; return a process exit status."""
    args = build_parser().parse_args(argv)
    session = build_session(args)
    runtime = session.runtime
    publishers = session.publishers
    store = session.store
    handlers = session.handlers
    try:
        runtime.start()
        threading.Thread(
            target=_read_commands, args=(runtime,), name="commands", daemon=True
        ).start()
        while not runtime.stopped.wait(0.1):
            pass
        logger.info("STATE: CLI exiting")
        return 0
    except KeyboardInterrupt:
        logger.info("STATE: CLI interrupted")
        return 130
    finally:
        runtime.stop()
        for publisher in publishers.values():
            publisher.disconnect()
        store.close()
        root = logging.getLogger()
        for handler in handlers:
            root.removeHandler(handler)
            handler.close()


if __name__ == "__main__":
    raise SystemExit(main())
