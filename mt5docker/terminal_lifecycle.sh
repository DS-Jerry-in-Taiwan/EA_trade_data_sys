#!/usr/bin/env bash
PROC_ROOT="${PROC_ROOT:-/proc}"
MT5_STARTUP_PATTERN='(^|[[:space:]])Startup[[:space:]]+successfully[[:space:]]+initialized[[:space:]]+from[[:space:]]+start[[:space:]]+config([[:space:]]|$)|(^|[[:space:]])Terminal[[:space:]]+.*build[[:space:]]+[0-9]+[[:space:]]+started([[:space:]]|$)'

read_process_exe() { IFS= read -r -d '' REPLY < "$1"; }
read_process_cmdline() { tr '\0' ' ' < "$1"; }

find_mt5_exe() {
    local portable_root="${1:-${MT5_PORTABLE_ROOT:-/opt/wineprefix/drive_c/users/root/AppData/Roaming/MetaTrader 5}}"
    # /portable reads its saved account beside the executable. Searching all
    # of drive_c can choose a different installation or liveupdate payload.
    # Only the root executable in the persistent portable tree is eligible.
    [ -f "$portable_root/terminal64.exe" ] && [ -r "$portable_root/terminal64.exe" ] || return 1
    printf '%s\n' "$portable_root/terminal64.exe"
}

terminal_processes() {
    local proc cmdline exe pid comm
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
        comm=''
        [ ! -r "$proc/comm" ] || IFS= read -r comm < "$proc/comm" || true
        # Wine can replace argv[0] with a combined command string. Its
        # kernel comm still identifies the Windows executable in that case.
        if [ "$exe" = terminal64.exe ] || [ "$comm" = terminal64.exe ]; then
            printf '%s\t%s\n' "$pid" "$cmdline"
        fi
    done
}
terminal_is_update_command() {
    printf '%s\n' "$1" | grep -Ei '(^|[[:space:]"])[-/]update([[:space:]"]|$)|[/\\]liveupdate[/\\]terminal64\.exe([[:space:]"]|$)' >/dev/null
}
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
capture_terminal_process_state() {
    local pid cmdline
    NORMAL_COUNT=0; UPDATE_COUNT=0; NORMAL_PIDS=''; UPDATE_PIDS=''
    # Classify both categories from one /proc scan, so a native handoff is
    # not counted as two different states between separate scans.
    while IFS=$'\t' read -r pid cmdline; do
        [ -n "$pid" ] || continue
        if terminal_is_update_command "$cmdline"; then
            UPDATE_COUNT=$((UPDATE_COUNT + 1)); UPDATE_PIDS+="${UPDATE_PIDS:+,}$pid"
        else
            NORMAL_COUNT=$((NORMAL_COUNT + 1)); NORMAL_PIDS+="${NORMAL_PIDS:+,}$pid"
        fi
    done < <(terminal_processes)
}

log_terminal_lifecycle_state() {
    local stage="$1" journal_update="${2:-0}" signature
    signature="$stage:$NORMAL_PIDS:$UPDATE_PIDS:$journal_update"
    if [ "$signature" != "${MT5_LAST_LIFECYCLE_STATE:-}" ] ||
        [ "$((SECONDS - ${MT5_LAST_LIFECYCLE_LOG:-0}))" -ge 15 ]; then
        # Only bounded stage labels, numeric PIDs, and booleans are emitted.
        # Never include a process command line or Journal/config contents.
        printf '>>> MT5 lifecycle stage=%s elapsed=%ss normal_pids=[%s] update_pids=[%s] journal_update_launch=%s\n' \
            "$stage" "$SECONDS" "$NORMAL_PIDS" "$UPDATE_PIDS" "$journal_update"
        MT5_LAST_LIFECYCLE_STATE="$signature"; MT5_LAST_LIFECYCLE_LOG="$SECONDS"
    fi
}
exactly_one_normal_terminal() {
    capture_terminal_process_state
    [ "$NORMAL_COUNT" -eq 1 ] && [ "$UPDATE_COUNT" -eq 0 ]
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

await_terminal_ready() {
    local snapshot="$1" deadline log journal_update
    deadline=$((SECONDS + MT5_READY_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        capture_terminal_process_state
        journal_update=0
        terminal_update_launch_pending "$snapshot" && journal_update=1
        log_terminal_lifecycle_state startup "$journal_update"
        [ "$UPDATE_COUNT" -le 1 ] || return 12
        [ "$NORMAL_COUNT" -le 1 ] || return 13
        MT5_UPDATE_OBSERVED=0
        [ "$UPDATE_COUNT" -eq 0 ] || { MT5_UPDATE_OBSERVED=1; return 10; }
        [ "$journal_update" -eq 0 ] || return 10
        [ "$NORMAL_COUNT" -eq 1 ] || { sleep 1; continue; }
        while IFS= read -r -d '' log; do
            log_has_new_startup_marker "$snapshot" "$log" && return 0
        done < <(changed_terminal_logs "$snapshot")
        sleep 1
    done
    return 1
}

await_single_update() {
    # Readiness observed one updater, possibly overlapping its replacement
    # normal terminal. Its exit between probes still completes the cycle.
    local snapshot="${1:-}" deadline saw_updater="${MT5_UPDATE_OBSERVED:-1}" journal_update
    deadline=$((SECONDS + MT5_UPDATE_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        capture_terminal_process_state
        journal_update=0
        [ -z "$snapshot" ] || { terminal_update_launch_pending "$snapshot" && journal_update=1; }
        log_terminal_lifecycle_state update "$journal_update"
        [ "$UPDATE_COUNT" -le 1 ] || return 12
        [ "$NORMAL_COUNT" -le 1 ] || return 13
        [ "$UPDATE_COUNT" -eq 0 ] || saw_updater=1
        if [ "$UPDATE_COUNT" -eq 0 ] && [ "$saw_updater" -eq 1 ]; then
            # Native LiveUpdate can restart the normal terminal itself. Its
            # new Startup+authorization still need verification, but launching
            # a second terminal here would create a duplicate.
            [ "$NORMAL_COUNT" -eq 0 ] && return 0
            return 20
        fi
        # Journal start can precede /proc visibility. Until an updater was
        # seen, only a later normal Startup can confirm native completion.
        if [ "$saw_updater" -eq 0 ] && [ "$NORMAL_COUNT" -eq 1 ] &&
            [ "$journal_update" -eq 0 ]; then return 20; fi
        sleep 1
    done
    return 15
}

snapshot_terminal_logs() {
    local log_root="$1" snapshot="$2" log size
    : > "$snapshot"
    while IFS= read -r -d '' log; do
        size="$(stat -c %s "$log" 2>/dev/null || printf 0)"
        printf '%s\t%s\n' "$size" "$log" >> "$snapshot"
    done < <(find "$log_root" -maxdepth 1 -type f -name '*.log' -print0 2>/dev/null)
}

changed_terminal_logs() {
    local snapshot="$1" log_root="${MT5_LOG_ROOT:-/mt5docker/MT5_Data/logs}" threshold
    # Include same-timestamp writes (filesystem precision varies). Baseline
    # byte counts still ensure unchanged files cannot contribute stale lines.
    threshold="$(stat -c %Y "$snapshot" 2>/dev/null)" || return 1
    threshold=$((threshold - 1))
    find "$log_root" -maxdepth 1 -type f -name '*.log' -newermt "@$threshold" -print0 2>/dev/null
}

log_baseline_size() {
    local snapshot="$1" log="$2" size path
    while IFS=$'\t' read -r size path; do
        [ "$path" = "$log" ] && { printf '%s\n' "$size"; return; }
    done < "$snapshot"
    printf '0\n'
}

log_has_new_startup_marker() {
    # Consume the entire decoded tail. grep -q can close its input early,
    # making iconv fail with SIGPIPE under the startup script's pipefail.
    log_tail_utf8 "$1" "$2" | grep -Ei "$MT5_STARTUP_PATTERN" >/dev/null
}

log_tail_utf8() {
    local snapshot="$1" log="$2" baseline current
    baseline="$(log_baseline_size "$snapshot" "$log")"
    current="$(stat -c %s "$log" 2>/dev/null || printf 0)"
    [ "$current" -ne "$baseline" ] || return 0
    # A truncated/replaced log is a new stream.  Do not replay the old
    # stream, because an old LiveUpdate line must not permanently poison a
    # later confirmed normal startup.
    [ "$current" -ge "$baseline" ] || baseline=0
    baseline=$((baseline - (baseline % 2)))
    if command -v iconv >/dev/null 2>&1; then
        tail -c "+$((baseline + 1))" "$log" 2>/dev/null |
            iconv -f UTF-16LE -t UTF-8 2>/dev/null
    else
        tail -c "+$((baseline + 1))" "$log" 2>/dev/null | tr -d '\000'
    fi
}

terminal_update_pending() {
    local snapshot="$1" log text update_line startup_line
    [ -r "$snapshot" ] || return 0
    [ "$(update_terminal_pids | count_lines)" -eq 0 ] || return 0
    while IFS= read -r -d '' log; do
        text="$(log_tail_utf8 "$snapshot" "$log")"
        update_line="$(printf '%s\n' "$text" | grep -Ein 'live[[:space:]]*update|update[[:space:]]+(required|failed|in[[:space:]]+progress)|updat(e|ing)[[:space:]].*terminal' | tail -1 | cut -d: -f1 || true)"
        [ -n "$update_line" ] || continue
        startup_line="$(printf '%s\n' "$text" | grep -Ein "$MT5_STARTUP_PATTERN" | tail -1 | cut -d: -f1 || true)"
        [ -n "$startup_line" ] && [ "$startup_line" -gt "$update_line" ] && continue
        return 0
    done < <(changed_terminal_logs "$snapshot")
    return 1
}

terminal_update_launch_pending() {
    local snapshot="$1" log text launch_line startup_line
    [ -r "$snapshot" ] || return 1
    while IFS= read -r -d '' log; do
        text="$(log_tail_utf8 "$snapshot" "$log")"
        launch_line="$(printf '%s\n' "$text" | grep -Ein '(^|[[:space:]])LiveUpdate[[:space:]]+start[[:space:]]+.*terminal64\.exe.*[-/]update([[:space:]\"]|$)' | tail -1 | cut -d: -f1 || true)"
        [ -n "$launch_line" ] || continue
        startup_line="$(printf '%s\n' "$text" | grep -Ein "$MT5_STARTUP_PATTERN" | tail -1 | cut -d: -f1 || true)"
        [ -n "$startup_line" ] && [ "$startup_line" -gt "$launch_line" ] && continue
        return 0
    done < <(changed_terminal_logs "$snapshot")
    return 1
}

log_has_new_authorized_marker() {
    local snapshot="$1" log="$2" text success_line failure_line
    text="$(log_tail_utf8 "$snapshot" "$log")"
    success_line="$(printf '%s\n' "$text" | grep -Ein '(^|[[:space:]])authorized[[:space:]]+on([[:space:]]|$)|authorization[[:space:]]+(succeeded|successful)' | tail -1 | cut -d: -f1 || true)"
    [ -n "$success_line" ] || return 1
    failure_line="$(printf '%s\n' "$text" | grep -Ein "authorization[[:space:]]+.*(failed|denied|invalid)|invalid[[:space:]]+account|not[[:space:]]+authorized|disconnected[[:space:]]+from|connection[[:space:]]+(lost|closed)|live[[:space:]]*update|$MT5_STARTUP_PATTERN" | tail -1 | cut -d: -f1 || true)"
    [ -z "$failure_line" ] || [ "$success_line" -gt "$failure_line" ]
}

await_terminal_authorized() {
    local snapshot="$1" deadline log journal_update
    deadline=$((SECONDS + MT5_READY_TIMEOUT))
    while [ "$SECONDS" -lt "$deadline" ]; do
        capture_terminal_process_state
        journal_update=0
        terminal_update_launch_pending "$snapshot" && journal_update=1
        log_terminal_lifecycle_state authorization "$journal_update"
        [ "$UPDATE_COUNT" -le 1 ] || return 12
        [ "$NORMAL_COUNT" -le 1 ] || return 13
        MT5_UPDATE_OBSERVED=0
        [ "$UPDATE_COUNT" -eq 0 ] || { MT5_UPDATE_OBSERVED=1; return 10; }
        [ "$journal_update" -eq 0 ] || return 10
        [ "$NORMAL_COUNT" -eq 1 ] || return 11
        # A Journal-only update prompt can leave a normal terminal process
        # alive. Do not erase that pending state with a fresh health snapshot.
        if terminal_update_pending "$snapshot"; then sleep 1; continue; fi
        while IFS= read -r -d '' log; do
            if log_has_new_authorized_marker "$snapshot" "$log"; then
                if exactly_one_normal_terminal; then
                    log_terminal_lifecycle_state authorized
                    return 0
                fi
                [ "$UPDATE_COUNT" -eq 0 ] || { MT5_UPDATE_OBSERVED=1; return 10; }
                return 11
            fi
        done < <(changed_terminal_logs "$snapshot")
        sleep 1
    done
    return 1
}

start_terminal_with_one_update_cycle() {
    local snapshot="$1" status update_cycles=0 launch_required=1
    while :; do
        if [ "$launch_required" -eq 1 ]; then launch_terminal || return 11; fi
        if await_terminal_ready "$snapshot"; then
            # An updater may start after the initial Startup line. Keep the
            # same bounded recovery budget through Journal authorization.
            if await_terminal_authorized "$snapshot"; then return 0; else status=$?; fi
        else
            status=$?
        fi
        [ "$status" -eq 10 ] || return "$status"
        [ "$update_cycles" -eq 0 ] || return 14
        update_cycles=$((update_cycles + 1))

        # RPyC has not started yet, so health remains unavailable.
        if await_single_update "$snapshot"; then
            snapshot_terminal_logs "$MT5_LOG_ROOT" "$snapshot"
            launch_required=1
        else
            status=$?
            [ "$status" -eq 20 ] || return "$status"
            # Preserve the original baseline: the native restart may already
            # have written its Startup/auth lines before this poll sees it.
            # Latest Startup/LiveUpdate ordering rejects pre-update auth.
            launch_required=0
        fi
    done
}
