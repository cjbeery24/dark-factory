#!/usr/bin/env bash
set -euo pipefail

# tests/test-cost.sh — Tests for scripts/lib/cost.sh (Phase 5)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

setup_tmpdir

source "${TESTS_DIR}/../scripts/lib/common.sh"
DF_PROJECT_ROOT="$TMPDIR_TEST/project"
mkdir -p "$DF_PROJECT_ROOT/.dark-factory"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events.jsonl"

# ==============================================================================
# cost_estimate_from_turns — default rates
# ==============================================================================

test_begin "estimate_default_rates"
result=$(cost_estimate_from_turns 10)
# 10 * 4000 * 0.000015 + 10 * 2000 * 0.000075 = 0.6 + 1.5 = 2.10
assert_equals "2.10" "$result" "10 turns at default rates = \$2.10"

# ==============================================================================
# cost_estimate_from_turns — custom rates
# ==============================================================================

test_begin "estimate_custom_rates"
COST_PER_INPUT_TOKEN="0.00003"
COST_PER_OUTPUT_TOKEN="0.00015"
COST_EST_INPUT_TOKENS_PER_TURN="5000"
COST_EST_OUTPUT_TOKENS_PER_TURN="3000"
result=$(cost_estimate_from_turns 5)
# 5 * 5000 * 0.00003 + 5 * 3000 * 0.00015 = 0.75 + 2.25 = 3.00
assert_equals "3.00" "$result" "5 turns at custom rates = \$3.00"
# Reset
unset COST_PER_INPUT_TOKEN COST_PER_OUTPUT_TOKEN COST_EST_INPUT_TOKENS_PER_TURN COST_EST_OUTPUT_TOKENS_PER_TURN

# ==============================================================================
# cost_estimate_from_turns — zero turns
# ==============================================================================

test_begin "estimate_zero_turns"
result=$(cost_estimate_from_turns 0)
assert_equals "0.00" "$result" "0 turns = \$0.00"

# ==============================================================================
# _cost_float_gt — comparison
# ==============================================================================

test_begin "float_gt_true"
_cost_float_gt "10.5" "5.3"
assert_equals "0" "$?" "10.5 > 5.3"

test_begin "float_gt_false"
rc=0
_cost_float_gt "3.2" "5.3" || rc=$?
assert_equals "1" "$rc" "3.2 is not > 5.3"

test_begin "float_gt_equal"
rc=0
_cost_float_gt "5.0" "5.0" || rc=$?
assert_equals "1" "$rc" "5.0 is not > 5.0"

# ==============================================================================
# cost_check_budget — under budget returns 0
# ==============================================================================

test_begin "budget_under"
COST_BUDGET_USD="100.00"
_DF_SPEC_TOTAL_TURNS=10
rc=0
cost_check_budget || rc=$?
assert_equals "0" "$rc" "under budget returns 0"
unset COST_BUDGET_USD

# ==============================================================================
# cost_check_budget — unlimited (0) returns 0
# ==============================================================================

test_begin "budget_unlimited"
COST_BUDGET_USD="0"
_DF_SPEC_TOTAL_TURNS=99999
rc=0
cost_check_budget || rc=$?
assert_equals "0" "$rc" "unlimited budget returns 0"
unset COST_BUDGET_USD

# ==============================================================================
# cost_check_budget — over budget returns 1
# ==============================================================================

test_begin "budget_exceeded"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-budget.jsonl"
COST_BUDGET_USD="1.00"
_DF_SPEC_TOTAL_TURNS=1000  # ~$210 estimated
_DF_COST_ALERTED=()
rc=0
cost_check_budget || rc=$?
assert_equals "1" "$rc" "over budget returns 1"

# Check BUDGET_EXCEEDED event was emitted
if command -v jq >/dev/null 2>&1 && [[ -f "$_DF_EVENTS_FILE" ]]; then
    has_exceeded=$(jq -r 'select(.event == "BUDGET_EXCEEDED") | .event' "$_DF_EVENTS_FILE" | head -1)
    assert_equals "BUDGET_EXCEEDED" "$has_exceeded" "BUDGET_EXCEEDED event emitted"
fi
unset COST_BUDGET_USD

# ==============================================================================
# cost_check_budget — alerts fire at correct thresholds
# ==============================================================================

test_begin "budget_alerts"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-alerts.jsonl"
COST_BUDGET_USD="10.00"
COST_ALERT_THRESHOLDS="50,80"
_DF_COST_ALERTED=()

# 30 turns ≈ $6.30 → over 50% of $10, under 80%
_DF_SPEC_TOTAL_TURNS=30
cost_check_budget || true

if command -v jq >/dev/null 2>&1 && [[ -f "$_DF_EVENTS_FILE" ]]; then
    alert_count=$(jq -s '[.[] | select(.event == "BUDGET_ALERT")] | length' "$_DF_EVENTS_FILE")
    assert_equals "1" "$alert_count" "one alert at 50% threshold"
    alert_pct=$(jq -r 'select(.event == "BUDGET_ALERT") | .data.percent' "$_DF_EVENTS_FILE" | head -1)
    assert_equals "50" "$alert_pct" "alert is at 50%"
fi
unset COST_BUDGET_USD COST_ALERT_THRESHOLDS

# ==============================================================================
# cost_check_budget — alerts only fire once (idempotent)
# ==============================================================================

test_begin "budget_alerts_idempotent"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-idempotent.jsonl"
COST_BUDGET_USD="10.00"
_DF_COST_ALERTED=()
_DF_SPEC_TOTAL_TURNS=30  # ~$6.30 → over 50%

cost_check_budget || true
cost_check_budget || true
cost_check_budget || true

if command -v jq >/dev/null 2>&1 && [[ -f "$_DF_EVENTS_FILE" ]]; then
    alert_count=$(jq -s '[.[] | select(.event == "BUDGET_ALERT")] | length' "$_DF_EVENTS_FILE")
    assert_equals "1" "$alert_count" "alert fires only once despite 3 calls"
fi
unset COST_BUDGET_USD

# ==============================================================================
# cost_spec_summary — emits event
# ==============================================================================

test_begin "spec_summary"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-summary.jsonl"
_DF_SPEC_TOTAL_TURNS=50
_METRICS_START_TS=$(( $(date +%s) - 100 ))

cost_spec_summary

if command -v jq >/dev/null 2>&1 && [[ -f "$_DF_EVENTS_FILE" ]]; then
    has_summary=$(jq -r 'select(.event == "COST_SUMMARY") | .event' "$_DF_EVENTS_FILE" | head -1)
    assert_equals "COST_SUMMARY" "$has_summary" "COST_SUMMARY event emitted"
fi

# ==============================================================================

cleanup_tmpdir
test_summary
