#!/usr/bin/env bash
PROC_ROOT="${PROC_ROOT:-/proc}"

read_process_exe() { IFS= read -r -d '' REPLY < "$1"; }
read_process_cmdline() { tr '\0' ' ' < "$1"; }

terminal_processes() {
    local proc cmdline exe pid
    for proc in "$PROC_ROOT"/[0-9]*; do
        [ -r "$proc/cmdline" ] || continue
        pid="${proc##*/}"
        exe=''
        REPLY=''
        read_process_exe "$proc/cmdline" 2>/dev/null || [ -n "$REPLY" ] || continue
        exe="$REPLY"
        cmdline="$(read_process_cmdline "$proc/cmdline" 2>/dev/null)" || continue
        exe="${exe##*\\}"
        exe="${exe##*/}"
        case "$exe" in terminal64.exe) printf '%s\t%s\n' "$pid" "$cmdline" ;; esac
    done
}
terminal_is_update_command() { printf '%s\n' "$1" | grep -Eiq '(^|[[:space:]])[-/]update([[:space:]]|$)'; }
normal_terminal_pids() {
    local pid cmdline
    while IFS=$'\t' read -r pid cmdline; do
        [ -n "$pid" ] || continue
        terminal_is_update_command "$cmdline" || printf '%s\n' "$pid"
    done < <(terminal_processes)
}
update_terminal_pids() {
    local pid cmdline
    while IFS=$'\t' read -r pid cmdline; do
        [ -n "$pid" ] || continue
        terminal_is_update_command "$cmdline" && printf '%s\n' "$pid"
    done < <(terminal_processes)
}
count_lines() { awk 'NF { count++ } END { print count + 0 }'; }
exactly_one_normal_terminal() {
    [ "$(normal_terminal_pids | count_lines)" -eq 1 ] && [ "$(update_terminal_pids | count_lines)" -eq 0 ]
}
signal_pids() {
    local signal="$1" pid; shift
    for pid in "$@"; do
        case "$pid" in (*[!0-9]*|'') continue ;; esac
        kill "-$signal" "$pid" 2>/dev/null || true
    done
}
wait_for_pids_exit() {
    local timeout="$1" deadline pid alive; shift
    deadline=$((SECONDS + timeout))
    while [ "$SECONDS" -lt "$deadline" ]; do
        alive=0
        for pid in "$@"; do kill -0 "$pid" 2>/dev/null && alive=1; done
        [ "$alive" -eq 0 ] && return 0
        sleep 1
    done
    return 1
}
stop_exact_pids() {
    local timeout="$1"; shift
    [ "$#" -gt 0 ] || return 0
    signal_pids TERM "$@"
    wait_for_pids_exit "$timeout" "$@" || signal_pids KILL "$@"
}
rpyc_is_listening() {
    if command -v ss >/dev/null 2>&1; then
        ss -H -ltn 2>/dev/null | awk '$4 ~ /:8001$/ { found=1 } END { exit !found }'
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ltn 2>/dev/null | awk '$4 ~ /:8001$/ && $6 == "LISTEN" { found=1 } END { exit !found }'
    else return 1; fi
}

snapshot_terminal_logs() {
    local log_root="$1" snapshot="$2" log size
    : > "$snapshot"
    while IFS= read -r -d '' log; do
        size="$(stat -c %s "$log" 2>/dev/null || printf 0)"
        printf '%s\t%s\n' "$size" "$log" >> "$snapshot"
    done < <(find "$log_root" -maxdepth 1 -type f -name '*.log' -print0 2>/dev/null)
}

log_baseline_size() {
    local snapshot="$1" log="$2" size path
    while IFS=$'\t' read -r size path; do
        [ "$path" = "$log" ] && { printf '%s\n' "$size"; return; }
    done < "$snapshot"
    printf '0\n'
}

log_has_new_startup_marker() {
    local snapshot="$1" log="$2" baseline current
    baseline="$(log_baseline_size "$snapshot" "$log")"
    current="$(stat -c %s "$log" 2>/dev/null || printf 0)"
    [ "$current" -ge "$baseline" ] || baseline=0
    baseline=$((baseline - (baseline % 2)))
    # Only inspect bytes appended after launch and never echo account logs.
    if command -v iconv >/dev/null 2>&1; then
        tail -c "+$((baseline + 1))" "$log" 2>/dev/null | iconv -f UTF-16LE -t UTF-8 2>/dev/null | grep -Eiq '(^|[[:space:]])Startup[[:space:]]+successfully[[:space:]]+initialized[[:space:]]+from[[:space:]]+start[[:space:]]+config([[:space:]]|$)'
    else
        tail -c "+$((baseline + 1))" "$log" 2>/dev/null | tr -d '\000' | grep -Eiq '(^|[[:space:]])Startup[[:space:]]+successfully[[:space:]]+initialized[[:space:]]+from[[:space:]]+start[[:space:]]+config([[:space:]]|$)'
    fi
}

start_terminal_with_one_update_cycle() {
    local snapshot="$1" status
    launch_terminal
    if await_terminal_ready "$snapshot"; then return 0; else status=$?; fi
    [ "$status" -eq 10 ] || return "$status"

    # Mandatory MetaTrader updates are permitted once per container start.
    # RPyC has not started yet, so health remains strictly unavailable.
    await_single_update || return $?
    snapshot_terminal_logs "$MT5_LOG_ROOT" "$snapshot"
    launch_terminal
    if await_terminal_ready "$snapshot"; then return 0; else status=$?; fi
    # A second update request is a loop, not another maintenance cycle.
    [ "$status" -ne 10 ] || return 14
    return "$status"
}
