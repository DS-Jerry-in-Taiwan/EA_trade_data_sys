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

clear_processes
add_process 201 'C:\Program Files\MetaTrader 5\terminal64.exe' /portable
add_process 202 'C:\Program Files\MetaTrader 5\terminal64.exe' /portable
assert_failure exactly_one_normal_terminal

clear_processes
add_process 301 /bin/bash terminal64.exe /update
[ "$(terminal_processes | count_lines)" -eq 0 ] || fail 'matched argument instead of executable'

MOCK_BIN="$TMP/bin"; mkdir -p "$MOCK_BIN"
cat > "$MOCK_BIN/ss" <<'EOF'
#!/usr/bin/env bash
printf 'LISTEN 0 5 0.0.0.0:8001 0.0.0.0:*\n'
EOF
chmod +x "$MOCK_BIN/ss"
PATH="$MOCK_BIN:$PATH" assert_success rpyc_is_listening

grep -q 'winepath -w' "$ROOT/mt5docker/start_server.sh" || fail 'config is not converted by winepath'
grep -q '/skipupdate' "$ROOT/mt5docker/start_server.sh" || fail 'skip-update switch missing'
if grep -Eq 'pkill.*(python|terminal64)' "$ROOT/mt5docker/start_server.sh"; then
    fail 'broad pkill returned'
fi
echo 'terminal lifecycle shell tests passed'
