#!/usr/bin/env bash
set -euo pipefail

# tests/test-scenarios.sh — Tests for scripts/lib/scenarios.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"
source "${TESTS_DIR}/../scripts/lib/scenarios.sh"

setup_tmpdir

# ==============================================================================
# has_scenario_files
# ==============================================================================

test_begin "has_scenario_files"

# Dir with .test.ts file
mkdir -p "$TMPDIR_TEST/ts-scenarios"
touch "$TMPDIR_TEST/ts-scenarios/auth.test.ts"
assert_exit_code 0 "detects *.test.ts" has_scenario_files "$TMPDIR_TEST/ts-scenarios"

# Dir with .spec.js file
mkdir -p "$TMPDIR_TEST/js-scenarios"
touch "$TMPDIR_TEST/js-scenarios/api.spec.js"
assert_exit_code 0 "detects *.spec.js" has_scenario_files "$TMPDIR_TEST/js-scenarios"

# Dir with test_*.py file
mkdir -p "$TMPDIR_TEST/py-scenarios"
touch "$TMPDIR_TEST/py-scenarios/test_auth.py"
assert_exit_code 0 "detects test_*.py" has_scenario_files "$TMPDIR_TEST/py-scenarios"

# Dir with *_test.py file
mkdir -p "$TMPDIR_TEST/py-scenarios2"
touch "$TMPDIR_TEST/py-scenarios2/auth_test.py"
assert_exit_code 0 "detects *_test.py" has_scenario_files "$TMPDIR_TEST/py-scenarios2"

# Dir with *_test.go file
mkdir -p "$TMPDIR_TEST/go-scenarios"
touch "$TMPDIR_TEST/go-scenarios/auth_test.go"
assert_exit_code 0 "detects *_test.go" has_scenario_files "$TMPDIR_TEST/go-scenarios"

# Dir with .rs file
mkdir -p "$TMPDIR_TEST/rust-scenarios"
touch "$TMPDIR_TEST/rust-scenarios/test_auth.rs"
assert_exit_code 0 "detects *.rs" has_scenario_files "$TMPDIR_TEST/rust-scenarios"

# Empty dir — no scenarios
mkdir -p "$TMPDIR_TEST/empty-dir"
assert_exit_code 1 "returns 1 for empty dir" has_scenario_files "$TMPDIR_TEST/empty-dir"

# Dir with only .DS_Store
mkdir -p "$TMPDIR_TEST/ds-store-dir"
touch "$TMPDIR_TEST/ds-store-dir/.DS_Store"
assert_exit_code 1 "returns 1 for dir with only .DS_Store" has_scenario_files "$TMPDIR_TEST/ds-store-dir"

# Dir with only .gitkeep
mkdir -p "$TMPDIR_TEST/gitkeep-dir"
touch "$TMPDIR_TEST/gitkeep-dir/.gitkeep"
assert_exit_code 1 "returns 1 for dir with only .gitkeep" has_scenario_files "$TMPDIR_TEST/gitkeep-dir"

# Dir with non-test file
mkdir -p "$TMPDIR_TEST/non-test-dir"
touch "$TMPDIR_TEST/non-test-dir/README.md"
assert_exit_code 1 "returns 1 for dir with only README.md" has_scenario_files "$TMPDIR_TEST/non-test-dir"

cleanup_tmpdir

# ==============================================================================
# validate_spec_result
# ==============================================================================

test_begin "validate_spec_result"

# validate_spec_result uses relative paths (.dark-factory/reviews/...) so we
# cd into a temp directory that mimics the project root.
setup_tmpdir
_ORIG_DIR="$(pwd)"
cd "$TMPDIR_TEST"
_REVIEWS="$TMPDIR_TEST/.dark-factory/reviews"
mkdir -p "$_REVIEWS"

_write_status() {
    local spec="$1" score="$2" verdict="$3" divergence="${4:-}"
    {
        echo "spec=$spec"
        echo "date=2026-03-03"
        echo "score=$score"
        echo "verdict=$verdict"
        if [[ -n "$divergence" ]]; then echo "divergence=$divergence"; fi
    } > "$_REVIEWS/${spec}-validation.status"
}

_write_report() {
    local spec="$1" score="$2" verdict="$3" divergence="${4:-}"
    cat > "$_REVIEWS/${spec}-validation.md" <<EOF
# Spec Validation Report
## Completeness Score: ${score}/100
**Verdict:** $verdict
$([ -n "$divergence" ] && echo "**Divergence Rating:** $divergence")
EOF
}

# --- Baseline tests (score/verdict gates) ---

# score=100, verdict=PASS, divergence=LOW -> passes
_write_status "test-pass" 100 "PASS" "LOW"
SPEC_NAME="test-pass"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "0" "$_vsr_ec" "score=100 + PASS + LOW passes"
assert_contains "$_vsr_out" "divergence=LOW" "success message includes divergence"

# score=90 -> fails
_write_status "test-low-score" 90 "PASS" "LOW"
SPEC_NAME="test-low-score"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "1" "$_vsr_ec" "score=90 fails"
assert_contains "$_vsr_out" "90/100" "low score error mentions score"

# verdict=REVISE -> fails
_write_status "test-revise" 100 "REVISE" "LOW"
SPEC_NAME="test-revise"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "1" "$_vsr_ec" "verdict=REVISE fails"
assert_contains "$_vsr_out" "REVISE" "revise error mentions verdict"

# Markdown fallback (no .status file)
rm -f "$_REVIEWS/test-fallback-validation.status"
_write_report "test-fallback" 100 "PASS" "LOW"
SPEC_NAME="test-fallback"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "0" "$_vsr_ec" "markdown fallback works"
assert_contains "$_vsr_out" "WARNING" "markdown fallback shows WARNING"

# --- Divergence gate tests ---

# LOW divergence passes
_write_status "test-div-low" 100 "PASS" "LOW"
SPEC_NAME="test-div-low"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "0" "$_vsr_ec" "LOW divergence passes"

# MEDIUM divergence passes
_write_status "test-div-med" 100 "PASS" "MEDIUM"
SPEC_NAME="test-div-med"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "0" "$_vsr_ec" "MEDIUM divergence passes"

# HIGH divergence fails
_write_status "test-div-high" 100 "PASS" "HIGH"
SPEC_NAME="test-div-high"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "1" "$_vsr_ec" "HIGH divergence fails"
assert_contains "$_vsr_out" "contradictory implementations" "HIGH error message is informative"

# Missing divergence key fails
_write_status "test-div-missing" 100 "PASS" ""
SPEC_NAME="test-div-missing"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "1" "$_vsr_ec" "missing divergence key fails"
assert_contains "$_vsr_out" "Re-run spec validation" "missing divergence error suggests re-run"

# Divergence fallback from markdown report
rm -f "$_REVIEWS/test-div-md-validation.status"
_write_report "test-div-md" 100 "PASS" "MEDIUM"
SPEC_NAME="test-div-md"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "0" "$_vsr_ec" "divergence parsed from markdown fallback"

# HIGH divergence from markdown fallback also fails
rm -f "$_REVIEWS/test-div-md-high-validation.status"
_write_report "test-div-md-high" 100 "PASS" "HIGH"
SPEC_NAME="test-div-md-high"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "1" "$_vsr_ec" "HIGH divergence from markdown also fails"

# Case-insensitive divergence parsing
_write_status "test-div-case" 100 "PASS" "low"
SPEC_NAME="test-div-case"
_vsr_out=$(validate_spec_result 2>&1) && _vsr_ec=0 || _vsr_ec=$?
assert_equals "0" "$_vsr_ec" "lowercase divergence accepted"

cd "$_ORIG_DIR"
cleanup_tmpdir
test_summary
