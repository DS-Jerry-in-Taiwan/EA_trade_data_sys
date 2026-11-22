# XAU archive capture plan

This plan defines the reviewed historical fixture set for TradeBot E2E research.

## Identity

- logical symbol: `XAUUSDm`
- source symbol: `XAUUSD.sim`
- contract revision: `archive.dataset.v1`
- dataset files: one each for M5, M15, and H1

## Coverage

The fixture set uses the complete available sanitized cache range for each timeframe. M5 contains 520 bars, M15 contains 220 bars, and H1 contains 520 bars. The range includes warmup bars before the requested replay range. Consumers must check the requested range and strategy warmup before joining.

The join uses UTC timestamps and the latest closed M15 or H1 bar at or before each M5 timestamp. Missing coverage fails closed.

## Evidence

Each document contains closed-bar coverage, a canonical digest, session fingerprint, generation, epoch, snapshot schema revision, calendar revision, and source/deployment provenance. The historical snapshot is an offline sanitized observation. `calendar_revision=unknown` is non-authoritative and cannot be used to enable live decisions.

## Safety

The fixtures are read-only research data. They contain no credentials, account identifiers, API keys, or private identity. They do not authorize mutation.
