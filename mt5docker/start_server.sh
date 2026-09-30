#!/usr/bin/env bash
set -Eeuo pipefail
export DISPLAY=:100
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
. "$SCRIPT_DIR/terminal_lifecycle.sh"
. "$SCRIPT_DIR/terminal_config.sh"
MT5_CONNECTION_MODE="${MT5_CONNECTION_MODE:-terminal}"
MT5_CONFIG_LINUX="${MT5_CONFIG_LINUX:-/mt5docker/mt5cfg.ini}"
MT5_READY_TIMEOUT="${MT5_READY_TIMEOUT:-120}"
MT5_UPDATE_TIMEOUT="${MT5_UPDATE_TIMEOUT:-180}"
MT5_LOG_ROOT="${MT5_LOG_ROOT:-/mt5docker/MT5_Data/logs}"
MT5_READY_SNAPSHOT="${MT5_READY_SNAPSHOT:-/run/mt5-server/terminal-ready.snapshot}"
CHILD_PIDS=()
SHUTTING_DOWN=0
RUNTIME_CONFIG=""
remember_child() { CHILD_PIDS+=("$1"); }
cleanup() {
    local terminal_pids=()
    [ "$SHUTTING_DOWN" -eq 0 ] || return
    SHUTTING_DOWN=1; trap - EXIT INT TERM
    mapfile -t terminal_pids < <(terminal_processes | cut -f1)
    stop_exact_pids 10 "${CHILD_PIDS[@]}" "${terminal_pids[@]}"
    rm -f /run/mt5-server/rpyc.pid /run/mt5-server/terminal-ready \
        "${MT5_READY_SNAPSHOT:-/run/mt5-server/terminal-ready.snapshot}" \
        "${launch_snapshot:-}"
    [ -z "$RUNTIME_CONFIG" ] || rm -f "$RUNTIME_CONFIG"
}
trap cleanup EXIT INT TERM
cleanup_stale_runtime() {
    local stale=()
    mapfile -t stale < <(terminal_processes | cut -f1)
    stop_exact_pids 10 "${stale[@]}"
    rm -f /tmp/.X100-lock
}
find_mt5_exe() { find /opt/wineprefix/drive_c -type f -name terminal64.exe ! -path '*/Logs/*' -print -quit; }
launch_terminal() {
    wine "$MT5_EXE" /portable "/config:$MT5_CONFIG_WINDOWS" /skipupdate &
    remember_child "$!"
}
await_terminal_ready() {
    local snapshot="$1" deadline log normal_count update_count
    deadline=$((SECONDS + MT5_READY_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        normal_count="$(normal_terminal_pids | count_lines)"
        update_count="$(update_terminal_pids | count_lines)"
        [ "$update_count" -le 1 ] || return 12
        [ "$normal_count" -le 1 ] || return 13
        if [ "$update_count" -eq 1 ]; then
            [ "$normal_count" -eq 0 ] && return 10
            sleep 1; continue
        fi
        [ "$normal_count" -eq 1 ] || { sleep 1; continue; }
        while IFS= read -r -d '' log; do
            log_has_new_startup_marker "$snapshot" "$log" && return 0
        done < <(find "$MT5_LOG_ROOT" -maxdepth 1 -type f -name '*.log' -print0 2>/dev/null)
        sleep 1
    done
    return 1
}
await_single_update() {
    # await_terminal_ready only returns update status after observing exactly
    # one updater and zero normal terminals. Preserve that observation so an
    # updater that exits between the two probes is still treated as completed.
    local deadline normal_count update_count saw_updater=1
    deadline=$((SECONDS + MT5_UPDATE_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        normal_count="$(normal_terminal_pids | count_lines)"
        update_count="$(update_terminal_pids | count_lines)"
        [ "$update_count" -le 1 ] || return 12
        [ "$normal_count" -eq 0 ] || return 13
        [ "$update_count" -eq 1 ] && saw_updater=1
        if [ "$saw_updater" -eq 1 ] && [ "$update_count" -eq 0 ]; then return 0; fi
        sleep 1
    done
    return 15
}

echo '>>> Cleaning up stale MT5 runtime...'
cleanup_stale_runtime
validate_mt5_connection_mode "$MT5_CONNECTION_MODE" || exit 1
if [ "$MT5_CONNECTION_MODE" = terminal ] && [ "${MT5_SYNC_CONFIG:-0}" = 1 ]; then
    echo '>>> MT5_SYNC_CONFIG is only valid in managed mode; refusing fixed-account takeover.' >&2
    exit 1
fi
echo '>>> Starting GUI services...'
Xvfb :100 -ac -screen 0 1024x768x24 & remember_child "$!"
sleep 2
openbox & OPENBOX_PID="$!"; remember_child "$OPENBOX_PID"
pcmanfm --desktop --profile MT5 & PCMANFM_PID="$!"; remember_child "$PCMANFM_PID"
tint2 -c /root/.config/tint2/tint2rc & TINT2_PID="$!"; remember_child "$TINT2_PID"
# x11vnc is the sole clipboard bridge. Its default selection support handles
# both PRIMARY and CLIPBOARD updates from noVNC. A second clipboard manager can
# replay stale CUT_BUFFER0 content over newer browser clipboard events.
x11vnc -display :100 -forever -shared -dontdisconnect -rfbport 5901 -rfbauth /root/.vnc/passwd & remember_child "$!"
websockify --web /usr/share/novnc 6081 localhost:5901 & remember_child "$!"
sleep 1
for desktop_pid in "$OPENBOX_PID" "$PCMANFM_PID" "$TINT2_PID"; do
    kill -0 "$desktop_pid" 2>/dev/null || {
        echo '>>> Desktop shell failed to start; refusing partial noVNC service.' >&2
        exit 1
    }
done

MT5_EXE="$(find_mt5_exe)"
if [ -z "$MT5_EXE" ]; then
    echo '>>> MT5 executable is missing; refusing to start.' >&2
    echo '>>> Verify that MT5_DATA_DIR points to the persistent MT5_Data directory before running docker compose.' >&2
    exit 1
fi
if [ "$MT5_CONNECTION_MODE" = managed ] && [ "${MT5_SYNC_CONFIG:-0}" = 1 ]; then
    # Legacy opt-in only. Normal deployment consumes the exact read-only file
    # mounted by MT5_CONFIG_FILE and does not duplicate credentials.
    bash /mt5docker/sync_mt5cfg.sh
fi
[ -f "$MT5_CONFIG_LINUX" ] && [ -r "$MT5_CONFIG_LINUX" ] || {
    echo '>>> MT5 config is missing or unreadable; refusing to start.' >&2
    echo '>>> Set MT5_CONFIG_FILE to the existing generated mt5cfg.ini before running docker compose.' >&2
    exit 1
}
MT5_CONFIG_WINDOWS="$(winepath -w "$MT5_CONFIG_LINUX")"
[ -n "$MT5_CONFIG_WINDOWS" ] || { echo '>>> winepath could not resolve MT5 config.' >&2; exit 1; }
if [ "$MT5_CONNECTION_MODE" = terminal ]; then
    RUNTIME_CONFIG="$(mktemp /tmp/mt5cfg-terminal.XXXXXX)"
    MT5_CONFIG_LINUX="$(prepare_mt5_config terminal "$MT5_CONFIG_LINUX" "$RUNTIME_CONFIG")"
    MT5_CONFIG_WINDOWS="$(winepath -w "$MT5_CONFIG_LINUX")"
    [ -n "$MT5_CONFIG_WINDOWS" ] || { echo '>>> winepath could not resolve sanitized MT5 config.' >&2; exit 1; }
fi

launch_snapshot="$(mktemp /tmp/mt5-launch.XXXXXX)"
snapshot_terminal_logs "$MT5_LOG_ROOT" "$launch_snapshot"
echo '>>> Launching one MT5 terminal with LiveUpdate suppressed...'
# /skipupdate is the established MetaTrader startup switch. Readiness and
# health stay closed during one bounded mandatory update if the build ignores it.
if ! start_terminal_with_one_update_cycle "$launch_snapshot"; then
    echo '>>> MT5 readiness/update lifecycle failed or exceeded its bounded maintenance cycle.' >&2
    exit 1
fi
rm -f "$launch_snapshot"

# Health must have an explicit post-update baseline.  A normal terminal
# process by itself is insufficient: a LiveUpdate prompt can leave one
# terminal and an RPyC listener alive without completing authorization.
mkdir -p "$(dirname "$MT5_READY_SNAPSHOT")"
snapshot_terminal_logs "$MT5_LOG_ROOT" "$MT5_READY_SNAPSHOT"
printf '%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > /run/mt5-server/terminal-ready

echo '>>> Starting API Proxy after MT5 readiness...'
wine C:/Python/python.exe -m pymt5linux --host 0.0.0.0 --port 8001 C:/Python/python.exe &
RPYC_PID="$!"; remember_child "$RPYC_PID"
mkdir -p /run/mt5-server
printf '%s\n' "$RPYC_PID" > /run/mt5-server/rpyc.pid
while kill -0 "$RPYC_PID" 2>/dev/null; do
    exactly_one_normal_terminal || exit 1
    for desktop_pid in "$OPENBOX_PID" "$PCMANFM_PID" "$TINT2_PID"; do
        kill -0 "$desktop_pid" 2>/dev/null || exit 1
    done
    sleep 5
done
exit 1
