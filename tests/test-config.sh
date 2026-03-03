#!/usr/bin/env bash
set -euo pipefail

# tests/test-config.sh — Tests for scripts/lib/config.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"
source "${TESTS_DIR}/../scripts/lib/config.sh"

setup_tmpdir

# ==============================================================================
# validate_config
# ==============================================================================

test_begin "validate_config"

# Valid: assignments, comments, blanks
cat > "$TMPDIR_TEST/valid.sh" <<'CONF'
# This is a comment
LANGUAGE=node

SOURCE_DIR=src
export TEST_CMD="npm test"
CONF
assert_exit_code 0 "accepts valid config" validate_config "$TMPDIR_TEST/valid.sh"

# Valid: empty file
: > "$TMPDIR_TEST/empty.sh"
assert_exit_code 0 "accepts empty file" validate_config "$TMPDIR_TEST/empty.sh"

# Valid: missing file
assert_exit_code 0 "accepts missing file" validate_config "$TMPDIR_TEST/nonexistent.sh"

# Invalid: bare command
cat > "$TMPDIR_TEST/bare-cmd.sh" <<'CONF'
LANGUAGE=node
echo "hello"
CONF
assert_exit_code 1 "rejects bare commands" validate_config "$TMPDIR_TEST/bare-cmd.sh"

# Invalid: pipe
cat > "$TMPDIR_TEST/pipe.sh" <<'CONF'
LANGUAGE=node
cat /etc/passwd | grep root
CONF
assert_exit_code 1 "rejects pipes" validate_config "$TMPDIR_TEST/pipe.sh"

# Invalid: backtick command substitution
cat > "$TMPDIR_TEST/backtick.sh" <<'CONF'
LANGUAGE=`whoami`
CONF
# Note: this is a valid assignment line (backtick is in the value), so validate_config passes.
# The metacharacter check catches this instead.

# ==============================================================================
# validate_config_metacharacters
# ==============================================================================

test_begin "validate_config_metacharacters"

# Clean values
MY_VAR="hello"
MY_VAR2="world"
assert_exit_code 0 "accepts clean variables" validate_config_metacharacters MY_VAR MY_VAR2

# Value with $
MY_BAD='$HOME'
assert_exit_code 1 "rejects dollar sign" validate_config_metacharacters MY_BAD

# Value with backtick
MY_BAD='`whoami`'
assert_exit_code 1 "rejects backtick" validate_config_metacharacters MY_BAD

# Value with (
MY_BAD='$(cmd)'
assert_exit_code 1 "rejects open paren" validate_config_metacharacters MY_BAD

# Empty value is fine
MY_EMPTY=""
assert_exit_code 0 "accepts empty value" validate_config_metacharacters MY_EMPTY

# ==============================================================================
# validate_positive_integers
# ==============================================================================

test_begin "validate_positive_integers"

# Valid values
INT_A="1"
INT_B="100"
INT_C="42"
assert_exit_code 0 "accepts 1" validate_positive_integers INT_A
assert_exit_code 0 "accepts 100" validate_positive_integers INT_B
assert_exit_code 0 "accepts 42" validate_positive_integers INT_C

# Empty is allowed (not set)
INT_EMPTY=""
assert_exit_code 0 "accepts empty (unset)" validate_positive_integers INT_EMPTY

# Invalid: zero
INT_ZERO="0"
assert_exit_code 1 "rejects 0" validate_positive_integers INT_ZERO

# Invalid: negative
INT_NEG="-1"
assert_exit_code 1 "rejects -1" validate_positive_integers INT_NEG

# Invalid: non-numeric
INT_ABC="abc"
assert_exit_code 1 "rejects abc" validate_positive_integers INT_ABC

# Invalid: float
INT_FLOAT="1.5"
assert_exit_code 1 "rejects 1.5" validate_positive_integers INT_FLOAT

# ==============================================================================
# validate_spec_name
# ==============================================================================

test_begin "validate_spec_name"

assert_exit_code 0 "accepts simple name" validate_spec_name "my-feature"
assert_exit_code 0 "accepts dots and underscores" validate_spec_name "my-feature.v2"
assert_exit_code 0 "accepts all-numeric start" validate_spec_name "1-init"
assert_exit_code 0 "accepts underscore name" validate_spec_name "my_feature_v2"

assert_exit_code 1 "rejects path traversal" validate_spec_name "../hack"
assert_exit_code 1 "rejects hidden file" validate_spec_name ".hidden"
assert_exit_code 1 "rejects slash" validate_spec_name "foo/bar"
assert_exit_code 1 "rejects space" validate_spec_name "my feature"
assert_exit_code 1 "rejects empty" validate_spec_name ""

cleanup_tmpdir
test_summary
