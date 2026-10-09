"""Flask application for the independently deployed execution service."""

from __future__ import annotations

import hmac
import os
import uuid
from dataclasses import dataclass

from flask import Flask, g, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

from service.domain.trades.errors import DealMappingError
from service.infrastructure.mt5.session import AccountSessionTransition

from .errors import AmbiguousMT5Result, ExecutionError, parse_ticket_id
from .contracts import normalize_order_request, order_result, recovered_order_result, restore_order_intent
from .idempotency import IdempotencyStore, payload_fingerprint


SCHEMA_VERSION = 1


@dataclass
class ExecutionContext:
    adapter: object
    store: IdempotencyStore
    api_key_env: str = "READONLY_API_KEY"
    mutation_enabled: bool = False
    openapi_path: str = "/app/service/execution_openapi.yaml"
    account_policy: str = "DEMO"


def _request_id():
    if not getattr(g, "execution_request_id", None):
        g.execution_request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
    return g.execution_request_id


def _success(data, status=200):
    response = jsonify({"schema_version": SCHEMA_VERSION, "data": data})
    response.status_code = status
    response.headers["X-Request-ID"] = _request_id()
    return response


def _error(code, message, status, *, retcode=None):
    item = {"code": code, "message": message, "request_id": _request_id()}
    response = jsonify({"schema_version": SCHEMA_VERSION, "error": item})
    response.status_code = status
    response.headers["X-Request-ID"] = item["request_id"]
    if retcode is not None:
        response.headers["X-MT5-Retcode"] = str(retcode)
    return response


def create_execution_app(context):
    app = Flask(__name__)
    bind_store = getattr(context.adapter, "bind_idempotency_store", None)
    if callable(bind_store):
        bind_store(context.store)

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

    @app.errorhandler(ConnectionError)
    def handle_mt5_unavailable(_error_value):
        return _error("mt5_unavailable", "MT5 is unavailable", 503)

    @app.errorhandler(AccountSessionTransition)
    def handle_account_session_transition(_error_value):
        return _error(
            "account_session_transition",
            "MT5 account session transition requires reconciliation",
            503,
        )

    @app.errorhandler(DealMappingError)
    def handle_deal_mapping_error(_error_value):
        return _error(
            "mt5_deal_mapping_error", "MT5 deal data could not be normalized", 502
        )

    @app.errorhandler(404)
    def handle_not_found(_error_value):
        return _error("not_found", "Resource was not found", 404)

    @app.errorhandler(405)
    def handle_method_not_allowed(_error_value):
        return _error("method_not_allowed", "Method is not allowed", 405)

    @app.errorhandler(HTTPException)
    def handle_http_error(error):
        return _error("http_error", error.name, error.code)

    @app.errorhandler(Exception)
    def handle_unexpected_error(error):
        app.logger.error("Unhandled execution API error (%s)", type(error).__name__)
        return _error("internal_error", "Execution API encountered an internal error", 500)

    @app.get("/api/v1/health")
    def health():
        session_supported = callable(getattr(context.adapter, "session_status", None))
        if session_supported:
            try:
                session = context.adapter.session_status(refresh=True)
            except Exception:
                session = {
                    "state": "disconnected", "ready": False, "generation": 0,
                    "fingerprint": None, "error": "mt5_disconnected",
                }
            fingerprint = session.get("fingerprint") or {}
            mode = fingerprint.get("account_mode")
            connected = session.get("state") not in {"disconnected", "unknown"}
            demo = mode == "DEMO"
        else:
            try:
                account = context.adapter.account()
                connected = True
                demo = account["mutation_eligible"]
            except ExecutionError:
                connected, demo = False, False
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
                and str(context.account_policy).strip().upper() == "DEMO"
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
        order_id = str(parse_ticket_id(order_id, resource="order"))
        result = context.adapter.order(order_id)
        if result is None:
            raise ExecutionError("order_not_found", "Order was not found", status=404)
        return _success(result)

    @app.get("/api/v1/orders/by-client/<client_order_id>")
    def order_by_client(client_order_id):
        record = context.store.get(client_order_id)
        if record is None:
            raise ExecutionError("order_not_found", "Client order was not found", status=404)
        if record.state == "failed":
            raise ExecutionError("order_not_found", "Client order was not accepted", status=404)
        validate_recovery = getattr(context.adapter, "validate_recovery_session", None)
        if callable(validate_recovery):
            validate_recovery(record)
        recovered = context.adapter.order_by_client(client_order_id)
        if recovered is None:
            raise ExecutionError("order_not_found", "Client order acceptance is not yet known", status=404)
        recovered = restore_order_intent(recovered, record)
        if record.state in {"pending", "indeterminate"}:
            response = recovered_order_result(record, recovered)
            result = {
                "order_id": recovered["broker_order_id"], "recovered": True, "order": recovered,
                "response": response,
            }
            retcode = (record.result or {}).get("retcode")
            if retcode is not None:
                result["retcode"] = retcode
                response["broker_retcode"] = str(retcode)
            rejected = response["status"] == "rejected"
            if rejected:
                result.update(code="mt5_order_rejected", message=response["reason"], status=422)
            context.store.finish(client_order_id, "failed" if rejected else "succeeded", result)
            if rejected:
                raise ExecutionError("order_not_found", "Client order was rejected", status=404)
        return _success(recovered)

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
        payload = normalize_order_request(request.get_json(silent=True))
        client_order_id = request.headers.get("Idempotency-Key")
        if not client_order_id:
            raise ExecutionError("missing_idempotency_key", "Idempotency-Key is required")
        body_client_id = payload["intent"]["client_order_id"]
        if body_client_id != client_order_id:
            raise ExecutionError(
                "idempotency_key_mismatch",
                "Idempotency-Key must equal client_order_id",
            )
        request_id = payload["request_id"]
        if request.headers.get("X-Request-ID", request_id) != request_id:
            raise ExecutionError("request_id_mismatch", "X-Request-ID must equal request_id")
        g.execution_request_id = request_id
        record, created = context.store.reserve(
            client_order_id, payload_fingerprint(payload), request_id,
            payload=payload, session=expected_session,
        )
        if not created:
            validate_recovery = getattr(context.adapter, "validate_recovery_session", None)
            if callable(validate_recovery):
                validate_recovery(record)
            if record.state == "pending":
                raise ExecutionError(
                    "execution_in_progress", "The original request is still in progress", status=409
                )
            if record.state == "indeterminate":
                raise AmbiguousMT5Result(retcode=(record.result or {}).get("retcode"))
            if record.state == "failed":
                result = record.result or {}
                raise ExecutionError(
                    result.get("code", "execution_failed"), result.get("message", "Original execution failed"),
                    status=result.get("status", 422), retcode=result.get("retcode"),
                )
            if record.result and "response" in record.result:
                return _success(record.result["response"])
            raise ExecutionError("execution_outcome_unknown", "Use client_order_id lookup; automatic resend is forbidden", status=503)
        try:
            intent = payload["intent"]
            if intent["order_type"] != "market" or intent["time_in_force"] != "gtc":
                raise ExecutionError("unsupported_order_type", "This MT5 adapter supports only GTC market orders", status=422)
            mt5_request, check = context.adapter.preflight({
                "client_order_id": client_order_id, "symbol": intent["symbol"],
                "side": "BUY" if intent["direction"] == 1 else "SELL", "volume": intent["volume"],
                "stop_loss": intent["stop_loss"], "take_profit": intent["take_profit"],
            })
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
            try:
                response = order_result(request_id, result)
            except ExecutionError as exc:
                # MT5 was already invoked; malformed acceptance facts are an
                # unknown outcome, never proof that no order was accepted.
                raise AmbiguousMT5Result() from exc
            context.store.finish(client_order_id, "succeeded", dict(result, response=response))
            return _success(response, 201)
        except AmbiguousMT5Result as exc:
            context.store.finish(client_order_id, "indeterminate", {
                "code": exc.code, "message": exc.message, "retcode": exc.retcode,
            })
            raise
        except ExecutionError as exc:
            context.store.finish(client_order_id, "failed", {
                "code": exc.code, "message": exc.message, "retcode": exc.retcode, "status": exc.status,
            })
            raise

    @app.post("/api/v1/orders/<order_id>/cancel")
    def cancel_order(order_id):
        expected_session = require_mutation()
        order_id = str(parse_ticket_id(order_id, resource="order"))
        if expected_session is None:
            return _success(context.adapter.cancel(order_id))
        return _success(context.adapter.cancel(order_id, expected_session=expected_session))

    @app.post("/api/v1/positions/<position_id>/close")
    def close_position(position_id):
        expected_session = require_mutation()
        position_id = str(parse_ticket_id(position_id, resource="position"))
        if expected_session is None:
            return _success(context.adapter.close(position_id))
        return _success(context.adapter.close(position_id, expected_session=expected_session))

    @app.get("/api/v1/openapi.yaml")
    def openapi():
        return send_file(context.openapi_path, mimetype="text/yaml")

    return app


__all__ = ["ExecutionContext", "SCHEMA_VERSION", "create_execution_app"]
