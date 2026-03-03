#!/usr/bin/env bash
set -euo pipefail

# tests/run-tests.sh — Pure-bash test runner
# Discovers and runs all tests/test-*.sh files.

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TOTAL_PASS=0
TOTAL_FAIL=0
FAILED_FILES=()

echo "========================================"
echo "  Dark Factory Test Suite"
echo "========================================"
echo ""

for test_file in "$TESTS_DIR"/test-*.sh; do
    [[ ! -f "$test_file" ]] && continue
    name="$(basename "$test_file")"
    echo "--- $name ---"
    exit_code=0
    bash "$test_file" || exit_code=$?
    if [[ $exit_code -ne 0 ]]; then
        TOTAL_FAIL=$((TOTAL_FAIL + 1))
        FAILED_FILES+=("$name")
    else
        TOTAL_PASS=$((TOTAL_PASS + 1))
    fi
    echo ""
done

echo "========================================"
echo "  Results: ${TOTAL_PASS} passed, ${TOTAL_FAIL} failed"
if [[ ${#FAILED_FILES[@]} -gt 0 ]]; then
    echo "  Failed:"
    for f in "${FAILED_FILES[@]}"; do
        echo "    - $f"
    done
fi
echo "========================================"

[[ $TOTAL_FAIL -gt 0 ]] && exit 1
exit 0
