#!/usr/bin/env bash
# tests/lib/test-helpers.sh — Pure-bash test assertion helpers
#
# Usage: source this file, then call assert_* functions.
# Call test_summary at the end; it exits non-zero if any test failed.

_TEST_PASS=0
_TEST_FAIL=0
_TEST_CURRENT=""

# test_begin <name>
#   Start a named test case (for output context).
test_begin() {
    _TEST_CURRENT="$1"
}

# _test_pass / _test_fail <message>
_test_pass() {
    _TEST_PASS=$((_TEST_PASS + 1))
    echo "  PASS  ${_TEST_CURRENT:+[$_TEST_CURRENT] }$1"
}

_test_fail() {
    _TEST_FAIL=$((_TEST_FAIL + 1))
    echo "  FAIL  ${_TEST_CURRENT:+[$_TEST_CURRENT] }$1"
}

# assert_equals <expected> <actual> [message]
assert_equals() {
    local expected="$1" actual="$2" msg="${3:-assert_equals}"
    if [[ "$expected" == "$actual" ]]; then
        _test_pass "$msg"
    else
        _test_fail "$msg (expected='$expected', actual='$actual')"
    fi
}

# assert_not_equals <unexpected> <actual> [message]
assert_not_equals() {
    local unexpected="$1" actual="$2" msg="${3:-assert_not_equals}"
    if [[ "$unexpected" != "$actual" ]]; then
        _test_pass "$msg"
    else
        _test_fail "$msg (got unwanted value='$actual')"
    fi
}

# assert_contains <haystack> <needle> [message]
assert_contains() {
    local haystack="$1" needle="$2" msg="${3:-assert_contains}"
    if [[ "$haystack" == *"$needle"* ]]; then
        _test_pass "$msg"
    else
        _test_fail "$msg (haystack does not contain '$needle')"
    fi
}

# assert_not_contains <haystack> <needle> [message]
assert_not_contains() {
    local haystack="$1" needle="$2" msg="${3:-assert_not_contains}"
    if [[ "$haystack" != *"$needle"* ]]; then
        _test_pass "$msg"
    else
        _test_fail "$msg (haystack unexpectedly contains '$needle')"
    fi
}

# assert_exit_code <expected_code> <command...>
#   Runs the command and checks its exit code.
assert_exit_code() {
    local expected="$1" msg="${2:-assert_exit_code}"
    shift 2
    local actual=0
    "$@" >/dev/null 2>&1 || actual=$?
    if [[ "$actual" -eq "$expected" ]]; then
        _test_pass "$msg (exit $actual)"
    else
        _test_fail "$msg (expected exit $expected, got $actual)"
    fi
}

# assert_file_exists <path> [message]
assert_file_exists() {
    local path="$1" msg="${2:-assert_file_exists}"
    if [[ -f "$path" ]]; then
        _test_pass "$msg"
    else
        _test_fail "$msg (file not found: $path)"
    fi
}

# setup_tmpdir
#   Creates a temp directory and sets TMPDIR_TEST to its path.
setup_tmpdir() {
    TMPDIR_TEST="$(mktemp -d)"
}

# cleanup_tmpdir
#   Removes the temp directory.
cleanup_tmpdir() {
    [[ -n "${TMPDIR_TEST:-}" ]] && rm -rf "$TMPDIR_TEST"
}

# test_summary
#   Prints pass/fail counts and exits non-zero if any test failed.
test_summary() {
    local total=$((_TEST_PASS + _TEST_FAIL))
    echo ""
    echo "  ${total} tests: ${_TEST_PASS} passed, ${_TEST_FAIL} failed"
    [[ $_TEST_FAIL -gt 0 ]] && return 1
    return 0
}
