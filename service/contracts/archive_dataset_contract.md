# Archive Dataset Contract v1

This document defines the read-only contract between TradeSystem historical archive and external research consumers such as TradeBot.

## Request

An archive request contains `logical_symbol`, `timeframe`, `start_utc`, `end_utc`, and `dataset_id` or an explicit archive scope. `start_utc` and `end_utc` are timezone-aware UTC timestamps with second precision. The range is ordered, the end is no later than the last closed bar, and the request is bounded to 32 days and 20,000 rows per timeframe. A page or result larger than the limit is rejected.

## Dataset

The response is storage-neutral. It contains `contract_revision`, `dataset_id`, `logical_symbol`, `source_symbol`, `timeframe`, `coverage`, `bars`, `archive_digest`, `digest_algorithm`, `provenance`, and `session_evidence`.

Each bar contains `time`, `open`, `high`, `low`, `close`, and `tick_volume`. Optional fields are `spread` and `real_volume`. `time` is a UTC Unix second. Bars are ordered by ascending `time`. Duplicate timestamps are invalid. OHLC values must be finite and satisfy `high >= max(open, low, close)` and `low <= min(open, high, close)`.

`coverage.start_utc` and `coverage.end_utc` identify the verified source range. `coverage.closed_bars` is true only when both boundaries and every returned bar satisfy the closed-bar rule. Missing bars, extra bars, or a source change between repeated reads invalidate the dataset.

## Digest

The digest is SHA-256 over the canonical JSON representation of the ordered rows. The canonical row order is `time, open, high, low, close, tick_volume, spread, real_volume`. Missing optional numeric fields are encoded as `0`. JSON uses compact separators `(',', ':')`, UTF-8 encoding, and no whitespace. The digest is calculated after UTC normalization and ascending sort.

## Session evidence

`session_evidence` contains an opaque `session_fingerprint`, integer `session_generation`, opaque `session_epoch`, `snapshot_schema_revision`, `calendar_revision`, and a historical session snapshot. The snapshot records the source and source symbol, captured time, validity window, and UTC trade/quote windows. It contains no account number, password, API key, or private identity.

If evidence is missing, unknown, stale, malformed, or changed during repeated reads, the dataset is rejected. A generation or epoch change before persistence invalidates the complete read. Consumers must not reuse a dataset after its session identity changes.

## Multi-timeframe dataset

M5, M15, and H1 are requested as separate verified datasets. Each dataset keeps its own digest and identity. TradeBot joins them only when all identities match and timestamps are normalized to UTC. A bar is aligned to its timeframe boundary. The requested range must be covered after the strategy warmup range is applied. The join uses the latest closed bar at or before the M5 timestamp. Missing M15 or H1 coverage fails closed.

## Provenance

`provenance.source_commit` identifies the reviewed TradeSystem source revision. `provenance.deployment_commit` identifies the deployment revision that produced the archive. `provenance.captured_at` records the capture time. These fields identify code and data lineage and contain no secrets.

## Safety

This contract is read-only. It does not authorize an order, cancel, close, grant, or mutation gate. It does not require a public endpoint in the first implementation. An adapter may read a local archive or a future read-only transport, but strategy code must use only the ArchiveDataset interface.
