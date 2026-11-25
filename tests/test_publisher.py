"""Tests for the console and MQTT/TLS publisher seam."""

from __future__ import annotations

import json

from edge.publisher import MqttPublisher, NullPublisher


def test_null_publisher_logs_and_acks(caplog):
    p = NullPublisher("node-a")
    assert p.connect() and p.publish_batch([{"ts": 1, "values": {}}])
    assert "PUBLISH" in caplog.text


def test_auth_failure_suspends_retries_and_logs(fake_client_factory, caplog):
    p = MqttPublisher("node-a", "host", "bad", client_factory=fake_client_factory)
    p.connect()
    fake = p._client
    fake.fire_connect(5)
    assert p.is_connected is False
    assert p._retries_suspended is True
    assert "AUTH_FAILURE" in caplog.text


def test_auth_reason_code_135_suspends_retries(fake_client_factory, caplog):
    p = MqttPublisher("node-a", "host", "bad", client_factory=fake_client_factory)
    p.connect()
    p._client.fire_connect(135)
    assert p.is_connected is False
    assert p._retries_suspended is True
    assert "AUTH_FAILURE" in caplog.text
    assert "NETWORK_FAILURE" not in caplog.text


def test_network_failure_logs_and_backs_off(fake_client_factory, caplog):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect()
    p._client.fire_connect(1)
    assert "NETWORK_FAILURE" in caplog.text
    assert p._backoff_s in (1.0, 2.0)


def test_null_publisher_logs_node_rows_and_timestamps(caplog):
    p = NullPublisher("node-b")
    assert p.is_connected is True
    assert p.publish_batch(
        [
            {"id": 1, "ts": 100, "values": {"node_id": "node-b"}},
            {"id": 2, "ts": 200, "values": {"node_id": "node-b"}},
        ]
    )
    assert "PUBLISH node=node-b rows=2 ts=[100, 200]" in caplog.text
    p.disconnect()
    assert p.is_connected is False


def test_null_publisher_honours_auth_suspension(caplog):
    p = NullPublisher("node-a")
    p._retries_suspended = True
    assert p.publish_batch([{"id": 1, "ts": 1, "values": {}}]) is False
    assert "AUTH_FAILURE" in caplog.text
    assert "PUBLISH" not in caplog.text
    p.retry_auth()
    assert p.publish_batch([{"id": 1, "ts": 1, "values": {}}]) is True


def test_connect_configures_tls_auth_host_and_loop(fake_client_factory):
    p = MqttPublisher(
        "node-a",
        "broker.example.org",
        "token-a",
        ca_cert="/etc/ssl/ca.pem",
        client_factory=fake_client_factory,
    )
    assert p.connect() is True
    fake = p._client
    assert fake.tls_kwargs == {"ca_certs": "/etc/ssl/ca.pem"}
    assert (fake.username, fake.password) == ("token-a", None)
    assert fake.connect_args == ("broker.example.org", 8883)
    assert fake.client_id.startswith("pf-node-a-")
    assert len(fake.client_id) <= 23  # MQTT 3.1.1 client id limit
    assert fake.reconnect_on_failure is False
    assert fake.loop_started is True


def test_connect_passes_callback_api_version(fake_client_factory):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect()
    # paho 2.0 requires this argument; 2.1+ expose it as an enum, older fakes as a string.
    version = p._client.callback_api_version
    assert getattr(version, "name", version) == "VERSION1"


def test_connect_without_ca_cert_uses_system_trust(fake_client_factory):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect()
    assert p._client.tls_kwargs == {"ca_certs": None}


def test_connect_uses_unique_client_id_per_node(fake_client_factory):
    """Concurrent node clients must not share paho's empty default client id."""
    ids = []
    for node_id in ("node-a", "node-b", "node-c"):
        p = MqttPublisher(node_id, "host", "tok", client_factory=fake_client_factory)
        assert p.connect() is True
        ids.append(p._client.client_id)
        assert p._client.reconnect_on_failure is False
    assert all(item.startswith("pf-node-") for item in ids)
    assert all(len(item) <= 23 for item in ids)  # MQTT 3.1.1 client id limit
    assert len(set(ids)) == 3


def test_publish_batch_sends_json_telemetry_with_qos_and_waits(
    fake_client_factory, caplog
):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect()
    p._client.fire_connect(0)
    assert p.is_connected is True
    batch = [
        {"id": 7, "ts": 1000, "values": {"node_id": "node-a", "ph": 7.1}},
        {"id": 8, "ts": 2000, "values": {"node_id": "node-a", "ph": 7.2}},
    ]

    assert p.publish_batch(batch) is True

    topic, payload, qos = p._client.published[0]
    assert topic == "v1/devices/me/telemetry"
    assert qos == 1
    assert json.loads(payload) == [
        {"ts": row["ts"], "values": row["values"]} for row in batch
    ]
    assert p._client.wait_timeouts == [10]
    assert p._client.is_published_calls == 1
    assert "PUBLISH node=node-a rows=2" in caplog.text


def test_fake_wait_for_publish_matches_paho_contract(fake_client_factory):
    client = fake_client_factory()
    info = client.publish("topic", "[]", qos=1)

    client.ack_ok = False
    assert info.wait_for_publish(timeout=10) is None
    assert info.is_published() is False

    client.ack_ok = True
    assert info.wait_for_publish(timeout=10) is None
    assert info.is_published() is True
    assert client.wait_timeouts == [10, 10]


def test_network_failures_double_backoff_and_cap_at_60(fake_client_factory):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect()
    p._client.fire_connect(1)
    assert p._backoff_s == 2.0
    for _ in range(10):
        p._client.fire_connect(1)
    assert p._backoff_s == 60.0


def test_successful_connect_resets_backoff(fake_client_factory):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect()
    p._client.fire_connect(1)
    assert p._backoff_s == 2.0
    p._client.fire_connect(0)
    assert p.is_connected is True
    assert p._backoff_s == 1.0


def test_suspended_auth_skips_publish_until_retry_auth(fake_client_factory):
    p = MqttPublisher("node-a", "host", "bad", client_factory=fake_client_factory)
    p.connect()
    p._client.fire_connect(5)
    batch = [{"id": 1, "ts": 1, "values": {"node_id": "node-a"}}]

    assert p.publish_batch(batch) is False
    assert p._client.published == []
    assert p.connect() is False
    assert len(fake_client_factory.clients) == 1

    p.retry_auth()
    assert p._retries_suspended is False
    assert p.connect() is True
    assert len(fake_client_factory.clients) == 2


def test_missing_puback_fails_and_logs_network_failure(fake_client_factory, caplog):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect()
    p._client.fire_connect(0)
    p._client.ack_ok = False

    assert p.publish_batch([{"id": 1, "ts": 1, "values": {}}]) is False
    assert "NETWORK_FAILURE" in caplog.text
    assert "PUBLISH" not in caplog.text
    assert p._client.wait_timeouts == [10]
    assert p._client.is_published_calls == 1
    assert p._backoff_s == 2.0


def test_unexpected_broker_disconnect_logs_network_failure(fake_client_factory, caplog):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect()
    p._client.fire_connect(0)
    p._client.fire_disconnect(1)
    assert p.is_connected is False
    assert "NETWORK_FAILURE" in caplog.text


def test_disconnect_stops_loop_connection_state(fake_client_factory):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect()
    p._client.fire_connect(0)
    p.disconnect()
    assert p._client.disconnected is True
    assert p.is_connected is False


def test_connect_sets_paho_reconnect_delay_bounds(fake_client_factory):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    assert p.connect() is True
    assert p._client.reconnect_delays == [(1.0, 60.0)]


def test_reconnect_stops_old_client_before_creating_new_one(fake_client_factory):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    assert p.connect() is True
    old = p._client
    assert p.connect() is True
    new = p._client

    assert new is not old
    assert old.loop_stopped is True
    assert old.disconnected is True
    events = fake_client_factory.events
    assert events.index(("loop_stop", id(old))) < events.index(("disconnect", id(old)))
    assert events.index(("disconnect", id(old))) < events.index(("created", id(new)))


def test_large_batch_is_split_below_broker_payload_cap(fake_client_factory):
    p = MqttPublisher("node-a", "host", "tok", client_factory=fake_client_factory)
    p.connect()
    p._client.fire_connect(0)
    batch = [
        {
            "id": i,
            "ts": 1000 + i,
            "values": {"node_id": "node-a", "note": "x" * 100},
        }
        for i in range(20)
    ]

    assert p.publish_batch(batch) is True
    payloads = [payload for _, payload, _ in p._client.published]
    assert len(payloads) > 1
    assert all(len(payload) <= 1800 for payload in payloads)
    rows = [row for payload in payloads for row in json.loads(payload)]
    assert [row["ts"] for row in rows] == [row["ts"] for row in batch]
    assert p._client.wait_timeouts == [10] * len(payloads)
    assert p._client.is_published_calls == len(payloads)


def test_second_chunk_without_puback_fails_whole_batch(fake_client_factory, caplog):
    BaseClient = type(fake_client_factory())

    class AckThenFailClient(BaseClient):
        def publish(self, topic, payload, qos=0):
            info = super().publish(topic, payload, qos)
            if len(self.published) == 2:
                self.ack_ok = False
            return info

    def factory(**kwargs):
        return AckThenFailClient(**kwargs)

    p = MqttPublisher("node-a", "host", "tok", client_factory=factory)
    p.connect()
    p._client.fire_connect(0)
    batch = [
        {
            "id": i,
            "ts": 1000 + i,
            "values": {"node_id": "node-a", "note": "x" * 100},
        }
        for i in range(20)
    ]
    assert len(p._payload_chunks(batch)) > 1

    assert p.publish_batch(batch) is False
    assert "NETWORK_FAILURE" in caplog.text
    assert "PUBLISH" not in caplog.text
    assert len(p._client.published) == 2
