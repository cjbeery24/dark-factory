#!/usr/bin/env bash
set -euo pipefail

# tests/test-events.sh — Tests for scripts/lib/events.sh (Phase 5)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

setup_tmpdir

# ==============================================================================
# Setup
# ==============================================================================

source "${TESTS_DIR}/../scripts/lib/common.sh"
DF_PROJECT_ROOT="$TMPDIR_TEST/project"
mkdir -p "$DF_PROJECT_ROOT/.dark-factory"

# ==============================================================================
# _df_timestamp
# ==============================================================================

test_begin "timestamp_format"
ts=$(_df_timestamp)
# Should be ISO 8601 format (with or without milliseconds)
if [[ "$ts" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2} ]]; then
    _test_pass "timestamp is ISO 8601 format"
else
    _test_fail "timestamp not ISO 8601: $ts"
fi

# ==============================================================================
# emit_event — basic functionality
# ==============================================================================

test_begin "emit_event_basic"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-basic.jsonl"
_DF_CURRENT_SPEC=""
_DF_RUN_ID=""
_DF_PLAN_RUN_ID=""
_DF_CURRENT_ITER=""

emit_event "TEST_EVENT" "key1=val1" "key2=val2"
assert_file_exists "$_DF_EVENTS_FILE" "events file created"

line=$(cat "$_DF_EVENTS_FILE")
assert_contains "$line" '"event":"TEST_EVENT"' "event name in output"
assert_contains "$line" '"key1":"val1"' "data key1 in output"
assert_contains "$line" '"key2":"val2"' "data key2 in output"

# Validate JSON if jq available
if command -v jq >/dev/null 2>&1; then
    jq -e . "$_DF_EVENTS_FILE" >/dev/null 2>&1
    assert_equals "0" "$?" "output is valid JSON"
fi

# ==============================================================================
# emit_event — context globals
# ==============================================================================

test_begin "emit_event_with_context"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-context.jsonl"
_DF_CURRENT_SPEC="my-spec"
_DF_RUN_ID="run-123"
_DF_PLAN_RUN_ID="plan-456"
_DF_CURRENT_ITER="2"

emit_event "CONTEXT_EVENT" "foo=bar"
line=$(cat "$_DF_EVENTS_FILE")
assert_contains "$line" '"spec":"my-spec"' "spec in output"
assert_contains "$line" '"run_id":"run-123"' "run_id in output"
assert_contains "$line" '"plan_run_id":"plan-456"' "plan_run_id in output"
assert_contains "$line" '"iter":2' "iter in output"

# ==============================================================================
# emit_event — omits unset context globals
# ==============================================================================

test_begin "emit_event_no_context"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-nocontext.jsonl"
_DF_CURRENT_SPEC=""
_DF_RUN_ID=""
_DF_PLAN_RUN_ID=""
_DF_CURRENT_ITER=""

emit_event "BARE_EVENT"
line=$(cat "$_DF_EVENTS_FILE")
assert_not_contains "$line" '"spec"' "no spec when unset"
assert_not_contains "$line" '"run_id"' "no run_id when unset"
assert_not_contains "$line" '"plan_run_id"' "no plan_run_id when unset"
assert_not_contains "$line" '"iter"' "no iter when unset"

# ==============================================================================
# emit_event — special characters
# ==============================================================================

test_begin "emit_event_special_chars"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-special.jsonl"
_DF_CURRENT_SPEC=""
_DF_RUN_ID=""
_DF_PLAN_RUN_ID=""
_DF_CURRENT_ITER=""

emit_event "SPECIAL_EVENT" 'msg=hello "world"' "path=foo\\bar"
if command -v jq >/dev/null 2>&1; then
    jq -e . "$_DF_EVENTS_FILE" >/dev/null 2>&1
    assert_equals "0" "$?" "special chars produce valid JSON"
    val=$(jq -r '.data.msg' "$_DF_EVENTS_FILE")
    assert_equals 'hello "world"' "$val" "double quotes preserved in value"
fi

# ==============================================================================
# emit_event — no-jq fallback
# ==============================================================================

test_begin "emit_event_nojq_fallback"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-nojq.jsonl"
_DF_CURRENT_SPEC="test-spec"
_DF_RUN_ID="run-nojq"
_DF_PLAN_RUN_ID=""
_DF_CURRENT_ITER=""

# Force no-jq path
_DF_HAS_JQ_ORIG=$_DF_HAS_JQ
_DF_HAS_JQ=false
emit_event "NOJQ_EVENT" "key=value"
_DF_HAS_JQ=$_DF_HAS_JQ_ORIG

line=$(cat "$_DF_EVENTS_FILE")
assert_contains "$line" '"event":"NOJQ_EVENT"' "event name in no-jq output"
assert_contains "$line" '"spec":"test-spec"' "spec in no-jq output"
assert_contains "$line" '"key":"value"' "data in no-jq output"

# Verify it's valid JSON if jq available
if command -v jq >/dev/null 2>&1; then
    jq -e . "$_DF_EVENTS_FILE" >/dev/null 2>&1
    assert_equals "0" "$?" "no-jq fallback produces valid JSON"
fi

# ==============================================================================
# emit_event — jq and no-jq produce same schema
# ==============================================================================

test_begin "emit_event_schema_parity"
if command -v jq >/dev/null 2>&1; then
    _DF_CURRENT_SPEC="parity-spec"
    _DF_RUN_ID="parity-run"
    _DF_PLAN_RUN_ID=""
    _DF_CURRENT_ITER="1"

    export _DF_EVENTS_FILE="$TMPDIR_TEST/events-jq.jsonl"
    _DF_HAS_JQ=true
    emit_event "PARITY_TEST" "alpha=one" "beta=two"
    jq_keys=$(jq -c 'keys' "$_DF_EVENTS_FILE")

    export _DF_EVENTS_FILE="$TMPDIR_TEST/events-nojq2.jsonl"
    _DF_HAS_JQ=false
    emit_event "PARITY_TEST" "alpha=one" "beta=two"
    nojq_keys=$(jq -c 'keys' "$_DF_EVENTS_FILE")
    _DF_HAS_JQ=$_DF_HAS_JQ_ORIG

    assert_equals "$jq_keys" "$nojq_keys" "jq and no-jq paths produce same top-level keys"
fi

# ==============================================================================
# Convenience wrappers emit correct event types
# ==============================================================================

test_begin "convenience_wrappers"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-wrappers.jsonl"
_DF_CURRENT_SPEC=""
_DF_RUN_ID=""
_DF_PLAN_RUN_ID=""
_DF_CURRENT_ITER=""

# Test a representative subset of all 23 wrappers
emit_plan_start "plan_name=test"
emit_plan_complete "shipped_count=1"
emit_plan_timeout "wall_s=100"
emit_plan_resume "checkpoint_file=test"
emit_spec_start "spec_hash=abc"
emit_spec_dispatched "worktree_path=/tmp"
emit_spec_shipped "iters=1"
emit_spec_failed "reason=test"
emit_spec_escalated "reason=test"
emit_agent_start "agent_role=test"
emit_agent_completed "agent_role=test"
emit_agent_failed "agent_role=test"
emit_gate_completed "build=pass"
emit_satisfaction_scored "score=90"
emit_trajectory_classified "classification=IMPROVING"
emit_regression_run "spec_count=5"
emit_budget_exceeded "limit_usd=10"
emit_budget_alert "percent=80"
emit_barrier_violation "agent_role=test"
emit_cost_summary "total_turns=100"
emit_anomaly_detected "type=test"
emit_trust_scored "score=75"
emit_iter_cost "iter=1"

emit_files_changed "agent_role=implementer" "files_changed=42"

line_count=$(wc -l < "$_DF_EVENTS_FILE")
assert_equals "24" "$(echo "$line_count" | tr -d ' ')" "all 24 wrappers emitted"

if command -v jq >/dev/null 2>&1; then
    # Verify all are valid JSON
    invalid=0
    while IFS= read -r line; do
        echo "$line" | jq -e . >/dev/null 2>&1 || invalid=$((invalid + 1))
    done < "$_DF_EVENTS_FILE"
    assert_equals "0" "$invalid" "all 24 events are valid JSON"

    # Check specific event types
    types=$(jq -r '.event' "$_DF_EVENTS_FILE" | sort -u | wc -l)
    assert_equals "24" "$(echo "$types" | tr -d ' ')" "all 24 distinct event types"
fi

# ==============================================================================
# emit_files_changed wrapper
# ==============================================================================

test_begin "emit_files_changed_wrapper"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-files-changed.jsonl"
_DF_CURRENT_SPEC=""
_DF_RUN_ID=""
_DF_PLAN_RUN_ID=""
_DF_CURRENT_ITER=""

emit_files_changed "agent_role=implementer" "files_changed=42"
assert_file_exists "$_DF_EVENTS_FILE" "files changed event file created"

line=$(cat "$_DF_EVENTS_FILE")
assert_contains "$line" '"event":"FILES_CHANGED"' "event type is FILES_CHANGED"
assert_contains "$line" '"files_changed":"42"' "files_changed field present"

# ==============================================================================
# Concurrent writes produce valid JSONL
# ==============================================================================

test_begin "concurrent_writes"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-concurrent.jsonl"
_DF_CURRENT_SPEC=""
_DF_RUN_ID=""
_DF_PLAN_RUN_ID=""
_DF_CURRENT_ITER=""

for i in $(seq 1 20); do
    (
        _DF_CURRENT_SPEC="spec-$i"
        emit_event "CONCURRENT_TEST" "index=$i"
    ) &
done
wait

line_count=$(wc -l < "$_DF_EVENTS_FILE")
assert_equals "20" "$(echo "$line_count" | tr -d ' ')" "20 concurrent events written"

if command -v jq >/dev/null 2>&1; then
    invalid=0
    while IFS= read -r line; do
        echo "$line" | jq -e . >/dev/null 2>&1 || invalid=$((invalid + 1))
    done < "$_DF_EVENTS_FILE"
    assert_equals "0" "$invalid" "all concurrent events are valid JSON"
fi

# ==============================================================================
# Missing events directory is created
# ==============================================================================

test_begin "auto_create_dir"
export _DF_EVENTS_FILE="$TMPDIR_TEST/deep/nested/dir/events.jsonl"
_DF_CURRENT_SPEC=""
_DF_RUN_ID=""
_DF_PLAN_RUN_ID=""
_DF_CURRENT_ITER=""

emit_event "MKDIR_TEST"
assert_file_exists "$_DF_EVENTS_FILE" "deeply nested events file created"

# ==============================================================================
# Large values don't corrupt the file
# ==============================================================================

test_begin "large_values"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-large.jsonl"
_DF_CURRENT_SPEC=""
_DF_RUN_ID=""
_DF_PLAN_RUN_ID=""
_DF_CURRENT_ITER=""

big_val=$(head -c 2000 /dev/urandom | base64 | tr -d '\n' | head -c 1500)
emit_event "LARGE_EVENT" "big=$big_val"
if command -v jq >/dev/null 2>&1; then
    jq -e . "$_DF_EVENTS_FILE" >/dev/null 2>&1
    assert_equals "0" "$?" "large value produces valid JSON"
fi

# ==============================================================================

cleanup_tmpdir
test_summary
