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

# We test the crash classifier by extracting the SHARED production regex value
# (the `readonly DF_CRASH_REGEX='...'` line in scripts/quality-gate.sh) and
# feeding it through the same env-var mechanism production uses — so this test
# can never drift from the shipped pattern (it used to re-implement the regex
# inline, which silently diverged). Both production crash classifiers
# (_parse_scenario_counts aggregate + _write_per_test_results per-test) read the
# same DF_CRASH_REGEX, so this exercises both. Anchored to ^readonly so it can't
# grab a different line. The classifier counts crash-class lines, capped at failed.
PROD_CRASH_REGEX="$(grep -m1 '^readonly DF_CRASH_REGEX=' "$PROJECT_ROOT/scripts/quality-gate.sh" | sed -E "s/^readonly DF_CRASH_REGEX='(.*)'\$/\1/")"
if [[ -z "$PROD_CRASH_REGEX" || "$PROD_CRASH_REGEX" == readonly* ]]; then
    echo "  FAIL: could not extract production DF_CRASH_REGEX from quality-gate.sh" >&2
    exit 1
fi
classify_crashes() {
    local raw_output="$1"
    local failed="$2"
    local clean
    clean=$(echo "$raw_output" | sed 's/\x1b\[[0-9;]*m//g')
    echo "$clean" | DF_FAILED="$failed" DF_CRASH_REGEX="$PROD_CRASH_REGEX" python3 -c "
import os, sys, re
failed = int(os.environ['DF_FAILED'])
pat = re.compile(os.environ['DF_CRASH_REGEX'])
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
# Test 9: vitest dynamic-import "Cannot find module" — the bb api-bootstrap
# signature. The error is prefixed "Error: " and uses a quoted path, NOT a
# colon, so the old colon-anchored pattern counted it as an assertion failure
# (SCENARIO_CRASHED=0 alongside "Cannot find module"). It is a runtime crash.
# ---------------------------------------------------------------------------
echo "Test 9: vitest 'Error: Cannot find module' (import crash)"
output=$(cat <<'VITEST_OUTPUT'
 × GET /health returns 200 when degraded (4 ms)

 FAIL  fr4-health-degraded.scenarios.test.ts > FR-4 > GET /health returns 200
Error: Cannot find module '../../../apps/api/src/server.js' imported from '/project/.dark-factory/scenarios/spec/fr4-health-degraded.scenarios.test.ts'

 Test Files  1 failed | 0 passed
 Tests  1 failed | 0 passed
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 1)
assert_eq "vitest 'Error: Cannot find module' detected as crash" "1" "$crashed"

# ---------------------------------------------------------------------------
# Test 10: vitest "Caused by: Error: Failed to load url ..." (vite import layer)
# ---------------------------------------------------------------------------
echo "Test 10: vitest 'Failed to load url' (import crash)"
output=$(cat <<'VITEST_OUTPUT'
 × should boot (2 ms)

 FAIL  fr3-unknown-error.scenarios.test.ts > should boot
Caused by: Error: Failed to load url ../../../apps/api/src/server.js (resolved id: ../../../apps/api/src/server.js) in /project/.dark-factory/scenarios/spec/fr3-unknown-error.scenarios.test.ts. Does the file exist?

 Tests  1 failed | 0 passed
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 1)
assert_eq "vitest 'Failed to load url' detected as crash" "1" "$crashed"

# ---------------------------------------------------------------------------
# Test 11: assertion message that QUOTES "Cannot find module" must NOT crash
# (false-positive guard for the relaxed pattern).
# ---------------------------------------------------------------------------
echo "Test 11: assertion quoting 'Cannot find module' (not a crash)"
output=$(cat <<'VITEST_OUTPUT'
 × error message check (1 ms)

 FAIL  01.test.ts > error message check
AssertionError: expected 'boom' to contain 'Cannot find module'

 Tests  1 failed | 0 passed
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 1)
assert_eq "assertion quoting 'Cannot find module' not a crash" "0" "$crashed"

# ---------------------------------------------------------------------------
# Test 12: assertion quoting 'Failed to resolve import' must NOT crash — locks
# the boundary on the broadest (non-type-anchored) regex alternative (Neo).
# ---------------------------------------------------------------------------
echo "Test 12: assertion quoting 'Failed to resolve import' (not a crash)"
output=$(cat <<'VITEST_OUTPUT'
 × import error message (1 ms)

 FAIL  01.test.ts > import error message
AssertionError: expected error.message to be 'Failed to resolve import "x"'
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 1)
assert_eq "assertion quoting 'Failed to resolve import' not a crash" "0" "$crashed"

# ---------------------------------------------------------------------------
# Test 13: Node ESM bracket form "Error [ERR_MODULE_NOT_FOUND]: ..." (Codex) —
# node emits the error code in brackets, which the bare ERR_MODULE_NOT_FOUND
# alternative didn't catch.
# ---------------------------------------------------------------------------
echo "Test 13: 'Error [ERR_MODULE_NOT_FOUND]:' bracket form (import crash)"
output=$(cat <<'VITEST_OUTPUT'
 × loads esm dep (2 ms)

 FAIL  01.test.ts > loads esm dep
Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@project/shared' imported from /app/01.test.ts
VITEST_OUTPUT
)
crashed=$(classify_crashes "$output" 1)
assert_eq "'Error [ERR_MODULE_NOT_FOUND]:' detected as crash" "1" "$crashed"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
