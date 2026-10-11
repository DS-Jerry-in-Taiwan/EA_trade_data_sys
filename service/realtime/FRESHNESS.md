# Data health freshness semantics

Data API OpenAPI 4.1.0 retains existing endpoint paths and readiness behavior.
`ready` and HTTP 200 indicate the transport/account critical path is available,
not that broker quotes are new or that trading is authorized.

| Field | Timestamp basis | Meaning |
| --- | --- | --- |
| Component `fresh` / `heartbeat_fresh` | status `updated_at` | Worker published status recently |
| `fresh_symbols` | tick `received_at` | Legacy receipt-fresh symbols |
| `symbol_freshness[symbol].receipt` | tick `received_at` | Recent polling/transport observation |
| `symbol_freshness[symbol].broker_quote` | tick `source_time` | Broker timestamp age within `quote_max_age_seconds` |
| `quote_fresh_symbols` | tick `source_time` | Symbols with fresh broker timestamps |

Repeated polling can return the same old broker tick with a new receipt time.
It must not make the broker quote fresh. Missing broker timestamps are `null`;
missing, invalid, timezone-naive, stale and future timestamps are not fresh.
The legacy public tick `time` display field can fall back to receipt time and
must not be used as evidence of broker timestamp freshness.

History component heartbeat likewise does not certify latest bar time. These
fields do not establish market-open state, timezone, DST or holiday exceptions.
Execution quote/risk freshness and scoped mutation authorization are separate
contracts, unchanged by this Data health addition.
