#!/usr/bin/env bash
# tests/shell/test_parse_scenario_counts_vitest.sh
#
# Phase 4.6b.1d Finding 1 regression: _parse_scenario_counts must correctly
# parse vitest summary lines in every ordering of failed/passed/skipped,
# especially failure-only runs like Run 9's:
#
#   Tests  6 failed | 35 skipped (41)
#
# The old parser only matched "Tests:? N passed" and silently dropped
# failure-only runs into the binary fallback (total=1, failed=1).
#
# Usage: bash tests/shell/test_parse_scenario_counts_vitest.sh
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

# Run _parse_scenario_counts against a fixture and return the resulting env
# fragment's SCENARIO_PASSED/SCENARIO_FAILED/SCENARIO_TOTAL.
run_parser() {
    local raw="$1"
    local workdir
    workdir=$(mktemp -d)
    mkdir -p "$workdir/.dark-factory/reports"
    (
        cd "$workdir"
        SPEC_NAME="fixture" \
        GATE_FAILED="true" \
        SMOKE_TEST="false" \
        bash -c "
            source '$PROJECT_ROOT/scripts/quality-gate.sh' 2>/dev/null || true
            # sourcing quality-gate.sh runs the whole script body; we need to
            # cherry-pick the function.  Use a small shim instead.
        " 2>/dev/null || true
    )
    rm -rf "$workdir"
}

# Direct-extraction approach: instead of sourcing the whole gate script,
# extract _parse_scenario_counts and eval it in a controlled scope.
extract_fn() {
    awk '/^_parse_scenario_counts\(\) \{/,/^\}/' "$PROJECT_ROOT/scripts/quality-gate.sh"
}

parser_output() {
    local raw="$1"
    local workdir
    workdir=$(mktemp -d)
    mkdir -p "$workdir/.dark-factory/reports"
    (
        cd "$workdir"
        SPEC_NAME="fixture"
        GATE_FAILED="true"
        SMOKE_TEST="false"
        eval "$(extract_fn)"
        _parse_scenario_counts "$raw" >/dev/null 2>&1 || true
        cat ".dark-factory/reports/fixture-scenario-counts-executable.env" 2>/dev/null || echo ""
    )
    rm -rf "$workdir"
}

get_key() {
    local env_output="$1" key="$2"
    echo "$env_output" | grep "^${key}=" | cut -d= -f2
}

echo "=== _parse_scenario_counts vitest regression (4.6b.1d Finding 1) ==="
echo ""

# ---------------------------------------------------------------------------
# Fixture 1: Run 9 shape — failure-only with skipped tests.
# This is the exact line from Run 9's quality-gate.md, ANSI stripped.
# ---------------------------------------------------------------------------
RUN9_FIXTURE=" FAIL  .dark-factory/scenarios/fixture/server-startup.test.ts > Server Startup and Configuration > [AC-2] exits with code 1 when PORT is not a valid number
AssertionError: expected 2 to be 1 // Object.is equality
 Test Files  12 failed (12)
      Tests  6 failed | 35 skipped (41)
   Start at  00:09:12
   Duration  652ms"

out=$(parser_output "$RUN9_FIXTURE")
assert_eq "Run 9 shape: SCENARIO_FAILED=6" "6" "$(get_key "$out" SCENARIO_FAILED)"
assert_eq "Run 9 shape: SCENARIO_PASSED=0" "0" "$(get_key "$out" SCENARIO_PASSED)"
# SCENARIO_TOTAL = passed + failed under current semantics (skipped not counted)
assert_eq "Run 9 shape: SCENARIO_TOTAL=6"  "6" "$(get_key "$out" SCENARIO_TOTAL)"

# ---------------------------------------------------------------------------
# Fixture 2: classic all-pass.
# ---------------------------------------------------------------------------
ALLPASS_FIXTURE=" Test Files  3 passed (3)
      Tests  12 passed (12)"

out=$(parser_output "$ALLPASS_FIXTURE")
assert_eq "all pass: SCENARIO_PASSED=12" "12" "$(get_key "$out" SCENARIO_PASSED)"
assert_eq "all pass: SCENARIO_FAILED=0"  "0"  "$(get_key "$out" SCENARIO_FAILED)"

# ---------------------------------------------------------------------------
# Fixture 3: mixed failed + passed.
# ---------------------------------------------------------------------------
MIXED_FIXTURE=" Test Files  1 failed | 2 passed (3)
      Tests  3 failed | 9 passed (12)"

out=$(parser_output "$MIXED_FIXTURE")
assert_eq "mixed: SCENARIO_PASSED=9" "9" "$(get_key "$out" SCENARIO_PASSED)"
assert_eq "mixed: SCENARIO_FAILED=3" "3" "$(get_key "$out" SCENARIO_FAILED)"

# ---------------------------------------------------------------------------
# Fixture 4: failed | skipped | passed ordering.
# ---------------------------------------------------------------------------
THREEWAY_FIXTURE="      Tests  2 failed | 1 skipped | 7 passed (10)"

out=$(parser_output "$THREEWAY_FIXTURE")
assert_eq "three-way: SCENARIO_PASSED=7" "7" "$(get_key "$out" SCENARIO_PASSED)"
assert_eq "three-way: SCENARIO_FAILED=2" "2" "$(get_key "$out" SCENARIO_FAILED)"

echo ""
echo "=== Results ==="
echo "Passed: $PASS"
echo "Failed: $FAIL"
[[ "$FAIL" -eq 0 ]] && exit 0 || exit 1
