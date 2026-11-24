"""Pluggable telemetry sinks: log-only console and opt-in MQTT/TLS."""

from __future__ import annotations

import json
import logging
from typing import Any, Callable, Protocol

logger = logging.getLogger(__name__)

_TELEMETRY_TOPIC = "v1/devices/me/telemetry"
_PUBACK_TIMEOUT_S = 10
_AUTH_RC = 5
_MIN_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 60.0


class Publisher(Protocol):
    """Transport seam used by the flush loop; failures keep rows buffered."""

    is_connected: bool

    def connect(self) -> bool: ...

    def disconnect(self) -> None: ...

    def publish_batch(self, batch: list[dict]) -> bool: ...


class NullPublisher:
    """Zero-credential sink that logs batches instead of sending them."""

    def __init__(self, node_id: str) -> None:
        self.node_id = node_id
        self.is_connected = True
        self._retries_suspended = False

    def connect(self) -> bool:
        self.is_connected = True
        return True

    def disconnect(self) -> None:
        self.is_connected = False

    def publish_batch(self, batch: list[dict]) -> bool:
        if self._retries_suspended:
            logger.warning("AUTH_FAILURE: node=%s retries suspended", self.node_id)
            return False
        ts = [row["ts"] for row in batch]
        logger.info("PUBLISH node=%s rows=%d ts=%s", self.node_id, len(batch), ts)
        return True

    def retry_auth(self) -> None:
        """No credentials are used, but clear a suspension set by the runtime."""
        self._retries_suspended = False


class MqttPublisher:
    """One paho-mqtt client per node, publishing QoS 1 telemetry over TLS."""

    def __init__(
        self,
        node_id: str,
        host: str,
        token: str,
        port: int = 8883,
        ca_cert: str | None = None,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self.node_id = node_id
        self.host = host
        self.token = token
        self.port = port
        self.ca_cert = ca_cert
        self._client_factory = client_factory
        self._client: Any = None
        self.is_connected = False
        self._retries_suspended = False
        self._backoff_s = _MIN_BACKOFF_S

    def connect(self) -> bool:
        """Configure TLS/auth and start the network loop; rc arrives via callback."""
        if self._retries_suspended:
            logger.warning(
                "AUTH_FAILURE: node=%s retries suspended; call retry_auth()",
                self.node_id,
            )
            return False

        try:
            factory = self._client_factory
            if factory is None:
                # Keep the dependency optional: importing this module never
                # requires paho, and the console sink needs no client at all.
                from paho.mqtt.client import Client as factory
            client = factory()
            self._client = client
            client.tls_set(ca_certs=self.ca_cert or None)
            client.username_pw_set(self.token, None)
            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.connect(self.host, self.port)
            client.loop_start()
        except Exception as exc:
            self._client = None
            self.is_connected = False
            self._network_failure(f"connect error={exc}")
            return False
        return True

    def disconnect(self) -> None:
        self.is_connected = False
        if self._client is not None:
            self._client.disconnect()

    def publish_batch(self, batch: list[dict]) -> bool:
        """Publish one node's rows and wait for PUBACK before acknowledging."""
        if self._retries_suspended:
            logger.warning("AUTH_FAILURE: node=%s retries suspended", self.node_id)
            return False
        if not self.is_connected or self._client is None:
            self._network_failure("publish while disconnected")
            return False

        payload = json.dumps(
            [{"ts": row["ts"], "values": row["values"]} for row in batch]
        )
        try:
            info = self._client.publish(_TELEMETRY_TOPIC, payload, qos=1)
            info.wait_for_publish(timeout=_PUBACK_TIMEOUT_S)
        except Exception as exc:
            self._network_failure(f"publish error={exc}")
            return False

        ts = [row["ts"] for row in batch]
        logger.info("PUBLISH node=%s rows=%d ts=%s", self.node_id, len(batch), ts)
        return True

    def retry_auth(self) -> None:
        """Let the operator try again after fixing an access token."""
        self._retries_suspended = False
        self._backoff_s = _MIN_BACKOFF_S
        logger.info("RETRY_AUTH: node=%s", self.node_id)

    def _network_failure(self, reason: str) -> None:
        logger.warning("NETWORK_FAILURE: node=%s %s", self.node_id, reason)
        logger.info("BACKOFF: node=%s seconds=%s", self.node_id, self._backoff_s)
        self._backoff_s = min(_MAX_BACKOFF_S, self._backoff_s * 2)

    def _on_connect(
        self, _client: Any, _userdata: Any, _flags: Any, rc: Any, *_rest: Any
    ) -> None:
        """Accept both paho callback API versions (rc is always the 4th arg)."""
        code = getattr(rc, "value", rc)
        if code == 0:
            self.is_connected = True
            self._backoff_s = _MIN_BACKOFF_S
            logger.info("CONNECTED: node=%s", self.node_id)
        elif code == _AUTH_RC:
            self.is_connected = False
            self._retries_suspended = True
            logger.warning(
                "AUTH_FAILURE: node=%s rc=%s; retries suspended", self.node_id, code
            )
        else:
            self.is_connected = False
            self._network_failure(f"connect rc={code}")

    def _on_disconnect(self, _client: Any, _userdata: Any, *_args: Any) -> None:
        """paho v1 passes rc alone; v2 passes flags, reason code and properties."""
        self.is_connected = False
        rc = _args[0] if len(_args) == 1 else (_args[1] if len(_args) > 1 else 0)
        code = getattr(rc, "value", rc)
        if code != 0:
            self._network_failure(f"disconnect rc={code}")
