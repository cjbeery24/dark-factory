#!/usr/bin/env bash
# tests/shell/test_crash_classification.sh — Test crash classification in _parse_scenario_counts
#
# Verifies that the Python-based crash classifier correctly distinguishes
# scenario crashes (TypeError, ReferenceError, etc.) from assertion failures.
#
# Usage: bash tests/shell/test_crash_classification.sh
# Exit:  0 on all pass, 1 on any failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TMPDIR_BASE="$(mktemp -d)"
PASS=0
FAIL=0

cleanup() { rm -rf "$TMPDIR_BASE"; }
trap cleanup EXIT

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "  PASS: $label"
        ((PASS++))
    else
        echo "  FAIL: $label (expected='$expected', got='$actual')"
        ((FAIL++))
    fi
}

# We test the crash classification logic directly by mimicking _parse_scenario_counts.
# The classification uses a Python one-liner that reads from stdin and counts
# crash-class error lines.
classify_crashes() {
    local raw_output="$1"
    local failed="$2"
    local clean
    clean=$(echo "$raw_output" | sed 's/\x1b\[[0-9;]*m//g')
    echo "$clean" | DF_FAILED="$failed" python3 -c "
import os, sys, re
failed = int(os.environ['DF_FAILED'])
pat = re.compile(r'^\s*(?:TypeError|ReferenceError|SyntaxError|Cannot find module|ERR_MODULE_NOT_FOUND|ModuleNotFoundError|ImportError|NameError):')
n = sum(1 for line in sys.stdin if pat.match(line))
print(min(n, failed))
" 2>/dev/null || echo 0
}

echo "=== Crash Classification Tests ==="
echo ""

# ---------------------------------------------------------------------------
# Test 1: TypeError crash from non-existent assertion method
# ---------------------------------------------------------------------------
echo "Test 1: TypeError crash (non-existent method)"
output=$(cat <<'VITEST_OUTPUT'
 ✓ should create package.json (5 ms)
 × should validate config format (12 ms)

  ⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯

 FAIL  01-contract.test.ts > should validate config format
TypeError: expect(...).toEndWith is not a function
    at Object.<anonymous> (01-contract.test.ts:42:18)

 Test Files  1 failed | 0 passed
 Tests  1 failed | 1 passed
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 1)
assert_eq "TypeError crash detected" "1" "$crashed"

# ---------------------------------------------------------------------------
# Test 2: Assertion failure mentioning TypeError (should NOT be a crash)
# ---------------------------------------------------------------------------
echo "Test 2: Assertion mentioning TypeError (not a crash)"
output=$(cat <<'VITEST_OUTPUT'
 ✓ should handle valid input (3 ms)
 × should throw on invalid input (8 ms)

  ⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯

 FAIL  02-happy-path.test.ts > should throw on invalid input
AssertionError: expected to throw TypeError but received RangeError
    at Object.<anonymous> (02-happy-path.test.ts:15:5)

 Test Files  1 failed | 0 passed
 Tests  1 failed | 1 passed
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 1)
assert_eq "Assertion mentioning TypeError not classified as crash" "0" "$crashed"

# ---------------------------------------------------------------------------
# Test 3: No crash-class errors at all
# ---------------------------------------------------------------------------
echo "Test 3: Pure assertion failures"
output=$(cat <<'VITEST_OUTPUT'
 × should return 201 status (5 ms)
 × should include id field (3 ms)

  ⎯⎯⎯⎯ Failed Tests 2 ⎯⎯⎯⎯

 FAIL  01-contract.test.ts > should return 201 status
AssertionError: expected 404 to be 201
 FAIL  01-contract.test.ts > should include id field
AssertionError: expected undefined to be defined

 Test Files  1 failed | 0 passed
 Tests  2 failed | 0 passed
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 2)
assert_eq "No crashes in pure assertion output" "0" "$crashed"

# ---------------------------------------------------------------------------
# Test 4: Cannot find module crash
# ---------------------------------------------------------------------------
echo "Test 4: Cannot find module crash"
output=$(cat <<'VITEST_OUTPUT'
 × should import config module (2 ms)

  ⎯⎯⎯⎯ Failed Tests 1 ⎯⎯⎯⎯

 FAIL  01-contract.test.ts > should import config module
Cannot find module: '@project/shared/config'
    at Object.<anonymous> (01-contract.test.ts:3:1)

 Test Files  1 failed | 0 passed
 Tests  1 failed | 0 passed
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 1)
assert_eq "Cannot find module detected as crash" "1" "$crashed"

# ---------------------------------------------------------------------------
# Test 5: Crash count capped at failed count
# ---------------------------------------------------------------------------
echo "Test 5: Crash count capped at failed"
output=$(cat <<'VITEST_OUTPUT'
 × test one (2 ms)

TypeError: foo is not a function
TypeError: foo is not a function
TypeError: foo is not a function
TypeError: foo is not a function
TypeError: foo is not a function

 Tests  1 failed | 0 passed
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 1)
assert_eq "Crash count capped at failed count" "1" "$crashed"

# ---------------------------------------------------------------------------
# Test 6: Mixed crashes and assertions
# ---------------------------------------------------------------------------
echo "Test 6: Mixed crashes and assertions"
output=$(cat <<'VITEST_OUTPUT'
 × should use valid methods (5 ms)
 × should return correct status (3 ms)
 × should handle edge case (2 ms)

TypeError: expect(...).toEndWith is not a function
ReferenceError: nonExistentVar is not defined
AssertionError: expected 404 to be 201

 Tests  3 failed | 0 passed
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 3)
assert_eq "Mixed: 2 crashes detected" "2" "$crashed"

# ---------------------------------------------------------------------------
# Test 7: Zero failures (no classification needed)
# ---------------------------------------------------------------------------
echo "Test 7: Zero failures"
crashed=$(classify_crashes "all tests passed" 0)
# With failed=0, the if-branch is skipped entirely in the real code.
# But our classify_crashes function always runs. The cap ensures 0.
assert_eq "Zero failures = zero crashes" "0" "$crashed"

# ---------------------------------------------------------------------------
# Test 8: ERR_MODULE_NOT_FOUND (Node.js ESM error)
# ---------------------------------------------------------------------------
echo "Test 8: ERR_MODULE_NOT_FOUND"
output=$(cat <<'VITEST_OUTPUT'
 × should load ESM module (3 ms)

ERR_MODULE_NOT_FOUND: Cannot find package '@project/shared'
    at new NodeError (node:internal/errors:405:5)

 Tests  1 failed | 0 passed
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 1)
assert_eq "ERR_MODULE_NOT_FOUND detected as crash" "1" "$crashed"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
