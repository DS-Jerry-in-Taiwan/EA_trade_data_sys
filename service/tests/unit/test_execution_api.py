import os

import pytest

from service.execution.app import ExecutionContext, create_execution_app
from service.execution.errors import AmbiguousMT5Result, ExecutionError
from service.execution.idempotency import IdempotencyStore, payload_fingerprint


class FakeAdapter:
    def __init__(self, *, demo=True, ambiguous=False):
        self.demo = demo
        self.ambiguous = ambiguous
        self.send_count = 0

    def account(self):
        return {"trade_mode": "DEMO" if self.demo else "NON_DEMO_OR_UNKNOWN", "mutation_eligible": self.demo}

    def require_demo(self):
        if not self.demo:
            raise ExecutionError("demo_account_required", "Demo required", status=403)

    def symbol(self, symbol):
        return {"symbol": symbol, "stops_level": 10, "freeze_level": 5, "margin_per_lot": 100, "observed_at": "2026-01-01T00:00:00+00:00"}

    def orders(self):
        return [{"id": "7"}]

    def order(self, order_id):
        return {"id": "7"} if order_id == "7" else None

    def order_by_client(self, client_order_id):
        return None

    def positions(self):
        return []

    def deals(self):
        return []

    def preflight(self, payload):
        return {"symbol": payload["symbol"]}, {"retcode": 0, "margin": 100}

    def send_once(self, request):
        self.send_count += 1
        if self.ambiguous:
            raise AmbiguousMT5Result()
        return {"order_id": "7", "deal_id": "8", "retcode": 10009}

    def cancel(self, order_id):
        return {"order_id": order_id, "retcode": 10009}

    def close(self, position_id):
        return {"position_id": position_id, "retcode": 10009}


class SessionAdapter(FakeAdapter):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.refreshes = []

    def session_status(self, *, refresh=False):
        self.refreshes.append(refresh)
        return self.session


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = FakeAdapter()
    context = ExecutionContext(
        adapter=adapter,
        store=IdempotencyStore(tmp_path / "idempotency.sqlite3"),
        api_key_env="TEST_EXECUTION_KEY",
        mutation_enabled=False,
    )
    app = create_execution_app(context)
    app.testing = True
    return app.test_client(), context, adapter


def headers(**extra):
    return {"X-API-Key": "secret-for-test", **extra}


def test_auth_and_error_envelope(api):
    client, _, _ = api
    response = client.get("/api/v1/account")
    assert response.status_code == 401
    assert response.json["schema_version"] == "1.0.0"
    assert response.json["error"]["code"] == "unauthorized"
    assert response.json["error"]["request_id"]


@pytest.mark.parametrize("path", [
    "/api/v1/health", "/api/v1/account", "/api/v1/symbols/XAUUSDm",
    "/api/v1/orders", "/api/v1/orders/7", "/api/v1/positions", "/api/v1/deals",
])
def test_readonly_routes_use_envelope(api, path):
    client, _, _ = api
    response = client.get(path, headers=headers())
    assert response.status_code == 200
    assert response.json["schema_version"] == "1.0.0"
    assert "data" in response.json


def test_mutation_defaults_closed(api):
    client, _, adapter = api
    response = client.post("/api/v1/orders", headers=headers(), json={})
    assert response.status_code == 403
    assert response.json["error"]["code"] == "mutation_disabled"
    assert adapter.send_count == 0


def test_non_demo_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = FakeAdapter(demo=False)
    app = create_execution_app(ExecutionContext(
        adapter, IdempotencyStore(tmp_path / "db.sqlite3"), "TEST_EXECUTION_KEY", True
    ))
    response = app.test_client().post(
        "/api/v1/orders", headers=headers(**{"Idempotency-Key": "client-1"}),
        json={"client_order_id": "client-1", "symbol": "XAUUSDm", "side": "BUY", "volume": 0.01},
    )
    assert response.status_code == 403
    assert response.json["error"]["code"] == "demo_account_required"
    assert adapter.send_count == 0


def test_non_demo_health_is_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = FakeAdapter(demo=False)
    app = create_execution_app(ExecutionContext(
        adapter, IdempotencyStore(tmp_path / "db.sqlite3"), "TEST_EXECUTION_KEY", False
    ))
    response = app.test_client().get("/api/v1/health", headers=headers())
    assert response.status_code == 503
    assert response.json["data"]["ready"] is False
    assert response.json["data"]["account_mode"] == "NON_DEMO_OR_UNKNOWN"
    assert response.json["data"]["mutation_ready"] is False


def test_execution_health_refreshes_session_and_fails_closed_when_not_ready(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = SessionAdapter({
        "state": "switch_detected", "ready": False, "generation": 4,
        "fingerprint": None, "error": "account_session_transition",
    })
    app = create_execution_app(ExecutionContext(
        adapter, IdempotencyStore(tmp_path / "db.sqlite3"), "TEST_EXECUTION_KEY", False
    ))
    response = app.test_client().get("/api/v1/health", headers=headers())
    assert response.status_code == 503
    assert response.json["data"]["ready"] is False
    assert response.json["data"]["account_session"]["error"] == "account_session_transition"
    assert adapter.refreshes == [True]


def test_idempotent_replay_and_conflict(api):
    client, context, adapter = api
    context.mutation_enabled = True
    payload = {"client_order_id": "client-1", "symbol": "XAUUSDm", "side": "BUY", "volume": 0.01}
    mutation_headers = headers(**{"Idempotency-Key": "client-1", "X-Request-ID": "request-1"})
    first = client.post("/api/v1/orders", headers=mutation_headers, json=payload)
    second = client.post("/api/v1/orders", headers=mutation_headers, json=payload)
    conflict = client.post("/api/v1/orders", headers=mutation_headers, json={**payload, "volume": 0.02})
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json["data"]["replayed"] is True
    assert conflict.status_code == 409
    assert conflict.json["error"]["code"] == "idempotency_conflict"
    assert adapter.send_count == 1


def test_timeout_is_persisted_and_never_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = FakeAdapter(ambiguous=True)
    store = IdempotencyStore(tmp_path / "db.sqlite3")
    app = create_execution_app(ExecutionContext(adapter, store, "TEST_EXECUTION_KEY", True))
    client = app.test_client()
    payload = {"client_order_id": "client-timeout", "symbol": "XAUUSDm", "side": "BUY", "volume": 0.01}
    mutation_headers = headers(**{"Idempotency-Key": "client-timeout"})
    first = client.post("/api/v1/orders", headers=mutation_headers, json=payload)
    second = client.post("/api/v1/orders", headers=mutation_headers, json=payload)
    lookup = client.get("/api/v1/orders/by-client/client-timeout", headers=headers())
    assert first.status_code == 503
    assert second.status_code == 200
    assert second.json["data"]["state"] == "indeterminate"
    assert lookup.json["data"]["state"] == "indeterminate"
    assert adapter.send_count == 1


def test_by_client_reconciles_accepted_order_after_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = FakeAdapter(ambiguous=True)
    adapter.order_by_client = lambda _client_order_id: {"id": "77", "comment": "opaque"}
    store = IdempotencyStore(tmp_path / "db.sqlite3")
    app = create_execution_app(ExecutionContext(adapter, store, "TEST_EXECUTION_KEY", True))
    client = app.test_client()
    payload = {"client_order_id": "client-timeout", "symbol": "XAUUSDm", "side": "BUY", "volume": 0.01}
    mutation_headers = headers(**{"Idempotency-Key": "client-timeout"})
    assert client.post("/api/v1/orders", headers=mutation_headers, json=payload).status_code == 503
    lookup = client.get("/api/v1/orders/by-client/client-timeout", headers=headers())
    assert lookup.json["data"]["state"] == "succeeded"
    assert lookup.json["data"]["result"]["order_id"] == "77"
    assert lookup.json["data"]["result"]["recovered"] is True
    assert adapter.send_count == 1


def test_idempotency_survives_store_restart(tmp_path):
    path = tmp_path / "db.sqlite3"
    first = IdempotencyStore(path)
    fingerprint = payload_fingerprint({"symbol": "XAUUSDm"})
    first.reserve("client-1", fingerprint, "request-1")
    first.finish("client-1", "succeeded", {"order_id": "7"})
    restored = IdempotencyStore(path).get("client-1")
    assert restored.state == "succeeded"
    assert restored.result == {"order_id": "7"}
