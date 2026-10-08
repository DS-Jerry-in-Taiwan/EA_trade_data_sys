#!/usr/bin/env bash
# Run inside the stopped-terminal environment; never prints private contents.
set -Eeuo pipefail
umask 077
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
. "$SCRIPT_DIR/terminal_lifecycle.sh"
if [ "$#" -ne 4 ] || [ "$1" != --confirm-single-profile ]; then
    echo 'Usage: prepare_account_recovery.sh --confirm-single-profile PRIVATE_INI PORTABLE_ROOT BACKUP_PARENT' >&2
    exit 2
fi
source_config="$2"; portable_root="$3"; backup_parent="$4"
[ -f "$source_config" ] && [ -r "$source_config" ] && [ -d "$portable_root" ] || {
    echo 'Recovery requires a readable private INI and an existing portable directory.' >&2; exit 1;
}
if [ -n "$(terminal_processes)" ]; then
    echo 'Stop the MT5 terminal before preparing recovery.' >&2; exit 1
fi
mkdir -p "$backup_parent"
recovery_dir="$(mktemp -d "$backup_parent/account-recovery.XXXXXX")"
chmod 700 "$recovery_dir"
for native_dir in Config config; do
    if [ -d "$portable_root/$native_dir" ]; then
        cp -a "$portable_root/$native_dir" "$recovery_dir/$native_dir"
    fi
done
marker="${MT5_BOOTSTRAP_MARKER:-$portable_root/.mt5-bootstrap-complete}"
if [ -f "$marker" ]; then
    cp -p "$marker" "$recovery_dir/bootstrap-marker.backup"
fi
cp "$source_config" "$recovery_dir/selected-profile.ini"
chmod 600 "$recovery_dir/selected-profile.ini"
echo 'Native settings backup and single-profile recovery config prepared.'
echo 'Use the selected-profile.ini in the newly created recovery directory as MT5_CONFIG_FILE, with MT5_BOOTSTRAP_REIMPORT=1 for one controlled restart.'
echo 'Then restore MT5_BOOTSTRAP_REIMPORT=0. This imports one selected profile; it does not restore every account or change the active session until restart.'
echo 'Recovery leaves the original native files untouched. Restore the backed-up native directory only while the terminal is stopped.'
