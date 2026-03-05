#!/usr/bin/env bash
set -euo pipefail

# tests/test-plan-executor.sh — Tests for scripts/dark-factory-plan.sh (Phase 4)
# Tests: parse_plan, topological_sort, serialize_conflicting_specs
# Requires: bash 4+ (associative arrays)

TESTS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${TESTS_DIR}/lib/test-helpers.sh"

if [[ "${BASH_VERSINFO[0]}" -lt 4 ]]; then
    echo "  SKIP  test-plan-executor.sh (requires bash 4+, have bash ${BASH_VERSION})"
    exit 0
fi

setup_tmpdir

# ==============================================================================
# Setup: Create temp project structure, source plan executor
# ==============================================================================

_test_root="${TMPDIR_TEST}/project"
mkdir -p "$_test_root/.dark-factory/plans"
mkdir -p "$_test_root/.dark-factory/specs"

# Source common.sh first (sets DF_PROJECT_ROOT)
source "${TESTS_DIR}/../scripts/lib/common.sh"

# Override to test root
DF_PROJECT_ROOT="$_test_root"
_DF_WORKTREE_BASE="${DF_PROJECT_ROOT}/.dark-factory/worktrees"
_DF_REGISTRY_DIR="${DF_PROJECT_ROOT}/.dark-factory/shipped-scenarios"
_DF_SCRIPTS_DIR="${TESTS_DIR}/../scripts"

# Source the plan executor (functions only, BASH_SOURCE guard skips main)
source "${TESTS_DIR}/../scripts/dark-factory-plan.sh"

# Helper: reset plan state between tests
_reset_plan_state() {
    PLAN_SPECS=()
    PLAN_DEPS=()
    PLAN_PRIORITY=()
    DISPATCH_ORDER=()
}

# ==============================================================================
# parse_plan: well-formed plan table
# ==============================================================================

test_begin "parse_plan: well-formed plan table"

_reset_plan_state

cat > "${_test_root}/.dark-factory/plans/test-plan.md" <<'EOF'
# Test Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| auth-service | — | 1 |
| user-api | auth-service | 2 |
| dashboard | user-api | 3 |
EOF

# Create matching spec files (no depends-on metadata, so plan table deps are used)
for spec in auth-service user-api dashboard; do
    cat > "${_test_root}/.dark-factory/specs/${spec}.md" <<SPEC
---
spec-name: ${spec}
target-files:
  - src/${spec}.ts
---
# ${spec}
SPEC
done

rc=0
parse_plan "${_test_root}/.dark-factory/plans/test-plan.md" || rc=$?
assert_equals "0" "$rc" "parse returns 0"
assert_equals "3" "${#PLAN_SPECS[@]}" "3 specs parsed"
assert_equals "auth-service" "${PLAN_SPECS[0]}" "first spec is auth-service"
assert_equals "user-api" "${PLAN_SPECS[1]}" "second spec is user-api"
assert_equals "dashboard" "${PLAN_SPECS[2]}" "third spec is dashboard"
assert_equals "" "${PLAN_DEPS[auth-service]}" "auth-service has no deps"
assert_equals "auth-service" "${PLAN_DEPS[user-api]}" "user-api depends on auth-service"
assert_equals "user-api" "${PLAN_DEPS[dashboard]}" "dashboard depends on user-api"
assert_equals "1" "${PLAN_PRIORITY[auth-service]}" "auth-service priority is 1"

# ==============================================================================
# parse_plan: spec metadata depends-on overrides plan table
# ==============================================================================

test_begin "parse_plan: spec metadata overrides plan deps"

_reset_plan_state

cat > "${_test_root}/.dark-factory/plans/override-plan.md" <<'EOF'
# Override Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-x | — | 1 |
| spec-y | — | 2 |
EOF

cat > "${_test_root}/.dark-factory/specs/spec-x.md" <<'EOF'
---
spec-name: spec-x
target-files:
  - src/x.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/spec-y.md" <<'EOF'
---
spec-name: spec-y
depends-on: [spec-x]
target-files:
  - src/y.ts
---
EOF

parse_plan "${_test_root}/.dark-factory/plans/override-plan.md"
assert_equals "spec-x" "${PLAN_DEPS[spec-y]}" "metadata depends-on overrides plan table"

# ==============================================================================
# parse_plan: em-dash and hyphen both mean "no deps"
# ==============================================================================

test_begin "parse_plan: em-dash and hyphen as no-deps"

_reset_plan_state

# Overwrite spec files to remove depends-on metadata from previous test
cat > "${_test_root}/.dark-factory/specs/spec-x.md" <<'EOF'
---
spec-name: spec-x
target-files:
  - src/x.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/spec-y.md" <<'EOF'
---
spec-name: spec-y
target-files:
  - src/y.ts
---
EOF

cat > "${_test_root}/.dark-factory/plans/dash-plan.md" <<'EOF'
# Dash Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-x | — | 1 |
| spec-y | - | 2 |
EOF

parse_plan "${_test_root}/.dark-factory/plans/dash-plan.md"
assert_equals "" "${PLAN_DEPS[spec-x]}" "em-dash means no deps"
assert_equals "" "${PLAN_DEPS[spec-y]}" "hyphen means no deps"

# ==============================================================================
# parse_plan: missing dep reference
# ==============================================================================

test_begin "parse_plan: missing dep reference returns 1"

_reset_plan_state

cat > "${_test_root}/.dark-factory/plans/bad-dep-plan.md" <<'EOF'
# Bad Dep Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-x | nonexistent | 1 |
EOF

rc=0
parse_plan "${_test_root}/.dark-factory/plans/bad-dep-plan.md" 2>/dev/null || rc=$?
assert_equals "1" "$rc" "missing dep returns 1"

# ==============================================================================
# parse_plan: empty plan
# ==============================================================================

test_begin "parse_plan: empty plan returns 1"

_reset_plan_state

cat > "${_test_root}/.dark-factory/plans/empty-plan.md" <<'EOF'
# Empty Plan

No table here.
EOF

rc=0
parse_plan "${_test_root}/.dark-factory/plans/empty-plan.md" 2>/dev/null || rc=$?
assert_equals "1" "$rc" "empty plan returns 1"

# ==============================================================================
# parse_plan: duplicate spec names (silent overwrite)
# ==============================================================================

test_begin "parse_plan: duplicate spec names"

_reset_plan_state

cat > "${_test_root}/.dark-factory/plans/dup-plan.md" <<'EOF'
# Duplicate Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-x | — | 1 |
| spec-x | — | 5 |
EOF

rc=0
parse_plan "${_test_root}/.dark-factory/plans/dup-plan.md" 2>/dev/null || rc=$?
assert_equals "1" "$rc" "duplicate spec name returns 1"

# ==============================================================================
# topological_sort: linear chain
# ==============================================================================

test_begin "topological_sort: linear chain A→B→C"

_reset_plan_state

cat > "${_test_root}/.dark-factory/plans/linear-plan.md" <<'EOF'
# Linear Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-x | — | 1 |
| spec-y | spec-x | 2 |
| spec-z | spec-y | 3 |
EOF

parse_plan "${_test_root}/.dark-factory/plans/linear-plan.md"
topological_sort

assert_equals "spec-x" "${DISPATCH_ORDER[0]}" "spec-x dispatched first"
assert_equals "spec-y" "${DISPATCH_ORDER[1]}" "spec-y dispatched second"
assert_equals "spec-z" "${DISPATCH_ORDER[2]}" "spec-z dispatched third"

# ==============================================================================
# topological_sort: diamond dependency
# ==============================================================================

test_begin "topological_sort: diamond A→B,C→D"

_reset_plan_state

cat > "${_test_root}/.dark-factory/specs/spec-d.md" <<'EOF'
---
spec-name: spec-d
depends-on: [spec-y,spec-z]
target-files:
  - src/d.ts
---
EOF

cat > "${_test_root}/.dark-factory/plans/diamond-plan.md" <<'EOF'
# Diamond Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-x | — | 1 |
| spec-y | spec-x | 2 |
| spec-z | spec-x | 2 |
| spec-d | spec-y,spec-z | 3 |
EOF

# Reset spec-y and spec-z to not have metadata deps (use plan table)
cat > "${_test_root}/.dark-factory/specs/spec-y.md" <<'EOF'
---
spec-name: spec-y
target-files:
  - src/y.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/spec-z.md" <<'EOF'
---
spec-name: spec-z
target-files:
  - src/z.ts
---
EOF

parse_plan "${_test_root}/.dark-factory/plans/diamond-plan.md"
topological_sort

assert_equals "spec-x" "${DISPATCH_ORDER[0]}" "spec-x dispatched first"
# spec-y and spec-z can be in either order, but both before spec-d
assert_equals "spec-d" "${DISPATCH_ORDER[3]}" "spec-d dispatched last"

# Verify spec-d comes after both spec-y and spec-z
y_idx=-1; z_idx=-1; d_idx=-1
for i in "${!DISPATCH_ORDER[@]}"; do
    case "${DISPATCH_ORDER[$i]}" in
        spec-y) y_idx=$i ;;
        spec-z) z_idx=$i ;;
        spec-d) d_idx=$i ;;
    esac
done
if [[ $d_idx -gt $y_idx ]] && [[ $d_idx -gt $z_idx ]]; then
    _test_pass "spec-d comes after both spec-y and spec-z"
else
    _test_fail "spec-d should come after both spec-y and spec-z (y=$y_idx z=$z_idx d=$d_idx)"
fi

# ==============================================================================
# topological_sort: independent specs
# ==============================================================================

test_begin "topological_sort: independent specs (no deps)"

_reset_plan_state

cat > "${_test_root}/.dark-factory/plans/independent-plan.md" <<'EOF'
# Independent Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-x | — | 1 |
| spec-y | — | 2 |
| spec-z | — | 3 |
EOF

parse_plan "${_test_root}/.dark-factory/plans/independent-plan.md"
topological_sort

assert_equals "3" "${#DISPATCH_ORDER[@]}" "all 3 specs in dispatch order"

# ==============================================================================
# topological_sort: circular dependency
# ==============================================================================

test_begin "topological_sort: circular dependency detected"

_reset_plan_state

# Manually set up circular deps (A→B→A)
PLAN_SPECS=("spec-x" "spec-y")
PLAN_DEPS=([spec-x]="spec-y" [spec-y]="spec-x")
PLAN_PRIORITY=([spec-x]="1" [spec-y]="2")

rc=0
topological_sort 2>/dev/null || rc=$?
assert_equals "1" "$rc" "circular dependency returns 1"

# ==============================================================================
# topological_sort: self-loop
# ==============================================================================

test_begin "topological_sort: self-loop detected"

_reset_plan_state

PLAN_SPECS=("spec-x")
PLAN_DEPS=([spec-x]="spec-x")
PLAN_PRIORITY=([spec-x]="1")

rc=0
topological_sort 2>/dev/null || rc=$?
assert_equals "1" "$rc" "self-loop returns 1"

# ==============================================================================
# topological_sort: priority ordering among independent specs
# ==============================================================================

test_begin "topological_sort: independent specs sorted by priority"

_reset_plan_state

cat > "${_test_root}/.dark-factory/specs/spec-p10.md" <<'EOF'
---
spec-name: spec-p10
target-files:
  - src/p10.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/spec-p1.md" <<'EOF'
---
spec-name: spec-p1
target-files:
  - src/p1.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/spec-p5.md" <<'EOF'
---
spec-name: spec-p5
target-files:
  - src/p5.ts
---
EOF

cat > "${_test_root}/.dark-factory/plans/priority-plan.md" <<'EOF'
# Priority Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-p10 | — | 10 |
| spec-p1 | — | 1 |
| spec-p5 | — | 5 |
EOF

parse_plan "${_test_root}/.dark-factory/plans/priority-plan.md"
topological_sort

assert_equals "spec-p1" "${DISPATCH_ORDER[0]}" "priority 1 dispatched first"
assert_equals "spec-p5" "${DISPATCH_ORDER[1]}" "priority 5 dispatched second"
assert_equals "spec-p10" "${DISPATCH_ORDER[2]}" "priority 10 dispatched third"

# ==============================================================================
# topological_sort: tiered priority (dependents sorted by priority after root)
# ==============================================================================

test_begin "topological_sort: tiered dependents sorted by priority"

_reset_plan_state

cat > "${_test_root}/.dark-factory/specs/spec-root.md" <<'EOF'
---
spec-name: spec-root
target-files:
  - src/root.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/spec-child-lo.md" <<'EOF'
---
spec-name: spec-child-lo
target-files:
  - src/child-lo.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/spec-child-hi.md" <<'EOF'
---
spec-name: spec-child-hi
target-files:
  - src/child-hi.ts
---
EOF

cat > "${_test_root}/.dark-factory/plans/tiered-plan.md" <<'EOF'
# Tiered Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-root | — | 1 |
| spec-child-lo | spec-root | 10 |
| spec-child-hi | spec-root | 2 |
EOF

parse_plan "${_test_root}/.dark-factory/plans/tiered-plan.md"
topological_sort

assert_equals "spec-root" "${DISPATCH_ORDER[0]}" "root dispatched first"
assert_equals "spec-child-hi" "${DISPATCH_ORDER[1]}" "higher priority child dispatched second"
assert_equals "spec-child-lo" "${DISPATCH_ORDER[2]}" "lower priority child dispatched third"

# ==============================================================================
# topological_sort: default priority (empty column) treated as 99
# ==============================================================================

test_begin "topological_sort: empty priority defaults to 99"

_reset_plan_state

cat > "${_test_root}/.dark-factory/specs/spec-explicit.md" <<'EOF'
---
spec-name: spec-explicit
target-files:
  - src/explicit.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/spec-default.md" <<'EOF'
---
spec-name: spec-default
target-files:
  - src/default.ts
---
EOF

cat > "${_test_root}/.dark-factory/plans/default-priority-plan.md" <<'EOF'
# Default Priority Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-default | — |  |
| spec-explicit | — | 1 |
EOF

parse_plan "${_test_root}/.dark-factory/plans/default-priority-plan.md"
topological_sort

assert_equals "spec-explicit" "${DISPATCH_ORDER[0]}" "explicit priority 1 dispatched before default 99"
assert_equals "spec-default" "${DISPATCH_ORDER[1]}" "default priority dispatched last"

# ==============================================================================
# serialize_conflicting_specs: shared file creates synthetic dep
# ==============================================================================

test_begin "serialize_conflicting_specs: shared file serialized"

_reset_plan_state

cat > "${_test_root}/.dark-factory/specs/spec-x.md" <<'EOF'
---
spec-name: spec-x
target-files:
  - src/shared.ts
  - src/only-x.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/spec-y.md" <<'EOF'
---
spec-name: spec-y
target-files:
  - src/shared.ts
  - src/only-y.ts
---
EOF

cat > "${_test_root}/.dark-factory/plans/conflict-plan.md" <<'EOF'
# Conflict Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-x | — | 1 |
| spec-y | — | 2 |
EOF

parse_plan "${_test_root}/.dark-factory/plans/conflict-plan.md"
topological_sort

# Before serialization, both are independent
assert_equals "" "${PLAN_DEPS[spec-x]:-}" "spec-x starts with no deps"
assert_equals "" "${PLAN_DEPS[spec-y]:-}" "spec-y starts with no deps"

serialize_conflicting_specs 2>/dev/null

# After serialization, spec-y should depend on spec-x (first in dispatch order)
assert_contains "${PLAN_DEPS[spec-y]:-}" "spec-x" "spec-y now depends on spec-x"

# Re-sort should produce valid order
topological_sort
assert_equals "spec-x" "${DISPATCH_ORDER[0]}" "spec-x still first after re-sort"
assert_equals "spec-y" "${DISPATCH_ORDER[1]}" "spec-y second after re-sort"

# ==============================================================================
# serialize_conflicting_specs: no conflicts
# ==============================================================================

test_begin "serialize_conflicting_specs: no conflicts"

_reset_plan_state

cat > "${_test_root}/.dark-factory/specs/spec-x.md" <<'EOF'
---
spec-name: spec-x
target-files:
  - src/only-x.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/spec-y.md" <<'EOF'
---
spec-name: spec-y
target-files:
  - src/only-y.ts
---
EOF

cat > "${_test_root}/.dark-factory/plans/noconflict-plan.md" <<'EOF'
# No Conflict Plan

| Spec | Depends On | Priority |
|------|-----------|----------|
| spec-x | — | 1 |
| spec-y | — | 2 |
EOF

parse_plan "${_test_root}/.dark-factory/plans/noconflict-plan.md"
topological_sort
serialize_conflicting_specs 2>/dev/null

assert_equals "" "${PLAN_DEPS[spec-x]:-}" "spec-x still has no deps"
assert_equals "" "${PLAN_DEPS[spec-y]:-}" "spec-y still has no deps"

# ==============================================================================
# Bug 5: serialization-induced cycle gives specific error
# ==============================================================================

test_begin "serialize + topological_sort: detects serialization-induced cycle"

_reset_plan_state

# Set up: spec-a depends on spec-b, spec-b depends on spec-c, spec-c is root.
# spec-a and spec-c share a file. serialize_conflicting_specs sees spec-c first
# in DISPATCH_ORDER and adds spec-a→spec-c. But spec-a→spec-b→...→spec-c already
# implies spec-a after spec-c, so no cycle there.
#
# For a real cycle: spec-x depends on spec-y (explicit dep), and they share a file.
# serialize sees spec-y first (it has no deps), adds spec-x→spec-y (already exists — no-op).
# That won't cycle. We need the OPPOSITE direction.
#
# True cycle setup: 3 specs. spec-a→spec-b (explicit). spec-b and spec-c share file-1.
# spec-c and spec-a share file-2. DISPATCH_ORDER after first sort: spec-c, spec-a (or spec-b), ...
# serialize: spec-b→spec-c (shared file-1, spec-c is first in dispatch), spec-a→spec-c (shared file-2).
# Result: spec-a→spec-b→spec-c, spec-a→spec-c. No cycle still.
#
# Simplest forced cycle: manually set DISPATCH_ORDER to control which spec "owns" the file first.

cat > "${_test_root}/.dark-factory/specs/cyc-a.md" <<'EOF'
---
spec-name: cyc-a
target-files:
  - src/shared-cyc.ts
---
EOF

cat > "${_test_root}/.dark-factory/specs/cyc-b.md" <<'EOF'
---
spec-name: cyc-b
target-files:
  - src/shared-cyc.ts
---
EOF

PLAN_SPECS=("cyc-a" "cyc-b")
# cyc-a depends on cyc-b
PLAN_DEPS=([cyc-a]="cyc-b" [cyc-b]="")
PLAN_PRIORITY=([cyc-a]="2" [cyc-b]="1")

topological_sort  # DISPATCH_ORDER: [cyc-b, cyc-a]
# serialize_conflicting_specs: cyc-b is first in DISPATCH_ORDER, owns shared-cyc.ts.
# cyc-a also targets it → adds cyc-a depends on cyc-b (already exists, no-op).
# This doesn't create a cycle.

# To force a cycle, we need serialize to add cyc-b→cyc-a while cyc-a→cyc-b already exists.
# That requires cyc-a to come FIRST in DISPATCH_ORDER. Override manually:
DISPATCH_ORDER=("cyc-a" "cyc-b")
serialize_conflicting_specs 2>/dev/null  # Adds cyc-b→cyc-a (cyc-a owns the file)
# Now: cyc-a→cyc-b AND cyc-b→cyc-a = cycle

rc=0
topological_sort 2>/dev/null || rc=$?
assert_equals "1" "$rc" "cycle detected after serialization"

# ==============================================================================
# Teardown
# ==============================================================================

cleanup_tmpdir
test_summary
