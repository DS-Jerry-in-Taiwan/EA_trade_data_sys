#!/usr/bin/env bash
# ------------------------------------------------------------------
# Phase 5 E2E Test Runner
# Runs all 6 layers sequentially.  Each layer continues on failure
# so a summary is printed at the end.
# ------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

RESULTS_DIR="/tmp/e2e_results"
mkdir -p "$RESULTS_DIR"

LAYERS=(
    "test_infra.py::TestInfrastructure"
    "test_services.py"
    "test_api.py"
    "test_websocket.py"
    "test_data_integrity.py"
    "test_monitoring.py"
)

echo "=========================================="
echo "  Phase 5 — E2E Test Suite"
echo "  $(date -u '+%Y-%m-%d %H:%M:%S UTC')"
echo "=========================================="
echo ""

for layer in "${LAYERS[@]}"; do
    name="${layer%%::*}"
    echo "--- Layer: ${layer} ---"
    set +e
    python3 -m pytest -v --tb=short "$layer" 2>&1 \
        | tee "$RESULTS_DIR/$(basename "$name").log"
    rc=$?
    set -e
    if [ $rc -eq 0 ]; then
        echo "  Layer ${layer} PASSED"
    else
        echo "  Layer ${layer} EXIT CODE $rc (some tests may have failed)"
    fi
    echo ""
done

# Summary
echo "=========================================="
echo "  Individual Test Summary"
echo "=========================================="
python3 -m pytest --tb=line -q \
    test_infra.py test_services.py test_api.py \
    test_websocket.py test_data_integrity.py test_monitoring.py \
    2>&1 || true
