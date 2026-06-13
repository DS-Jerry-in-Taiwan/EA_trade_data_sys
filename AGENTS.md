# AGENTS.md

## What this repo is
- MT5 data bridge: `mt5docker/compose.yaml` runs `mt5-server` (Wine MT5 + `pymt5linux` on port 8001) and `python-runner` (Flask API on port 8090).
- The real REST entrypoint is `service/api_gateway.py`; background workers are launched by `mt5docker/start_runner.sh` as `tick_service.py`, `history_service.py`, and `api_gateway.py`.
- All MT5 calls should go through `service/core/mt5_client.py` (`ensure_connected()`, `call()`, resolver support) rather than constructing ad-hoc connections.
- Low-level connection settings come from `service/config/settings.yaml`: host `mt5-server`, fallbacks `172.21.0.2-4`, RPyC port `8001`, API port `8090`.

## Commands that are easy to guess wrong
- Start the stack from the compose directory: `cd mt5docker && docker compose up -d`.
- Check API quickly: `curl http://localhost:8090/api/v1/health` or `curl http://localhost:8090/api/v1/symbols`.
- Run the E2E suite against running containers: `docker exec python-runner python3 -m pytest /app/service/tests/e2e -q`.
- Run one focused E2E test: `docker exec python-runner python3 -m pytest /app/service/tests/e2e/test_api.py::TestHealthEndpoint::test_health_status -q`.
- Logs live in the container at `/app/service/logs/*.log`; the runner script prints them there, not to the host console after startup.

## Runtime and testing quirks
- Most Python files hard-code `/app` paths and insert `sys.path.insert(0, '/app')`; prefer running services/tests inside `python-runner` unless you intentionally patch paths/env.
- E2E fixtures assume API base `http://localhost:8090/api/v1` and a live stack; many tests check running processes, fresh files under `/app/service/data`, and MT5 connectivity.
- There is no verified lint/typecheck/formatter config in the repo; use focused pytest/curl checks for verification.
- `docs/` is gitignored and may be absent even though `/api/v1/openapi.yaml` references `/app/docs/api/v3_data_api.yaml`; verify file existence directly, not via `git status`.

## Data, symbols, and API conventions
- Configured logical symbols are `XAUUSDm`, `EURUSDm`, `GBPUSDm`, and `BTC`; do not uppercase symbols in API paths because services keep names exactly as configured.
- `SymbolResolver` may map logical names to broker names; external API/file names should generally keep the logical symbol from `settings.yaml`.
- History CSV cache files are named `/app/service/data/history/{symbol}_{timeframe}.csv`; API `/rates/<symbol>` reads these files, while `/rates/<symbol>/query` queries MT5 directly.

## Secrets and generated files
- Do not read, print, or commit real credentials from `service/config/accounts.json`, `mt5docker/accounts.json`, `.env*`, or generated `mt5docker/mt5cfg.ini`; they are ignored for a reason.
- `mt5docker/sync_mt5cfg.sh` rewrites `mt5docker/mt5cfg.ini` from `/app/service/config/accounts.json` before MT5 starts, so editing the INI alone is not the source of truth.
- `.opencode/` in this checkout is ignored runtime/config material and may contain API keys; avoid touching or quoting it unless the task is explicitly OpenCode configuration.
- Trade Query API (`/account`, `/positions`, `/history/deals`, `/history/orders`) requires `X-API-Key` header. The readonly key is stored in `.env` as `READONLY_API_KEY`; the Flask service must have it in its environment at runtime.

## Architecture documentation workflow
- Project planning docs, when requested, belong under `docs/agent_context/<phase>/`; architecture docs belong under `docs/arch/`. Remember `docs/` is gitignored, so check paths directly.
