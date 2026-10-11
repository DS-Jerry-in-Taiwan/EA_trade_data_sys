from datetime import datetime, timezone
import pytest
from service.domain.market_sessions import evaluate_history


def dt(value):
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


@pytest.fixture
def evidence():
    return dict(source="synthetic fixture", observed_at="2026-10-01T00:00:00Z",
                timezone="UTC", timezone_source="synthetic fixture",
                exceptions_source="synthetic fixture: no exceptions in window",
                valid_from="2026-10-01T00:00:00Z", valid_until="2026-10-31T00:00:00Z",
                weekly={str(day): ([["00:05", "23:59"]] if day < 5 else [])
                        for day in range(7)})


@pytest.mark.parametrize("bar,now,accepted,state", [
    ("2026-10-09T12:00:00", "2026-10-09T12:05:00", True, "open"),
    ("2026-10-09T11:00:00", "2026-10-09T12:05:00", False, "open"),
    ("2026-10-09T23:55:00", "2026-10-11T12:00:00", True, "closed"),
    ("2026-10-08T23:55:00", "2026-10-11T12:00:00", False, "closed"),
    ("2026-10-08T23:55:00", "2026-10-09T00:02:00", True, "closed"),
    ("2026-10-09T00:05:00", "2026-10-09T00:05:00", True, "open"),
    ("2026-10-09T23:55:00", "2026-10-09T23:59:00", True, "closed"),
])
def test_verdict(evidence, bar, now, accepted, state):
    result = evaluate_history(dt(bar), dt(now), 900, evidence)
    assert (result.accepted, result.state) == (accepted, state)


@pytest.mark.parametrize("missing", ["timezone_source", "exceptions_source", "source"])
def test_unknown_evidence(evidence, missing):
    del evidence[missing]
    result = evaluate_history(dt("2026-10-09T12:00:00"), dt("2026-10-09T12:05:00"), 900, evidence)
    assert not result.accepted and result.state == "unknown"


def test_no_calendar():
    assert not evaluate_history(dt("2026-10-09T12:00:00"), dt("2026-10-09T12:05:00"), 900).accepted


def test_timezone_and_exception(evidence):
    evidence["timezone"] = "Asia/Taipei"
    evidence["exceptions"] = {"2026-10-09": []}
    result = evaluate_history(dt("2026-10-08T15:55:00"), dt("2026-10-09T04:00:00"), 900, evidence)
    assert result.accepted and result.state == "closed"


def test_expired_and_future(evidence):
    assert evaluate_history(dt("2026-11-01T00:00:00"), dt("2026-11-01T00:00:00"), 900, evidence).state == "unknown"
    assert evaluate_history(dt("2026-10-09T12:06:00"), dt("2026-10-09T12:05:00"), 900, evidence).state == "unknown"


@pytest.mark.parametrize("instant,state", [
    ("2026-10-08T16:04:00", "closed"),
    ("2026-10-08T16:05:00", "open"),
])
def test_local_midnight_boundary(evidence, instant, state):
    evidence["timezone"] = "Asia/Taipei"
    result = evaluate_history(dt("2026-10-08T15:55:00"), dt(instant), 900, evidence)
    assert result.accepted and result.state == state


def test_invalid_zone(evidence):
    evidence["timezone"] = "Unverified/BrokerZone"
    assert evaluate_history(dt("2026-10-09T12:00:00"), dt("2026-10-09T12:05:00"), 900, evidence).state == "unknown"


def test_ambiguous_dst_fails_closed(evidence):
    evidence.update(timezone="America/New_York", valid_until="2026-11-10T00:00:00Z")
    evidence["weekly"]["6"] = [["01:30", "03:00"]]
    assert evaluate_history(dt("2026-11-01T06:00:00"), dt("2026-11-01T07:00:00"), 900, evidence).state == "unknown"


@pytest.mark.parametrize("intervals", [None, "00:05", [["12:00"]],
    [[12, "13:00"]], [["23:00", "00:00"]], [["00:00", "24:00"]],
    [["12:00", "13:00"], ["12:30", "14:00"]]])
def test_malformed_intervals(evidence, intervals):
    evidence["weekly"]["0"] = intervals
    assert evaluate_history(dt("2026-10-09T12:00:00"), dt("2026-10-09T12:05:00"), 900, evidence).state == "unknown"


def test_missing_weekday(evidence):
    del evidence["weekly"]["6"]
    assert evaluate_history(dt("2026-10-09T12:00:00"), dt("2026-10-09T12:05:00"), 900, evidence).state == "unknown"


@pytest.mark.parametrize("field,value", [("observed_at", 1), ("exceptions", []),
    ("valid_until", "2026-10-31T00:00:00"), ("exceptions", {"bad-date": []})])
def test_malformed_evidence(evidence, field, value):
    evidence[field] = value
    assert evaluate_history(dt("2026-10-09T12:00:00"), dt("2026-10-09T12:05:00"), 900, evidence).state == "unknown"


def test_nonexistent_dst_fails_closed(evidence):
    evidence.update(timezone="America/New_York", observed_at="2026-03-01T00:00:00Z",
                    valid_from="2026-03-01T00:00:00Z", valid_until="2026-03-31T00:00:00Z")
    evidence["weekly"]["6"] = [["02:30", "04:00"]]
    assert evaluate_history(dt("2026-03-08T06:00:00"), dt("2026-03-08T08:00:00"), 900, evidence).state == "unknown"
