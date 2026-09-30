import json
from datetime import datetime, timedelta, timezone

import pytest

from service.infrastructure.status.component_status import atomic_write_status, read_status
import service.entrypoints.api_gateway as gateway


def test_atomic_status_round_trip_and_staleness(tmp_path):
    path = tmp_path / "component.json"
    atomic_write_status(str(path), "tick_service", "healthy", connected=True)
    current = read_status(str(path), 10)
    assert current["component"] == "tick_service"
    assert current["state"] == "healthy"
    assert current["fresh"] is True

    payload = json.loads(path.read_text())
    payload["updated_at"] = (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat()
    path.write_text(json.dumps(payload))
    assert read_status(str(path), 10)["fresh"] is False


def test_status_reader_treats_missing_and_invalid_as_unknown(tmp_path):
    missing = read_status(str(tmp_path / "missing.json"), 10)
    assert missing["state"] == "unknown"
    assert missing["fresh"] is False
    invalid = tmp_path / "invalid.json"
    invalid.write_text("{")
    assert read_status(str(invalid), 10)["state"] == "unknown"


def _configure_health(monkeypatch, tmp_path, history_state="healthy", session=None):
    tick_path = tmp_path / "tick.json"
    history_path = tmp_path / "history.json"
    atomic_write_status(str(tick_path), "tick_service", "healthy")
    atomic_write_status(str(history_path), "history_service", history_state)
    context = gateway.app.extensions["gateway_context"]
    monkeypatch.setattr(context, "tick_symbols", ["XAUUSDm"])
    monkeypatch.setattr(context, "tick_status_path", str(tick_path))
    monkeypatch.setattr(context, "tick_status_max_age", 30)
    monkeypatch.setattr(context, "history_status_path", str(history_path))
    monkeypatch.setattr(context, "history_status_max_age", 30)
    monkeypatch.setattr(type(gateway.tick_consumer), "connected", property(lambda _self: True))
    monkeypatch.setattr(gateway.tick_consumer, "get", lambda symbol: {"symbol": symbol})
    monkeypatch.setattr(gateway.tick_consumer, "is_fresh", lambda tick, max_age: True)
    monkeypatch.setattr(
        context.account_service,
        "session_status",
        lambda refresh=False: session or {
            "state": "ready", "ready": True, "generation": 1,
            "fingerprint": {"id": "hashed", "login_hash": "hashed", "server": "Demo", "account_mode": "DEMO"},
        },
    )


def test_health_is_ready_when_tick_critical_path_is_fresh(monkeypatch, tmp_path):
    _configure_health(monkeypatch, tmp_path)
    response = gateway.app.test_client().get("/api/v1/health")
    assert response.status_code == 200
    assert response.json["status"] == "healthy"
    assert response.json["ready"] is True


def test_history_failure_degrades_without_failing_realtime_readiness(monkeypatch, tmp_path):
    _configure_health(monkeypatch, tmp_path, history_state="unhealthy")
    response = gateway.app.test_client().get("/api/v1/health")
    assert response.status_code == 200
    assert response.json["status"] == "degraded"
    assert response.json["ready"] is True


def test_tick_ipc_disconnect_is_not_ready(monkeypatch, tmp_path):
    _configure_health(monkeypatch, tmp_path)
    monkeypatch.setattr(type(gateway.tick_consumer), "connected", property(lambda _self: False))
    response = gateway.app.test_client().get("/api/v1/health")
    assert response.status_code == 503
    assert response.json["status"] == "not-ready"
    assert response.json["ready"] is False


def test_syncing_history_is_not_unhealthy(monkeypatch, tmp_path):
    _configure_health(monkeypatch, tmp_path, history_state="syncing")
    response = gateway.app.test_client().get("/api/v1/health")
    assert response.status_code == 200
    assert response.json["status"] == "healthy"


@pytest.mark.parametrize("state", ["disconnected", "unknown", "switch_detected"])
def test_account_session_not_ready_fails_gateway_health(monkeypatch, tmp_path, state):
    _configure_health(
        monkeypatch,
        tmp_path,
        session={
            "state": state, "ready": False, "generation": 2,
            "fingerprint": None, "error": "account_session_transition",
        },
    )
    response = gateway.app.test_client().get("/api/v1/health")
    assert response.status_code == 503
    assert response.json["status"] == "not-ready"
    assert response.json["ready"] is False


def test_account_reconciliation_failure_fails_gateway_health(monkeypatch, tmp_path):
    _configure_health(
        monkeypatch,
        tmp_path,
        session={
            "state": "switch_detected", "ready": False, "generation": 3,
            "fingerprint": {"id": "hashed", "server": "Demo", "account_mode": "DEMO"},
            "error": "account_reconciliation_failed",
        },
    )
    response = gateway.app.test_client().get("/api/v1/health")
    assert response.status_code == 503
    assert response.json["account_session"]["error"] == "account_reconciliation_failed"


def test_health_refreshes_account_session_snapshot(monkeypatch, tmp_path):
    calls = []
    _configure_health(
        monkeypatch,
        tmp_path,
        session={"state": "ready", "ready": True, "generation": 7, "fingerprint": None},
    )
    context = gateway.app.extensions["gateway_context"]
    monkeypatch.setattr(
        context.account_service,
        "session_status",
        lambda refresh=False: calls.append(refresh) or {
            "state": "ready", "ready": True, "generation": 7, "fingerprint": None,
        },
    )
    response = gateway.app.test_client().get("/api/v1/health")
    assert response.status_code == 200
    assert calls == [True]
