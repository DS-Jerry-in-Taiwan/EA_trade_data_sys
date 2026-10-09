#!/usr/bin/env bash
set -Eeuo pipefail

TEST_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
"$TEST_DIR/test_terminal_config.sh"
bash "$TEST_DIR/test_account_recovery.sh"
"$TEST_DIR/test_terminal_lifecycle.sh"
"$TEST_DIR/test_update_state_machine.sh"
bash "$TEST_DIR/test_update_event_classification.sh"
"$TEST_DIR/test_compose_config.sh"
bash "$TEST_DIR/test_compose_wine_prefix.sh"
"$TEST_DIR/test_desktop_shell.sh"
