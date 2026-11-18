"""Composition-root contract tests for calendar evidence."""

from service.config.models import HistoryServiceSettings, Settings
from service.history.worker import HistoryService


def test_calendar_setting_is_opt_in_and_serialized():
    settings = Settings.from_mapping(
        {"history_service": {"calendar_enabled": True}}, environ={}
    )
    assert settings.history_service.calendar_enabled is True
    assert settings.as_dict()["history_service"]["calendar_enabled"] is True
    assert HistoryServiceSettings().calendar_enabled is False


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
