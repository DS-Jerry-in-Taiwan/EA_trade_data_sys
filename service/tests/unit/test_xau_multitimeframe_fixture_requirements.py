"""Contract-level checks for the XAU M5/M15/H1 fixture set.

These tests use sanitized in-memory documents. The real XAU delivery must run
the same checks against three reviewed fixture documents.
"""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from datetime import timedelta

import pandas as pd
import pytest


CONTRACT = Path(__file__).parents[2] / "contracts" / "archive_dataset_sample.json"
REAL_FIXTURE_DIR = Path(__file__).parents[2] / "contracts" / "fixtures"
TIMEFRAMES = ("M5", "M15", "H1")


def _digest(bars):
    rows = []
    for bar in sorted(bars, key=lambda item: item["time"]):
        rows.append([
            int(pd.Timestamp(bar["time"]).timestamp()),
            bar["open"], bar["high"], bar["low"], bar["close"],
            bar.get("tick_volume", 0), bar.get("spread", 0),
            bar.get("real_volume", 0),
        ])
    payload = json.dumps(rows, separators=(",", ":"), ensure_ascii=False).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sample_set():
    base = json.loads(CONTRACT.read_text(encoding="utf-8"))
    documents = []
    for index, timeframe in enumerate(TIMEFRAMES):
        document = deepcopy(base)
        document["timeframe"] = timeframe
        document["dataset_id"] = f"xau-sanitized-{timeframe.lower()}-001"
        document["logical_symbol"] = "XAUUSDm"
        document["source_symbol"] = "XAUUSD"
        document["bars"][0]["close"] += index
        document["archive_digest"] = _digest(document["bars"])
        documents.append(document)
    return documents


def validate_xau_fixture_set(documents):
    """Return acceptance failures for a candidate XAU multi-timeframe set."""
    failures = []
    if {doc.get("timeframe") for doc in documents} != set(TIMEFRAMES):
        failures.append("M5, M15, and H1 are required")
    if len(documents) != 3:
        failures.append("exactly three timeframe documents are required")
    if not documents:
        return failures
    logical = {doc.get("logical_symbol") for doc in documents}
    source = {doc.get("source_symbol") for doc in documents}
    if len(logical) != 1 or len(source) != 1:
        failures.append("symbol mapping must be common across timeframes")
    if any(doc.get("contract_revision") != "archive.dataset.v1" for doc in documents):
        failures.append("archive.dataset.v1 is required")
    if any(doc.get("coverage", {}).get("closed_bars") is not True for doc in documents):
        failures.append("all coverage must be closed bars")
    ids = [doc.get("dataset_id") for doc in documents]
    digests = [doc.get("archive_digest") for doc in documents]
    if len(set(ids)) != len(ids):
        failures.append("dataset identities must be independent")
    if len(set(digests)) != len(digests):
        failures.append("timeframe digests must be independent")
    for doc in documents:
        if doc.get("archive_digest") != _digest(doc.get("bars", [])):
            failures.append(f"digest mismatch for {doc.get('timeframe')}")
        if len(doc.get("provenance", {}).get("source_commit", "")) != 40:
            failures.append(f"source provenance missing for {doc.get('timeframe')}")
        if len(doc.get("provenance", {}).get("deployment_commit", "")) != 40:
            failures.append(f"deployment provenance missing for {doc.get('timeframe')}")
    return failures


def validate_long_range_fixture_set(documents, minimum_days=90):
    """Return acceptance failures for a long-range candidate set.

    This validator intentionally reports a short reviewed fixture as a failure.
    It does not manufacture bars or treat a short representative fixture as a
    long-range acceptance result.
    """
    failures = validate_xau_fixture_set(documents)
    if not documents:
        return failures
    ranges = []
    for document in documents:
        coverage = document.get("coverage", {})
        try:
            start = pd.Timestamp(coverage["start_utc"])
            end = pd.Timestamp(coverage["end_utc"])
        except (KeyError, TypeError, ValueError):
            failures.append(f"coverage timestamps missing for {document.get('timeframe')}")
            continue
        if start.tzinfo is None or end.tzinfo is None or end < start:
            failures.append(f"coverage timestamps invalid for {document.get('timeframe')}")
            continue
        ranges.append((start.tz_convert("UTC"), end.tz_convert("UTC")))
    if ranges:
        common_start = max(start for start, _ in ranges)
        common_end = min(end for _, end in ranges)
        if common_end - common_start < timedelta(days=minimum_days):
            failures.append(f"common coverage is shorter than {minimum_days} days")
    return failures


def test_sanitized_xau_fixture_set_meets_multitimeframe_contract():
    documents = _sample_set()
    assert validate_xau_fixture_set(documents) == []


def test_published_xau_fixture_set_meets_multitimeframe_contract():
    documents = [
        json.loads((REAL_FIXTURE_DIR / f"xauusd_mtf_{timeframe}.json").read_text(encoding="utf-8"))
        for timeframe in TIMEFRAMES
    ]
    assert validate_xau_fixture_set(documents) == []
    assert {doc["logical_symbol"] for doc in documents} == {"XAUUSDm"}
    assert {doc["source_symbol"] for doc in documents} == {"XAUUSD.sim"}
    assert all(doc["coverage"]["bar_count"] >= 200 for doc in documents)


def test_representative_fixture_is_not_accepted_as_long_range():
    documents = [
        json.loads((REAL_FIXTURE_DIR / f"xauusd_mtf_{timeframe}.json").read_text(encoding="utf-8"))
        for timeframe in TIMEFRAMES
    ]
    failures = validate_long_range_fixture_set(documents, minimum_days=90)
    assert "common coverage is shorter than 90 days" in failures


@pytest.mark.parametrize("removed", TIMEFRAMES)
def test_xau_fixture_set_reports_missing_timeframe(removed):
    documents = [doc for doc in _sample_set() if doc["timeframe"] != removed]
    failures = validate_xau_fixture_set(documents)
    assert "M5, M15, and H1 are required" in failures
