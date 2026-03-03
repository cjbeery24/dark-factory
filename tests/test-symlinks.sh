#!/usr/bin/env bash
set -euo pipefail

# tests/test-symlinks.sh — Tests for check_symlink_safety

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

# Source check_symlink_safety — it's in docker.sh but we need it standalone.
# For now, define it inline (it will move to docker.sh in Phase 5).
# This test validates the logic regardless of where the function lives.

check_symlink_safety() {
    local dir="$1" forbidden="$2" max_depth="${3:-}"
    local -a find_args=("$dir")
    [[ -n "$max_depth" ]] && find_args+=(-maxdepth "$max_depth")
    find_args+=(-type l)
    while IFS= read -r link; do
        local target
        target=$(readlink -f "$link" 2>/dev/null) || continue
        if [[ "$target" == *"$forbidden"* ]]; then
            echo "ERROR: Symlink '$link' points into restricted area '$forbidden'"
            echo "This would bypass the information barrier. Remove or fix the symlink."
            return 1
        fi
    done < <(find "${find_args[@]}" 2>/dev/null)
    return 0
}

setup_tmpdir

# ==============================================================================
# check_symlink_safety
# ==============================================================================

test_begin "symlink safety"

# Safe symlink — points to non-restricted path
mkdir -p "$TMPDIR_TEST/safe-target"
mkdir -p "$TMPDIR_TEST/project"
echo "safe" > "$TMPDIR_TEST/safe-target/file.txt"
ln -s "$TMPDIR_TEST/safe-target/file.txt" "$TMPDIR_TEST/project/safe-link"
assert_exit_code 0 "safe symlink passes" check_symlink_safety "$TMPDIR_TEST/project" ".dark-factory/scenarios"

# Dangerous symlink — points into restricted area
mkdir -p "$TMPDIR_TEST/project2/.dark-factory/scenarios"
echo "secret" > "$TMPDIR_TEST/project2/.dark-factory/scenarios/test.ts"
ln -s "$TMPDIR_TEST/project2/.dark-factory/scenarios/test.ts" "$TMPDIR_TEST/project2/sneaky-link"
assert_exit_code 1 "symlink into restricted area fails" check_symlink_safety "$TMPDIR_TEST/project2" ".dark-factory/scenarios"

# No symlinks at all
mkdir -p "$TMPDIR_TEST/project3"
echo "normal" > "$TMPDIR_TEST/project3/normal-file.txt"
assert_exit_code 0 "no symlinks passes" check_symlink_safety "$TMPDIR_TEST/project3" ".dark-factory/scenarios"

# Missing directory doesn't crash
assert_exit_code 0 "missing directory doesn't crash" check_symlink_safety "$TMPDIR_TEST/nonexistent" ".dark-factory/scenarios"

# max_depth parameter works
mkdir -p "$TMPDIR_TEST/project4/deep/nested"
mkdir -p "$TMPDIR_TEST/project4/.dark-factory/scenarios"
echo "secret" > "$TMPDIR_TEST/project4/.dark-factory/scenarios/test.ts"
ln -s "$TMPDIR_TEST/project4/.dark-factory/scenarios/test.ts" "$TMPDIR_TEST/project4/deep/nested/deep-link"
# With depth 1, the deep symlink should not be found
assert_exit_code 0 "max_depth=1 skips deep symlinks" check_symlink_safety "$TMPDIR_TEST/project4" ".dark-factory/scenarios" 1

cleanup_tmpdir
test_summary
