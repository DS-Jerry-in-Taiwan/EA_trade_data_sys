#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
. "$ROOT/mt5docker/terminal_config.sh"

SOURCE="$TMP/source.ini"
SANITIZED="$TMP/sanitized.ini"
cat > "$SOURCE" <<'EOF'
[Common]
Login=123456
Password=secret-value
Server=OANDA-Demo-1
ProxyEnable=0

[Charts]
MaxBars=1000000
EOF

result="$(prepare_mt5_config terminal "$SOURCE" "$SANITIZED")"
[ "$result" = "$SANITIZED" ] || {
    echo 'FAIL: terminal mode did not return sanitized config path' >&2
    exit 1
}
grep -Fq 'ProxyEnable=0' "$SANITIZED" || {
    echo 'FAIL: terminal config lost unrelated settings' >&2
    exit 1
}
if grep -Eiq '^[[:space:]]*(login|password|server)[[:space:]]*=' "$SANITIZED"; then
    echo 'FAIL: terminal config still forces an account' >&2
    exit 1
fi

managed_result="$(prepare_mt5_config managed "$SOURCE" "$TMP/unused.ini")"
[ "$managed_result" = "$SOURCE" ] || {
    echo 'FAIL: managed mode did not preserve source config' >&2
    exit 1
}

if validate_mt5_connection_mode invalid; then
    echo 'FAIL: invalid MT5 connection mode was accepted' >&2
    exit 1
fi

if MT5_CONNECTION_MODE=terminal bash "$ROOT/mt5docker/sync_mt5cfg.sh" >/dev/null 2>&1; then
    echo 'FAIL: account sync was allowed in terminal mode' >&2
    exit 1
fi

echo 'terminal config mode tests passed'
