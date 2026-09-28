"""Realtime snapshots and persisted-history routes."""

import os

from flask import Blueprint, jsonify, request, send_file

from service.gateway.websocket import public_tick


def create_blueprint(tick_consumer, max_age_seconds, history_query_service,
                     account_service, symbol_names, openapi_path):
    bp = Blueprint("market_data", __name__)

    @bp.get("/api/v1/ticks/<symbol>")
    def get_tick(symbol):
        tick = tick_consumer.get(symbol)
        if tick and tick_consumer.is_fresh(tick, max_age_seconds):
            return jsonify(public_tick(tick))
        if tick:
            return jsonify({"error": "Tick data is stale", "symbol": symbol,
                            "last_received_at": tick.get("received_at")}), 503
        return jsonify({"error": "Symbol not found", "symbol": symbol}), 404

    @bp.get("/api/v1/rates/<symbol>")
    def get_rates(symbol):
        result = history_query_service.get_rates(
            symbol, timeframe=request.args.get("timeframe", "M5"),
            limit=request.args.get("limit"), days=request.args.get("days", "0"),
        )
        if "error_response" in result:
            return jsonify(result["error_response"]), result["status"]
        return jsonify(result["data"])

    @bp.get("/api/v1/rates/<symbol>/query")
    def query_rates_by_range(symbol):
        start_time, end_time = request.args.get("start_time"), request.args.get("end_time")
        if not start_time or not end_time:
            return jsonify({"error": "start_time and end_time are required"}), 400
        result = history_query_service.get_rates(
            symbol, timeframe=request.args.get("timeframe", "M5"), days="0",
            start_time=start_time, end_time=end_time,
        )
        if "error_response" in result:
            return jsonify(result["error_response"]), result["status"]
        if not result["data"]:
            return jsonify({"error": "No data found", "symbol": symbol,
                            "timeframe": result["timeframe"],
                            "available_range": result["available_range"]}), 404
        return jsonify({"symbol": symbol, "timeframe": result["timeframe"],
                        "start_time": start_time, "end_time": end_time,
                        "data": result["data"], "count": len(result["data"]),
                        "source": "history_storage"})

    @bp.get("/api/v1/symbols")
    def list_symbols():
        try:
            return jsonify(account_service.get_symbols(symbol_names))
        except Exception as exc:
            return jsonify({"error": str(exc)}), 500

    @bp.get("/api/v1/openapi.yaml")
    def openapi_spec():
        if not os.path.exists(openapi_path):
            return jsonify({"error": "Specification file not found"}), 404
        return send_file(openapi_path, mimetype="text/yaml")

    return bp


__all__ = ["create_blueprint"]
