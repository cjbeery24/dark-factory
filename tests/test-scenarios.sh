#!/usr/bin/env bash
set -euo pipefail

# tests/test-scenarios.sh — Tests for scripts/lib/scenarios.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"
source "${TESTS_DIR}/../scripts/lib/scenarios.sh"

setup_tmpdir

# ==============================================================================
# has_scenario_files
# ==============================================================================

test_begin "has_scenario_files"

# Dir with .test.ts file
mkdir -p "$TMPDIR_TEST/ts-scenarios"
touch "$TMPDIR_TEST/ts-scenarios/auth.test.ts"
assert_exit_code 0 "detects *.test.ts" has_scenario_files "$TMPDIR_TEST/ts-scenarios"

# Dir with .spec.js file
mkdir -p "$TMPDIR_TEST/js-scenarios"
touch "$TMPDIR_TEST/js-scenarios/api.spec.js"
assert_exit_code 0 "detects *.spec.js" has_scenario_files "$TMPDIR_TEST/js-scenarios"

# Dir with test_*.py file
mkdir -p "$TMPDIR_TEST/py-scenarios"
touch "$TMPDIR_TEST/py-scenarios/test_auth.py"
assert_exit_code 0 "detects test_*.py" has_scenario_files "$TMPDIR_TEST/py-scenarios"

# Dir with *_test.py file
mkdir -p "$TMPDIR_TEST/py-scenarios2"
touch "$TMPDIR_TEST/py-scenarios2/auth_test.py"
assert_exit_code 0 "detects *_test.py" has_scenario_files "$TMPDIR_TEST/py-scenarios2"

# Dir with *_test.go file
mkdir -p "$TMPDIR_TEST/go-scenarios"
touch "$TMPDIR_TEST/go-scenarios/auth_test.go"
assert_exit_code 0 "detects *_test.go" has_scenario_files "$TMPDIR_TEST/go-scenarios"

# Dir with .rs file
mkdir -p "$TMPDIR_TEST/rust-scenarios"
touch "$TMPDIR_TEST/rust-scenarios/test_auth.rs"
assert_exit_code 0 "detects *.rs" has_scenario_files "$TMPDIR_TEST/rust-scenarios"

# Empty dir — no scenarios
mkdir -p "$TMPDIR_TEST/empty-dir"
assert_exit_code 1 "returns 1 for empty dir" has_scenario_files "$TMPDIR_TEST/empty-dir"

# Dir with only .DS_Store
mkdir -p "$TMPDIR_TEST/ds-store-dir"
touch "$TMPDIR_TEST/ds-store-dir/.DS_Store"
assert_exit_code 1 "returns 1 for dir with only .DS_Store" has_scenario_files "$TMPDIR_TEST/ds-store-dir"

# Dir with only .gitkeep
mkdir -p "$TMPDIR_TEST/gitkeep-dir"
touch "$TMPDIR_TEST/gitkeep-dir/.gitkeep"
assert_exit_code 1 "returns 1 for dir with only .gitkeep" has_scenario_files "$TMPDIR_TEST/gitkeep-dir"

# Dir with non-test file
mkdir -p "$TMPDIR_TEST/non-test-dir"
touch "$TMPDIR_TEST/non-test-dir/README.md"
assert_exit_code 1 "returns 1 for dir with only README.md" has_scenario_files "$TMPDIR_TEST/non-test-dir"

cleanup_tmpdir
test_summary
