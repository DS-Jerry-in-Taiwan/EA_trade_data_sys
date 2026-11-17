from types import SimpleNamespace
from flask import Flask
import pytest

from service.gateway.routes.market_data import create_blueprint


def _client_for(payload):
    app = Flask(__name__)
    app.register_blueprint(create_blueprint(
        SimpleNamespace(get=lambda _: None, is_fresh=lambda *_: False), 30,
        SimpleNamespace(get_rates=lambda *args, **kwargs: {}),
        SimpleNamespace(get_symbols=lambda _: []), [], "/missing",
        history_status_reader=lambda *_: payload,
        history_status_path="status.json", history_status_max_age=30,
    ))
    return app.test_client()


def test_rates_status_projects_versioned_metadata_per_cache():
    app = Flask(__name__)
    payload = {
        "state": "healthy",
        "fresh": True,
        "updated_at": "2026-11-16T00:00:00+00:00",
        "provenance_id": "proc-1",
        "publication_version": 7,
        "last_successful_scan": "2026-11-16T00:00:00+00:00",
        "last_successful_publish": "2026-11-16T00:00:00+00:00",
        "cache_readiness": {
            "XAUUSDm:M5": {
                "state": "ready", "logical_symbol": "XAUUSDm",
                "source_symbol": "XAUUSD.sim", "timeframe": "M5",
                "bars": 500, "minimum_bars": 100,
                "source_exhausted": False, "session_generation": 3,
                "session_state": "open", "provenance_id": "proc-1",
                "calendar_revision": "cal-2", "effective_from": None,
                "effective_until": None, "gaps": [],
            }
        },
    }
    app.register_blueprint(create_blueprint(
        SimpleNamespace(get=lambda _: None, is_fresh=lambda *_: False), 30,
        SimpleNamespace(get_rates=lambda *args, **kwargs: {}),
        SimpleNamespace(get_symbols=lambda _: []), [], "/missing",
        history_status_reader=lambda *_: payload,
        history_status_path="status.json", history_status_max_age=30,
    ))

    with app.test_client() as client:
        response = client.get("/api/v1/rates/status")
    assert response.status_code == 200
    body = response.get_json()
    assert body["schema_version"] == 1
    metadata = body["history"]["XAUUSDm:M5"]
    assert metadata["history"]["logical_symbol"] == "XAUUSDm"
    assert metadata["history"]["source_symbol"] == "XAUUSD.sim"
    assert metadata["history"]["session_state"] == "open"
    assert body["publication_version"] == 7


def test_rates_status_publishes_error_record_without_marking_ready():
    """Worker diagnostics are allowed, while required metadata stays intact."""
    payload = {
        "state": "healthy", "fresh": True,
        "cache_readiness": {
            "XAUUSDm:M5": {
                "state": "error", "logical_symbol": "XAUUSDm",
                "source_symbol": "XAUUSD.sim", "timeframe": "M5",
                "bars": 0, "minimum_bars": 100,
                "source_exhausted": False, "session_generation": 3,
                "session_state": "unknown", "provenance_id": "proc-1",
                "calendar_revision": "unknown", "effective_from": None,
                "effective_until": None, "gaps": [],
                "detail": "MT5 returned no history bars",
            }
        },
    }
    with _client_for(payload) as client:
        response = client.get("/api/v1/rates/status")
    assert response.status_code == 200
    body = response.get_json()
    assert body["ready"] is False
    assert body["history"]["XAUUSDm:M5"]["history"]["state"] == "error"


def _app_with_readiness(readiness):
    app = Flask(__name__)
    payload = {"state": "healthy", "fresh": True, "cache_readiness": readiness}
    app.register_blueprint(create_blueprint(
        SimpleNamespace(get=lambda _: None, is_fresh=lambda *_: False), 30,
        SimpleNamespace(get_rates=lambda *args, **kwargs: {}),
        SimpleNamespace(get_symbols=lambda _: []), [], "/missing",
        history_status_reader=lambda *_: payload,
        history_status_path="status.json", history_status_max_age=30,
    ))
    return app


def test_rates_status_rejects_missing_and_invalid_metadata_instead_of_defaults():
    base = {
        "state": "ready", "logical_symbol": "XAUUSDm", "source_symbol": "XAUUSD.sim",
        "timeframe": "M5", "bars": 500, "minimum_bars": 100,
        "source_exhausted": False, "session_generation": 3, "session_state": "open",
        "provenance_id": "proc-1", "calendar_revision": "cal-2", "effective_from": None,
        "effective_until": None, "gaps": [],
    }
    for field, value in (("timeframe", ""), ("session_state", "bogus"),
                         ("gaps", "not-a-list"), ("bars", "500"),
                         ("source_symbol", None)):
        item = dict(base)
        item[field] = value
        with _app_with_readiness({"XAUUSDm:M5": item}).test_client() as client:
            response = client.get("/api/v1/rates/status")
        assert response.status_code == 503
        assert response.get_json()["error"] == "invalid_history_metadata"


@pytest.mark.parametrize("field,value", [
    ("session_state", "unknown"),
    ("calendar_revision", "unknown"),
    ("gaps", [{
        "start": "2026-11-15T00:00:00Z",
        "end": "2026-11-15T01:00:00Z",
        "session_close": None,
        "session_reopen": None,
        "classification": "cache_backfill_gap",
    }]),
])
def test_rates_status_top_level_ready_fails_closed_for_unqualified_metadata(field, value):
    item = {
        "state": "ready", "logical_symbol": "XAUUSDm", "source_symbol": "XAUUSD.sim",
        "timeframe": "M5", "bars": 500, "minimum_bars": 100,
        "source_exhausted": False, "session_generation": 3, "session_state": "open",
        "provenance_id": "proc-1", "calendar_revision": "cal-2", "effective_from": None,
        "effective_until": None, "gaps": [],
    }
    item[field] = value
    with _app_with_readiness({"XAUUSDm:M5": item}).test_client() as client:
        response = client.get("/api/v1/rates/status")
    assert response.status_code == 200
    assert response.get_json()["ready"] is False
