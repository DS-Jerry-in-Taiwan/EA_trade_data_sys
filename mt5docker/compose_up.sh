#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
PROFILE_ENV="${MT5_PROFILE_ENV:-${HOME}/.config/ea-trading/active-profile.env}"
if [ ! -r "$PROFILE_ENV" ]; then
    echo "active MT5 profile is missing: $PROFILE_ENV" >&2
    echo "run profile_selector.py after confirming the VNC login" >&2
    exit 2
fi
declare -A PROFILE_VALUES=()
while IFS=$'\t' read -r key value; do
    [ -n "$key" ] || continue
    PROFILE_VALUES["$key"]="$value"
done < <(python3 "$SCRIPT_DIR/profile_selector.py" --read-env "$PROFILE_ENV")
MT5_PROFILE_ID="${PROFILE_VALUES[MT5_PROFILE_ID]:-}"
MT5_PROFILE_FINGERPRINT="${PROFILE_VALUES[MT5_PROFILE_FINGERPRINT]:-}"
MT5_CONFIG_FILE="${PROFILE_VALUES[MT5_CONFIG_FILE]:-}"
MT5_DATA_DIR="${PROFILE_VALUES[MT5_DATA_DIR]:-}"
: "${MT5_PROFILE_ID:?active profile has no MT5_PROFILE_ID}"
: "${MT5_PROFILE_FINGERPRINT:?active profile has no fingerprint}"
: "${MT5_CONFIG_FILE:?active profile has no MT5_CONFIG_FILE}"
: "${MT5_DATA_DIR:?active profile has no MT5_DATA_DIR}"
export MT5_PROFILE_ID MT5_PROFILE_FINGERPRINT MT5_CONFIG_FILE MT5_DATA_DIR
RUNTIME_OVERRIDE="${MT5_RUNTIME_OVERRIDE:-$SCRIPT_DIR/compose.runtime.override.yaml}"
if [ ! -r "$RUNTIME_OVERRIDE" ]; then
    echo "required runtime compose override is missing: $RUNTIME_OVERRIDE" >&2
    echo "restore the deployment runtime contract before starting compose" >&2
    exit 2
fi
exec docker compose \
    --env-file "$SCRIPT_DIR/.env" \
    -f "$SCRIPT_DIR/compose.yaml" \
    -f "$RUNTIME_OVERRIDE" \
    -f "$SCRIPT_DIR/compose.ftmo-profile.override.yaml" \
    "$@"
