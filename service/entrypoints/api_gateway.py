"""Composition root and executable entrypoint for the public Gateway."""

import atexit
import signal

from service.config import load_settings, resolve_config_path
from service.gateway.app import GatewayContext, create_app
from service.history.query_service import HistoryQueryService
from service.history.repository import HistoryRepository
from service.infrastructure.ipc.tick_protocol import DEFAULT_SOCKET_PATH
from service.infrastructure.mt5.client import MT5Client
from service.infrastructure.observability import metrics
from service.infrastructure.status.component_status import read_status
from service.realtime.consumer import TickConsumer
from service.trade_query.account_service import AccountService


def _config_path():
    return str(resolve_config_path())


def build_gateway(config_path=None):
    settings = load_settings(config_path or _config_path())
    tick_cfg, history_cfg = settings.tick_service, settings.history_service
    tick_interval = tick_cfg.update_interval_seconds
    history_interval = history_cfg.update_interval_seconds
    client = MT5Client(symbol_aliases=settings.symbol_aliases)
    account_service = AccountService(settings=settings, mt5_client=client)
    consumer = TickConsumer(tick_cfg.socket_path or DEFAULT_SOCKET_PATH)
    context = GatewayContext(
        tick_consumer=consumer,
        history_query_service=HistoryQueryService(HistoryRepository()),
        account_service=account_service,
        metrics=metrics,
        status_reader=read_status,
        config_loader=lambda: settings,
        tick_symbols=list(tick_cfg.symbols),
        symbol_names=[item.name for item in history_cfg.symbols],
        tick_max_age=float(tick_cfg.max_age_seconds or max(tick_interval * 2, 5)),
        tick_status_path=tick_cfg.status_path,
        tick_status_max_age=float(
            tick_cfg.status_max_age_seconds or max(tick_interval * 2, 5)
        ),
        history_status_path=history_cfg.status_path,
        history_status_max_age=float(max(history_interval * 3, 30)),
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
        settings = gateway.context.config_loader()
        socketio.run(app, host=settings.api_gateway.host,
                     port=settings.api_gateway.port,
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
