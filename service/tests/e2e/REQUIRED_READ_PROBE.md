# Required authenticated read probe

`required_read_probe.py` is the explicit acceptance entry point for required
authenticated gateway reads. Run it only against an operator approved API URL
with the readonly key provided through the environment; it sends GET requests
to `/health`, `/account`, `/positions`, `/history/deals`, and
`/history/orders`. It reports redacted outcome names and HTTP statuses only.
It does not print response bodies, account identifiers, or the key.

Example:

```bash
READONLY_API_KEY='<operator supplied key>' python3 service/tests/e2e/required_read_probe.py \
  --base-url 'https://<approved-host>/api/v1'
```

The probe has three overall outcomes: `required_pass`, `fail`, and `unknown`.
Missing key, non-200 authenticated GET (including 401 and 503), malformed or
missing account/list data, and a changed health session identity are failures.
An empty positions, deals, or orders list is valid: it can describe zero
exposure or no activity in the requested period. A required probe does not
silently skip. Health session identity is sampled before and after the read
window. The gateway account/list responses do not include per-response session
identity, so a matching bracket is evidence of a stable observed window, not
proof of each response's provenance or that a session could not change and
change back between samples. Missing/unready health identity is `unknown` and
cannot produce an overall pass.

Market status is separate from authenticated-read status. The gateway health
heartbeat is not a quote and never certifies quote freshness. The existing
gateway tick endpoint does not expose the age/freshness contract needed here,
and this probe has no broker calendar source. Therefore this probe reports
market status as `unknown`; it may be reported as `market_closed` only when a
separate approved broker-calendar observation establishes closure. It does not
infer closure from weekends, missing quotes, HTTP errors, or heartbeat health.
Open-session freshness requires a quote with an explicit timestamp and age no
greater than five seconds; absence remains `unknown` and stale evidence fails.

## Outcome policy

| Outcome | Evidence | Counts as required pass? |
| --- | --- | --- |
| `required_pass` | All required GETs return 200 and valid response shapes; before/after session identity is ready and equal | Yes |
| `fail` | Missing key, 401/503/other non-200, missing/invalid required data, or session mismatch | No |
| `unknown` | Required health identity unavailable, or market/calendar/quote evidence absent | No |
| `market_closed` | Independent approved broker calendar says the relevant market is closed | No quote-freshness claim |
| optional skip | A separately labeled optional legacy check lacks its optional dependency/service | Never counts as required pass |

The existing broad `service/tests/e2e` suite remains a diagnostic suite, not
the required-read acceptance command. Its optional skips include unconfigured
`READONLY_API_KEY` checks in `test_api.py`, optional metrics endpoint checks,
missing diagnostic log files in service/monitoring checks, missing
`python-socketio` or an unavailable gateway in WebSocket checks, and absent
tick/log artifacts. These skips must be reported separately; they do not
override a failed required-read probe. Existing history/cache freshness checks
remain subject to #82/#89's broker-session, timezone, and calendar evidence
requirements. No market-closed classification or historical completeness is
claimed by this probe.

## Evidence lanes

Offline regression tests in `service/tests/unit/test_required_read_probe.py`
use synthetic responses and prove that missing credentials, 401/503, missing
data, and session mismatch fail without network access. They validate the
probe's failure behavior only. A real run of the command is a separate,
approved GET-only evidence item and must retain its redacted JSON result,
source/deployment revisions, UTC time, and explicit limitations. Neither lane
changes deployment, creates a grant, opens a gate, nor attempts a trade.

The command does not satisfy #82 history completeness, #89 UTC freshness, a
24-hour stability window, restart/recovery, broker quote freshness, WebSocket
consumer behavior, or bot acceptance. Those remain separate evidence and
blockers; this issue must not close them.
