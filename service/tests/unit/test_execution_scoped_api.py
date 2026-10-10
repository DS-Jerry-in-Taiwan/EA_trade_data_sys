"""Synthetic route contracts; no real terminal or mutation gate is used."""
from datetime import datetime, timedelta, timezone

import pytest

from service.execution.app import ExecutionContext, create_execution_app
from service.execution.authorization import AuthorizationStore
from service.execution.idempotency import IdempotencyStore
from service.execution.errors import AmbiguousMT5Result
from service.tests.unit.test_execution_api import FakeAdapter, submit_payload


class ScopedAdapter(FakeAdapter):
    def session_status(self, *, refresh=False):
        return {"state": "ready", "ready": True, "generation": 1,
                "fingerprint": {"id": "synthetic-session", "account_mode": "DEMO"}}

    def mutation_session(self):
        return {"generation": 1, "fingerprint": "synthetic-session"}

    def symbol(self, symbol):
        return {"symbol": symbol, "minimum_volume": "0.01"}

    def assert_entry_scope(self, symbol):
        pass

    def validate_mutation_session(self, expected):
        pass

    def send_once(self, request, *, expected_session=None, expected_empty_symbol=None, final_authorization=None):
        if final_authorization:
            final_authorization(None)
        return super().send_once(request)

    def cancel_scope(self, client_id, order_id, symbol, volume):
        return {"client_order_id": client_id, "order_id": order_id, "symbol": symbol, "volume": volume}

    def exposure_scope(self, client_id, order_id, symbol, volume):
        return dict(self.cancel_scope(client_id, order_id, symbol, volume), position_id="9")

    def cancel(self, order_id, **kwargs):
        return super().cancel(order_id)

    def close(self, position_id, **kwargs):
        return super().close(position_id)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("READONLY_API_KEY", "synthetic-only")
    adapter = ScopedAdapter()
    authorization = AuthorizationStore(tmp_path / "authorization.sqlite")
    context = ExecutionContext(adapter, IdempotencyStore(tmp_path / "orders.sqlite"),
                               mutation_enabled=True, authorization_store=authorization,
                               session_epoch="synthetic-epoch")
    now = datetime.now(timezone.utc)
    authorization.provision({
        "authorization_id": "synthetic-grant", "account_fingerprint": "synthetic-session",
        "session_generation": 1, "session_epoch": "synthetic-epoch", "symbol": "XAUUSDm",
        "minimum_volume": "0.01", "client_order_id": "client-1",
        "permissions": ["place", "cancel", "close"], "abort_owner": "synthetic-operator",
        "entry_not_before": (now-timedelta(minutes=1)).isoformat(),
        "entry_expires_at": (now+timedelta(minutes=1)).isoformat(),
        "recovery_expires_at": (now+timedelta(minutes=5)).isoformat(),
    })
    headers = {"X-API-Key": "synthetic-only", "Idempotency-Key": "client-1",
               "X-Execution-Authorization": "synthetic-grant"}
    return create_execution_app(context).test_client(), headers, adapter, context


def test_api_key_alone_cannot_mutate(setup):
    client, headers, adapter, _ = setup
    headers.pop("X-Execution-Authorization")
    response = client.post("/api/v1/orders", json=submit_payload(), headers=headers)
    assert response.status_code == 403
    assert adapter.send_count == 0


def test_exact_volume_and_unknown_fields_fail_closed(setup):
    client, headers, adapter, _ = setup
    assert client.post("/api/v1/orders", json=submit_payload(volume="0.02"), headers=headers).status_code == 403
    body = submit_payload()
    body["authorization_override"] = True
    assert client.post("/api/v1/orders", json=body, headers=headers).status_code == 400
    assert adapter.send_count == 0


def test_place_repeat_returns_same_result_without_resend(setup):
    client, headers, adapter, _ = setup
    first = client.post("/api/v1/orders", json=submit_payload(), headers=headers)
    second = client.post("/api/v1/orders", json=submit_payload(), headers=headers)
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json == second.json
    assert adapter.send_count == 1


def test_restart_epoch_rejects_existing_grant(setup):
    client, headers, adapter, context = setup
    context.session_epoch = "new-process"
    assert client.post("/api/v1/orders", json=submit_payload(), headers=headers).status_code == 403
    assert adapter.send_count == 0


def test_health_separate_entry_exit_and_no_credential(setup):
    client, headers, _, _ = setup
    health = client.get("/api/v1/health", headers=headers).json["data"]
    assert health["execution_authorization"]["entry"]["enabled"] is True
    assert health["execution_authorization"]["exit"]["enabled"] is False
    assert health["account_session"]["epoch"] == "synthetic-epoch"
    assert "synthetic-only" not in str(health)


def test_unknown_exit_body_and_unrelated_target_rejected(setup):
    client, headers, _, _ = setup
    assert client.post("/api/v1/orders/7/cancel", json={"force": True}, headers=headers).status_code == 400
    assert client.post("/api/v1/orders/7/cancel", json={}, headers=headers).status_code == 403


def test_entry_kill_preserves_scoped_exit_not_unrelated_target(setup):
    client, headers, _, context = setup
    assert client.post("/api/v1/orders", json=submit_payload(), headers=headers).status_code == 201
    context.mutation_enabled = False
    context.authorization_store.disable_entry("synthetic-grant")
    assert client.post("/api/v1/positions/123/close", json={}, headers=headers).status_code == 403
    assert client.post("/api/v1/positions/9/close", json={}, headers=headers).status_code == 200


def test_ambiguous_exit_not_resent_and_abort_recorded(setup):
    client, headers, adapter, context = setup
    client.post("/api/v1/orders", json=submit_payload(), headers=headers)
    sends = []
    def unknown(*args, **kwargs):
        sends.append(1)
        raise AmbiguousMT5Result()
    adapter.cancel = unknown
    assert client.post("/api/v1/orders/7/cancel", json={}, headers=headers).status_code == 503
    assert client.post("/api/v1/orders/7/cancel", json={}, headers=headers).status_code == 409
    assert len(sends) == 1
    assert context.authorization_store.abort_events()


def test_successful_exit_replay_does_not_require_remaining_position(setup):
    client, headers, adapter, _ = setup
    client.post("/api/v1/orders", json=submit_payload(), headers=headers)
    first = client.post("/api/v1/positions/9/close", json={}, headers=headers)
    def missing(*args):
        raise AssertionError("Closed position must not be queried on replay")
    adapter.exposure_scope = missing
    second = client.post("/api/v1/positions/9/close", json={}, headers=headers)
    assert first.status_code == second.status_code == 200
    assert first.json == second.json


def test_kill_during_preflight_prevents_send(setup):
    client, headers, adapter, context = setup
    preflight = adapter.preflight
    def killed(payload):
        context.authorization_store.disable_entry("synthetic-grant")
        return preflight(payload)
    adapter.preflight = killed
    response = client.post("/api/v1/orders", json=submit_payload(), headers=headers)
    assert response.status_code == 403
    assert adapter.send_count == 0


def test_claim_without_order_record_never_sends_after_reopen(setup):
    client, headers, adapter, context = setup
    from service.execution.contracts import normalize_order_request
    session = dict(adapter.session_status(), epoch=context.session_epoch)
    context.authorization_store.claim_entry("synthetic-grant", session, normalize_order_request(submit_payload()))
    context.authorization_store = AuthorizationStore(context.authorization_store.path)
    response = client.post("/api/v1/orders", json=submit_payload(), headers=headers)
    assert response.status_code == 409
    assert adapter.send_count == 0
