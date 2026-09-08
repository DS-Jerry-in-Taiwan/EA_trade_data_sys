import importlib.util
from pathlib import Path


module_path = Path(__file__).parents[2] / 'core' / 'symbol_resolver.py'
spec = importlib.util.spec_from_file_location('symbol_resolver_under_test', module_path)
symbol_resolver_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(symbol_resolver_module)
SymbolResolver = symbol_resolver_module.SymbolResolver


class _Symbol:
    def __init__(self, name):
        self.name = name


class _MT5:
    def __init__(self, symbols):
        self._symbols = [_Symbol(symbol) for symbol in symbols]

    def symbols_get(self):
        return self._symbols


def _initialize(symbols, configured=('BTC',)):
    resolver = SymbolResolver()
    resolver.initialize(_MT5(symbols), list(configured))
    return resolver


def test_crypto_alias_maps_btc_to_broker_usd_symbol():
    resolver = _initialize({'BTCUSDm', 'BTCAUDm', 'ETHBTCm'})

    assert resolver.resolve('BTC') == 'BTCUSDm'
    assert resolver.mapping == {'BTC': 'BTCUSDm'}
    assert resolver.unresolved == []


def test_crypto_alias_prefers_usd_over_usdt_independent_of_input_order():
    for symbols in (
        ['BTCUSDTm', 'BTCUSDm'],
        ['BTCUSDm', 'BTCUSDTm'],
    ):
        resolver = _initialize(symbols)

        assert resolver.resolve('BTC') == 'BTCUSDm'


def test_crypto_alias_prefers_configured_broker_suffix():
    resolver = _initialize({'BTCUSD', 'BTCUSDm'})

    assert resolver.resolve('BTC') == 'BTCUSDm'


def test_crypto_alias_accepts_case_insensitive_broker_name():
    resolver = _initialize({'btcusdm'})

    assert resolver.resolve('BTC') == 'btcusdm'


def test_explicit_crypto_alias_wins_over_literal_symbol():
    resolver = _initialize({'BTC', 'BTCUSDm'})

    assert resolver.resolve('BTC') == 'BTCUSDm'


def test_crypto_alias_falls_back_to_usdt_only_after_usd_candidates():
    resolver = _initialize({'BTCUSDTm'})

    assert resolver.resolve('BTC') == 'BTCUSDTm'


def test_unresolved_symbol_keeps_logical_fallback():
    resolver = _initialize({'BTCAUDm'})

    assert resolver.resolve('BTC') == 'BTC'
    assert resolver.mapping == {}
    assert resolver.unresolved == ['BTC']
