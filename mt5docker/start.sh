#!/usr/bin/env bash
set -Eeuo pipefail

# Keep one startup owner. The old script launched an unbounded watchdog and
# restarted MT5 with a fixed config, which could override an account selected
# through the desktop. start_server.sh owns the bounded update lifecycle,
# /skipupdate, readiness and RPyC startup for both entrypoint names.
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
exec "$SCRIPT_DIR/start_server.sh" "$@"
