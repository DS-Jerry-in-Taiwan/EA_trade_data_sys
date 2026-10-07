"""Catalog-validated mapping between public logical and broker symbols.

Only exact names, known ``m``/``.sim`` suffixes, and a single underscore
between the base and quote are inferred. Deployment-specific names must be
configured explicitly, and are still checked against the current catalog.
"""

import re
import threading
from collections.abc import Mapping


class SymbolResolutionError(ValueError):
    """A logical symbol has no unambiguous, catalog-validated broker name."""

    def __init__(self, symbol, reason, candidates=()):
        self.symbol = symbol
        self.reason = reason
        self.candidates = tuple(sorted(candidates))
        detail = f": {', '.join(self.candidates)}" if self.candidates else ""
        super().__init__(f"Symbol {symbol!r} cannot be resolved ({reason}){detail}")


class SymbolResolver:
    """Resolve configured logical names, rejecting unsupported/ambiguous names.

    ``broker_aliases`` contains exact logical-to-broker names for the deployment
    broker. An explicit alias takes precedence over inference, but an alias that
    disappears after reconnect/account switch is rejected rather than replaced
    with a different product. ``BTC``/``ETH`` infer only USD markets; selecting
    a USDT or other quote requires an explicit deployment alias.
    """

    CRYPTO_BASES = frozenset({"BTC", "ETH"})
    KNOWN_SUFFIXES = ("", "m", ".sim")

    def __init__(self, broker_aliases=None):
        if broker_aliases is None:
            broker_aliases = {}
        if not isinstance(broker_aliases, Mapping) or any(
            not isinstance(logical, str) or not logical or logical != logical.strip()
            or not isinstance(broker, str) or not broker or broker != broker.strip()
            for logical, broker in broker_aliases.items()
        ):
            raise ValueError("broker_aliases must map non-empty symbol names to exact broker names")
        self._aliases = dict(broker_aliases)
        self._mapping = {}
        self._reverse_mapping = {}
        self._failures = {}
        self._unresolved = []
        self._broker_symbols = set()
        self._initialized = False
        self._lock = threading.RLock()

    def initialize(self, mt5, configured_symbols):
        """Replace all account-scoped state using the current broker catalog.

        Failed catalog reads invalidate the old mappings too. Initialization
        records per-symbol failures so callers can publish readiness details;
        ``resolve`` raises before an unsupported broker call can be made.
        """
        with self._lock:
            self._mapping = {}
            self._reverse_mapping = {}
            self._failures = {}
            self._unresolved = []
            self._broker_symbols = set()
            self._initialized = False
            all_symbols = mt5.symbols_get()
            if all_symbols is None:
                raise SymbolResolutionError("catalog", "symbol_catalog_unavailable")
            self._broker_symbols = {
                symbol.name for symbol in all_symbols
                if isinstance(getattr(symbol, "name", None), str) and symbol.name
            }
            logical_symbols = tuple(dict.fromkeys(configured_symbols))
            for logical in logical_symbols:
                try:
                    self._mapping[logical] = self._resolve_one(logical)
                except SymbolResolutionError as exc:
                    self._failures[logical] = (exc.reason, exc.candidates)

            # Reverse normalization must never pick an arbitrary logical name
            # when multiple public symbols point at the same broker instrument.
            by_broker = {}
            for logical, broker in self._mapping.items():
                by_broker.setdefault(broker, []).append(logical)
            for broker, logical_names in by_broker.items():
                if len(logical_names) > 1:
                    for logical in logical_names:
                        self._mapping.pop(logical)
                        self._failures[logical] = ("ambiguous_logical_alias", (broker,))
                else:
                    self._reverse_mapping[broker] = logical_names[0]
            self._unresolved = [logical for logical in logical_symbols if logical in self._failures]
            self._initialized = True
            print(f"[SymbolResolver] Resolved {len(self._mapping)}/{len(logical_symbols)}."
                  f" Unresolved: {self._unresolved}")

    def resolve(self, logical_name):
        """Return a validated broker name; never fall back to unchecked input."""
        with self._lock:
            if not self._initialized:
                raise SymbolResolutionError(logical_name, "resolver_not_initialized")
            if logical_name in self._mapping:
                return self._mapping[logical_name]
            reason, candidates = self._failures.get(logical_name, ("unsupported_symbol", ()))
            raise SymbolResolutionError(logical_name, reason, candidates)

    def logical_for(self, broker_name):
        """Normalize a broker result using only established current mappings."""
        with self._lock:
            if not self._initialized:
                raise SymbolResolutionError(broker_name, "resolver_not_initialized")
            if broker_name in self._reverse_mapping:
                return self._reverse_mapping[broker_name]
            raise SymbolResolutionError(broker_name, "unsupported_broker_symbol")

    def _resolve_one(self, logical):
        if not isinstance(logical, str) or not logical:
            raise SymbolResolutionError(logical, "unsupported_symbol")
        if logical in self._aliases:
            alias = self._aliases[logical]
            if alias not in self._broker_symbols:
                raise SymbolResolutionError(logical, "configured_alias_unavailable", (alias,))
            return alias
        if logical in self._broker_symbols:
            return logical

        candidates = {logical.casefold()}
        base = logical
        if base.casefold().endswith("m"):
            base = base[:-1]
        elif base.casefold().endswith(".sim"):
            base = base[:-4]
        base = base.upper()
        if base in self.CRYPTO_BASES:
            base += "USD"
        # Infer only a base/quote pair, never arbitrary punctuation removal or
        # partial-prefix matches such as BTCUSD matching BTCUSDT.
        if re.fullmatch(r"[A-Z]{3}_[A-Z]{3}", base):
            base = base.replace("_", "")
        if re.fullmatch(r"[A-Z]{6}", base):
            for stem in (base, f"{base[:3]}_{base[3:]}"):
                candidates.update((stem + suffix).casefold() for suffix in self.KNOWN_SUFFIXES)
        matches = {
            symbol for symbol in self._broker_symbols if symbol.casefold() in candidates
        }
        if len(matches) == 1:
            return matches.pop()
        if matches:
            raise SymbolResolutionError(logical, "ambiguous_broker_symbol", matches)
        raise SymbolResolutionError(logical, "unsupported_symbol")

    @property
    def mapping(self):
        with self._lock:
            return dict(self._mapping) if self._initialized else {}

    @property
    def unresolved(self):
        with self._lock:
            return list(self._unresolved) if self._initialized else []

    def refresh(self, mt5, configured_symbols):
        """Rebuild mappings after reconnect or account change."""
        self.initialize(mt5, configured_symbols)
