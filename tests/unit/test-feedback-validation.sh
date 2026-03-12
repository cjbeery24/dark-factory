#!/usr/bin/env bash
set -euo pipefail

# tests/test-feedback-validation.sh — Tests for validate_oracle_feedback (semantic leakage detection)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"
source "${TESTS_DIR}/../scripts/lib/audit.sh"

setup_tmpdir

AUDIT_LOG="$TMPDIR_TEST/audit.log"
touch "$AUDIT_LOG"

# ==============================================================================
# Clean feedback — no leakage
# ==============================================================================

test_begin "clean feedback (spec-section references only)"

> "$AUDIT_LOG"
output=$(validate_oracle_feedback "The implementation does not satisfy FR-1 and EC-3. The error handling behavior needs improvement." 2>&1)
leaks=$(grep -c "SEMANTIC_LEAKAGE_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || leaks=0
assert_equals "0" "$leaks" "clean feedback has 0 leaks"
assert_not_contains "$output" "WARNING" "no warning for clean feedback"

# ==============================================================================
# Scenario filename detection
# ==============================================================================

test_begin "scenario filename detected"

> "$AUDIT_LOG"
validate_oracle_feedback "The scenario_03 test case is failing." >/dev/null 2>&1
leaks=$(grep -c "SEMANTIC_LEAKAGE_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || leaks=0
assert_equals "1" "$leaks" "scenario_03 detected"
assert_contains "$(cat "$AUDIT_LOG")" "scenario_filename" "category is scenario_filename"

test_begin "test file extension detected"

> "$AUDIT_LOG"
validate_oracle_feedback "Check the auth.test.ts file for details." >/dev/null 2>&1
leaks=$(grep -c "SEMANTIC_LEAKAGE_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || leaks=0
assert_equals "1" "$leaks" "auth.test.ts detected"

# ==============================================================================
# Test framework call detection
# ==============================================================================

test_begin "test framework call detected"

> "$AUDIT_LOG"
validate_oracle_feedback 'The assertion expect(result).toBe(true) shows the issue.' >/dev/null 2>&1
leaks=$(grep -c "SEMANTIC_LEAKAGE_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || leaks=0
assert_equals "1" "$leaks" "expect().toBe() detected"
assert_contains "$(cat "$AUDIT_LOG")" "test_assertion_call" "category is test_assertion_call"

# ==============================================================================
# Test DSL detection
# ==============================================================================

test_begin "test DSL pattern detected"

> "$AUDIT_LOG"
validate_oracle_feedback 'The describe("auth") block has issues.' >/dev/null 2>&1
leaks=$(grep -c "SEMANTIC_LEAKAGE_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || leaks=0
assert_equals "1" "$leaks" "describe() detected"
assert_contains "$(cat "$AUDIT_LOG")" "test_dsl_pattern" "category is test_dsl_pattern"

# ==============================================================================
# Raw test output detection
# ==============================================================================

test_begin "raw test output detected"

> "$AUDIT_LOG"
validate_oracle_feedback "Expected: 200 Received: 401" >/dev/null 2>&1
leaks=$(grep -c "SEMANTIC_LEAKAGE_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || leaks=0
assert_equals "1" "$leaks" "Expected/Received detected"
assert_contains "$(cat "$AUDIT_LOG")" "raw_test_output" "category is raw_test_output"

# ==============================================================================
# Stack trace detection
# ==============================================================================

test_begin "stack trace detected"

> "$AUDIT_LOG"
validate_oracle_feedback "Error at Object.<anonymous> (/test/file.js:10:5)" >/dev/null 2>&1
leaks=$(grep -c "SEMANTIC_LEAKAGE_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || leaks=0
assert_equals "1" "$leaks" "at Object.<anonymous> detected"
assert_contains "$(cat "$AUDIT_LOG")" "test_stack_trace" "category is test_stack_trace"

# ==============================================================================
# No false positive for prose "assert" without parens
# ==============================================================================

test_begin "prose 'assert' without parens not detected"

> "$AUDIT_LOG"
validate_oracle_feedback "We should assert that the implementation handles edge cases correctly and ensure expected behavior." >/dev/null 2>&1
leaks=$(grep -c "SEMANTIC_LEAKAGE_SUSPECTED" "$AUDIT_LOG" 2>/dev/null) || leaks=0
assert_equals "0" "$leaks" "prose 'assert' and 'expected' without parens not detected"

# ==============================================================================
# Always returns 0 (detection-only)
# ==============================================================================

test_begin "always returns 0"

> "$AUDIT_LOG"
exit_code=0
validate_oracle_feedback 'expect(x).toBe(y) in scenario_01 at Object.<anonymous>' || exit_code=$?
assert_equals "0" "$exit_code" "returns 0 even with multiple leaks"

cleanup_tmpdir
test_summary
