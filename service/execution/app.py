"""Flask application for the independently deployed execution service."""

from __future__ import annotations

import hmac
import os
import uuid
from dataclasses import dataclass

from flask import Flask, jsonify, request, send_file

from .errors import AmbiguousMT5Result, ExecutionError
from .idempotency import IdempotencyStore, payload_fingerprint


SCHEMA_VERSION = "1.0.0"


@dataclass
class ExecutionContext:
    adapter: object
    store: IdempotencyStore
    api_key_env: str = "READONLY_API_KEY"
    mutation_enabled: bool = False
    openapi_path: str = "/app/service/execution_openapi.yaml"
    account_policy: str = "DEMO"


def _request_id():
    return request.headers.get("X-Request-ID") or str(uuid.uuid4())


def _success(data, status=200):
    response = jsonify({"schema_version": SCHEMA_VERSION, "data": data})
    response.status_code = status
    response.headers["X-Request-ID"] = _request_id()
    return response


def _error(code, message, status, *, retcode=None):
    item = {"code": code, "message": message, "request_id": _request_id()}
    if retcode is not None:
        item["retcode"] = retcode
    response = jsonify({"schema_version": SCHEMA_VERSION, "error": item})
    response.status_code = status
    response.headers["X-Request-ID"] = item["request_id"]
    return response


def create_execution_app(context):
    app = Flask(__name__)

    @app.before_request
    def authenticate():
        if request.path == "/api/v1/openapi.yaml":
            return None
        expected = os.getenv(context.api_key_env)
        if not expected:
            return _error("auth_not_configured", "Execution API authentication is not configured", 503)
        provided = request.headers.get("X-API-Key")
        if not provided or not hmac.compare_digest(provided, expected):
            return _error("unauthorized", "Missing or invalid API key", 401)
        return None

    @app.errorhandler(ExecutionError)
    def handle_execution_error(error):
        return _error(error.code, error.message, error.status, retcode=error.retcode)

    @app.errorhandler(404)
    def handle_not_found(_error_value):
        return _error("not_found", "Resource was not found", 404)

    @app.errorhandler(405)
    def handle_method_not_allowed(_error_value):
        return _error("method_not_allowed", "Method is not allowed", 405)

    @app.get("/api/v1/health")
    def health():
        try:
            account = context.adapter.account()
            connected = True
            demo = account["mutation_eligible"]
        except ExecutionError:
            connected, demo = False, False
        session_supported = callable(getattr(context.adapter, "session_status", None))
        if session_supported:
            try:
                session = context.adapter.session_status(refresh=True)
            except Exception:
                session = {
                    "state": "disconnected", "ready": False, "generation": 0,
                    "fingerprint": None, "error": "mt5_disconnected",
                }
        else:
            session = {
                "state": "unknown", "ready": False, "generation": 0,
                "fingerprint": None, "error": "account_session_unavailable",
            }
        ready = connected and demo and (
            session.get("ready", False) if session_supported else True
        )
        return _success({
            "status": "healthy" if ready else "unhealthy",
            "ready": ready,
            "mt5_connected": connected,
            "account_mode": "DEMO" if demo else "NON_DEMO_OR_UNKNOWN",
            "account_session": session,
            "mutation_enabled": bool(context.mutation_enabled),
            "account_policy": str(context.account_policy).strip().upper(),
            "mutation_ready": bool(
                context.mutation_enabled and demo
                and (session.get("ready", False) if session_supported else True)
            ),
        }), 200 if ready else 503

    @app.get("/api/v1/account")
    def account():
        return _success(context.adapter.account())

    @app.get("/api/v1/symbols/<symbol>")
    def symbol(symbol):
        return _success(context.adapter.symbol(symbol))

    @app.get("/api/v1/orders")
    def orders():
        return _success(context.adapter.orders())

    @app.get("/api/v1/orders/<order_id>")
    def order(order_id):
        result = context.adapter.order(order_id)
        if result is None:
            raise ExecutionError("order_not_found", "Order was not found", status=404)
        return _success(result)

    @app.get("/api/v1/orders/by-client/<client_order_id>")
    def order_by_client(client_order_id):
        record = context.store.get(client_order_id)
        if record is None:
            raise ExecutionError("order_not_found", "Client order was not found", status=404)
        if record.state in {"pending", "indeterminate"}:
            recovered = context.adapter.order_by_client(client_order_id)
            if recovered is not None:
                record = context.store.finish(client_order_id, "succeeded", {
                    "order_id": recovered["id"],
                    "recovered": True,
                    "order": recovered,
                })
        data = {
            "client_order_id": record.client_order_id,
            "state": record.state,
            "result": record.result,
        }
        return _success(data)

    @app.get("/api/v1/positions")
    def positions():
        return _success(context.adapter.positions())

    @app.get("/api/v1/deals")
    def deals():
        return _success(context.adapter.deals())

    def require_mutation():
        if not context.mutation_enabled:
            raise ExecutionError(
                "mutation_disabled", "Execution mutation is disabled", status=403
            )
        if str(context.account_policy).strip().upper() != "DEMO":
            raise ExecutionError(
                "mutation_policy_denied",
                "Execution mutation account policy must be Demo",
                status=403,
            )
        capture = getattr(context.adapter, "mutation_session", None)
        if callable(capture):
            return capture()
        context.adapter.require_demo()
        return None

    @app.post("/api/v1/orders")
    def create_order():
        expected_session = require_mutation()
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            raise ExecutionError("invalid_request", "JSON object body is required")
        client_order_id = request.headers.get("Idempotency-Key")
        if not client_order_id:
            raise ExecutionError("missing_idempotency_key", "Idempotency-Key is required")
        body_client_id = payload.get("client_order_id", client_order_id)
        if body_client_id != client_order_id:
            raise ExecutionError(
                "idempotency_key_mismatch",
                "Idempotency-Key must equal client_order_id",
            )
        payload["client_order_id"] = client_order_id
        request_id = _request_id()
        record, created = context.store.reserve(
            client_order_id, payload_fingerprint(payload), request_id
        )
        if not created:
            if record.state == "pending":
                raise ExecutionError(
                    "execution_in_progress", "The original request is still in progress", status=409
                )
            return _success({
                "client_order_id": client_order_id,
                "state": record.state,
                "result": record.result,
                "replayed": True,
            })
        try:
            mt5_request, check = context.adapter.preflight(payload)
            validate = getattr(context.adapter, "validate_mutation_session", None)
            if expected_session is not None and callable(validate):
                validate(expected_session)
            if expected_session is None:
                result = context.adapter.send_once(mt5_request)
            else:
                result = context.adapter.send_once(
                    mt5_request, expected_session=expected_session
                )
            result["preflight"] = check
            record = context.store.finish(client_order_id, "succeeded", result)
            return _success({
                "client_order_id": client_order_id,
                "state": record.state,
                "result": record.result,
                "replayed": False,
            }, 201)
        except AmbiguousMT5Result as exc:
            context.store.finish(client_order_id, "indeterminate", {
                "code": exc.code, "message": exc.message
            })
            raise
        except ExecutionError as exc:
            context.store.finish(client_order_id, "failed", {
                "code": exc.code, "message": exc.message, "retcode": exc.retcode
            })
            raise

    @app.post("/api/v1/orders/<order_id>/cancel")
    def cancel_order(order_id):
        expected_session = require_mutation()
        if expected_session is None:
            return _success(context.adapter.cancel(order_id))
        return _success(context.adapter.cancel(order_id, expected_session=expected_session))

    @app.post("/api/v1/positions/<position_id>/close")
    def close_position(position_id):
        expected_session = require_mutation()
        if expected_session is None:
            return _success(context.adapter.close(position_id))
        return _success(context.adapter.close(position_id, expected_session=expected_session))

    @app.get("/api/v1/openapi.yaml")
    def openapi():
        return send_file(context.openapi_path, mimetype="text/yaml")

    return app


__all__ = ["ExecutionContext", "SCHEMA_VERSION", "create_execution_app"]
