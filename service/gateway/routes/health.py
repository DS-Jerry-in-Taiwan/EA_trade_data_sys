"""Gateway readiness and component-health route."""

from datetime import datetime, timezone

from flask import Blueprint, jsonify


def health_payload(context):
    expected = context.tick_symbols
    fresh = [symbol for symbol in expected if context.fresh_tick(symbol)]
    tick = context.status_reader(context.tick_status_path, context.tick_status_max_age)
    history = context.status_reader(context.history_status_path, context.history_status_max_age)
    ready = bool(context.tick_consumer.connected and tick.get("fresh")
                 and tick.get("state") == "healthy" and expected
                 and set(fresh) == set(expected))
    history_ok = bool(history.get("fresh") and history.get("state") in ("healthy", "syncing"))
    account_service = getattr(context, "account_service", None)
    session_status = (
        account_service.session_status()
        if account_service is not None and callable(getattr(account_service, "session_status", None))
        else {
            "state": "unknown", "ready": False, "generation": 0,
            "fingerprint": None, "error": "account_session_unavailable",
        }
    )
    if ready:
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
