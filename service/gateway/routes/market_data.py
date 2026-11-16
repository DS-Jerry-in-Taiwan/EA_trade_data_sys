"""Realtime snapshots and persisted-history routes."""

import os

from flask import Blueprint, jsonify, request, send_file

from service.gateway.websocket import public_tick
from service.trade_query.contracts import TradeQueryError


def create_blueprint(tick_consumer, max_age_seconds, history_query_service,
                     account_service, symbol_names, openapi_path,
                     history_status_reader=None, history_status_path=None,
                     history_status_max_age=None):
    bp = Blueprint("market_data", __name__)

    @bp.get("/api/v1/rates/status")
    def rates_status():
        """Return versioned history metadata for every logical cache."""
        if history_status_reader is None or history_status_path is None:
            payload = {"state": "unknown", "fresh": False, "sync": {}}
        else:
            payload = history_status_reader(history_status_path, history_status_max_age)
        readiness = payload.get("cache_readiness", payload.get("sync", {}))
        if not isinstance(readiness, dict):
            readiness = {}

        # Worker status entries are the source of truth for cache metadata.
        # Normalize legacy entries into the versioned HistorySessionMetadata
        # shape so clients never have to infer symbol or session identity.
        sessions = {}
        cache_readiness = {}
        for key, item in readiness.items():
            if not isinstance(item, dict):
                continue
            logical, _, timeframe = str(key).partition(":")
            logical = item.get("logical_symbol") or logical
            timeframe = item.get("timeframe") or timeframe
            if not logical or not timeframe:
                continue
            try:
                bars = max(0, int(item.get("bars", 0) or 0))
                minimum_bars = max(1, int(item.get("minimum_bars", 1) or 1))
                generation = max(1, int(item.get("session_generation", 1) or 1))
            except (TypeError, ValueError):
                # Invalid worker metadata is not exposed as a partially typed
                # object; publish an explicit error record instead.
                bars, minimum_bars, generation = 0, 1, 1
            history = {
                "state": item.get("state") if item.get("state") in {"ready", "insufficient", "error"} else "error",
                "logical_symbol": logical,
                "source_symbol": item.get("source_symbol") or logical,
                "timeframe": timeframe if timeframe in {"M5", "M15", "H1", "D1"} else "M5",
                "bars": bars,
                "minimum_bars": minimum_bars,
                "source_exhausted": bool(item.get("source_exhausted", False)),
                "session_generation": generation,
                "session_state": item.get("session_state") if item.get("session_state") in {"open", "closed", "unknown"} else "unknown",
                "provenance_id": item.get("provenance_id") or payload.get("provenance_id") or "unknown",
                "calendar_revision": item.get("calendar_revision") or "unknown",
                "effective_from": item.get("effective_from"),
                "effective_until": item.get("effective_until"),
                "gaps": item.get("gaps") if isinstance(item.get("gaps"), list) else [],
            }
            normalized_timeframe = history["timeframe"]
            session_state = "ready" if history["session_state"] == "open" and history["state"] == "ready" else "disconnected"
            envelope = {
                "schema_version": 1,
                "history": history,
                "session": {
                    "state": session_state,
                    "ready": session_state == "ready",
                    "generation": history["session_generation"],
                },
            }
            sessions[f"{logical}:{normalized_timeframe}"] = envelope
            cache_readiness[f"{logical}:{normalized_timeframe}"] = history

        all_ready = bool(cache_readiness) and all(
            item.get("state") == "ready" for item in cache_readiness.values()
        )
        heartbeat = {
            "fresh": bool(payload.get("fresh")),
            "state": payload.get("state", "unknown"),
            "updated_at": payload.get("updated_at"),
            "age_seconds": payload.get("age_seconds"),
        }
        return jsonify({
            "schema_version": 1,
            "status": payload.get("state", "unknown"),
            "ready": bool(payload.get("fresh") and all_ready),
            "heartbeat": heartbeat,
            "history": sessions,
            "history_sessions": sessions,
            "cache_readiness": cache_readiness,
            "provenance_id": payload.get("provenance_id"),
            "publication_version": payload.get("publication_version", 0),
            "last_successful_scan": payload.get("last_successful_scan"),
            "last_successful_publish": payload.get("last_successful_publish"),
            "storage_id": payload.get("storage_id"),
        })

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
            result = account_service.get_symbols(symbol_names)
            if isinstance(result, dict) and result.get("code") == "account_session_transition":
                return jsonify(result), 503
            if isinstance(result, dict) and result.get("error") == "MT5 not connected":
                return jsonify({"error": "MT5 is unavailable", "code": "mt5_unavailable"}), 503
            return jsonify(result)
        except TradeQueryError as exc:
            return jsonify(exc.as_response()), exc.status
        except Exception:
            return jsonify({"error": "Internal server error", "code": "internal_error"}), 500

    @bp.get("/api/v1/openapi.yaml")
    def openapi_spec():
        if not os.path.exists(openapi_path):
            return jsonify({"error": "Specification file not found"}), 404
        return send_file(openapi_path, mimetype="text/yaml")

    return bp


__all__ = ["create_blueprint"]
