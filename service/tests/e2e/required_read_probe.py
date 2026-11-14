#!/usr/bin/env python3
"""GET-only required authenticated read probe; no broker actions are possible."""

import argparse
from datetime import datetime, timezone
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


REQUIRED_LISTS = {"positions", "deals", "orders"}
ACCOUNT_FIELDS = {"login", "server", "balance", "equity"}


def optional_skip(reason, *, approved):
    """A skip is valid only for a documented optional check with approval."""
    if approved and isinstance(reason, str) and reason.strip():
        return {"outcome": "optional_skip", "reason": reason.strip()}
    return {"outcome": "fail", "reason": "unapproved_skip"}


def classify_probe(status, body, name):
    if status != 200:
        return {"http_status": status, "outcome": "fail", "reason": f"http_{status}"}
    if name == "account":
        if not isinstance(body, dict) or not ACCOUNT_FIELDS <= body.keys():
            return {"http_status": status, "outcome": "fail", "reason": "missing_account_fields"}
        if any(body.get(field) is None for field in ACCOUNT_FIELDS):
            return {"http_status": status, "outcome": "fail", "reason": "missing_account_fields"}
        return {"http_status": status, "outcome": "required_pass"}
    if name in REQUIRED_LISTS:
        if not isinstance(body, list):
            return {"http_status": status, "outcome": "fail", "reason": "missing_list_data"}
        return {"http_status": status, "outcome": "required_pass"}
    return {"http_status": status, "outcome": "fail", "reason": "unknown_probe"}


def _session_identity(response):
    if not isinstance(response, dict) or response.get("status") != 200:
        return None
    body = response.get("body")
    session = body.get("account_session") if isinstance(body, dict) else None
    fingerprint = session.get("fingerprint") if isinstance(session, dict) else None
    identifier = fingerprint.get("id") if isinstance(fingerprint, dict) else None
    generation = session.get("generation") if isinstance(session, dict) else None
    if (session.get("ready") is not True or session.get("state") != "ready"
            or not isinstance(identifier, str) or not identifier
            or type(generation) is not int or generation < 1):
        return None
    return identifier, generation


def compare_sessions(before, after):
    left, right = _session_identity(before), _session_identity(after)
    if left is None or right is None:
        return {"outcome": "unknown", "reason": "session_identity_unavailable"}
    if left != right:
        return {"outcome": "fail", "reason": "session_mismatch"}
    return {"outcome": "required_pass"}


def market_outcome(*, session_state, quote, calendar):
    """Classify explicit evidence; health/heartbeat is never quote freshness."""
    if calendar == "closed":
        return "market_closed"
    if calendar != "open" or session_state != "ready":
        return "unknown"
    if (not isinstance(quote, dict) or quote.get("fresh") is not True
            or quote.get("quote_at") is None):
        return "unknown"
    age = quote.get("age_seconds")
    if isinstance(age, bool) or not isinstance(age, (int, float)) or not 0 <= age <= 5:
        return "fail"
    return "required_pass"


def run(fetch, *, api_key):
    if not api_key:
        return {"outcome": "fail", "reason": "missing_api_key", "checks": [],
                "required_pass": False, "mutation_attempted": False}

    checks = []
    before_status, before_body = fetch("/health", api_key)
    before = {"status": before_status, "body": before_body}
    checks.append({"check": "health_before", "http_status": before_status, "outcome":
                   "required_pass" if _session_identity(before) else "unknown"})

    for name, path in (("account", "/account"), ("positions", "/positions"),
                       ("deals", "/history/deals?days=7"),
                       ("orders", "/history/orders?days=7")):
        status, body = fetch(path, api_key)
        checks.append({"check": name, **classify_probe(status, body, name)})

    after_status, after_body = fetch("/health", api_key)
    after = {"status": after_status, "body": after_body}
    session_result = compare_sessions(before, after)
    checks.append({"check": "health_after", "http_status": after_status,
                   "outcome": "required_pass" if _session_identity(after) else "unknown"})
    checks.append({"check": "session_window", **session_result})
    required_checks = checks[1:5]
    required_pass = (all(row["outcome"] == "required_pass" for row in required_checks)
                     and session_result["outcome"] == "required_pass")
    outcome = "required_pass" if required_pass else (
        "fail" if any(row["outcome"] == "fail" for row in checks) else "unknown")
    return {
        "outcome": outcome,
        "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "required_pass": required_pass,
        "session_stable": session_result["outcome"] == "required_pass",
        "session_scope": "health identities bracket the request window; API responses do not expose per-response session identity",
        "market": "unknown",
        "market_reason": "this probe does not establish broker calendar state or quote freshness",
        "heartbeat_is_quote_freshness": False,
        "mutation_attempted": False,
    }


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Trusted gateway URL ending in /api/v1")
    parser.add_argument("--key-env", default="READONLY_API_KEY",
                        help="Environment variable containing the readonly API key")
    args = parser.parse_args()
    parsed = urllib.parse.urlsplit(args.base_url)
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path.rstrip("/") != "/api/v1"):
        print(json.dumps({"outcome": "fail", "reason": "invalid_base_url",
                          "mutation_attempted": False}, sort_keys=True))
        return 2
    key = os.environ.get(args.key_env)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def fetch(path, api_key):
        request = urllib.request.Request(
            args.base_url.rstrip("/") + path,
            headers={"X-API-Key": api_key, "Accept": "application/json"}, method="GET",
        )
        try:
            with opener.open(request, timeout=10) as response:
                return response.status, json.loads(response.read(4_000_001))
        except urllib.error.HTTPError as error:
            return error.code, None
        except Exception:
            return 0, None

    result = run(fetch, api_key=key)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["required_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
