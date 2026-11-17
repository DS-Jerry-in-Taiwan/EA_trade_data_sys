"""Composition-root boundaries for history calendar evidence."""

from types import SimpleNamespace

from service.entrypoints import history_worker
from service.infrastructure.mt5.calendar import UnknownCalendar
from service.infrastructure.mt5.calendar_composer import CalendarEvidenceComposer
from service.infrastructure.mt5.mt5_session_calendar import MT5SessionCalendarAdapter


def _settings(calendar=None):
    return SimpleNamespace(
        history_service=SimpleNamespace(calendar=calendar or {"enabled": False}),
    )


def test_history_composition_always_injects_fail_closed_calendar(monkeypatch):
    created = {}

    class FakeHistoryService:
        def __init__(self, *, settings):
            self.mt5_client = object()
            self.calendar_adapter = None
            created["service"] = self

    monkeypatch.setattr(history_worker, "HistoryService", FakeHistoryService)
    service = history_worker.build_history_service(settings=_settings())

    assert service is created["service"]
    assert isinstance(service.calendar_adapter, CalendarEvidenceComposer)
    assert isinstance(service.calendar_adapter.mt5_sessions, MT5SessionCalendarAdapter)
    assert isinstance(service.calendar_adapter.broker_calendar, UnknownCalendar)


def test_history_composition_preserves_injected_evidence_sources(monkeypatch):
    class FakeHistoryService:
        def __init__(self, *, settings):
            self.mt5_client = object()
            self.calendar_adapter = None

    mt5_source = object()
    broker_source = object()
    monkeypatch.setattr(history_worker, "HistoryService", FakeHistoryService)
    service = history_worker.build_history_service(
        settings=_settings({"enabled": True, "complete": True}),
        mt5_sessions=mt5_source,
        broker_calendar=broker_source,
    )

    assert service.calendar_adapter.mt5_sessions is mt5_source
    assert service.calendar_adapter.broker_calendar is broker_source
