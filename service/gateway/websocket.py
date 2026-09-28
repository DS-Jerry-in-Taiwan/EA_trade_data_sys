"""Gateway WebSocket subscription lifecycle."""

from flask_socketio import emit, join_room, leave_room


def public_tick(event):
    result = dict(event)
    result["time"] = event.get("source_time") or event.get("received_at")
    return result


def register_websocket_handlers(socketio, tick_consumer, max_age_seconds):
    def fresh_tick(symbol):
        tick = tick_consumer.get(symbol)
        return tick if tick_consumer.is_fresh(tick, max_age_seconds) else None

    @socketio.on("connect")
    def handle_connect():
        emit("connected", {"message": "Connected to MT5 API Gateway"})

    @socketio.on("subscribe")
    def handle_subscribe(data):
        symbol = (data or {}).get("symbol", "")
        if symbol:
            join_room(symbol)
            emit("subscribed", {"symbol": symbol})
            tick = fresh_tick(symbol)
            if tick:
                emit("tick", public_tick(tick))

    @socketio.on("unsubscribe")
    def handle_unsubscribe(data):
        symbol = (data or {}).get("symbol", "")
        if symbol:
            leave_room(symbol)

    return {"connect": handle_connect, "subscribe": handle_subscribe,
            "unsubscribe": handle_unsubscribe}


__all__ = ["public_tick", "register_websocket_handlers"]
