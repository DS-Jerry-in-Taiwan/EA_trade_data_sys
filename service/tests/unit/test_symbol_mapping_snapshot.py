from types import SimpleNamespace

import pytest

from service.infrastructure.mt5.symbol_resolver import (
    SymbolResolutionError,
    SymbolResolver,
)


class Catalog:
    def __init__(self, names):
        self.names = list(names)

    def symbols_get(self):
        return [SimpleNamespace(name=name) for name in self.names]


def test_snapshot_is_typed_immutable_and_bound_to_opaque_session_ticket():
    resolver = SymbolResolver()
    resolver.initialize(
        Catalog(["EURUSDm"]),
        ["EURUSDm"],
        session_fingerprint={"id": "opaque-token", "login_hash": "must-not-copy"},
        session_generation=7,
    )

    snapshot = resolver.snapshot
    assert snapshot.mapping_revision == 1
    assert snapshot.session_fingerprint == "opaque-token"
    assert snapshot.session_generation == 7
    assert snapshot.ready
    assert snapshot.bound_to("opaque-token", 7)
    assert not snapshot.bound_to("other-token", 7)
    assert "must-not-copy" not in repr(snapshot)
    with pytest.raises(TypeError):
        snapshot.mapping["EURUSDm"] = "other"


def test_catalog_and_mapping_digests_are_order_independent_but_change_on_refresh():
    resolver = SymbolResolver()
    resolver.initialize(Catalog(["BTCUSDm", "EURUSDm"]), ["BTC", "EURUSDm"])
    first = resolver.snapshot
    resolver.refresh(Catalog(["EURUSDm", "BTCUSDm"]), ["EURUSDm", "BTC"])
    second = resolver.snapshot

    assert first.catalog_digest == second.catalog_digest
    assert first.mapping_digest == second.mapping_digest
    assert second.mapping_revision == first.mapping_revision + 1

    resolver.refresh(Catalog(["EURUSD.sim", "BTCUSDm"]), ["EURUSDm", "BTC"])
    assert resolver.snapshot.catalog_digest != second.catalog_digest
    assert resolver.snapshot.mapping_digest != second.mapping_digest


def test_missing_and_reverse_collisions_publish_invalid_snapshot_and_fail_closed():
    resolver = SymbolResolver(
        {"EURUSDm": "EUR_USD", "EURUSD": "eur_usd"}
    )
    resolver.initialize(Catalog(["EUR_USD", "eur_usd"]), ["EURUSDm", "EURUSD", "BTC"])

    snapshot = resolver.snapshot
    assert snapshot.ready is False
    assert snapshot.missing == ("BTC",)
    assert set(snapshot.reverse_collisions) == {"eur_usd"}
    assert set(snapshot.reverse_collisions["eur_usd"]) == {"EURUSD", "EURUSDm"}
    with pytest.raises(SymbolResolutionError, match="ambiguous_logical_alias"):
        resolver.resolve("EURUSDm")
    with pytest.raises(SymbolResolutionError, match="unsupported_symbol"):
        resolver.resolve("BTC")


def test_failed_catalog_read_invalidates_snapshot_atomically():
    resolver = SymbolResolver()
    resolver.initialize(Catalog(["BTCUSDm"]), ["BTC"])
    assert resolver.snapshot is not None
    broken = Catalog([])
    broken.symbols_get = lambda: None

    with pytest.raises(SymbolResolutionError, match="symbol_catalog_unavailable"):
        resolver.refresh(broken, ["BTC"])
    assert resolver.snapshot is None
    assert resolver.mapping == {}
    with pytest.raises(SymbolResolutionError, match="resolver_not_initialized"):
        resolver.resolve("BTC")
