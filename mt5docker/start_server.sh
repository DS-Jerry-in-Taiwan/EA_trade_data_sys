#!/usr/bin/env bash
set -Eeuo pipefail
export DISPLAY=:100
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
. "$SCRIPT_DIR/terminal_lifecycle.sh"
MT5_CONFIG_LINUX="${MT5_CONFIG_LINUX:-/mt5docker/mt5cfg.ini}"
MT5_READY_TIMEOUT="${MT5_READY_TIMEOUT:-120}"
MT5_LOG_ROOT="${MT5_LOG_ROOT:-/opt/wineprefix/drive_c/Program Files/MetaTrader 5/Logs}"
CHILD_PIDS=()
SHUTTING_DOWN=0
remember_child() { CHILD_PIDS+=("$1"); }
cleanup() {
    local terminal_pids=()
    [ "$SHUTTING_DOWN" -eq 0 ] || return
    SHUTTING_DOWN=1; trap - EXIT INT TERM
    mapfile -t terminal_pids < <(terminal_processes | cut -f1)
    stop_exact_pids 10 "${CHILD_PIDS[@]}" "${terminal_pids[@]}"
    rm -f /run/mt5-server/rpyc.pid "${launch_snapshot:-}"
}
trap cleanup EXIT INT TERM
cleanup_stale_runtime() {
    local stale=()
    mapfile -t stale < <(terminal_processes | cut -f1)
    stop_exact_pids 10 "${stale[@]}"
    rm -f /tmp/.X100-lock
}
find_mt5_exe() { find /opt/wineprefix/drive_c -type f -name terminal64.exe ! -path '*/Logs/*' -print -quit; }
snapshot_terminal_logs() {
    local snapshot="$1" log size
    : > "$snapshot"
    while IFS= read -r -d '' log; do
        size="$(stat -c %s "$log" 2>/dev/null || printf 0)"
        printf '%s\t%s\n' "$size" "$log" >> "$snapshot"
    done < <(find "$MT5_LOG_ROOT" -maxdepth 1 -type f -name '*.log' -print0 2>/dev/null)
}
log_baseline_size() {
    local snapshot="$1" log="$2" size path
    while IFS=$'\t' read -r size path; do
        [ "$path" = "$log" ] && { printf '%s\n' "$size"; return; }
    done < "$snapshot"
    printf '0\n'
}
log_has_new_ready_marker() {
    local snapshot="$1" log="$2" baseline current
    baseline="$(log_baseline_size "$snapshot" "$log")"
    current="$(stat -c %s "$log" 2>/dev/null || printf 0)"
    # Treat truncation as a new file. Keep UTF-16 reads aligned to code units.
    [ "$current" -ge "$baseline" ] || baseline=0
    baseline=$((baseline - (baseline % 2)))
    # Never echo logs: they may contain account metadata.
    if command -v iconv >/dev/null 2>&1; then
        tail -c "+$((baseline + 1))" "$log" 2>/dev/null | iconv -f UTF-16LE -t UTF-8 2>/dev/null | grep -Eiq '(^|[[:space:]])initialized([[:space:]]|$)'
    else
        tail -c "+$((baseline + 1))" "$log" 2>/dev/null | tr -d '\000' | grep -Eiq '(^|[[:space:]])initialized([[:space:]]|$)'
    fi
}
wait_for_terminal_ready() {
    local snapshot="$1" deadline log
    deadline=$((SECONDS + MT5_READY_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        exactly_one_normal_terminal || {
            [ "$(update_terminal_pids | count_lines)" -eq 0 ] || return 2
            sleep 1; continue
        }
        while IFS= read -r -d '' log; do
            log_has_new_ready_marker "$snapshot" "$log" && return 0
        done < <(find "$MT5_LOG_ROOT" -maxdepth 1 -type f -name '*.log' -print0 2>/dev/null)
        sleep 1
    done
    return 1
}

echo '>>> Cleaning up stale MT5 runtime...'
cleanup_stale_runtime
echo '>>> Starting GUI services...'
Xvfb :100 -ac -screen 0 1024x768x24 & remember_child "$!"
sleep 2
openbox & remember_child "$!"
x11vnc -display :100 -forever -shared -dontdisconnect -rfbport 5901 -rfbauth /root/.vnc/passwd & remember_child "$!"
websockify --web /usr/share/novnc 6081 localhost:5901 & remember_child "$!"

MT5_EXE="$(find_mt5_exe)"
[ -n "$MT5_EXE" ] || { echo '>>> MT5 executable is missing; refusing to start.' >&2; exit 1; }
bash /mt5docker/sync_mt5cfg.sh
[ -f "$MT5_CONFIG_LINUX" ] || { echo '>>> MT5 config was not generated.' >&2; exit 1; }
MT5_CONFIG_WINDOWS="$(winepath -w "$MT5_CONFIG_LINUX")"
[ -n "$MT5_CONFIG_WINDOWS" ] || { echo '>>> winepath could not resolve MT5 config.' >&2; exit 1; }

launch_snapshot="$(mktemp /tmp/mt5-launch.XXXXXX)"
snapshot_terminal_logs "$launch_snapshot"
echo '>>> Launching one MT5 terminal with LiveUpdate suppressed...'
# /skipupdate is the established MetaTrader startup switch. Readiness and
# health fail closed if an image/build ignores it and launches with /update.
wine "$MT5_EXE" /portable "/config:$MT5_CONFIG_WINDOWS" /skipupdate &
remember_child "$!"
if ! wait_for_terminal_ready "$launch_snapshot"; then
    echo '>>> MT5 did not become ready, exited, duplicated, or entered LiveUpdate.' >&2
    exit 1
fi
rm -f "$launch_snapshot"

echo '>>> Starting API Proxy after MT5 readiness...'
wine C:/Python/python.exe -m pymt5linux --host 0.0.0.0 --port 8001 C:/Python/python.exe &
RPYC_PID="$!"; remember_child "$RPYC_PID"
mkdir -p /run/mt5-server
printf '%s\n' "$RPYC_PID" > /run/mt5-server/rpyc.pid
while kill -0 "$RPYC_PID" 2>/dev/null; do
    exactly_one_normal_terminal || exit 1
    sleep 5
done
exit 1
