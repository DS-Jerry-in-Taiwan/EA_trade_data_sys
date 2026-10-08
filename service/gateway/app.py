"""Application factory for the sole public trade-data endpoint."""

import time
from dataclasses import dataclass
from datetime import datetime

from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

from service.gateway.routes import health, market_data, metrics as metrics_routes, trade_query
from service.gateway.websocket import public_tick, register_websocket_handlers


@dataclass
class GatewayContext:
    tick_consumer: object
    history_query_service: object
    account_service: object
    metrics: object
    status_reader: object
    config_loader: object
    tick_symbols: list
    symbol_names: list
    tick_max_age: float
    tick_status_path: str
    tick_status_max_age: float
    history_status_path: str
    history_status_max_age: float
    openapi_path: str = "/app/service/openapi.yaml"
    history_storage_id: str | None = None

    def fresh_tick(self, symbol):
        tick = self.tick_consumer.get(symbol)
        return tick if self.tick_consumer.is_fresh(tick, self.tick_max_age) else None


@dataclass
class GatewayApplication:
    app: Flask
    socketio: SocketIO
    context: GatewayContext


def create_app(context):
    app = Flask(__name__)
    CORS(app)
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
    started_at = time.time()

    def update_component_metrics(payload=None):
        if payload is None:
            payload = health.health_payload(context)
        tick, history = payload["tick_service"], payload["history_service"]
        for component, value in (("tick_service", tick), ("history_service", history)):
            current = value.get("state", "unknown")
            for state in ("healthy", "syncing", "degraded", "unhealthy",
                          "starting", "stopped", "unknown"):
                context.metrics.component_state.labels(component=component, state=state).set(
                    1 if state == current else 0
                )
            if "age_seconds" in value:
                context.metrics.component_status_age_seconds.labels(component=component).set(
                    value["age_seconds"]
                )
        context.metrics.gateway_ready.set(1 if payload["ready"] else 0)
        context.metrics.tick_ipc_connected.set(1 if context.tick_consumer.connected else 0)
        context.metrics.fresh_tick_symbols.set(len(payload["fresh_symbols"]))

    def on_tick(event):
        context.metrics.mt5_tick_bid.labels(symbol=event["symbol"]).set(event["bid"])
        context.metrics.mt5_tick_ask.labels(symbol=event["symbol"]).set(event["ask"])
        try:
            source_time = datetime.fromisoformat(
                (event.get("source_time") or event["received_at"]).replace("Z", "+00:00")
            )
            context.metrics.mt5_last_tick_timestamp.labels(symbol=event["symbol"]).set(
                source_time.timestamp()
            )
        except (KeyError, TypeError, ValueError):
            pass
        context.metrics.mt5_connected.set(1)
        socketio.emit("tick", public_tick(event), room=event["symbol"])

    context.tick_consumer.on_tick = on_tick
    metrics_routes.install_request_metrics(app, context.metrics)
    app.register_blueprint(market_data.create_blueprint(
        context.tick_consumer, context.tick_max_age, context.history_query_service,
        context.account_service, context.symbol_names, context.openapi_path,
    ))
    app.register_blueprint(trade_query.create_blueprint(
        context.account_service,
        context.config_loader,
        context.metrics.mt5_deal_mapping_errors_total,
    ))
    app.register_blueprint(health.create_blueprint(context, update_component_metrics))
    app.register_blueprint(metrics_routes.create_blueprint(
        context, context.metrics, update_component_metrics, started_at
    ))
    handlers = register_websocket_handlers(
        socketio, context.tick_consumer, context.tick_max_age
    )
    app.extensions["gateway_context"] = context
    app.extensions["gateway_ws_handlers"] = handlers
    return GatewayApplication(app=app, socketio=socketio, context=context)


__all__ = ["GatewayApplication", "GatewayContext", "create_app"]
