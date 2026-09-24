#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
FIXTURE_DATA="/tmp/mt5-data-compose-fixture"
FIXTURE_CONFIG="/tmp/mt5-config-compose-fixture.ini"
rendered="$(
    READONLY_API_KEY=compose-test-only \
    MT5_DATA_DIR="$FIXTURE_DATA" \
    MT5_CONFIG_FILE="$FIXTURE_CONFIG" \
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

config_count="$(printf '%s\n' "$rendered" | awk '/source:/ { print $2 }' | grep -Fxc "$FIXTURE_CONFIG" || true)"
if [ "$config_count" -ne 1 ]; then
    echo 'FAIL: expected exactly one MT5_CONFIG_FILE mount source' >&2
    exit 1
fi
printf '%s\n' "$rendered" | grep -Fq 'target: /run/mt5/mt5cfg.ini' || {
    echo 'FAIL: exact runtime config target is missing' >&2
    exit 1
}
config_json="$(
    READONLY_API_KEY=compose-test-only \
    MT5_DATA_DIR="$FIXTURE_DATA" \
    MT5_CONFIG_FILE="$FIXTURE_CONFIG" \
    docker compose -f "$ROOT/mt5docker/compose.yaml" config --format json
)"
printf '%s' "$config_json" | python3 -c '
import json, sys
service = json.load(sys.stdin)["services"]["mt5-server"]
matches = [v for v in service["volumes"] if v["target"] == "/run/mt5/mt5cfg.ini"]
if len(matches) != 1 or matches[0]["source"] != "/tmp/mt5-config-compose-fixture.ini" or not matches[0]["read_only"]:
    raise SystemExit("FAIL: config mount is not exact and read-only")
if service["environment"].get("MT5_CONFIG_LINUX") != "/run/mt5/mt5cfg.ini":
    raise SystemExit("FAIL: server does not consume the mounted runtime config")
'
echo 'compose MT5_CONFIG_FILE interpolation test passed'

default_rendered="$(
    env -u MT5_DATA_DIR -u MT5_CONFIG_FILE READONLY_API_KEY=compose-test-only \
    docker compose -f "$ROOT/mt5docker/compose.yaml" config
)"
default_source="$ROOT/mt5docker/MT5_Data"
default_count="$(printf '%s\n' "$default_rendered" | awk '/source:/ { print $2 }' | grep -Fxc "$default_source" || true)"
if [ "$default_count" -ne 2 ]; then
    echo 'FAIL: default MT5_Data source is not backwards compatible' >&2
    exit 1
fi

default_config="$ROOT/mt5docker/mt5cfg.ini"
default_config_count="$(printf '%s\n' "$default_rendered" | awk '/source:/ { print $2 }' | grep -Fxc "$default_config" || true)"
if [ "$default_config_count" -ne 1 ]; then
    echo 'FAIL: default mt5cfg.ini source is not backwards compatible' >&2
    exit 1
fi

echo 'compose default MT5_Data interpolation test passed'
