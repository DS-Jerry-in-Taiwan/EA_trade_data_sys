"""Catalog-validated mapping between public logical and broker symbols.

Only exact names, known ``m``/``.sim`` suffixes, and a single underscore
between the base and quote are inferred. Deployment-specific names must be
configured explicitly, and are still checked against the current catalog.
"""

import re
import threading
import hashlib
import json
from dataclasses import dataclass, field
from collections.abc import Mapping
from types import MappingProxyType


def _digest(value):
    """Return a deterministic digest for non-secret mapping evidence."""
    payload = json.dumps(value, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class SymbolMappingSnapshot:
    """Immutable, account-scoped evidence for one symbol mapping build.

    A snapshot deliberately carries only the opaque account fingerprint token,
    generation and catalog/mapping digests.  It never stores account login,
    passwords or the raw account-info object.  ``mapping`` and
    ``reverse_mapping`` are mapping proxies so a reader cannot observe a
    partially rebuilt resolver or mutate the published state.
    """

    mapping_revision: int
    session_fingerprint: str | None
    session_generation: int | None
    catalog_digest: str
    mapping_digest: str
    mapping: Mapping
    reverse_mapping: Mapping
    missing: tuple[str, ...] = ()
    ambiguous: Mapping = field(default_factory=lambda: MappingProxyType({}))
    reverse_collisions: Mapping = field(default_factory=lambda: MappingProxyType({}))

    @property
    def unresolved(self):
        """Logical names that cannot be used from this snapshot."""
        values = list(self.missing)
        for symbol in self.ambiguous:
            if symbol not in values:
                values.append(symbol)
        for symbols in self.reverse_collisions.values():
            for symbol in symbols:
                if symbol not in values:
                    values.append(symbol)
        return tuple(values)

    @property
    def ready(self):
        """Whether every configured symbol has one safe forward/reverse path."""
        return not self.missing and not self.ambiguous and not self.reverse_collisions

    @property
    def valid(self):
        return self.ready

    def bound_to(self, session_fingerprint, session_generation):
        """Check that this snapshot belongs to the observed session ticket."""
        return (
            self.session_fingerprint == session_fingerprint
            and self.session_generation == session_generation
        )


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
        self._snapshot = None
        self._mapping_revision = 0
        self._lock = threading.RLock()

    def initialize(self, mt5, configured_symbols, *, session_fingerprint=None,
                   session_generation=None):
        """Replace all account-scoped state using the current broker catalog.

        Failed catalog reads invalidate the old mappings too. Initialization
        records per-symbol failures so callers can publish readiness details;
        ``resolve`` raises before an unsupported broker call can be made.
        """
        with self._lock:
            # Build every field in locals and publish one immutable snapshot at
            # the end. Readers therefore see either the previous complete
            # snapshot or the new complete snapshot, never a mixed catalog.
            self._mapping = {}
            self._reverse_mapping = {}
            self._failures = {}
            self._unresolved = []
            self._broker_symbols = set()
            self._initialized = False
            self._snapshot = None
            all_symbols = mt5.symbols_get()
            if all_symbols is None:
                raise SymbolResolutionError("catalog", "symbol_catalog_unavailable")
            broker_symbols = {
                symbol.name for symbol in all_symbols
                if isinstance(getattr(symbol, "name", None), str) and symbol.name
            }
            logical_symbols = tuple(dict.fromkeys(configured_symbols))
            mapping = {}
            failures = {}
            for logical in logical_symbols:
                try:
                    mapping[logical] = self._resolve_one(logical, broker_symbols)
                except SymbolResolutionError as exc:
                    failures[logical] = (exc.reason, exc.candidates)

            # Reverse normalization must never pick an arbitrary logical name
            # when multiple public symbols point at the same broker instrument.
            by_broker = {}
            for logical, broker in mapping.items():
                # Broker names are case-insensitive in the terminal. Treat
                # case-only aliases as a reverse collision as well.
                by_broker.setdefault(broker.casefold(), []).append((logical, broker))
            reverse_collisions = {}
            reverse_mapping = {}
            for broker_key, entries in by_broker.items():
                logical_names = tuple(sorted(logical for logical, _ in entries))
                if len(logical_names) > 1:
                    broker_names = tuple(sorted({broker for _, broker in entries}))
                    reverse_collisions[broker_key] = logical_names
                    for logical in logical_names:
                        mapping.pop(logical, None)
                        failures[logical] = ("ambiguous_logical_alias", broker_names)
                else:
                    reverse_mapping[entries[0][1]] = logical_names[0]

            ambiguous = {
                logical: candidates
                for logical, (reason, candidates) in failures.items()
                if reason in {"ambiguous_broker_symbol", "ambiguous_logical_alias"}
            }
            missing = tuple(
                logical for logical in logical_symbols
                if logical in failures and logical not in ambiguous
            )
            unresolved = tuple(logical for logical in logical_symbols if logical in failures)
            self._mapping_revision += 1
            snapshot = SymbolMappingSnapshot(
                mapping_revision=self._mapping_revision,
                session_fingerprint=self._normalize_fingerprint(session_fingerprint),
                session_generation=session_generation,
                catalog_digest=_digest(sorted(broker_symbols)),
                mapping_digest=_digest(sorted(mapping.items())),
                mapping=MappingProxyType(dict(mapping)),
                reverse_mapping=MappingProxyType(dict(reverse_mapping)),
                missing=missing,
                ambiguous=MappingProxyType(dict(ambiguous)),
                reverse_collisions=MappingProxyType(dict(reverse_collisions)),
            )
            self._mapping = dict(snapshot.mapping)
            self._reverse_mapping = dict(snapshot.reverse_mapping)
            self._failures = dict(failures)
            self._unresolved = list(unresolved)
            self._broker_symbols = broker_symbols
            self._snapshot = snapshot
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

    @staticmethod
    def _normalize_fingerprint(value):
        """Keep only the opaque fingerprint token in account-scoped state."""
        if isinstance(value, Mapping):
            value = value.get("id")
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _resolve_one(self, logical, broker_symbols=None):
        if not isinstance(logical, str) or not logical:
            raise SymbolResolutionError(logical, "unsupported_symbol")
        if broker_symbols is None:
            broker_symbols = self._broker_symbols
        if logical in self._aliases:
            alias = self._aliases[logical]
            if alias not in broker_symbols:
                raise SymbolResolutionError(logical, "configured_alias_unavailable", (alias,))
            return alias
        if logical in broker_symbols:
            return logical

        candidates = {logical.casefold()}
        base = logical
        if base.casefold().endswith(".sim"):
            base = base[:-4]
        elif base.casefold().endswith("m"):
            base = base[:-1]
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
            symbol for symbol in broker_symbols if symbol.casefold() in candidates
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

    @property
    def snapshot(self):
        """Current immutable mapping evidence, or ``None`` before init."""
        with self._lock:
            return self._snapshot

    @property
    def mapping_revision(self):
        with self._lock:
            return self._snapshot.mapping_revision if self._snapshot else 0

    def refresh(self, mt5, configured_symbols, *, session_fingerprint=None,
                session_generation=None):
        """Rebuild mappings after reconnect or account change."""
        self.initialize(
            mt5,
            configured_symbols,
            session_fingerprint=session_fingerprint,
            session_generation=session_generation,
        )


__all__ = ["SymbolMappingSnapshot", "SymbolResolutionError", "SymbolResolver"]
