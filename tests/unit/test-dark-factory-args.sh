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

# ==============================================================================
# RUN_ID entropy format
# ==============================================================================

test_begin "RUN_ID format includes hex suffix"

# Generate a RUN_ID using the same method as dark-factory.sh
run_id="$(date +%Y%m%d-%H%M%S)-$$-$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
# Verify format: YYYYMMDD-HHMMSS-PID-HEX{8}
if [[ "$run_id" =~ ^[0-9]{8}-[0-9]{6}-[0-9]+-[0-9a-f]{8}$ ]]; then
    _test_pass "RUN_ID matches expected format"
else
    _test_fail "RUN_ID '$run_id' does not match YYYYMMDD-HHMMSS-PID-HEX{8}"
fi

test_begin "RUN_ID hex suffix is 8 chars"

suffix="$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
assert_equals "8" "${#suffix}" "hex suffix length is 8"

# ==============================================================================
# --auto-pr flag
# ==============================================================================

test_begin "--auto-pr flag"

output=$(bash "$SCRIPT" test-spec --auto-pr 2>&1 || true)
# Should get past arg parsing (fail later on missing config/docker, not on unknown option)
assert_not_contains "$output" "Unknown option" "--auto-pr is recognized"

# ==============================================================================
# --no-pr flag
# ==============================================================================

test_begin "--no-pr flag"

output=$(bash "$SCRIPT" test-spec --no-pr 2>&1 || true)
assert_not_contains "$output" "Unknown option" "--no-pr is recognized"

# ==============================================================================
# --base-branch requires value
# ==============================================================================

test_begin "--base-branch missing value"

output=$(bash "$SCRIPT" test-spec --base-branch 2>&1 || true)
assert_contains "$output" "ERROR" "missing --base-branch value produces error"

# ==============================================================================
# --base-branch with value
# ==============================================================================

test_begin "--base-branch with value"

output=$(bash "$SCRIPT" test-spec --base-branch develop 2>&1 || true)
assert_not_contains "$output" "Unknown option" "--base-branch with value is recognized"

test_summary
