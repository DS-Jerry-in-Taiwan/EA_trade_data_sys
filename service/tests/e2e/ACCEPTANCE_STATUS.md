# Read-only acceptance checkpoint (2026-11-14 UTC)

Parent: server #99. Integration PR: #104. This is a partial evidence record,
not completed E2E, bot admission, stable-operation or trading certification.

## Source and offline verification

Application base: `afc6a88`. Tested implementation: `98f07ec`, including runner
failure propagation (#100) and required authenticated Data reads (#101).

```bash
READONLY_API_KEY=synthetic-offline-only PYTHONPATH=. python3 -m pytest \
  service/tests --ignore=service/tests/e2e -q -rs -p no:cacheprovider
```

Disposable environment: Python 3.13, pytest 8.4.2, declared application/test
dependencies. Result: **703 passed, 1 skipped, 10 warnings**. The skip is the
actual-bot-contract decoder test: external bot contract evidence was not
available. Warnings concern an existing pandas datetime parsing deprecation.
The test dependency versions are not a proof of deployment image equivalence.

```bash
python3 -m pytest service/tests/unit/test_e2e_runner.py \
  service/tests/unit/test_required_read_probe.py -q -p no:cacheprovider
```

Focused result: **26 passed**. Runner red evidence: 11 failed, 1 passed before
correction. Required-probe review regressions initially had four failures;
after correction the probe's 14 tests pass. Bash syntax and diff checks pass.
Only fake mutation adapters and synthetic credentials are used offline.

## Separate live read-only observations

| Evidence lane | Observation | Limit |
| --- | --- | --- |
| Execution diagnostic | 18/18 structural GET checks pass; observed session bracket stable | Four quote/risk pairs report fresh=false and risk_ready=false; no independent UTC freshness certification |
| Required Data probe | Six HTTP GETs return 200; four protected reads and observed DEMO session bracket pass | Market remains unknown; account/list responses lack per-response identity |
| Data worker status | 16/16 history combinations reported ready | Worker state is not proof of complete, calendar-correct history |
| Deployed history E2E | test_recent_data passes; test_history_data_freshness fails | Existing unconditional 900-second expectation remains unresolved under #82; failure is not waived |
| Containers | Three healthy, restart counts 0 at observation | Snapshot only, not an observation window or restart test |

The deployed history checks required the container's `/app` import root;
the initial invocation without it had collection errors and is not a data
failure. Neither invocation establishes bot consumption or source convergence.

## Deployment and remaining gates

An explicitly authorized metadata-only repair restored the existing deployment
Git pointer after private backup. Deployment HEAD stayed `2c19117`; hashes of
all 207 tracked files and worktree registration metadata remained unchanged.
No checkout, configuration/mount edit, service restart or deployment occurred.
Existing differences and a private backup permission warning were preserved.
Metadata repair alone does not complete #96.

| Gate | Current state / next evidence |
| --- | --- |
| #100/#101 integration | Implemented and locally verified; integration PR remains draft |
| #96 source/configuration/image convergence | Incomplete; reviewed deployment plan and separate runtime authorization required |
| #82/#89 calendar and UTC freshness | Incomplete; do not infer closure or an offset from symbol names, weekends or stale quotes |
| #97 restart/recovery | No new authorized restart test; saved-session and recovery proof still required |
| #102 actual bot consumption | External bot contract and consumer evidence missing; server self-tests do not replace it |
| #103 stability | No qualifying 24-hour window spanning market open and rollover; container uptime cannot substitute |

No real order, cancel, close, grant creation, gate enablement or external
notification occurred. Changed identity/generation/epoch must not rebind old
grants. Keep unmet issues open and do not mark this integration as fully
accepted until all dependent evidence is supplied.
