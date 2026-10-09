"""Authenticated read-only account and trade-query routes."""

from datetime import datetime, timedelta, timezone

from flask import Blueprint, jsonify, request

from service.gateway.auth import require_readonly_api_key
from service.domain.trades.errors import DealMappingError
from service.trade_query.contracts import TradeQueryError


def _parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def parse_trade_query_range(config_loader):
    config = config_loader()
    if hasattr(config, "trade_query"):
        default_days = config.trade_query.default_days
        max_days = config.trade_query.max_days
    else:
        cfg = config.get("trade_query", {})
        default_days = int(cfg.get("default_days", 7))
        max_days = int(cfg.get("max_days", 90))
    from_arg, to_arg = request.args.get("from"), request.args.get("to")
    if from_arg or to_arg:
        from_dt, to_dt = _parse_date(from_arg), _parse_date(to_arg)
        if from_dt is None or to_dt is None:
            return None, None, {"error": "invalid date format"}
        if from_dt > to_dt:
            return None, None, {"error": "from must be before to"}
        if (to_dt - from_dt).days > max_days:
            return None, None, {"error": f"date range too large, max {max_days} days"}
        return from_dt, to_dt, None
    try:
        days = int(request.args.get("days", default_days))
    except (TypeError, ValueError):
        return None, None, {"error": "invalid date format"}
    if days <= 0:
        return None, None, {"error": "invalid date format"}
    if days > max_days:
        return None, None, {"error": f"date range too large, max {max_days} days"}
    to_dt = datetime.now(timezone.utc)
    return to_dt - timedelta(days=days), to_dt, None


def _response(result):
    if isinstance(result, dict) and (
        result.get("error") == "MT5 not connected"
        or result.get("code") == "account_session_transition"
    ):
        return jsonify(result), 503
    if isinstance(result, dict) and result.get("error"):
        return jsonify(result), 500
    return jsonify(result)


def create_blueprint(account_service, config_loader, mapping_error_counter=None):
    bp = Blueprint("trade_query", __name__)

    @bp.get("/api/v1/account")
    def get_account():
        return require_readonly_api_key(config_loader) or _response(account_service.get_account())

    @bp.get("/api/v1/positions")
    def get_positions():
        error = require_readonly_api_key(config_loader)
        if error:
            return error
        try:
            return _response(account_service.get_positions(symbol=request.args.get("symbol")))
        except TradeQueryError as exc:
            return jsonify(exc.as_response()), exc.status

    @bp.get("/api/v1/history/deals")
    def get_history_deals():
        error = require_readonly_api_key(config_loader)
        if error:
            return error
        from_dt, to_dt, parse_error = parse_trade_query_range(config_loader)
        if parse_error:
            return jsonify(parse_error), 400
        try:
            result = account_service.get_deals(
                from_dt=from_dt, to_dt=to_dt, limit=None,
                include_summary=request.args.get("summary", "").lower() == "true",
            )
        except DealMappingError:
            if mapping_error_counter is not None:
                mapping_error_counter.inc()
            return jsonify({
                "error": "upstream deal contract invalid",
                "code": "mt5_deal_mapping_error",
            }), 502
        return _response(result)

    @bp.get("/api/v1/history/orders")
    def get_history_orders():
        error = require_readonly_api_key(config_loader)
        if error:
            return error
        from_dt, to_dt, parse_error = parse_trade_query_range(config_loader)
        if parse_error:
            return jsonify(parse_error), 400
        return _response(account_service.get_history_orders(from_dt, to_dt))

    return bp


__all__ = ["create_blueprint", "parse_trade_query_range"]
