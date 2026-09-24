#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
FIXTURE_DATA="/tmp/mt5-data-compose-fixture"
rendered="$(
    READONLY_API_KEY=compose-test-only \
    MT5_DATA_DIR="$FIXTURE_DATA" \
    docker compose -f "$ROOT/mt5docker/compose.yaml" config
)"

sources="$(printf '%s\n' "$rendered" | awk '/source:/ { print $2 }')"
count="$(printf '%s\n' "$sources" | grep -Fxc "$FIXTURE_DATA" || true)"
if [ "$count" -ne 2 ]; then
    echo "FAIL: expected MT5_DATA_DIR as both rendered mount sources" >&2
    exit 1
fi

printf '%s\n' "$rendered" | grep -Fq 'target: /mt5docker/MT5_Data' || {
    echo 'FAIL: readiness-log mount target is missing' >&2
    exit 1
}
printf '%s\n' "$rendered" | grep -Fq 'target: /opt/wineprefix/drive_c/users/root/AppData/Roaming/MetaTrader 5' || {
    echo 'FAIL: Wine AppData mount target is missing' >&2
    exit 1
}

echo 'compose MT5_DATA_DIR interpolation test passed'

default_rendered="$(
    env -u MT5_DATA_DIR READONLY_API_KEY=compose-test-only \
    docker compose -f "$ROOT/mt5docker/compose.yaml" config
)"
default_source="$ROOT/mt5docker/MT5_Data"
default_count="$(printf '%s\n' "$default_rendered" | awk '/source:/ { print $2 }' | grep -Fxc "$default_source" || true)"
if [ "$default_count" -ne 2 ]; then
    echo 'FAIL: default MT5_Data source is not backwards compatible' >&2
    exit 1
fi

echo 'compose default MT5_Data interpolation test passed'
