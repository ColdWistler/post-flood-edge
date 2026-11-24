"""Shared pytest fixtures for the edge simulator tests."""

from __future__ import annotations

import logging

import pytest

from edge.models import NodeConfig

# Telemetry delivery is logged at INFO in production; make it visible to
# caplog in tests that do not need to change the global logging configuration.
logging.getLogger("edge.publisher").setLevel(logging.INFO)

NODE_A = NodeConfig("node-a", 27.7172, 85.3240, 1001, 500, 0.0)


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

    def __init__(self, callback_api_version=None) -> None:
        self.callback_api_version = callback_api_version
        self.tls_kwargs: dict = {}
        self.username: str | None = None
        self.password: str | None = None
        self.connect_args: tuple | None = None
        self.loop_started = False
        self.disconnected = False
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

    def loop_start(self) -> None:
        self.loop_started = True

    def disconnect(self) -> None:
        self.disconnected = True

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

    def factory(callback_api_version=None) -> FakeMqttClient:
        client = FakeMqttClient(callback_api_version=callback_api_version)
        clients.append(client)
        return client

    factory.clients = clients  # type: ignore[attr-defined]
    return factory
