#!/usr/bin/env bash

# Helpers for selecting MT5 startup arguments and optional config copies.
# Native terminal mode omits /config to restore the saved GUI account. Only
# a one-time bootstrap or explicit re-import selects the private source.
# Optional sanitized copies must also remove account selectors safely.
# No credential value is printed.

MT5_BOOTSTRAP_MARKER_VALUE='mt5-bootstrap-complete-v1'

validate_mt5_connection_mode() {
    case "${1:-}" in
        terminal|managed) return 0 ;;
        *)
            echo '>>> Invalid MT5_CONNECTION_MODE; expected terminal or managed.' >&2
            return 1
            ;;
    esac
}

bootstrap_reimport_requested() {
    case "${1:-0}" in
        1|true|yes|on) return 0 ;;
        *) return 1 ;;
    esac
}

bootstrap_marker_valid() {
    local marker="$1"
    [ -r "$marker" ] || return 1
    grep -Fqx "$MT5_BOOTSTRAP_MARKER_VALUE" "$marker" 2>/dev/null
}

bootstrap_config_required() {
    local marker="$1" reimport="${2:-0}" portable_root="${3:-}"
    bootstrap_reimport_requested "$reimport" && return 0
    bootstrap_marker_valid "$marker" && return 1
    # Existing GUI account state can predate our bootstrap marker. Its
    # presence is enough to preserve operator control; never inspect it.
    if [ -n "$portable_root" ]; then
        [ ! -s "$portable_root/Config/accounts.dat" ] &&
            [ ! -s "$portable_root/config/accounts.dat" ] || return 1
    fi
    return 0
}

write_bootstrap_marker() {
    local marker="$1" parent temporary
    parent="${marker%/*}"
    [ "$parent" != "$marker" ] || parent='.'
    mkdir -p "$parent" || return 1
    temporary="$(mktemp "${marker}.tmp.XXXXXX")" || return 1
    if ! printf '%s\n' "$MT5_BOOTSTRAP_MARKER_VALUE" > "$temporary"; then
        rm -f "$temporary"
        return 1
    fi
    mv -f "$temporary" "$marker"
}

set_mt5_terminal_launch_arguments() {
    local mode="$1" bootstrap_active="${2:-0}" windows_config="${3:-}"
    validate_mt5_connection_mode "$mode" || return 1
    MT5_TERMINAL_ARGS=(/portable /skipupdate)
    if [ "$mode" = managed ] || [ "$bootstrap_active" -eq 1 ]; then
        [ -n "$windows_config" ] || {
            echo '>>> MT5 bootstrap/managed config path is missing.' >&2
            return 1
        }
        MT5_TERMINAL_ARGS+=("/config:$windows_config")
    fi
}

prepare_mt5_config() {
    local mode="$1" source="$2" destination="$3" prefix encoding bom temporary
    validate_mt5_connection_mode "$mode" || return
    [ -r "$source" ] || {
        echo '>>> MT5 config is missing or unreadable.' >&2
        return 1
    }

    if [ "$mode" = terminal ]; then
        [ "$source" != "$destination" ] || {
            echo '>>> MT5 sanitized config must use a separate path.' >&2
            return 1
        }
        # Windows-created INI files commonly have a UTF-16 BOM. Applying awk
        # directly to those bytes leaves the account keys intact. Decode only
        # inside this private pipeline, and retain the source encoding/BOM.
        prefix="$(od -An -tx1 -N4 "$source" | tr -d '[:space:]')" || return 1
        encoding=UTF-8; bom=0
        case "$prefix" in
            fffe*) encoding=UTF-16LE; bom=2 ;;
            feff*) encoding=UTF-16BE; bom=2 ;;
            efbbbf*) bom=3 ;;
            ??00??00) encoding=UTF-16LE ;;
            00??00??) encoding=UTF-16BE ;;
        esac
        temporary="$(umask 077; mktemp "${destination}.tmp.XXXXXX")" || return 1
        # Match INI keys case-insensitively, while preserving all unrelated
        # terminal settings and comments. Login/Password/Server must not be
        # able to take over the account selected in the GUI.
        if ! (
            set -o pipefail
            tail -c "+$((bom + 1))" "$source" |
                iconv -f "$encoding" -t UTF-8 2>/dev/null |
                LC_ALL=C awk '
            {
                key = $0
                sub(/^[[:space:]]*/, "", key)
                sub(/[[:space:]]*=.*$/, "", key)
                key = tolower(key)
                if (key == "login" || key == "password" || key == "server") next
                print
            }
        ' | {
            case "$bom:$encoding" in
                2:UTF-16LE) printf '\377\376' ;;
                2:UTF-16BE) printf '\376\377' ;;
                3:UTF-8) printf '\357\273\277' ;;
            esac
            iconv -f UTF-8 -t "$encoding" 2>/dev/null
        }
        ) > "$temporary"; then
            rm -f "$temporary"
            echo '>>> MT5 config could not be safely sanitized.' >&2
            return 1
        fi
        if ! mv -f "$temporary" "$destination"; then
            rm -f "$temporary"
            return 1
        fi
        printf '%s\n' "$destination"
        return 0
    fi

    # Managed mode deliberately keeps the source config, which is the
    # explicit opt-in path for credential-based terminal initialization.
    printf '%s\n' "$source"
}
