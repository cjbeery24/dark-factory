#!/usr/bin/env bash
# tests/shell/test_write_per_test_results_duration_strip.sh
#
# Phase 4.6b.1g Finding 3 (Run 11 regression): _write_per_test_results
# must strip trailing duration from every format vitest/jest emits, not
# just "(N ms)".  Run 11 captured "... 2028ms" / "2027ms" / "2029ms" in
# iter1/2/3 and drifted through detect_stable_failures's set
# intersection, silently defeating plateau detection.
#
# Usage: bash tests/shell/test_write_per_test_results_duration_strip.sh

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
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label"
        echo "    expected: $expected"
        echo "    actual:   $actual"
        FAIL=$((FAIL + 1))
    fi
}

# Extract the inline sed used by _write_per_test_results.  We test the
# exact regex, not a paraphrase, to catch drift.
SED_EXPR='s/[[:space:]]*\(?[0-9]+[[:space:]]*m?s\)?[[:space:]]*$//'

strip() {
    echo "$1" | sed -E "$SED_EXPR"
}

run_case() {
    local label="$1" input="$2" expected="$3"
    local actual
    actual=$(strip "$input")
    assert_eq "$label" "$expected" "$actual"
}

echo ""
echo "Vitest bare suffix (Run 11 regression shape):"
run_case "vitest 2028ms" \
    "Server startup > [AC-1] starts successfully 2028ms" \
    "Server startup > [AC-1] starts successfully"
run_case "vitest 1ms" \
    "health check returns 200 1ms" \
    "health check returns 200"
run_case "vitest 12345ms" \
    "slow integration test 12345ms" \
    "slow integration test"

echo ""
echo "Jest parenthesized suffix (legacy coverage):"
run_case "jest (5 ms)" \
    "auth middleware rejects invalid tokens (5 ms)" \
    "auth middleware rejects invalid tokens"
run_case "jest (123 ms)" \
    "db query pagination (123 ms)" \
    "db query pagination"
run_case "jest (2 s)" \
    "long running scenario (2 s)" \
    "long running scenario"

echo ""
echo "Vitest parenthesized with no space:"
run_case "vitest (5ms)" \
    "renders list (5ms)" \
    "renders list"

echo ""
echo "Drift invariance (iter1/iter2/iter3 must collapse to same name):"
iter1=$(strip "Server startup > [AC-1] starts successfully 2028ms")
iter2=$(strip "Server startup > [AC-1] starts successfully 2027ms")
iter3=$(strip "Server startup > [AC-1] starts successfully 2029ms")
assert_eq "iter1 == iter2" "$iter1" "$iter2"
assert_eq "iter2 == iter3" "$iter2" "$iter3"
assert_eq "iter1 == iter3" "$iter1" "$iter3"

echo ""
echo "No false strip on legitimate test names:"
run_case "no digits" \
    "handles empty input gracefully" \
    "handles empty input gracefully"
run_case "embedded number not at end" \
    "rejects 404 errors in middleware" \
    "rejects 404 errors in middleware"
run_case "ends in non-s letter" \
    "validates password complexity" \
    "validates password complexity"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
