import importlib.util
import json
from pathlib import Path
import sys


PROBE = Path(__file__).resolve().parents[1] / "e2e" / "required_read_probe.py"
spec = importlib.util.spec_from_file_location("required_read_probe", PROBE)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


def session(identifier="synthetic-session", generation=4):
    return {
        "state": "ready",
        "ready": True,
        "generation": generation,
        "fingerprint": {"id": identifier, "account_mode": "DEMO"},
    }


def health(identity):
    return {"status": 200, "body": {"status": "healthy", "ready": True,
                                     "account_session": identity}}


def test_missing_key_is_required_failure_without_any_http_request():
    called = []
    result = probe.run(lambda *_: called.append(True), api_key=None)
    assert result["outcome"] == "fail"
    assert result["reason"] == "missing_api_key"
    assert called == []


def test_401_and_503_are_required_failures_not_skips():
    for status in (401, 503):
        result = probe.classify_probe(status, {"error": "unavailable"}, "account")
        assert result == {"http_status": status, "outcome": "fail", "reason": f"http_{status}"}


def test_optional_skip_requires_explicit_approval_and_reason():
    assert probe.optional_skip("optional socket client unavailable", approved=True) == {
        "outcome": "optional_skip", "reason": "optional socket client unavailable"
    }
    assert probe.optional_skip("", approved=True) == {
        "outcome": "fail", "reason": "unapproved_skip"
    }
    assert probe.optional_skip("missing key", approved=False) == {
        "outcome": "fail", "reason": "unapproved_skip"
    }


def test_account_missing_required_data_fails():
    assert probe.classify_probe(200, {"balance": 10}, "account") == {
        "http_status": 200, "outcome": "fail", "reason": "missing_account_fields"
    }


def test_account_requires_valid_readable_values():
    valid = {"login": 123, "server": "Demo", "balance": -1.5, "equity": 0}
    assert probe.classify_probe(200, valid, "account") == {
        "http_status": 200, "outcome": "required_pass"
    }
    invalid_accounts = (
        {**valid, "server": ""}, {**valid, "server": []},
        {**valid, "login": "123"}, {**valid, "login": True}, {**valid, "login": 0},
        {**valid, "balance": "1"}, {**valid, "balance": True},
        {**valid, "equity": float("nan")}, {**valid, "equity": float("inf")},
    )
    for account in invalid_accounts:
        result = probe.classify_probe(200, account, "account")
        assert result == {"http_status": 200, "outcome": "fail",
                          "reason": "invalid_account_data"}


def test_malformed_health_responses_are_unknown_without_raising():
    for response in (None, {"status": 200, "body": None},
                     {"status": 200, "body": {"account_session": None}},
                     {"status": 200, "body": {"account_session": []}}):
        assert probe.compare_sessions(response, response) == {
            "outcome": "unknown", "reason": "session_identity_unavailable"
        }


def test_only_demo_health_session_can_certify_required_reads():
    real = health({**session(), "fingerprint": {"id": "synthetic-session",
                                                    "account_mode": "REAL"}})
    unknown_mode = health({**session(), "fingerprint": {"id": "synthetic-session"}})
    assert probe.compare_sessions(real, real)["outcome"] == "unknown"
    assert probe.compare_sessions(unknown_mode, unknown_mode)["outcome"] == "unknown"


def test_valid_empty_position_and_history_lists_are_readable():
    for name in ("positions", "deals", "orders"):
        assert probe.classify_probe(200, [], name) == {"http_status": 200, "outcome": "required_pass"}


def test_required_lists_reject_non_object_items():
    malformed_items = ([None], [1], ["x"])
    for name in ("positions", "deals", "orders"):
        for body in malformed_items:
            assert probe.classify_probe(200, body, name) == {
                "http_status": 200, "outcome": "fail", "reason": "malformed_list_data"
            }


def test_health_session_mismatch_is_failure_but_unknown_is_unknown():
    mismatch = probe.compare_sessions(health(session("before")), health(session("after")))
    assert mismatch == {"outcome": "fail", "reason": "session_mismatch"}
    unknown = probe.compare_sessions(health(session()), {"status": 503, "body": None})
    assert unknown == {"outcome": "unknown", "reason": "session_identity_unavailable"}


def test_market_state_never_uses_heartbeat_as_quote_freshness():
    assert probe.market_outcome(session_state="healthy", quote=None, calendar="open") == "unknown"
    assert probe.market_outcome(session_state="healthy", quote=None, calendar="closed") == "market_closed"
    fresh_quote = {"age_seconds": 2, "fresh": True, "quote_at": "synthetic"}
    assert probe.market_outcome(session_state="ready", quote=fresh_quote, calendar="open") == "required_pass"
    assert probe.market_outcome(session_state="healthy", quote=None, calendar="unknown") == "unknown"


def test_run_classifies_required_gets_and_emits_no_payload_or_credentials():
    identity = session()
    responses = iter([
        (200, {"status": "healthy", "ready": True, "account_session": identity}),
        (200, {"login": 123, "server": "Demo", "balance": 1, "equity": 1}),
        (200, []), (200, []), (200, []),
        (200, {"status": "healthy", "ready": True, "account_session": identity}),
    ])
    result = probe.run(lambda *_: next(responses), api_key="synthetic-secret")
    assert result["required_pass"] is True
    assert result["session_stable"] is True
    assert result["market"] == "unknown"
    assert "synthetic-secret" not in repr(result)
    assert "Demo" not in repr(result)


def test_run_does_not_convert_auth_or_service_failure_into_skip():
    for failure_status in (401, 503):
        identity = session()
        responses = iter([
            (200, {"status": "healthy", "ready": True, "account_session": identity}),
            (failure_status, None), (200, []), (200, []), (200, []),
            (200, {"status": "healthy", "ready": True, "account_session": identity}),
        ])
        result = probe.run(lambda *_: next(responses), api_key="synthetic-only")
        assert result["outcome"] == "fail"
        assert result["checks"][1]["outcome"] == "fail"


def test_run_fails_if_health_bracket_observes_a_session_change():
    responses = iter([
        (200, {"status": "healthy", "ready": True, "account_session": session("before")}),
        (200, {"login": 123, "server": "Demo", "balance": 1, "equity": 1}),
        (200, []), (200, []), (200, []),
        (200, {"status": "healthy", "ready": True, "account_session": session("after")}),
    ])
    result = probe.run(lambda *_: next(responses), api_key="synthetic-only")
    assert result["outcome"] == "fail"
    assert result["checks"][-1] == {"check": "session_window", "outcome": "fail",
                                   "reason": "session_mismatch"}


def test_cli_setup_errors_are_redacted_json(capsys, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["required_read_probe.py", "--base-url", "http://[broken/api/v1"])
    assert probe.main() == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"outcome": "fail", "reason": "invalid_base_url",
                                  "mutation_attempted": False}
    assert "broken" not in output

    monkeypatch.setattr(sys, "argv", ["required_read_probe.py", "--base-url",
                                      "https://approved.example/api/v1", "--key-env", "bad-name"])
    assert probe.main() == 2
    output = capsys.readouterr().out
    assert json.loads(output) == {"outcome": "fail", "reason": "invalid_key_env",
                                  "mutation_attempted": False}
    assert "approved.example" not in output
