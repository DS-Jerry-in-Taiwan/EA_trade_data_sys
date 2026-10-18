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
AUTH_INDEX=0
UPDATE_STATUS=0
MT5_LOG_ROOT=/fixture/logs
MT5_READY_TIMEOUT=3
launch_terminal() { LAUNCHES=$((LAUNCHES + 1)); }
snapshot_terminal_logs() { SNAPSHOTS=$((SNAPSHOTS + 1)); }
await_terminal_ready() {
    local status="${READY_CODES[$READY_INDEX]}"
    READY_INDEX=$((READY_INDEX + 1))
    return "$status"
}
await_single_update() { return "$UPDATE_STATUS"; }
await_terminal_authorized() {
    local status="${AUTH_CODES[$AUTH_INDEX]:-0}"
    AUTH_INDEX=$((AUTH_INDEX + 1))
    return "$status"
}
reset_case() { LAUNCHES=0; SNAPSHOTS=0; READY_INDEX=0; AUTH_INDEX=0; AUTH_CODES=(0); UPDATE_STATUS=0; }

reset_case; READY_CODES=(0)
assert_success start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] && [ "$SNAPSHOTS" -eq 0 ] || fail 'no-update path launched incorrectly'

reset_case; READY_CODES=(10 0)
assert_success start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 2 ] && [ "$SNAPSHOTS" -eq 2 ] || fail 'controlled update did not relaunch with fresh snapshot'

reset_case; READY_CODES=(12)
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] || fail 'duplicate updater should fail before relaunch'

reset_case; READY_CODES=(10); UPDATE_STATUS=15
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] || fail 'timed-out update should not relaunch'

reset_case; READY_CODES=(10 10)
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 2 ] && [ "$SNAPSHOTS" -eq 2 ] || fail 'repeated update path was not bounded to one cycle'

# A late mandatory update after Startup shares the same one-cycle budget.
reset_case; READY_CODES=(0 0); AUTH_CODES=(10 0)
assert_success start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 2 ] && [ "$SNAPSHOTS" -eq 2 ] && [ "$AUTH_INDEX" -eq 2 ] || fail 'late update did not recover through authorization'

reset_case; READY_CODES=(10 0); AUTH_CODES=(10)
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 2 ] && [ "$SNAPSHOTS" -eq 2 ] || fail 'late update exceeded the shared recovery budget'

reset_case; READY_CODES=(0); AUTH_CODES=(21)
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] && [ "$SNAPSHOTS" -eq 0 ] || fail 'authorization timeout caused an unbounded credential/restart retry'

reset_case; READY_CODES=(0); AUTH_CODES=(11)
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] || fail 'vanished terminal was relaunched without update evidence'

# The updater can resume the native normal terminal before the next probe.
# Preserve its existing Journal baseline and do not launch a duplicate.
reset_case; READY_CODES=(10 0); UPDATE_STATUS=20
assert_success start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] && [ "$SNAPSHOTS" -eq 1 ] || fail 'native auto-restart launched a second terminal or lost its Journal checkpoint'

reset_case; READY_CODES=(0 0); AUTH_CODES=(10 0); UPDATE_STATUS=20
assert_success start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] && [ "$SNAPSHOTS" -eq 1 ] && [ "$AUTH_INDEX" -eq 2 ] || fail 'late native auto-restart bypassed authorization or launched a duplicate'

reset_case; READY_CODES=(10 10); UPDATE_STATUS=20
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] || fail 'native auto-restart exceeded the one-update budget'

reset_case; READY_CODES=(10 0); AUTH_CODES=(21); UPDATE_STATUS=20
assert_failure start_terminal_with_one_update_cycle /fixture/snapshot
[ "$LAUNCHES" -eq 1 ] || fail 'native auto-restart bypassed fresh authorization'

# Integrate the real poll/log parsers with a synthetic native updater. The
# replacement starts and writes Journal lines while the updater still runs;
# its authorization must survive completion without launching a duplicate.
run_native_case() (
    NATIVE_CASE="$1"
    NATIVE_TMP="$(mktemp -d)"
    trap 'rm -rf "$NATIVE_TMP"' EXIT
    unset SECONDS; SECONDS=0
    . "$ROOT/mt5docker/terminal_lifecycle.sh"
    MT5_LOG_ROOT="$NATIVE_TMP/logs"; mkdir -p "$MT5_LOG_ROOT"
    MT5_READY_TIMEOUT=3; MT5_UPDATE_TIMEOUT=3
    NATIVE_SNAPSHOT="$NATIVE_TMP/launch.snapshot"
    printf 'account authorized on synthetic-prior-container\r\n' | iconv -f UTF-8 -t UTF-16LE > "$MT5_LOG_ROOT/journal.log"
    snapshot_terminal_logs "$MT5_LOG_ROOT" "$NATIVE_SNAPSHOT"
    NATIVE_PHASE=0; NATIVE_LAUNCHES=0
    write_journal() { printf '%s\r\n' "$@" | iconv -f UTF-8 -t UTF-16LE >> "$MT5_LOG_ROOT/journal.log"; }
    launch_terminal() {
        NATIVE_LAUNCHES=$((NATIVE_LAUNCHES + 1)); NATIVE_PHASE=1
        case "$NATIVE_CASE" in
            saved-authorized|saved-no-auth)
                . "$ROOT/mt5docker/terminal_config.sh"
                set_mt5_terminal_launch_arguments terminal 0 'Z:\synthetic-private.ini'
                [ "${MT5_TERMINAL_ARGS[*]}" = '/portable /skipupdate' ] || fail 'saved-session launch included private config'
                NATIVE_PHASE=3
                write_journal 'Terminal MetaTrader5 build 5321 started for synthetic-broker'
                if [ "$NATIVE_CASE" = saved-authorized ]; then write_journal 'account authorized on synthetic-GUI-session'; fi
                return 0
                ;;
        esac
        write_journal 'Terminal MetaTrader 5 x64 build 5320 started' 'account authorized on synthetic-old-session'
        if [ "$NATIVE_CASE" != stale-no-update-log ]; then write_journal 'LiveUpdate start synthetic/liveupdate/terminal64.exe /update /config:synthetic.ini'; fi
        if [ "$NATIVE_CASE" = ambiguous-pre-observation ]; then
            NATIVE_PHASE=2
            write_journal 'Terminal MetaTrader5 build 5321 started' 'account authorized on synthetic-unattributed-session'
        fi
    }
    normal_terminal_pids() {
        if [ "$NATIVE_PHASE" -ge 2 ]; then printf '701\n'; fi
    }
    update_terminal_pids() {
        if [ "$NATIVE_PHASE" -eq 1 ] || [ "$NATIVE_PHASE" -eq 2 ]; then printf '601\n'; fi
    }
    capture_terminal_process_state() {
        NORMAL_PIDS="$(normal_terminal_pids | paste -sd, -)"
        UPDATE_PIDS="$(update_terminal_pids | paste -sd, -)"
        NORMAL_COUNT="$(normal_terminal_pids | count_lines)"
        UPDATE_COUNT="$(update_terminal_pids | count_lines)"
    }
    sleep() {
        SECONDS=$((SECONDS + 1))
        [ "$NATIVE_CASE" != timeout ] || return 0
        NATIVE_PHASE=$((NATIVE_PHASE + 1))
        if [ "$NATIVE_PHASE" -eq 2 ] && [ "$NATIVE_CASE" != stale-no-update-log ]; then
            write_journal 'Terminal MetaTrader5 build 5321 started for synthetic-broker'
            if [ "$NATIVE_CASE" = authorized ]; then write_journal 'account authorized on synthetic-GUI-session'; fi
        fi
    }
    if start_terminal_with_one_update_cycle "$NATIVE_SNAPSHOT"; then status=0; else status=$?; fi
    case "$NATIVE_CASE" in
        authorized|saved-authorized) [ "$status" -eq 0 ] || fail 'native saved session did not authorize' ;;
        no-new-auth|saved-no-auth) [ "$status" -eq 21 ] || fail 'native saved session reused stale authorization' ;;
        timeout) [ "$status" -eq 15 ] || fail 'native updater did not time out' ;;
        stale-no-update-log|ambiguous-pre-observation) [ "$status" -eq 1 ] || fail 'native replacement reused old or unattributable Startup/auth' ;;
    esac
    [ "$NATIVE_LAUNCHES" -eq 1 ] || fail 'native updater completion launched a duplicate/fixed-account terminal'
)
run_native_case authorized
run_native_case no-new-auth
run_native_case timeout
run_native_case saved-authorized
run_native_case saved-no-auth
run_native_case stale-no-update-log
run_native_case ambiguous-pre-observation

echo 'terminal update state-machine tests passed'
