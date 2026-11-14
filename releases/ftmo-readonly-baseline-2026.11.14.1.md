# FTMO Demo read-only recovery baseline 2026.11.14.1

Tag: `ftmo-readonly-baseline-2026.11.14.1`. This is a **prerelease recovery
reference**, not a production release or permission to trade. The tag identifies
the source, reference configuration and diagnostic in this commit; it does not
package private terminal state or reproduce an entire live deployment.

## Included

- A credential-free FTMO Demo settings example with catalog-verified mappings:
  XAUUSDm -> XAUUSD, EURUSDm -> EURUSD, GBPUSDm -> GBPUSD, BTC -> BTCUSD.
- A redacted, non-retrying GET-only execution diagnostic and synthetic tests.
- The operator recovery, normal GUI shutdown and rollback procedure below.

The default configuration and all application endpoints remain unchanged.
Data OpenAPI stays **4.0.0**, Execution OpenAPI **1.2.0**, and the execution
envelope uses integer `schema_version: 1`. The baseline identifier is not a
replacement for those contract versions or the unrelated frontend version.

## Historical validation, not an ongoing guarantee

The recovery observation established Demo login, a normal saved-session restart,
one normal terminal, no updater, `/skipupdate`, an internal RPyC listener and
three healthy containers. After the alias-only configuration change, 18/18
GET structural checks passed with stable session and expected mappings.
Mutation remained disabled and no real order/cancel/close was attempted.

Those observations were made on an existing operational deployment, not a
deployment of this new tag. Source/deployment convergence remains open. A healthy
container or HTTP 200 is not evidence of broker quote freshness.

## Explicit limitations

- All four quotes/risk responses reported not fresh/not ready. In the
  observation, the BTC timestamp led trusted UTC by about two hours. This cannot
  be explained just by a closed market, nor does it prove an annual offset/DST
  rule. No inferred time correction is enabled by this baseline.
- History was 11/16 ready: BTC:M5 insufficient; EURUSDm:D1, GBPUSDm:D1,
  XAUUSDm:D1 and XAUUSDm:H1 had gap-related errors. Published rows/markers
  matched the new broker symbols, but full historical completeness was not met.
- Durable historical provenance currently binds a broker symbol, not a complete
  account/server identity. Do not infer general cross-account isolation.
- Full service-suite, future market-open and mutation certification are not
  implied by the successful operational GET probe or synthetic diagnostic tests.

## Configuration and invocation

Explicitly select `service/config/examples/ftmo-demo.yaml` using the existing
`TRADE_DATA_CONFIG` selector (a path visible inside the container). It is a full
settings input with typed defaults, not an overlay automatically merged with
local settings: preserve and review site-specific storage/timing overrides.
Use the same reviewed settings for Data and Execution and restart only affected
services after approval. Missing/ambiguous symbols must remain fail closed.
Aliases are reference facts for the observed catalog, not universal FTMO names.

Run only against an operator-approved trusted origin:

```bash
python3 mt5docker/probe_execution_readonly.py \
  --base-url '<trusted-execution-origin>/api/v1' \
  --key-file '<private-key-file>'
```

The key file is never an argument value itself; it must be an operator-owned
regular file with no group/world access and no symlink. Use verified HTTPS
outside a trusted private transport. Redirects and environment proxies are
disabled. No request is retried and no by-client recovery route is called
(the latter can update durable reconciliation state despite using GET).

Exit 0 is a **diagnostic structural** pass, not complete account/order/deal
schema validation or bot admission. Health brackets detect observable session
changes, but list/account responses lack per-response session identity. The
tool reports `reported_fresh`, not independently certified current-UTC freshness;
old internally consistent responses can still pass. Bot must independently
validate current UTC, identity, mapping and risk facts. Exit 1 means a failed
check; exit 2 means setup failure. Never infer zero exposure from a failed GET.

## Controlled recovery and rollback

1. Obtain explicit account and downtime authorization. Operator supplies an
   existing valid Demo privately; never put credentials, account IDs, private
   paths or raw desktop captures in Git, issues or logs.
2. Record reviewed source, runtime binary build/hash, image identity and settings
   privately. Stop Data/Execution, stop MT5, and confirm no terminal/updater.
3. Back up the same point-in-time executable, native Config, Wine registry,
   bootstrap state and selectors; use owner-only permissions and verify backup.
4. Stage one explicitly selected private profile with password persistence.
   Import it once; do not silently substitute another account or broker.
   Always reset `MT5_BOOTSTRAP_REIMPORT=0`, including error paths. Preserve
   `/skipupdate`, Demo gates, idempotency and authorization stores.
5. Verify fresh broker authorization and Demo mode. A startup config login can
   succeed while normal startup still selects an old account. If needed, use
   the existing-account GUI login with Save password, then perform a **normal
   GUI exit** to flush the selected account before a config-free restart.
   Do not assume an abrupt process/container stop saves the new selection.
6. Prevent an automatic-restart race during the controlled GUI exit; restore the
   approved restart policy afterward. Prove a fresh, successful saved-session
   boot with reimport disabled, then restore Data/Execution and repeat GETs.
7. Keep entry disabled. Changed account/generation/epoch cannot reuse or rebind
   an old grant. Any future mutation requires separate scoped authorization.
8. On failure, stop affected services before restoring one coherent backup set
   and selectors; do not mix Wine/native state from different snapshots. Preserve
   investigation evidence. Restoring an expired account restores files, not
   broker usability. Do not disable UAC/TLS/readiness to force a pass.

Deployment metadata repair, main checkout migration, time-policy integration and
historical repair are separate work. Do not bundle them into this baseline.

Tracking: server #92/#94-#97, #84/#88/#89; bot #111. Identically numbered issues
in different repositories are not interchangeable. This baseline does not close
the full recovery specification or claim mutation readiness.
