#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
# Explicit empty env-file prevents loading deployment secrets. Values are synthetic.
export READONLY_API_KEY=compose-test-only
export MT5_DATA_DIR=/tmp/mt5-data-compose-fixture
export MT5_CONFIG_FILE=/tmp/mt5-config-compose-fixture.ini
export MT5_WINE_PREFIX_DIR=/tmp/mt5-prefix-compose-fixture
compose=(docker compose --env-file /dev/null -f "$ROOT/mt5docker/compose.yaml")
base_json="$("${compose[@]}" config --format json)"
merged_json="$("${compose[@]}" -f "$ROOT/mt5docker/compose.wine-prefix.yaml" config --format json)"
rg -q '^          create_host_path: false$' "$ROOT/mt5docker/compose.wine-prefix.yaml"
printf '%s' "$base_json" | python3 -c '
import json, sys
volumes = json.load(sys.stdin)["services"]["mt5-server"]["volumes"]
assert not any(v["target"] == "/opt/wineprefix" for v in volumes), "default compose must not mask the image prefix"
'
printf '%s' "$merged_json" | python3 -c '
import json, sys
services = json.load(sys.stdin)["services"]
volumes = services["mt5-server"]["volumes"]
prefix = [v for v in volumes if v["target"] == "/opt/wineprefix"]
assert len(prefix) == 1
assert prefix[0]["type"] == "bind"
assert prefix[0]["source"] == "/tmp/mt5-prefix-compose-fixture"
# Compose omits false defaults in its normalized JSON representation.
assert prefix[0].get("bind", {}).get("create_host_path", False) is False
for target in ("/mt5docker/MT5_Data", "/opt/wineprefix/drive_c/users/root/AppData/Roaming/MetaTrader 5"):
    matches = [v for v in volumes if v["target"] == target]
    assert len(matches) == 1 and matches[0]["source"] == "/tmp/mt5-data-compose-fixture"
config = [v for v in volumes if v["target"] == "/run/mt5/mt5cfg.ini"]
assert len(config) == 1 and config[0]["read_only"]
assert config[0]["source"] == "/tmp/mt5-config-compose-fixture.ini"
for name, service in services.items():
    if name != "mt5-server":
        assert not any(v["target"] == "/opt/wineprefix" for v in service.get("volumes", []))
'
if env -u MT5_WINE_PREFIX_DIR "${compose[@]}" -f "$ROOT/mt5docker/compose.wine-prefix.yaml" config >/dev/null 2>&1; then
    echo 'FAIL: missing prefix selection must fail' >&2
    exit 1
fi
if MT5_WINE_PREFIX_DIR='' "${compose[@]}" -f "$ROOT/mt5docker/compose.wine-prefix.yaml" config >/dev/null 2>&1; then
    echo 'FAIL: empty prefix selection must fail' >&2
    exit 1
fi
echo 'optional Wine-prefix compose tests passed'
