#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export PROC_ROOT="$TMP/proc"
mkdir -p "$PROC_ROOT"
. "$ROOT/mt5docker/terminal_lifecycle.sh"

fail() { echo "FAIL: $*" >&2; exit 1; }
assert_success() { "$@" || fail "expected success: $*"; }
assert_failure() { if "$@"; then fail "expected failure: $*"; fi; }
add_process() {
    local pid="$1"; shift
    mkdir -p "$PROC_ROOT/$pid"
    printf '%s\0' "$@" > "$PROC_ROOT/$pid/cmdline"
}
clear_processes() { rm -rf "$PROC_ROOT"; mkdir -p "$PROC_ROOT"; }

# Portable mode must always use the persisted root terminal, even when Wine
# also contains installer/update payloads. It must not fall back to them if
# the root terminal is missing.
PORTABLE_ROOT="$TMP/persisted/MetaTrader 5"
mkdir -p "$PORTABLE_ROOT/liveupdate" "$TMP/Program Files/MetaTrader 5"
: > "$PORTABLE_ROOT/terminal64.exe"
: > "$PORTABLE_ROOT/liveupdate/terminal64.exe"
: > "$TMP/Program Files/MetaTrader 5/terminal64.exe"
[ "$(find_mt5_exe "$PORTABLE_ROOT")" = "$PORTABLE_ROOT/terminal64.exe" ] || fail 'portable executable escaped persistent data directory'
rm "$PORTABLE_ROOT/terminal64.exe"
assert_failure find_mt5_exe "$PORTABLE_ROOT"

add_process 101 'C:\Program Files\MetaTrader 5\terminal64.exe' /portable /skipupdate
assert_success exactly_one_normal_terminal

add_process 102 'C:\Program Files\MetaTrader 5\terminal64.exe' /update
assert_failure exactly_one_normal_terminal
[ "$(update_terminal_pids)" = 102 ] || fail 'update terminal was not classified'
# This is the same fail-closed predicate used by startup and healthcheck.
[ "$(update_terminal_pids | count_lines)" -gt 0 ] || fail 'LiveUpdate did not fail closed'
capture_terminal_process_state
[ "$NORMAL_COUNT" -eq 1 ] && [ "$UPDATE_COUNT" -eq 1 ] || fail 'single-scan process classification disagreed'
diagnostic="$(log_terminal_lifecycle_state startup 1)"
if printf '%s\n' "$diagnostic" | grep -Eq 'Program Files|terminal64\.exe|portable|skipupdate'; then
    fail 'lifecycle diagnostics included raw command-line contents'
fi

clear_processes
add_process 103 'C:\synthetic\liveupdate\terminal64.exe' /portable
[ "$(update_terminal_pids)" = 103 ] || fail 'updater payload path was classified as normal'
add_process 104 'C:\synthetic\liveupdate\terminal64.exe /update'
printf 'terminal64.exe\n' > "$PROC_ROOT/104/comm"
[ "$(update_terminal_pids | count_lines)" -eq 2 ] || fail 'Wine combined argv updater was not classified by comm'

clear_processes
add_process 201 'C:\Program Files\MetaTrader 5\terminal64.exe' /portable
add_process 202 'C:\Program Files\MetaTrader 5\terminal64.exe' /portable
assert_failure exactly_one_normal_terminal

clear_processes
add_process 301 /bin/bash terminal64.exe /update
[ "$(terminal_processes | count_lines)" -eq 0 ] || fail 'matched argument instead of executable'

# Simulate a /proc entry disappearing after the readability check.
add_process 302 'C:\Program Files\MetaTrader 5\terminal64.exe' /portable
read_process_exe() { return 1; }
[ "$(terminal_processes 2>"$TMP/proc-race.err" | count_lines)" -eq 0 ] || fail 'raced process was not skipped'
[ ! -s "$TMP/proc-race.err" ] || fail '/proc race emitted stderr'
unset -f read_process_exe
read_process_exe() { IFS= read -r -d '' REPLY < "$1"; }

MOCK_BIN="$TMP/bin"; mkdir -p "$MOCK_BIN"
cat > "$MOCK_BIN/ss" <<'EOF'
#!/usr/bin/env bash
printf 'LISTEN 0 5 0.0.0.0:8001 0.0.0.0:*\n'
EOF
chmod +x "$MOCK_BIN/ss"
PATH="$MOCK_BIN:$PATH" assert_success rpyc_is_listening

LOG_ROOT="$TMP/logs"; mkdir -p "$LOG_ROOT"
LOG="$LOG_ROOT/terminal.log"
SNAPSHOT="$TMP/log.snapshot"
printf 'Startup successfully initialized from start config\r\n' | iconv -f UTF-8 -t UTF-16LE > "$LOG"
snapshot_terminal_logs "$LOG_ROOT" "$SNAPSHOT"
UNCHANGED_LOG="$LOG_ROOT/old-terminal.log"
printf 'old synthetic Journal\r\n' | iconv -f UTF-8 -t UTF-16LE > "$UNCHANGED_LOG"
touch -d '2000-01-01 UTC' "$UNCHANGED_LOG"
for day in $(seq 1 72); do
    printf 'synthetic historic Journal\r\n' | iconv -f UTF-8 -t UTF-16LE > "$LOG_ROOT/history-$day.log"
    touch -d '2000-01-01 UTC' "$LOG_ROOT/history-$day.log"
done
MT5_LOG_ROOT="$LOG_ROOT"
if changed_terminal_logs "$SNAPSHOT" | tr '\0' '\n' | grep -Fq "$UNCHANGED_LOG"; then
    fail 'unchanged historical Journal was scanned'
fi
[ "$(changed_terminal_logs "$SNAPSHOT" | tr '\0' '\n' | count_lines)" -eq 1 ] || fail 'historic Journal scan did not stay limited to changed files'
assert_failure log_has_new_startup_marker "$SNAPSHOT" "$LOG"
printf 'network scan completed\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG"
assert_failure log_has_new_startup_marker "$SNAPSHOT" "$LOG"
printf 'Startup successfully initialized from start configuration\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG"
assert_failure log_has_new_startup_marker "$SNAPSHOT" "$LOG"
printf 'NotStartup successfully initialized from start config\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG"
assert_failure log_has_new_startup_marker "$SNAPSHOT" "$LOG"
printf 'Startup\tsuccessfully initialized from start config "Z:\\\\run\\\\mt5\\\\mt5cfg.ini"\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG"
assert_success log_has_new_startup_marker "$SNAPSHOT" "$LOG"

NEW_LOG="$LOG_ROOT/new-terminal.log"
printf 'Startup successfully initialized from start config\r\n' | iconv -f UTF-8 -t UTF-16LE > "$NEW_LOG"
assert_success log_has_new_startup_marker "$SNAPSHOT" "$NEW_LOG"
# Native saved-session starts have no Startup-from-config line.
NATIVE_LOG="$LOG_ROOT/native-terminal.log"
printf 'Terminal\tMetaTrader 5 x64 build 5320 started for synthetic-broker\r\n' | iconv -f UTF-8 -t UTF-16LE > "$NATIVE_LOG"
assert_success log_has_new_startup_marker "$SNAPSHOT" "$NATIVE_LOG"
snapshot_terminal_logs "$LOG_ROOT" "$TMP/native.snapshot"
assert_failure log_has_new_startup_marker "$TMP/native.snapshot" "$NATIVE_LOG"
# A large new log must not lose its marker through an early grep exit/SIGPIPE.
awk 'BEGIN { for (i = 0; i < 10000; i++) print "synthetic terminal status line" }' |
    iconv -f UTF-8 -t UTF-16LE >> "$NEW_LOG"
assert_success log_has_new_startup_marker "$SNAPSHOT" "$NEW_LOG"

# A terminal left behind after LiveUpdate must fail health until a later
# confirmed normal startup marker is observed.  The failure must not become
# permanent once the normal marker arrives.
READY_SNAPSHOT="$TMP/ready.snapshot"
MT5_LOG_ROOT="$LOG_ROOT"
snapshot_terminal_logs "$LOG_ROOT" "$READY_SNAPSHOT"
printf 'LiveUpdate entered update prompt\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG"
assert_success terminal_update_pending "$READY_SNAPSHOT"
printf 'Startup successfully initialized from start config\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG"
assert_failure terminal_update_pending "$READY_SNAPSHOT"
printf 'LiveUpdate started native updater\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG"
assert_success terminal_update_pending "$READY_SNAPSHOT"
printf 'Terminal\tMetaTrader 5 x64 build 5320 started for synthetic-broker\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG"
assert_failure terminal_update_pending "$READY_SNAPSHOT"

# Authorization must be a successful marker appended after the launch
# snapshot. Invalid/failed authorization and old success lines are rejected.
AUTH_SNAPSHOT="$TMP/auth.snapshot"
printf 'authorized on old-account\r\n' | iconv -f UTF-8 -t UTF-16LE > "$LOG_ROOT/auth.log"
snapshot_terminal_logs "$LOG_ROOT" "$AUTH_SNAPSHOT"
assert_failure log_has_new_authorized_marker "$AUTH_SNAPSHOT" "$LOG_ROOT/auth.log"
printf 'invalid account authorization failed\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
assert_failure log_has_new_authorized_marker "$AUTH_SNAPSHOT" "$LOG_ROOT/auth.log"
printf 'account authorized on broker\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
assert_success log_has_new_authorized_marker "$AUTH_SNAPSHOT" "$LOG_ROOT/auth.log"
printf 'authorization failed\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
assert_failure log_has_new_authorized_marker "$AUTH_SNAPSHOT" "$LOG_ROOT/auth.log"
printf 'account authorized on broker\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
printf 'authorization on broker failed\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
assert_failure log_has_new_authorized_marker "$AUTH_SNAPSHOT" "$LOG_ROOT/auth.log"
printf 'account authorized on broker\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
printf 'disconnected from broker\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
assert_failure log_has_new_authorized_marker "$AUTH_SNAPSHOT" "$LOG_ROOT/auth.log"
printf 'account authorized on broker\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
printf 'Startup successfully initialized from start config\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
assert_failure log_has_new_authorized_marker "$AUTH_SNAPSHOT" "$LOG_ROOT/auth.log"
printf 'account authorized on broker\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
printf 'Terminal\tMetaTrader 5 x64 build 5320 started for synthetic-broker\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
assert_failure log_has_new_authorized_marker "$AUTH_SNAPSHOT" "$LOG_ROOT/auth.log"
printf 'account authorized on broker\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
assert_success log_has_new_authorized_marker "$AUTH_SNAPSHOT" "$LOG_ROOT/auth.log"
printf 'LiveUpdate starting native updater\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$LOG_ROOT/auth.log"
assert_failure log_has_new_authorized_marker "$AUTH_SNAPSHOT" "$LOG_ROOT/auth.log"

# Exercise the actual authorization poll with a synthetic post-launch Journal
# line. The success marker alone cannot authorize a vanished/duplicate/updating
# terminal. A fake clock makes timeout behavior deterministic and fast.
(
    unset SECONDS
    SECONDS=0
    POLL_CASE=normal; POLL=0
    MT5_READY_TIMEOUT=3; MT5_UPDATE_TIMEOUT=3
    POLL_LOG_ROOT="$TMP/poll-logs"; mkdir -p "$POLL_LOG_ROOT"
    MT5_LOG_ROOT="$POLL_LOG_ROOT"
    POLL_SNAPSHOT="$TMP/poll.snapshot"
    snapshot_terminal_logs "$POLL_LOG_ROOT" "$POLL_SNAPSHOT"
    printf 'account authorized on synthetic-broker\r\n' | iconv -f UTF-8 -t UTF-16LE > "$POLL_LOG_ROOT/journal.log"
    normal_terminal_pids() {
        case "$POLL_CASE" in
            normal|normal-and-update) printf '401\n' ;;
            duplicate) printf '401\n402\n' ;;
        esac
    }
    update_terminal_pids() {
        case "$POLL_CASE" in
            updater|normal-and-update) printf '501\n' ;;
            duplicate-update) printf '501\n502\n' ;;
        esac
    }
    capture_terminal_process_state() {
        NORMAL_PIDS="$(normal_terminal_pids | paste -sd, -)"
        UPDATE_PIDS="$(update_terminal_pids | paste -sd, -)"
        NORMAL_COUNT="$(normal_terminal_pids | count_lines)"
        UPDATE_COUNT="$(update_terminal_pids | count_lines)"
    }
    sleep() { POLL=$((POLL + 1)); SECONDS=$((SECONDS + 1)); }
    assert_status() {
        local expected="$1" actual; shift
        if "$@"; then actual=0; else actual=$?; fi
        [ "$actual" -eq "$expected" ] || fail "expected status $expected, got $actual: $*"
    }
    assert_status 0 await_terminal_authorized "$POLL_SNAPSHOT"
    POLL_CASE=vanished; assert_status 11 await_terminal_authorized "$POLL_SNAPSHOT"
    POLL_CASE=duplicate; assert_status 13 await_terminal_authorized "$POLL_SNAPSHOT"
    POLL_CASE=updater; assert_status 10 await_terminal_authorized "$POLL_SNAPSHOT"
    POLL_CASE=duplicate-update; assert_status 12 await_terminal_authorized "$POLL_SNAPSHOT"
    POLL_CASE=normal-and-update; POLL=0
    assert_status 10 await_terminal_authorized "$POLL_SNAPSHOT"
    MT5_UPDATE_OBSERVED=1
    POLL_CASE=updater; POLL=0
    assert_status 15 await_single_update
    [ "$POLL" -eq 3 ] || fail 'mandatory update timeout was not bounded'
    POLL_CASE=vanished; assert_status 0 await_single_update
    POLL_CASE=normal; assert_status 20 await_single_update
    POLL_CASE=duplicate; assert_status 13 await_single_update
    POLL_CASE=normal-and-update; POLL=0
    assert_status 15 await_single_update
    [ "$POLL" -eq 3 ] || fail 'native update/restart overlap was not bounded'
    POLL_CASE=normal; POLL=0
    printf 'LiveUpdate entered update prompt\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$POLL_LOG_ROOT/journal.log"
    assert_status 1 await_terminal_authorized "$POLL_SNAPSHOT"
    [ "$POLL" -eq 3 ] || fail 'Journal-only update prompt bypassed authorization'
    printf 'LiveUpdate start synthetic/liveupdate/terminal64.exe /update\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$POLL_LOG_ROOT/journal.log"
    assert_status 10 await_terminal_ready "$POLL_SNAPSHOT"
    [ "$MT5_UPDATE_OBSERVED" -eq 0 ] || fail 'Journal-only launch was mistaken for an observed updater process'
    POLL_CASE=vanished; POLL=0
    assert_status 15 await_single_update "$POLL_SNAPSHOT"
    [ "$POLL" -eq 3 ] || fail 'Journal updater visibility gap triggered an immediate duplicate launch'
    POLL_CASE=normal
    printf 'Terminal MetaTrader 5 build 5321 started\r\n' | iconv -f UTF-8 -t UTF-16LE >> "$POLL_LOG_ROOT/journal.log"
    assert_status 20 await_single_update "$POLL_SNAPSHOT"
)

grep -q 'winepath -w' "$ROOT/mt5docker/start_server.sh" || fail 'config is not converted by winepath'
grep -q '/skipupdate' "$ROOT/mt5docker/start_server.sh" || fail 'skip-update switch missing'
if grep -Eq 'pkill.*(python|terminal64)' "$ROOT/mt5docker/start_server.sh"; then
    fail 'broad pkill returned'
fi
echo 'terminal lifecycle shell tests passed'
