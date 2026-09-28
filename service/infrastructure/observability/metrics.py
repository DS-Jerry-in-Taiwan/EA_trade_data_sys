"""Prometheus instruments shared by the trade-data application processes."""

from prometheus_client import Gauge, Histogram, Counter, generate_latest

mt5_tick_bid = Gauge('mt5_tick_bid', 'Current bid price', ['symbol'])
mt5_tick_ask = Gauge('mt5_tick_ask', 'Current ask price', ['symbol'])
mt5_last_tick_timestamp = Gauge('mt5_last_tick_timestamp', 'Last tick unix timestamp', ['symbol'])
mt5_connected = Gauge('mt5_connected', 'MT5 connection status (1=connected)')

mt5_account_balance = Gauge('mt5_account_balance', 'Account balance')
mt5_account_equity = Gauge('mt5_account_equity', 'Account equity')
service_uptime_seconds = Gauge('service_uptime_seconds', 'Seconds since service started')
component_state = Gauge(
    'trade_data_component_state',
    'Component state (1 when the labelled state is current)',
    ['component', 'state'],
)
gateway_ready = Gauge('trade_data_gateway_ready', 'Gateway critical-path readiness')
tick_ipc_connected = Gauge('trade_data_tick_ipc_connected', 'Gateway Tick IPC connection')
fresh_tick_symbols = Gauge('trade_data_fresh_tick_symbols', 'Number of fresh configured Tick symbols')
component_status_age_seconds = Gauge(
    'trade_data_component_status_age_seconds', 'Age of component status publication', ['component']
)

api_requests_total = Counter('api_requests_total', 'Total HTTP requests', ['method', 'endpoint', 'status'])
api_request_duration_seconds = Histogram('api_request_duration_seconds', 'Request duration', ['method', 'endpoint'])
