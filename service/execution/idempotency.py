"""Transactional, restart-safe idempotency records."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .errors import ExecutionError


def payload_fingerprint(payload):
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def correlation_token(client_order_id):
    """Fit a stable opaque client reference inside MT5's comment limit."""
    digest = hashlib.sha256(client_order_id.encode("utf-8")).hexdigest()[:24]
    return f"ac5:{digest}"


@dataclass(frozen=True)
class IdempotencyRecord:
    client_order_id: str
    fingerprint: str
    state: str
    result: dict | None
    request_id: str


class IdempotencyStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS execution_idempotency (
                    client_order_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    result_json TEXT,
                    request_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )"""
            )

    @staticmethod
    def _record(row):
        if row is None:
            return None
        return IdempotencyRecord(
            client_order_id=row["client_order_id"],
            fingerprint=row["fingerprint"],
            state=row["state"],
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            request_id=row["request_id"],
        )

    def reserve(self, client_order_id, fingerprint, request_id):
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM execution_idempotency WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
            if row is not None:
                connection.commit()
                record = self._record(row)
                if record.fingerprint != fingerprint:
                    raise ExecutionError(
                        "idempotency_conflict",
                        "Idempotency-Key was already used with a different payload",
                        status=409,
                    )
                return record, False
            connection.execute(
                "INSERT INTO execution_idempotency VALUES (?, ?, 'pending', NULL, ?, ?, ?)",
                (client_order_id, fingerprint, request_id, now, now),
            )
            connection.commit()
        return IdempotencyRecord(client_order_id, fingerprint, "pending", None, request_id), True

    def finish(self, client_order_id, state, result):
        if state not in {"succeeded", "failed", "indeterminate"}:
            raise ValueError("invalid terminal idempotency state")
        encoded = json.dumps(result, sort_keys=True, separators=(",", ":"))
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                "UPDATE execution_idempotency SET state = ?, result_json = ?, updated_at = ? WHERE client_order_id = ?",
                (state, encoded, now, client_order_id),
            )
        return self.get(client_order_id)

    def get(self, client_order_id):
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM execution_idempotency WHERE client_order_id = ?",
                (client_order_id,),
            ).fetchone()
        return self._record(row)
