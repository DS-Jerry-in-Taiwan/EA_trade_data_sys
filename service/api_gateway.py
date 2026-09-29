"""Compatibility executable for the modular Gateway entrypoint."""

import signal

from service.entrypoints.api_gateway import (
    _handle_shutdown_signal,
    account_svc,
    app,
    history_query_svc,
    mt5_client,
    run_gateway as _run_gateway,
    socketio,
    tick_consumer,
)


def run_gateway():
    _run_gateway()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    run_gateway()
