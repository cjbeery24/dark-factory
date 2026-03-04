#!/usr/bin/env bash
set -euo pipefail

# tests/test-symlinks.sh — Tests for check_symlink_safety and check_hardlink_safety

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"
source "${TESTS_DIR}/../scripts/lib/config.sh"
source "${TESTS_DIR}/../scripts/lib/audit.sh"
source "${TESTS_DIR}/../scripts/lib/barriers.sh"
source "${TESTS_DIR}/../scripts/lib/docker.sh"

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

# ==============================================================================
# check_hardlink_safety (Item 23)
# ==============================================================================

test_begin "hardlink safety — clean pass"

mkdir -p "$TMPDIR_TEST/hl-src" "$TMPDIR_TEST/hl-scenarios"
echo "code" > "$TMPDIR_TEST/hl-src/app.js"
echo "test" > "$TMPDIR_TEST/hl-scenarios/test.js"
assert_exit_code 0 "no hardlinks passes" check_hardlink_safety "$TMPDIR_TEST/hl-src" "$TMPDIR_TEST/hl-scenarios"

test_begin "hardlink safety — barrier crossing detected"

mkdir -p "$TMPDIR_TEST/hl2-src" "$TMPDIR_TEST/hl2-scenarios"
echo "secret" > "$TMPDIR_TEST/hl2-scenarios/secret-test.js"
# Create hardlink from source dir to scenarios file
ln "$TMPDIR_TEST/hl2-scenarios/secret-test.js" "$TMPDIR_TEST/hl2-src/leaked-test.js"
assert_exit_code 1 "hardlink crossing barrier detected" check_hardlink_safety "$TMPDIR_TEST/hl2-src" "$TMPDIR_TEST/hl2-scenarios"

test_begin "hardlink safety — missing directory"

assert_exit_code 0 "missing scan dir passes" check_hardlink_safety "$TMPDIR_TEST/nonexistent" "$TMPDIR_TEST/hl-scenarios"
assert_exit_code 0 "missing forbidden dir passes" check_hardlink_safety "$TMPDIR_TEST/hl-src" "$TMPDIR_TEST/nonexistent"

cleanup_tmpdir
test_summary
