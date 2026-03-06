#!/usr/bin/env bash
set -euo pipefail

# tests/test-plan-state.sh — Tests for scripts/lib/plan-state.sh (Phase 5)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

setup_tmpdir

# ==============================================================================
# Setup
# ==============================================================================

source "${TESTS_DIR}/../scripts/lib/common.sh"
DF_PROJECT_ROOT="$TMPDIR_TEST/project"
export DF_PROJECT_ROOT
mkdir -p "$DF_PROJECT_ROOT/.dark-factory/state"
mkdir -p "$DF_PROJECT_ROOT/.dark-factory/specs"

# Override plan state dir
_DF_PLAN_STATE_DIR="$DF_PROJECT_ROOT/.dark-factory/state"

# Events file (needed by emit_* calls)
export _DF_EVENTS_FILE="$TMPDIR_TEST/events.jsonl"

# Create mock spec files
for spec in auth-v2 search-v3 payments-v1; do
    printf -- '---\nspec-name: %s\n---\n# %s\n' "$spec" "$spec" > "$DF_PROJECT_ROOT/.dark-factory/specs/${spec}.md"
done

# ==============================================================================
# plan_checkpoint_write — creates file with correct JSON
# ==============================================================================

test_begin "checkpoint_write_creates_file"
plan_checkpoint_write "test-plan" "run1" "auth-v2" "shipped" "0"
ckpt_file="$_DF_PLAN_STATE_DIR/plan-test-plan-run1.jsonl"
assert_file_exists "$ckpt_file" "checkpoint file created"

line=$(cat "$ckpt_file")
assert_contains "$line" '"spec":"auth-v2"' "spec in checkpoint"
assert_contains "$line" '"status":"shipped"' "status in checkpoint"
assert_contains "$line" '"exit_code":0' "exit_code in checkpoint"
assert_contains "$line" '"run_id":"run1"' "run_id in checkpoint"

if command -v jq >/dev/null 2>&1; then
    jq -e . "$ckpt_file" >/dev/null 2>&1
    assert_equals "0" "$?" "checkpoint is valid JSON"
fi

# ==============================================================================
# plan_checkpoint_write — appends (not overwrites)
# ==============================================================================

test_begin "checkpoint_write_appends"
plan_checkpoint_write "test-plan" "run1" "search-v3" "failed" "1"
line_count=$(wc -l < "$ckpt_file")
assert_equals "2" "$(echo "$line_count" | tr -d ' ')" "second write appends"

# ==============================================================================
# plan_checkpoint_write — extras
# ==============================================================================

test_begin "checkpoint_write_extras"
ckpt2="$_DF_PLAN_STATE_DIR/plan-extras-run2.jsonl"
plan_checkpoint_write "extras" "run2" "auth-v2" "shipped" "0" "wall_s=342" "pr_url=https://example.com"
line=$(cat "$ckpt2")
assert_contains "$line" '"wall_s":"342"' "extra wall_s in checkpoint"
assert_contains "$line" '"pr_url":"https://example.com"' "extra pr_url in checkpoint"

# ==============================================================================
# plan_checkpoint_load — returns latest checkpoint
# ==============================================================================

test_begin "checkpoint_load_latest"
# Create two checkpoint files with different timestamps
mkdir -p "$_DF_PLAN_STATE_DIR"
printf '{"spec":"a","status":"shipped","exit_code":0,"ts":"2026-03-01T10:00:00Z","run_id":"older","spec_hash":""}\n' \
    > "$_DF_PLAN_STATE_DIR/plan-load-test-older.jsonl"
printf '{"spec":"b","status":"shipped","exit_code":0,"ts":"2026-03-05T10:00:00Z","run_id":"newer","spec_hash":""}\n' \
    > "$_DF_PLAN_STATE_DIR/plan-load-test-newer.jsonl"

result=$(plan_checkpoint_load "load-test")
assert_contains "$result" "newer" "loads most recent checkpoint"

# ==============================================================================
# plan_checkpoint_load — returns empty when none exist
# ==============================================================================

test_begin "checkpoint_load_none"
rc=0
plan_checkpoint_load "nonexistent-plan" >/dev/null 2>&1 || rc=$?
assert_equals "1" "$rc" "returns 1 when no checkpoint exists"

# ==============================================================================
# plan_checkpoint_completed_specs — returns shipped specs
# ==============================================================================

test_begin "completed_specs_shipped"
ckpt3="$_DF_PLAN_STATE_DIR/plan-completed-test.jsonl"
printf '{"spec":"spec-a","status":"shipped","exit_code":0,"ts":"2026-03-05T10:00:00Z","run_id":"r1","spec_hash":"abc"}\n' > "$ckpt3"
printf '{"spec":"spec-b","status":"failed","exit_code":1,"ts":"2026-03-05T10:01:00Z","run_id":"r1","spec_hash":"def"}\n' >> "$ckpt3"
printf '{"spec":"spec-c","status":"budget_skipped","exit_code":0,"ts":"2026-03-05T10:02:00Z","run_id":"r1","spec_hash":"ghi"}\n' >> "$ckpt3"
printf '{"spec":"spec-d","status":"escalated","exit_code":2,"ts":"2026-03-05T10:03:00Z","run_id":"r1","spec_hash":"jkl"}\n' >> "$ckpt3"

completed=$(plan_checkpoint_completed_specs "$ckpt3")
assert_contains "$completed" "spec-a" "shipped spec is completed"
assert_contains "$completed" "spec-c" "budget_skipped spec is completed"
assert_not_contains "$completed" "spec-b" "failed spec is not completed"
assert_not_contains "$completed" "spec-d" "escalated spec is not completed"

# ==============================================================================
# plan_checkpoint_spec_hash — returns hash for a spec
# ==============================================================================

test_begin "checkpoint_spec_hash"
if command -v jq >/dev/null 2>&1; then
    hash=$(plan_checkpoint_spec_hash "$ckpt3" "spec-a")
    assert_equals "abc" "$hash" "returns correct spec hash"
fi

# ==============================================================================
# plan_checkpoint_completed_specs — empty file
# ==============================================================================

test_begin "completed_specs_empty"
empty_ckpt="$_DF_PLAN_STATE_DIR/plan-empty-test.jsonl"
touch "$empty_ckpt"
completed=$(plan_checkpoint_completed_specs "$empty_ckpt")
assert_equals "" "$completed" "empty checkpoint returns no specs"

# ==============================================================================
# plan_checkpoint_completed_specs — nonexistent file
# ==============================================================================

test_begin "completed_specs_nofile"
completed=$(plan_checkpoint_completed_specs "/nonexistent/file.jsonl")
assert_equals "" "$completed" "nonexistent file returns no specs"

# ==============================================================================
# plan_checkpoint_write — includes spec_hash
# ==============================================================================

test_begin "checkpoint_includes_spec_hash"
ckpt4="$_DF_PLAN_STATE_DIR/plan-hash-run4.jsonl"
plan_checkpoint_write "hash" "run4" "auth-v2" "shipped" "0"
line=$(cat "$ckpt4")
assert_contains "$line" '"spec_hash"' "checkpoint includes spec_hash field"
# The hash should be non-empty since we created the spec file
if command -v jq >/dev/null 2>&1; then
    hash=$(jq -r '.spec_hash' "$ckpt4")
    assert_not_equals "" "$hash" "spec_hash is non-empty"
fi

# ==============================================================================

cleanup_tmpdir
test_summary
