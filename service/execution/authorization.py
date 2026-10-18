"""Local-only durable, one-entry Demo authorization. Never performs MT5 calls."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import sqlite3
import re
from pathlib import Path

from .errors import ExecutionError
from .idempotency import payload_fingerprint


def _deny(code, message):
    raise ExecutionError(code, message, status=403)


def _utc(value):
    if not isinstance(value, str):
        raise ValueError("UTC timestamp required")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError("absolute UTC timestamp required")
    return parsed


class AuthorizationStore:
    FIELDS = {"authorization_id", "account_fingerprint", "session_generation", "symbol",
              "session_epoch", "minimum_volume", "client_order_id", "permissions", "entry_not_before",
              "entry_expires_at", "recovery_expires_at", "abort_owner"}

    def __init__(self, path):
        self.path = str(path)
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as c:
            c.execute("CREATE TABLE IF NOT EXISTS demo_authorization (id TEXT PRIMARY KEY, artifact TEXT NOT NULL, killed INTEGER NOT NULL DEFAULT 0, claim TEXT, order_id TEXT, position_id TEXT)")
            c.execute("CREATE TABLE IF NOT EXISTS demo_authorization_exit (authorization_id TEXT, operation TEXT, target_id TEXT, state TEXT NOT NULL, result TEXT, PRIMARY KEY(authorization_id, operation, target_id))")
            c.execute("CREATE TABLE IF NOT EXISTS demo_authorization_abort (id INTEGER PRIMARY KEY, authorization_id TEXT NOT NULL, abort_owner TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL)")

    def _connect(self):
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA synchronous=FULL")
        return c

    def provision(self, artifact):
        """Operator-local provisioning only; immutable IDs cannot be overwritten."""
        if not isinstance(artifact, dict) or set(artifact) != self.FIELDS:
            raise ValueError("authorization fields must match the strict schema")
        for key in ("authorization_id", "account_fingerprint", "session_epoch", "symbol", "client_order_id", "abort_owner"):
            if not isinstance(artifact[key], str) or not artifact[key].strip():
                raise ValueError("nonempty authorization strings required")
        if type(artifact["session_generation"]) is not int or artifact["session_generation"] < 1:
            raise ValueError("verified positive generation required")
        permissions = artifact["permissions"]
        if not isinstance(permissions, list) or not permissions or any(not isinstance(p, str) for p in permissions) or len(set(permissions)) != len(permissions) or any(p not in {"place", "cancel", "close"} for p in permissions):
            raise ValueError("invalid permissions")
        try:
            if not isinstance(artifact["minimum_volume"], str) or len(artifact["minimum_volume"]) > 64 or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", artifact["minimum_volume"]):
                raise ValueError("minimum volume must be a bounded decimal string")
            volume = Decimal(str(artifact["minimum_volume"]))
            if isinstance(artifact["minimum_volume"], bool) or not volume.is_finite() or volume <= 0:
                raise ValueError("positive finite minimum volume required")
        except InvalidOperation as exc:
            raise ValueError("invalid minimum volume") from exc
        start, end, recovery = (_utc(artifact[k]) for k in ("entry_not_before", "entry_expires_at", "recovery_expires_at"))
        if not start < end <= recovery:
            raise ValueError("invalid authorization windows")
        with self._connect() as c:
            c.execute("INSERT INTO demo_authorization(id, artifact) VALUES (?, ?)", (artifact["authorization_id"], json.dumps(artifact, sort_keys=True)))

    def _verified(self, c, auth_id, session):
        row = c.execute("SELECT * FROM demo_authorization WHERE id=?", (auth_id,)).fetchone()
        if row is None:
            _deny("authorization_required", "Scoped operator authorization is required")
        artifact = json.loads(row["artifact"])
        session = session or {}
        if session.get("ready") is not True:
            _deny("authorization_session_not_ready", "Verified ready session is required")
        fp = session.get("fingerprint")
        mode = session.get("account_mode")
        if isinstance(fp, dict):
            mode, fp = fp.get("account_mode"), fp.get("id")
        if mode != "DEMO":
            _deny("authorization_demo_required", "Verified Demo account is required")
        if fp != artifact["account_fingerprint"] or session.get("generation") != artifact["session_generation"] or session.get("epoch") != artifact["session_epoch"]:
            _deny("authorization_session_changed", "Account/session changed; operator escalation required")
        return row, artifact

    def claim_entry(self, auth_id, session, payload, now=None):
        now = now or datetime.now(timezone.utc)
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row, a = self._verified(c, auth_id, session)
            intent = payload.get("intent", payload)
            if "place" not in a["permissions"] or intent.get("client_order_id") != a["client_order_id"] or intent.get("symbol") != a["symbol"]:
                _deny("authorization_scope_mismatch", "Entry is outside authorized scope")
            try:
                matches = Decimal(str(intent.get("volume"))) == Decimal(str(a["minimum_volume"]))
            except InvalidOperation:
                matches = False
            if not matches:
                _deny("authorization_volume_mismatch", "Exact authorized minimum volume required")
            fingerprint = payload_fingerprint(payload)
            if row["claim"]:
                if row["claim"] != fingerprint:
                    _deny("authorization_payload_conflict", "Authorization was claimed with another payload")
                return False
            if row["killed"] or not _utc(a["entry_not_before"]) <= now < _utc(a["entry_expires_at"]):
                _deny("authorization_entry_closed", "New-entry authorization is closed")
            c.execute("UPDATE demo_authorization SET claim=? WHERE id=?", (fingerprint, auth_id))
            return True

    def bind_exposure(self, auth_id, session, *, order_id=None, position_id=None, verified=False):
        if verified is not True or (order_id is None and position_id is None):
            _deny("authorization_exposure_unverified", "Verified accepted exposure required")
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            row, _ = self._verified(c, auth_id, session)
            if not row["claim"]:
                _deny("authorization_exposure_unverified", "No authorized entry claim")
            for field, value in (("order_id", order_id), ("position_id", position_id)):
                if value is not None:
                    if isinstance(value, bool) or not str(value).isdigit() or int(value) <= 0 or (row[field] and row[field] != str(value)):
                        _deny("authorization_exposure_conflict", "Exposure binding is invalid or immutable")
                    c.execute(f"UPDATE demo_authorization SET {field}=? WHERE id=?", (str(value), auth_id))

    def authorize_entry(self, auth_id, session, payload, now=None):
        """Recheck immediately inside the guarded send lock; replay is not send permission."""
        now = now or datetime.now(timezone.utc)
        with self._connect() as c:
            row, artifact = self._verified(c, auth_id, session)
            if not row["claim"] or row["claim"] != payload_fingerprint(payload):
                _deny("authorization_payload_conflict", "Matching durable entry claim is required")
            if row["killed"] or "place" not in artifact["permissions"] or not _utc(artifact["entry_not_before"]) <= now < _utc(artifact["entry_expires_at"]):
                _deny("authorization_entry_closed", "New-entry authorization is closed")

    validate_entry = authorize_entry

    def _exit(self, c, auth_id, session, operation, target_id, now):
        row, a = self._verified(c, auth_id, session)
        field = {"cancel": "order_id", "close": "position_id"}.get(operation)
        if not field or operation not in a["permissions"] or not row[field] or row[field] != str(target_id):
            _deny("authorization_exit_scope_mismatch", "Exit target is not verified authorized exposure")
        if now >= _utc(a["recovery_expires_at"]):
            _deny("authorization_recovery_expired", "Recovery window expired; operator escalation required")

    def authorize_exit(self, auth_id, session, operation, target_id, now=None):
        with self._connect() as c:
            self._exit(c, auth_id, session, operation, target_id, now or datetime.now(timezone.utc))

    def reserve_exit(self, auth_id, session, operation, target_id, now=None):
        with self._connect() as c:
            c.execute("BEGIN IMMEDIATE")
            self._exit(c, auth_id, session, operation, target_id, now or datetime.now(timezone.utc))
            row = c.execute("SELECT state,result FROM demo_authorization_exit WHERE authorization_id=? AND operation=? AND target_id=?", (auth_id, operation, str(target_id))).fetchone()
            if row:
                return {"state": row["state"], "result": json.loads(row["result"]) if row["result"] else None}, False
            c.execute("INSERT INTO demo_authorization_exit VALUES(?,?,?,'pending',NULL)", (auth_id, operation, str(target_id)))
            return {"state": "pending", "result": None}, True

    def exit_record(self, auth_id, operation, target_id):
        """Internal lookup only; caller must independently authorize current identity."""
        with self._connect() as c:
            row = c.execute("SELECT state,result FROM demo_authorization_exit WHERE authorization_id=? AND operation=? AND target_id=?", (auth_id, operation, str(target_id))).fetchone()
            return {"state": row["state"], "result": json.loads(row["result"]) if row["result"] else None} if row else None

    def finish_exit(self, auth_id, operation, target_id, state, result):
        if state not in {"succeeded", "failed", "indeterminate"}:
            raise ValueError("invalid exit state")
        with self._connect() as c:
            c.execute("UPDATE demo_authorization_exit SET state=?,result=? WHERE authorization_id=? AND operation=? AND target_id=? AND state='pending'", (state, json.dumps(result), auth_id, operation, str(target_id)))
            if state != "succeeded":
                c.execute("UPDATE demo_authorization SET killed=1 WHERE id=?", (auth_id,))
                self._abort(c, auth_id, "exit_" + state)

    def disable_entry(self, auth_id, reason="operator"):
        with self._connect() as c:
            c.execute("UPDATE demo_authorization SET killed=1 WHERE id=?", (auth_id,))
            self._abort(c, auth_id, reason)

    def _abort(self, connection, auth_id, reason):
        row = connection.execute("SELECT artifact FROM demo_authorization WHERE id=?", (auth_id,)).fetchone()
        if row:
            owner = json.loads(row["artifact"])["abort_owner"]
            connection.execute("INSERT INTO demo_authorization_abort(authorization_id,abort_owner,reason,created_at) VALUES(?,?,?,?)", (auth_id, owner, str(reason), datetime.now(timezone.utc).isoformat()))

    def abort_events(self):
        """Local operator outbox. External notification delivery remains operator-owned."""
        with self._connect() as c:
            return [dict(row) for row in c.execute("SELECT * FROM demo_authorization_abort ORDER BY id")]

    def scope(self, auth_id, session):
        """Internal verified scope; never serialize as a public health response."""
        with self._connect() as c:
            row, artifact = self._verified(c, auth_id, session)
            return dict(artifact, order_id=row["order_id"], position_id=row["position_id"], entry_claimed=bool(row["claim"]))

    def find_by_client(self, client_order_id, session):
        with self._connect() as c:
            matches = []
            for row in c.execute("SELECT id,artifact FROM demo_authorization").fetchall():
                if json.loads(row["artifact"])["client_order_id"] == client_order_id:
                    self._verified(c, row["id"], session)
                    matches.append(row["id"])
            if len(matches) > 1:
                _deny("authorization_ambiguous", "Multiple grants match client reference")
            return matches[0] if matches else None

    def policy(self, session, now=None):
        now = now or datetime.now(timezone.utc)
        entry = exit_enabled = False
        with self._connect() as c:
            for row in c.execute("SELECT id FROM demo_authorization").fetchall():
                try:
                    record, a = self._verified(c, row["id"], session)
                except ExecutionError:
                    continue
                entry |= not record["killed"] and not record["claim"] and "place" in a["permissions"] and _utc(a["entry_not_before"]) <= now < _utc(a["entry_expires_at"])
                exit_enabled |= bool(record["order_id"] or record["position_id"]) and now < _utc(a["recovery_expires_at"]) and bool(set(a["permissions"]) & {"cancel", "close"})
        return {"authorization_required": True, "entry": {"enabled": bool(entry), "default": "closed"}, "exit": {"enabled": bool(exit_enabled), "scope": "verified_authorized_exposure", "session_change": "operator_escalation"}}


def main():
    """Local operator commands; not a network endpoint. Never prints artifacts."""
    import argparse
    import sys
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("provision", help="Read one strict authorization JSON from stdin")
    kill = sub.add_parser("kill-entry")
    kill.add_argument("authorization_id")
    kill.add_argument("--reason", default="operator")
    sub.add_parser("abort-status", help="Print pending local abort notifications, no account identity")
    args = parser.parse_args()
    store = AuthorizationStore(args.database)
    if args.command == "provision":
        store.provision(json.load(sys.stdin))
        print(json.dumps({"provisioned": True}))
    elif args.command == "kill-entry":
        store.disable_entry(args.authorization_id, args.reason)
        print(json.dumps({"entry_disabled": True}))
    else:
        print(json.dumps({"events": store.abort_events()}))


if __name__ == "__main__":
    main()
