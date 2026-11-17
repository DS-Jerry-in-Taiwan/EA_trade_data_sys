# Module ownership and dependency matrix

This document is the ownership contract for the converged Trade Data System.
Workers share infrastructure seams; they do not call one another directly.

## Runtime boundaries

| Module | Owns | May depend on | Must not own / call |
|---|---|---|---|
| `api_gateway` / gateway routes | Public HTTP/WebSocket routes, authentication, validation, DTOs and read models | Application read services, config | MT5/RPyC connections, tick fetching, symbol/calendar policy, file writes |
| `tick_worker` / realtime worker | Tick acquisition, freshness and IPC publication | MT5 adapter, session/symbol resolver, settings | History downloads, route code, direct client HTTP calls |
| `history_worker` | Historical downloads, gap classification, cache publication and metadata | MT5 adapter, session/symbol resolver, calendar adapter, status store | Realtime IPC, gateway code, embedded broker timezone policy |
| MT5 adapter | MT5/RPyC lifecycle, transport retries and raw-call normalization | Settings and MT5 runtime | Business readiness, HTTP concerns, ad-hoc connections |
| Session/symbol resolver | Account/session identity, generation and logical-to-broker mapping | MT5 adapter and settings | Calendar schedules, history persistence, route formatting |
| Calendar evidence adapter | Broker schedule evidence, timezone/DST, holidays, early closes and UTC windows | Versioned evidence/config | MT5 lifecycle, quote collection, route decisions |
| Status/history store | Versioned provenance, session generation, publication and readiness snapshots | Storage backend/config | MT5 calls, symbol resolution, authentication |
| Supervisor | Process ownership, startup/shutdown and failure propagation | Entrypoint configuration | Domain logic, API handling, worker orchestration |

## Dependency direction

```text
gateway routes ──> application read services ──> infrastructure seams
tick worker ──────> MT5 adapter + identity resolver
history worker ───> MT5 adapter + identity resolver + calendar adapter + store
calendar adapter ─> evidence/config
supervisor ───────> entrypoints (process lifecycle only)
```

The gateway is the only external application seam; MT5/RPyC remains internal.
History and realtime remain separate modules and do not call one another.

## Identity and invalidation

The resolver emits a session generation and mapping snapshot. A change of
account, server, terminal boot epoch, or generation invalidates previous
quote, risk, calendar and history-provenance snapshots. Consumers resolve
again rather than reusing an old broker symbol or grant.

The calendar adapter is fail-closed: incomplete or unversioned evidence is
`unknown`, never inferred from a product name. History may degrade while
realtime continues, but the gateway exposes degraded metadata and the bot
must not treat it as execution readiness.

## Composition root

Settings and adapters are constructed once at the composition root and
injected into application services. Routes, workers and domain objects must
not read ad-hoc environment variables or construct MT5 clients directly.
