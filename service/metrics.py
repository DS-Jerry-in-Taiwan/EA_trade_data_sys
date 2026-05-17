from prometheus_client import Gauge, Histogram, Counter, generate_latest

mt5_tick_bid = Gauge('mt5_tick_bid', 'Current bid price', ['symbol'])
mt5_tick_ask = Gauge('mt5_tick_ask', 'Current ask price', ['symbol'])
mt5_last_tick_timestamp = Gauge('mt5_last_tick_timestamp', 'Last tick unix timestamp', ['symbol'])
mt5_connected = Gauge('mt5_connected', 'MT5 connection status (1=connected)')

mt5_account_balance = Gauge('mt5_account_balance', 'Account balance')
mt5_account_equity = Gauge('mt5_account_equity', 'Account equity')
service_uptime_seconds = Gauge('service_uptime_seconds', 'Seconds since service started')

api_requests_total = Counter('api_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
api_request_duration_seconds = Histogram('api_request_duration_seconds', 'Request duration', ['method', 'endpoint'])
