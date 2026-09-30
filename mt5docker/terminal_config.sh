#!/usr/bin/env bash

# Helpers for selecting the MT5 configuration used at startup. In terminal
# mode the mounted file is treated as an operator-provided template, but
# account selectors are removed so MT5 restores the account saved in its
# persistent Wine/portable data directory. No credential value is printed.

validate_mt5_connection_mode() {
    case "${1:-}" in
        terminal|managed) return 0 ;;
        *)
            echo '>>> Invalid MT5_CONNECTION_MODE; expected terminal or managed.' >&2
            return 1
            ;;
    esac
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
