# Repository and deployment path provenance

This document defines the path roles for `EA_trade_data_sys`. It is the
provenance record for a source commit and its deployment. It does not contain
credentials, account data, or runtime state.

## Path roles

| Role | Path | Allowed contents | Git rule |
| --- | --- | --- | --- |
| Canonical source | `/home/ubuntu/projects/trade_data_dowload` | Versioned application source, tests, and documentation | This path is the source of truth. It must contain valid Git metadata before a release is made. An empty or incomplete `.git` directory is invalid. |
| Feature worktree | `/tmp/trade-data-<ticket>` | A short-lived checkout for one issue or PR | Create from the current `main` commit. Commit and push the feature branch. Do not use it as a deployment source. |
| Deployment worktree | `/home/ubuntu/projects/trade_data_dowload-ac3-deploy` | The exact reviewed commit selected for deployment | Deployment-only checkout. It must be clean, detached or on a deployment branch, and recorded with the source commit and image/build identifier. Do not develop here. |
| Runtime state | Docker volumes and host-managed MT5/Wine profile paths | Logs, caches, generated data, MT5 profile, and secrets | Never commit or copy into source. Preserve across Data/Execution rebuilds unless an explicit MT5-profile operation is approved. |

The similarly named directories `trade_data_dowload-ac3-market-data`,
`trade_data_dowload-trade-deal-contract`, and ad-hoc `/tmp/trade-data-*`
directories are not canonical. They are historical or task worktrees and must
not be used as a deployment input without an explicit provenance record.

## Required provenance record

For each deployment, record (without secrets):

1. repository and source path;
2. source commit and feature PR;
3. deployment worktree path and checked-out commit;
4. compose file path and bind-mount source;
5. service/image identifiers;
6. read-only verification result and timestamp.

The deployment commit must equal the reviewed source commit, unless a release
record explicitly identifies the reviewed range and the deployment commit.

## Runtime and cleanup rules

- Do not commit `.env*`, account files, API keys, MT5 credentials, Wine
  profiles, logs, caches, or generated history data.
- A feature worktree may be removed only after its branch is merged or
  abandoned and its commit/PR is recorded. Cleanup is optional and must not
  remove runtime state.
- Never delete the canonical source or deployment worktree as a cleanup step.
- Rebuild Data/Execution services from the recorded deployment commit. Keep the
  MT5 service and persistent profile unchanged unless separately authorised.
- A path or Git metadata mismatch is a release blocker; do not silently
  continue from an untracked directory.

## Verification commands

Run these checks before deployment (replace `<worktree>` with the selected
path):

```bash
git -C <worktree> rev-parse --show-toplevel
git -C <worktree> rev-parse HEAD
git -C <worktree> status --short
git -C <worktree> diff --exit-code
```

The deployment record must show the same commit for source review and the
deployment checkout. Use read-only health and contract probes after the
services are recreated. Do not use mutation endpoints as a path or deployment
verification step.
