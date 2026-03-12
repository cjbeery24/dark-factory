#!/usr/bin/env bash
set -euo pipefail

# tests/test-notify.sh — Tests for scripts/lib/notify.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"
source "${TESTS_DIR}/../scripts/lib/notify.sh"

# ==============================================================================
# _notify_json_escape — basic escaping
# ==============================================================================

test_begin "_notify_json_escape basics"

result=$(_notify_json_escape 'hello "world"')
assert_contains "$result" '\"' "escapes double quotes"

result=$(_notify_json_escape 'line1
line2')
assert_contains "$result" '\n' "escapes newlines"

result=$(_notify_json_escape 'back\slash')
assert_contains "$result" '\\' "escapes backslashes"

# ==============================================================================
# Missing webhook URL — silent no-op
# ==============================================================================

test_begin "missing webhook URL is silent no-op"

unset NOTIFY_SLACK_WEBHOOK_URL 2>/dev/null || true
unset NOTIFY_WEBHOOK_URL 2>/dev/null || true

_rc=0
notify_shipped "test-spec" "run-123" "2" "" 2>/dev/null || _rc=$?
assert_equals "0" "$_rc" "notify_shipped returns 0 with no webhook URL"

_rc=0
notify_escalated "test-spec" "run-123" "3" "max iters" 2>/dev/null || _rc=$?
assert_equals "0" "$_rc" "notify_escalated returns 0 with no webhook URL"

_rc=0
notify_failed "test-spec" "run-123" "build error" 2>/dev/null || _rc=$?
assert_equals "0" "$_rc" "notify_failed returns 0 with no webhook URL"

_rc=0
notify_pr_created "test-spec" "https://github.com/example/pr/1" 2>/dev/null || _rc=$?
assert_equals "0" "$_rc" "notify_pr_created returns 0 with no webhook URL"

_rc=0
notify_regression_blocked "test-spec" "spec-a spec-b" 2>/dev/null || _rc=$?
assert_equals "0" "$_rc" "notify_regression_blocked returns 0 with no webhook URL"

# ==============================================================================
# Notification functions return 0 even with invalid webhook URL
# ==============================================================================

test_begin "invalid webhook URL returns 0"

NOTIFY_SLACK_WEBHOOK_URL="http://localhost:1/nonexistent"
NOTIFY_WEBHOOK_URL="http://localhost:1/nonexistent"

_rc=0
notify_shipped "test-spec" "run-123" "2" "" 2>/dev/null || _rc=$?
assert_equals "0" "$_rc" "notify_shipped returns 0 with unreachable webhook"

_rc=0
notify_failed "test-spec" "run-123" "error" 2>/dev/null || _rc=$?
assert_equals "0" "$_rc" "notify_failed returns 0 with unreachable webhook"

unset NOTIFY_SLACK_WEBHOOK_URL NOTIFY_WEBHOOK_URL

test_summary
