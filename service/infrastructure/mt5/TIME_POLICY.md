# Minimal broker time conversion (library only)

`normalize_mt5_time(raw, resource, server, policy, now=...)` is a pure,
stdlib-only helper. It performs no MT5/network/file access and does not infer
an offset. `TimePolicy` is immutable, strictly validated, and loaded through
`Settings.time_policies`. Missing configuration produces an empty tuple; there
is deliberately no active broker policy or implicit three-hour subtraction.

Each policy requires id/version, exact server and resource
(`tick`, `bar`, `order`, `deal`, `position`), explicit `seconds` or
`milliseconds`, semantic, evidence source, and aware validity bounds.
`utc_epoch` leaves the encoded instant unchanged. `broker_wall_clock` requires
exactly one IANA timezone or bounded integer `offset_seconds`. `unknown`
cannot normalize. Both evaluation time and resulting UTC must fall within
the half-open validity interval. DST gaps/folds fail closed. Policy source
strings are operator assertions, not automatic proof of broker semantics.

The immutable result retains raw value/unit and policy provenance alongside
an aware UTC datetime. Passing that wrapper back as raw input is rejected;
extracting its numeric data and calling twice cannot be automatically detected
and must be prevented by adapter ownership. Freshness is separate: a future or
stale normalized instant is not clamped to receipt time or made fresh.

Configuration imports this exact independent leaf as a narrowly tested package
boundary exception. No general config-to-MT5 client access is permitted.

## Acceptance split

- Server #86: pure helper and typed configuration.
- #88 stage A: deterministic library/configuration tests and isolated service
  regression; not live broker or wire certification.
- #80/#81: broker timezone/DST/date applicability evidence.
- #87: per-resource interpretation and old-data provenance.
- #89: adapter integration, reviewed deployment and GET-only acceptance.

Adapters do not call the helper yet. No production YAML, cache, API path,
OpenAPI, authorization clock, gate, or idempotency behavior is changed here.
Future integration must select one applicable policy explicitly, bind it to
the current session/server, and convert only once before canonical mapping.
Historical policy use may require explicit historical evaluation time; this
does not authorize policy reuse in a current live session.
