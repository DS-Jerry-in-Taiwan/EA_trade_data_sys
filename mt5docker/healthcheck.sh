#!/usr/bin/env bash
set -u
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
. "$SCRIPT_DIR/terminal_lifecycle.sh"
# Do not open an RPyC connection: health probes must not consume client slots.
exactly_one_normal_terminal || exit 1
[ -r /run/mt5-server/rpyc.pid ] || exit 1
read -r rpyc_pid < /run/mt5-server/rpyc.pid
case "$rpyc_pid" in (*[!0-9]*|'') exit 1 ;; esac
kill -0 "$rpyc_pid" 2>/dev/null || exit 1
rpyc_is_listening || exit 1
