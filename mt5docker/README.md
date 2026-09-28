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
