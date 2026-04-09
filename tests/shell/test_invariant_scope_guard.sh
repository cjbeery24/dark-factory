#!/usr/bin/env bash
# tests/shell/test_invariant_scope_guard.sh
#
# Phase 4.6b.1g: _assert_scenario_count_invariant must skip the
# smoke-test baseline (SMOKE_TEST=true) and the iter0 baseline.
# Otherwise it compares smoke-test.env (absent key) against
# test-results-iter0.json (may contain failing rows from the
# pre-impl baseline) and spuriously fails.  Run 11 hit this shape:
# telemetry line showed reported 0/0, parsed 5.
#
# Usage: bash tests/shell/test_invariant_scope_guard.sh
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
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label"
        echo "    expected: $expected"
        echo "    actual:   $actual"
        FAIL=$((FAIL + 1))
    fi
}

# Source the quality-gate.sh function definitions.  The script needs
# SPEC_NAME and does I/O relative to CWD, so cd into a temp dir first.
setup_env() {
    local tmp="$1"
    cd "$tmp"
    mkdir -p .dark-factory/reports .dark-factory/audit
    export SPEC_NAME="test-spec"
    # Prevent the script's main body from running on source.
    export DF_TEST_SOURCE_ONLY=1
    GATE_FAILED=false
}

# Extract just the function we need instead of sourcing the whole
# script (which executes top-level code).  The function is self-
# contained except for SPEC_NAME and GATE_FAILED.
extract_function() {
    awk '/^_assert_scenario_count_invariant\(\)/,/^}$/' \
        "$PROJECT_ROOT/scripts/quality-gate.sh"
}

FUNC_BODY="$(extract_function)"

# ---------- Test 1: smoke mode skip ----------
echo ""
echo "Test 1: SMOKE_TEST=true is skipped (no telemetry line, no FATAL)"
TMP1="$TMPDIR_BASE/t1"
mkdir -p "$TMP1"
setup_env "$TMP1"
# Create a mismatching state that WOULD fail invariant if not skipped.
printf '[{"name":"t","status": "failed"}]\n' \
    > ".dark-factory/reports/${SPEC_NAME}-test-results-iter0.json"
# No scenario-counts-executable.env => reported_failed=0, parsed=1 => mismatch.
SMOKE_TEST=true
GATE_FAILED=false
eval "$FUNC_BODY"
OUTPUT=$(_assert_scenario_count_invariant 0 2>&1)
RET=$?
assert_eq "smoke mode returns 0"            "0" "$RET"
assert_eq "smoke mode GATE_FAILED untouched" "false" "$GATE_FAILED"
if [[ ! -f .dark-factory/audit/gate-telemetry.jsonl ]]; then
    assert_eq "smoke mode writes no telemetry" "true" "true"
else
    assert_eq "smoke mode writes no telemetry" "true" "false"
fi
unset SMOKE_TEST
cd "$PROJECT_ROOT"

# ---------- Test 2: iter0 skip even outside smoke ----------
echo ""
echo "Test 2: iteration=0 is skipped (pre-impl baseline)"
TMP2="$TMPDIR_BASE/t2"
mkdir -p "$TMP2"
setup_env "$TMP2"
printf '[{"name":"t","status": "failed"}]\n' \
    > ".dark-factory/reports/${SPEC_NAME}-test-results-iter0.json"
eval "$FUNC_BODY"
OUTPUT=$(_assert_scenario_count_invariant 0 2>&1)
RET=$?
assert_eq "iter0 returns 0"            "0" "$RET"
assert_eq "iter0 GATE_FAILED untouched" "false" "$GATE_FAILED"
if [[ ! -f .dark-factory/audit/gate-telemetry.jsonl ]]; then
    assert_eq "iter0 writes no telemetry" "true" "true"
else
    assert_eq "iter0 writes no telemetry" "true" "false"
fi
cd "$PROJECT_ROOT"

# ---------- Test 3: iter1 mismatch still FATAL (regression guard) ----------
echo ""
echo "Test 3: iteration=1 with mismatch still fires FATAL"
TMP3="$TMPDIR_BASE/t3"
mkdir -p "$TMP3"
setup_env "$TMP3"
printf 'SCENARIO_FAILED=0\nSCENARIO_TOTAL=34\n' \
    > ".dark-factory/reports/${SPEC_NAME}-scenario-counts-executable.env"
printf '[{"name":"a","status": "failed"},{"name":"b","status": "failed"}]\n' \
    > ".dark-factory/reports/${SPEC_NAME}-test-results-iter1.json"
eval "$FUNC_BODY"
_assert_scenario_count_invariant 1 >/dev/null 2>&1
RET=$?
assert_eq "iter1 mismatch returns 1" "1" "$RET"
assert_eq "iter1 mismatch sets GATE_FAILED" "true" "$GATE_FAILED"
assert_eq "iter1 writes telemetry" "true" \
    "$([[ -f .dark-factory/audit/gate-telemetry.jsonl ]] && echo true || echo false)"
cd "$PROJECT_ROOT"

# ---------- Test 4: iter1 agreement passes ----------
echo ""
echo "Test 4: iteration=1 with agreement passes"
TMP4="$TMPDIR_BASE/t4"
mkdir -p "$TMP4"
setup_env "$TMP4"
printf 'SCENARIO_FAILED=1\nSCENARIO_TOTAL=34\n' \
    > ".dark-factory/reports/${SPEC_NAME}-scenario-counts-executable.env"
printf '[{"name":"a","status": "failed"},{"name":"b","status":"passed"}]\n' \
    > ".dark-factory/reports/${SPEC_NAME}-test-results-iter1.json"
eval "$FUNC_BODY"
_assert_scenario_count_invariant 1 >/dev/null 2>&1
RET=$?
assert_eq "iter1 agreement returns 0" "0" "$RET"
assert_eq "iter1 agreement GATE_FAILED=false" "false" "$GATE_FAILED"
cd "$PROJECT_ROOT"

echo ""
echo "Results: $PASS passed, $FAIL failed"
[[ "$FAIL" -eq 0 ]]
