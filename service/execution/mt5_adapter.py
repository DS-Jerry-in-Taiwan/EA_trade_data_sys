"""MT5 normalization and the single-attempt Demo execution adapter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from service.trade_query.mt5_deal_mapper import map_mt5_deal
from service.trade_query.presenters import present_deal_v1

from .errors import AmbiguousMT5Result, ExecutionError
from .idempotency import correlation_token


RETCODE_ERRORS = {
    10014: ("invalid_volume", "MT5 rejected the requested volume"),
    10016: ("invalid_stops", "MT5 rejected stop-loss or take-profit"),
    10018: ("market_closed", "Market is closed"),
    10019: ("insufficient_margin", "Insufficient margin"),
    10027: ("trading_disabled", "Trading is disabled by the terminal"),
}
SUCCESS_RETCODES = {10008, 10009, 10010}


def _iso(timestamp):
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class MT5ExecutionAdapter:
    def __init__(self, mt5_client, resolver_symbols=()):
        self.client = mt5_client
        self.resolver_symbols = tuple(resolver_symbols)

    def _call(self, callback):
        try:
            return self.client.call(callback)
        except ConnectionError as exc:
            raise ExecutionError("mt5_unavailable", "MT5 is unavailable", status=503) from exc

    def _resolve(self, symbol):
        if not isinstance(symbol, str) or not symbol:
            raise ExecutionError("invalid_symbol", "symbol must be a non-empty string")
        if not getattr(self.client, "_resolver", None):
            self.client.init_resolver(self.resolver_symbols or (symbol,))
        return self.client.resolve(symbol)

    def account(self):
        info = self._call(lambda mt5: mt5.account_info())
        if info is None:
            raise ExecutionError("mt5_unavailable", "MT5 account information is unavailable", status=503)
        trade_mode = getattr(info, "trade_mode", None)
        demo_value = self._call(lambda mt5: getattr(mt5, "ACCOUNT_TRADE_MODE_DEMO", 0))
        return {
            "balance": getattr(info, "balance", None),
            "equity": getattr(info, "equity", None),
            "margin": getattr(info, "margin", None),
            "free_margin": getattr(info, "margin_free", None),
            "margin_level": getattr(info, "margin_level", None),
            "currency": getattr(info, "currency", None),
            "leverage": getattr(info, "leverage", None),
            "trade_mode": "DEMO" if trade_mode == demo_value else "NON_DEMO_OR_UNKNOWN",
            "mutation_eligible": trade_mode == demo_value,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    def require_demo(self):
        account = self.account()
        if not account["mutation_eligible"]:
            raise ExecutionError(
                "demo_account_required",
                "Mutation is allowed only when MT5 explicitly reports Demo account mode",
                status=403,
            )

    def symbol(self, logical_symbol):
        symbol = self._resolve(logical_symbol)
        info = self._call(lambda mt5: mt5.symbol_info(symbol))
        if info is None:
            raise ExecutionError("symbol_not_found", "Symbol is unavailable", status=404)
        ask = _number(getattr(info, "ask", None))
        margin = self._call(
            lambda mt5: mt5.order_calc_margin(
                getattr(mt5, "ORDER_TYPE_BUY", 0), symbol, 1.0, ask
            )
        )
        return {
            "symbol": logical_symbol,
            "digits": int(getattr(info, "digits", 0)),
            "point": _number(getattr(info, "point", None)),
            "minimum_volume": _number(getattr(info, "volume_min", None)),
            "maximum_volume": _number(getattr(info, "volume_max", None)),
            "volume_step": _number(getattr(info, "volume_step", None)),
            "tick_size": _number(getattr(info, "trade_tick_size", None)),
            "tick_value": _number(getattr(info, "trade_tick_value", None)),
            "contract_size": _number(getattr(info, "trade_contract_size", None)),
            "stops_level": int(getattr(info, "trade_stops_level", 0)),
            "freeze_level": int(getattr(info, "trade_freeze_level", 0)),
            "margin_per_lot": _number(margin, None),
            "bid": _number(getattr(info, "bid", None)),
            "ask": ask,
            "observed_at": datetime.now(timezone.utc).isoformat(),
        }

    @staticmethod
    def _order(order):
        return {
            "id": str(getattr(order, "ticket", "")),
            "order_id": str(getattr(order, "ticket", "")),
            "symbol": str(getattr(order, "symbol", "") or ""),
            "type": getattr(order, "type", None),
            "state": getattr(order, "state", None),
            "volume": getattr(order, "volume_initial", getattr(order, "volume", None)),
            "remaining_volume": getattr(order, "volume_current", None),
            "price": getattr(order, "price_open", None),
            "sl": getattr(order, "sl", None),
            "tp": getattr(order, "tp", None),
            "created_at": _iso(getattr(order, "time_setup", None)),
            "completed_at": _iso(getattr(order, "time_done", None)),
            "comment": str(getattr(order, "comment", "") or ""),
        }

    def orders(self):
        now = datetime.now(timezone.utc)
        active = self._call(lambda mt5: mt5.orders_get()) or ()
        history = self._call(lambda mt5: mt5.history_orders_get(now - timedelta(days=90), now)) or ()
        merged = {str(getattr(item, "ticket", "")): self._order(item) for item in history}
        merged.update({str(getattr(item, "ticket", "")): self._order(item) for item in active})
        return sorted(merged.values(), key=lambda item: item.get("created_at") or "", reverse=True)

    def order(self, order_id):
        order_id = str(order_id)
        return next((item for item in self.orders() if item["id"] == order_id), None)

    def order_by_client(self, client_order_id):
        token = correlation_token(client_order_id)
        matches = [item for item in self.orders() if item.get("comment") == token]
        if len(matches) > 1:
            raise ExecutionError(
                "client_order_ambiguous",
                "More than one MT5 order has the client correlation token",
                status=409,
            )
        return matches[0] if matches else None

    def positions(self):
        positions = self._call(lambda mt5: mt5.positions_get()) or ()
        return [{
            "id": str(getattr(item, "ticket", "")),
            "position_id": str(getattr(item, "ticket", "")),
            "symbol": str(getattr(item, "symbol", "") or ""),
            "side": "BUY" if getattr(item, "type", 0) == 0 else "SELL",
            "volume": getattr(item, "volume", None),
            "open_price": getattr(item, "price_open", None),
            "current_price": getattr(item, "price_current", None),
            "sl": getattr(item, "sl", None),
            "tp": getattr(item, "tp", None),
            "profit": getattr(item, "profit", None),
            "opened_at": _iso(getattr(item, "time", None)),
        } for item in positions]

    def deals(self):
        now = datetime.now(timezone.utc)
        deals = self._call(lambda mt5: mt5.history_deals_get(now - timedelta(days=90), now)) or ()
        return [present_deal_v1(map_mt5_deal(item)) for item in deals]

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

    def send_once(self, request):
        invoked = False
        try:
            invoked = True
            result = self._call(lambda mt5: mt5.order_send(request))
        except Exception as exc:
            if invoked:
                raise AmbiguousMT5Result() from exc
            raise
        if result is None:
            raise AmbiguousMT5Result()
        retcode = int(getattr(result, "retcode", -1))
        payload = {
            "order_id": str(getattr(result, "order", "") or ""),
            "deal_id": str(getattr(result, "deal", "") or ""),
            "retcode": retcode,
            "comment": str(getattr(result, "comment", "") or ""),
        }
        if retcode not in SUCCESS_RETCODES:
            code, message = RETCODE_ERRORS.get(retcode, ("mt5_order_rejected", "MT5 rejected the order"))
            raise ExecutionError(code, message, status=422, retcode=retcode)
        return payload

    def cancel(self, order_id):
        constants = self._call(lambda mt5: getattr(mt5, "TRADE_ACTION_REMOVE", 8))
        return self.send_once({"action": constants, "order": int(order_id)})

    def close(self, position_id):
        positions = self._call(lambda mt5: mt5.positions_get(ticket=int(position_id))) or ()
        if not positions:
            raise ExecutionError("position_not_found", "Position was not found", status=404)
        position = positions[0]
        symbol = getattr(position, "symbol", "")
        info = self._call(lambda mt5: mt5.symbol_info(symbol))
        if info is None:
            raise ExecutionError("symbol_not_found", "Symbol quote is unavailable", status=404)
        constants = self._call(lambda mt5: {
            "action": getattr(mt5, "TRADE_ACTION_DEAL", 1),
            "buy": getattr(mt5, "ORDER_TYPE_BUY", 0),
            "sell": getattr(mt5, "ORDER_TYPE_SELL", 1),
        })
        closing_buy = getattr(position, "type", 0) != constants["buy"]
        request = {
            "action": constants["action"], "position": int(position_id), "symbol": symbol,
            "volume": getattr(position, "volume", 0),
            "type": constants["buy"] if closing_buy else constants["sell"],
            "price": getattr(info, "ask" if closing_buy else "bid", 0), "deviation": 20,
        }
        return self.send_once(request)
