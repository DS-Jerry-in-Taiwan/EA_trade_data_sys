import os
from copy import deepcopy
from pathlib import Path
import sys

import pytest

from service.domain.trades.errors import DealMappingError
from service.execution.app import ExecutionContext, create_execution_app
from service.execution.errors import AmbiguousMT5Result, ExecutionError
from service.execution.idempotency import IdempotencyStore, payload_fingerprint


def submit_payload(client_id="client-1", **intent_changes):
    return {
        "request_id": "request-1", "submitted_at": "2026-01-01T00:00:00Z",
        "intent": {
            "client_order_id": client_id, "symbol": "XAUUSDm", "direction": 1,
            "order_type": "market", "volume": "0.01", "limit_price": None,
            "stop_loss": None, "take_profit": None, "time_in_force": "gtc", **intent_changes,
        },
    }


def fake_order(ticket="7"):
    return {
        "broker_order_id": ticket, "request_id": "request-1",
        "intent": submit_payload()["intent"], "status": "accepted", "filled_volume": "0",
        "average_fill_price": None, "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


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
        return [fake_order()]

    def order(self, order_id):
        return fake_order() if order_id == "7" else None

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


class CachedHealthAdapter(FakeAdapter):
    def account(self):
        raise AssertionError("health must not make a blocking account_info call")

    def session_status(self, *, refresh=False):
        return {
            "state": "ready",
            "ready": True,
            "generation": 1,
            "fingerprint": {"account_mode": "DEMO", "id": "demo"},
        }


class MutationSessionAdapter(FakeAdapter):
    def __init__(self, *, changed=False):
        super().__init__()
        self.generation = 4
        self.changed = changed
        self.cancel_count = 0
        self.close_count = 0

    def mutation_session(self):
        return {"generation": self.generation, "fingerprint": "demo"}

    def validate_mutation_session(self, expected_session):
        if self.changed:
            raise ExecutionError(
                "session_changed_before_send",
                "MT5 account session changed before mutation was sent",
                status=409,
            )
        assert expected_session["generation"] == self.generation

    def cancel(self, order_id, *, expected_session=None):
        if expected_session is not None:
            self.validate_mutation_session(expected_session)
        self.cancel_count += 1
        return {"order_id": order_id, "retcode": 10009}

    def close(self, position_id, *, expected_session=None):
        if expected_session is not None:
            self.validate_mutation_session(expected_session)
        self.close_count += 1
        return {"position_id": position_id, "retcode": 10009}


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
    return {"X-API-Key": "secret-for-test", "X-Request-ID": "request-1", **extra}


def test_auth_and_error_envelope(api):
    client, _, _ = api
    response = client.get("/api/v1/account")
    assert response.status_code == 401
    assert response.json["schema_version"] == 1
    assert type(response.json["schema_version"]) is int
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
    assert response.json["schema_version"] == 1
    assert type(response.json["schema_version"]) is int
    assert "data" in response.json


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/v1/missing"), ("PUT", "/api/v1/account"),
])
def test_routing_errors_are_versioned_json(api, method, path):
    client, _, _ = api
    response = client.open(path, method=method, headers=headers())
    assert response.status_code in {404, 405}
    assert type(response.json["schema_version"]) is int
    assert response.json["schema_version"] == 1
    assert response.json["error"]["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.parametrize("error,status,code", [
    (ConnectionError("private terminal details"), 503, "mt5_unavailable"),
    (DealMappingError("private deal source"), 502, "mt5_deal_mapping_error"),
    (RuntimeError("private unexpected details"), 500, "internal_error"),
])
def test_error_boundary_redacts_exception_details(api, monkeypatch, caplog, error, status, code):
    client, _, adapter = api

    def fail():
        raise error

    monkeypatch.setattr(adapter, "deals", fail)
    response = client.get(
        "/api/v1/deals", headers=headers(**{"X-Request-ID": "failure-request"}),
    )
    assert response.status_code == status
    assert response.content_type == "application/json"
    assert response.json["schema_version"] == 1
    assert type(response.json["schema_version"]) is int
    assert response.json["error"]["code"] == code
    assert response.json["error"]["request_id"] == "failure-request"
    assert response.headers["X-Request-ID"] == "failure-request"
    assert "private" not in response.get_data(as_text=True)
    assert "private" not in caplog.text


@pytest.mark.parametrize("ticket", ["not-a-ticket", "-1", "0", "1.5", "18446744073709551616", "١"])
@pytest.mark.parametrize("method,path,adapter_method,code", [
    ("GET", "/api/v1/orders/{ticket}", "order", "invalid_order_id"),
    ("POST", "/api/v1/orders/{ticket}/cancel", "cancel", "invalid_order_id"),
    ("POST", "/api/v1/positions/{ticket}/close", "close", "invalid_position_id"),
])
def test_malformed_ticket_does_not_reach_adapter(
    api, monkeypatch, ticket, method, path, adapter_method, code,
):
    client, context, adapter = api
    context.mutation_enabled = True

    def should_not_run(*_args, **_kwargs):
        raise AssertionError("malformed tickets must not reach MT5 operations")

    monkeypatch.setattr(adapter, adapter_method, should_not_run)
    response = client.open(path.format(ticket=ticket), method=method, headers=headers())
    assert response.status_code == 400
    assert response.json["schema_version"] == 1
    assert response.json["error"]["code"] == code
    assert adapter.send_count == 0


def test_malformed_mutation_ticket_preserves_closed_gate(api):
    client, _, adapter = api
    response = client.post("/api/v1/orders/malformed/cancel", headers=headers())
    assert response.status_code == 403
    assert response.json["error"]["code"] == "mutation_disabled"
    assert adapter.send_count == 0


def test_health_auth_unconfigured_uses_integer_error_envelope(api, monkeypatch):
    client, _, _ = api
    monkeypatch.delenv("TEST_EXECUTION_KEY")
    response = client.get("/api/v1/health", headers=headers())
    assert response.status_code == 503
    assert response.json["schema_version"] == 1
    assert response.json["error"]["code"] == "auth_not_configured"


def test_unexpected_preflight_failure_cannot_retry_order(api, monkeypatch):
    client, context, adapter = api
    context.mutation_enabled = True

    def fail(_payload):
        raise RuntimeError("private preflight failure")

    monkeypatch.setattr(adapter, "preflight", fail)
    request_headers = headers(**{"Idempotency-Key": "unexpected"})
    payload = submit_payload("unexpected")
    first = client.post("/api/v1/orders", headers=request_headers, json=payload)
    replay = client.post("/api/v1/orders", headers=request_headers, json=payload)
    assert first.status_code == 500
    assert first.json["error"]["code"] == "internal_error"
    assert "private" not in first.get_data(as_text=True)
    assert replay.status_code == 409
    assert replay.json["error"]["code"] == "execution_in_progress"
    assert context.store.get("unexpected").state == "pending"
    assert adapter.send_count == 0


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


def test_execution_health_uses_cached_session_without_blocking_account_call(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = CachedHealthAdapter()
    app = create_execution_app(ExecutionContext(
        adapter, IdempotencyStore(tmp_path / "db.sqlite3"), "TEST_EXECUTION_KEY", False
    ))
    response = app.test_client().get("/api/v1/health", headers=headers())
    assert response.status_code == 200
    assert response.json["data"]["ready"] is True
    assert response.json["data"]["account_mode"] == "DEMO"


def test_idempotent_replay_and_conflict(api):
    client, context, adapter = api
    context.mutation_enabled = True
    payload = submit_payload()
    mutation_headers = headers(**{"Idempotency-Key": "client-1", "X-Request-ID": "request-1"})
    first = client.post("/api/v1/orders", headers=mutation_headers, json=payload)
    second = client.post("/api/v1/orders", headers=mutation_headers, json=payload)
    conflict = client.post("/api/v1/orders", headers=mutation_headers, json=submit_payload(volume="0.02"))
    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json["data"] == first.json["data"]
    assert second.json["data"]["broker_retcode"] == "10009"
    assert second.json["data"]["broker_order_id"] == "7"
    assert conflict.status_code == 409
    assert conflict.json["error"]["code"] == "idempotency_conflict"
    assert adapter.send_count == 1


def test_legacy_durable_record_migrates_and_recovers_without_new_send(api):
    import sqlite3

    client, context, adapter = api
    with sqlite3.connect(context.store.path) as connection:
        connection.execute("DROP TABLE execution_idempotency")
        connection.execute("""CREATE TABLE execution_idempotency (
            client_order_id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, state TEXT NOT NULL,
            result_json TEXT, request_id TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )""")
        connection.execute(
            "INSERT INTO execution_idempotency VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("legacy", "old-fingerprint", "indeterminate", None, "legacy-request", "2026-01-01", "2026-01-01"),
        )
    context.store = IdempotencyStore(context.store.path)
    adapter.order_by_client = lambda _client_id: fake_order("77")
    response = client.get("/api/v1/orders/by-client/legacy", headers=headers())
    assert response.status_code == 200
    assert response.json["data"]["request_id"] == "legacy-request"
    assert response.json["data"]["intent"]["client_order_id"] == "legacy"
    assert context.store.get("legacy").state == "succeeded"
    assert context.store.get("legacy").result["response"]["broker_order_id"] == "77"
    assert adapter.send_count == 0


def test_recovered_rejection_is_not_persisted_as_acceptance_and_never_resends(api):
    from service.execution.contracts import normalize_order_request

    client, context, adapter = api
    context.mutation_enabled = True
    payload = submit_payload()
    normalized = normalize_order_request(payload)
    context.store.reserve("client-1", payload_fingerprint(normalized), "request-1", payload=normalized)
    context.store.finish("client-1", "indeterminate", {"code": "execution_outcome_unknown"})
    rejected = fake_order()
    rejected["status"] = "rejected"
    adapter.order_by_client = lambda _client_id: rejected
    response = client.get("/api/v1/orders/by-client/client-1", headers=headers())
    assert response.status_code == 404
    record = context.store.get("client-1")
    assert record.state == "failed"
    assert record.result["response"]["broker_order_id"] is None
    assert record.result["response"]["reason"]
    replay = client.post(
        "/api/v1/orders", headers=headers(**{"Idempotency-Key": "client-1"}), json=payload,
    )
    assert replay.status_code == 422
    assert replay.json["error"]["code"] == "mt5_order_rejected"
    assert adapter.send_count == 0


def test_recovery_does_not_override_inconsistent_observed_order_volume(api):
    client, context, adapter = api
    context.store.reserve("client-1", "fingerprint", "request-1", payload=submit_payload(volume="0.1"))
    context.store.finish("client-1", "indeterminate", {"code": "execution_outcome_unknown"})
    adapter.order_by_client = lambda _client_id: fake_order()
    response = client.get("/api/v1/orders/by-client/client-1", headers=headers())
    assert response.status_code == 502
    assert response.json["error"]["code"] == "mt5_contract_invalid"
    assert context.store.get("client-1").state == "indeterminate"
    assert adapter.send_count == 0


def test_malformed_acceptance_result_stays_indeterminate_and_cannot_resend(api, monkeypatch):
    client, context, adapter = api
    context.mutation_enabled = True

    def bad_acceptance(_request):
        adapter.send_count += 1
        return {"order_id": "0", "deal_id": "8", "retcode": 10009}

    monkeypatch.setattr(adapter, "send_once", bad_acceptance)
    request_headers = headers(**{"Idempotency-Key": "client-1"})
    first = client.post("/api/v1/orders", headers=request_headers, json=submit_payload())
    replay = client.post("/api/v1/orders", headers=request_headers, json=submit_payload())
    assert first.status_code == replay.status_code == 503
    assert first.json["error"]["code"] == "execution_outcome_unknown"
    assert context.store.get("client-1").state == "indeterminate"
    assert adapter.send_count == 1


@pytest.mark.parametrize("retcode", [10012, 10031])
def test_ambiguous_mt5_retcode_is_durable_and_recovered_without_resend(api, monkeypatch, retcode):
    from types import SimpleNamespace
    from service.execution.mt5_adapter import MT5ExecutionAdapter

    client, context, adapter = api
    context.mutation_enabled = True

    def send(_request):
        adapter.send_count += 1
        return SimpleNamespace(retcode=retcode, order=0, deal=0, comment="private timeout details")

    class SenderClient:
        def call(self, callback):
            return callback(SimpleNamespace(order_send=send))

    sender = MT5ExecutionAdapter(SenderClient())
    monkeypatch.setattr(adapter, "send_once", sender.send_once)
    request_headers = headers(**{"Idempotency-Key": "client-1"})
    payload = submit_payload()
    first = client.post("/api/v1/orders", headers=request_headers, json=payload)
    assert first.status_code == 503
    assert first.json["error"]["code"] == "execution_outcome_unknown"
    assert set(first.json["error"]) == {"code", "message", "request_id"}
    assert first.headers["X-MT5-Retcode"] == str(retcode)
    assert "private" not in first.get_data(as_text=True)
    assert context.store.get("client-1").state == "indeterminate"
    assert context.store.get("client-1").result["retcode"] == retcode

    context.store = IdempotencyStore(context.store.path)
    replay = client.post("/api/v1/orders", headers=request_headers, json=payload)
    assert replay.status_code == 503
    assert replay.headers["X-MT5-Retcode"] == str(retcode)
    assert context.store.get("client-1").state == "indeterminate"
    unknown = client.get("/api/v1/orders/by-client/client-1", headers=headers())
    assert unknown.status_code == 404
    assert context.store.get("client-1").state == "indeterminate"
    assert adapter.send_count == 1

    monkeypatch.setattr(adapter, "order_by_client", lambda _client_id: fake_order())
    recovered = client.get("/api/v1/orders/by-client/client-1", headers=headers())
    assert recovered.status_code == 200
    assert recovered.json["data"]["broker_order_id"] == "7"
    record = context.store.get("client-1")
    assert record.state == "succeeded"
    assert record.result["retcode"] == retcode
    accepted_replay = client.post("/api/v1/orders", headers=request_headers, json=payload)
    assert accepted_replay.status_code == 200
    assert accepted_replay.json["data"]["broker_retcode"] == str(retcode)
    assert adapter.send_count == 1


def test_timeout_is_persisted_and_never_retried(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = FakeAdapter(ambiguous=True)
    store = IdempotencyStore(tmp_path / "db.sqlite3")
    app = create_execution_app(ExecutionContext(adapter, store, "TEST_EXECUTION_KEY", True))
    client = app.test_client()
    payload = submit_payload("client-timeout")
    mutation_headers = headers(**{"Idempotency-Key": "client-timeout"})
    first = client.post("/api/v1/orders", headers=mutation_headers, json=payload)
    second = client.post("/api/v1/orders", headers=mutation_headers, json=payload)
    lookup = client.get("/api/v1/orders/by-client/client-timeout", headers=headers())
    assert first.status_code == 503
    assert second.status_code == 503
    assert second.json["error"]["code"] == "execution_outcome_unknown"
    assert lookup.status_code == 404
    assert store.get("client-timeout").state == "indeterminate"
    assert adapter.send_count == 1


def test_by_client_reconciles_accepted_order_after_timeout(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = FakeAdapter(ambiguous=True)
    adapter.order_by_client = lambda _client_order_id: fake_order("77")
    store = IdempotencyStore(tmp_path / "db.sqlite3")
    app = create_execution_app(ExecutionContext(adapter, store, "TEST_EXECUTION_KEY", True))
    client = app.test_client()
    payload = submit_payload("client-timeout")
    mutation_headers = headers(**{"Idempotency-Key": "client-timeout"})
    assert client.post("/api/v1/orders", headers=mutation_headers, json=payload).status_code == 503
    lookup = client.get("/api/v1/orders/by-client/client-timeout", headers=headers())
    assert lookup.status_code == 200
    assert lookup.json["data"]["broker_order_id"] == "77"
    assert lookup.json["data"]["intent"]["client_order_id"] == "client-timeout"
    assert store.get("client-timeout").state == "succeeded"
    assert store.get("client-timeout").result["recovered"] is True
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


@pytest.mark.parametrize("changed", [
    {"volume": 0.01}, {"direction": True}, {"direction": "long"},
    {"order_type": "unknown"}, {"volume": "NaN"}, {"volume": "0"},
    {"order_type": []}, {"time_in_force": {}}, {"volume": "1E+1000000000"},
])
def test_invalid_bot_order_request_never_sends(api, changed):
    client, context, adapter = api
    context.mutation_enabled = True
    response = client.post(
        "/api/v1/orders", headers=headers(**{"Idempotency-Key": "client-1"}),
        json=submit_payload(**changed),
    )
    assert response.status_code == 400
    assert response.json["error"]["code"] == "invalid_request"
    assert adapter.send_count == 0
    assert context.store.get("client-1") is None


@pytest.mark.parametrize("changes", [
    {"order_type": "limit", "limit_price": "2000"},
    {"order_type": "stop", "limit_price": "2000"},
    {"time_in_force": "ioc"},
])
def test_unsupported_bot_order_kind_fails_before_send_and_replays_failure(api, changes):
    client, context, adapter = api
    context.mutation_enabled = True
    payload = submit_payload(**changes)
    request_headers = headers(**{"Idempotency-Key": "client-1"})
    first = client.post("/api/v1/orders", headers=request_headers, json=payload)
    replay = client.post("/api/v1/orders", headers=request_headers, json=payload)
    assert first.status_code == replay.status_code == 422
    assert replay.json["error"]["code"] == "unsupported_order_type"
    assert context.store.get("client-1").state == "failed"
    assert adapter.send_count == 0


def test_mt5_retcode_preserves_exact_bot_error_fields(api, monkeypatch):
    client, context, adapter = api
    context.mutation_enabled = True

    def reject(_payload):
        raise ExecutionError("invalid_stops", "MT5 rejected stops", status=422, retcode=10016)

    monkeypatch.setattr(adapter, "preflight", reject)
    response = client.post(
        "/api/v1/orders", headers=headers(**{"Idempotency-Key": "client-1"}), json=submit_payload(),
    )
    assert response.status_code == 422
    assert set(response.json["error"]) == {"code", "message", "request_id"}
    assert response.headers["X-MT5-Retcode"] == "10016"
    assert context.store.get("client-1").result["retcode"] == 10016


def test_original_bot_request_survives_store_restart(api):
    client, context, _ = api
    context.mutation_enabled = True
    payload = submit_payload()
    response = client.post(
        "/api/v1/orders", headers=headers(**{"Idempotency-Key": "client-1"}), json=payload,
    )
    assert response.status_code == 201
    restored = IdempotencyStore(context.store.path).get("client-1")
    assert restored.payload["intent"] == payload["intent"]
    assert restored.result["response"] == response.json["data"]


def _bot_contracts():
    evidence = Path(os.getenv("EXECUTION_BOT_CONTRACT_ROOT", "/tmp/ea-trade-bot-ac5-contract-evidence"))
    if not (evidence / "src" / "execution" / "contracts.py").is_file():
        pytest.skip("AC5 bot contract evidence is not mounted; set EXECUTION_BOT_CONTRACT_ROOT")
    sys.path.insert(0, str(evidence))
    from src.execution import contracts
    return contracts


def test_api_responses_decode_with_actual_bot_contracts(api):
    from types import SimpleNamespace
    from service.execution.mt5_adapter import MT5ExecutionAdapter

    contracts = _bot_contracts()
    client, context, _ = api
    timestamp = 1_798_459_200
    account_info = SimpleNamespace(
        login=123456, server="private-server", currency="USD", balance=1000.25,
        equity=1000.25, margin=0, margin_free=1000.25,
    )
    symbol_info = SimpleNamespace(
        digits=2, point=.01, volume_min=.01, volume_max=10, volume_step=.01,
        trade_tick_size=.01, trade_tick_value=1, trade_contract_size=100,
        trade_stops_level=20, trade_freeze_level=0, ask=2001,
    )
    raw_order = SimpleNamespace(
        ticket=7, symbol="XAUUSDm", type=0, state=4, type_time=0,
        volume_initial=.01, volume_current=0, price_open=2001, sl=0, tp=0,
        time_setup=timestamp, time_done=timestamp+1, comment="",
    )
    raw_position = SimpleNamespace(
        ticket=9, symbol="XAUUSDm", type=0, volume=.01, price_open=2001,
        price_current=2002, sl=0, time=timestamp,
    )
    raw_deal = SimpleNamespace(
        ticket=8, order=7, position_id=9, symbol="XAUUSDm", type=0, entry=0,
        volume=.01, price=2001, time=timestamp,
    )
    module = SimpleNamespace(
        account_info=lambda: account_info, symbol_info=lambda _symbol: symbol_info,
        order_calc_margin=lambda *_args: 100, orders_get=lambda: [raw_order],
        history_orders_get=lambda *_args: [], positions_get=lambda: [raw_position],
        history_deals_get=lambda *_args, **_kwargs: [raw_deal],
    )

    class MT5ClientFake:
        _resolver = object()

        def call(self, callback):
            return callback(module)

        def resolve(self, symbol):
            return symbol

    context.adapter = MT5ExecutionAdapter(MT5ClientFake())
    paths = [
        ("/api/v1/account", contracts.AccountSnapshot, False),
        ("/api/v1/symbols/XAUUSDm", contracts.SymbolSpecification, False),
        ("/api/v1/orders", contracts.Order, True),
        ("/api/v1/orders/7", contracts.Order, False),
        ("/api/v1/positions", contracts.Position, True),
        ("/api/v1/deals", contracts.Deal, True),
    ]
    for path, contract, many in paths:
        response = client.get(path, headers=headers())
        assert response.status_code == 200, response.json
        assert set(response.json) == {"schema_version", "data"}
        values = response.json["data"] if many else [response.json["data"]]
        for value in values:
            assert contract.from_dict(value).to_dict() == value
        assert "private-server" not in response.get_data(as_text=True)
        assert "123456" not in response.get_data(as_text=True)

    adapter = FakeAdapter(ambiguous=True)
    context.adapter = adapter
    context.mutation_enabled = True
    payload = submit_payload("actual-bot-recovery")
    mutation_headers = headers(**{"Idempotency-Key": "actual-bot-recovery"})
    assert client.post("/api/v1/orders", headers=mutation_headers, json=payload).status_code == 503
    adapter.order_by_client = lambda _client_id: deepcopy(fake_order("7"))
    recovered = client.get("/api/v1/orders/by-client/actual-bot-recovery", headers=headers())
    assert recovered.status_code == 200
    assert contracts.Order.from_dict(recovered.json["data"]).broker_order_id == "7"
    assert context.store.get("actual-bot-recovery").state == "succeeded"
    replay = client.post("/api/v1/orders", headers=mutation_headers, json=payload)
    assert replay.status_code == 200
    assert contracts.OrderResult.from_dict(replay.json["data"]).broker_order_id == "7"
    assert adapter.send_count == 1


def test_session_change_before_order_send_is_terminal_and_never_sends(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = MutationSessionAdapter(changed=True)
    store = IdempotencyStore(tmp_path / "db.sqlite3")
    app = create_execution_app(ExecutionContext(
        adapter, store, "TEST_EXECUTION_KEY", True,
    ))
    client = app.test_client()
    payload = submit_payload("client-switch")
    request_headers = headers(**{"Idempotency-Key": "client-switch"})

    response = client.post("/api/v1/orders", headers=request_headers, json=payload)
    replay = client.post("/api/v1/orders", headers=request_headers, json=payload)
    lookup = client.get("/api/v1/orders/by-client/client-switch", headers=headers())

    assert response.status_code == 409
    assert response.json["error"]["code"] == "session_changed_before_send"
    assert replay.status_code == 409
    assert replay.json["error"]["code"] == "session_changed_before_send"
    assert lookup.status_code == 404
    assert store.get("client-switch").state == "failed"
    assert adapter.send_count == 0


def test_account_policy_blocks_mutation_before_adapter(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = MutationSessionAdapter()
    app = create_execution_app(ExecutionContext(
        adapter, IdempotencyStore(tmp_path / "db.sqlite3"),
        "TEST_EXECUTION_KEY", True, account_policy="REAL",
    ))
    response = app.test_client().post(
        "/api/v1/orders",
        headers=headers(**{"Idempotency-Key": "policy-denied"}),
        json={"client_order_id": "policy-denied", "symbol": "XAUUSDm", "side": "BUY", "volume": 0.01},
    )
    assert response.status_code == 403
    assert response.json["error"]["code"] == "mutation_policy_denied"
    assert adapter.send_count == 0


@pytest.mark.parametrize("path,attribute", [
    ("/api/v1/orders/7/cancel", "cancel_count"),
    ("/api/v1/positions/9/close", "close_count"),
])
def test_session_change_blocks_cancel_and_close_before_mt5_mutation(
    tmp_path, monkeypatch, path, attribute,
):
    monkeypatch.setenv("TEST_EXECUTION_KEY", "secret-for-test")
    adapter = MutationSessionAdapter(changed=True)
    app = create_execution_app(ExecutionContext(
        adapter, IdempotencyStore(tmp_path / "db.sqlite3"),
        "TEST_EXECUTION_KEY", True,
    ))
    response = app.test_client().post(path, headers=headers())
    assert response.status_code == 409
    assert response.json["error"]["code"] == "session_changed_before_send"
    assert getattr(adapter, attribute) == 0
