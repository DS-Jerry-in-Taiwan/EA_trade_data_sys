"""Executable composition root for the history worker process."""

import signal

from service.config import load_settings
from service.history.worker import HistoryService, _handle_shutdown_signal
from service.infrastructure.mt5.calendar import calendar_from_mapping


def main():
    settings = load_settings()
    calendar_config = settings.history_service.calendar
    calendar = calendar_from_mapping(calendar_config) if calendar_config.get("enabled", False) else None
    service = HistoryService(settings=settings, calendar_adapter=calendar)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    try:
        service.run()
    finally:
        service.close()


if __name__ == "__main__":
    main()
