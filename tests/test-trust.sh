#!/usr/bin/env bash
set -euo pipefail

# tests/test-trust.sh — Tests for scripts/lib/trust.sh (Phase 5)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

setup_tmpdir

source "${TESTS_DIR}/../scripts/lib/common.sh"
DF_PROJECT_ROOT="$TMPDIR_TEST/project"
mkdir -p "$DF_PROJECT_ROOT/.dark-factory/specs"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events.jsonl"

# Helper: reset events file
_reset_events() { : > "$_DF_EVENTS_FILE"; }

# Helper: create a spec file with target-files and depends-on
_create_spec() {
    local name="$1" targets="${2:-2}" deps="${3:-0}"
    local spec_file="$DF_PROJECT_ROOT/.dark-factory/specs/${name}.md"
    {
        echo "---"
        echo "target-files:"
        for i in $(seq 1 "$targets"); do
            echo "- src/file${i}.ts"
        done
        if (( deps > 0 )); then
            echo "depends-on:"
            for i in $(seq 1 "$deps"); do
                echo "- dep-${i}"
            done
        fi
        echo "---"
    } > "$spec_file"
}

# Helper: seed events with N shipped specs (all first-pass)
_seed_shipped_first_pass() {
    local n="$1"
    _reset_events
    for i in $(seq 1 "$n"); do
        echo "{\"event\":\"SPEC_SHIPPED\",\"data\":{\"iters\":\"1\",\"wall_s\":\"300\"}}" >> "$_DF_EVENTS_FILE"
    done
}

# Helper: seed events with mixed results
_seed_mixed_history() {
    _reset_events
    echo '{"event":"SPEC_SHIPPED","data":{"iters":"1","wall_s":"200"}}' >> "$_DF_EVENTS_FILE"
    echo '{"event":"SPEC_SHIPPED","data":{"iters":"1","wall_s":"250"}}' >> "$_DF_EVENTS_FILE"
    echo '{"event":"SPEC_SHIPPED","data":{"iters":"1","wall_s":"180"}}' >> "$_DF_EVENTS_FILE"
    echo '{"event":"SPEC_SHIPPED","data":{"iters":"3","wall_s":"600"}}' >> "$_DF_EVENTS_FILE"
    echo '{"event":"SPEC_SHIPPED","data":{"iters":"3","wall_s":"700"}}' >> "$_DF_EVENTS_FILE"
    echo '{"event":"SPEC_SHIPPED","data":{"iters":"2","wall_s":"450"}}' >> "$_DF_EVENTS_FILE"
    echo '{"event":"REGRESSION_RUN","data":{"failed":"0","passed":"5","spec_count":"5"}}' >> "$_DF_EVENTS_FILE"
    echo '{"event":"REGRESSION_RUN","data":{"failed":"2","passed":"3","spec_count":"5"}}' >> "$_DF_EVENTS_FILE"
    echo '{"event":"REGRESSION_RUN","data":{"failed":"0","passed":"5","spec_count":"5"}}' >> "$_DF_EVENTS_FILE"
}

# ==============================================================================
# trust_calculate — cold start (no history)
# ==============================================================================

test_begin "cold_start_no_history"
_reset_events
result=$(trust_calculate "my-spec")
assert_equals "50" "$result" "returns 50 with no history"

# ==============================================================================
# trust_calculate — cold start (no events file)
# ==============================================================================

test_begin "cold_start_no_jq"
old_events="$_DF_EVENTS_FILE"
unset _DF_EVENTS_FILE
result=$(trust_calculate "my-spec")
assert_equals "50" "$result" "returns 50 without events file"
export _DF_EVENTS_FILE="$old_events"

# ==============================================================================
# trust_calculate — cold start (insufficient history)
# ==============================================================================

test_begin "cold_start_insufficient_history"
_reset_events
echo '{"event":"SPEC_SHIPPED","data":{"iters":"1"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"SPEC_SHIPPED","data":{"iters":"1"}}' >> "$_DF_EVENTS_FILE"
result=$(trust_calculate "my-spec")
assert_equals "50" "$result" "returns 50 with only 2 shipped specs"

# ==============================================================================
# trust_calculate — high score with all first-pass
# ==============================================================================

test_begin "high_score_all_first_pass"
_seed_shipped_first_pass 10
_create_spec "simple-spec" 1 0
result=$(trust_calculate "simple-spec")
if (( result > 75 )); then
    _test_pass "high score ($result) with all first-pass history"
else
    _test_fail "expected >75, got $result"
fi

# ==============================================================================
# trust_calculate — low score with many failures
# ==============================================================================

test_begin "low_score_many_failures"
_reset_events
for i in $(seq 1 6); do
    echo "{\"event\":\"SPEC_SHIPPED\",\"data\":{\"iters\":\"4\",\"wall_s\":\"900\"}}" >> "$_DF_EVENTS_FILE"
done
for i in $(seq 1 5); do
    echo "{\"event\":\"REGRESSION_RUN\",\"data\":{\"failed\":\"3\",\"passed\":\"2\"}}" >> "$_DF_EVENTS_FILE"
done
_create_spec "complex-spec" 8 3
result=$(trust_calculate "complex-spec")
if (( result < 30 )); then
    _test_pass "low score ($result) with many-failure history"
else
    _test_fail "expected <30, got $result"
fi

# ==============================================================================
# _trust_first_pass_rate — correct computation
# ==============================================================================

test_begin "first_pass_rate"
_reset_events
echo '{"event":"SPEC_SHIPPED","data":{"iters":"1"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"SPEC_SHIPPED","data":{"iters":"1"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"SPEC_SHIPPED","data":{"iters":"2"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"SPEC_SHIPPED","data":{"iters":"3"}}' >> "$_DF_EVENTS_FILE"
if command -v jq >/dev/null 2>&1; then
    result=$(_trust_first_pass_rate)
    assert_equals "50" "$result" "2 of 4 first-pass = 50%"
else
    _test_pass "skipped (no jq)"
fi

# ==============================================================================
# _trust_avg_iterations — correct computation
# ==============================================================================

test_begin "avg_iterations"
_reset_events
echo '{"event":"SPEC_SHIPPED","data":{"iters":"1"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"SPEC_SHIPPED","data":{"iters":"2"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"SPEC_SHIPPED","data":{"iters":"3"}}' >> "$_DF_EVENTS_FILE"
if command -v jq >/dev/null 2>&1; then
    result=$(_trust_avg_iterations)
    assert_equals "75" "$result" "avg 2 iters → score 75"
else
    _test_pass "skipped (no jq)"
fi

# ==============================================================================
# _trust_avg_iterations — clamps at boundaries
# ==============================================================================

test_begin "avg_iterations_clamp_low"
_reset_events
for i in $(seq 1 5); do
    echo '{"event":"SPEC_SHIPPED","data":{"iters":"1"}}' >> "$_DF_EVENTS_FILE"
done
if command -v jq >/dev/null 2>&1; then
    result=$(_trust_avg_iterations)
    assert_equals "100" "$result" "avg 1 iter → score 100"
else
    _test_pass "skipped (no jq)"
fi

test_begin "avg_iterations_clamp_high"
_reset_events
for i in $(seq 1 5); do
    echo '{"event":"SPEC_SHIPPED","data":{"iters":"8"}}' >> "$_DF_EVENTS_FILE"
done
if command -v jq >/dev/null 2>&1; then
    result=$(_trust_avg_iterations)
    assert_equals "0" "$result" "avg 8 iters → clamps to 5 → score 0"
else
    _test_pass "skipped (no jq)"
fi

# ==============================================================================
# _trust_regression_fail_rate — correct computation
# ==============================================================================

test_begin "regression_fail_rate"
_reset_events
echo '{"event":"REGRESSION_RUN","data":{"failed":"0","passed":"5"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"REGRESSION_RUN","data":{"failed":"0","passed":"5"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"REGRESSION_RUN","data":{"failed":"2","passed":"3"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"REGRESSION_RUN","data":{"failed":"0","passed":"5"}}' >> "$_DF_EVENTS_FILE"
if command -v jq >/dev/null 2>&1; then
    result=$(_trust_regression_fail_rate)
    assert_equals "75" "$result" "1 of 4 regression failures → 75%"
else
    _test_pass "skipped (no jq)"
fi

# ==============================================================================
# _trust_spec_complexity — scales with target files
# ==============================================================================

test_begin "complexity_scales"
_create_spec "simple" 1 0
result=$(_trust_spec_complexity "simple")
assert_equals "90" "$result" "1 file, 0 deps → complexity 90"

test_begin "complexity_moderate"
_create_spec "moderate" 3 2
result=$(_trust_spec_complexity "moderate")
assert_equals "30" "$result" "3 files, 2 deps → complexity 30"

# ==============================================================================
# _trust_spec_complexity — caps at 100
# ==============================================================================

test_begin "complexity_caps"
_create_spec "huge" 10 5
result=$(_trust_spec_complexity "huge")
assert_equals "0" "$result" "10 files, 5 deps → complexity 0 (capped)"

# ==============================================================================
# _trust_spec_complexity — missing spec returns 50
# ==============================================================================

test_begin "complexity_missing_spec"
result=$(_trust_spec_complexity "nonexistent")
assert_equals "50" "$result" "missing spec → 50"

# ==============================================================================
# _trust_spec_complexity — target-files as last key before --- delimiter
# ==============================================================================

test_begin "complexity_last_key_before_delimiter"
cat > "$DF_PROJECT_ROOT/.dark-factory/specs/last-key.md" <<'EOF'
---
target-files:
- src/a.ts
- src/b.ts
---
Some body text with
- a list item that should not count
EOF
result=$(_trust_spec_complexity "last-key")
# 2 files * 10 + 0 deps * 20 = 20, inverted = 80
assert_equals "80" "$result" "target-files as last YAML key → only 2 files counted"

# ==============================================================================
# _trust_module_familiarity — scales with prior specs
# ==============================================================================

test_begin "familiarity_scales"
_reset_events
for i in $(seq 1 7); do
    echo '{"event":"SPEC_SHIPPED","data":{"iters":"1"}}' >> "$_DF_EVENTS_FILE"
done
_create_spec "fam-test" 1 0
if command -v jq >/dev/null 2>&1; then
    result=$(_trust_module_familiarity "fam-test")
    assert_equals "70" "$result" "7 shipped specs → familiarity 70"
else
    _test_pass "skipped (no jq)"
fi

test_begin "familiarity_caps"
_reset_events
for i in $(seq 1 15); do
    echo '{"event":"SPEC_SHIPPED","data":{"iters":"1"}}' >> "$_DF_EVENTS_FILE"
done
_create_spec "fam-cap" 1 0
if command -v jq >/dev/null 2>&1; then
    result=$(_trust_module_familiarity "fam-cap")
    assert_equals "100" "$result" "15 shipped → capped at 10 → familiarity 100"
else
    _test_pass "skipped (no jq)"
fi

# ==============================================================================
# trust_to_autonomy — mapping
# ==============================================================================

test_begin "autonomy_low"
result=$(trust_to_autonomy 15)
assert_equals "1" "$result" "score 15 → autonomy 1"

test_begin "autonomy_mid"
result=$(trust_to_autonomy 45)
assert_equals "2" "$result" "score 45 → autonomy 2"

test_begin "autonomy_high"
result=$(trust_to_autonomy 65)
assert_equals "3" "$result" "score 65 → autonomy 3"

test_begin "autonomy_very_high"
result=$(trust_to_autonomy 90)
assert_equals "3" "$result" "score 90 → autonomy 3"

# ==============================================================================
# trust_to_autonomy — boundary values
# ==============================================================================

test_begin "autonomy_boundary_30"
result=$(trust_to_autonomy 30)
assert_equals "2" "$result" "score 30 → autonomy 2"

test_begin "autonomy_boundary_60"
result=$(trust_to_autonomy 60)
assert_equals "3" "$result" "score 60 → autonomy 3"

test_begin "autonomy_boundary_80"
result=$(trust_to_autonomy 80)
assert_equals "3" "$result" "score 80 → autonomy 3"

# ==============================================================================
# trust_predict_iterations — no history
# ==============================================================================

test_begin "predict_no_history"
_reset_events
_create_spec "pred-simple" 1 0
result=$(trust_predict_iterations "pred-simple")
assert_equals "1" "$result" "simple spec, no history → predict 1"

test_begin "predict_complex_no_history"
_create_spec "pred-complex" 8 3
_reset_events
result=$(trust_predict_iterations "pred-complex")
assert_equals "3" "$result" "complex spec, no history → predict 3"

# ==============================================================================
# trust_predict_iterations — with history
# ==============================================================================

test_begin "predict_with_history"
_reset_events
echo '{"event":"SPEC_SHIPPED","data":{"iters":"2"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"SPEC_SHIPPED","data":{"iters":"2"}}' >> "$_DF_EVENTS_FILE"
echo '{"event":"SPEC_SHIPPED","data":{"iters":"3"}}' >> "$_DF_EVENTS_FILE"
_create_spec "pred-hist" 3 0
if command -v jq >/dev/null 2>&1; then
    result=$(trust_predict_iterations "pred-hist")
    assert_equals "2" "$result" "historical avg ~2.3 → predict 2"
else
    _test_pass "skipped (no jq)"
fi

# ==============================================================================
# All dimension functions return integers
# ==============================================================================

test_begin "dimensions_return_integers"
_seed_mixed_history
_create_spec "int-test" 2 1
if command -v jq >/dev/null 2>&1; then
    for func in _trust_first_pass_rate _trust_avg_iterations _trust_regression_fail_rate; do
        result=$($func)
        if [[ "$result" =~ ^[0-9]+$ ]]; then
            _test_pass "$func returns integer ($result)"
        else
            _test_fail "$func returned non-integer: $result"
        fi
    done
    for func_spec in _trust_spec_complexity _trust_module_familiarity; do
        result=$($func_spec "int-test")
        if [[ "$result" =~ ^[0-9]+$ ]]; then
            _test_pass "$func_spec returns integer ($result)"
        else
            _test_fail "$func_spec returned non-integer: $result"
        fi
    done
else
    _test_pass "skipped (no jq)"
fi

# ==============================================================================
# Empty events file — graceful handling
# ==============================================================================

test_begin "empty_events_file"
_reset_events
_create_spec "empty-test" 2 0
result=$(trust_calculate "empty-test")
assert_equals "50" "$result" "empty events → cold start 50"

# ==============================================================================

cleanup_tmpdir
test_summary
