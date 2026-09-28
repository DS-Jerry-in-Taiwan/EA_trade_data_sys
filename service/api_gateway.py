"""Compatibility executable for the modular Gateway entrypoint."""

import os
import signal

from service.entrypoints.api_gateway import (
    _handle_shutdown_signal,
    account_svc,
    app,
    history_query_svc,
    mt5_client,
    socketio,
    tick_consumer,
)


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


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    run_gateway()
