"""MT5 normalization and the single-attempt Demo execution adapter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextvars import ContextVar
from decimal import Decimal
from functools import wraps
from hashlib import sha256

from service.domain.trades.errors import DealMappingError
from service.trade_query.mt5_deal_mapper import map_mt5_deal
from service.infrastructure.mt5.session import AccountSessionGuard, AccountSessionTransition
from service.infrastructure.mt5.symbol_resolver import SymbolResolutionError

from .contracts import contract_error, decimal_wire, now_wire, restore_order_intent, timestamp_wire, validate_order
from .errors import AmbiguousMT5Result, ExecutionError, parse_ticket_id
from .idempotency import correlation_token


RETCODE_ERRORS = {
    10014: ("invalid_volume", "MT5 rejected the requested volume"),
    10016: ("invalid_stops", "MT5 rejected stop-loss or take-profit"),
    10018: ("market_closed", "Market is closed"),
    10019: ("insufficient_margin", "Insufficient margin"),
    10027: ("trading_disabled", "Trading is disabled by the terminal"),
}
SUCCESS_RETCODES = {10008, 10009, 10010}
AMBIGUOUS_RETCODES = {10012, 10031}


def consistent_read(callback):
    """Reject responses assembled from more than one account generation."""
    @wraps(callback)
    def read(self, *args, **kwargs):
        if self._read_session.get() is not None:
            return callback(self, *args, **kwargs)
        token = self._read_session.set({"expected": None})
        try:
            return callback(self, *args, **kwargs)
        finally:
            self._read_session.reset(token)
    return read


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _source_ticket(value, resource):
    try:
        return str(parse_ticket_id(value, resource=resource))
    except ExecutionError as exc:
        raise contract_error() from exc


class MT5ExecutionAdapter:
    def __init__(self, mt5_client, resolver_symbols=()):
        self.client = mt5_client
        self.resolver_symbols = tuple(resolver_symbols)
        self.idempotency_store = None
        self._read_session = ContextVar("execution_read_session", default=None)

    def bind_idempotency_store(self, store):
        self.idempotency_store = store

    def session_status(self, *, refresh=False):
        getter = getattr(self.client, "session_status", None)
        if callable(getter):
            return getter(refresh=refresh)
        return {
            "state": "unknown",
            "ready": False,
            "generation": 0,
            "fingerprint": None,
            "error": "account_session_unavailable",
        }

    def _call(self, callback):
        def guarded(mt5):
            frame = self._read_session.get()
            if frame is not None:
                status = self._current_guard_status()
                if status is not None:
                    expected = frame["expected"]
                    current = (status.get("generation"), (status.get("fingerprint") or {}).get("id"))
                    if not status.get("ready") or (expected is not None and current != expected):
                        raise ExecutionError(
                            "account_session_transition", "MT5 account changed while assembling execution data", status=503
                        )
                    frame["expected"] = current
            return callback(mt5)

        try:
            return self.client.call(guarded)
        except AccountSessionTransition as exc:
            raise ExecutionError(
                "account_session_transition",
                "MT5 account session transition requires reconciliation",
                status=503,
            ) from exc
        except ConnectionError as exc:
            raise ExecutionError("mt5_unavailable", "MT5 is unavailable", status=503) from exc
        except SymbolResolutionError as exc:
            if exc.reason in {"symbol_catalog_unavailable", "resolver_not_initialized"}:
                raise ExecutionError("mt5_unavailable", "MT5 symbol catalog is unavailable", status=503) from exc
            raise ExecutionError("symbol_not_found", "Symbol is unavailable or ambiguous", status=404) from exc

    def _current_guard_status(self):
        guard = getattr(self.client, "session_guard", None)
        status = getattr(guard, "status", None)
        if callable(status):
            return status()
        if callable(getattr(self.client, "session_status", None)):
            return self.session_status(refresh=False)
        return None

    def _resolve(self, symbol):
        if not isinstance(symbol, str) or not symbol:
            raise ExecutionError("invalid_symbol", "symbol must be a non-empty string")

        def resolve(_mt5):
            if not getattr(self.client, "_resolver", None):
                self.client.init_resolver(self.resolver_symbols or (symbol,))
            return self.client.resolve(symbol)

        return self._call(resolve)

    def _logical_symbol(self, broker_symbol):
        reverse = getattr(self.client, "logical_symbol", None)
        if not callable(reverse):
            return broker_symbol

        def logical(_mt5):
            if not getattr(self.client, "_resolver", None):
                self.client.init_resolver(self.resolver_symbols or (broker_symbol,))
            try:
                return reverse(broker_symbol)
            except SymbolResolutionError as exc:
                if exc.reason == "unsupported_broker_symbol":
                    # Historical instruments may be outside the configured set.
                    return broker_symbol
                raise

        return self._call(logical)

    @consistent_read
    def account(self):
        info = self._call(lambda mt5: mt5.account_info())
        if info is None:
            raise ExecutionError("mt5_unavailable", "MT5 account information is unavailable", status=503)
        login, server = getattr(info, "login", None), getattr(info, "server", None)
        currency = getattr(info, "currency", None)
        if not login or not isinstance(server, str) or not server or not isinstance(currency, str) or not currency:
            raise contract_error()
        return {
            "account_id": "mt5-" + sha256(f"{login}:{server}".encode()).hexdigest(),
            "balance": decimal_wire(getattr(info, "balance", None), allow_zero=True),
            "equity": decimal_wire(getattr(info, "equity", None), allow_zero=True),
            "margin": decimal_wire(getattr(info, "margin", None), allow_zero=True),
            "free_margin": decimal_wire(getattr(info, "margin_free", None), allow_zero=True),
            "currency": currency,
            "observed_at": now_wire(),
        }

    def require_demo(self):
        info = self._call(lambda mt5: mt5.account_info())
        if info is None:
            raise ExecutionError("mt5_unavailable", "MT5 account information is unavailable", status=503)
        demo_value = self._call(lambda mt5: getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", None))
        if demo_value is None or getattr(info, "trade_mode", None) is None:
            raise ExecutionError("account_mode_unknown", "MT5 account mode could not be verified", status=503)
        if getattr(info, "trade_mode", None) != demo_value:
            raise ExecutionError(
                "demo_account_required",
                "Mutation is allowed only when MT5 explicitly reports Demo account mode",
                status=403,
            )

    @staticmethod
    def _session_error(status):
        """Map session readiness to stable mutation-gate errors."""
        state = status.get("state")
        if state == "switch_detected":
            return ExecutionError(
                "account_session_transition",
                "MT5 account session transition requires reconciliation",
                status=503,
            )
        if state == "unknown" or status.get("error") == "account_mode_unknown":
            return ExecutionError(
                "account_mode_unknown",
                "MT5 account mode could not be verified",
                status=503,
            )
        if not status.get("ready"):
            return ExecutionError(
                "mt5_unavailable",
                "MT5 account session is not ready",
                status=503,
            )
        return None

    @staticmethod
    def _account_mode_error(status):
        fingerprint = status.get("fingerprint") or {}
        mode = fingerprint.get("account_mode")
        if mode == "REAL":
            return ExecutionError(
                "real_account_forbidden",
                "Execution mutation is forbidden for Real accounts",
                status=403,
            )
        if mode != "DEMO":
            return ExecutionError(
                "account_mode_unknown",
                "MT5 account mode could not be verified as Demo",
                status=503,
            )
        return None

    def mutation_session(self):
        """Capture a verified Demo session generation before mutation work."""
        status = self.session_status(refresh=True)
        transition_getter = getattr(self.client, "transition_detected", None)
        if callable(transition_getter) and transition_getter():
            raise ExecutionError(
                "account_session_transition",
                "MT5 account session transition requires reconciliation",
                status=503,
            )
        error = self._session_error(status)
        if error is not None:
            raise error
        error = self._account_mode_error(status)
        if error is not None:
            raise error
        status = self._call(self._fresh_mutation_status)
        error = self._account_mode_error(status)
        if error is not None:
            raise error
        return {
            "generation": status["generation"],
            "fingerprint": (status.get("fingerprint") or {}).get("id"),
        }

    def validate_mutation_session(self, expected_session):
        """Revalidate the captured generation immediately before MT5 send."""
        return self._call(
            lambda mt5: self._validate_session_status(expected_session, self._fresh_mutation_status(mt5))
        )

    def _validate_session_status(self, expected_session, status):
        error = self._session_error(status)
        if error is not None:
            raise error
        if (
            status.get("generation") != expected_session.get("generation")
            or (status.get("fingerprint") or {}).get("id") != expected_session.get("fingerprint")
        ):
            raise ExecutionError(
                "session_changed_before_send",
                "MT5 account session changed before mutation was sent",
                status=409,
            )
        error = self._account_mode_error(status)
        if error is not None:
            raise error
        return status

    def _fresh_mutation_status(self, mt5):
        """Observe terminal identity inside the existing guarded call lock."""
        current = self._current_guard_status() or self.session_status(refresh=False)
        demo = getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", None)
        real = getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", None)
        if demo is None or real is None:
            raise ExecutionError("account_mode_unknown", "MT5 account mode could not be verified", status=503)
        info = mt5.account_info()
        if info is None:
            raise ExecutionError("mt5_unavailable", "MT5 account information is unavailable", status=503)
        if type(getattr(info, "trade_mode", None)) is not int:
            raise ExecutionError("account_mode_unknown", "MT5 account mode could not be verified", status=503)
        fresh = AccountSessionGuard(demo_trade_mode=demo, real_trade_mode=real).observe_account_info(info)
        fresh["generation"] = current.get("generation")
        error = self._session_error(current)
        if error is not None:
            raise error
        return fresh

    def validate_recovery_session(self, record):
        """Recover only on the account that originally reserved this client ID."""
        if not isinstance(record.session, dict) or not record.session.get("fingerprint"):
            raise ExecutionError(
                "account_session_unverifiable", "Original execution account identity is unavailable", status=503
            )

        def validate(mt5):
            status = self._fresh_mutation_status(mt5)
            if (status.get("fingerprint") or {}).get("id") != record.session["fingerprint"]:
                raise ExecutionError(
                    "account_session_transition", "Original execution belongs to another MT5 account", status=503
                )
            # A process restart or reconnect may change the generation while
            # retaining the same account. Durable recovery compares identity.
            expected = dict(record.session, generation=status.get("generation"))
            return self._validate_session_status(expected, status)

        return self._call(validate)

    @consistent_read
    def symbol(self, logical_symbol):
        symbol = self._resolve(logical_symbol)
        info = self._call(lambda mt5: mt5.symbol_info(symbol))
        if info is None:
            raise ExecutionError("symbol_not_found", "Symbol is unavailable", status=404)
        digits = getattr(info, "digits", None)
        if type(digits) is not int or digits < 0:
            raise contract_error()
        ask = _number(getattr(info, "ask", None))
        margin = self._call(
            lambda mt5: mt5.order_calc_margin(
                getattr(mt5, "ORDER_TYPE_BUY", 0), symbol, 1.0, ask
            )
        )
        try:
            margin_per_lot = decimal_wire(margin, optional=True)
        except ExecutionError:
            margin_per_lot = None
        minimum_volume = decimal_wire(getattr(info, "volume_min", None))
        maximum_volume = decimal_wire(getattr(info, "volume_max", None))
        if Decimal(minimum_volume) > Decimal(maximum_volume):
            raise contract_error()
        return {
            "symbol": logical_symbol,
            "digits": digits,
            "point": decimal_wire(getattr(info, "point", None)),
            "minimum_volume": minimum_volume,
            "maximum_volume": maximum_volume,
            "volume_step": decimal_wire(getattr(info, "volume_step", None)),
            "tick_size": decimal_wire(getattr(info, "trade_tick_size", None), optional=True),
            "tick_value": decimal_wire(getattr(info, "trade_tick_value", None), optional=True),
            "contract_size": decimal_wire(getattr(info, "trade_contract_size", None), optional=True),
            "stops_level": decimal_wire(getattr(info, "trade_stops_level", None), allow_zero=True, optional=True),
            "freeze_level": decimal_wire(getattr(info, "trade_freeze_level", None), allow_zero=True, optional=True),
            "margin_per_lot": margin_per_lot,
            "observed_at": now_wire(),
        }

    def _order(self, order):
        ticket = _source_ticket(getattr(order, "ticket", None), "order")
        order_type = getattr(order, "type", None)
        if type(order_type) is not int or type(getattr(order, "state", None)) is not int:
            raise contract_error()
        kind = {0: "market", 1: "market", 2: "limit", 3: "limit", 4: "stop", 5: "stop"}.get(order_type)
        status = {
            0: "pending", 1: "accepted", 2: "cancelled", 3: "partially_filled", 4: "filled",
            5: "rejected", 6: "cancelled", 7: "pending", 8: "accepted", 9: "accepted",
        }.get(getattr(order, "state", None))
        tif = {0: "gtc", 1: "day"}.get(getattr(order, "type_time", 0))
        if kind is None or status is None or tif is None:
            raise contract_error()
        volume = decimal_wire(getattr(order, "volume_initial", None))
        remaining = decimal_wire(getattr(order, "volume_current", None), allow_zero=True)
        filled = Decimal(volume) - Decimal(remaining)
        if filled < 0 or (status == "filled" and filled != Decimal(volume)):
            raise contract_error()
        if status == "partially_filled" and not 0 < filled < Decimal(volume):
            raise contract_error()
        if status not in {"filled", "partially_filled", "cancelled"} and filled != 0:
            raise contract_error()
        average_fill_price = None
        if filled > 0:
            fills = self._call(lambda mt5: mt5.history_deals_get(ticket=int(ticket))) or ()
            total_volume, total_value = Decimal(0), Decimal(0)
            for fill in fills:
                if getattr(fill, "order", None) != int(ticket):
                    continue
                if type(getattr(fill, "type", None)) is not int or fill.type not in {0, 1}:
                    continue
                fill_volume = Decimal(decimal_wire(getattr(fill, "volume", None)))
                fill_price = Decimal(decimal_wire(getattr(fill, "price", None)))
                total_volume += fill_volume
                total_value += fill_volume * fill_price
            if total_volume != filled:
                raise contract_error()
            average_fill_price = decimal_wire(total_value / total_volume)
        created = timestamp_wire(getattr(order, "time_setup", None))
        updated = timestamp_wire(getattr(order, "time_done", None) or getattr(order, "time_setup", None))
        if updated < created:
            raise contract_error()
        result = {
            "broker_order_id": ticket, "request_id": f"mt5-order-{ticket}",
            "intent": {
                "client_order_id": f"mt5-order-{ticket}",
                "symbol": self._logical_symbol(getattr(order, "symbol", "")),
                "direction": 1 if order_type in {0, 2, 4} else -1,
                "order_type": kind, "volume": volume,
                "limit_price": decimal_wire(getattr(order, "price_open", None)) if kind != "market" else None,
                "stop_loss": decimal_wire(getattr(order, "sl", None), optional=True),
                "take_profit": decimal_wire(getattr(order, "tp", None), optional=True),
                "time_in_force": tif,
            },
            "status": status, "filled_volume": str(filled),
            "average_fill_price": average_fill_price,
            "created_at": created, "updated_at": updated,
        }
        if self.idempotency_store is not None:
            record = self.idempotency_store.get_by_correlation_token(getattr(order, "comment", ""))
            if record is not None:
                self.validate_recovery_session(record)
                result = restore_order_intent(result, record)
        return validate_order(result)

    def _raw_orders(self):
        now = datetime.now(timezone.utc)
        active = self._call(lambda mt5: mt5.orders_get())
        history = self._call(lambda mt5: mt5.history_orders_get(now - timedelta(days=90), now))
        if active is None or history is None:
            raise ExecutionError("mt5_unavailable", "MT5 orders are unavailable", status=503)
        merged = {str(getattr(item, "ticket", "")): item for item in history}
        merged.update({str(getattr(item, "ticket", "")): item for item in active})
        return list(merged.values())

    @consistent_read
    def orders(self):
        merged = {str(getattr(item, "ticket", "")): self._order(item) for item in self._raw_orders()}
        return sorted(merged.values(), key=lambda item: item.get("created_at") or "", reverse=True)

    @consistent_read
    def order(self, order_id):
        order_id = str(parse_ticket_id(order_id, resource="order"))
        return next((self._order(item) for item in self._raw_orders() if str(getattr(item, "ticket", "")) == order_id), None)

    @consistent_read
    def order_by_client(self, client_order_id):
        expected = None
        if self.idempotency_store is not None:
            record = self.idempotency_store.get(client_order_id)
            if record is not None:
                status = self.validate_recovery_session(record)
                expected = {
                    "generation": status["generation"], "fingerprint": status["fingerprint"]["id"],
                }
        token = correlation_token(client_order_id)
        matches = [item for item in self._raw_orders() if getattr(item, "comment", None) == token]
        if len(matches) > 1:
            raise ExecutionError(
                "client_order_ambiguous",
                "More than one MT5 order has the client correlation token",
                status=409,
            )
        order = self._order(matches[0]) if matches else None
        if expected is not None:
            self._call(lambda mt5: self._validate_session_status(expected, self._fresh_mutation_status(mt5)))
        return order

    @consistent_read
    def positions(self):
        positions = self._call(lambda mt5: mt5.positions_get())
        if positions is None:
            raise ExecutionError("mt5_unavailable", "MT5 positions are unavailable", status=503)
        if any(type(getattr(item, "type", None)) is not int or item.type not in {0, 1} for item in positions):
            raise contract_error()
        return [{
            "position_id": _source_ticket(getattr(item, "ticket", None), "position"),
            "symbol": self._logical_symbol(getattr(item, "symbol", "")),
            "direction": 1 if getattr(item, "type", None) == 0 else -1,
            "status": "open", "volume": decimal_wire(getattr(item, "volume", None)),
            "entry_price": decimal_wire(getattr(item, "price_open", None)),
            "current_price": decimal_wire(getattr(item, "price_current", None)),
            "stop_loss": decimal_wire(getattr(item, "sl", None), optional=True),
            "opened_at": timestamp_wire(getattr(item, "time", None)), "closed_at": None,
        } for item in positions]

    def _deal(self, raw):
        deal = map_mt5_deal(raw)
        if deal.deal_type not in {"BUY", "SELL"}:
            return None
        if deal.entry_type not in {"IN", "OUT", "OUT_BY"}:
            raise DealMappingError("MT5 reversal cannot be represented as one execution Deal")
        if deal.order_id <= 0 or deal.position_id <= 0:
            raise DealMappingError("Trading deals require positive order and position IDs")
        try:
            volume, price = decimal_wire(deal.volume), decimal_wire(deal.price)
        except ExecutionError as exc:
            raise DealMappingError("Trading deals require positive volume and price") from exc
        return {
            "deal_id": str(deal.deal_id), "broker_order_id": str(deal.order_id),
            "position_id": str(deal.position_id), "symbol": self._logical_symbol(deal.symbol),
            "direction": 1 if deal.deal_type == "BUY" else -1,
            "deal_type": "open" if deal.entry_type == "IN" else "close",
            "volume": volume, "price": price,
            "executed_at": deal.occurred_at.isoformat().replace("+00:00", "Z"),
        }

    @consistent_read
    def deals(self):
        now = datetime.now(timezone.utc)
        deals = self._call(lambda mt5: mt5.history_deals_get(now - timedelta(days=90), now))
        if deals is None:
            raise ExecutionError("mt5_unavailable", "MT5 deals are unavailable", status=503)
        try:
            return [deal for item in deals if (deal := self._deal(item)) is not None]
        except DealMappingError as exc:
            raise ExecutionError(
                "mt5_deal_mapping_error",
                "MT5 deal data could not be normalized",
                status=502,
            ) from exc

    def _constants_and_request(self, payload):
        symbol = self._resolve(payload.get("symbol"))
        side = str(payload.get("side", "")).upper()
        if side not in {"BUY", "SELL"}:
            raise ExecutionError("invalid_side", "side must be BUY or SELL")
        try:
            volume = float(payload["volume"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ExecutionError("invalid_volume", "volume must be numeric") from exc
        if volume <= 0:
            raise ExecutionError("invalid_volume", "volume must be positive")
        info = self._call(lambda mt5: mt5.symbol_info(symbol))
        if info is None:
            raise ExecutionError("symbol_not_found", "Symbol quote is unavailable", status=404)
        constants = self._call(lambda mt5: {
            "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
            "buy": getattr(mt5, "ORDER_TYPE_BUY", 0),
            "sell": getattr(mt5, "ORDER_TYPE_SELL", 1),
            "gtc": getattr(mt5, "ORDER_TIME_GTC", 0),
            "ioc": getattr(mt5, "ORDER_FILLING_IOC", 1),
        })
        order_type = constants["buy"] if side == "BUY" else constants["sell"]
        request = {
            "action": constants["action"], "symbol": symbol, "volume": volume,
            "type": order_type,
            "price": _number(payload.get("price"), getattr(info, "ask" if side == "BUY" else "bid", 0)),
            "sl": _number(payload.get("sl", payload.get("stop_loss"))),
            "tp": _number(payload.get("tp", payload.get("take_profit"))),
            "deviation": int(payload.get("deviation", 20)),
            "type_time": constants["gtc"], "type_filling": constants["ioc"],
            "comment": correlation_token(str(payload.get("client_order_id", ""))),
        }
        return request

    def preflight(self, payload):
        request = self._constants_and_request(payload)
        result = self._call(lambda mt5: mt5.order_check(request))
        if result is None:
            raise ExecutionError("order_check_failed", "MT5 order check returned no result", status=422)
        retcode = int(getattr(result, "retcode", -1))
        if retcode not in {0, 10009}:
            code, message = RETCODE_ERRORS.get(retcode, ("order_check_rejected", "MT5 order check rejected the request"))
            raise ExecutionError(code, message, status=422, retcode=retcode)
        return request, {"retcode": retcode, "margin": getattr(result, "margin", None)}

    def assert_entry_scope(self, symbol):
        """Require an empty symbol book before bounded test exposure."""
        native = self._resolve(symbol)
        def verify(mt5):
            positions = mt5.positions_get(symbol=native)
            orders = mt5.orders_get(symbol=native)
            if positions is None or orders is None:
                raise ExecutionError("mt5_unavailable", "Exposure baseline is unavailable", status=503)
            if positions or orders:
                raise ExecutionError("exposure_scope_conflict", "Symbol already has exposure", status=409)
        self._call(verify)
        return native

    @staticmethod
    def _scope_error():
        return ExecutionError("exposure_scope_conflict", "Exposure ownership could not be proven", status=409)

    def _validate_exposure(self, mt5, scope, *, operation):
        """Verify broker lineage, not just a caller-supplied position ticket."""
        try:
            order_id = int(parse_ticket_id(scope["order_id"], resource="order"))
            native, volume = scope["symbol"], Decimal(decimal_wire(scope["volume"]))
            token = correlation_token(scope["client_order_id"])
            orders = mt5.history_orders_get(ticket=order_id)
            active = mt5.orders_get(ticket=order_id)
            if orders is None or active is None:
                raise self._scope_error()
            matching = [o for o in (*orders, *active) if getattr(o, "ticket", None) == order_id]
            if not matching or any(
                getattr(o, "symbol", None) != native or getattr(o, "comment", None) != token
                or Decimal(decimal_wire(getattr(o, "volume_initial", None))) != volume
                for o in matching
            ):
                raise self._scope_error()
            if operation == "cancel":
                if len(active) != 1 or Decimal(decimal_wire(getattr(active[0], "volume_current", None))) != volume:
                    raise self._scope_error()
                return
            position_id = int(parse_ticket_id(scope["position_id"], resource="position"))
            positions = mt5.positions_get(ticket=position_id)
            deals = mt5.history_deals_get(position=position_id)
            if positions is None or deals is None or len(positions) != 1 or not deals:
                raise self._scope_error()
            position = positions[0]
            if (getattr(position, "ticket", None) != position_id
                or getattr(position, "symbol", None) != native
                or Decimal(decimal_wire(getattr(position, "volume", None))) != volume):
                raise self._scope_error()
            total = Decimal(0)
            for deal in deals:
                if (getattr(deal, "order", None) != order_id or getattr(deal, "position_id", None) != position_id
                    or getattr(deal, "symbol", None) != native or getattr(deal, "entry", None) != 0
                    or getattr(deal, "type", None) != getattr(position, "type", None)
                    or getattr(deal, "comment", None) != token):
                    raise self._scope_error()
                total += Decimal(decimal_wire(getattr(deal, "volume", None)))
            if total != volume:
                raise self._scope_error()
        except (KeyError, TypeError, ValueError):
            raise self._scope_error() from None

    def exposure_scope(self, client_order_id, order_id, logical_symbol, volume):
        native = self._resolve(logical_symbol)
        scope = {"client_order_id": client_order_id, "order_id": str(order_id),
                 "symbol": native, "volume": decimal_wire(volume)}
        def discover(mt5):
            deals = mt5.history_deals_get(ticket=int(parse_ticket_id(order_id, resource="order")))
            if deals is None:
                raise self._scope_error()
            ids = {getattr(d, "position_id", None) for d in deals}
            if len(ids) != 1 or not next(iter(ids), None):
                raise self._scope_error()
            scope["position_id"] = str(next(iter(ids)))
            self._validate_exposure(mt5, scope, operation="close")
        self._call(discover)
        return scope

    def cancel_scope(self, client_order_id, order_id, logical_symbol, volume):
        scope = {"client_order_id": client_order_id, "order_id": str(order_id),
                 "symbol": self._resolve(logical_symbol), "volume": decimal_wire(volume)}
        self._call(lambda mt5: self._validate_exposure(mt5, scope, operation="cancel"))
        return scope

    def _exit_check(self, mt5, scope, operation, authorization_check):
        if scope is not None:
            self._validate_exposure(mt5, scope, operation=operation)
        if authorization_check is not None:
            authorization_check(mt5)

    def send_once(self, request, *, expected_session=None, before_send=None, expected_empty_symbol=None):
        invoked = False

        def send(mt5):
            nonlocal invoked
            if expected_session is not None:
                # The client may have reconnected or reconciled between the
                # preflight check and this locked callback. Inspect its current
                # generation and Demo mode while it still owns the call lock.
                self._validate_session_status(expected_session, self._fresh_mutation_status(mt5))
            if before_send is not None:
                before_send(mt5)
            if expected_empty_symbol is not None:
                positions = mt5.positions_get(symbol=expected_empty_symbol)
                orders = mt5.orders_get(symbol=expected_empty_symbol)
                if positions is None or orders is None or positions or orders:
                    raise self._scope_error()
            invoked = True
            return mt5.order_send(request)

        try:
            result = self._call(send)
        except ExecutionError as exc:
            if invoked and exc.code in {"account_session_transition", "mt5_unavailable"}:
                raise AmbiguousMT5Result() from exc
            if expected_session is not None and exc.code == "account_session_transition":
                raise ExecutionError(
                    "session_changed_before_send",
                    "MT5 account session changed before mutation was sent",
                    status=409,
                ) from exc
            raise
        except Exception as exc:
            if invoked:
                raise AmbiguousMT5Result() from exc
            raise
        if result is None:
            raise AmbiguousMT5Result()
        try:
            retcode = int(getattr(result, "retcode"))
            payload = {
                "order_id": str(getattr(result, "order", "") or ""),
                "deal_id": str(getattr(result, "deal", "") or ""),
                "retcode": retcode,
                "comment": str(getattr(result, "comment", "") or ""),
            }
        except (TypeError, ValueError, AttributeError) as exc:
            raise AmbiguousMT5Result() from exc
        if retcode in AMBIGUOUS_RETCODES:
            raise AmbiguousMT5Result(retcode=retcode)
        if retcode not in SUCCESS_RETCODES:
            code, message = RETCODE_ERRORS.get(retcode, ("mt5_order_rejected", "MT5 rejected the order"))
            raise ExecutionError(code, message, status=422, retcode=retcode)
        return payload

    def cancel(self, order_id, *, expected_session=None, expected_exposure=None, authorization_check=None):
        order_id = parse_ticket_id(order_id, resource="order")
        if expected_exposure is not None and str(order_id) != str(expected_exposure.get("order_id")):
            raise self._scope_error()
        constants = self._call(lambda mt5: getattr(mt5, "TRADE_ACTION_REMOVE", 8))
        if expected_session is not None:
            self.validate_mutation_session(expected_session)
        self.send_once(
            {"action": constants, "order": order_id},
            expected_session=expected_session,
            before_send=lambda mt5: self._exit_check(mt5, expected_exposure, "cancel", authorization_check),
        )
        order = self.order(order_id)
        if order is None:
            raise AmbiguousMT5Result()
        return order

    def close(self, position_id, *, expected_session=None, expected_exposure=None, authorization_check=None):
        position_id = parse_ticket_id(position_id, resource="position")
        positions = self._call(lambda mt5: mt5.positions_get(ticket=position_id)) or ()
        if not positions:
            raise ExecutionError("position_not_found", "Position was not found", status=404)
        position = positions[0]
        position_type = getattr(position, "type", None)
        if type(position_type) is not int or position_type not in {0, 1}:
            raise contract_error()
        if _source_ticket(getattr(position, "ticket", None), "position") != str(position_id):
            raise contract_error()
        volume = float(decimal_wire(getattr(position, "volume", None)))
        symbol = getattr(position, "symbol", "")
        info = self._call(lambda mt5: mt5.symbol_info(symbol))
        if info is None:
            raise ExecutionError("symbol_not_found", "Symbol quote is unavailable", status=404)
        constants = self._call(lambda mt5: {
            "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
            "buy": getattr(mt5, "ORDER_TYPE_BUY", 0),
            "sell": getattr(mt5, "ORDER_TYPE_SELL", 1),
        })
        closing_buy = position_type != constants["buy"]
        request = {
            "action": constants["action"], "position": position_id, "symbol": symbol,
            "volume": volume,
            "type": constants["buy"] if closing_buy else constants["sell"],
            "price": getattr(info, "ask" if closing_buy else "bid", 0), "deviation": 20,
        }
        if expected_session is not None:
            self.validate_mutation_session(expected_session)
        if expected_exposure is not None and str(position_id) != str(expected_exposure.get("position_id")):
            raise self._scope_error()
        result = self.send_once(
            request, expected_session=expected_session,
            before_send=lambda mt5: self._exit_check(mt5, expected_exposure, "close", authorization_check),
        )
        ticket = result.get("deal_id")
        order_ticket = result.get("order_id")
        if not ticket or ticket == "0" or not order_ticket or order_ticket == "0":
            raise AmbiguousMT5Result()
        raw_deals = self._call(lambda mt5: mt5.history_deals_get(ticket=int(order_ticket))) or ()
        raw_deal = next((item for item in raw_deals if str(getattr(item, "ticket", "")) == ticket), None)
        if raw_deal is None:
            raise AmbiguousMT5Result()
        try:
            deal = self._deal(raw_deal)
        except DealMappingError as exc:
            raise ExecutionError("mt5_deal_mapping_error", "MT5 deal data could not be normalized", status=502) from exc
        if deal is None:
            raise AmbiguousMT5Result()
        return deal
