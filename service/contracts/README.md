# Public metadata contracts

`history_session_metadata.schema.json` is the versioned wire contract for the
non-sensitive metadata that qualifies persisted history. It intentionally
contains no credentials, API keys, login numbers, account fingerprints, or
other account identifiers. Producers must reject an unknown state or field
instead of treating it as ready.

The legacy `GET /api/v1/rates/{symbol}` response remains the existing JSON
array of OHLCV rows. Metadata is additive and must not be folded into that
array; consumers that need metadata use the versioned envelope represented by
the OpenAPI schemas and fixtures.
