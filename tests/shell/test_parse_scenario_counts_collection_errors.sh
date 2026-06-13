#!/usr/bin/env bash
# tests/shell/test_parse_scenario_counts_collection_errors.sh
#
# Sprint 25 A0: _parse_scenario_counts must capture pytest COLLECTION errors
# into SCENARIO_COLLECTION_ERRORS. A module-scope import side effect (e.g.
# `create_engine(DATABASE_URL)` that throws on a missing driver) makes every
# test in a file fail to COLLECT — pytest prints "Interrupted: N errors during
# collection" and "... N errors in Xs". These are neither passed/failed/skipped
# and were previously DISCARDED, so a mass collection drop masqueraded as a
# tiny passing gate (or a fabricated 1-failed via the binary fallback).
#
# Usage: bash tests/shell/test_parse_scenario_counts_collection_errors.sh
# Exit:  0 on all pass, 1 on any failure.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0

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

echo "=== _parse_scenario_counts collection-error capture (Sprint 25 A0) ==="
echo ""

# ---------------------------------------------------------------------------
# Fixture 1: pure collection failure (a13bfc6f shape) — 9 files all fail to
# import. pytest "Interrupted" + summary trailer. Real literal phrasing.
# ---------------------------------------------------------------------------
PURE_COLLECTION="ImportError while importing test module 'test_create.py'.
src/app/database.py:13: in <module>
    engine = create_engine(DATABASE_URL)
E   ModuleNotFoundError: No module named 'psycopg2'
!!!!!!!!!!!!!!!!!!! Interrupted: 9 errors during collection !!!!!!!!!!!!!!!!!!!!
========================= 1 warning, 9 errors in 0.36s ========================="
out=$(parser_output "$PURE_COLLECTION")
assert_eq "pure collection: SCENARIO_COLLECTION_ERRORS=9" "9" "$(get_key "$out" SCENARIO_COLLECTION_ERRORS)"

# ---------------------------------------------------------------------------
# Fixture 2: self-tests shape — 2 errors during collection.
# ---------------------------------------------------------------------------
TWO_ERRORS="!!!!!!!!!!!!!!!!!!! Interrupted: 2 errors during collection !!!!!!!!!!!!!!!!!!!!
============================== 2 errors in 0.20s ==============================="
out=$(parser_output "$TWO_ERRORS")
assert_eq "two errors: SCENARIO_COLLECTION_ERRORS=2" "2" "$(get_key "$out" SCENARIO_COLLECTION_ERRORS)"

# ---------------------------------------------------------------------------
# Fixture 3: mixed run — some tests pass, some files error at collection.
# Summary trailer "N passed, M errors in Xs" (no Interrupted line).
# ---------------------------------------------------------------------------
MIXED="test_ok.py::test_a PASSED
==================== 5 passed, 3 errors in 1.20s ===================="
out=$(parser_output "$MIXED")
assert_eq "mixed: SCENARIO_COLLECTION_ERRORS=3" "3" "$(get_key "$out" SCENARIO_COLLECTION_ERRORS)"
assert_eq "mixed: SCENARIO_PASSED=5 (unchanged)" "5" "$(get_key "$out" SCENARIO_PASSED)"

# ---------------------------------------------------------------------------
# Fixture 4: clean all-pass — no collection errors.
# ---------------------------------------------------------------------------
CLEAN="==================== 30 passed in 2.10s ===================="
out=$(parser_output "$CLEAN")
assert_eq "clean: SCENARIO_COLLECTION_ERRORS=0" "0" "$(get_key "$out" SCENARIO_COLLECTION_ERRORS)"
assert_eq "clean: SCENARIO_PASSED=30 (unchanged)" "30" "$(get_key "$out" SCENARIO_PASSED)"

# ---------------------------------------------------------------------------
# Fixture 5: single error (singular "error", no Interrupted) — must match
# "error" as well as "errors".
# ---------------------------------------------------------------------------
SINGLE="==================== 1 error in 0.05s ===================="
out=$(parser_output "$SINGLE")
assert_eq "single: SCENARIO_COLLECTION_ERRORS=1" "1" "$(get_key "$out" SCENARIO_COLLECTION_ERRORS)"

# ---------------------------------------------------------------------------
# Fixture 6: FALSE-POSITIVE GUARD (gate-review finding). A failing run whose
# captured assertion PROSE contains "N errors in Xs" but is NOT the pytest
# summary rule line. The real summary "1 failed in Xs" carries no error count.
# Must NOT count the prose → SCENARIO_COLLECTION_ERRORS=0.
# ---------------------------------------------------------------------------
PROSE_FP="FAILED test_x.py::test_handler - AssertionError: expected 5 errors in 3.2s window
==================== 1 failed in 0.40s ===================="
out=$(parser_output "$PROSE_FP")
assert_eq "prose false-positive guard: SCENARIO_COLLECTION_ERRORS=0" "0" "$(get_key "$out" SCENARIO_COLLECTION_ERRORS)"

# ---------------------------------------------------------------------------
# Fixture 7: prose containing "errors in Xs" AFTER which a REAL =-wrapped
# summary appears — the anchored summary must win, not the prose.
# ---------------------------------------------------------------------------
PROSE_THEN_SUMMARY="note: saw 7 errors in 9.9s earlier in a log line
==================== 2 passed, 4 errors in 0.80s ===================="
out=$(parser_output "$PROSE_THEN_SUMMARY")
assert_eq "prose-then-summary: SCENARIO_COLLECTION_ERRORS=4 (summary wins)" "4" "$(get_key "$out" SCENARIO_COLLECTION_ERRORS)"

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="
[[ "$FAIL" -eq 0 ]]
