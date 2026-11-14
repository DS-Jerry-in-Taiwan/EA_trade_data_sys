# Validation: 2026.11.14.1 read-only recovery reference

## Offline source checks

```bash
PYTHONPATH=. python3 -m pytest \
  service/tests/unit/test_readonly_recovery_baseline.py \
  service/tests/unit/test_config_loader.py \
  service/tests/test_dependency_boundaries.py \
  -k 'not test_package_skeleton_is_importable' -q -p no:cacheprovider
```

Result: **72 passed, 24 deselected**. The 24 package-import cases require the
full application dependency environment and were not run in this focused
source check. This is not a complete service-suite result.

The new diagnostic alone has 38 synthetic cases covering health/gate/session
rejection, malformed payloads, strict schema version, future/stale reported
quotes, conservative risk facts, mismatched broker mapping, safe key-file
permissions, redirect rejection and GET-only CLI transport. No real key or
MT5 connection is used by these tests. A historical internally consistent
payload deliberately cannot establish current-UTC or mutation certification.

## Post-review correction validation

The original tag and its historical results above remain unchanged. The PR
follow-up corrects FIFO key-file blocking with non-blocking open while retaining
descriptor-based file-type, owner, permission and symlink checks. It also names
the three loss comparison fields explicitly rather than relying on a slice.

Re-running the command above on the corrected source produced **82 passed,
24 deselected**. Running only
`service/tests/unit/test_readonly_recovery_baseline.py` produced **48 passed**.
The ten additional cases cover rejection of an owner-only FIFO without a writer
in a bounded subprocess, plus below/equal/above checks for each of the three
loss comparison fields. The FIFO test failed with a timeout before the fix and
passed afterward. An independent synthetic CLI check returned exit 2 and a
redacted setup-failure result for a FIFO. `git diff --check` also passed.

These are offline tests, not a complete service-suite run or deployed-build
certification. No real credentials, broker connection, grant, gate change or
mutation was used. The published baseline tag is not moved by this correction.

## Operational evidence boundary

The 18/18 GET result in the baseline record is a prior observation on the
recovered deployment. It is not evidence that this tag was deployed, nor an
ongoing guarantee. Exact private deployment/account/credential artifacts are
excluded from this publication. Re-run approved GET checks against the selected
Demo session before relying on readability; independently verify current UTC
freshness and all outstanding broker/history constraints.
