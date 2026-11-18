from datetime import datetime, timezone, timedelta

from service.history.worker import HistoryService
from service.infrastructure.mt5.calendar import CalendarObservation
from service.tests.unit.test_history_service import FakeMT5Client, _bar
from service.config.models import Settings


def _settings(tmp_path):
    return Settings.from_mapping({
        "history_service": {
            "symbols": [{"name": "BTC", "timeframes": ["M5"]}],
            "data_path": str(tmp_path),
            "minimum_bars": {"M5": 1},
            "fetch_margin_bars": 0,
            "retention_margin_bars": 0,
        }
    }, environ={})


class Calendar:
    authoritative = True

    def __init__(self, state):
        self.state = state
        self.calls = []

    def current_state(self, symbol, now, timeframe):
        self.calls.append((symbol, timeframe))
        return CalendarObservation(
            state=self.state, effective_from=now,
            effective_until=now + timedelta(minutes=1),
            source="test", revision="r1", reason="test",
        )

    def classify_gap(self, *args):
        return CalendarObservation(
            state="unknown", effective_from=args[1], effective_until=args[2],
            source="test", revision="r1", reason="test",
        )


def test_closed_market_skips_latest_fetch(tmp_path):
    client = FakeMT5Client([_bar("2026-09-08T10:00:00Z")])
    (tmp_path / "BTC_M5.csv").write_text(
        "time,open,high,low,close,tick_volume,source_symbol\n"
        "2026-09-08T10:00:00Z,100,101,99,100,10,broker-BTC\n"
    )
    (tmp_path / "BTC_M5.csv.ready").write_text("broker-BTC")
    service = HistoryService(settings=_settings(tmp_path), mt5_client=client,
                             calendar_service=Calendar("closed"))
    service.fetch_incremental(now=datetime(2026, 9, 8, 12, tzinfo=timezone.utc))
    assert client.mt5.requests == []
    assert service.get_sync_status()["BTC:M5"]["state"] == "closed"


def test_unknown_calendar_allows_one_bounded_bootstrap_then_skips(tmp_path):
    # The bootstrap asks for one closed bar plus the forming-bar margin.  Give
    # the fake both rows so the bounded page is satisfied in one bridge call;
    # a one-row fake would force a legitimate second pagination request.
    client = FakeMT5Client([
        _bar("2026-09-08T10:00:00Z"),
        _bar("2026-09-08T10:05:00Z"),
    ])
    calendar = Calendar("unknown")
    service = HistoryService(settings=_settings(tmp_path), mt5_client=client,
                             calendar_service=calendar)
    now = datetime(2026, 9, 8, 12, tzinfo=timezone.utc)
    service.fetch_incremental(now=now)
    assert len(client.mt5.requests) == 1
    service.fetch_incremental(now=now)
    assert len(client.mt5.requests) == 1
    assert service.get_sync_status()["BTC:M5"]["state"] == "unknown"
