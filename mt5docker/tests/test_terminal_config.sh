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

MARKER="$TMP/MT5_Data/.mt5-bootstrap-complete"
if ! bootstrap_config_required "$MARKER" 0; then
    echo 'FAIL: missing bootstrap marker did not require first import' >&2
    exit 1
fi
write_bootstrap_marker "$MARKER" || {
    echo 'FAIL: bootstrap marker could not be persisted' >&2
    exit 1
}
if bootstrap_config_required "$MARKER" 0; then
    echo 'FAIL: valid marker still required bootstrap import' >&2
    exit 1
fi
if ! bootstrap_config_required "$MARKER" 1; then
    echo 'FAIL: explicit bootstrap re-import was not honored' >&2
    exit 1
fi
if ! grep -Fqx 'mt5-bootstrap-complete-v1' "$MARKER"; then
    echo 'FAIL: bootstrap marker is not the expected non-secret value' >&2
    exit 1
fi
if grep -Fq 'secret-value' "$MARKER"; then
    echo 'FAIL: bootstrap marker leaked credential material' >&2
    exit 1
fi

# A failed bootstrap must not create the persistent marker. This harness
# models startup failure before write_bootstrap_marker is reached.
FAILED_MARKER="$TMP/failed/.mt5-bootstrap-complete"
if [ -e "$FAILED_MARKER" ]; then
    echo 'FAIL: failed bootstrap marker unexpectedly exists' >&2
    exit 1
fi

bootstrap_output="$(bootstrap_marker_valid "$MARKER" 2>&1 || true)"
if printf '%s' "$bootstrap_output" | grep -Fq 'secret-value'; then
    echo 'FAIL: bootstrap status output leaked credentials' >&2
    exit 1
fi

grep -Fq 'await_terminal_authorized' "$ROOT/mt5docker/start_server.sh" || {
    echo 'FAIL: startup does not verify the Journal authorization session' >&2
    exit 1
}
if grep -Eq 'account_session_ready|account_info\(|MetaTrader5' "$ROOT/mt5docker/start_server.sh"; then
    echo 'FAIL: startup readiness probe opened an RPyC/MT5 client' >&2
    exit 1
fi
grep -Fq 'MT5_BOOTSTRAP_MARKER' "$ROOT/mt5docker/start_server.sh" || {
    echo 'FAIL: startup marker path is not configured' >&2
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
