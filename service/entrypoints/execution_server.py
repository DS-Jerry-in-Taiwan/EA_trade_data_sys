"""Composition root for the separately deployed execution API."""

import atexit
import os

from service.config import load_settings
from service.execution.app import ExecutionContext, create_execution_app
from service.execution.idempotency import IdempotencyStore
from service.execution.mt5_adapter import MT5ExecutionAdapter
from service.infrastructure.mt5.client import MT5Client


def _enabled(value):
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def build_execution_server():
    settings = load_settings()
    client = MT5Client()
    symbols = [item.name for item in settings.history_service.symbols]
    adapter = MT5ExecutionAdapter(client, symbols)
    store = IdempotencyStore(
        os.getenv("EXECUTION_IDEMPOTENCY_DB", "/app/runtime/execution/idempotency.sqlite3")
    )
    context = ExecutionContext(
        adapter=adapter,
        store=store,
        api_key_env=os.getenv("EXECUTION_API_KEY_ENV", "READONLY_API_KEY"),
        mutation_enabled=_enabled(os.getenv("EXECUTION_MUTATION_ENABLED", "false")),
        account_policy=os.getenv("EXECUTION_ACCOUNT_POLICY", "DEMO"),
    )
    return create_execution_app(context), client


app, mt5_client = build_execution_server()
atexit.register(mt5_client.shutdown)


def main():
    try:
        app.run(
            host=os.getenv("EXECUTION_HOST", "0.0.0.0"),
            port=int(os.getenv("EXECUTION_PORT", "8091")),
            threaded=True,
        )
    finally:
        mt5_client.shutdown()


if __name__ == "__main__":
    main()
