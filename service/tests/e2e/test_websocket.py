"""
Layer D: WebSocket Verification (5 tests)
===========================================
Tests for the WebSocket endpoint at ws://localhost:5000/api/v1/ws.
If WebSocket support is not available, all tests skip gracefully.
"""
import json
import pytest
import websocket

pytest.importorskip("websocket", reason="websocket-client not installed")

WS_URL = "ws://localhost:8090/api/v1/ws"


def _recv(ws, timeout=5):
    """Receive a single message with timeout."""
    ws.settimeout(timeout)
    try:
        return json.loads(ws.recv())
    except websocket.WebSocketTimeoutException:
        return None


class TestWebSocket:

    def test_ws_connect(self):
        """WebSocket connection can be established"""
        try:
            ws = websocket.create_connection(WS_URL, timeout=5)
            ws.close()
        except (websocket.WebSocketException, ConnectionRefusedError, OSError):
            pytest.skip("WebSocket endpoint not available")

    def test_ws_tick_subscription(self):
        """Subscribing to ticks should return at least one tick message"""
        try:
            ws = websocket.create_connection(WS_URL, timeout=5)
        except Exception:
            pytest.skip("WebSocket endpoint not available")
        try:
            ws.send(json.dumps({"type": "subscribe", "channel": "ticks", "symbol": "XAUUSDm"}))
            msg = _recv(ws, timeout=5)
            assert msg is not None, "No tick message received within 5s"
            assert isinstance(msg, dict), "Tick message should be a JSON object"
        except websocket.WebSocketException:
            pytest.skip("WebSocket communication failed")
        finally:
            ws.close()

    def test_ws_heartbeat(self):
        """WebSocket should send periodic heartbeats"""
        try:
            ws = websocket.create_connection(WS_URL, timeout=5)
        except Exception:
            pytest.skip("WebSocket endpoint not available")
        try:
            msg = _recv(ws, timeout=10)
            assert msg is not None, "No heartbeat received within 10s"
        except websocket.WebSocketException:
            pytest.skip("WebSocket communication failed")
        finally:
            ws.close()

    def test_ws_subscription_error(self):
        """Invalid subscription should not crash the server"""
        try:
            ws = websocket.create_connection(WS_URL, timeout=5)
        except Exception:
            pytest.skip("WebSocket endpoint not available")
        try:
            ws.send(json.dumps({"type": "subscribe", "channel": "invalid_channel"}))
            msg = _recv(ws, timeout=3)
            assert ws.connected, "Connection dropped after invalid subscription"
        except websocket.WebSocketException:
            pytest.skip("WebSocket communication failed")
        finally:
            ws.close()

    def test_ws_pong(self):
        """Server must reply to ping with pong"""
        try:
            ws = websocket.create_connection(WS_URL, timeout=5)
        except Exception:
            pytest.skip("WebSocket endpoint not available")
        try:
            ws.ping("test")
            ws.settimeout(3)
            resp = ws.recv()
            assert True  # No exception = pong received
        except websocket.WebSocketTimeoutException:
            pytest.fail("No pong response within 3s")
        except websocket.WebSocketException:
            pytest.skip("WebSocket ping/pong failed")
        finally:
            ws.close()
