"""Shared pytest fixtures for the edge simulator tests."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from edge.hysteresis import HysteresisEngine
from edge.models import EdgeConfig, NodeConfig
from edge.publisher import NullPublisher
from edge.scenarios import ScenarioController, load_scenario
from edge.storage import BufferStore

# Telemetry delivery and runtime orchestration are logged at INFO in
# production; make them visible to caplog in tests that need the markers.
logging.getLogger("edge.publisher").setLevel(logging.INFO)
logging.getLogger("edge.runtime").setLevel(logging.INFO)

NODE_A = NodeConfig("node-a", 27.7172, 85.3240, 1001, 500, 0.0)
DEMO_SCENARIO = Path(__file__).resolve().parents[1] / "scenarios" / "demo.json"
FIRST_TS_MS = 1_700_000_000_000


class FakeClock:
    """Callable monotonic clock that only moves when advance() is called."""

    def __init__(self, start: float = 1000.0) -> None:
        self._now = float(start)

    def __call__(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += float(seconds)


class FakeMqttClient:
    """In-memory stand-in for a paho client; no broker or paho install needed."""

    def __init__(self, callback_api_version=None, events=None) -> None:
        self.callback_api_version = callback_api_version
        self.tls_kwargs: dict = {}
        self.username: str | None = None
        self.password: str | None = None
        self.connect_args: tuple | None = None
        self.reconnect_delays: list[tuple[float, float]] = []
        self.loop_started = False
        self.loop_stopped = False
        self.disconnected = False
        self._events = [] if events is None else events
        self.published: list[tuple[str, str, int]] = []
        self.wait_timeouts: list[float] = []
        self.is_published_calls = 0
        self.ack_ok = True
        self.on_connect = None
        self.on_disconnect = None

    def tls_set(self, **kwargs) -> None:
        self.tls_kwargs = kwargs

    def username_pw_set(self, username: str, password: str | None) -> None:
        self.username = username
        self.password = password

    def connect(self, host: str, port: int = 1883) -> None:
        self.connect_args = (host, port)

    def reconnect_delay_set(self, min_delay: float, max_delay: float) -> None:
        self.reconnect_delays.append((min_delay, max_delay))

    def loop_start(self) -> None:
        self.loop_started = True

    def loop_stop(self) -> None:
        self.loop_stopped = True
        self._events.append(("loop_stop", id(self)))

    def disconnect(self) -> None:
        self.disconnected = True
        self._events.append(("disconnect", id(self)))

    def publish(self, topic: str, payload: str, qos: int = 0) -> "FakeMessage":
        self.published.append((topic, payload, qos))
        return FakeMessage(self)

    def fire_connect(self, rc: int) -> None:
        """Invoke the registered connection callback with a broker return code."""
        assert self.on_connect is not None
        self.on_connect(self, None, {}, rc)

    def fire_disconnect(self, rc: int = 0) -> None:
        """Invoke the registered disconnection callback."""
        assert self.on_disconnect is not None
        self.on_disconnect(self, None, rc)


class FakeMessage:
    """paho-style MQTTMessageInfo: waiting never raises; PUBACK state is queried."""

    def __init__(self, client: FakeMqttClient) -> None:
        self._client = client

    def wait_for_publish(self, timeout: float | None = None) -> None:
        self._client.wait_timeouts.append(timeout)

    def is_published(self) -> bool:
        self._client.is_published_calls += 1
        return self._client.ack_ok


@pytest.fixture
def fake_clock() -> FakeClock:
    """Deterministic now_fn for timeline, pacing and outage tests."""
    return FakeClock()


@pytest.fixture
def fake_client_factory():
    """Return a factory that records every fake client it creates."""
    clients: list[FakeMqttClient] = []
    events: list[tuple[str, int]] = []

    def factory(callback_api_version=None) -> FakeMqttClient:
        client = FakeMqttClient(callback_api_version=callback_api_version, events=events)
        clients.append(client)
        events.append(("created", id(client)))
        return client

    factory.clients = clients  # type: ignore[attr-defined]
    factory.events = events  # type: ignore[attr-defined]
    return factory


class FailingPublisher(NullPublisher):
    """Log-only sink whose publish attempts always fail (network error)."""

    def publish_batch(self, batch: list[dict]) -> bool:
        logger = logging.getLogger(__name__)
        logger.warning("NETWORK_FAILURE: node=%s injected failure", self.node_id)
        return False


@pytest.fixture
def failing_publisher():
    """A publisher that fails node-b while the other nodes still succeed."""
    return FailingPublisher("node-b")


@pytest.fixture
def expected_first_ts(monkeypatch):
    """Freeze the sensors' epoch clock so replayed rows keep their first ts."""
    monkeypatch.setattr(
        "edge.sensors.time",
        SimpleNamespace(time=lambda: FIRST_TS_MS / 1000.0),
    )
    return FIRST_TS_MS


@pytest.fixture
def build_runtime(request, tmp_path, fake_clock, expected_first_ts):
    """Return a factory building deterministic runtimes stopped after the test."""
    from edge.runtime import EdgeRuntime

    def factory(path=tmp_path, clock=fake_clock):
        config = EdgeConfig(db_path=path / "edge.db", tick_seconds=5.0)
        controller = ScenarioController(load_scenario(DEMO_SCENARIO), now_fn=clock)
        store = BufferStore(config.db_path, config.buffer_max_rows, config.batch_size)
        publishers = {
            node.node_id: NullPublisher(node.node_id) for node in config.nodes
        }
        engines = {
            node.node_id: HysteresisEngine(
                flow_zero_sustained_s=config.flow_zero_sustained_s,
                node_inactive_s=config.node_inactive_s,
                derivative_window_s=config.derivative_window_s,
                fault_samples=config.validity_fault_samples,
            )
            for node in config.nodes
        }
        runtime = EdgeRuntime(
            config, controller, store, publishers, engines, now_fn=clock
        )
        request.addfinalizer(lambda: (runtime.stop(), store.close()))
        return runtime

    return factory
