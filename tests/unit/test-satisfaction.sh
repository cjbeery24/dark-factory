#!/usr/bin/env bash
set -euo pipefail

# tests/test-satisfaction.sh — Tests for scripts/lib/satisfaction.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"
source "${TESTS_DIR}/../scripts/lib/satisfaction.sh"

# ==============================================================================
# _df_layer_weight
# ==============================================================================

test_begin "_df_layer_weight"

assert_equals "100" "$(_df_layer_weight "01-contract.test.ts")" "01 → CONTRACT default 100"
assert_equals "100" "$(_df_layer_weight "02-happy-path.test.ts")" "02 → HAPPY_PATH default 100"
assert_equals "80"  "$(_df_layer_weight "03-edge-cases.test.ts")" "03 → EDGE_CASES default 80"
assert_equals "90"  "$(_df_layer_weight "04-error-conditions.test.ts")" "04 → ERROR_CONDITIONS default 90"
assert_equals "80"  "$(_df_layer_weight "05-state-sequence.test.ts")" "05 → STATE_SEQUENCE default 80"
assert_equals "120" "$(_df_layer_weight "06-security.test.ts")" "06 → SECURITY default 120"
assert_equals "70"  "$(_df_layer_weight "07-property-based.test.ts")" "07 → PROPERTY_BASED default 70"
assert_equals "60"  "$(_df_layer_weight "08-adversarial.test.ts")" "08 → ADVERSARIAL default 60"
assert_equals "100" "$(_df_layer_weight "99-unknown.test.ts")" "unknown prefix → 100"

# Custom env override
export SCENARIO_WEIGHT_SECURITY="200"
assert_equals "200" "$(_df_layer_weight "06-security.test.ts")" "06 → SECURITY custom override 200"
unset SCENARIO_WEIGHT_SECURITY

# ==============================================================================
# _df_multi_run_file
# ==============================================================================

test_begin "_df_multi_run_file"

# Always-pass: N=3, K=2 → rc=0, early exit at K
_df_multi_run_file "true" 3 2
assert_equals "0" "$?" "_mr always-pass rc=0 (implicit)"
assert_equals "2" "$_MR_SUCCESSES" "_mr always-pass successes=2 (early exit at K)"

# Always-fail: N=3, K=2 → rc=1
rc=0
_df_multi_run_file "false" 3 2 || rc=$?
assert_equals "1" "$rc" "_mr always-fail rc=1"
assert_equals "2" "$_MR_FAILURES" "_mr always-fail failures=2 (early exit: K impossible)"

# Partial pass via tmp-file counter: passes first 2 times, fails after
setup_tmpdir
counter_file="$TMPDIR_TEST/counter"
echo "0" > "$counter_file"
# Command: read counter, increment, pass if counter <= 2
partial_cmd="c=\$(cat $counter_file); c=\$((c+1)); echo \$c > $counter_file; [ \$c -le 2 ]"
rc=0
_df_multi_run_file "$partial_cmd" 3 2 || rc=$?
assert_equals "0" "$rc" "_mr partial-pass rc=0 with K=2"
assert_equals "2" "$_MR_SUCCESSES" "_mr partial-pass successes=2"
cleanup_tmpdir

# N=1, K=1 with passing command
_df_multi_run_file "true" 1 1
assert_equals "0" "$?" "_mr N=1 K=1 pass rc=0 (implicit)"

# N=1, K=1 with failing command
rc=0
_df_multi_run_file "false" 1 1 || rc=$?
assert_equals "1" "$rc" "_mr N=1 K=1 fail rc=1"

# Early-exit impossibility: N=3, K=3, always-fail → exits after 1st failure
rc=0
_df_multi_run_file "false" 3 3 || rc=$?
assert_equals "1" "$rc" "_mr impossible K=3 rc=1"
assert_equals "1" "$_MR_FAILURES" "_mr impossible K=3 failures=1 (early exit)"

# ==============================================================================
# _df_classify_trajectory
# ==============================================================================

test_begin "_df_classify_trajectory"

# Single score → INSUFFICIENT
_df_classify_trajectory "50"
assert_equals "INSUFFICIENT" "$_DF_TRAJECTORY_TYPE" "single score → INSUFFICIENT"

# Empty → INSUFFICIENT
_df_classify_trajectory ""
assert_equals "INSUFFICIENT" "$_DF_TRAJECTORY_TYPE" "empty → INSUFFICIENT"

# All deltas positive → IMPROVING
_df_classify_trajectory "50|60|70"
assert_equals "IMPROVING" "$_DF_TRAJECTORY_TYPE" "50|60|70 → IMPROVING"

# All deltas negative → DEGRADING
_df_classify_trajectory "70|60|50"
assert_equals "DEGRADING" "$_DF_TRAJECTORY_TYPE" "70|60|50 → DEGRADING"

# All deltas within ±threshold → PLATEAU
_df_classify_trajectory "50|52|51"
assert_equals "PLATEAU" "$_DF_TRAJECTORY_TYPE" "50|52|51 → PLATEAU"

# Alternating signs → OSCILLATING
_df_classify_trajectory "50|70|50"
assert_equals "OSCILLATING" "$_DF_TRAJECTORY_TYPE" "50|70|50 → OSCILLATING"

# Recovering: last delta positive after previous negative (needs 3+ deltas, non-oscillating)
_df_classify_trajectory "80|70|60|70"
assert_equals "RECOVERING" "$_DF_TRAJECTORY_TYPE" "80|70|60|70 → RECOVERING"

# Mixed: no clear pattern (oscillation check breaks at consecutive same-sign)
_df_classify_trajectory "50|60|50|45"
assert_equals "MIXED" "$_DF_TRAJECTORY_TYPE" "50|60|50|45 → MIXED"

# Empty pipe segments filtered out (exercises [[ -z "$s" ]] && continue)
_df_classify_trajectory "50||70"
assert_equals "IMPROVING" "$_DF_TRAJECTORY_TYPE" "50||70 → IMPROVING (empty segment filtered)"

_df_classify_trajectory "|50|60"
assert_equals "IMPROVING" "$_DF_TRAJECTORY_TYPE" "|50|60 → IMPROVING (leading empty filtered)"

# ==============================================================================
# _df_validate_satisfaction_config
# ==============================================================================

test_begin "_df_validate_satisfaction_config"

# All defaults (unset) → valid
rc=0
_df_validate_satisfaction_config >/dev/null 2>&1 || rc=$?
assert_equals "0" "$rc" "all defaults → rc=0"

# N=0 → invalid
export SCENARIO_MULTI_RUN_N="0"
rc=0
_df_validate_satisfaction_config >/dev/null 2>&1 || rc=$?
assert_equals "1" "$rc" "N=0 → rc=1"
unset SCENARIO_MULTI_RUN_N

# K > N → invalid
export SCENARIO_MULTI_RUN_N="2"
export SCENARIO_MULTI_RUN_K="3"
rc=0
_df_validate_satisfaction_config >/dev/null 2>&1 || rc=$?
assert_equals "1" "$rc" "K>N → rc=1"
unset SCENARIO_MULTI_RUN_N SCENARIO_MULTI_RUN_K

# MIN_SATISFACTION_SCORE=101 → invalid
export MIN_SATISFACTION_SCORE="101"
rc=0
_df_validate_satisfaction_config >/dev/null 2>&1 || rc=$?
assert_equals "1" "$rc" "score=101 → rc=1"
unset MIN_SATISFACTION_SCORE

# Negative weight → invalid
export SCENARIO_WEIGHT_SECURITY="-1"
rc=0
_df_validate_satisfaction_config >/dev/null 2>&1 || rc=$?
assert_equals "1" "$rc" "negative weight → rc=1"
unset SCENARIO_WEIGHT_SECURITY

# Zero weight → invalid (regex ^[1-9][0-9]*$ rejects 0)
export SCENARIO_WEIGHT_SECURITY="0"
rc=0
_df_validate_satisfaction_config >/dev/null 2>&1 || rc=$?
assert_equals "1" "$rc" "zero weight → rc=1"
unset SCENARIO_WEIGHT_SECURITY

# Non-numeric N → invalid
export SCENARIO_MULTI_RUN_N="abc"
rc=0
_df_validate_satisfaction_config >/dev/null 2>&1 || rc=$?
assert_equals "1" "$rc" "non-numeric N → rc=1"
unset SCENARIO_MULTI_RUN_N

# All valid custom values
export SCENARIO_MULTI_RUN_N="5"
export SCENARIO_MULTI_RUN_K="3"
export MIN_SATISFACTION_SCORE="70"
export SCENARIO_WEIGHT_SECURITY="200"
export TRAJECTORY_PLATEAU_THRESHOLD="10"
rc=0
_df_validate_satisfaction_config >/dev/null 2>&1 || rc=$?
assert_equals "0" "$rc" "all valid custom values → rc=0"
unset SCENARIO_MULTI_RUN_N SCENARIO_MULTI_RUN_K MIN_SATISFACTION_SCORE SCENARIO_WEIGHT_SECURITY TRAJECTORY_PLATEAU_THRESHOLD

test_summary
