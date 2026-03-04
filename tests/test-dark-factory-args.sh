#!/usr/bin/env bash
set -euo pipefail

# tests/test-dark-factory-args.sh — Tests for dark-factory.sh argument validation (Item 21)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

SCRIPT="${TESTS_DIR}/../scripts/dark-factory.sh"

# ==============================================================================
# --max-iterations missing value
# ==============================================================================

test_begin "max-iterations missing value"

output=$(bash "$SCRIPT" test-spec --max-iterations 2>&1 || true)
assert_contains "$output" "ERROR" "missing --max-iterations value produces error"

# ==============================================================================
# --test-cmd missing value
# ==============================================================================

test_begin "test-cmd missing value"

output=$(bash "$SCRIPT" test-spec --test-cmd 2>&1 || true)
assert_contains "$output" "ERROR" "missing --test-cmd value produces error"

# ==============================================================================
# --max-iterations non-numeric
# ==============================================================================

test_begin "max-iterations non-numeric"

output=$(bash "$SCRIPT" test-spec --max-iterations abc 2>&1 || true)
assert_contains "$output" "ERROR" "non-numeric --max-iterations produces error"
assert_contains "$output" "positive integer" "error message mentions positive integer"

test_summary
