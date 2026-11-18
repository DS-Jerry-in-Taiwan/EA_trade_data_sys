"""Composition-root contract tests for calendar evidence."""

from service.config.models import HistoryServiceSettings, Settings
from service.history.worker import HistoryService
from service.history.calendar_service import compose_calendar_service
from service.infrastructure.mt5.session import AccountSessionTransition
from service.infrastructure.mt5.calendar_snapshot import CalendarSnapshot
from datetime import datetime, timezone, timedelta


def test_calendar_setting_is_opt_in_and_serialized():
    settings = Settings.from_mapping(
        {"history_service": {"calendar_enabled": True}}, environ={}
    )
    assert settings.history_service.calendar_enabled is True
    assert settings.as_dict()["history_service"]["calendar_enabled"] is True
    assert HistoryServiceSettings().calendar_enabled is False


def test_calendar_modes_are_explicit_and_observe_is_non_authoritative():
    disabled = Settings.from_mapping({"history_service": {}}, environ={})
    assert disabled.history_service.calendar_mode == "disabled"
    assert disabled.history_service.calendar_enabled is False

    observed = Settings.from_mapping(
        {"history_service": {"calendar_mode": "observe"}}, environ={}
    )
    assert observed.history_service.calendar_mode == "observe"
    assert observed.history_service.calendar_enabled is False

    enforced = Settings.from_mapping(
        {"history_service": {"calendar_mode": "enforce"}}, environ={}
    )
    assert enforced.history_service.calendar_mode == "enforce"
    assert enforced.history_service.calendar_enabled is True


def test_observe_factory_probes_but_returns_unknown():
    seen = []
    service = compose_calendar_service(
        mode="observe", probe=lambda symbol: seen.append(symbol) or {
            "source_symbol": "BTCUSD.sim", "revision": "r1",
            "utc_windows": [],
        }
    )
    result = service.classify_gap(
        "BTC", datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 1, tzinfo=timezone.utc), "M5"
    )
    assert result.state == "unknown"
    assert seen == ["BTC"]
    assert service.observations[0]["status"] == "observed"


def test_observe_factory_caches_probe_for_one_cycle_and_refreshes_next_cycle():
    seen = []
    service = compose_calendar_service(
        mode="observe", probe=lambda symbol: seen.append(symbol) or {
            "source_symbol": "BTCUSD.sim", "revision": "r1", "utc_windows": [],
        }
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service.begin_cycle(1)
    service.classify_gap("BTC", start, start + timedelta(hours=1), "M5")
    service.classify_gap("BTC", start, start + timedelta(hours=2), "M5")
    assert seen == ["BTC"]
    service.begin_cycle(2)
    service.classify_gap("BTC", start, start + timedelta(hours=1), "M5")
    assert seen == ["BTC", "BTC"]


def test_observe_factory_aborts_when_session_generation_changes():
    generation = [1]
    service = compose_calendar_service(
        mode="observe", generation_provider=lambda: generation[0],
        probe=lambda symbol: {"source_symbol": "BTCUSD.sim", "revision": "r1", "utc_windows": []},
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service.begin_cycle(1)
    generation[0] = 2
    try:
        service.classify_gap("BTC", start, start + timedelta(hours=1), "M5")
    except AccountSessionTransition:
        pass
    else:
        raise AssertionError("session generation change must abort observation cycle")


def test_observe_factory_aborts_if_generation_changes_during_probe():
    generation = [1]

    def probe(_symbol):
        generation[0] = 2
        return {"source_symbol": "BTCUSD.sim", "revision": "r1", "utc_windows": []}

    service = compose_calendar_service(
        mode="observe", generation_provider=lambda: generation[0], probe=probe,
    )
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    service.begin_cycle(1)
    try:
        service.classify_gap("BTC", start, start + timedelta(hours=1), "M5")
    except AccountSessionTransition:
        pass
    else:
        raise AssertionError("generation change during probe must abort immediately")
    assert service._cycle_cache == {}


def test_history_service_accepts_only_deep_calendar_boundary(tmp_path):
    class StubCalendar:
        def classify_gap(self, symbol, start, end, timeframe):
            raise AssertionError("not called during construction")

    service = HistoryService(
        config_path=None,
        settings=Settings.from_mapping(
            {"history_service": {"data_path": str(tmp_path)}}, environ={}
        ),
        mt5_client=object(),
        calendar_service=StubCalendar(),
    )
    assert service.calendar_service is not None
    assert callable(service.calendar_service.classify_gap)
    service.close = lambda: None


def test_composition_factory_is_unknown_without_refresh_coordinator():
    service = compose_calendar_service()
    result = service.classify_gap(
        "BTC", datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 1, tzinfo=timezone.utc), "M5"
    )
    assert result.state == "unknown"


def test_refresh_backed_factory_classifies_only_covered_snapshot():
    class Coordinator:
        def snapshot(self, *, session):
            return CalendarSnapshot(
                schema_version="calendar.snapshot.v1", snapshot_id="s1",
                source="mt5+broker", revision="r1",
                captured_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
                valid_from=datetime(2026, 1, 1, tzinfo=timezone.utc),
                valid_until=datetime(2026, 1, 2, tzinfo=timezone.utc),
                session_fingerprint="fp", session_generation=1,
                session_epoch="e1", symbols={"BTC": {
                    "source_symbol": "BTCUSD.sim",
                    "session_state": "closed",
                    "utc_windows": [{"from": "2026-01-01T00:00:00Z", "until": "2026-01-01T02:00:00Z"}],
                }},
            )

    service = compose_calendar_service(
        coordinator=Coordinator(),
        session_provider=lambda: {"fingerprint": "fp", "generation": 1, "epoch": "e1"},
    )
    result = service.classify_gap(
        "BTC", datetime(2026, 1, 1, tzinfo=timezone.utc),
        datetime(2026, 1, 1, 1, tzinfo=timezone.utc), "M5"
    )
    assert result.state == "closed"
