#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
PROFILE_ENV="${MT5_PROFILE_ENV:-${HOME}/.config/ea-trading/active-profile.env}"
if [ ! -r "$PROFILE_ENV" ]; then
    echo 'profile_acceptance=blocked reason=active_profile_missing'
    exit 2
fi
PROFILE_LINES="$(python3 "$SCRIPT_DIR/profile_selector.py" --read-env "$PROFILE_ENV")"
profile_id="$(printf '%s\n' "$PROFILE_LINES" | awk -F '\t' '$1=="MT5_PROFILE_ID" {print $2}')"
fingerprint="$(printf '%s\n' "$PROFILE_LINES" | awk -F '\t' '$1=="MT5_PROFILE_FINGERPRINT" {print $2}')"
config="$(printf '%s\n' "$PROFILE_LINES" | awk -F '\t' '$1=="MT5_CONFIG_FILE" {print $2}')"
data="$(printf '%s\n' "$PROFILE_LINES" | awk -F '\t' '$1=="MT5_DATA_DIR" {print $2}')"
printf 'profile_acceptance=pass\nprofile_id=%s\nfingerprint=%s\nconfig_path=%s\ndata_path=%s\nlive_restart=not_run\nmutation=not_run\n' "$profile_id" "$fingerprint" "$config" "$data"
