# AGENTS.md

## Repo map
- `mt5docker/` + `service/` are the MT5 bridge/API; `apps/trade-review-web/` is the Next.js review/simulation UI.
- There is no repo-level JS workspace manifest; run frontend commands inside `apps/trade-review-web/` with `npm` (`package-lock.json` is there).
- Real REST entrypoint is `service/api_gateway.py`; `mt5docker/start_runner.sh` launches `tick_service.py`, `history_service.py`, and `api_gateway.py`.
- All MT5 calls should go through `service/core/mt5_client.py`; avoid ad-hoc connections.

## Commands that are easy to guess wrong
- Start the stack from the compose dir: `cd mt5docker && docker compose up -d`.
- Quick API checks: `curl http://localhost:8090/api/v1/health` and `curl http://localhost:8090/api/v1/symbols`.
- Backend E2E against running containers: `docker exec python-runner python3 -m pytest /app/service/tests/e2e -q`.
- One backend test: `docker exec python-runner python3 -m pytest /app/service/tests/e2e/test_api.py::TestHealthEndpoint::test_health_status -q`.
- Frontend checks: `cd apps/trade-review-web && npm run lint && npm run typecheck` (use `npm run dev` / `npm run build` there too).

## Runtime quirks
- Python code hard-codes `/app`; prefer running tests inside `python-runner`.
- E2E fixtures assume `http://localhost:8090/api/v1`, a live stack, and files under `/app/service/data`.
- `mt5docker/start_server.sh` owns MT5/VNC startup and the `pymt5linux` proxy on port 8001; runner logs land in `/app/service/logs/*.log`.
- Logical symbols are exactly `XAUUSDm`, `EURUSDm`, `GBPUSDm`, and `BTC`; keep names unchanged in code, URLs, and filenames.
- History cache files are `/app/service/data/history/{symbol}_{timeframe}.csv`; `/rates/<symbol>` reads the cache, `/rates/<symbol>/query` hits MT5 directly.

## Config and secrets
- Trade Query API (`/account`, `/positions`, `/history/deals`, `/history/orders`) requires `X-API-Key`; `READONLY_API_KEY` is injected via `mt5docker/compose.yaml`.
- Frontend DB access uses `apps/trade-review-web/.env.local`; see `.env.local.example` for the expected `DATABASE_URL`.
- Do not read, print, or commit `service/config/accounts.json`, `mt5docker/accounts.json`, `.env*`, or generated `mt5docker/mt5cfg.ini`.
- Leave `.opencode/` alone unless the task is explicitly about OpenCode config.

## Docs
- Planning docs belong under `docs/agent_context/<phase>/`; architecture docs belong under `docs/arch/`.
- `docs/` is gitignored, so check file paths directly instead of relying on `git status`.
