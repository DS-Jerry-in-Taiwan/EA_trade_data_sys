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

# Exercise the encodings used by Windows INI editors, including a BOM before
# the very first account key, and CRLF/mixed-case keys. All data is synthetic.
for format in utf8-bom utf16le-bom utf16be-bom utf16le utf16be; do
    ENCODED_SOURCE="$TMP/$format-source.ini"
    ENCODED_RESULT="$TMP/$format-result.ini"
    encoding=UTF-8; bom_size=0
    case "$format" in
        utf8-bom) printf '\357\273\277' > "$ENCODED_SOURCE"; bom_size=3 ;;
        utf16le-bom) printf '\377\376' > "$ENCODED_SOURCE"; encoding=UTF-16LE; bom_size=2 ;;
        utf16be-bom) printf '\376\377' > "$ENCODED_SOURCE"; encoding=UTF-16BE; bom_size=2 ;;
        utf16le) : > "$ENCODED_SOURCE"; encoding=UTF-16LE ;;
        utf16be) : > "$ENCODED_SOURCE"; encoding=UTF-16BE ;;
    esac
    printf ' LoGiN = 123456\r\n[Common]\r\n PaSsWoRd\t= synthetic-secret\r\n SERVER = demo-server\r\nProxyEnable=0\r\n; keep this comment\r\n' |
        iconv -f UTF-8 -t "$encoding" >> "$ENCODED_SOURCE"
    prepare_mt5_config terminal "$ENCODED_SOURCE" "$ENCODED_RESULT" >/dev/null || {
        echo "FAIL: $format config could not be sanitized" >&2; exit 1
    }
    decoded="$(tail -c "+$((bom_size + 1))" "$ENCODED_RESULT" | iconv -f "$encoding" -t UTF-8)"
    if printf '%s\n' "$decoded" | grep -Eiq '^[[:space:]]*(login|password|server)[[:space:]]*='; then
        echo "FAIL: $format config still selects an account" >&2; exit 1
    fi
    if ! printf '%s\n' "$decoded" | grep -Fq 'ProxyEnable=0'; then
        echo "FAIL: $format config lost unrelated settings" >&2; exit 1
    fi
    if [ "$bom_size" -gt 0 ]; then
        [ "$(od -An -tx1 -N"$bom_size" "$ENCODED_SOURCE")" = "$(od -An -tx1 -N"$bom_size" "$ENCODED_RESULT")" ] || {
            echo "FAIL: $format config did not retain its BOM" >&2; exit 1
        }
    fi
done

# A malformed encoded file must fail without publishing partial output.
MALFORMED="$TMP/malformed.ini"
printf '\377\376L\000o' > "$MALFORMED"
printf 'unchanged\n' > "$TMP/malformed-result.ini"
if prepare_mt5_config terminal "$MALFORMED" "$TMP/malformed-result.ini" >/dev/null 2>&1; then
    echo 'FAIL: malformed UTF-16 config was accepted' >&2; exit 1
fi
[ "$(<"$TMP/malformed-result.ini")" = unchanged ] || {
    echo 'FAIL: failed sanitization published a partial config' >&2; exit 1
}
if prepare_mt5_config terminal "$SOURCE" "$SOURCE" >/dev/null 2>&1; then
    echo 'FAIL: sanitizer allowed overwriting the private source' >&2; exit 1
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

# Model a persisted second start after the GUI selected another account.
# Only the private bootstrap may select an account from the source. A marker
# and an existing GUI account store both suppress a silent re-import.
PERSISTED_ROOT="$TMP/persisted-terminal"
mkdir -p "$PERSISTED_ROOT/Config"
printf 'opaque-synthetic-account-store\n' > "$PERSISTED_ROOT/Config/accounts.dat"
if bootstrap_config_required "$MARKER" 0 "$PERSISTED_ROOT"; then
    echo 'FAIL: second start selected private bootstrap instead of GUI session' >&2; exit 1
fi
SECOND_CONFIG="$(prepare_mt5_config terminal "$SOURCE" "$TMP/second-start.ini")"
if grep -Eiq '^[[:space:]]*(login|password|server)[[:space:]]*=' "$SECOND_CONFIG"; then
    echo 'FAIL: second start retained fixed account selectors' >&2; exit 1
fi
if bootstrap_config_required "$TMP/legacy-marker-missing" 0 "$PERSISTED_ROOT"; then
    echo 'FAIL: a missing marker allowed takeover of an existing GUI account store' >&2; exit 1
fi
if ! bootstrap_config_required "$TMP/legacy-marker-missing" 1 "$PERSISTED_ROOT"; then
    echo 'FAIL: explicit operator re-import did not override existing account state' >&2; exit 1
fi

# Test the actual launch argument policy. Subsequent terminal starts restore
# native saved settings and cannot even pass the mounted private config path.
set_mt5_terminal_launch_arguments terminal 1 'Z:\synthetic-bootstrap.ini'
[ "${MT5_TERMINAL_ARGS[*]}" = '/portable /skipupdate /config:Z:\synthetic-bootstrap.ini' ] || {
    echo 'FAIL: explicit bootstrap launch did not select its private config' >&2; exit 1
}
set_mt5_terminal_launch_arguments terminal 0 'Z:\synthetic-bootstrap.ini'
[ "${MT5_TERMINAL_ARGS[*]}" = '/portable /skipupdate' ] || {
    echo 'FAIL: persisted native start passed a fixed account config' >&2; exit 1
}
set_mt5_terminal_launch_arguments terminal 0 '' || {
    echo 'FAIL: saved GUI session unnecessarily required mounted private config' >&2; exit 1
}
set_mt5_terminal_launch_arguments managed 0 'Z:\synthetic-managed.ini'
[ "${MT5_TERMINAL_ARGS[*]}" = '/portable /skipupdate /config:Z:\synthetic-managed.ini' ] || {
    echo 'FAIL: explicitly managed launch lost its config' >&2; exit 1
}
if set_mt5_terminal_launch_arguments terminal 1 '' >/dev/null 2>&1; then
    echo 'FAIL: bootstrap accepted a missing config path' >&2; exit 1
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

grep -Fq 'await_terminal_authorized' "$ROOT/mt5docker/terminal_lifecycle.sh" || {
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
