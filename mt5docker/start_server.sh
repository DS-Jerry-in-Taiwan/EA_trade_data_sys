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
MT5_AUTH_MAINTENANCE_TIMEOUT="${MT5_AUTH_MAINTENANCE_TIMEOUT:-900}"
MT5_PORTABLE_ROOT="${MT5_PORTABLE_ROOT:-/opt/wineprefix/drive_c/users/root/AppData/Roaming/MetaTrader 5}"
MT5_LOG_ROOT="${MT5_LOG_ROOT:-/mt5docker/MT5_Data/logs}"
MT5_READY_SNAPSHOT="${MT5_READY_SNAPSHOT:-/run/mt5-server/terminal-ready.snapshot}"
MT5_BOOTSTRAP_MARKER="${MT5_BOOTSTRAP_MARKER:-/mt5docker/MT5_Data/.mt5-bootstrap-complete}"
MT5_BOOTSTRAP_REIMPORT="${MT5_BOOTSTRAP_REIMPORT:-0}"
for timeout in "$MT5_READY_TIMEOUT" "$MT5_UPDATE_TIMEOUT" "$MT5_AUTH_MAINTENANCE_TIMEOUT"; do
    case "$timeout" in
        ''|*[!0-9]*|0|0*)
            echo '>>> MT5 lifecycle timeouts must be positive integer seconds.' >&2
            exit 1
            ;;
    esac
done
CHILD_PIDS=()
SHUTTING_DOWN=0
BOOTSTRAP_ACTIVE=0
TERMINAL_LAUNCHES=0
MT5_CONFIG_WINDOWS=""
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
}
trap cleanup EXIT INT TERM
cleanup_stale_runtime() {
    local stale=()
    mapfile -t stale < <(terminal_processes | cut -f1)
    stop_exact_pids 10 "${stale[@]}"
    rm -f /tmp/.X100-lock
}
launch_terminal() {
    local import_config=0
    if [ "$BOOTSTRAP_ACTIVE" -eq 1 ] && [ "$TERMINAL_LAUNCHES" -eq 0 ]; then import_config=1; fi
    set_mt5_terminal_launch_arguments "$MT5_CONNECTION_MODE" "$import_config" "$MT5_CONFIG_WINDOWS" || return 1
    wine "$MT5_EXE" "${MT5_TERMINAL_ARGS[@]}" &
    remember_child "$!"
    TERMINAL_LAUNCHES=$((TERMINAL_LAUNCHES + 1))
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

if ! MT5_EXE="$(find_mt5_exe "$MT5_PORTABLE_ROOT")"; then
    echo '>>> MT5 executable is missing; refusing to start.' >&2
    echo '>>> Verify that MT5_DATA_DIR points to the persistent MT5_Data directory before running docker compose.' >&2
    exit 1
fi
if [ "$MT5_CONNECTION_MODE" = managed ] && [ "${MT5_SYNC_CONFIG:-0}" = 1 ]; then
    # Legacy opt-in only. Normal deployment consumes the exact read-only file
    # mounted by MT5_CONFIG_FILE and does not duplicate credentials.
    bash /mt5docker/sync_mt5cfg.sh
fi
if [ "$MT5_CONNECTION_MODE" = terminal ]; then
    if bootstrap_config_required "$MT5_BOOTSTRAP_MARKER" "$MT5_BOOTSTRAP_REIMPORT" "$MT5_PORTABLE_ROOT"; then
        # The private mounted config is used only for this one bootstrap (or
        # an explicit re-import). Never print its contents or credentials.
        echo '>>> Importing private MT5 bootstrap config for account setup.'
        BOOTSTRAP_ACTIVE=1
    else
        echo '>>> Restoring the saved GUI-selected MT5 session without a startup config.'
    fi
fi
if [ "$MT5_CONNECTION_MODE" = managed ] || [ "$BOOTSTRAP_ACTIVE" -eq 1 ]; then
    [ -f "$MT5_CONFIG_LINUX" ] && [ -r "$MT5_CONFIG_LINUX" ] || {
        echo '>>> MT5 config is missing or unreadable; refusing bootstrap/managed startup.' >&2
        echo '>>> Set MT5_CONFIG_FILE to the existing generated mt5cfg.ini before running docker compose.' >&2
        exit 1
    }
    MT5_CONFIG_WINDOWS="$(winepath -w "$MT5_CONFIG_LINUX")"
    [ -n "$MT5_CONFIG_WINDOWS" ] || { echo '>>> winepath could not resolve MT5 config.' >&2; exit 1; }
fi

launch_snapshot="$(mktemp /tmp/mt5-launch.XXXXXX)"
snapshot_terminal_logs "$MT5_LOG_ROOT" "$launch_snapshot"
echo '>>> Launching one MT5 terminal with LiveUpdate suppressed...'
# /skipupdate is the established MetaTrader startup switch. Readiness and
# health stay closed during one bounded mandatory update if the build ignores it.
if start_terminal_with_one_update_cycle "$launch_snapshot"; then
    :
else
    lifecycle_status=$?
    if [ "$lifecycle_status" -eq 21 ]; then
        echo ">>> MT5 Journal authorization timed out; keeping the GUI available for ${MT5_AUTH_MAINTENANCE_TIMEOUT}s of manual login. Readiness remains closed."
        if hold_for_terminal_authorization "$launch_snapshot"; then lifecycle_status=0; else lifecycle_status=$?; fi
    fi
    if [ "$lifecycle_status" -ne 0 ]; then
        case "$lifecycle_status" in
            1) echo '>>> MT5 startup timed out; readiness remains closed.' >&2 ;;
            10) echo '>>> MT5 entered an update during authorization maintenance; readiness remains closed.' >&2 ;;
            11) echo '>>> MT5 terminal disappeared before authorization; readiness remains closed.' >&2 ;;
            12|13) echo '>>> MT5 lifecycle found conflicting terminal/update processes; readiness remains closed.' >&2 ;;
            14|15) echo '>>> MT5 mandatory update exceeded its one bounded maintenance cycle; readiness remains closed.' >&2 ;;
            22) echo '>>> MT5 manual authorization maintenance window expired; readiness remains closed.' >&2 ;;
            *) echo '>>> MT5 readiness/update lifecycle failed; readiness remains closed.' >&2 ;;
        esac
        exit 1
    fi
fi

rm -f "$launch_snapshot"

# Health must have an explicit post-update baseline.  A normal terminal
# process by itself is insufficient: a LiveUpdate prompt can leave one
# terminal and an RPyC listener alive without completing authorization.
mkdir -p "$(dirname "$MT5_READY_SNAPSHOT")"
snapshot_terminal_logs "$MT5_LOG_ROOT" "$MT5_READY_SNAPSHOT"

if [ "$BOOTSTRAP_ACTIVE" -eq 1 ]; then
    if ! write_bootstrap_marker "$MT5_BOOTSTRAP_MARKER"; then
        echo '>>> MT5 bootstrap completed but its persistent marker could not be written; refusing startup.' >&2
        exit 1
    fi
fi

# This marker is written only after terminal startup and Journal authorization;
# health cannot report success for a login or LiveUpdate prompt.
mkdir -p /run/mt5-server
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
