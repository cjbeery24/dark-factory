#!/usr/bin/env bash
set -euo pipefail

# tests/test-barriers.sh — Tests for scripts/lib/barriers.sh

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"
source "${TESTS_DIR}/../scripts/lib/barriers.sh"

# ==============================================================================
# configure_barriers — single SOURCE_DIR
# ==============================================================================

test_begin "barriers single source dir"

SOURCE_DIRS="src"
SOURCE_DIR="src"
configure_barriers

# Implementer deny must block scenarios and reports
assert_contains "$IMPLEMENTER_DENY" ".dark-factory/scenarios" "implementer denies scenarios"
assert_contains "$IMPLEMENTER_DENY" ".dark-factory/reports" "implementer denies reports"
assert_contains "$IMPLEMENTER_DENY" "WebSearch" "implementer denies WebSearch"

# Implementer barrier prompt
assert_contains "$IMPLEMENTER_BARRIER" "MUST NOT" "implementer barrier has MUST NOT"
assert_contains "$IMPLEMENTER_BARRIER" ".dark-factory/scenarios/" "implementer barrier mentions scenarios"

# Scenario architect deny must block source dirs
assert_contains "$SCENARIO_ARCH_DENY" "Read(src/**)" "scenario-arch denies Read(src/**)"
assert_contains "$SCENARIO_ARCH_DENY" "Grep(src/**)" "scenario-arch denies Grep(src/**)"
assert_contains "$SCENARIO_ARCH_DENY" "WebSearch" "scenario-arch denies WebSearch"

# Scenario architect barrier prompt
assert_contains "$SCENARIO_ARCH_BARRIER" "MUST NOT" "scenario-arch barrier has MUST NOT"
assert_contains "$SCENARIO_ARCH_BARRIER" "src/" "scenario-arch barrier mentions src/"

# Reviewer deny
assert_contains "$REVIEWER_DENY" ".dark-factory/scenarios" "reviewer denies scenarios"
assert_contains "$REVIEWER_DENY" "WebSearch" "reviewer denies WebSearch"

# Reviewer barrier
assert_contains "$REVIEWER_BARRIER" "MUST NOT" "reviewer barrier has MUST NOT"

# Spec validator deny
assert_contains "$SPEC_VALIDATOR_DENY" ".dark-factory/scenarios" "spec-validator denies scenarios"

# Spec validator barrier
assert_contains "$SPEC_VALIDATOR_BARRIER" "MUST NOT" "spec-validator barrier has MUST NOT"

# Oracle and escalation deny
assert_contains "$ORACLE_DENY" "WebSearch" "oracle denies WebSearch"
assert_contains "$ESCALATION_DENY" "WebSearch" "escalation denies WebSearch"

# ==============================================================================
# configure_barriers — multiple SOURCE_DIRS
# ==============================================================================

test_begin "barriers multiple source dirs"

SOURCE_DIRS="src lib pkg"
configure_barriers

assert_contains "$SCENARIO_ARCH_DENY" "Read(src/**)" "multi: denies Read(src/**)"
assert_contains "$SCENARIO_ARCH_DENY" "Read(lib/**)" "multi: denies Read(lib/**)"
assert_contains "$SCENARIO_ARCH_DENY" "Read(pkg/**)" "multi: denies Read(pkg/**)"
assert_contains "$SCENARIO_ARCH_DENY" "Grep(lib/**)" "multi: denies Grep(lib/**)"
assert_contains "$SCENARIO_ARCH_BARRIER" "src/" "multi: barrier mentions src/"
assert_contains "$SCENARIO_ARCH_BARRIER" "lib/" "multi: barrier mentions lib/"
assert_contains "$SCENARIO_ARCH_BARRIER" "pkg/" "multi: barrier mentions pkg/"

# ==============================================================================
# Completeness: every role has a non-empty deny variable
# ==============================================================================

test_begin "barrier completeness"

SOURCE_DIRS="src"
configure_barriers

assert_not_equals "" "$IMPLEMENTER_DENY" "IMPLEMENTER_DENY is non-empty"
assert_not_equals "" "$SCENARIO_ARCH_DENY" "SCENARIO_ARCH_DENY is non-empty"
assert_not_equals "" "$REVIEWER_DENY" "REVIEWER_DENY is non-empty"
assert_not_equals "" "$SPEC_VALIDATOR_DENY" "SPEC_VALIDATOR_DENY is non-empty"
assert_not_equals "" "$ORACLE_DENY" "ORACLE_DENY is non-empty"
assert_not_equals "" "$ESCALATION_DENY" "ESCALATION_DENY is non-empty"

assert_not_equals "" "$IMPLEMENTER_BARRIER" "IMPLEMENTER_BARRIER is non-empty"
assert_not_equals "" "$SCENARIO_ARCH_BARRIER" "SCENARIO_ARCH_BARRIER is non-empty"
assert_not_equals "" "$REVIEWER_BARRIER" "REVIEWER_BARRIER is non-empty"
assert_not_equals "" "$SPEC_VALIDATOR_BARRIER" "SPEC_VALIDATOR_BARRIER is non-empty"

test_summary
