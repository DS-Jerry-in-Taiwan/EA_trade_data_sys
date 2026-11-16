"""Executable composition root for the history worker process."""

from service.config import load_settings
from service.history.worker import HistoryService
from service.infrastructure.mt5.calendar import calendar_from_mapping


def main():
    settings = load_settings()
    service = HistoryService(settings=settings, calendar_adapter=calendar_from_mapping(settings.history_service.calendar))
    try:
        service.run()
    finally:
        service.close()


if __name__ == "__main__":
    main()
