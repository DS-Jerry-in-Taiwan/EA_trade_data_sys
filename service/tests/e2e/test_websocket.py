"""Socket.IO lifecycle checks against API Gateway's sole external endpoint."""

import threading

import pytest

socketio = pytest.importorskip("socketio", reason="python-socketio not installed")

BASE_URL = "http://localhost:8090"


def _connected_client(events):
    client = socketio.Client(reconnection=False, logger=False, engineio_logger=False)

    @client.on("connected")
    def on_connected(payload):
        events["connected_payload"] = payload
        events["connected"].set()

    @client.on("subscribed")
    def on_subscribed(payload):
        events["subscribed_payload"] = payload
        events["subscribed"].set()

    @client.on("tick")
    def on_tick(payload):
        events["tick_payload"] = payload
        events["tick"].set()

    try:
        client.connect(BASE_URL, transports=["websocket"], wait_timeout=5)
    except (socketio.exceptions.ConnectionError, OSError):
        pytest.skip("Gateway Socket.IO endpoint not available")
    return client


@pytest.fixture
def live_socketio_client():
    events = {
        "connected": threading.Event(),
        "subscribed": threading.Event(),
        "tick": threading.Event(),
    }
    client = _connected_client(events)
    yield client, events
    if client.connected:
        client.disconnect()


class TestWebSocketLifecycle:
    def test_connects_through_gateway(self, live_socketio_client):
        client, events = live_socketio_client
        assert client.connected
        assert events["connected"].wait(3), "Gateway did not emit connected event"
        assert "message" in events["connected_payload"]

    def test_subscribe_receives_ack_and_fresh_snapshot(self, live_socketio_client):
        client, events = live_socketio_client
        client.emit("subscribe", {"symbol": "XAUUSDm"})

        assert events["subscribed"].wait(3), "Gateway did not acknowledge subscription"
        assert events["subscribed_payload"] == {"symbol": "XAUUSDm"}
        assert events["tick"].wait(5), "Gateway did not send the fresh Tick snapshot"
        tick = events["tick_payload"]
        assert tick["version"] == 1
        assert tick["type"] == "tick"
        assert tick["symbol"] == "XAUUSDm"
        for field in ("bid", "ask", "received_at", "time"):
            assert field in tick

    def test_unsubscribe_does_not_disconnect_transport(self, live_socketio_client):
        client, events = live_socketio_client
        client.emit("subscribe", {"symbol": "XAUUSDm"})
        assert events["subscribed"].wait(3)

        client.emit("unsubscribe", {"symbol": "XAUUSDm"})
        client.sleep(0.2)
        assert client.connected
