#!/usr/bin/env bash
set -euo pipefail

# tests/test-lock-contention.sh — Concurrent lock contention tests for state.sh
# Tests _df_acquire_lock / _df_release_lock under real concurrency.

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

setup_tmpdir

# Resolve real paths (macOS /var → /private/var symlink)
TMPDIR_TEST="$(cd "$TMPDIR_TEST" && pwd -P)"

# Source common.sh then state.sh
source "${TESTS_DIR}/../scripts/lib/common.sh"
source "${TESTS_DIR}/../scripts/lib/state.sh"

# Fast poll interval for tests
export _DF_LOCK_POLL_INTERVAL=0.1

# Cleanup leaked background jobs on abort
_test_bgpids=()
_lock_test_cleanup() {
    for pid in "${_test_bgpids[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
}
trap '_lock_test_cleanup' EXIT

# ==============================================================================
# Two-process contention: A holds lock, B waits, A releases, B succeeds
# ==============================================================================

test_begin "lock contention: two-process contention"

_lockdir="${TMPDIR_TEST}/lock-two.lockdir"
_logfile="${TMPDIR_TEST}/lock-two.log"
: > "$_logfile"

# Process A: acquire lock, hold for 0.5s, release
(
    _df_acquire_lock "$_lockdir" 30 || exit 1
    echo "A-acquired" >> "$_logfile"
    sleep 0.5
    _df_release_lock "$_lockdir"
    echo "A-released" >> "$_logfile"
) &
_test_bgpids+=($!)
pid_a=$!

# Brief delay so A gets lock first
sleep 0.1

# Process B: try to acquire (should wait), then release
(
    _df_acquire_lock "$_lockdir" 30 || exit 1
    echo "B-acquired" >> "$_logfile"
    _df_release_lock "$_lockdir"
) &
_test_bgpids+=($!)
pid_b=$!

# Wait for both (timeout 10s)
wait_ok=true
wait "$pid_a" 2>/dev/null || wait_ok=false
wait "$pid_b" 2>/dev/null || wait_ok=false

if [[ "$wait_ok" == "true" ]]; then
    _test_pass "both processes completed"
else
    _test_fail "one or both processes failed"
fi

# Verify A acquired before B
log_content=$(cat "$_logfile")
assert_contains "$log_content" "A-acquired" "A acquired lock"
assert_contains "$log_content" "B-acquired" "B acquired lock"

# A-acquired should appear before B-acquired
a_line=$(grep -n "A-acquired" "$_logfile" | head -1 | cut -d: -f1)
b_line=$(grep -n "B-acquired" "$_logfile" | head -1 | cut -d: -f1)
if [[ "$a_line" -lt "$b_line" ]]; then
    _test_pass "A acquired before B (correct ordering)"
else
    _test_fail "B acquired before A (wrong ordering: A=$a_line B=$b_line)"
fi

# ==============================================================================
# Five-process contention: all write PIDs, no deadlock
# ==============================================================================

test_begin "lock contention: five-process contention"

_lockdir5="${TMPDIR_TEST}/lock-five.lockdir"
_logfile5="${TMPDIR_TEST}/lock-five.log"
: > "$_logfile5"

pids5=()
for i in 1 2 3 4 5; do
    (
        _df_acquire_lock "$_lockdir5" 30 || exit 1
        echo "$BASHPID" >> "$_logfile5"
        sleep 0.1  # hold briefly
        _df_release_lock "$_lockdir5"
    ) &
    pids5+=($!)
    _test_bgpids+=($!)
done

all_ok=true
for pid in "${pids5[@]}"; do
    wait "$pid" 2>/dev/null || all_ok=false
done

if [[ "$all_ok" == "true" ]]; then
    _test_pass "all 5 processes completed without deadlock"
else
    _test_fail "one or more processes failed or deadlocked"
fi

# Count unique PIDs
unique_pids=$(sort -u "$_logfile5" | wc -l | tr -d ' ')
assert_equals "5" "$unique_pids" "5 unique PIDs in log"

# ==============================================================================
# No-PID-file recovery: lockdir exists without pid file
# ==============================================================================

test_begin "lock contention: no-PID-file recovery"

_lockdir_nopid="${TMPDIR_TEST}/lock-nopid.lockdir"
# Create lockdir without pid file (simulates crash between mkdir and pid write)
mkdir -p "$_lockdir_nopid"

# Use 1s poll interval so grace period (elapsed >= 2) takes ~2s wall time
_saved_poll="$_DF_LOCK_POLL_INTERVAL"
_DF_LOCK_POLL_INTERVAL=1

# Acquirer should wait grace period (~2 iterations * 1s) then acquire
_start_ts=$(date +%s)
rc=0
_df_acquire_lock "$_lockdir_nopid" 10 || rc=$?
_end_ts=$(date +%s)
_elapsed=$((_end_ts - _start_ts))

_DF_LOCK_POLL_INTERVAL="$_saved_poll"

assert_equals "0" "$rc" "acquires after grace period"
if [[ $_elapsed -ge 1 ]]; then
    _test_pass "waited at least 1s for grace period (${_elapsed}s)"
else
    _test_fail "acquired too fast (${_elapsed}s) — grace period not respected"
fi

_df_release_lock "$_lockdir_nopid"

# ==============================================================================
# Stale lock cleanup: lockdir with dead PID, 3 concurrent acquirers
# ==============================================================================

test_begin "lock contention: stale lock with dead PID"

_lockdir_stale="${TMPDIR_TEST}/lock-stale.lockdir"
_logfile_stale="${TMPDIR_TEST}/lock-stale.log"
: > "$_logfile_stale"

# Create a stale lock with a PID that doesn't exist
mkdir -p "$_lockdir_stale"
echo "99999999" > "$_lockdir_stale/pid"

# Launch 3 concurrent acquirers — one should clean up the stale lock
pids_stale=()
for i in 1 2 3; do
    (
        _df_acquire_lock "$_lockdir_stale" 10 || exit 1
        echo "$BASHPID" >> "$_logfile_stale"
        sleep 0.1
        _df_release_lock "$_lockdir_stale"
    ) &
    pids_stale+=($!)
    _test_bgpids+=($!)
done

stale_ok=true
for pid in "${pids_stale[@]}"; do
    wait "$pid" 2>/dev/null || stale_ok=false
done

if [[ "$stale_ok" == "true" ]]; then
    _test_pass "all 3 acquirers completed"
else
    _test_fail "one or more acquirers failed with stale lock"
fi

unique_stale=$(sort -u "$_logfile_stale" | wc -l | tr -d ' ')
assert_equals "3" "$unique_stale" "3 unique PIDs acquired after stale cleanup"

# ==============================================================================
# Concurrent worktree_create: two real worktree_create calls in parallel
# ==============================================================================

test_begin "lock contention: concurrent worktree_create (integration)"

# Set up a real git repo for this integration test
_wt_repo="${TMPDIR_TEST}/wt-project"
_wt_bare="${TMPDIR_TEST}/wt-bare.git"
mkdir -p "$_wt_repo"
cd "$_wt_repo"
git init -q
git config user.email "test@test.com"
git config user.name "Test"
echo "initial" > README.md
git add README.md
git commit -q -m "initial commit"
git init -q --bare "$_wt_bare"
git remote add origin "$_wt_bare"
git push -q origin main

# Override globals for worktree.sh
DF_PROJECT_ROOT="$_wt_repo"
_DF_WORKTREE_BASE="${DF_PROJECT_ROOT}/.dark-factory/worktrees"
mkdir -p "${_wt_repo}/.dark-factory/specs"

cat > "${_wt_repo}/.dark-factory/specs/wt-spec-1.md" <<'EOF'
---
spec-name: wt-spec-1
target-files:
  - src/one.ts
---
EOF

cat > "${_wt_repo}/.dark-factory/specs/wt-spec-2.md" <<'EOF'
---
spec-name: wt-spec-2
target-files:
  - src/two.ts
---
EOF

_wt_log="${TMPDIR_TEST}/wt-concurrent.log"
: > "$_wt_log"

# Launch two worktree_create calls in parallel
(
    path=$(worktree_create "wt-spec-1" "concrun1234567890") && echo "1:${path}" >> "$_wt_log" || echo "1:FAIL" >> "$_wt_log"
) &
_test_bgpids+=($!)
pid_wt1=$!

(
    path=$(worktree_create "wt-spec-2" "concrun1234567890") && echo "2:${path}" >> "$_wt_log" || echo "2:FAIL" >> "$_wt_log"
) &
_test_bgpids+=($!)
pid_wt2=$!

wt_ok=true
wait "$pid_wt1" 2>/dev/null || wt_ok=false
wait "$pid_wt2" 2>/dev/null || wt_ok=false

wt_log_content=$(cat "$_wt_log")

# Both should succeed
assert_not_contains "$wt_log_content" "FAIL" "neither worktree_create failed"

# Both should produce distinct paths
wt1_path=$(grep "^1:" "$_wt_log" | cut -d: -f2-)
wt2_path=$(grep "^2:" "$_wt_log" | cut -d: -f2-)
if [[ -n "$wt1_path" ]] && [[ -n "$wt2_path" ]] && [[ "$wt1_path" != "$wt2_path" ]]; then
    _test_pass "both worktrees created with distinct paths"
else
    _test_fail "worktree paths not distinct (1='${wt1_path}' 2='${wt2_path}')"
fi

# Verify both branches exist
branch_count=$(git -C "$_wt_repo" branch --list 'dark-factory/*' | wc -l | tr -d ' ')
if [[ "$branch_count" -ge 2 ]]; then
    _test_pass "both branches created"
else
    _test_fail "expected 2+ branches, got ${branch_count}"
fi

# Cleanup
worktree_cleanup_all 2>/dev/null || true
cd "$TMPDIR_TEST"

# ==============================================================================
# Teardown
# ==============================================================================

cleanup_tmpdir
test_summary
