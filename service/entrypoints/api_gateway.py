"""Composition root and executable entrypoint for the public Gateway."""

import atexit
import os
import signal
from pathlib import Path

import yaml

from service.gateway.app import GatewayContext, create_app
from service.history.query_service import HistoryQueryService
from service.history.repository import HistoryRepository
from service.infrastructure.ipc.tick_protocol import DEFAULT_SOCKET_PATH
from service.infrastructure.mt5.client import MT5Client
from service.infrastructure.observability import metrics
from service.infrastructure.status.component_status import DEFAULT_STATUS_DIR, read_status
from service.realtime.consumer import TickConsumer
from service.trade_query.account_service import AccountService


def _config_path():
    deployed = Path("/app/service/config/settings.yaml")
    fallback = Path(__file__).resolve().parents[1] / "config" / "settings.yaml"
    return os.getenv("TRADE_DATA_CONFIG", str(deployed if deployed.exists() else fallback))


def _read_config(path):
    with open(path, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def build_gateway(config_path=None):
    config_path = config_path or _config_path()

    def config_loader():
        cfg = _read_config(config_path)
        return {"api_gateway": cfg.get("api_gateway", {}),
                "trade_query": cfg.get("trade_query", {})}

    cfg = _read_config(config_path)
    tick_cfg, history_cfg = cfg.get("tick_service", {}), cfg.get("history_service", {})
    tick_interval = float(tick_cfg.get("update_interval_seconds", 60))
    history_interval = float(history_cfg.get("update_interval_seconds", 60))
    client = MT5Client()
    account_service = AccountService(config_path=config_path, mt5_client=client)
    consumer = TickConsumer(os.getenv(
        "TICK_SOCKET_PATH", tick_cfg.get("socket_path", DEFAULT_SOCKET_PATH)
    ))
    context = GatewayContext(
        tick_consumer=consumer,
        history_query_service=HistoryQueryService(HistoryRepository()),
        account_service=account_service,
        metrics=metrics,
        status_reader=read_status,
        config_loader=config_loader,
        tick_symbols=list(tick_cfg.get("symbols", [])),
        symbol_names=[item["name"] for item in history_cfg.get("symbols", [])],
        tick_max_age=float(tick_cfg.get("max_age_seconds", max(tick_interval * 2, 5))),
        tick_status_path=os.getenv("TICK_STATUS_PATH", tick_cfg.get(
            "status_path", os.path.join(DEFAULT_STATUS_DIR, "tick-status.json"))),
        tick_status_max_age=float(tick_cfg.get(
            "status_max_age_seconds", max(tick_interval * 2, 5))),
        history_status_path=os.getenv("HISTORY_STATUS_PATH", history_cfg.get(
            "status_path", os.path.join(DEFAULT_STATUS_DIR, "history-status.json"))),
        history_status_max_age=float(history_cfg.get(
            "status_max_age_seconds", max(history_interval * 3, 30))),
    )
    return create_app(context), client


gateway, mt5_client = build_gateway()
app, socketio = gateway.app, gateway.socketio
tick_consumer = gateway.context.tick_consumer
history_query_svc = gateway.context.history_query_service
account_svc = gateway.context.account_service
atexit.register(tick_consumer.stop)
atexit.register(mt5_client.shutdown)


def _handle_shutdown_signal(signum, _frame):
    raise SystemExit(128 + signum)


def run_gateway():
    tick_consumer.start()
    try:
        socketio.run(app, host=os.getenv("API_GATEWAY_HOST", "0.0.0.0"),
                     port=int(os.getenv("API_GATEWAY_PORT", 8090)),
                     allow_unsafe_werkzeug=True)
    finally:
        try:
            tick_consumer.stop()
        finally:
            mt5_client.shutdown()


def main():
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    run_gateway()


if __name__ == "__main__":
    main()
