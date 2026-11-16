import json

import pytest

from service.contracts.history_session_metadata import (
    MetadataContractError,
    validate_history_session_metadata,
)


@pytest.fixture
def fixtures():
    with open("service/contracts/fixtures/history_session_metadata_wire.json", encoding="utf-8") as handle:
        return json.load(handle)


def test_valid_and_fail_closed_metadata(fixtures):
    assert validate_history_session_metadata(fixtures["valid"])["history"]["session_state"] == "open"
    assert validate_history_session_metadata(fixtures["fail_closed"])["history"]["session_state"] == "unknown"


@pytest.mark.parametrize("name", ["rejected_unknown_state", "rejected_sensitive_field"])
def test_invalid_metadata_fails_closed(fixtures, name):
    with pytest.raises(MetadataContractError):
        validate_history_session_metadata(fixtures[name])


def test_gap_classification_is_restricted(fixtures):
    payload = json.loads(json.dumps(fixtures["valid"]))
    payload["history"]["gaps"] = [{
        "start": "2026-11-14T23:50:00Z",
        "end": "2026-11-15T01:05:00Z",
        "session_close": "2026-11-14T23:59:00Z",
        "session_reopen": "2026-11-15T01:05:00Z",
        "classification": "expected_session_break",
    }]
    assert validate_history_session_metadata(payload)["history"]["gaps"]
    payload["history"]["gaps"][0]["classification"] = "guessed"
    with pytest.raises(MetadataContractError):
        validate_history_session_metadata(payload)
