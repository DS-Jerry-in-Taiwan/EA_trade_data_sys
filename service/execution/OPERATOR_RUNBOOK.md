# Bounded Demo authorization — read-only handoff

This runbook is a procedure, not permission to trade. No real mutation or gate
enablement is part of the implementation certification. Authentication via
`X-API-Key` alone never authorizes an operation. Wire envelope remains
`schema_version: 1`; OpenAPI document version is `1.1.0`.

## Approval and transport

Only an operator with separately approved trading authority may provision a
grant locally. There is no HTTP grant-creation endpoint. The immutable grant ID
is sent as `X-Execution-Authorization` alongside API authentication.

Obtain fresh authenticated `GET /api/v1/health`, account and symbol facts.
Require ready DEMO, exact fingerprint, generation and boot epoch. Select one
logical symbol, its exact current minimum volume, one client order ID,
explicit permissions, an absolute UTC entry window, a later bounded recovery
deadline, and a named abort owner. Reject missing/stale facts. The strict grant
schema rejects unknown fields. Never put credentials in the grant.

Use `python -m service.execution.authorization --help` for local provisioning
and entry-kill commands. Feed the private grant through stdin; do not publish
its production identity or use shell tracing. The authorization database must
share the service's persistent runtime volume. Inspect fresh health after
provisioning; provisioning does not override a closed runtime entry switch.

Exact local commands, only after separate operator approval:

```sh
python -m service.execution.authorization --database /app/runtime/execution/authorization.sqlite3 provision < approved-grant.json
python -m service.execution.authorization --database /app/runtime/execution/authorization.sqlite3 kill-entry APPROVED_AUTHORIZATION_ID --reason operator-stop
python -m service.execution.authorization --database /app/runtime/execution/authorization.sqlite3 abort-status
```

Do not run `provision` during read-only certification. `abort-status` is private
operator output and must not be pasted into public evidence.

The runtime entry switch is an additional prerequisite, not authorization.
Changing it requires a controlled service configuration workflow. A process
restart changes boot epoch and invalidates previously provisioned mutation
grants: fetch fresh health and obtain fresh operator approval afterwards.
Do not restart a service merely to toggle entry during an unresolved exposure.

For a **future separately authorized** bounded test, an operator-reviewed
Compose override must explicitly contain:

```yaml
services:
  execution-service:
    environment:
      EXECUTION_MUTATION_ENABLED: "true"
```

Apply only execution-service with the normal private runtime overrides and
approved key injection already configured:

```sh
docker compose -f compose.yaml -f compose.runtime.override.yaml -f approved-entry.override.yaml up -d --no-deps --no-build --force-recreate execution-service
```

After health recovers, fetch the new epoch and provision the approved grant.
Neither the override nor provisioning is executed in this handoff. To stop
new entry **without a restart**, use `kill-entry` first; this preserves the
same-session scoped exit. After proving zero exposure, recreate only
execution-service without the approved-entry override to return to the base
`false` configuration. Never remove the persistent runtime volume/database.

## Bounded entry and recovery

1. Obtain explicit approval for symbol, exact minimum volume, client order,
   operation permissions and absolute UTC windows. Verify the abort owner is
   available. Before any entry, reconcile GET orders/positions/deals and prove
   there is no pre-existing exposure or pending order in the selected symbol.
2. Provision the approved immutable grant locally. Enable the runtime entry
   prerequisite only through the separately approved operator workflow. Fetch
   fresh health and provision against the resulting boot epoch if restarted.
3. Submit one order using the granted ID, `Idempotency-Key` equal to its client
   order ID and matching `X-Request-ID`. Same key/different payload is rejected.
   Do not resend after a timeout, pending state, lost connection or crash.
4. Recover using GET by-client, orders, positions and deals. A 404 is not proof
   of non-acceptance. An ambiguous operation remains non-retryable. Escalate to
   the abort owner rather than creating another order or exit attempt.
5. Close/cancel only verified exclusively owned exposure under the same
   fingerprint, generation and boot epoch and within the recovery deadline.
   Entry expiry, entry kill and runtime entry disable do not themselves revoke
   this scoped recovery path. Session changes or shared/netting exposure deny
   mutation and require operator escalation; never send an exit to a different
   account. Recovery after restart needs separate reviewed operator handling;
   an old grant is deliberately invalid, not silently rebound.
6. On exit failure, persist entry kill and inspect the local abort outbox.
   The named operator must acknowledge and notify the abort owner. The outbox
   is durable evidence, **not proof of external notification delivery**. There
   is no configured external alert delivery transport in this release.
7. Before declaring cleanup, reconcile the original order, position and all
   related deals; establish zero test exposure and no pending test order.
   Do not use a gate boolean as cleanup proof. Kill entry locally, return the
   runtime entry prerequisite to default disabled, and archive deidentified
   reconciliation evidence. If certainty is impossible, report unresolved
   exposure and operator escalation, never success.

## Acceptance labels

- Deterministic fake tests exercise authorization, place, exit and restart.
- Real authenticated GET probes establish only read-only connectivity.
- Real Demo mutation and actual external abort notification delivery are not
  certified by either category and require additional explicit authorization.
- The bot must send the new operation header before any future mutation test;
  no changes to the bot repository are made by this server project.
