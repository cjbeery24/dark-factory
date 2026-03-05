#!/usr/bin/env bash
set -euo pipefail

# tests/test-worktree.sh — Tests for scripts/lib/worktree.sh (Phase 4)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

setup_tmpdir

# ==============================================================================
# Setup: Create a real git repo with bare remote for worktree tests
# ==============================================================================

# Resolve real paths (macOS /var → /private/var symlink breaks git porcelain matching)
TMPDIR_TEST="$(cd "$TMPDIR_TEST" && pwd -P)"
_test_repo="${TMPDIR_TEST}/project"
_bare_repo="${TMPDIR_TEST}/bare-remote.git"

mkdir -p "$_test_repo"
cd "$_test_repo"
git init -q
git config user.email "test@test.com"
git config user.name "Test"
echo "initial" > README.md
git add README.md
git commit -q -m "initial commit"

git init -q --bare "$_bare_repo"
git remote add origin "$_bare_repo"
git push -q origin main

# Source common.sh (sets DF_PROJECT_ROOT to the real dark-factory root)
# Then override paths to point at our test repo
source "${TESTS_DIR}/../scripts/lib/common.sh"
DF_PROJECT_ROOT="$_test_repo"
_DF_WORKTREE_BASE="${DF_PROJECT_ROOT}/.dark-factory/worktrees"
_DF_REGISTRY_DIR="${DF_PROJECT_ROOT}/.dark-factory/shipped-scenarios"
_DF_SCRIPTS_DIR="${TESTS_DIR}/../scripts"

# Create spec and config dirs
mkdir -p "${_test_repo}/.dark-factory/specs"
mkdir -p "${_test_repo}/.dark-factory/plans"

# ==============================================================================
# _parse_target_files
# ==============================================================================

test_begin "_parse_target_files: well-formed block"

cat > "${_test_repo}/.dark-factory/specs/parse-test.md" <<'EOF'
---
spec-name: parse-test
target-files:
  - src/main.ts
  - src/utils/helper.ts
  - tests/main.test.ts
---
# Parse Test Spec
EOF

result=$(_parse_target_files "${_test_repo}/.dark-factory/specs/parse-test.md")
assert_contains "$result" "src/main.ts" "contains first file"
assert_contains "$result" "src/utils/helper.ts" "contains second file"
assert_contains "$result" "tests/main.test.ts" "contains third file"
line_count=$(echo "$result" | wc -l | tr -d ' ')
assert_equals "3" "$line_count" "outputs exactly 3 lines"

test_begin "_parse_target_files: no target-files block"

cat > "${_test_repo}/.dark-factory/specs/no-targets.md" <<'EOF'
---
spec-name: no-targets
---
# No Targets Spec
EOF

result=$(_parse_target_files "${_test_repo}/.dark-factory/specs/no-targets.md")
assert_equals "" "$result" "empty output for spec without target-files"

test_begin "_parse_target_files: bare filenames filtered out"

cat > "${_test_repo}/.dark-factory/specs/bare-names.md" <<'EOF'
---
spec-name: bare-names
target-files:
  - Makefile
  - src/app.js
  - Dockerfile
---
EOF

result=$(_parse_target_files "${_test_repo}/.dark-factory/specs/bare-names.md")
assert_not_contains "$result" "Makefile" "Makefile filtered out (no / or .)"
assert_contains "$result" "src/app.js" "src/app.js included (has / and .)"
assert_not_contains "$result" "Dockerfile" "Dockerfile filtered out (no / or .)"

test_begin "_parse_target_files: whitespace trimmed"

cat > "${_test_repo}/.dark-factory/specs/whitespace.md" <<'EOF'
---
spec-name: whitespace
target-files:
  -   src/padded.ts
  - src/normal.ts
---
EOF

result=$(_parse_target_files "${_test_repo}/.dark-factory/specs/whitespace.md")
assert_contains "$result" "src/padded.ts" "leading whitespace trimmed"
assert_contains "$result" "src/normal.ts" "normal file included"

# ==============================================================================
# conflict_detect (requires bash 4+ for local -A)
# ==============================================================================

if [[ "${BASH_VERSINFO[0]}" -ge 4 ]]; then

test_begin "conflict_detect: no overlapping files"

cat > "${_test_repo}/.dark-factory/specs/spec-a.md" <<'EOF'
---
spec-name: spec-a
target-files:
  - src/alpha.ts
---
EOF

cat > "${_test_repo}/.dark-factory/specs/spec-b.md" <<'EOF'
---
spec-name: spec-b
target-files:
  - src/beta.ts
---
EOF

rc=0
conflict_detect "spec-a" "spec-b" 2>/dev/null || rc=$?
assert_equals "0" "$rc" "no conflict returns 0"

test_begin "conflict_detect: overlapping files"

cat > "${_test_repo}/.dark-factory/specs/spec-c.md" <<'EOF'
---
spec-name: spec-c
target-files:
  - src/shared.ts
  - src/only-c.ts
---
EOF

cat > "${_test_repo}/.dark-factory/specs/spec-d.md" <<'EOF'
---
spec-name: spec-d
target-files:
  - src/shared.ts
  - src/only-d.ts
---
EOF

rc=0
err=$(conflict_detect "spec-c" "spec-d" 2>&1 >/dev/null) || rc=$?
assert_equals "1" "$rc" "conflict returns 1"
assert_contains "$err" "CONFLICT" "stderr contains CONFLICT"

test_begin "conflict_detect: spec with no target-files"

cat > "${_test_repo}/.dark-factory/specs/spec-empty.md" <<'EOF'
---
spec-name: spec-empty
---
EOF

rc=0
err=$(conflict_detect "spec-a" "spec-empty" 2>&1 >/dev/null) || rc=$?
assert_equals "0" "$rc" "no conflict when one spec has no targets"
assert_contains "$err" "WARNING" "warning emitted for missing target-files"

test_begin "conflict_detect: single spec"

rc=0
conflict_detect "spec-a" 2>/dev/null || rc=$?
assert_equals "0" "$rc" "single spec never conflicts"

else
    echo "  SKIP  conflict_detect tests (require bash 4+, have bash ${BASH_VERSION})"
fi  # bash 4+ guard

# ==============================================================================
# worktree_create (spec files needed for copy)
# ==============================================================================

# Create spec-a and spec-b files if not already present (may have been created above)
cat > "${_test_repo}/.dark-factory/specs/spec-a.md" <<'EOF'
---
spec-name: spec-a
target-files:
  - src/alpha.ts
---
EOF

cat > "${_test_repo}/.dark-factory/specs/spec-b.md" <<'EOF'
---
spec-name: spec-b
target-files:
  - src/beta.ts
---
EOF

test_begin "worktree_create: basic creation"

wt_path=$(worktree_create "spec-a" "testrun1234567890")
assert_contains "$wt_path" ".dark-factory/worktrees/spec-a-testrun123456789" "path under worktrees dir"

if [[ -d "$wt_path" ]]; then
    _test_pass "worktree directory exists"
else
    _test_fail "worktree directory does not exist"
fi

# Check branch was created
branch_exists=$(git -C "$_test_repo" branch --list "dark-factory/spec-a-testrun123456789*" | wc -l | tr -d ' ')
if [[ "$branch_exists" -ge 1 ]]; then
    _test_pass "branch created"
else
    _test_fail "branch not created"
fi

# Check .dark-factory subdirs
for subdir in specs scenarios reviews reports audit state; do
    if [[ -d "${wt_path}/.dark-factory/${subdir}" ]]; then
        _test_pass ".dark-factory/${subdir} created"
    else
        _test_fail ".dark-factory/${subdir} not created"
    fi
done

# Check spec file was copied
if [[ -f "${wt_path}/.dark-factory/specs/spec-a.md" ]]; then
    _test_pass "spec file copied into worktree"
else
    _test_fail "spec file not copied into worktree"
fi

test_begin "worktree_create: config.sh snapshot"

cat > "${_test_repo}/.dark-factory/config.sh" <<'EOF'
LANGUAGE="typescript"
EOF

wt_path2=$(worktree_create "spec-b" "testrun1234567890")
if [[ -f "${wt_path2}/.dark-factory/config.sh" ]]; then
    _test_pass "config.sh copied into worktree"
    cfg_content=$(cat "${wt_path2}/.dark-factory/config.sh")
    assert_contains "$cfg_content" "typescript" "config content preserved"
else
    _test_fail "config.sh not copied into worktree"
fi

test_begin "worktree_create: resume (branch already exists)"

# Remove worktree but keep the branch, then re-create should work
remove_rc=0
git -C "$_test_repo" worktree remove "$wt_path" --force || remove_rc=$?
assert_equals "0" "$remove_rc" "worktree remove succeeded (precondition for resume)"

rc=0
wt_path_resume=$(worktree_create "spec-a" "testrun1234567890") || rc=$?
assert_equals "0" "$rc" "resume case succeeds"
if [[ -d "$wt_path_resume" ]]; then
    _test_pass "resumed worktree directory exists"
else
    _test_fail "resumed worktree directory does not exist"
fi

# ==============================================================================
# worktree_list
# ==============================================================================

test_begin "worktree_list: lists created worktrees"

listed=$(worktree_list)
assert_contains "$listed" "spec-a" "lists spec-a worktree"
assert_contains "$listed" "spec-b" "lists spec-b worktree"

# ==============================================================================
# worktree_remove
# ==============================================================================

test_begin "worktree_remove: non-existent worktree is no-op"

rc=0
worktree_remove "nonexistent" "fakerunid00000000" 2>/dev/null || rc=$?
assert_equals "0" "$rc" "returns 0 for non-existent worktree"

test_begin "worktree_remove: refuses when no upstream"

# spec-a worktree branch was never pushed to origin
rc=0
worktree_remove "spec-a" "testrun1234567890" 2>/dev/null || rc=$?
assert_equals "1" "$rc" "refuses removal with no upstream"

test_begin "worktree_remove: refuses with uncommitted changes"

# Find spec-b worktree
wt_spec_b_actual=""
while IFS= read -r wt; do
    [[ "$wt" == *"spec-b"* ]] && wt_spec_b_actual="$wt"
done < <(worktree_list)

if [[ -n "$wt_spec_b_actual" ]]; then
    # Push branch to have upstream
    cd "$wt_spec_b_actual"
    git push -q -u origin HEAD 2>/dev/null || true
    # Create uncommitted change
    echo "dirty" > "${wt_spec_b_actual}/dirty-file.txt"
    cd "$_test_repo"

    rc=0
    worktree_remove "spec-b" "testrun1234567890" 2>/dev/null || rc=$?
    assert_equals "1" "$rc" "refuses removal with uncommitted changes"

    # Clean up the dirty file
    rm -f "${wt_spec_b_actual}/dirty-file.txt"
    cd "$_test_repo"
else
    _test_fail "could not find spec-b worktree for removal test"
fi

# ==============================================================================
# worktree_cleanup_all
# ==============================================================================

test_begin "worktree_cleanup_all: removes all dark-factory worktrees"

before_listed=$(worktree_list)
if [[ -n "$before_listed" ]]; then
    _test_pass "worktrees exist before cleanup"
else
    _test_fail "no worktrees exist before cleanup"
fi

worktree_cleanup_all

after_listed=$(worktree_list)
if [[ -z "$after_listed" ]]; then
    _test_pass "all worktrees removed"
else
    _test_fail "worktrees remain after cleanup: $after_listed"
fi

test_begin "worktree_cleanup_all: branches deleted"

branch_count=$(git -C "$_test_repo" branch --list 'dark-factory/*' | wc -l | tr -d ' ')
assert_equals "0" "$branch_count" "all dark-factory branches deleted"

# ==============================================================================
# worktree_list: empty after cleanup
# ==============================================================================

test_begin "worktree_list: empty when no worktrees"

result=$(worktree_list)
assert_equals "" "$result" "empty output when no worktrees"

# ==============================================================================
# Teardown
# ==============================================================================

cd "$TMPDIR_TEST"
cleanup_tmpdir
test_summary
