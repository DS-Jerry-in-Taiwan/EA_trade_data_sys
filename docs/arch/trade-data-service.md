# Trade data service architecture (v3.1.0)

Release date: 2026-11-16. The deployment has three containers with separate
responsibilities:

```text
mt5-server (Wine + MT5 + internal RPyC :8001)
      |
      +---- trade-data-service :8090
      |       `-- service.runtime.supervisor (PID 1)
      |             |-- service.entrypoints.tick_worker
      |             |     `-- realtime -> MT5 + tick IPC publisher
      |             |-- service.entrypoints.history_worker
      |             |     `-- history -> MT5 + atomic CSV storage
      |             `-- service.entrypoints.api_gateway
      |                   `-- GET/read-only data and trade-query boundary
      |
      `---- execution-service :8091
              `-- service.entrypoints.execution_server
                    `-- execution interface + authorization/idempotency stores
```

`mt5-server` owns the terminal and the internal RPyC bridge. The bridge is
available only on the Compose network. `trade-data-service` owns realtime
polling, history persistence and the public data gateway. Its supervisor owns
exactly three process groups; an unexpected child exit stops its siblings and
makes the container exit non-zero. `execution-service` is a separate process
boundary and does not add execution processes to the data-service supervisor.

## Interfaces and safety gates

- Port `8090` is the data gateway. Its external evidence is GET-only: health,
  ticks, rates, symbols, account, positions, deals and orders.
- Port `8091` is the execution contract. The service has its own interface and
  durable authorization/idempotency stores so timeout recovery survives a
  container replacement.
- `EXECUTION_MUTATION_ENABLED=false` is the default. Mutation routes remain
  fail-closed until a separately authorized policy and account gate are both
  enabled. The release evidence attempted no order, cancel or close request.
- Each OS process owns its own `MT5Client`; source dependencies point inward
  through `service/infrastructure`. The old compatibility modules are not
  runtime entrypoints.

## Time and evidence boundaries

Broker timestamps are local FTMO/OANDA MetaTrader wall-clock values. The
configured conversion is validity-bounded:

| Validity window (broker wall clock) | Offset |
|:------------------------------------|:-------|
| `[2026-03-29, 2026-11-02)` | GMT+3 |
| `[2026-11-02, 2027-03-28)` | GMT+2 |

Values outside these windows, or values that do not match exactly one window,
fail closed. The service does not infer an offset from symbol names, weekends
or stale quotes.

The v3.1.0 evidence record is bounded and read-only:

- Execution structural GET checks: **18/18 passed**.
- Data GET checks: **7/7 passed**.
- Full service suite: **756 passed, 1 skipped, 7 baseline configuration
  failures**. The seven baseline failures remain visible and are not reported
  as a clean release suite.

These observations do not certify continuous uptime, current-UTC quote
freshness or complete history. History completeness blocker **#82** and broker
calendar blockers **#80** and **#81** remain open.

## State ownership

| State or responsibility | Repository path / interface | Owner and rule |
|:------------------------|:----------------------------|:---------------|
| Application source | `service/`, `core/`, `apps/` | Versioned code for data, execution, and connection boundaries |
| Deployment definition | `mt5docker/compose.yaml`, `mt5docker/start_*.sh`, `service/config/` | Container topology, exposed ports, selectors and startup lifecycle |
| Realtime/history state | `service/data/`, `/run/trade-data` | Tick/history cache, IPC socket and component status; generated at runtime |
| Execution durable state | deployment-managed `runtime/execution/` | Authorization and idempotency records; preserve across replacement and keep secrets out |
| Terminal state | persistent MT5 data mount selected by deployment | Native terminal session and readiness state; never publish account identity or credentials |

The repository paths describe ownership without embedding a deployment-specific
absolute path. Secrets, raw account identifiers and private terminal captures
are deployment inputs and do not belong in this document.

## Lifecycle

1. `mt5-server` starts the persisted terminal and only reports healthy after a
   fresh, unambiguous authorization state and RPyC listener are available.
2. Compose starts `trade-data-service` and `execution-service` after the MT5
   dependency is available. The data runner installs its bind-mounted Linux
   requirements before starting the supervisor.
3. Tick Service is the only periodic tick poller and publishes versioned NDJSON
   through its Unix socket. Gateway consumers reconnect with bounded backoff.
4. History Worker validates, merges and atomically publishes completed CSVs;
   history routes read those persisted files and do not synchronously fetch MT5.
5. Execution health and preflight reads observe the current session. A session
   change invalidates stale account-scoped state; the execution adapter rejects
   the request before any broker mutation.
6. On SIGTERM/SIGINT the data supervisor forwards graceful termination to all
   three children, waits for the bounded grace period and kills only survivors.
