"""Evidence-only history freshness; no broker-name or weekend heuristics."""
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class HistoryVerdict:
    accepted: bool
    state: str
    reason: str


def evaluate_history(last_bar, now, maximum_age, evidence=None):
    """Closed sessions require a bar near the last proven close, not unlimited age.

    Evidence must cover exceptions for the entire search window. UTC timestamps
    in evidence are absolute; weekly sessions use an explicit IANA timezone.
    A calendar is verification input, never authorization to trade.
    """
    unknown = lambda reason: HistoryVerdict(False, "unknown", reason)
    if not evidence:
        return unknown("session evidence missing")
    try:
        if not all(evidence.get(k) for k in (
            "source", "observed_at", "timezone", "timezone_source",
            "exceptions_source", "valid_from", "valid_until",
        )):
            return unknown("incomplete timezone/provenance/exception evidence")
        if not all(isinstance(evidence[k], str) and evidence[k].strip() for k in (
            "source", "observed_at", "timezone", "timezone_source",
            "exceptions_source", "valid_from", "valid_until",
        )):
            return unknown("invalid provenance types")
        weekly = evidence["weekly"]
        exceptions = evidence.get("exceptions", {})
        if not isinstance(weekly, dict) or set(weekly) != {str(d) for d in range(7)}:
            return unknown("all seven weekdays must be explicit")
        if not isinstance(exceptions, dict):
            return unknown("invalid exceptions")
        for date_key in exceptions:
            if datetime.fromisoformat(date_key).date().isoformat() != date_key:
                return unknown("invalid exception date")
        for pairs in list(weekly.values()) + list(exceptions.values()):
            if not isinstance(pairs, list):
                return unknown("invalid intervals")
            previous_close = None
            for pair in pairs:
                if not isinstance(pair, list) or len(pair) != 2 or not all(
                    isinstance(t, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", t)
                    for t in pair
                ):
                    return unknown("invalid interval time")
                opening, closing = pair
                if closing <= opening or (previous_close and opening < previous_close):
                    return unknown("unordered/overlapping/overnight interval unsupported")
                previous_close = closing
        parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00"))
        start, end = parse(evidence["valid_from"]), parse(evidence["valid_until"])
        observed = parse(evidence["observed_at"])
        if any(t.tzinfo is None for t in (last_bar, now, start, end, observed)):
            return unknown("naive timestamp")
        if not start <= now <= end or observed > now:
            return unknown("evidence outside validity window")
        if last_bar > now:
            return unknown("future bar")
        zone = ZoneInfo(evidence["timezone"])
        local = now.astimezone(zone)
        intervals = []
        # Eight days include the last weekly opening, unless closures exceed a week.
        for days in range(8):
            date = local.date() - timedelta(days=days)
            pairs = exceptions.get(date.isoformat(), weekly[str(date.weekday())])
            for opening, closing in pairs:
                a = datetime.fromisoformat(f"{date}T{opening}").replace(tzinfo=zone)
                b = datetime.fromisoformat(f"{date}T{closing}").replace(tzinfo=zone)
                if any(t.replace(fold=0).utcoffset() != t.replace(fold=1).utcoffset()
                       or t.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
                       != t.replace(tzinfo=None) for t in (a, b)):
                    return unknown("ambiguous or nonexistent DST boundary")
                if b <= a:
                    return unknown("overnight interval unsupported")
                if a.astimezone(timezone.utc) < start:
                    continue
                intervals.append((a, b))
        if any(a <= local < b for a, b in intervals):
            age = (now - last_bar).total_seconds()
            return HistoryVerdict(age <= maximum_age, "open", f"bar age={age:.0f}s")
        closes = [b.astimezone(timezone.utc) for _, b in intervals if b <= local]
        if not closes:
            return unknown("last close not covered by evidence")
        close = max(closes)
        age_at_close = (close - last_bar).total_seconds()
        return HistoryVerdict(0 <= age_at_close <= maximum_age, "closed",
                              f"bar age at proven close={age_at_close:.0f}s")
    except (KeyError, ValueError, TypeError, AttributeError):
        return unknown("invalid session evidence")


def configured_history_verdict(symbol, last_bar, now, maximum_age):
    """Optional non-secret operator evidence file; absent evidence fails explicitly."""
    path = os.environ.get("HISTORY_SESSION_EVIDENCE_PATH")
    try:
        with open(path, encoding="utf-8") as handle:
            evidence = json.load(handle)["symbols"].get(symbol)
    except (OSError, TypeError, ValueError, KeyError, AttributeError):
        evidence = None
    return evaluate_history(last_bar, now, maximum_age, evidence)
