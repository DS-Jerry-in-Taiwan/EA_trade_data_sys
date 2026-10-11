"""Gateway readiness and component-health route."""

from datetime import datetime, timezone

from flask import Blueprint, jsonify
from service.realtime.consumer import TickConsumer


def health_payload(context):
    expected = context.tick_symbols
    fresh = [symbol for symbol in expected if context.fresh_tick(symbol)]
    now = datetime.now(timezone.utc)
    symbol_freshness = {}
    for symbol in expected:
        event = context.tick_consumer.get(symbol)
        symbol_freshness[symbol] = {
            "receipt": TickConsumer.timestamp_freshness(event, "received_at", context.tick_max_age, now),
            "broker_quote": TickConsumer.timestamp_freshness(event, "source_time", context.tick_max_age, now),
        }
    quote_fresh = [symbol for symbol in expected if symbol_freshness[symbol]["broker_quote"]["fresh"]]
    tick = context.status_reader(context.tick_status_path, context.tick_status_max_age)
    history = context.status_reader(context.history_status_path, context.history_status_max_age)
    expected_storage = getattr(context, 'history_storage_id', None)
    history_config_ok = expected_storage is None or not history.get('fresh') or (
        history.get('storage_id') == expected_storage
    )
    if not history_config_ok:
        history = dict(history, state='unhealthy', error='history_configuration_unavailable')
    account_service = getattr(context, "account_service", None)
    session_status = {
        "state": "unknown", "ready": False, "generation": 0,
        "fingerprint": None, "error": "account_session_unavailable",
    }
    session_getter = (
        getattr(account_service, "session_status", None)
        if account_service is not None else None
    )
    if callable(session_getter):
        try:
            session_status = session_getter(refresh=True)
        except TypeError:
            # Compatibility for injected test/adapters that predate the
            # refresh keyword; production AccountService supports it.
            session_status = session_getter()
        except Exception:
            session_status = {
                "state": "disconnected", "ready": False, "generation": 0,
                "fingerprint": None, "error": "mt5_disconnected",
            }
    if not isinstance(session_status, dict):
        session_status = {
            "state": "unknown", "ready": False, "generation": 0,
            "fingerprint": None, "error": "account_session_unavailable",
        }
    session_ready = bool(session_status.get("ready"))
    ready = bool(context.tick_consumer.connected and tick.get("fresh")
                 and tick.get("state") == "healthy" and expected
                 and set(fresh) == set(expected)
                 and session_ready and history_config_ok)
    history_ok = bool(history.get("fresh") and history.get("state") in ("healthy", "syncing"))
    if not session_ready:
        status = "not-ready"
    elif ready:
        status = "healthy" if history_ok else "degraded"
    elif tick.get("fresh") and tick.get("state") == "unhealthy":
        status = "unhealthy"
    else:
        status = "not-ready"
    return {"status": status, "ready": ready,
            "gateway": {"state": "healthy", "ready": ready},
            "tick_service": tick, "history_service": history,
            "account_session": session_status,
            "tick_ipc_connected": context.tick_consumer.connected,
            "symbols_tracked": expected, "fresh_symbols": fresh,
            "readiness_basis": "transport_receipt_and_account_session",
            "fresh_symbols_basis": "received_at",
            "quote_fresh_symbols": quote_fresh,
            "symbol_freshness": symbol_freshness,
            "quote_max_age_seconds": context.tick_max_age,
            "timestamp": datetime.now(timezone.utc).isoformat()}


def create_blueprint(context, update_metrics):
    bp = Blueprint("health", __name__)

    @bp.get("/api/v1/health")
    def health():
        payload = health_payload(context)
        update_metrics(payload)
        return jsonify(payload), (200 if payload["ready"] else 503)

    return bp


__all__ = ["create_blueprint", "health_payload"]
