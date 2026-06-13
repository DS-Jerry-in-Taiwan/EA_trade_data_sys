import sys
import yaml
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional
sys.path.insert(0, '/app')
from service.core.mt5_client import MT5Client


MT5_NOT_CONNECTED = {'error': 'MT5 not connected'}


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
    def __init__(self, config_path='/app/service/config/settings.yaml'):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        self.mt5_client = MT5Client()

    def get_balance(self):
        return self.get_account()

    def get_account(self):
        if not self.mt5_client.ensure_connected():
            return MT5_NOT_CONNECTED
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
        if not self.mt5_client.ensure_connected():
            return MT5_NOT_CONNECTED
        if symbol:
            positions = self.mt5_client.call(lambda m: m.positions_get(symbol=symbol))
        else:
            positions = self.mt5_client.call(lambda m: m.positions_get())
        if not positions:
            return []
        result = []
        for p in positions:
            result.append({
                'ticket': getattr(p, 'ticket', None),
                'symbol': getattr(p, 'symbol', symbol),
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

    def get_orders(self):
        if not self.mt5_client.ensure_connected():
            return MT5_NOT_CONNECTED
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
            return MT5_NOT_CONNECTED
        if to_dt is None:
            to_dt = datetime.now(timezone.utc)
        if from_dt is None:
            from_dt = to_dt - timedelta(days=30)
        deals = self.mt5_client.call(lambda m: m.history_deals_get(from_dt, to_dt))
        if not deals:
            if include_summary:
                return {'data': [], 'summary': self._summarize_deals([])}
            return []
        deal_types = ['BUY', 'SELL', 'BALANCE', 'CREDIT', 'CHARGE', 'CORRECTION', 'BONUS', 'COMMISSION', 'COMMISSION_DAILY', 'COMMISSION_MONTHLY', 'COMMISSION_AGENT_DAILY', 'COMMISSION_AGENT_MONTHLY', 'INTEREST', 'BUY_CANCELED', 'SELL_CANCELED', 'DIVIDEND', 'DIVIDEND_FRANKED', 'TAX']
        entry_types = ['IN', 'OUT', 'INOUT', 'OUT_BY']
        sorted_deals = sorted(deals, key=lambda x: getattr(x, 'time', 0), reverse=True)
        if limit is not None:
            sorted_deals = sorted_deals[:limit]
        result = []
        for d in sorted_deals:
            deal_type = getattr(d, 'type', None)
            entry = getattr(d, 'entry', None)
            result.append({
                'deal': getattr(d, 'deal', getattr(d, 'ticket', None)),
                'order': getattr(d, 'order', None),
                'position_id': getattr(d, 'position_id', None),
                'symbol': getattr(d, 'symbol', ''),
                'type': deal_types[deal_type] if isinstance(deal_type, int) and deal_type < len(deal_types) else deal_type,
                'entry': entry_types[entry] if isinstance(entry, int) and entry < len(entry_types) else entry,
                'volume': getattr(d, 'volume', None),
                'price': getattr(d, 'price', None),
                'profit': _round_number(getattr(d, 'profit', 0), 2),
                'commission': _round_number(getattr(d, 'commission', 0), 2),
                'swap': _round_number(getattr(d, 'swap', 0), 2),
                'time': _to_utc_iso(getattr(d, 'time', None)),
                'comment': getattr(d, 'comment', '')
            })
        if include_summary:
            return {'data': result, 'summary': self._summarize_deals(result)}
        return result

    def get_history_orders(self, from_dt: datetime, to_dt: datetime):
        if not self.mt5_client.ensure_connected():
            return MT5_NOT_CONNECTED
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

    def _summarize_deals(self, deals: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            'profit': _round_number(sum(d.get('profit') or 0 for d in deals), 2),
            'commission': _round_number(sum(d.get('commission') or 0 for d in deals), 2),
            'swap': _round_number(sum(d.get('swap') or 0 for d in deals), 2),
            'count': len(deals),
        }


if __name__ == '__main__':
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
