#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
"$TEST_DIR/test_terminal_lifecycle.sh"
"$TEST_DIR/test_compose_config.sh"
