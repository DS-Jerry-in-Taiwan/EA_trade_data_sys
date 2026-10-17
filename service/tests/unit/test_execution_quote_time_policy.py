"""Fake broker tick encoding only; never connects or sends an order."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

import service.execution.mt5_adapter as implementation
from service.execution.errors import ExecutionError
from service.infrastructure.mt5.time_policy import TimePolicy
from service.tests.unit.test_execution_quote_risk import adapter


NOW = datetime(2026, 10, 17, 12, tzinfo=timezone.utc)


def configured(monkeypatch, *, age=1, policies=None, elapsed=0, generations=(7, 7)):
    class Clock(datetime):
        values = iter((NOW, NOW + timedelta(seconds=elapsed)))

        @classmethod
        def now(cls, tz=None):
            return next(cls.values)

    monkeypatch.setattr(implementation, "datetime", Clock)
    subject, mt5 = adapter()
    def forbidden(*args, **kwargs):
        pytest.fail("quote/risk must not mutate MT5 or change its session")
    for operation in ("order_send", "order_check", "login", "initialize", "shutdown"):
        setattr(mt5, operation, forbidden)
    mt5.symbol_info_tick = lambda _: type("Tick", (), {
        "bid": 100, "ask": 101,
        "time_msc": int((NOW.timestamp() + 10800 - age) * 1000),
    })()
    subject.time_policies = tuple(policies if policies is not None else [policy()])
    identities = iter(generations)
    subject._fresh_mutation_status = lambda _: {
        "ready": True, "generation": next(identities),
        "fingerprint": {"id": "fake", "server": "fake-demo", "account_mode": "DEMO"},
    }
    return subject


def policy(**changes):
    base = TimePolicy("fake-policy", "1", "fake-demo", "tick", "broker_wall_clock",
                      "milliseconds", NOW - timedelta(days=1), NOW + timedelta(days=1),
                      "fake-only evidence", offset_seconds=10800)
    return replace(base, **changes)


@pytest.mark.parametrize("risk", [False, True])
def test_normalizes_once_and_uses_same_utc_for_both_age_checks(monkeypatch, risk):
    result = configured(monkeypatch, elapsed=2).quote_risk("BTC", risk=risk)
    assert result["quote_at"] == "2026-10-17T11:59:59Z"
    assert Decimal(result["age_seconds"]) == 3
    assert result["fresh"]
    if risk:
        assert result["risk_ready"]


@pytest.mark.parametrize("age", [-1, 6])
def test_normalization_does_not_clamp_stale_or_future(monkeypatch, age):
    result = configured(monkeypatch, age=age).quote_risk("BTC", risk=True)
    assert Decimal(result["age_seconds"]) == age
    assert not result["fresh"] and not result["risk_ready"]


@pytest.mark.parametrize("change", [
    {"server": "other"}, {"resource": "deal"}, {"unit": "seconds"},
    {"valid_until": NOW}, {"valid_from": NOW + timedelta(seconds=1)},
    {"semantic": "unknown", "offset_seconds": None},
])
def test_configured_invalid_or_unmatched_policy_never_falls_back(monkeypatch, change):
    subject = configured(monkeypatch, policies=[policy(**change)])
    with pytest.raises(ExecutionError) as error:
        subject.quote_risk("BTC")
    assert error.value.code == "timestamp_policy_unavailable"


def test_ambiguous_current_policies_rejected(monkeypatch):
    subject = configured(monkeypatch, policies=[policy(), policy(id="other")])
    with pytest.raises(ExecutionError) as error:
        subject.quote_risk("BTC")
    assert error.value.code == "timestamp_policy_unavailable"


def test_policy_expiry_during_calculation_rejected(monkeypatch):
    subject = configured(monkeypatch, policies=[policy(valid_until=NOW + timedelta(seconds=1))], elapsed=2)
    with pytest.raises(ExecutionError) as error:
        subject.quote_risk("BTC", risk=True)
    assert error.value.code == "timestamp_policy_unavailable"


def test_session_transition_still_rejected_after_normalization(monkeypatch):
    subject = configured(monkeypatch, generations=(7, 8))
    with pytest.raises(ExecutionError) as error:
        subject.quote_risk("BTC", risk=True)
    assert error.value.code == "account_session_transition"


def test_empty_configuration_preserves_unproven_legacy_utc_fail_closed(monkeypatch):
    result = configured(monkeypatch, policies=[]).quote_risk("BTC", risk=True)
    assert Decimal(result["age_seconds"]) == -10799
    assert not result["fresh"] and not result["risk_ready"]


@pytest.mark.parametrize("suffix", ["quote", "risk"])
@pytest.mark.parametrize("valid", [True, False])
def test_authenticated_wire_retains_envelope_session_and_policy_failure(monkeypatch, tmp_path, suffix, valid):
    from service.execution.app import ExecutionContext, create_execution_app
    from service.execution.idempotency import IdempotencyStore

    selected = policy() if valid else policy(server="other")
    subject = configured(monkeypatch, policies=[selected])
    monkeypatch.setenv("READONLY_API_KEY", "fake-only")
    context = ExecutionContext(subject, IdempotencyStore(tmp_path / "fake.sqlite"), session_epoch="fake-boot")
    response = create_execution_app(context).test_client().get(
        "/api/v1/symbols/BTC/" + suffix, headers={"X-API-Key": "fake-only"})
    assert response.json["schema_version"] == 1
    if valid:
        assert response.status_code == 200
        assert response.json["data"]["account_session"]["epoch"] == "fake-boot"
        assert response.json["data"]["fresh"]
    else:
        assert response.status_code == 503
        assert response.json["error"]["code"] == "timestamp_policy_unavailable"
