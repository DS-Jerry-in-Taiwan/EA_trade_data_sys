"""Convert broker-local MT5 timestamp values into canonical UTC.

The FTMO/OANDA MetaTrader server changes its fixed GMT offset with the
European DST calendar. The schedule is explicit and validity bounded so a
quote cannot be treated as fresh when the configured evidence is stale.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite


@dataclass(frozen=True)
class BrokerTimeWindow:
    """A half-open broker wall-clock validity window and fixed GMT offset."""

    valid_from: datetime
    valid_until: datetime
    offset_seconds: int

    def __post_init__(self):
        if self.valid_from.tzinfo is not None or self.valid_until.tzinfo is not None:
            raise ValueError("broker schedule boundaries must be naive wall-clock values")
        if self.valid_until <= self.valid_from:
            raise ValueError("broker schedule valid_until must be after valid_from")
        if self.offset_seconds not in {7200, 10800}:
            raise ValueError("broker schedule offset must be GMT+2 or GMT+3")


@dataclass(frozen=True)
class BrokerTimeSettings:
    """Explicit broker timestamp schedule used by quote and tick ingestion."""

    timezone: str = "UTC"
    source: str = "configuration"
    schedule: tuple[BrokerTimeWindow, ...] = ()

    def offset_for(self, wall_clock: datetime) -> int:
        if wall_clock.tzinfo is not None:
            raise ValueError("wall_clock must be naive")
        if self.schedule:
            matches = [window for window in self.schedule
                       if window.valid_from <= wall_clock < window.valid_until]
            if len(matches) != 1:
                raise ValueError("broker timestamp is outside the configured validity schedule")
            return matches[0].offset_seconds
        if self.timezone == "UTC":
            return 0
        raise ValueError("broker timestamp schedule is not configured")


def normalize_mt5_epoch(value: int | float, settings: BrokerTimeSettings | None = None) -> datetime:
    """Return an aware UTC datetime for an MT5 server-local epoch value."""

    if isinstance(value, bool):
        raise ValueError("MT5 timestamp must be numeric")
    try:
        timestamp = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("MT5 timestamp must be numeric") from exc
    if not isfinite(timestamp):
        raise ValueError("MT5 timestamp must be finite")
    policy = settings or BrokerTimeSettings()
    wall_clock = datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
    offset = policy.offset_for(wall_clock)
    return (wall_clock - timedelta(seconds=offset)).replace(tzinfo=timezone.utc)


def normalize_mt5_wire(value: int | float, settings: BrokerTimeSettings | None = None) -> str:
    """Return an ISO-8601 UTC wire timestamp for an MT5 value."""

    return normalize_mt5_epoch(value, settings).isoformat().replace("+00:00", "Z")


def parse_schedule(raw) -> tuple[BrokerTimeWindow, ...]:
    """Parse bounded schedule entries from non-secret YAML configuration."""

    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValueError("broker_time.schedule must be a non-empty list")
    result = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise ValueError("broker_time.schedule entries must be mappings")
        try:
            start = datetime.fromisoformat(str(entry["valid_from"]).replace("Z", "+00:00"))
            end = datetime.fromisoformat(str(entry["valid_until"]).replace("Z", "+00:00"))
            if start.tzinfo is not None:
                start = start.replace(tzinfo=None)
            if end.tzinfo is not None:
                end = end.replace(tzinfo=None)
            offset = int(entry["offset_seconds"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid broker_time.schedule entry") from exc
        result.append(BrokerTimeWindow(start, end, offset))
    result.sort(key=lambda item: item.valid_from)
    for previous, current in zip(result, result[1:]):
        if current.valid_from < previous.valid_until:
            raise ValueError("broker timestamp schedule windows overlap")
    return tuple(result)
