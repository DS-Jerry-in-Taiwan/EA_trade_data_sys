from datetime import datetime, timezone
import pytest
from service.execution.authorization import AuthorizationStore
from service.execution.errors import ExecutionError


NOW = datetime(2030, 1, 1, 12, tzinfo=timezone.utc)
SESSION = {"fingerprint": "opaque-demo", "generation": 1, "epoch": "boot-a", "account_mode": "DEMO", "ready": True}
ARTIFACT = {"authorization_id": "test-grant", "account_fingerprint": "opaque-demo", "session_generation": 1,
            "session_epoch": "boot-a", "symbol": "BTC", "minimum_volume": "0.01", "client_order_id": "one-entry",
            "permissions": ["place", "cancel", "close"], "entry_not_before": "2030-01-01T11:00:00Z",
            "entry_expires_at": "2030-01-01T13:00:00Z", "recovery_expires_at": "2030-01-01T14:00:00Z", "abort_owner": "operator"}
PAYLOAD = {"intent": {"symbol": "BTC", "volume": 0.01, "client_order_id": "one-entry"}}


@pytest.fixture
def store(tmp_path):
    result = AuthorizationStore(tmp_path / "authorization.sqlite")
    result.provision(dict(ARTIFACT))
    return result


def test_restart_claim_never_resends(store):
    assert store.claim_entry("test-grant", SESSION, PAYLOAD, NOW)
    reopened = AuthorizationStore(store.path)
    assert not reopened.claim_entry("test-grant", SESSION, PAYLOAD, NOW)
    with pytest.raises(ExecutionError):
        reopened.claim_entry("test-grant", SESSION, dict(intent=dict(PAYLOAD["intent"], client_order_id="another")), NOW)


@pytest.mark.parametrize("change", [{"account_mode": "LIVE"}, {"account_mode": None}, {"fingerprint": "other"}, {"generation": 2}, {"epoch": "boot-b"}])
def test_identity_fail_closed(store, change):
    with pytest.raises(ExecutionError):
        store.claim_entry("test-grant", dict(SESSION, **change), PAYLOAD, NOW)


@pytest.mark.parametrize("field,value", [("unexpected", True), ("minimum_volume", float("nan")), ("entry_expires_at", "2030-01-01T13:00:00"), ("permissions", ["place", "other"]), ("session_generation", True)])
def test_strict_artifact(store, field, value):
    with pytest.raises(ValueError):
        store.provision(dict(ARTIFACT, authorization_id="other", **{field: value}))


def test_expiry_and_kill_do_not_block_scoped_recovery(store):
    store.claim_entry("test-grant", SESSION, PAYLOAD, NOW)
    store.bind_exposure("test-grant", SESSION, order_id=101, position_id=202, verified=True)
    store.disable_entry("test-grant")
    late = datetime(2030, 1, 1, 13, 30, tzinfo=timezone.utc)
    store.authorize_exit("test-grant", SESSION, "close", 202, late)
    with pytest.raises(ExecutionError):
        store.authorize_exit("test-grant", SESSION, "close", 303, late)
    with pytest.raises(ExecutionError):
        store.authorize_exit("test-grant", SESSION, "close", 202, datetime(2030, 1, 1, 14, tzinfo=timezone.utc))
    assert not store.policy(SESSION, late)["entry"]["enabled"]
    assert store.policy(SESSION, late)["exit"]["enabled"]


def test_ambiguous_exit_durable(store):
    store.claim_entry("test-grant", SESSION, PAYLOAD, NOW)
    store.bind_exposure("test-grant", SESSION, position_id=202, verified=True)
    assert store.reserve_exit("test-grant", SESSION, "close", 202, NOW)[1]
    store.finish_exit("test-grant", "close", 202, "indeterminate", {"code": "unknown"})
    record, created = AuthorizationStore(store.path).reserve_exit("test-grant", SESSION, "close", 202, NOW)
    assert not created and record["state"] == "indeterminate"


def test_unverified_binding_rejected(store):
    with pytest.raises(ExecutionError):
        store.bind_exposure("test-grant", SESSION, position_id=202)


def test_empty_policy_closed(tmp_path):
    policy = AuthorizationStore(tmp_path / "empty.sqlite").policy(SESSION, NOW)
    assert not policy["entry"]["enabled"] and not policy["exit"]["enabled"]


def test_pre_send_kill_rejects_previously_claimed_entry(store):
    store.claim_entry("test-grant", SESSION, PAYLOAD, NOW)
    store.authorize_entry("test-grant", SESSION, PAYLOAD, NOW)
    store.disable_entry("test-grant")
    assert not store.claim_entry("test-grant", SESSION, PAYLOAD, NOW)
    with pytest.raises(ExecutionError):
        store.authorize_entry("test-grant", SESSION, PAYLOAD, NOW)


def test_pre_send_expiry_rejects_previously_claimed_entry(store):
    store.claim_entry("test-grant", SESSION, PAYLOAD, NOW)
    with pytest.raises(ExecutionError):
        store.authorize_entry("test-grant", SESSION, PAYLOAD, datetime(2030, 1, 1, 13, tzinfo=timezone.utc))


def test_pre_send_requires_matching_claim_and_ready(store):
    with pytest.raises(ExecutionError):
        store.authorize_entry("test-grant", SESSION, PAYLOAD, NOW)
    store.claim_entry("test-grant", SESSION, PAYLOAD, NOW)
    with pytest.raises(ExecutionError):
        store.authorize_entry("test-grant", dict(SESSION, ready=False), PAYLOAD, NOW)
    with pytest.raises(ExecutionError):
        store.authorize_entry("test-grant", SESSION, dict(PAYLOAD, request_id="changed"), NOW)


@pytest.mark.parametrize("value", [0.01, 1, True, "", "1e-2", ".01", "NaN", "Infinity", "0", "1" * 65])
def test_minimum_volume_strict_decimal_string(store, value):
    with pytest.raises(ValueError):
        store.provision(dict(ARTIFACT, authorization_id="other", minimum_volume=value))


@pytest.mark.parametrize("ready", [None, False, 1, "true"])
def test_readiness_mandatory_boolean_true(store, ready):
    with pytest.raises(ExecutionError):
        store.claim_entry("test-grant", dict(SESSION, ready=ready), PAYLOAD, NOW)


def test_missing_readiness_rejected(store):
    session = {key: value for key, value in SESSION.items() if key != "ready"}
    with pytest.raises(ExecutionError):
        store.claim_entry("test-grant", session, PAYLOAD, NOW)


@pytest.mark.parametrize("permissions", [[{}], [[]], [1], [True]])
def test_unhashable_permissions_rejected(store, permissions):
    with pytest.raises(ValueError):
        store.provision(dict(ARTIFACT, authorization_id="other", permissions=permissions))
