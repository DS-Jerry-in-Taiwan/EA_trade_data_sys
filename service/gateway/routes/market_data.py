"""Realtime snapshots and persisted-history routes."""

import os

from flask import Blueprint, jsonify, request, send_file

from service.gateway.websocket import public_tick
from service.contracts.history_session_metadata import MetadataContractError, validate_history_session_metadata
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
            return jsonify({"schema_version": 1, "status": "unknown", "ready": False,
                            "error": "history_metadata_unavailable"}), 503
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
                return jsonify({"schema_version": 1, "status": "unknown", "ready": False,
                                "error": "invalid_history_metadata"}), 503
            logical_from_key, separator, timeframe_from_key = str(key).partition(":")
            # Do not repair malformed worker metadata from the cache key or
            # defaults.  The status endpoint is a contract boundary: missing
            # fields must fail closed instead of being silently coerced.
            required = {
                "state", "logical_symbol", "source_symbol", "timeframe",
                "bars", "minimum_bars", "source_exhausted", "session_generation",
                "session_state", "provenance_id", "calendar_revision",
                "effective_from", "effective_until", "gaps",
            }
            # Worker records may carry explicitly documented operational
            # diagnostics.  They are not part of the public envelope, but
            # must be allow-listed so an accidental field cannot bypass the
            # wire contract.  In particular, error records carry ``detail``.
            operational = {
                "updated_at", "detail", "publication_version",
                "last_successful_publish", "calendar_gaps", "session_fingerprint",
            }
            if not required <= set(item) or bool(set(item) - required - operational) or not separator:
                return jsonify({"schema_version": 1, "status": "unknown", "ready": False,
                                "error": "invalid_history_metadata"}), 503
            logical = item.get("logical_symbol")
            timeframe = item.get("timeframe")
            if logical != logical_from_key or timeframe != timeframe_from_key:
                return jsonify({"schema_version": 1, "status": "unknown", "ready": False,
                                "error": "invalid_history_metadata"}), 503
            try:
                bars = item["bars"]
                minimum_bars = item["minimum_bars"]
                generation = item["session_generation"]
                if (isinstance(bars, bool) or not isinstance(bars, int) or bars < 0 or
                        isinstance(minimum_bars, bool) or not isinstance(minimum_bars, int) or minimum_bars < 1 or
                        isinstance(generation, bool) or not isinstance(generation, int) or generation < 1):
                    raise ValueError("invalid numeric metadata")
            except (TypeError, ValueError):
                return jsonify({"schema_version": 1, "status": "unknown", "ready": False,
                                "error": "invalid_history_metadata"}), 503
            if timeframe not in {"M5", "M15", "H1", "D1"}:
                return jsonify({"schema_version": 1, "status": "unknown", "ready": False,
                                "error": "invalid_history_metadata"}), 503
            if item.get("state") not in {"ready", "insufficient", "error"}:
                return jsonify({"schema_version": 1, "status": "unknown", "ready": False,
                                "error": "invalid_history_metadata"}), 503
            history = {
                "state": item["state"],
                "logical_symbol": logical,
                "source_symbol": item["source_symbol"],
                "timeframe": timeframe,
                "bars": bars,
                "minimum_bars": minimum_bars,
                "source_exhausted": item["source_exhausted"],
                "session_generation": generation,
                "session_state": item["session_state"],
                "provenance_id": item["provenance_id"],
                "calendar_revision": item["calendar_revision"],
                "effective_from": item.get("effective_from"),
                "effective_until": item.get("effective_until"),
                "gaps": item["gaps"],
            }
            if item.get("session_fingerprint") is not None:
                if (not isinstance(item["session_fingerprint"], str)
                        or not item["session_fingerprint"].strip()):
                    return jsonify({"schema_version": 1, "status": "unknown", "ready": False,
                                    "error": "invalid_history_metadata"}), 503
                history["session_fingerprint"] = item["session_fingerprint"]
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
            try:
                validate_history_session_metadata(envelope)
            except MetadataContractError:
                return jsonify({"schema_version": 1, "status": "unknown", "ready": False,
                                "error": "invalid_history_metadata"}), 503
            sessions[f"{logical}:{normalized_timeframe}"] = envelope
            cache_readiness[f"{logical}:{normalized_timeframe}"] = history

        # Aggregate readiness is deliberately stricter than the worker's
        # cache state.  A cache can contain enough bars while its broker
        # session or calendar evidence is still unknown; exposing that as a
        # ready history snapshot would let consumers bypass fail-closed
        # admission.  Backfill gaps also remain unqualified until the worker
        # publishes an evidence-backed session break classification.
        all_ready = bool(cache_readiness) and all(
            item.get("state") == "ready"
            and item.get("session_state") != "unknown"
            and item.get("calendar_revision") != "unknown"
            and all(
                gap.get("classification") == "expected_session_break"
                for gap in item.get("gaps", [])
            )
            for item in cache_readiness.values()
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
