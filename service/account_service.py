import sys
import yaml
from datetime import datetime, timezone, timedelta
sys.path.insert(0, '/app')
from service.core.mt5_client import MT5Client


class AccountService:
    def __init__(self, config_path='/app/service/config/settings.yaml'):
        with open(config_path) as f:
            self.cfg = yaml.safe_load(f)
        self.mt5_client = MT5Client()

    def get_balance(self):
        if not self.mt5_client.ensure_connected():
            return {'error': 'MT5 not connected'}
        info = self.mt5_client.call(lambda m: m.account_info())
        if info:
            return {
                'balance': info.balance,
                'equity': info.equity,
                'margin': info.margin,
                'free_margin': info.margin_free,
                'margin_level': round(info.margin_level, 2) if info.margin > 0 else 0,
                'currency': info.currency,
                'server': info.server if hasattr(info, 'server') else 'N/A',
                'login': info.login,
                'time': datetime.now(timezone.utc).isoformat()
            }
        return {'error': 'Failed to get account info'}

    def get_positions(self):
        if not self.mt5_client.ensure_connected():
            return {'error': 'MT5 not connected'}
        positions = self.mt5_client.call(lambda m: m.positions_get())
        if positions is None:
            return []
        result = []
        for p in positions:
            result.append({
                'ticket': p.ticket,
                'symbol': p.symbol,
                'type': 'buy' if p.type == 0 else 'sell',
                'volume': p.volume,
                'price_open': p.price_open,
                'price_current': p.price_current,
                'sl': p.sl,
                'tp': p.tp,
                'profit': round(p.profit, 2),
                'swap': round(p.swap, 2),
                'time': datetime.fromtimestamp(p.time, tz=timezone.utc).isoformat()
            })
        return result

    def get_orders(self):
        if not self.mt5_client.ensure_connected():
            return {'error': 'MT5 not connected'}
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

    def get_deals(self, limit=100):
        if not self.mt5_client.ensure_connected():
            return {'error': 'MT5 not connected'}
        now = datetime.now(timezone.utc)
        deals = self.mt5_client.call(lambda m: m.history_deals_get(now - timedelta(days=30), now))
        if deals is None:
            return []
        deal_types = ['buy', 'sell', 'buy_limit', 'sell_limit', 'buy_stop', 'sell_stop', 'balance', 'credit', 'unknown']
        result = []
        for d in sorted(deals, key=lambda x: x.time, reverse=True)[:limit]:
            dt = d.type if d.type < len(deal_types) else -1
            result.append({
                'ticket': d.ticket,
                'symbol': d.symbol,
                'type': deal_types[dt],
                'volume': d.volume,
                'price': d.price,
                'profit': round(d.profit, 2),
                'commission': round(d.commission, 2),
                'swap': round(d.swap, 2),
                'time': datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat()
            })
        return result


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
