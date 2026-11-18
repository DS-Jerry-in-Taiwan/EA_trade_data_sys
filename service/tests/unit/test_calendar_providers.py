from datetime import datetime, timezone, timedelta

import pytest

from service.infrastructure.mt5.calendar_providers import (
    BrokerCalendarArtifactProvider,
    CalendarProviderError,
    CompositeCalendarSnapshotProvider,
    MQL5SessionCalendarProvider,
)


NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


def windows():
    return [{"from": "2026-11-18T00:00:00Z", "until": "2026-11-18T23:59:00Z"}]


def artifact(**changes):
    value = {
        "source": "broker:official",
        "revision": "rev-7",
        "captured_at": NOW.isoformat(),
        "valid_from": NOW.isoformat(),
        "valid_until": (NOW + timedelta(days=7)).isoformat(),
        "symbols": {"BTC": {"source_symbol": "BTCUSD", "utc_windows": windows(), "session_state": "open"}},
    }
    value.update(changes)
    return value


def test_mql5_provider_maps_logical_symbol_and_normalizes_evidence():
    calls = []

    def bridge(source_symbol):
        calls.append(source_symbol)
        return {"source_symbol": source_symbol, "revision": "terminal-1", "captured_at": NOW, "valid_from": NOW, "valid_until": NOW + timedelta(days=1), "utc_windows": windows(), "session_state": "open"}

    evidence = MQL5SessionCalendarProvider(bridge, symbol_mapping={"BTC": "BTCUSD.sim"}).fetch("BTC")
    assert calls == ["BTCUSD.sim"]
    assert evidence.symbol == "BTC"
    assert evidence.source_symbol == "BTCUSD.sim"
    assert evidence.utc_windows[0]["from"].endswith("Z")


def test_mql5_provider_rejects_unknown_fields():
    def bridge(source_symbol):
        return {"source_symbol": source_symbol, "revision": "terminal-1", "captured_at": NOW,
                "valid_from": NOW, "valid_until": NOW + timedelta(days=1),
                "utc_windows": windows(), "session_state": "open", "secret": "ignored"}

    with pytest.raises(CalendarProviderError, match="unknown fields"):
        MQL5SessionCalendarProvider(bridge).fetch("BTC")


def test_mql5_missing_bridge_is_diagnostic_and_fail_closed():
    with pytest.raises(CalendarProviderError, match="unavailable"):
        MQL5SessionCalendarProvider(None).fetch("BTC")


def test_mql5_provider_sanitizes_bridge_exception():
    secret = "password=do-not-leak"

    def bridge(_name):
        raise RuntimeError(secret)

    with pytest.raises(CalendarProviderError) as caught:
        MQL5SessionCalendarProvider(bridge).fetch("BTC")
    assert str(caught.value) == "mt5_session_provider_error"
    assert secret not in str(caught.value)


def test_broker_artifact_file_error_does_not_expose_path(tmp_path):
    path = tmp_path / "private-password.json"
    with pytest.raises(CalendarProviderError) as caught:
        BrokerCalendarArtifactProvider(path)
    assert str(caught.value) == "broker_artifact_unavailable"
    assert "private-password" not in str(caught.value)


@pytest.mark.parametrize("field", ["revision", "captured_at", "valid_from", "valid_until"])
def test_broker_artifact_requires_provenance(field):
    value = artifact()
    value.pop(field)
    with pytest.raises(CalendarProviderError, match=field):
        BrokerCalendarArtifactProvider(value)


def test_broker_artifact_requires_symbol_coverage():
    provider = BrokerCalendarArtifactProvider(artifact(symbols={}))
    with pytest.raises(CalendarProviderError, match="coverage"):
        provider.fetch("EURUSDm")


def test_broker_artifact_normalizes_source_and_metadata():
    evidence = BrokerCalendarArtifactProvider(artifact()).fetch("BTC")
    assert evidence.source == "broker:official-artifact"
    assert evidence.revision == "rev-7"
    assert evidence.session_state == "open"


def test_broker_artifact_production_mode_requires_auditable_schedule_metadata():
    with pytest.raises(CalendarProviderError, match="schedule metadata"):
        BrokerCalendarArtifactProvider(artifact(), require_schedule_metadata=True)


def test_broker_artifact_accepts_provenance_dst_holidays_and_early_close():
    value = artifact(
        timezone="Europe/London",
        dst_rule="IANA:Europe/London",
        holidays=[{"date": "2026-12-25", "label": "Christmas"}],
        early_closes=[{"date": "2026-12-24", "until": "2026-12-24T12:00:00Z", "label": "Christmas Eve"}],
        provenance={"publisher": "broker", "uri": "https://broker.example/calendar", "retrieved_at": NOW.isoformat()},
    )
    provider = BrokerCalendarArtifactProvider(value, require_schedule_metadata=True)
    assert provider.fetch("BTC").source_symbol == "BTCUSD"


@pytest.mark.parametrize("field", ["timezone", "dst_rule", "holidays", "early_closes", "provenance"])
def test_broker_artifact_rejects_missing_required_schedule_metadata(field):
    value = artifact(
        timezone="Europe/London", dst_rule="IANA:Europe/London", holidays=[], early_closes=[],
        provenance={"publisher": "broker", "uri": "https://broker.example/calendar"},
    )
    value.pop(field)
    with pytest.raises(CalendarProviderError, match="schedule metadata"):
        BrokerCalendarArtifactProvider(value, require_schedule_metadata=True)


def test_malformed_windows_are_rejected():
    bad = artifact(symbols={"BTC": {"source_symbol": "BTCUSD", "utc_windows": [{"from": "bad", "until": "2026-11-18T23:00:00Z"}]}})
    with pytest.raises(CalendarProviderError, match="timestamp"):
        BrokerCalendarArtifactProvider(bad).fetch("BTC")


def test_composite_provider_builds_session_bound_snapshot():
    source = artifact()
    mt5 = MQL5SessionCalendarProvider(lambda name: {"source_symbol": "BTCUSD", "revision": "mt5-1", "captured_at": NOW, "valid_from": NOW, "valid_until": NOW + timedelta(days=1), "utc_windows": windows(), "session_state": "open"})
    broker = BrokerCalendarArtifactProvider(source)
    snapshot = CompositeCalendarSnapshotProvider(symbols=["BTC"], mt5=mt5, broker=broker, clock=lambda: NOW).fetch(session={"fingerprint": {"id": "fp"}, "generation": 1, "epoch": "e"})
    assert snapshot.session_fingerprint == "fp"
    assert snapshot.symbols["BTC"]["source_symbol"] == "BTCUSD"


def test_composite_provider_rejects_partial_coverage_and_conflict():
    source = artifact()
    mt5 = MQL5SessionCalendarProvider(lambda name: {"source_symbol": "OTHER", "revision": "mt5-1", "captured_at": NOW, "valid_from": NOW, "valid_until": NOW + timedelta(days=1), "utc_windows": windows(), "session_state": "open"})
    with pytest.raises(CalendarProviderError, match="conflict"):
        CompositeCalendarSnapshotProvider(symbols=["BTC"], mt5=mt5, broker=BrokerCalendarArtifactProvider(source), clock=lambda: NOW).fetch(session={"fingerprint": {"id": "fp"}, "generation": 1, "epoch": "e"})
    with pytest.raises(CalendarProviderError, match="incomplete"):
        CompositeCalendarSnapshotProvider(symbols=["EURUSDm"], mt5=mt5, broker=BrokerCalendarArtifactProvider(source), clock=lambda: NOW).fetch(session={"fingerprint": {"id": "fp"}, "generation": 1, "epoch": "e"})


def test_composite_provider_requires_complete_session_identity():
    provider = CompositeCalendarSnapshotProvider(symbols=["BTC"], mt5=MQL5SessionCalendarProvider(None), broker=BrokerCalendarArtifactProvider(artifact()))
    with pytest.raises(CalendarProviderError, match="identity"):
        provider.fetch(session={"generation": 1})


def test_composite_provider_sanitizes_nested_provider_error():
    secret = "Authorization: Bearer hidden"

    class Failing:
        def fetch(self, _symbol):
            raise CalendarProviderError(secret)

    provider = CompositeCalendarSnapshotProvider(
        symbols=["BTC"], mt5=Failing(), broker=Failing(), clock=lambda: NOW
    )
    with pytest.raises(CalendarProviderError) as caught:
        provider.fetch(session={"fingerprint": {"id": "fp"}, "generation": 1, "epoch": "e"})
    assert str(caught.value) == "calendar evidence incomplete for BTC"
    assert secret not in str(caught.value)
