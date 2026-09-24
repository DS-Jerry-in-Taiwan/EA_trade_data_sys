#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
. "$ROOT/mt5docker/terminal_lifecycle.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_success() { "$@" || fail "expected success: $*"; }
assert_failure() { if "$@"; then fail "expected failure: $*"; fi; }

LAUNCHES=0
SNAPSHOTS=0
READY_INDEX=0
UPDATE_STATUS=0
MT5_LOG_ROOT=/fixture/logs
launch_terminal() { LAUNCHES=$((LAUNCHES + 1)); }
snapshot_terminal_logs() { SNAPSHOTS=$((SNAPSHOTS + 1)); }
await_terminal_ready() {
    local status="${READY_CODES[$READY_INDEX]}"
    READY_INDEX=$((READY_INDEX + 1))
    return "$status"
}
await_single_update() { return "$UPDATE_STATUS"; }
reset_case() { LAUNCHES=0; SNAPSHOTS=0; READY_INDEX=0; UPDATE_STATUS=0; }

reset_case; READY_CODES=(0)
assert_success start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] && [ "$SNAPSHOTS" -eq 0 ] || fail 'no-update path launched incorrectly'

reset_case; READY_CODES=(10 0)
assert_success start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 2 ] && [ "$SNAPSHOTS" -eq 1 ] || fail 'controlled update did not relaunch with fresh snapshot'

reset_case; READY_CODES=(12)
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] || fail 'duplicate updater should fail before relaunch'

reset_case; READY_CODES=(10); UPDATE_STATUS=15
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] || fail 'timed-out update should not relaunch'

reset_case; READY_CODES=(10 10)
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 2 ] && [ "$SNAPSHOTS" -eq 1 ] || fail 'repeated update path was not bounded to one cycle'

echo 'terminal update state-machine tests passed'
