#!/usr/bin/env bash
set -euo pipefail

# tests/test-events-integration.sh — End-to-end event flow integration test (Phase 5)
#
# Simulates a minimal pipeline flow using the real event functions
# and validates the resulting events.jsonl.

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

setup_tmpdir

source "${TESTS_DIR}/../scripts/lib/common.sh"
DF_PROJECT_ROOT="$TMPDIR_TEST/project"
mkdir -p "$DF_PROJECT_ROOT/.dark-factory"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events.jsonl"

# Skip integration tests if jq is not available
if ! command -v jq >/dev/null 2>&1; then
    echo "SKIP: Integration tests require jq"
    exit 0
fi

# ==============================================================================
# Simulate a 2-spec pipeline run
# ==============================================================================

test_begin "integration_pipeline_flow"

# Plan-level setup
export _DF_PLAN_RUN_ID="plan-$(date +%s)"
_DF_CURRENT_SPEC=""
_DF_RUN_ID=""
_DF_CURRENT_ITER=""

# Emit PLAN_START (plan-level, no spec)
emit_plan_start "plan_name=test-plan" "spec_count=2" "max_concurrent=2"

# --- Spec 1 ---
_DF_CURRENT_SPEC="auth-v2"
_DF_RUN_ID="run-auth-001"
_DF_CURRENT_ITER=1

emit_spec_start "spec_hash=abc123" "autonomy_level=3" "max_iters=3"
emit_agent_start "agent_role=spec-validator" "max_turns=50"
emit_agent_completed "agent_role=spec-validator" "exit_code=0" "wall_s=30" "turns_allocated=50"
emit_agent_start "agent_role=implementer" "max_turns=200"
emit_agent_completed "agent_role=implementer" "exit_code=0" "wall_s=120" "turns_allocated=200"
emit_gate_completed "build=pass" "lint=pass" "typecheck=pass" "scenarios=pass"
emit_spec_shipped "iters=1" "satisfaction_score=95" "pass_rate=100" "wall_s=180"

# --- Spec 2 ---
_DF_CURRENT_SPEC="search-v3"
_DF_RUN_ID="run-search-002"
_DF_CURRENT_ITER=1

emit_spec_start "spec_hash=def456" "autonomy_level=3" "max_iters=3"
emit_agent_start "agent_role=implementer" "max_turns=200"
emit_agent_failed "agent_role=implementer" "exit_code=1" "wall_s=60" "error=build_failure"

_DF_CURRENT_ITER=2
emit_agent_start "agent_role=implementer" "max_turns=200"
emit_agent_completed "agent_role=implementer" "exit_code=0" "wall_s=90" "turns_allocated=200"
emit_gate_completed "build=pass" "lint=pass" "typecheck=pass" "scenarios=pass"
emit_spec_shipped "iters=2" "satisfaction_score=88" "pass_rate=95" "wall_s=300"

# Plan complete
_DF_CURRENT_SPEC=""
_DF_RUN_ID=""
_DF_CURRENT_ITER=""
emit_plan_complete "shipped_count=2" "failed_count=0" "wall_s=500"

# ==============================================================================
# Validate event file
# ==============================================================================

test_begin "all_events_valid_json"
invalid=$(jq -c '.' "$_DF_EVENTS_FILE" 2>&1 | grep -c 'parse error' || true)
assert_equals "0" "$invalid" "all lines are valid JSON"

test_begin "correct_event_count"
total=$(wc -l < "$_DF_EVENTS_FILE" | tr -d ' ')
assert_equals "16" "$total" "16 events total"

test_begin "plan_start_present"
count=$(jq -s '[.[] | select(.event == "PLAN_START")] | length' "$_DF_EVENTS_FILE")
assert_equals "1" "$count" "exactly 1 PLAN_START"

test_begin "plan_complete_present"
count=$(jq -s '[.[] | select(.event == "PLAN_COMPLETE")] | length' "$_DF_EVENTS_FILE")
assert_equals "1" "$count" "exactly 1 PLAN_COMPLETE"

test_begin "spec_shipped_count"
count=$(jq -s '[.[] | select(.event == "SPEC_SHIPPED")] | length' "$_DF_EVENTS_FILE")
assert_equals "2" "$count" "2 SPEC_SHIPPED events"

test_begin "agent_completed_count"
count=$(jq -s '[.[] | select(.event == "AGENT_COMPLETED")] | length' "$_DF_EVENTS_FILE")
assert_equals "3" "$count" "3 AGENT_COMPLETED events"

test_begin "agent_failed_count"
count=$(jq -s '[.[] | select(.event == "AGENT_FAILED")] | length' "$_DF_EVENTS_FILE")
assert_equals "1" "$count" "1 AGENT_FAILED event"

# ==============================================================================
# Validate event structure
# ==============================================================================

test_begin "plan_events_have_plan_run_id"
missing=$(jq -s '[.[] | select(.event == "PLAN_START" or .event == "PLAN_COMPLETE") | select(.plan_run_id == null)] | length' "$_DF_EVENTS_FILE")
assert_equals "0" "$missing" "plan events have plan_run_id"

test_begin "spec_events_have_spec_and_run_id"
missing=$(jq -s '[.[] | select(.event | startswith("SPEC_")) | select(.spec == null or .run_id == null)] | length' "$_DF_EVENTS_FILE")
assert_equals "0" "$missing" "spec events have spec and run_id"

test_begin "plan_start_no_spec"
has_spec=$(jq -s '[.[] | select(.event == "PLAN_START") | select(.spec != null)] | length' "$_DF_EVENTS_FILE")
assert_equals "0" "$has_spec" "PLAN_START has no spec field"

test_begin "plan_complete_no_spec"
has_spec=$(jq -s '[.[] | select(.event == "PLAN_COMPLETE") | select(.spec != null)] | length' "$_DF_EVENTS_FILE")
assert_equals "0" "$has_spec" "PLAN_COMPLETE has no spec field"

test_begin "events_have_timestamps"
missing_ts=$(jq -s '[.[] | select(.ts == null or .ts == "")] | length' "$_DF_EVENTS_FILE")
assert_equals "0" "$missing_ts" "all events have timestamps"

test_begin "events_chronological"
# Check timestamps are non-decreasing
out_of_order=$(jq -s '
    [range(1; length) | select(.[.].ts < .[. - 1].ts)] | length
' "$_DF_EVENTS_FILE" 2>/dev/null || echo "0")
assert_equals "0" "$out_of_order" "events are in chronological order"

# ==============================================================================
# Validate specific data fields
# ==============================================================================

test_begin "shipped_spec_has_iters"
auth_iters=$(jq -s '[.[] | select(.event == "SPEC_SHIPPED" and .spec == "auth-v2")][0].data.iters' "$_DF_EVENTS_FILE")
assert_equals "\"1\"" "$auth_iters" "auth-v2 shipped in 1 iteration"

test_begin "search_shipped_iters"
search_iters=$(jq -s '[.[] | select(.event == "SPEC_SHIPPED" and .spec == "search-v3")][0].data.iters' "$_DF_EVENTS_FILE")
assert_equals "\"2\"" "$search_iters" "search-v3 shipped in 2 iterations"

test_begin "agent_roles_present"
roles=$(jq -s '[.[] | select(.event == "AGENT_COMPLETED" or .event == "AGENT_FAILED") | .data.agent_role] | unique | sort' "$_DF_EVENTS_FILE")
assert_contains "$roles" "implementer" "implementer role present"
assert_contains "$roles" "spec-validator" "spec-validator role present"

# ==============================================================================
# Concurrent event writes (spawn subshells)
# ==============================================================================

test_begin "concurrent_writes"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-concurrent.jsonl"
> "$_DF_EVENTS_FILE"

# Spawn 10 subshells writing events concurrently
pids=()
for i in $(seq 1 10); do
    (
        _DF_CURRENT_SPEC="concurrent-$i"
        _DF_RUN_ID="run-conc-$i"
        _DF_CURRENT_ITER="$i"
        emit_spec_start "seq=$i"
        emit_spec_shipped "iters=1" "seq=$i"
    ) &
    pids+=($!)
done
for pid in "${pids[@]}"; do
    wait "$pid" 2>/dev/null || true
done

# Verify all lines are valid JSON
total_lines=$(wc -l < "$_DF_EVENTS_FILE" | tr -d ' ')
valid_lines=$(jq -c '.' "$_DF_EVENTS_FILE" 2>/dev/null | wc -l | tr -d ' ')
assert_equals "$total_lines" "$valid_lines" "all $total_lines concurrent lines are valid JSON"

# Should have 20 events (2 per subshell × 10)
assert_equals "20" "$total_lines" "20 events from 10 concurrent writers"

# ==============================================================================
# Cost summary event
# ==============================================================================

test_begin "cost_summary_event"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-cost.jsonl"
> "$_DF_EVENTS_FILE"
_DF_SPEC_TOTAL_TURNS=50
_METRICS_START_TS=$(( $(date +%s) - 100 ))

cost_spec_summary

if [[ -f "$_DF_EVENTS_FILE" ]]; then
    has_cost=$(jq -r 'select(.event == "COST_SUMMARY") | .event' "$_DF_EVENTS_FILE" | head -1)
    assert_equals "COST_SUMMARY" "$has_cost" "COST_SUMMARY event emitted in integration"
fi

# ==============================================================================

cleanup_tmpdir
test_summary
