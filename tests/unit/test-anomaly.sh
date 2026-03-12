#!/usr/bin/env bash
set -euo pipefail

# tests/test-anomaly.sh — Tests for scripts/lib/anomaly.sh (Phase 5)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

setup_tmpdir

source "${TESTS_DIR}/../scripts/lib/common.sh"
DF_PROJECT_ROOT="$TMPDIR_TEST/project"
mkdir -p "$DF_PROJECT_ROOT/.dark-factory"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events.jsonl"

# ==============================================================================
# anomaly_check_agent — passes below threshold
# ==============================================================================

test_begin "agent_below_threshold"
rc=0
anomaly_check_agent "implementer" 100 300 || rc=$?
assert_equals "0" "$rc" "100 turns < 150 limit passes"

# ==============================================================================
# anomaly_check_agent — blocks on turns exceeded
# ==============================================================================

test_begin "agent_turns_exceeded"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-agent-block.jsonl"
rc=0
anomaly_check_agent "implementer" 200 300 || rc=$?
assert_equals "1" "$rc" "200 turns > 150 limit blocks"

if command -v jq >/dev/null 2>&1 && [[ -f "$_DF_EVENTS_FILE" ]]; then
    severity=$(jq -r '.data.severity' "$_DF_EVENTS_FILE" | head -1)
    assert_equals "block" "$severity" "severity is block"
fi

# ==============================================================================
# anomaly_check_agent — custom threshold
# ==============================================================================

test_begin "agent_custom_threshold"
ANOMALY_MAX_AGENT_TURNS=50
rc=0
anomaly_check_agent "implementer" 60 100 || rc=$?
assert_equals "1" "$rc" "60 turns > 50 custom limit blocks"
unset ANOMALY_MAX_AGENT_TURNS

# ==============================================================================
# anomaly_check_spec — passes below all thresholds
# ==============================================================================

test_begin "spec_below_thresholds"
rc=0
anomaly_check_spec 500 3600 || rc=$?
assert_equals "0" "$rc" "500 turns, 3600s within limits"

# ==============================================================================
# anomaly_check_spec — blocks on total turns exceeded
# ==============================================================================

test_begin "spec_turns_exceeded"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-spec-turns.jsonl"
rc=0
anomaly_check_spec 1000 3600 || rc=$?
assert_equals "1" "$rc" "1000 turns > 800 limit blocks"

# ==============================================================================
# anomaly_check_spec — blocks on total wall-time exceeded
# ==============================================================================

test_begin "spec_wall_exceeded"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-spec-wall.jsonl"
rc=0
anomaly_check_spec 500 8000 || rc=$?
assert_equals "1" "$rc" "8000s > 7200s limit blocks"

# ==============================================================================
# anomaly_compute_baseline — insufficient data
# ==============================================================================

test_begin "baseline_insufficient"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-few.jsonl"
# Write 3 events (need 5 for baseline)
for i in 1 2 3; do
    printf '{"event":"SPEC_SHIPPED","data":{"wall_s":"100"}}\n' >> "$_DF_EVENTS_FILE"
done
rc=0
anomaly_compute_baseline "wall_s" >/dev/null || rc=$?
assert_equals "1" "$rc" "returns 1 with < 5 events"

# ==============================================================================
# anomaly_compute_baseline — computes correctly
# ==============================================================================

test_begin "baseline_correct"
if command -v jq >/dev/null 2>&1; then
    export _DF_EVENTS_FILE="$TMPDIR_TEST/events-baseline.jsonl"
    # Write 10 events with known wall_s values: all 100
    for i in $(seq 1 10); do
        printf '{"event":"SPEC_SHIPPED","data":{"wall_s":"100"}}\n' >> "$_DF_EVENTS_FILE"
    done
    result=$(anomaly_compute_baseline "wall_s")
    mean="${result%% *}"
    stddev="${result##* }"
    assert_equals "100" "$mean" "mean is 100"
    assert_equals "0" "$stddev" "stddev is 0 (all same value)"
fi

# ==============================================================================
# anomaly_check_baseline — passes within 2 stddev
# ==============================================================================

test_begin "baseline_within_range"
if command -v jq >/dev/null 2>&1; then
    export _DF_EVENTS_FILE="$TMPDIR_TEST/events-baseline-ok.jsonl"
    for i in $(seq 1 10); do
        printf '{"event":"SPEC_SHIPPED","data":{"iters":"2"}}\n' >> "$_DF_EVENTS_FILE"
    done
    rc=0
    anomaly_check_baseline "iters" 2 || rc=$?
    assert_equals "0" "$rc" "value within range passes"
fi

# ==============================================================================
# anomaly_check_baseline — warns beyond 2 stddev
# ==============================================================================

test_begin "baseline_deviation"
if command -v jq >/dev/null 2>&1; then
    export _DF_EVENTS_FILE="$TMPDIR_TEST/events-baseline-dev.jsonl"
    for i in $(seq 1 10); do
        printf '{"event":"SPEC_SHIPPED","data":{"wall_s":"100"}}\n' >> "$_DF_EVENTS_FILE"
    done
    rc=0
    anomaly_check_baseline "wall_s" 500 || rc=$?
    assert_equals "1" "$rc" "500 when mean=100 stddev=0 → anomaly"
fi

# ==============================================================================
# anomaly_check_baseline — returns 0 without jq
# ==============================================================================

test_begin "baseline_no_jq"
# Simulate no jq by pointing to nonexistent events file
export _DF_EVENTS_FILE="$TMPDIR_TEST/nonexistent.jsonl"
rc=0
anomaly_check_baseline "wall_s" 9999 || rc=$?
assert_equals "0" "$rc" "returns 0 (no anomaly) without data"

# ==============================================================================
# anomaly_composite_score — normal (< 20) → return 0, no event
# ==============================================================================

test_begin "composite_normal"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-comp-normal.jsonl"
rc=0
anomaly_composite_score 0 0 0 1 0 || rc=$?  # 15 points (files only)
assert_equals "0" "$rc" "score 15 returns 0"
if [[ -f "$_DF_EVENTS_FILE" ]]; then
    lines=$(wc -l < "$_DF_EVENTS_FILE")
    assert_equals "0" "$(echo "$lines" | tr -d ' ')" "no event emitted for score < 20"
fi

# ==============================================================================
# anomaly_composite_score — warn (20-39) → return 0, emit warn
# ==============================================================================

test_begin "composite_warn"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-comp-warn.jsonl"
rc=0
anomaly_composite_score 0 1 0 0 0 || rc=$?  # 20 points (wall only)
assert_equals "0" "$rc" "score 20 returns 0"
if command -v jq >/dev/null 2>&1 && [[ -f "$_DF_EVENTS_FILE" ]]; then
    severity=$(jq -r '.data.severity' "$_DF_EVENTS_FILE" | head -1)
    assert_equals "warn" "$severity" "severity is warn"
fi

# ==============================================================================
# anomaly_composite_score — escalate (40-69)
# ==============================================================================

test_begin "composite_escalate"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-comp-esc.jsonl"
rc=0
anomaly_composite_score 0 0 0 0 1 || rc=$?  # 40 points (barrier only)
assert_equals "0" "$rc" "score 40 returns 0 (escalate, not block)"
if command -v jq >/dev/null 2>&1 && [[ -f "$_DF_EVENTS_FILE" ]]; then
    severity=$(jq -r '.data.severity' "$_DF_EVENTS_FILE" | head -1)
    assert_equals "escalate" "$severity" "severity is escalate"
fi

# ==============================================================================
# anomaly_composite_score — block (>= 70)
# ==============================================================================

test_begin "composite_block"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-comp-block.jsonl"
rc=0
anomaly_composite_score 1 1 1 0 0 || rc=$?  # 30+20+25 = 75
assert_equals "1" "$rc" "score 75 returns 1 (block)"
if command -v jq >/dev/null 2>&1 && [[ -f "$_DF_EVENTS_FILE" ]]; then
    severity=$(jq -r '.data.severity' "$_DF_EVENTS_FILE" | head -1)
    assert_equals "block" "$severity" "severity is block"
fi

# ==============================================================================
# anomaly_composite_score — barrier violation alone (40 points) → escalate
# ==============================================================================

test_begin "composite_barrier_escalate"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-comp-barrier.jsonl"
rc=0
anomaly_composite_score 0 0 0 0 1 || rc=$?  # barrier=40
assert_equals "0" "$rc" "barrier alone → escalate, not block"

# ==============================================================================
# anomaly_composite_score — files_flag contributes to escalate
# ==============================================================================

test_begin "composite_files_escalate"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-comp-files-esc.jsonl"
rc=0
anomaly_composite_score 1 0 0 1 0 || rc=$?  # 30+15 = 45 → escalate
assert_equals "0" "$rc" "score 45 returns 0 (escalate, not block)"
if command -v jq >/dev/null 2>&1 && [[ -f "$_DF_EVENTS_FILE" ]]; then
    severity=$(jq -r '.data.severity' "$_DF_EVENTS_FILE" | head -1)
    assert_equals "escalate" "$severity" "turns+files → escalate"
fi

# ==============================================================================
# anomaly_composite_score — files_flag alone below warn
# ==============================================================================

test_begin "composite_files_alone"
export _DF_EVENTS_FILE="$TMPDIR_TEST/events-comp-files-alone.jsonl"
rc=0
anomaly_composite_score 0 0 0 1 0 || rc=$?  # 15 → below warn (< 20)
assert_equals "0" "$rc" "score 15 returns 0"
if [[ -f "$_DF_EVENTS_FILE" ]]; then
    lines=$(wc -l < "$_DF_EVENTS_FILE")
    assert_equals "0" "$(echo "$lines" | tr -d ' ')" "no event emitted for files_flag alone"
fi

# ==============================================================================

cleanup_tmpdir
test_summary
