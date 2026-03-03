#!/usr/bin/env bash
set -euo pipefail

# tests/test-state.sh — Tests for scripts/lib/state.sh

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

cleanup_tmpdir
test_summary
