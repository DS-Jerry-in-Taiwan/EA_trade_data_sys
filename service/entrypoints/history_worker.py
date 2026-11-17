"""Executable composition root for the history worker process."""

import signal

from service.config import load_settings
from service.history.worker import HistoryService, _handle_shutdown_signal
from service.infrastructure.mt5.calendar import calendar_from_mapping
from service.infrastructure.mt5.calendar_composer import CalendarEvidenceComposer
from service.infrastructure.mt5.calendar import UnknownCalendar
from service.infrastructure.mt5.mt5_session_calendar import MT5SessionCalendarAdapter


def build_history_service(config_path=None, *, settings=None,
                          mt5_sessions=None, broker_calendar=None):
    """Build the history process with one, fail-closed calendar seam.

    The process owns the MT5 client, so the optional MT5 session source is
    attached to that client after construction.  Calendar evidence is always
    represented by ``CalendarEvidenceComposer``; disabled or incomplete
    production configuration therefore becomes an explicit ``UnknownCalendar``
    result instead of silently bypassing gap qualification.

    ``mt5_sessions`` and ``broker_calendar`` are injectable for the eventual
    MQL5 bridge and broker evidence adapters and for deterministic tests.  No
    credentials or runtime state are read by this assembly function.
    """
    settings = settings or load_settings(config_path)
    service = HistoryService(settings=settings)
    configured_broker = broker_calendar
    if configured_broker is None:
        configured_broker = calendar_from_mapping(settings.history_service.calendar)
    configured_mt5 = mt5_sessions
    if configured_mt5 is None:
        configured_mt5 = MT5SessionCalendarAdapter(
            mt5_client=service.mt5_client,
            symbol_resolver=service.mt5_client,
        )
    service.calendar_adapter = CalendarEvidenceComposer(
        mt5_sessions=configured_mt5 or UnknownCalendar(),
        broker_calendar=configured_broker or UnknownCalendar(),
    )
    return service


def main():
    service = build_history_service()
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    try:
        service.run()
    finally:
        service.close()


if __name__ == "__main__":
    main()
