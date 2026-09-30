#!/usr/bin/env bash

# Helpers for selecting the MT5 configuration used at startup. In terminal
# mode the mounted file is treated as an operator-provided template, but
# account selectors are removed so MT5 restores the account saved in its
# persistent Wine/portable data directory. A separate one-time bootstrap
# helper may explicitly select the private source before marker completion.
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
    local marker="$1" reimport="${2:-0}"
    bootstrap_reimport_requested "$reimport" && return 0
    ! bootstrap_marker_valid "$marker"
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

prepare_mt5_config() {
    local mode="$1" source="$2" destination="$3"
    validate_mt5_connection_mode "$mode" || return
    [ -r "$source" ] || {
        echo '>>> MT5 config is missing or unreadable.' >&2
        return 1
    }

    if [ "$mode" = terminal ]; then
        # Match INI keys case-insensitively, while preserving all unrelated
        # terminal settings and comments. Login/Password/Server must not be
        # able to take over the account selected in the GUI.
        awk '
            {
                key = $0
                sub(/^[[:space:]]*/, "", key)
                sub(/[[:space:]]*=.*$/, "", key)
                key = tolower(key)
                if (key == "login" || key == "password" || key == "server") next
                print
            }
        ' "$source" > "$destination"
        printf '%s\n' "$destination"
        return 0
    fi

    # Managed mode deliberately keeps the source config, which is the
    # explicit opt-in path for credential-based terminal initialization.
    printf '%s\n' "$source"
}
