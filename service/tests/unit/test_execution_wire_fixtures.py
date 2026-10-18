"""Wire examples are checked against actual synthetic route responses."""
import json
from pathlib import Path

import pytest

from service.execution.app import ExecutionContext, create_execution_app
from service.execution.authorization import AuthorizationStore
from service.execution.errors import AmbiguousMT5Result
from service.execution.idempotency import IdempotencyStore
from service.tests.unit.test_execution_api import fake_order
from service.tests.unit.test_execution_scoped_api import ScopedAdapter


@pytest.fixture
def wire(tmp_path, monkeypatch):
    fixture = json.loads((Path(__file__).parents[2] / "execution/fixtures/demo_authorization_wire.json").read_text())
    monkeypatch.setenv("READONLY_API_KEY", fixture["headers"]["X-API-Key"])
    monkeypatch.setattr("service.execution.contracts.now_wire", lambda: "2026-01-01T00:00:00Z")
    adapter = ScopedAdapter()
    adapter.order_by_client = lambda client_id: fake_order()
    authorization = AuthorizationStore(tmp_path / "grants.sqlite")
    authorization.provision(fixture["authorization"])
    context = ExecutionContext(adapter, IdempotencyStore(tmp_path / "orders.sqlite"),
        mutation_enabled=True, authorization_store=authorization, session_epoch="synthetic-epoch")
    return fixture, context, create_execution_app(context).test_client()


def test_valid_grant_idempotent_place_and_by_client_wire(wire):
    f, context, client = wire
    first = client.post("/api/v1/orders", json=f["place_request"], headers=f["headers"])
    repeat = client.post("/api/v1/orders", json=f["place_request"], headers=f["headers"])
    assert first.status_code == 201 and repeat.status_code == 200
    assert first.json == repeat.json == f["place_response"]
    assert context.adapter.send_count == 1
    lookup = client.get("/api/v1/orders/by-client/client-1", headers=f["headers"])
    assert lookup.status_code == 200
    assert lookup.json == f["by_client_response"]


def test_rejected_grant_unknown_fields_and_scope_wire(wire):
    f, context, client = wire
    with pytest.raises(ValueError):
        context.authorization_store.provision(dict(f["authorization"], **f["rejected_authorization"]))
    payload = json.loads(json.dumps(f["place_request"]))
    payload["intent"]["symbol"] = "EURUSDm"
    response = client.post("/api/v1/orders", json=payload, headers=f["headers"])
    assert response.status_code == 403
    assert response.json == f["rejected_scope_error"]
    assert context.adapter.send_count == 0


@pytest.mark.parametrize("operation,target", [("orders/7", "cancel"), ("positions/9", "close")])
def test_ambiguous_exit_wire_is_never_resent(wire, operation, target):
    f, context, client = wire
    client.post("/api/v1/orders", json=f["place_request"], headers=f["headers"])
    calls = []
    def ambiguous(*args, **kwargs):
        calls.append(1)
        raise AmbiguousMT5Result()
    setattr(context.adapter, target, ambiguous)
    url = f"/api/v1/{operation}/{target}"
    first = client.post(url, json={}, headers=f["headers"])
    second = client.post(url, json={}, headers=f["headers"])
    assert first.status_code == 503 and second.status_code == 409
    assert first.json == f["ambiguous_exit_error"]
    assert second.json == f["exit_replay_error"]
    assert len(calls) == 1


def test_restart_grant_denied_but_get_recovery_wire(wire):
    f, context, client = wire
    client.post("/api/v1/orders", json=f["place_request"], headers=f["headers"])
    context.authorization_store = AuthorizationStore(context.authorization_store.path)
    context.store = IdempotencyStore(context.store.path)
    context.session_epoch = "synthetic-restart"
    restarted = create_execution_app(context).test_client()
    denied = restarted.post("/api/v1/orders", json=f["place_request"], headers=f["headers"])
    assert denied.status_code == 403 and denied.json == f["restart_mutation_error"]
    lookup = restarted.get("/api/v1/orders/by-client/client-1", headers=f["headers"])
    assert lookup.status_code == 200 and lookup.json == f["by_client_response"]
    assert context.adapter.send_count == 1
