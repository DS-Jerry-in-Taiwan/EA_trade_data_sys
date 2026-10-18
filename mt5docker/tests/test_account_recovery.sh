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
[ "$(stat -c %a "$backup/Config/accounts.dat")" = 600 ]
printf '%s\n' "$output" | grep -Fx "recovery_directory=$backup"
verification="$(bash "$ROOT/mt5docker/prepare_account_recovery.sh" --verify "$TMP/native" "$TMP/marker")"
printf '%s\n' "$verification" | grep -Fx 'native_account_state=native_present'
printf '%s\n' "$verification" | grep -Fx 'bootstrap_marker_valid=true'
mkdir -p "$TMP/empty-native"
if bash "$ROOT/mt5docker/prepare_account_recovery.sh" --verify "$TMP/empty-native" "$TMP/marker" > "$TMP/verification"; then
    echo 'FAIL: verification accepted marker without native state' >&2; exit 1
fi
grep -Fx 'native_account_state=inconsistent_marker_native_missing' "$TMP/verification"
grep -Fx 'metadata_reason=missing_or_inaccessible_store' "$TMP/verification"
if bash "$ROOT/mt5docker/prepare_account_recovery.sh" --verify "$TMP/native" "$TMP/missing-marker" >/dev/null; then
    echo 'FAIL: verification accepted missing marker' >&2; exit 1
fi
if PROC_ROOT="$TMP/proc" bash "$ROOT/mt5docker/prepare_account_recovery.sh" "$TMP/source.ini" "$TMP/native" "$TMP/backups" >/dev/null 2>&1; then
    echo 'FAIL: recovery did not require deliberate single-profile confirmation' >&2; exit 1
fi
mkdir -p "$TMP/proc/123"
printf 'terminal64.exe\0' > "$TMP/proc/123/cmdline"
if PROC_ROOT="$TMP/proc" bash "$ROOT/mt5docker/prepare_account_recovery.sh" --confirm-single-profile "$TMP/source.ini" "$TMP/native" "$TMP/backups" >/dev/null 2>&1; then
    echo 'FAIL: recovery accepted a running terminal' >&2; exit 1
fi
cmp "$TMP/native/Config/accounts.dat" "$backup/Config/accounts.dat"
echo 'account recovery tests passed'
for claim in recovery session restart; do
    printf '%s\n' "$verification" | grep -Fx "${claim}_certified=false"
done
mkdir -p "$TMP/empty-native/Config"
touch "$TMP/empty-native/Config/accounts.dat"
if bash "$ROOT/mt5docker/prepare_account_recovery.sh" --verify "$TMP/empty-native" "$TMP/marker" > "$TMP/verification"; then
    echo 'FAIL: verification accepted empty native state' >&2; exit 1
fi
grep -Fx 'metadata_present=false' "$TMP/verification"
grep -Fx 'metadata_reason=empty_store' "$TMP/verification"
bash "$ROOT/mt5docker/observe_account_recovery.sh" "$TMP/empty-native" "$TMP/marker" > "$TMP/observation"
grep -E '^sample=1 target=Config/accounts.dat exists=true size=0 inode=[0-9]+ mtime=[0-9]+$' "$TMP/observation"
grep -Fx 'sample=1 target=config/accounts.dat exists=false size=unknown inode=unknown mtime=unknown' "$TMP/observation"
mkdir -p "$TMP/transient/config"
printf 'opaque-transient-fixture\n' > "$TMP/transient/config/accounts.dat"
bash "$ROOT/mt5docker/prepare_account_recovery.sh" --verify "$TMP/transient" "$TMP/marker" > "$TMP/transient-verify"
grep -Fx 'restart_certified=false' "$TMP/transient-verify"
(sleep 0.3; rm "$TMP/transient/config/accounts.dat") &
remover=$!
bash "$ROOT/mt5docker/observe_account_recovery.sh" "$TMP/transient" "$TMP/marker" 2 1 > "$TMP/observation"
wait "$remover"
grep -E '^sample=1 target=config/accounts.dat exists=true size=[1-9][0-9]* inode=[0-9]+ mtime=[0-9]+$' "$TMP/observation"
grep -Fx 'sample=2 target=config/accounts.dat exists=false size=unknown inode=unknown mtime=unknown' "$TMP/observation"
if grep -Eq 'opaque|synthetic|certified=true' "$TMP/observation"; then
    echo 'FAIL: observer exposed contents or certified recovery' >&2; exit 1
fi
for bounds in '0 1' '3601 1' '2 0' '2 61' '3600 2' 'bad 1'; do
    read -r count delay <<< "$bounds"
    if bash "$ROOT/mt5docker/observe_account_recovery.sh" "$TMP/native" "$TMP/marker" "$count" "$delay" >/dev/null 2>&1; then
        echo 'FAIL: observer accepted invalid bounds' >&2; exit 1
    fi
done
echo 'metadata observation tests passed'
