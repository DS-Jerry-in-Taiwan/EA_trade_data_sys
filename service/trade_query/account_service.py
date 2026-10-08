from datetime import datetime, timezone, timedelta
from typing import List, Optional
from service.config import Settings, load_settings
from service.infrastructure.mt5.client import MT5Client
from service.infrastructure.mt5.session import AccountSessionTransition
from service.infrastructure.mt5.symbol_resolver import SymbolResolutionError
from service.trade_query.contracts import TradeQueryError
from service.trade_query.mt5_deal_mapper import map_mt5_deal
from service.domain.trades.models import DealRecord, DealSummary
from service.trade_query.presenters import present_deal_summary_v1, present_deal_v1


MT5_NOT_CONNECTED = {'error': 'MT5 not connected'}


def _session_not_ready(mt5_client):
    status_getter = getattr(mt5_client, "session_status", None)
    status = status_getter() if callable(status_getter) else {}
    transition_getter = getattr(mt5_client, "transition_detected", None)
    if status.get("state") == "switch_detected" or (
        callable(transition_getter) and transition_getter()
    ):
        return {
            "error": "account_session_transition",
            "code": "account_session_transition",
            "session_generation": status.get("generation", 0),
        }
    return MT5_NOT_CONNECTED


def _to_utc_iso(timestamp):
    if not timestamp:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def _round_number(value, digits=2):
    if value is None:
        return None
    try:
        return round(value, digits)
    except TypeError:
        return value


class AccountService:
    def __init__(self, config_path=None, mt5_client=None,
                 settings: Settings | None = None):
        self.settings = settings or load_settings(config_path)
        self.cfg = self.settings.as_dict()
        self.mt5_client = (
            mt5_client if mt5_client is not None
            else MT5Client(symbol_aliases=self.settings.symbol_aliases, settings=self.settings)
        )

    def get_balance(self):
        return self.get_account()

    def session_status(self, *, refresh=False):
        """Return non-secret MT5 account-session lifecycle facts."""
        getter = getattr(self.mt5_client, "session_status", None)
        if callable(getter):
            return getter(refresh=refresh)
        return {
            "state": "unknown",
            "ready": False,
            "generation": 0,
            "fingerprint": None,
            "error": "account_session_unavailable",
        }

    def get_account(self):
        if not self.mt5_client.ensure_connected():
            return _session_not_ready(self.mt5_client)
        info = self.mt5_client.call(lambda m: m.account_info())
        if info:
            margin = getattr(info, 'margin', 0)
            return {
                'login': getattr(info, 'login', None),
                'server': getattr(info, 'server', 'N/A'),
                'balance': getattr(info, 'balance', None),
                'equity': getattr(info, 'equity', None),
                'margin': margin,
                'free_margin': getattr(info, 'margin_free', None),
                'margin_level': _round_number(getattr(info, 'margin_level', 0), 2) if margin > 0 else 0,
                'currency': getattr(info, 'currency', None),
                'leverage': getattr(info, 'leverage', None),
                'time': datetime.now(timezone.utc).isoformat()
            }
        return {'error': 'Failed to get account info'}

    def get_positions(self, symbol: Optional[str] = None):
        if not self._connected():
            return _session_not_ready(self.mt5_client)

        def read(module):
            self._ensure_resolver()
            if symbol:
                broker_symbol = self.mt5_client.resolve(symbol)
                positions = module.positions_get(symbol=broker_symbol)
            else:
                positions = module.positions_get()
            result = []
            for p in positions or ():
                result.append({
                    'ticket': getattr(p, 'ticket', None),
                    'symbol': self._public_symbol(getattr(p, 'symbol', symbol)),
                    'type': 'BUY' if getattr(p, 'type', 0) == 0 else 'SELL',
                    'volume': getattr(p, 'volume', None),
                    'price_open': getattr(p, 'price_open', None),
                    'price_current': getattr(p, 'price_current', None),
                    'sl': getattr(p, 'sl', None),
                    'tp': getattr(p, 'tp', None),
                    'profit': _round_number(getattr(p, 'profit', None), 2),
                    'swap': _round_number(getattr(p, 'swap', None), 2),
                    'time': _to_utc_iso(getattr(p, 'time', None)),
                    'comment': getattr(p, 'comment', '')
                })
            return result

        return self._call(read)

    def _translate_query_failure(self, operation):
        """Keep transport/catalog details inside the trade-query service."""
        try:
            return operation()
        except SymbolResolutionError as exc:
            if exc.reason in {'resolver_not_initialized', 'symbol_catalog_unavailable'}:
                raise TradeQueryError('mt5_unavailable', 'MT5 symbol catalog is unavailable', 503) from exc
            if exc.reason in {'ambiguous_broker_symbol', 'ambiguous_logical_alias'}:
                raise TradeQueryError('ambiguous_symbol', 'Symbol mapping is ambiguous', 409) from exc
            raise TradeQueryError('symbol_not_found', 'Symbol is unavailable', 404) from exc
        except AccountSessionTransition as exc:
            raise TradeQueryError(
                'account_session_transition',
                'MT5 account session transition requires reconciliation', 503,
            ) from exc
        except ConnectionError as exc:
            raise TradeQueryError('mt5_unavailable', 'MT5 is unavailable', 503) from exc

    def _connected(self):
        return self._translate_query_failure(self.mt5_client.ensure_connected)

    def _call(self, operation):
        return self._translate_query_failure(lambda: self.mt5_client.call(operation))

    def _ensure_resolver(self, names=()):
        """Initialize only inside client.call's account-readiness boundary."""
        if not getattr(self.mt5_client, '_resolver', None):
            configured = tuple(dict.fromkeys((
                *self.settings.tick_service.symbols,
                *(item.name for item in self.settings.history_service.symbols),
                *names,
            )))
            self.mt5_client.init_resolver(configured)

    def _public_symbol(self, broker_symbol):
        normalize = getattr(self.mt5_client, 'logical_symbol', None)
        if not callable(normalize):
            return broker_symbol
        try:
            return normalize(broker_symbol)
        except SymbolResolutionError as exc:
            if exc.reason == 'unsupported_broker_symbol':
                # Unfiltered read-only account views also include instruments
                # outside the service's configured logical symbol universe.
                return broker_symbol
            raise

    def get_symbols(self, logical_symbols):
        """Return public symbol metadata while keeping MT5 access behind this service."""
        names = list(logical_symbols)
        if not self._connected():
            return _session_not_ready(self.mt5_client)
        self._call(lambda _module: self._ensure_resolver(names))
        result = []
        for name in names:
            try:
                info = self._call(
                    lambda module: module.symbol_info(self.mt5_client.resolve(name))
                )
            except TradeQueryError as exc:
                if exc.status not in {404, 409}:
                    raise
                result.append({'name': name, 'digits': None, 'spread': None,
                               **exc.as_response()})
                continue
            if info:
                result.append({
                    'name': name,
                    'digits': getattr(info, 'digits', None),
                    'spread': getattr(info, 'spread', None),
                    'description': getattr(info, 'description', ''),
                    'trade_mode': getattr(info, 'trade_mode', None),
                })
            else:
                result.append({'name': name, 'digits': None, 'spread': None})
        return {'symbols': result, 'count': len(result), 'source': 'mt5'}

    def get_orders(self):
        if not self.mt5_client.ensure_connected():
            return _session_not_ready(self.mt5_client)
        orders = self.mt5_client.call(lambda m: m.orders_get())
        if orders is None:
            return []
        order_types = ['buy', 'sell', 'buy_limit', 'sell_limit', 'buy_stop', 'sell_stop']
        result = []
        for o in orders:
            ot = o.type if o.type < len(order_types) else 0
            result.append({
                'ticket': o.ticket,
                'symbol': o.symbol,
                'type': order_types[ot],
                'volume': o.volume_current if hasattr(o, 'volume_current') else o.volume,
                'price': o.price_open,
                'sl': o.sl,
                'tp': o.tp,
                'time_setup': datetime.fromtimestamp(o.time_setup, tz=timezone.utc).isoformat()
            })
        return result

    def get_deals(
        self,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        limit: Optional[int] = 100,
        include_summary: bool = False,
    ):
        if not self.mt5_client.ensure_connected():
            return _session_not_ready(self.mt5_client)
        if to_dt is None:
            to_dt = datetime.now(timezone.utc)
        if from_dt is None:
            from_dt = to_dt - timedelta(days=30)
        deals = self.mt5_client.call(lambda m: m.history_deals_get(from_dt, to_dt))
        if not deals:
            if include_summary:
                return {
                    'data': [],
                    'summary': present_deal_summary_v1(self._summarize_deals([])),
                }
            return []
        mapped_deals = [map_mt5_deal(deal) for deal in deals]
        sorted_deals = sorted(
            mapped_deals, key=lambda deal: deal.occurred_at, reverse=True
        )
        if limit is not None:
            sorted_deals = sorted_deals[:limit]
        result = [present_deal_v1(deal) for deal in sorted_deals]
        if include_summary:
            return {
                'data': result,
                'summary': present_deal_summary_v1(
                    self._summarize_deals(sorted_deals)
                ),
            }
        return result

    def get_history_orders(self, from_dt: datetime, to_dt: datetime):
        if not self.mt5_client.ensure_connected():
            return _session_not_ready(self.mt5_client)
        orders = self.mt5_client.call(lambda m: m.history_orders_get(from_dt, to_dt))
        if not orders:
            return []
        order_types = ['BUY', 'SELL', 'BUY_LIMIT', 'SELL_LIMIT', 'BUY_STOP', 'SELL_STOP', 'BUY_STOP_LIMIT', 'SELL_STOP_LIMIT', 'CLOSE_BY']
        order_states = ['STARTED', 'PLACED', 'CANCELED', 'PARTIAL', 'FILLED', 'REJECTED', 'EXPIRED', 'REQUEST_ADD', 'REQUEST_MODIFY', 'REQUEST_CANCEL']
        result = []
        for o in sorted(orders, key=lambda x: getattr(x, 'time_setup', 0), reverse=True):
            order_type = getattr(o, 'type', None)
            state = getattr(o, 'state', None)
            result.append({
                'ticket': getattr(o, 'ticket', None),
                'symbol': getattr(o, 'symbol', ''),
                'type': order_types[order_type] if isinstance(order_type, int) and order_type < len(order_types) else order_type,
                'state': order_states[state] if isinstance(state, int) and state < len(order_states) else state,
                'volume_initial': getattr(o, 'volume_initial', None),
                'volume_current': getattr(o, 'volume_current', None),
                'price_open': getattr(o, 'price_open', None),
                'price_current': getattr(o, 'price_current', None),
                'sl': getattr(o, 'sl', None),
                'tp': getattr(o, 'tp', None),
                'time_setup': _to_utc_iso(getattr(o, 'time_setup', None)),
                'time_done': _to_utc_iso(getattr(o, 'time_done', None)),
                'comment': getattr(o, 'comment', '')
            })
        return result

    def _summarize_deals(self, deals: List[DealRecord]) -> DealSummary:
        return DealSummary(
            profit=sum(deal.profit for deal in deals),
            commission=sum(deal.commission for deal in deals),
            swap=sum(deal.swap for deal in deals),
            count=len(deals),
        )


def main():
    svc = AccountService()
    print('=== Balance ===')
    print(svc.get_balance())
    print()
    print('=== Positions ===')
    print(svc.get_positions())
    print()
    print('=== Orders ===')
    print(svc.get_orders())
    print()
    print('=== Deals (3) ===')
    print(svc.get_deals(limit=3))


if __name__ == '__main__':
    main()
