from datetime import datetime, timedelta, timezone
import ast
from pathlib import Path

import service.api_gateway as gateway


def _event(received_at=None):
    return {
        "version": 1,
        "type": "tick",
        "symbol": "XAUUSDm",
        "bid": 2300.0,
        "ask": 2300.2,
        "last": 2300.1,
        "volume": 3,
        "source_time": "2026-09-28T10:00:00+00:00",
        "received_at": received_at or datetime.now(timezone.utc).isoformat(),
    }


def test_tick_rest_returns_snapshot_without_polling_mt5(monkeypatch):
    event = _event()
    monkeypatch.setattr(gateway.tick_consumer, "get", lambda symbol: event)
    monkeypatch.setattr(gateway.tick_consumer, "is_fresh", lambda tick, max_age: True)
    response = gateway.app.test_client().get("/api/v1/ticks/XAUUSDm")
    assert response.status_code == 200
    assert response.json["bid"] == 2300.0
    assert response.json["time"] == event["source_time"]


def test_tick_rest_returns_503_for_stale_snapshot(monkeypatch):
    event = _event((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat())
    monkeypatch.setattr(gateway.tick_consumer, "get", lambda symbol: event)
    monkeypatch.setattr(gateway.tick_consumer, "is_fresh", lambda tick, max_age: False)
    response = gateway.app.test_client().get("/api/v1/ticks/XAUUSDm")
    assert response.status_code == 503
    assert response.json == {
        "error": "Tick data is stale",
        "symbol": "XAUUSDm",
        "last_received_at": event["received_at"],
    }


def test_websocket_subscription_immediately_receives_fresh_snapshot(monkeypatch):
    event = _event()
    monkeypatch.setattr(gateway.tick_consumer, "get", lambda symbol: event)
    monkeypatch.setattr(gateway.tick_consumer, "is_fresh", lambda tick, max_age: True)
    client = gateway.socketio.test_client(gateway.app)
    client.get_received()
    client.emit("subscribe", {"symbol": "XAUUSDm"})
    received = client.get_received()
    assert [item["name"] for item in received] == ["subscribed", "tick"]
    assert received[1]["args"][0]["bid"] == 2300.0
    client.disconnect()


def test_websocket_unsubscribe_leaves_symbol_room(monkeypatch):
    monkeypatch.setattr(gateway.tick_consumer, "get", lambda symbol: None)
    client = gateway.socketio.test_client(gateway.app)
    client.get_received()
    client.emit("subscribe", {"symbol": "XAUUSDm"})
    client.get_received()

    gateway.socketio.emit("tick", {"symbol": "XAUUSDm", "bid": 1}, room="XAUUSDm")
    assert [item["name"] for item in client.get_received()] == ["tick"]

    client.emit("unsubscribe", {"symbol": "XAUUSDm"})
    client.get_received()
    gateway.socketio.emit("tick", {"symbol": "XAUUSDm", "bid": 2}, room="XAUUSDm")
    assert client.get_received() == []
    client.disconnect()


def test_gateway_has_no_tick_fetcher_or_mt5_tick_polling():
    for path in (Path(gateway.__file__).parent / "gateway").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assert "TickFetcher" not in source
        assert not any(isinstance(node, ast.Attribute) and node.attr == "symbol_info_tick"
                       for node in ast.walk(tree))
