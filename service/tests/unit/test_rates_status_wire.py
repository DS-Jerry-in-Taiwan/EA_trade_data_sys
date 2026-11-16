from types import SimpleNamespace
from flask import Flask

from service.gateway.routes.market_data import create_blueprint


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
