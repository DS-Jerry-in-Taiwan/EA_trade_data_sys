"""Flask application for the independently deployed execution service."""

from __future__ import annotations

import hmac
import os
import uuid
from decimal import Decimal
from dataclasses import dataclass, field

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
    authorization_store: object | None = None
    session_epoch: str = field(default_factory=lambda: str(uuid.uuid4()))


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
        session = dict(session, epoch=context.session_epoch)
        ready = connected and demo and (
            session.get("ready", False) if session_supported else True
        )
        policy = {"authorization_required": True, "entry": {"enabled": False}, "exit": {"enabled": False}}
        if context.authorization_store is not None:
            policy = context.authorization_store.policy(session)
        if not context.mutation_enabled:
            policy["entry"]["enabled"] = False
        return _success({
            "status": "healthy" if ready else "unhealthy",
            "ready": ready,
            "mt5_connected": connected,
            "account_mode": "DEMO" if demo else "NON_DEMO_OR_UNKNOWN",
            "account_session": session,
            "mutation_enabled": bool(context.mutation_enabled),
            "account_policy": str(context.account_policy).strip().upper(),
            "execution_authorization": policy,
            "mutation_ready": bool(
                context.mutation_enabled and policy["entry"]["enabled"] and demo
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

    @app.get("/api/v1/symbols/<symbol>/quote")
    @app.get("/api/v1/symbols/<symbol>/risk")
    def quote_risk(symbol):
        data = context.adapter.quote_risk(symbol, risk=request.path.endswith("/risk"))
        data["account_session"]["epoch"] = context.session_epoch
        return _success(data)

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

    def require_mutation(*, entry=True):
        if entry and not context.mutation_enabled:
            raise ExecutionError(
                "mutation_disabled", "Execution mutation is disabled", status=403
            )
        if str(context.account_policy).strip().upper() != "DEMO":
            raise ExecutionError(
                "mutation_policy_denied",
                "Execution mutation account policy must be Demo",
                status=403,
            )
        if context.authorization_store is None:
            raise ExecutionError("authorization_required", "Scoped execution authorization is required", status=403)
        capture = getattr(context.adapter, "mutation_session", None)
        status = getattr(context.adapter, "session_status", None)
        if not callable(capture) or not callable(status):
            raise ExecutionError("authorization_session_unknown", "Verified session identity is required", status=403)
        expected = capture()
        session = dict(status(refresh=True), epoch=context.session_epoch)
        fingerprint = session.get("fingerprint") or {}
        if (not session.get("ready") or fingerprint.get("account_mode") != "DEMO"
                or expected.get("generation") != session.get("generation")
                or expected.get("fingerprint") != fingerprint.get("id")):
            raise ExecutionError("authorization_session_changed", "Verified Demo session is required", status=403)
        authorization_id = request.headers.get("X-Execution-Authorization")
        if not authorization_id:
            raise ExecutionError("authorization_required", "X-Execution-Authorization is required", status=403)
        return expected, session, authorization_id

    @app.post("/api/v1/orders")
    def create_order():
        expected_session, session, authorization_id = require_mutation()
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
        previous = context.store.get(client_order_id)
        if previous is not None and previous.fingerprint != payload_fingerprint(payload):
            raise ExecutionError("idempotency_conflict", "Idempotency-Key was already used with a different payload", status=409)
        specification = context.adapter.symbol(payload["intent"]["symbol"])
        if Decimal(payload["intent"]["volume"]) != Decimal(str(specification["minimum_volume"])):
            raise ExecutionError("authorization_volume_denied", "Only exact broker minimum volume is authorized", status=403)
        claimed = context.authorization_store.claim_entry(authorization_id, session, payload)
        record, created = context.store.reserve(
            client_order_id, payload_fingerprint(payload), request_id,
            payload=payload, session=expected_session,
        )
        if created and not claimed:
            raise ExecutionError("execution_outcome_unknown", "Authorization already consumed; reconciliation is required", status=409)
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
            empty_symbol = context.adapter.assert_entry_scope(intent["symbol"])
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
                    mt5_request, expected_session=expected_session,
                    expected_empty_symbol=empty_symbol,
                    final_authorization=lambda _mt5: context.authorization_store.validate_entry(authorization_id, session, payload),
                )
            result["preflight"] = check
            try:
                response = order_result(request_id, result)
            except ExecutionError as exc:
                # MT5 was already invoked; malformed acceptance facts are an
                # unknown outcome, never proof that no order was accepted.
                raise AmbiguousMT5Result() from exc
            context.store.finish(client_order_id, "succeeded", dict(result, response=response))
            context.authorization_store.bind_exposure(authorization_id, session, order_id=result["order_id"], verified=True)
            return _success(response, 201)
        except AmbiguousMT5Result as exc:
            context.authorization_store.disable_entry(authorization_id, "entry_outcome_unknown")
            context.store.finish(client_order_id, "indeterminate", {
                "code": exc.code, "message": exc.message, "retcode": exc.retcode,
            })
            raise
        except ExecutionError as exc:
            context.authorization_store.disable_entry(authorization_id, "entry_failed")
            context.store.finish(client_order_id, "failed", {
                "code": exc.code, "message": exc.message, "retcode": exc.retcode, "status": exc.status,
            })
            raise
        except Exception as exc:
            context.store.finish(client_order_id, "indeterminate", {"code": "execution_outcome_unknown"})
            context.authorization_store.disable_entry(authorization_id, "entry_outcome_unknown")
            raise AmbiguousMT5Result() from exc

    @app.post("/api/v1/orders/<order_id>/cancel")
    def cancel_order(order_id):
        order_id = str(parse_ticket_id(order_id, resource="order"))
        return scoped_exit("cancel", order_id)

    @app.post("/api/v1/positions/<position_id>/close")
    def close_position(position_id):
        position_id = str(parse_ticket_id(position_id, resource="position"))
        return scoped_exit("close", position_id)

    def scoped_exit(operation, target):
        if request.get_data() and request.get_json(silent=True) != {}:
            raise ExecutionError("invalid_request", "Exit body must be empty or an empty object")
        expected, session, authorization_id = require_mutation(entry=False)
        scope = context.authorization_store.scope(authorization_id, session)
        bound_target = scope.get("order_id" if operation == "cancel" else "position_id")
        if bound_target == target:
            existing = context.authorization_store.exit_record(authorization_id, operation, target)
            if existing is not None:
                context.authorization_store.authorize_exit(authorization_id, session, operation, target)
                if existing.get("state") == "succeeded":
                    return _success(existing["result"])
                raise ExecutionError("exit_outcome_unknown", "Exit may have been accepted; reconcile without resending", status=409)
        order_record = context.store.get(scope["client_order_id"])
        order_id = scope.get("order_id")
        if not order_id and order_record is not None:
            if not scope["entry_claimed"] or order_record.session != expected:
                raise ExecutionError("authorization_exposure_unverified", "Original authorized session must match", status=403)
            context.adapter.validate_recovery_session(order_record)
            recovered = context.adapter.order_by_client(scope["client_order_id"])
            if recovered is not None:
                recovered = restore_order_intent(recovered, order_record)
                if recovered["intent"]["symbol"] != scope["symbol"] or Decimal(recovered["intent"]["volume"]) != Decimal(scope["minimum_volume"]):
                    raise ExecutionError("authorization_exposure_unverified", "Recovered order is outside authorized scope", status=403)
                order_id = recovered.get("broker_order_id")
                context.authorization_store.bind_exposure(authorization_id, session, order_id=order_id, verified=True)
        if not order_id:
            raise ExecutionError("authorization_exposure_unverified", "Accepted exposure must be reconciled", status=403)
        if operation == "close":
            expected_exposure = context.adapter.exposure_scope(scope["client_order_id"], order_id, scope["symbol"], scope["minimum_volume"])
            context.authorization_store.bind_exposure(authorization_id, session, position_id=expected_exposure["position_id"], verified=True)
        else:
            expected_exposure = context.adapter.cancel_scope(scope["client_order_id"], order_id, scope["symbol"], scope["minimum_volume"])
        context.authorization_store.authorize_exit(authorization_id, session, operation, target)
        record, created = context.authorization_store.reserve_exit(authorization_id, session, operation, target)
        if not created:
            if record.get("state") == "succeeded":
                return _success(record["result"])
            raise ExecutionError("exit_outcome_unknown", "Exit may have been accepted; reconcile without resending", status=409)
        try:
            result = getattr(context.adapter, operation)(target, expected_session=expected, expected_exposure=expected_exposure,
                authorization_check=lambda _mt5: context.authorization_store.authorize_exit(authorization_id, session, operation, target))
            context.authorization_store.finish_exit(authorization_id, operation, target, "succeeded", result)
            return _success(result)
        except Exception:
            context.authorization_store.finish_exit(authorization_id, operation, target, "indeterminate", {})
            context.authorization_store.disable_entry(authorization_id, "exit_failed_operator_escalation")
            raise

    @app.get("/api/v1/openapi.yaml")
    def openapi():
        return send_file(context.openapi_path, mimetype="text/yaml")

    return app


__all__ = ["ExecutionContext", "SCHEMA_VERSION", "create_execution_app"]
