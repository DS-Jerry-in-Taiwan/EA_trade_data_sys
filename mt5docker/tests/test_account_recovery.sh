#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/proc" "$TMP/native/Config"
printf 'synthetic-private-profile\n' > "$TMP/source.ini"
printf 'opaque-native-fixture\n' > "$TMP/native/Config/accounts.dat"
printf 'mt5-bootstrap-complete-v1\n' > "$TMP/marker"
output="$(PROC_ROOT="$TMP/proc" MT5_BOOTSTRAP_MARKER="$TMP/marker" bash "$ROOT/mt5docker/prepare_account_recovery.sh" --confirm-single-profile "$TMP/source.ini" "$TMP/native" "$TMP/backups")"
if printf '%s' "$output" | grep -Eq 'synthetic-private|opaque-native'; then
    echo 'FAIL: recovery output exposed fixture contents' >&2; exit 1
fi
mapfile -t backups < <(find "$TMP/backups" -mindepth 1 -maxdepth 1 -type d)
[ "${#backups[@]}" -eq 1 ]
backup="${backups[0]}"
cmp "$TMP/source.ini" "$backup/selected-profile.ini"
cmp "$TMP/native/Config/accounts.dat" "$backup/Config/accounts.dat"
cmp "$TMP/marker" "$backup/bootstrap-marker.backup"
[ "$(stat -c %a "$backup")" = 700 ]
[ "$(stat -c %a "$backup/selected-profile.ini")" = 600 ]
if PROC_ROOT="$TMP/proc" bash "$ROOT/mt5docker/prepare_account_recovery.sh" "$TMP/source.ini" "$TMP/native" "$TMP/backups" >/dev/null 2>&1; then
    echo 'FAIL: recovery did not require deliberate single-profile confirmation' >&2; exit 1
fi
mkdir -p "$TMP/proc/123"
printf 'terminal64.exe\0' > "$TMP/proc/123/cmdline"
if PROC_ROOT="$TMP/proc" bash "$ROOT/mt5docker/prepare_account_recovery.sh" --confirm-single-profile "$TMP/source.ini" "$TMP/native" "$TMP/backups" >/dev/null 2>&1; then
    echo 'FAIL: recovery accepted a running terminal' >&2; exit 1
fi
echo 'account recovery tests passed'
