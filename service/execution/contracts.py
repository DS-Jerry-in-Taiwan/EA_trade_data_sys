"""Execution-only wire helpers matching the bot's schema-version 1 DTOs.

The data API's legacy deal presenter deliberately remains separate. Decimal
strings, integer directions, and exact DTO fields are part of this boundary.
"""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from .errors import ExecutionError


def contract_error():
    return ExecutionError(
        "mt5_contract_invalid", "MT5 data does not satisfy the execution contract", status=502
    )


def decimal_wire(value, *, allow_zero=False, optional=False):
    if optional and value is None:
        return None
    try:
        if isinstance(value, bool):
            raise ValueError
        number = Decimal(str(value))
        if not number.is_finite() or number < 0:
            raise ValueError
        if not -324 <= number.as_tuple().exponent <= 308:
            raise ValueError
        if number == 0 and not allow_zero:
            if optional:
                return None
            raise ValueError
        return format(number, "f")
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise contract_error() from exc


def timestamp_wire(timestamp):
    try:
        if timestamp is None or isinstance(timestamp, bool):
            raise ValueError
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    except (TypeError, OverflowError, OSError, ValueError) as exc:
        raise contract_error() from exc


def now_wire():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_order_request(payload):
    """Validate the bot's nested OrderRequest without accepting float decimals."""
    invalid = ExecutionError("invalid_request", "A valid OrderRequest JSON object is required")
    if not isinstance(payload, dict) or set(payload) != {"request_id", "intent", "submitted_at"}:
        raise invalid
    intent = payload["intent"]
    required = {"client_order_id", "symbol", "direction", "order_type", "volume"}
    optional = {"limit_price", "stop_loss", "take_profit", "time_in_force"}
    if not isinstance(intent, dict) or not required <= set(intent) or set(intent) - required - optional:
        raise invalid
    for value, limit in (
        (payload["request_id"], 128), (intent["client_order_id"], 128), (intent["symbol"], 64),
    ):
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise invalid
    if type(intent["direction"]) is not int or intent["direction"] not in {1, -1}:
        raise invalid
    if not isinstance(intent["order_type"], str) or intent["order_type"] not in {"market", "limit", "stop"}:
        raise invalid
    time_in_force = intent.get("time_in_force", "gtc")
    if not isinstance(time_in_force, str) or time_in_force not in {"gtc", "day", "ioc", "fok"}:
        raise invalid
    normalized = dict(intent, time_in_force=time_in_force)
    for field in ("volume", "limit_price", "stop_loss", "take_profit"):
        value = intent.get(field)
        if value is None and field != "volume":
            normalized[field] = None
            continue
        if isinstance(value, bool) or not isinstance(value, (str, int)):
            raise invalid
        try:
            normalized[field] = decimal_wire(value)
        except ExecutionError as exc:
            raise invalid from exc
    if (intent["order_type"] == "market") != (normalized["limit_price"] is None):
        raise invalid
    try:
        if not isinstance(payload["submitted_at"], str):
            raise ValueError
        submitted = datetime.fromisoformat(payload["submitted_at"].replace("Z", "+00:00"))
        if submitted.tzinfo is None or submitted.utcoffset() is None:
            raise ValueError
    except (TypeError, ValueError) as exc:
        raise invalid from exc
    return {
        "request_id": payload["request_id"], "intent": normalized,
        "submitted_at": submitted.astimezone(timezone.utc).isoformat(),
    }


def order_result(request_id, result):
    retcode = result.get("retcode")
    status = {10008: "accepted", 10009: "filled", 10010: "partially_filled"}.get(retcode)
    broker_order_id = result.get("order_id")
    if status is None or not broker_order_id or str(broker_order_id) == "0":
        raise contract_error()
    return {
        "request_id": request_id, "status": status, "occurred_at": now_wire(),
        "broker_order_id": str(broker_order_id), "reason": None,
        "broker_retcode": str(retcode),
    }


def validate_order(order):
    """Validate order relationships after durable intent has been restored."""
    try:
        intent = order["intent"]
        if type(intent["direction"]) is not int or intent["direction"] not in {1, -1}:
            raise ValueError
        if not isinstance(intent["symbol"], str) or not intent["symbol"].strip():
            raise ValueError
        volume = Decimal(decimal_wire(intent["volume"]))
        filled = Decimal(decimal_wire(order["filled_volume"], allow_zero=True))
        status = order["status"]
        if status not in {"pending", "accepted", "partially_filled", "filled", "cancelled", "rejected"}:
            raise ValueError
        if filled > volume or (filled > 0) != (order["average_fill_price"] is not None):
            raise ValueError
        if order["average_fill_price"] is not None:
            decimal_wire(order["average_fill_price"])
        if status == "filled" and filled != volume:
            raise ValueError
        if status == "partially_filled" and not 0 < filled < volume:
            raise ValueError
        if status not in {"filled", "partially_filled", "cancelled"} and filled != 0:
            raise ValueError
        created = datetime.fromisoformat(order["created_at"].replace("Z", "+00:00"))
        updated = datetime.fromisoformat(order["updated_at"].replace("Z", "+00:00"))
        if created.tzinfo is None or updated.tzinfo is None or updated < created:
            raise ValueError
    except (KeyError, TypeError, ValueError, InvalidOperation) as exc:
        raise contract_error() from exc
    return order


def restore_order_intent(order, record):
    """Restore only metadata whose order facts agree with the durable request."""
    result = dict(order, intent=dict(order["intent"]))
    payload = record.payload
    if isinstance(payload, dict) and isinstance(payload.get("intent"), dict):
        original = payload["intent"]
        for field in ("symbol", "direction", "order_type", "time_in_force"):
            if result["intent"].get(field) != original.get(field):
                raise contract_error()
        if Decimal(decimal_wire(result["intent"]["volume"])) != Decimal(decimal_wire(original.get("volume"))):
            raise contract_error()
        result["intent"] = dict(original)
    result["request_id"] = record.request_id
    result["intent"]["client_order_id"] = record.client_order_id
    return validate_order(result)


def recovered_order_result(record, order):
    """Represent observed rejection separately from proven MT5 acceptance."""
    validate_order(order)
    rejected = order["status"] == "rejected"
    if order["status"] == "pending":
        raise ExecutionError(
            "execution_outcome_unknown", "MT5 acceptance is not yet known; automatic resend is forbidden", status=503
        )
    return {
        "request_id": record.request_id, "status": order["status"],
        "occurred_at": order["updated_at"],
        "broker_order_id": None if rejected else order["broker_order_id"],
        "reason": "MT5 rejected the order" if rejected else None,
        "broker_retcode": None,
    }
