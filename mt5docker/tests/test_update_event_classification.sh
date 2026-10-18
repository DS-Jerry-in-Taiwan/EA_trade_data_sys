#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
export PROC_ROOT="$TMP/proc" MT5_LOG_ROOT="$TMP/logs"
mkdir -p "$PROC_ROOT" "$MT5_LOG_ROOT"
. "$ROOT/mt5docker/terminal_lifecycle.sh"
LOG="$MT5_LOG_ROOT/20261009.log"
SNAPSHOT="$TMP/baseline"
fail() { echo "FAIL: $*" >&2; exit 1; }
append() { printf '%s\r\n' "$1" | iconv -f UTF-8 -t UTF-16LE >> "$LOG"; }
reset_journal() {
    : > "$LOG"
    snapshot_terminal_logs "$MT5_LOG_ROOT" "$SNAPSHOT"
    append 'Terminal MetaTrader5 build 5320 started'
    append 'account authorized on synthetic-source'
}
for passive in \
    'LiveUpdate new version 6230 is available' \
    'LiveUpdate start downloading new version' \
    'LiveUpdate downloading update payload' \
    'LiveUpdate download completed successfully' \
    'LiveUpdate checking for updates' \
    'LiveUpdate no new updates available'; do
    reset_journal
    append "$passive"
    if terminal_update_pending "$SNAPSHOT"; then fail 'passive event blocks health'; fi
    terminal_journal_authorized "$SNAPSHOT" || fail 'passive event revokes authorization'
    log_has_new_authorized_marker "$SNAPSHOT" "$LOG" || fail 'passive event revokes per-log authorization'
done
for blocking in \
    'LiveUpdate update required' \
    'LiveUpdate installing downloaded update' \
    'LiveUpdate download completed; restart required' \
    'LiveUpdate start synthetic/liveupdate/terminal64.exe /update' \
    'LiveUpdate starting terminal64.exe after download' \
    'LiveUpdate entered update prompt' \
    'LiveUpdate download failed' \
    'LiveUpdate unknown phase' \
    'update in progress' \
    'updating current terminal'; do
    reset_journal
    append "$blocking"
    terminal_update_pending "$SNAPSHOT" || fail 'blocking event accepted'
    if terminal_journal_authorized "$SNAPSHOT"; then fail 'blocking event leaves authorization valid'; fi
    if log_has_new_authorized_marker "$SNAPSHOT" "$LOG"; then fail 'blocking event leaves per-log authorization valid'; fi
    append 'LiveUpdate download completed successfully'
    terminal_update_pending "$SNAPSHOT" || fail 'passive completion erased blocking event'
    append 'account authorized on synthetic-source'
    terminal_update_pending "$SNAPSHOT" || fail 'authorization erased blocking event'
    append 'Terminal MetaTrader5 build 6230 started'
    if terminal_update_pending "$SNAPSHOT"; then fail 'normal startup did not clear old blocking event'; fi
done
reset_journal
mkdir -p "$PROC_ROOT/999"
printf '%s\0' terminal64.exe /update > "$PROC_ROOT/999/cmdline"
append 'LiveUpdate download completed successfully'
terminal_update_pending "$SNAPSHOT" || fail 'updater process accepted'
terminal_update_pending "$TMP/missing.snapshot" || fail 'missing snapshot accepted'
echo 'update event classification tests passed (passive, blocking, ordering, updater, missing snapshot)'
