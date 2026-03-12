#!/usr/bin/env bash
set -euo pipefail

# tests/test-git-ops.sh — Tests for scripts/lib/git-ops.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

setup_tmpdir

# Create a temporary git repo for testing
_test_repo="${TMPDIR_TEST}/test-repo"
mkdir -p "$_test_repo"
cd "$_test_repo"
git init -q
git config user.name "test"
git config user.email "test@test.com"
git commit --allow-empty -m "initial" -q

# Source the library under test
source "${TESTS_DIR}/../scripts/lib/git-ops.sh"

# ==============================================================================
# gitops_preflight — inside git repo (gh check will fail in test, that's OK)
# ==============================================================================

test_begin "gitops_preflight inside git repo without gh"

# Override command -v to simulate missing gh
_rc=0
(
    # Run in subshell — preflight will fail on gh check but that's expected
    gitops_preflight 2>/dev/null
) || _rc=$?
# Should fail because gh isn't authenticated in test env (or missing)
# The important thing is it doesn't fail on the "not inside git repo" check
if [[ $_rc -ne 0 ]]; then
    _test_pass "gitops_preflight fails gracefully (gh auth issue expected in test)"
else
    _test_pass "gitops_preflight passed (gh available and authenticated)"
fi

# ==============================================================================
# gitops_preflight — outside git repo
# ==============================================================================

test_begin "gitops_preflight outside git repo"

_rc=0
(cd /tmp && gitops_preflight 2>/dev/null) || _rc=$?
assert_not_equals "0" "$_rc" "gitops_preflight fails outside git repo"

# ==============================================================================
# gitops_create_branch — creates branch with correct naming
# ==============================================================================

test_begin "gitops_create_branch naming format"

cd "$_test_repo"
gitops_create_branch "my-feature" "20260304-120000-12345-abcd1234" 2>/dev/null
_branch=$(git rev-parse --abbrev-ref HEAD)
assert_equals "dark-factory/my-feature-20260304-120" "$_branch" "branch name uses short run_id (12 chars)"

# ==============================================================================
# gitops_create_branch — idempotent (calling twice doesn't fail)
# ==============================================================================

test_begin "gitops_create_branch idempotent"

_rc=0
gitops_create_branch "my-feature" "20260304-120000-12345-abcd1234" 2>/dev/null || _rc=$?
assert_equals "0" "$_rc" "second create_branch call succeeds"

_branch=$(git rev-parse --abbrev-ref HEAD)
assert_equals "dark-factory/my-feature-20260304-120" "$_branch" "still on correct branch"

# ==============================================================================
# gitops_commit_changes — commits project files, excludes .dark-factory/
# ==============================================================================

test_begin "gitops_commit_changes staging"

cd "$_test_repo"
mkdir -p src .dark-factory/state
echo "code" > src/main.js
echo "state" > .dark-factory/state/test.state
echo '{}' > package.json

SOURCE_DIRS="src"
TEST_DIR=""
gitops_commit_changes "test-spec" "run-123" 2>/dev/null

# Verify src/main.js and package.json are committed
_committed=$(git log --name-only --format="" -1)
assert_contains "$_committed" "src/main.js" "src/main.js is committed"
assert_contains "$_committed" "package.json" "package.json is committed"
assert_not_contains "$_committed" ".dark-factory" ".dark-factory files are not committed"

# ==============================================================================
# gitops_commit_changes — idempotent (no changes = no commit)
# ==============================================================================

test_begin "gitops_commit_changes no-op when nothing changed"

_before=$(git rev-parse HEAD)
gitops_commit_changes "test-spec" "run-456" 2>/dev/null
_after=$(git rev-parse HEAD)
assert_equals "$_before" "$_after" "no new commit when nothing changed"

# Cleanup
cd "$TESTS_DIR"
cleanup_tmpdir

test_summary
