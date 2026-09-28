"""Atomic component status publication and side-effect-free status reads."""

import json
import os
import tempfile
from datetime import datetime, timezone


DEFAULT_STATUS_DIR = "/run/trade-data"


def utc_now():
    return datetime.now(timezone.utc)


def atomic_write_status(path, component, state, **details):
    """Publish a complete JSON document without exposing partial writes."""
    payload = {
        "component": component,
        "state": state,
        "updated_at": utc_now().isoformat(),
    }
    payload.update(details)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=f".{os.path.basename(path)}.", delete=False,
        ) as output:
            temporary = output.name
            json.dump(payload, output, separators=(",", ":"), sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)
    return payload


def read_status(path, max_age_seconds, now=None):
    """Read status defensively and annotate freshness; never raises."""
    try:
        with open(path, encoding="utf-8") as source:
            payload = json.load(source)
        if not isinstance(payload, dict):
            raise ValueError("status is not an object")
        updated_at = datetime.fromisoformat(payload["updated_at"].replace("Z", "+00:00"))
        if updated_at.tzinfo is None:
            raise ValueError("updated_at is not timezone-aware")
        current = now or utc_now()
        age = (current - updated_at).total_seconds()
        result = dict(payload)
        result["age_seconds"] = max(age, 0)
        result["fresh"] = 0 <= age <= max_age_seconds
        return result
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return {"state": "unknown", "fresh": False, "error": str(exc)}
