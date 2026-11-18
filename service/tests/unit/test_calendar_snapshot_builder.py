from datetime import datetime, timezone, timedelta

import pytest

from service.infrastructure.mt5.calendar_providers import CalendarEvidence, CalendarProviderError
from service.infrastructure.mt5.calendar_snapshot_builder import CalendarSnapshotBuilder


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def evidence(source, symbol="BTC", broker="BTCUSD"):
    return CalendarEvidence(source=source, revision="r1", captured_at=NOW,
                            valid_from=NOW - timedelta(hours=1), valid_until=NOW + timedelta(days=1),
                            symbol=symbol, source_symbol=broker,
                            utc_windows=({"from": "2026-01-01T00:00:00Z", "until": "2026-01-02T00:00:00Z"},),
                            session_state="open")


class Provider:
    def __init__(self, item): self.item = item
    def fetch(self, symbol): return self.item


def session(): return {"fingerprint": {"id": "fp"}, "generation": 2, "epoch": "boot"}


def test_builder_requires_both_sources_and_binds_session():
    result = CalendarSnapshotBuilder(Provider(evidence("mt5")), Provider(evidence("broker")), symbols=("BTC",)).fetch(session=session())
    assert result.session_fingerprint == "fp"
    assert result.session_generation == 2
    assert result.symbols["BTC"]["source_symbol"] == "BTCUSD"


def test_builder_rejects_conflicting_windows():
    other = CalendarEvidence(source="broker", revision="r2", captured_at=NOW,
                             valid_from=NOW - timedelta(hours=1), valid_until=NOW + timedelta(days=1),
                             symbol="BTC", source_symbol="BTCUSD",
                             utc_windows=({"from": "2026-01-01T01:00:00Z", "until": "2026-01-02T00:00:00Z"},), session_state="open")
    with pytest.raises(CalendarProviderError, match="conflict"):
        CalendarSnapshotBuilder(Provider(evidence("mt5")), Provider(other), symbols=("BTC",)).fetch(session=session())
