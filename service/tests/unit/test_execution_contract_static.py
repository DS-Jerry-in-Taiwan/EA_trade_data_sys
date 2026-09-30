"""Static deployment and contract checks for the isolated execution service.

These checks intentionally do not import Flask, connect to MT5, or execute a
mutation.  They run in the lightweight host test environment and protect the
deployment contract that is otherwise exercised by the container E2E suite.
"""

from pathlib import Path

import yaml

from service.execution.idempotency import IdempotencyStore


ROOT = Path(__file__).parents[3]
COMPOSE = ROOT / "mt5docker" / "compose.yaml"
OPENAPI = ROOT / "service" / "execution_openapi.yaml"
START_SERVER = ROOT / "mt5docker" / "start_server.sh"
HEALTHCHECK = ROOT / "mt5docker" / "healthcheck.sh"
TERMINAL_LIFECYCLE = ROOT / "mt5docker" / "terminal_lifecycle.sh"


def _service_environment(service):
    environment = service.get("environment", {})
    if isinstance(environment, list):
        return dict(item.split("=", 1) for item in environment if "=" in item)
    return environment


def test_execution_openapi_covers_client_lifecycle_and_health_contract():
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    paths = spec["paths"]
    expected = {
        "/health", "/account", "/symbols/{symbol}", "/orders",
        "/orders/{id}", "/orders/by-client/{client_order_id}",
        "/orders/{id}/cancel", "/positions", "/positions/{id}/close", "/deals",
    }
    assert expected <= set(paths)
    assert spec["components"]["securitySchemes"]["ApiKeyAuth"]["name"] == "X-API-Key"
    create = paths["/orders"]["post"]
    assert "at most one order_send" in create["description"]
    assert "never automatically retry" in create["description"].lower()
    parameter_names = {item["$ref"].split("/")[-1] for item in create["parameters"]}
    assert {"IdempotencyKey", "RequestId"} <= parameter_names
    health_required = set(spec["components"]["schemas"]["ExecutionHealth"]["required"])
    assert {"ready", "account_session", "mutation_enabled", "account_policy", "mutation_ready"} <= health_required


def test_compose_keeps_execution_mutation_closed_and_state_durable():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["execution-service"]
    env = _service_environment(service)
    assert env["EXECUTION_MUTATION_ENABLED"] == "false"
    assert env["EXECUTION_ACCOUNT_POLICY"] == "DEMO"
    assert env["EXECUTION_IDEMPOTENCY_DB"] == "/app/runtime/execution/idempotency.sqlite3"
    mounts = service["volumes"]
    assert any("../runtime:/app/runtime" in mount for mount in mounts)
    assert service["depends_on"]["mt5-server"]["condition"] == "service_healthy"
    health = " ".join(str(part) for part in service["healthcheck"]["test"])
    assert "/api/v1/health" in health
    assert "ready" in health


def test_compose_persists_terminal_bootstrap_state_without_exposing_credentials():
    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    service = compose["services"]["mt5-server"]
    env = _service_environment(service)
    assert env["MT5_BOOTSTRAP_REIMPORT"] == "${MT5_BOOTSTRAP_REIMPORT:-0}"
    assert env["MT5_BOOTSTRAP_MARKER"] == "/mt5docker/MT5_Data/.mt5-bootstrap-complete"
    assert any(":/mt5docker/MT5_Data" in mount for mount in service["volumes"])
    startup = START_SERVER.read_text(encoding="utf-8")
    assert ".mt5-bootstrap-complete" in startup
    assert "account_session_ready" in startup
    assert "Password" not in startup


def test_idempotency_state_survives_execution_service_restart(tmp_path):
    path = tmp_path / "runtime" / "execution" / "idempotency.sqlite3"
    first = IdempotencyStore(path)
    fingerprint = "fingerprint"
    first.reserve("restart-safe", fingerprint, "request-1")
    first.finish("restart-safe", "indeterminate", {"code": "execution_outcome_unknown"})

    restored = IdempotencyStore(path).get("restart-safe")
    assert restored.state == "indeterminate"
    assert restored.result == {"code": "execution_outcome_unknown"}


def test_mt5_startup_and_health_are_fail_closed_for_update_or_multiple_terminals():
    startup = START_SERVER.read_text(encoding="utf-8")
    health = HEALTHCHECK.read_text(encoding="utf-8")
    lifecycle = TERMINAL_LIFECYCLE.read_text(encoding="utf-8")
    assert "/skipupdate" in startup
    assert "winepath -w" in startup
    assert "start_terminal_with_one_update_cycle" in startup
    assert "exactly_one_normal_terminal" in startup
    assert "update_terminal_pids" in lifecycle
    assert "exactly_one_normal_terminal" in health
    assert "rpyc_is_listening" in health
