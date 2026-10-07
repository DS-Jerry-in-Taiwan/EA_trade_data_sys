# MT5 Docker runtime

This directory defines the two-container runtime for the trade-data application:

- `mt5-server` runs the Wine MT5 terminal, VNC and the `pymt5linux` RPyC bridge.
- `trade-data-service` runs one application image/container with three supervised OS processes: `service.entrypoints.tick_worker`, `service.entrypoints.history_worker` and `service.entrypoints.api_gateway`.

Port `8090` on API Gateway is the only external trade-data API. RPyC port `8001` is reachable only through the Compose network and must not be published on the host. VNC is an operational interface, not a trade-data API.

## Startup

```bash
export READONLY_API_KEY='<your-readonly-api-key>'
docker compose up -d
```

The lifecycle is ordered as follows:

1. `mt5-server` starts Wine with the Windows-readable terminal config path, skips LiveUpdate and waits until exactly one terminal is ready.
2. The RPyC bridge starts only after terminal readiness. Its healthcheck fails if the terminal exits or enters LiveUpdate.
3. Compose starts `trade-data-service` after `mt5-server` is healthy.
4. `start_runner.sh` installs `/app/mt5docker/requirements.txt` from the bind-mounted checkout, then `exec`s `service.runtime.supervisor`. These packages are installed at service startup; they are not baked into the image.
5. The supervisor starts Tick Service, History Worker and API Gateway. If any child exits unexpectedly, it terminates the others and exits non-zero so the container restart policy can act.
6. `trade-data-service` becomes healthy only when Gateway reports HTTP 200 and `ready: true`.

The repository checkout is bind-mounted at `/app`; the running code and requirements therefore come from the deployment worktree selected by Compose.

`pymt5linux` has two distinct installations. The Linux client is installed
from `requirements.txt` when `trade-data-service` starts. The RPyC server must run
inside Wine, so `Dockerfile` installs the pinned packages in
`wine-requirements.txt` (including `pymt5linux` and `MetaTrader5`) into
`C:/Python/python.exe` at image-build time. Rebuild the shared image after
changing those versions:

```bash
docker compose build mt5-server trade-data-service
```

## Application process boundaries

```text
MT5 terminal <- internal RPyC -> Tick Service -> /run/trade-data/ticks.sock -> API Gateway :8090
             <- internal RPyC -> History Worker -> atomic persisted CSV -> API Gateway reads
             <- internal RPyC -> AccountService (inside Gateway request boundary)
```

- Tick Service is the only periodic `symbol_info_tick()` caller. It publishes versioned newline-delimited JSON and does not wait for slow consumers.
- Gateway reconnects to the Unix socket with bounded backoff, keeps a fresh in-memory snapshot, and supplies REST/Socket.IO clients. A new IPC consumer receives current snapshots before live events.
- History Worker independently fetches, validates, merges and atomically publishes completed bars. Gateway history routes read persisted data through `HistoryRepository`; they do not synchronously fetch MT5 data.
- History failure degrades health reporting but does not make the real-time path unavailable. Stale/missing Tick status, disconnected IPC, or missing fresh symbols makes Gateway not ready.

## Connection and shutdown flow

External clients establish HTTP or Socket.IO connections only with Gateway on `8090`. A Socket.IO client emits `subscribe` with a logical symbol, receives an immediate fresh snapshot when available, then receives pushed `tick` events until it unsubscribes or disconnects. The WebSocket transport itself does not poll MT5.

Each process owns its own `MT5Client` lifecycle and reconnect behavior. On `SIGTERM` or `SIGINT`, the supervisor forwards graceful termination to all process groups, waits up to `SUPERVISOR_SHUTDOWN_TIMEOUT`, and only then sends `SIGKILL` to remaining children. Tick Service removes only the Unix socket inode it owns; Gateway stops its IPC consumer before closing its MT5 client.

## Verification

```bash
curl http://localhost:8090/api/v1/health
curl http://localhost:8090/api/v1/symbols
docker exec trade-data-service python3 -m pytest /app/service/tests/e2e -q
docker exec trade-data-service python3 -m pytest /app/service/tests --ignore=/app/service/tests/e2e -q
```

Expected health semantics:

- HTTP 200, `ready: true`, `status: healthy`: real-time and history status are healthy.
- HTTP 200, `ready: true`, `status: degraded`: real-time is ready; history is stale or unhealthy.
- HTTP 503, `ready: false`: real-time critical path is not ready.

Use the deployment environment's `VNC_PWD` for the VNC UI at `http://localhost:6081/vnc.html`. Never commit passwords, account files, generated terminal configuration, or API keys.

## MT5 connection modes

The service defaults to `connection.mode: terminal`. In this mode the MT5
terminal selected through the desktop/noVNC UI is authoritative: the Linux
service calls `initialize()` without login, password, or server arguments and
never opens `accounts.json`. This allows an operator to switch accounts in the
terminal without the backend taking the session back to a configured profile.

`managed` mode is an explicit opt-in for deployments that need profile-based
initialization. Set `MT5_CONNECTION_MODE=managed` (or `connection.mode:
managed`) and provide the private `MT5_ACCOUNTS_PATH`. Missing or malformed
managed configuration fails closed. Credentials must remain outside Git,
logs, API responses, and metrics.

In terminal mode, normal startup omits `/config` and restores the native saved
terminal settings. The persistent `MT5_Data`/Wine directory remains the source
of the GUI-selected session. The private mounted `mt5cfg.ini` is used only for
bootstrap or an explicit operator re-import. The optional config-sanitizing
helper removes `Login`, `Password`, and `Server` from UTF-8 or Windows UTF-16
files and fails without publishing a partial file for malformed encoding. The
legacy `MT5_SYNC_CONFIG=1` path is rejected in terminal mode; it is available
only for an explicit managed deployment. `/skipupdate`, exactly-one-terminal
readiness, and the single bounded update cycle remain enforced for both modes.

For a clean persistent data directory, terminal mode supports a one-time
bootstrap import from the private mounted `mt5cfg.ini`. When
`MT5_Data/.mt5-bootstrap-complete` is absent or invalid and the portable tree
has no existing `Config/accounts.dat` account store, startup may use the
configured account in that file, waits for a successful read-only MT5
Journal authorization marker appended after launch, and then atomically writes
only the non-secret marker value `mt5-bootstrap-complete-v1`. Startup does not
create a separate RPyC client for this probe. The next
startup restores the native saved session without passing a startup config;
it still waits for the selected account session before
publishing terminal readiness. Set `MT5_BOOTSTRAP_REIMPORT=1` only for an explicit
operator-requested re-import; unset it afterward. A failed bootstrap never
writes the marker and startup exits fail-closed. Credentials are never printed
or copied into the marker.

The `/portable` executable is selected only from the root of the persistent
terminal tree (`MT5_PORTABLE_ROOT`, normally the Wine path mounted from
`MT5_Data`). A missing root executable fails startup; installer, backup, or
`liveupdate/terminal64.exe` payloads are never selected as a replacement.
Journal authorization also requires exactly one normal terminal and no update
process or unresolved update prompt. A mandatory updater observed during
authorization shares the same single bounded update cycle used during
startup. If LiveUpdate restarts exactly one normal terminal, startup preserves
that process and verifies its native `Terminal ... build ... started` and fresh
authorization records. It never launches a second terminal over the native
replacement. If the updater exits without a replacement, the single controlled
restart restores saved settings without repeating the bootstrap import.
An authorization timeout does not trigger a credential re-import.

After the service observes `account_info()`, the client publishes an
`account_session` health fact containing only a hashed login, server, account
mode, and monotonic session generation. A changed login/server/trade mode,
disconnect, or unknown mode makes that session not ready until a later
reconciliation step acknowledges the observed account. The raw login and all
credentials remain out of status, logs, metrics, and responses.

Gateway and execution health probes refresh this fact through a read-only
`account_info()` observation. Overall readiness is false for any session that
is disconnected, unknown, switching, or has failed reconciliation; history
storage remains untouched while the account-scoped MT5 state is rebuilt.
The canonical MT5 client performs this rebuild automatically after a detected
switch. The first read that observes the change is rejected, while subsequent
reads use the rebuilt session; a failed rebuild remains blocked and retryable.

Health refreshes are single-flight and snapshot-based: a request never waits
for `account_info()` over RPyC. One daemon monitor owns the potentially slow
refresh, publishes `refreshing`/not-ready while it is in flight, and keeps the
last confirmed session facts for subsequent probes. This prevents overlapping
health requests from consuming RPyC client slots.

`mt5-server` writes a post-startup readiness snapshot only after the normal
startup marker is observed. Its healthcheck requires that marker, exactly one
normal terminal, no updater process, and no uncompleted `LiveUpdate` log event.
After an update event, a later confirmed normal startup marker clears the
pending state; an old log event alone cannot permanently poison a new log
stream.

Execution mutation remains fail-closed behind both `EXECUTION_MUTATION_ENABLED`
and `EXECUTION_ACCOUNT_POLICY=DEMO`. Before preflight and again immediately
before the one MT5 send/cancel/close call, the execution adapter verifies the
Demo account fingerprint and captures the session generation. A generation
change is rejected as `session_changed_before_send`; it never targets a
resource from the previous terminal account. An indeterminate order result is
persisted and must be recovered through `orders/by-client/{client_order_id}`;
the server never retries it automatically.

## Execution-service deployment migration

The execution API is a separate Compose service on port `8091`; the existing
trade-data API on `8090` remains read-only. Both services attach to the
internal `mt5-server` RPyC bridge, while only the API ports and VNC port are
published. The default deployment keeps `EXECUTION_MUTATION_ENABLED=false`
and `EXECUTION_ACCOUNT_POLICY=DEMO`, so adding the service cannot enable order
mutation by accident.

The execution idempotency database is mounted at
`<deployment-worktree>/runtime/execution/idempotency.sqlite3` through the
`/app/runtime` bind mount. Preserve this directory during image rebuilds or
container replacement; deleting it intentionally discards timeout-recovery
state and requires a migration decision. A safe deployment migration is:

1. Render and inspect `docker compose -f mt5docker/compose.yaml config` with
   the deployment worktree's existing environment.
2. Verify the `runtime/` directory is writable and contains no credentials.
3. Build the image and start the stack from the deployment Compose directory.
4. Wait for `mt5-server` health, then probe only `GET /api/v1/health`,
   `GET /api/v1/account`, `GET /api/v1/symbols/{symbol}`, orders, positions
   and deals with the read-only key.
5. Keep mutation disabled until Demo-account evidence and the execution client
   handoff explicitly authorize a separate mutation test.

Do not use `docker compose down -v` during this migration: named or mounted
runtime state must remain available for restart/idempotency recovery.
