#!/usr/bin/env bash
set -euo pipefail

# tests/test-state.sh — Tests for scripts/lib/state.sh (Items 20, 25)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"
source "${TESTS_DIR}/../scripts/lib/state.sh"

setup_tmpdir

# ==============================================================================
# write_state / read_state
# ==============================================================================

test_begin "state roundtrip"

STATE_FILE="$TMPDIR_TEST/test.state"

# Write and read back
write_state "status" "running"
result=$(read_state "status" "default")
assert_equals "running" "$result" "write+read roundtrip"

# Overwrite existing key
write_state "status" "shipped"
result=$(read_state "status" "default")
assert_equals "shipped" "$result" "key update overwrites"

# Multiple keys
write_state "iteration" "3"
result=$(read_state "iteration" "0")
assert_equals "3" "$result" "second key works"

# First key still intact
result=$(read_state "status" "default")
assert_equals "shipped" "$result" "first key survives second write"

# Missing key returns default
result=$(read_state "nonexistent" "fallback")
assert_equals "fallback" "$result" "missing key returns default"

# Missing key with no default returns empty
result=$(read_state "nonexistent")
assert_equals "" "$result" "missing key with no default returns empty"

test_begin "state directory creation"

STATE_FILE="$TMPDIR_TEST/nested/dir/test.state"
write_state "key" "val"
result=$(read_state "key" "")
assert_equals "val" "$result" "creates nested directories"

test_begin "state value with equals sign"

STATE_FILE="$TMPDIR_TEST/equals.state"
write_state "cmd" "FOO=bar BAZ=qux"
result=$(read_state "cmd" "")
assert_equals "FOO=bar BAZ=qux" "$result" "preserves equals signs in value"

# ==============================================================================
# Item 25: read_state empty value handling
# ==============================================================================

test_begin "read_state returns default for empty value (Item 25)"

STATE_FILE="$TMPDIR_TEST/empty.state"
echo "empty_key=" > "$STATE_FILE"
result=$(read_state "empty_key" "fallback")
assert_equals "fallback" "$result" "empty value returns default"

test_begin "read_state returns value for populated key"

STATE_FILE="$TMPDIR_TEST/populated.state"
echo "key=value" > "$STATE_FILE"
result=$(read_state "key" "fallback")
assert_equals "value" "$result" "populated key returns value"

# ==============================================================================
# Item 20: _df_acquire_lock / _df_release_lock (portable mkdir locking)
# ==============================================================================

test_begin "acquire_lock creates lockdir with PID"

lockdir="$TMPDIR_TEST/test.lockdir"
_df_acquire_lock "$lockdir" 5
assert_file_exists "${lockdir}/pid" "PID file exists"
pid_content=$(cat "${lockdir}/pid")
assert_equals "$$" "$pid_content" "PID file contains current PID"
_df_release_lock "$lockdir"

test_begin "release_lock removes lockdir"

lockdir="$TMPDIR_TEST/test2.lockdir"
_df_acquire_lock "$lockdir" 5
_df_release_lock "$lockdir"
if [[ -d "$lockdir" ]]; then
    _test_fail "lockdir still exists after release"
else
    _test_pass "lockdir removed after release"
fi

test_begin "stale lock detection (dead PID)"

lockdir="$TMPDIR_TEST/test3.lockdir"
mkdir -p "$lockdir"
# Use a PID that definitely doesn't exist
echo "99999999" > "${lockdir}/pid"
_df_acquire_lock "$lockdir" 2
lock_result=$?
assert_equals "0" "$lock_result" "acquires lock after stale PID detected"
_df_release_lock "$lockdir"

test_begin "lock timeout returns 1"

lockdir="$TMPDIR_TEST/test4.lockdir"
mkdir -p "$lockdir"
# Use current PID (alive) so lock can't be acquired
echo "$$" > "${lockdir}/pid"
lock_result=0
_df_acquire_lock "$lockdir" 1 || lock_result=$?
assert_equals "1" "$lock_result" "returns 1 on timeout"
rm -rf "$lockdir"

# ==============================================================================
# write_state fail-closed on lock failure
# ==============================================================================

test_begin "write_state returns 1 when lock held"

STATE_FILE="$TMPDIR_TEST/locked.state"
lockdir="${STATE_FILE}.lockdir"
mkdir -p "$lockdir"
# Use current PID (alive) so lock can't be acquired
echo "$$" > "${lockdir}/pid"

ws_exit=0
write_state "key" "val" 2>/dev/null || ws_exit=$?
assert_equals "1" "$ws_exit" "write_state returns 1 on lock failure"

# Verify state file was NOT written (fail-closed)
if [[ -f "$STATE_FILE" ]]; then
    _test_fail "state file should NOT exist after lock failure"
else
    _test_pass "state file not written on lock failure (fail-closed)"
fi
rm -rf "$lockdir"

# ==============================================================================
# write_state_or_warn
# ==============================================================================

test_begin "write_state_or_warn emits warning but does not fail"

STATE_FILE="$TMPDIR_TEST/warn.state"
lockdir="${STATE_FILE}.lockdir"
mkdir -p "$lockdir"
echo "$$" > "${lockdir}/pid"

warn_output=$(write_state_or_warn "key" "val" 2>&1)
warn_exit=$?
assert_equals "0" "$warn_exit" "write_state_or_warn returns 0 despite lock failure"
assert_contains "$warn_output" "WARNING" "write_state_or_warn emits WARNING"
rm -rf "$lockdir"

cleanup_tmpdir
test_summary
