#!/usr/bin/env python3
"""Redacted GET-only recovery diagnostic, not broker/mutation certification."""
import argparse
from datetime import datetime
from decimal import Decimal, InvalidOperation
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request

SYMBOLS = ("XAUUSDm", "EURUSDm", "GBPUSDm", "BTC")
SPEC = set("symbol digits point minimum_volume maximum_volume volume_step tick_size tick_value contract_size observed_at stops_level freeze_level margin_per_lot".split())
QUOTE = set("symbol broker_symbol bid ask quote_at observed_at age_seconds max_age_seconds fresh account_session".split())
RISK = set("loss_tick_value tick_value_loss loss_calculation_buy loss_calculation_sell tick_value_currency account_currency currency_relationship loss_value_basis tick_size point volume_min volume_step stops_level freeze_level entry_deviation_points close_deviation_points deviation_price risk_ready".split())


def identity(session):
    if not isinstance(session, dict):
        return None
    fingerprint = session.get("fingerprint")
    if isinstance(fingerprint, dict):
        fingerprint = fingerprint.get("id")
    generation, epoch = session.get("generation"), session.get("epoch")
    if (not isinstance(fingerprint, str) or not fingerprint or type(generation) is not int
            or generation < 1 or not isinstance(epoch, str) or not epoch):
        return None
    return fingerprint, generation, epoch


def number(value):
    if isinstance(value, bool):
        raise ValueError
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError
    return value


def instant(value):
    if not isinstance(value, str):
        raise ValueError
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError
    return result


def health_ok(data):
    if not isinstance(data, dict):
        return False
    policy, session = data.get("execution_authorization"), data.get("account_session")
    if not isinstance(policy, dict) or not isinstance(session, dict):
        return False
    if any(not isinstance(policy.get(gate), dict) for gate in ("entry", "exit")):
        return False
    return (data.get("ready") is True and data.get("mt5_connected") is True
            and data.get("status") == "healthy" and data.get("account_mode") == "DEMO"
            and data.get("account_policy") == "DEMO" and data.get("mutation_enabled") is False
            and data.get("mutation_ready") is False and policy.get("authorization_required") is True
            and policy["entry"].get("enabled") is False and policy["exit"].get("enabled") is False
            and session.get("ready") is True and identity(session) is not None)


def quote_ok(data, symbol, expected, currency, risk=False):
    if not isinstance(data, dict) or not (QUOTE | (RISK if risk else set())) <= data.keys():
        return False
    session = data["account_session"]
    if (data["symbol"] != symbol or not isinstance(data["broker_symbol"], str)
            or not data["broker_symbol"] or expected is None or identity(session) != expected
            or not isinstance(session, dict) or session.get("account_mode") != "DEMO"
            or session.get("ready") is not True):
        return False
    age = number(data["age_seconds"])
    reported_age = Decimal(str((instant(data["observed_at"]) - instant(data["quote_at"])).total_seconds()))
    if (abs(reported_age-age) > Decimal("0.01") or type(data["fresh"]) is not bool
            or type(data["max_age_seconds"]) is not int or data["max_age_seconds"] != 5
            or data["fresh"] != (0 <= age <= 5)
            or not 0 < number(data["bid"]) <= number(data["ask"])):
        return False
    if risk:
        loss_comparisons = ("tick_value_loss", "loss_calculation_buy", "loss_calculation_sell")
        positive = "loss_tick_value tick_value_loss loss_calculation_buy loss_calculation_sell tick_size point volume_min volume_step".split()
        if (any(number(data[k]) <= 0 for k in positive)
                or any(number(data[k]) < 0 for k in ("stops_level", "freeze_level"))
                or currency is None or data["tick_value_currency"] != currency or data["account_currency"] != currency
                or data["currency_relationship"] != "order_calc_profit_account_currency"
                or data["loss_value_basis"] != "max_mt5_loss_and_buy_sell_one_tick_loss_per_lot"
                or number(data["loss_tick_value"]) < max(number(data[k]) for k in loss_comparisons)
                or type(data["entry_deviation_points"]) is not int or type(data["close_deviation_points"]) is not int
                or data["entry_deviation_points"] != 20 or data["close_deviation_points"] != 20
                or number(data["deviation_price"]) != number(data["point"])*20
                or type(data["risk_ready"]) is not bool or data["risk_ready"] != data["fresh"]):
            return False
    return True


def run(fetch):
    rows, snapshots = [], {}
    paths = [("health_before", "/health"), ("account", "/account"), ("orders", "/orders"),
             ("positions", "/positions"), ("deals", "/deals")]
    for symbol in SYMBOLS:
        paths.extend((f"{symbol}:{kind}", f"/symbols/{symbol}" + ("" if kind == "spec" else f"/{kind}"))
                     for kind in ("spec", "quote", "risk"))
    paths.append(("health_after", "/health"))
    for name, path in paths:
        status, body = fetch(path)
        ok = (status == 200 and isinstance(body, dict) and type(body.get("schema_version")) is int
              and body["schema_version"] == 1 and "data" in body and "error" not in body)
        data = body.get("data") if ok else None
        try:
            expected = identity(snapshots.get("health_before", {}).get("account_session"))
            currency = snapshots.get("account", {}).get("currency")
            if name.startswith("health"):
                ok = ok and health_ok(data)
            elif name == "account":
                fields = set("account_id currency balance equity margin free_margin observed_at".split())
                ok = ok and isinstance(data, dict) and fields <= data.keys() and isinstance(data["currency"], str) and bool(data["currency"])
            elif name in {"orders", "positions", "deals"}:
                ok = ok and isinstance(data, list)
            else:
                symbol, kind = name.split(":")
                if kind == "spec":
                    ok = ok and isinstance(data, dict) and SPEC <= data.keys() and data["symbol"] == symbol
                else:
                    ok = ok and quote_ok(data, symbol, expected, currency, kind == "risk")
            if isinstance(data, dict):
                snapshots[name] = data
        except (AttributeError, KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
            ok = False
        row = {"check": name, "http_status": status, "contract_ok": bool(ok)}
        if ok and isinstance(data, list):
            row["count"] = len(data)
        if ok and ":" in name and not name.endswith(":spec"):
            row["reported_fresh"] = data["fresh"]
            if name.endswith(":risk"):
                row["reported_risk_ready"] = data["risk_ready"]
        rows.append(row)
    before = identity(snapshots.get("health_before", {}).get("account_session"))
    after = identity(snapshots.get("health_after", {}).get("account_session"))
    stable = before is not None and before == after
    pairs_match = all(snapshots.get(f"{s}:quote", {}).get("broker_symbol") is not None
                      and snapshots.get(f"{s}:quote", {}).get("broker_symbol") == snapshots.get(f"{s}:risk", {}).get("broker_symbol") for s in SYMBOLS)
    readable = all(row["contract_ok"] for row in rows) and stable and pairs_match
    return {"checks": rows, "request_count": len(rows), "session_stable": stable,
            "quote_risk_mapping_match": pairs_match, "read_only_contract_pass": readable,
            "quote_risk_reported_fresh": readable and all(snapshots.get(f"{s}:{k}", {}).get("fresh") is True for s in SYMBOLS for k in ("quote", "risk")),
            "current_utc_freshness_certified": False, "mutation_attempted": False, "mutation_certified": False}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def read_key(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "r", encoding="ascii") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError
        key = stream.read(4097).strip()
        if not key or len(key) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in key):
            raise ValueError
        return key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Trusted execution origin ending in /api/v1")
    parser.add_argument("--key-file", required=True, help="Operator-owned private API key file")
    args = parser.parse_args()
    try:
        url = urllib.parse.urlsplit(args.base_url)
        if (url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path.rstrip("/") != "/api/v1"):
            raise ValueError
        key = read_key(args.key_file)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        def fetch(path):
            request = urllib.request.Request(args.base_url.rstrip("/")+path,
                                            headers={"X-API-Key": key, "Accept": "application/json"}, method="GET")
            try:
                with opener.open(request, timeout=10) as response:
                    return response.status, json.loads(response.read(4_000_001))
            except urllib.error.HTTPError as error:
                return error.code, None
            except Exception:
                return 0, None
        result = run(fetch)
        print(json.dumps(result, sort_keys=True))
        return 0 if result["read_only_contract_pass"] else 1
    except Exception:
        print(json.dumps({"probe_setup_ok": False, "mutation_attempted": False}))
        return 2


if __name__ == "__main__":
    sys.exit(main())
