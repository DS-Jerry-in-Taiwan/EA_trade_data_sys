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

add_process 101 'C:\Program Files\MetaTrader 5\terminal64.exe' /portable /skipupdate
assert_success exactly_one_normal_terminal

add_process 102 'C:\Program Files\MetaTrader 5\terminal64.exe' /update
assert_failure exactly_one_normal_terminal
[ "$(update_terminal_pids)" = 102 ] || fail 'update terminal was not classified'
# This is the same fail-closed predicate used by startup and healthcheck.
[ "$(update_terminal_pids | count_lines)" -gt 0 ] || fail 'LiveUpdate did not fail closed'

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

grep -q 'winepath -w' "$ROOT/mt5docker/start_server.sh" || fail 'config is not converted by winepath'
grep -q '/skipupdate' "$ROOT/mt5docker/start_server.sh" || fail 'skip-update switch missing'
if grep -Eq 'pkill.*(python|terminal64)' "$ROOT/mt5docker/start_server.sh"; then
    fail 'broad pkill returned'
fi
echo 'terminal lifecycle shell tests passed'
