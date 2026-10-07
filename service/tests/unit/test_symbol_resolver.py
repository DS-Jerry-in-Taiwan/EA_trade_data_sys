import pytest

from service.infrastructure.mt5.symbol_resolver import SymbolResolutionError, SymbolResolver


class _Symbol:
    def __init__(self, name):
        self.name = name


class _MT5:
    def __init__(self, symbols):
        self._symbols = [_Symbol(symbol) for symbol in symbols]

    def symbols_get(self):
        return self._symbols


def _initialize(symbols, configured=('BTC',), aliases=None):
    resolver = SymbolResolver(aliases)
    resolver.initialize(_MT5(symbols), list(configured))
    return resolver


def test_crypto_alias_maps_btc_to_broker_usd_symbol():
    resolver = _initialize({'BTCUSDm', 'BTCAUDm', 'ETHBTCm'})

    assert resolver.resolve('BTC') == 'BTCUSDm'
    assert resolver.mapping == {'BTC': 'BTCUSDm'}
    assert resolver.unresolved == []


def test_crypto_alias_maps_usd_and_never_guesses_usdt():
    for symbols in (
        ['BTCUSDTm', 'BTCUSDm'],
        ['BTCUSDm', 'BTCUSDTm'],
    ):
        resolver = _initialize(symbols)

        assert resolver.resolve('BTC') == 'BTCUSDm'


def test_multiple_crypto_suffix_variants_are_ambiguous():
    resolver = _initialize({'BTCUSD', 'BTCUSDm'})

    with pytest.raises(SymbolResolutionError) as caught:
        resolver.resolve('BTC')
    assert caught.value.reason == 'ambiguous_broker_symbol'
    assert caught.value.candidates == ('BTCUSD', 'BTCUSDm')


def test_crypto_alias_accepts_case_insensitive_broker_name():
    resolver = _initialize({'btcusdm'})

    assert resolver.resolve('BTC') == 'btcusdm'


def test_exact_catalog_symbol_is_preserved_before_inferred_alias():
    resolver = _initialize({'BTC', 'BTCUSDm'})

    assert resolver.resolve('BTC') == 'BTC'


def test_usdt_market_requires_explicit_alias():
    resolver = _initialize({'BTCUSDTm'})

    with pytest.raises(SymbolResolutionError, match='unsupported_symbol'):
        resolver.resolve('BTC')

    configured = _initialize({'BTCUSDTm'}, aliases={'BTC': 'BTCUSDTm'})
    assert configured.resolve('BTC') == 'BTCUSDTm'
    assert configured.logical_for('BTCUSDTm') == 'BTC'


def test_unresolved_symbol_never_returns_unchecked_logical_input():
    resolver = _initialize({'BTCAUDm'})

    with pytest.raises(SymbolResolutionError, match='unsupported_symbol'):
        resolver.resolve('BTC')
    assert resolver.mapping == {}
    assert resolver.unresolved == ['BTC']


@pytest.mark.parametrize('broker', ['XAUUSD', 'XAUUSD.sim', 'XAU_USD', 'xau_usd.sim'])
def test_known_catalog_name_variants_preserve_public_symbol(broker):
    resolver = _initialize([broker], configured=['XAUUSDm'])

    assert resolver.resolve('XAUUSDm') == broker
    assert resolver.logical_for(broker) == 'XAUUSDm'


@pytest.mark.parametrize(('logical', 'broker'), [
    ('EURUSD.sim', 'EURUSDm'),
    ('BTCUSD.sim', 'BTCUSDm'),
    ('BTCUSD.sim', 'BTC_USD'),
    ('eurusd.SIM', 'eurUSDM'),
    ('btcusd.SiM', 'bTc_UsD'),
])
def test_sim_suffix_infers_known_catalog_variants(logical, broker):
    resolver = _initialize([broker], configured=[logical])

    assert resolver.resolve(logical) == broker
    assert resolver.logical_for(broker) == logical
    assert resolver.unresolved == []


def test_arbitrary_prefixes_suffixes_and_quote_products_are_not_inferred():
    resolver = _initialize(['XAUUSDc', 'XAUUSDcash', 'BTCUSDT', 'micro.BTCUSD'],
                           configured=['XAUUSDm', 'BTC'])

    assert resolver.mapping == {}
    assert resolver.unresolved == ['XAUUSDm', 'BTC']


def test_ambiguous_variants_fail_independently_of_catalog_order():
    for symbols in (['EURUSD', 'EUR_USD'], ['EUR_USD', 'EURUSD']):
        resolver = _initialize(symbols, configured=['EURUSDm'])
        with pytest.raises(SymbolResolutionError) as caught:
            resolver.resolve('EURUSDm')
        assert caught.value.reason == 'ambiguous_broker_symbol'
        assert caught.value.candidates == ('EURUSD', 'EUR_USD')


def test_explicit_alias_disambiguates_catalog_and_overrides_exact_name():
    resolver = _initialize(['EURUSDm', 'EURUSD', 'EUR_USD'], configured=['EURUSDm'],
                           aliases={'EURUSDm': 'EUR_USD'})

    assert resolver.resolve('EURUSDm') == 'EUR_USD'
    assert resolver.logical_for('EUR_USD') == 'EURUSDm'
    with pytest.raises(SymbolResolutionError, match='unsupported_broker_symbol'):
        resolver.logical_for('EURUSD')


def test_configured_alias_must_match_exact_catalog_spelling():
    resolver = _initialize(['EUR_USD'], configured=['EURUSDm'],
                           aliases={'EURUSDm': 'eur_usd'})

    with pytest.raises(SymbolResolutionError, match='configured_alias_unavailable'):
        resolver.resolve('EURUSDm')


def test_aliases_to_one_broker_symbol_are_not_reversed_arbitrarily():
    resolver = _initialize(['EUR_USD'], configured=['EURUSDm', 'EURUSD'],
                           aliases={'EURUSDm': 'EUR_USD', 'EURUSD': 'EUR_USD'})

    assert resolver.mapping == {}
    assert resolver.unresolved == ['EURUSDm', 'EURUSD']
    with pytest.raises(SymbolResolutionError, match='ambiguous_logical_alias'):
        resolver.resolve('EURUSDm')


def test_reinitialize_replaces_mappings_failures_and_reverse_names():
    resolver = _initialize(['XAUUSD.sim'], configured=['XAUUSDm', 'BTC'])
    assert resolver.unresolved == ['BTC']
    resolver.initialize(_MT5(['BTCUSD.sim']), ['BTC'])

    assert resolver.mapping == {'BTC': 'BTCUSD.sim'}
    assert resolver.unresolved == []
    with pytest.raises(SymbolResolutionError, match='unsupported_symbol'):
        resolver.resolve('XAUUSDm')
    with pytest.raises(SymbolResolutionError, match='unsupported_broker_symbol'):
        resolver.logical_for('XAUUSD.sim')


def test_refresh_rebuilds_against_new_account_catalog():
    resolver = _initialize(['BTCUSDm'])
    resolver.refresh(_MT5(['BTC_USD']), ['BTC'])

    assert resolver.resolve('BTC') == 'BTC_USD'
    assert resolver.logical_for('BTC_USD') == 'BTC'
    with pytest.raises(SymbolResolutionError, match='unsupported_broker_symbol'):
        resolver.logical_for('BTCUSDm')


def test_unavailable_explicit_alias_is_not_replaced_after_account_change():
    resolver = _initialize(['XAU_USD'], configured=['XAUUSDm'],
                           aliases={'XAUUSDm': 'XAU_USD'})
    resolver.refresh(_MT5(['XAUUSDm']), ['XAUUSDm'])

    assert resolver.mapping == {}
    with pytest.raises(SymbolResolutionError, match='configured_alias_unavailable'):
        resolver.resolve('XAUUSDm')


def test_failed_catalog_read_invalidates_previously_resolved_names():
    resolver = _initialize(['BTCUSDm'])
    mt5 = _MT5([])
    mt5._symbols = None

    with pytest.raises(SymbolResolutionError, match='symbol_catalog_unavailable'):
        resolver.refresh(mt5, ['BTC'])
    assert resolver.mapping == {}
    with pytest.raises(SymbolResolutionError, match='resolver_not_initialized'):
        resolver.resolve('BTC')


def test_case_fold_collisions_are_rejected():
    resolver = _initialize(['btcusd', 'BtcUsd'])

    with pytest.raises(SymbolResolutionError, match='ambiguous_broker_symbol'):
        resolver.resolve('BTC')


def test_case_folded_logical_name_uses_same_known_suffix_rules():
    resolver = _initialize(['xau_usd.sim'], configured=['XAUUSDM'])

    assert resolver.resolve('XAUUSDM') == 'xau_usd.sim'
    assert resolver.logical_for('xau_usd.sim') == 'XAUUSDM'


def test_uninitialized_or_unconfigured_names_fail_closed():
    with pytest.raises(SymbolResolutionError, match='resolver_not_initialized'):
        SymbolResolver().resolve('BTC')
    resolver = _initialize(['BTCUSDm', 'EURUSDm'])
    with pytest.raises(SymbolResolutionError, match='unsupported_symbol'):
        resolver.resolve('EURUSDm')
