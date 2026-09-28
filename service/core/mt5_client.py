"""Compatibility import for the relocated MT5 client.

New code should import :mod:`service.infrastructure.mt5.client` directly.
"""

from service.infrastructure.mt5.client import MT5Client as InfrastructureMT5Client


class MT5Client(InfrastructureMT5Client):
    """Legacy constructor that keeps ``core.symbol_resolver`` patchable."""

    def __init__(self, connector_factory=None, resolver_factory=None):
        if resolver_factory is None:
            def resolver_factory():
                # Resolve lazily, matching the old module's monkeypatch and
                # extension behavior during the staged migration.
                from service.core.symbol_resolver import SymbolResolver

                return SymbolResolver()
        if connector_factory is None:
            super().__init__(resolver_factory=resolver_factory)
        else:
            super().__init__(connector_factory, resolver_factory=resolver_factory)

__all__ = ["MT5Client"]
