# Phase 4: Concurrent Multi-Spec Execution — Implementation Plan

**Date:** 2026-03-04
**Prerequisite:** Phases 1–3 complete
**Estimated Scope:** 6 sub-phases, ~1,200–1,500 lines of new/modified code (revised per review)
**Goal:** Enable parallel spec processing with git worktree isolation, dependency-ordered dispatch, cross-spec regression, and a shipped scenario registry

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Sub-Phase 4.1: Git Worktree Isolation](#sub-phase-41-git-worktree-isolation)
3. [Sub-Phase 4.2: Plan Executor & Dependency Graph](#sub-phase-42-plan-executor--dependency-graph)
4. [Sub-Phase 4.3: Resource Governor](#sub-phase-43-resource-governor)
5. [Sub-Phase 4.4: Cross-Spec Conflict Detection](#sub-phase-44-cross-spec-conflict-detection)
6. [Sub-Phase 4.5: Shipped Scenario Registry](#sub-phase-45-shipped-scenario-registry)
7. [Sub-Phase 4.6: Full Regression as Merge Gate](#sub-phase-46-full-regression-as-merge-gate)
8. [File Change Summary](#file-change-summary)
9. [Config Additions](#config-additions)
10. [Testing Strategy](#testing-strategy)
11. [Migration & Backward Compatibility](#migration--backward-compatibility)
12. [Risk Analysis](#risk-analysis)

---

## 1. Architecture Overview

### Current: Single-Spec Sequential

```
Human runs: ./scripts/dark-factory.sh my-spec
  → single spec, single branch, git checkout switches HEAD
  → sequential: validate → scenarios → implement → gate → review → oracle
  → post-SHIP: regression (serial), PR creation
```

### Target: Multi-Spec Parallel with Coordinator

```
Human/CI runs: ./scripts/dark-factory-plan.sh my-plan
  → reads plan from .dark-factory/plans/my-plan.md
  → builds dependency graph from spec metadata
  → dispatches independent specs in parallel (respecting MAX_CONCURRENT_SPECS)
  → each spec runs in isolated git worktree
  → on SHIP: registers scenarios, triggers cross-spec regression
  → on regression pass: creates PR from worktree branch
  → coordinator waits for all specs, reports aggregate results
```

### Key Design Decisions

1. **Worktrees over long-lived branches.** Git worktrees provide true filesystem isolation — each spec has its own directory with its own working tree. Docker containers bind-mount the worktree path instead of the repo root. This eliminates all checkout-based race conditions.

2. **New orchestrator script, not a rewrite.** `dark-factory.sh` remains the single-spec pipeline. A new `dark-factory-plan.sh` orchestrator dispatches multiple `dark-factory.sh` processes, each targeting a worktree. This preserves backward compatibility and limits blast radius.

3. **Bash process management with exit-code files.** The coordinator uses bash background jobs (`&`) with exit-code file signaling for portable child reaping. Each child writes its exit code to `.dark-factory/worktrees/{spec}/exit_code` on completion. The coordinator polls with `kill -0` and reads exit-code files — no `wait -n` (bash 4.3+) or `wait -p` (bash 5.1+) dependency, fully compatible with macOS's bash 3.2. Each child process is a full `dark-factory.sh` invocation.

4. **Dependency graph via topological sort.** Specs declare `depends-on: [spec-id, ...]` in their metadata block (already parsed by `check_dependencies()`). The coordinator performs a topological sort and dispatches specs in waves: all specs with satisfied dependencies start immediately, up to the concurrency cap.

5. **Shipped scenario registry as a persistent directory.** When a spec ships, its scenarios are copied to `.dark-factory/shipped-scenarios/{spec}/`. This directory persists independently of state files and serves as the cumulative regression suite. The existing state-file-based regression is replaced.

---

## Sub-Phase 4.1: Git Worktree Isolation

### Problem

`git-ops.sh` uses `git checkout -b` which moves HEAD for the entire working directory. Two concurrent specs would race on branch switching, causing corruption.

### Solution

New library `scripts/lib/worktree.sh` providing worktree lifecycle management.

### New File: `scripts/lib/worktree.sh` (~120 lines)

```bash
#!/usr/bin/env bash
# Git worktree lifecycle management for concurrent spec execution

# Worktree base directory (under project root, gitignored)
_DF_WORKTREE_BASE="${DF_PROJECT_ROOT}/.dark-factory/worktrees"

# Create an isolated worktree for a spec
# Usage: worktree_create <spec_name> <run_id>
# Sets: _DF_WORKTREE_PATH, _DF_WORKTREE_BRANCH
worktree_create() {
    local spec="$1" run_id="$2"
    local branch="dark-factory/${spec}-${run_id:0:16}"
    local worktree_path="${_DF_WORKTREE_BASE}/${spec}-${run_id:0:16}"

    # Ensure base directory exists
    mkdir -p "$_DF_WORKTREE_BASE"

    # Create worktree from base branch (default: main)
    local base="${GIT_BASE_BRANCH:-main}"
    git -C "$DF_PROJECT_ROOT" worktree add \
        "$worktree_path" \
        -b "$branch" \
        "${GIT_REMOTE:-origin}/${base}" 2>/dev/null \
    || {
        # Branch may already exist (resume case) — reuse it
        git -C "$DF_PROJECT_ROOT" worktree add \
            "$worktree_path" \
            "$branch" 2>/dev/null \
        || { echo "ERROR: Failed to create worktree for ${spec}" >&2; return 1; }
    }

    _DF_WORKTREE_PATH="$worktree_path"
    _DF_WORKTREE_BRANCH="$branch"

    # Copy .dark-factory runtime dirs into worktree (scenarios, state, etc.)
    # These are spec-specific so no conflict
    mkdir -p "${worktree_path}/.dark-factory/"{specs,scenarios,reviews,reports,audit,state}

    # Copy config into worktree (snapshot — avoids symlink crossing isolation
    # boundary and prevents mid-execution config drift across concurrent specs)
    if [[ -f "${DF_PROJECT_ROOT}/.dark-factory/config.sh" ]]; then
        cp "${DF_PROJECT_ROOT}/.dark-factory/config.sh" \
           "${worktree_path}/.dark-factory/config.sh"
    fi

    # Copy the spec file into the worktree
    if [[ -f "${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md" ]]; then
        cp "${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md" \
           "${worktree_path}/.dark-factory/specs/${spec}.md"
    fi

    echo "$worktree_path"
}

# Remove a worktree after spec completes
# Usage: worktree_remove <spec_name> <run_id>
worktree_remove() {
    local spec="$1" run_id="$2"
    local worktree_path="${_DF_WORKTREE_BASE}/${spec}-${run_id:0:12}"
    local branch="dark-factory/${spec}-${run_id:0:12}"

    if [[ -d "$worktree_path" ]]; then
        # Safety check: verify branch was pushed before force-removing
        local unpushed
        unpushed=$(git -C "$worktree_path" log --oneline "@{upstream}..HEAD" 2>/dev/null | wc -l | tr -d ' ')
        if [[ "$unpushed" -gt 0 ]]; then
            echo "WARNING: Worktree ${spec} has ${unpushed} unpushed commit(s) — skipping removal" >&2
            return 1
        fi
        # Check for uncommitted changes
        if ! git -C "$worktree_path" diff --quiet 2>/dev/null || \
           ! git -C "$worktree_path" diff --cached --quiet 2>/dev/null; then
            echo "WARNING: Worktree ${spec} has uncommitted changes — skipping removal" >&2
            return 1
        fi
        git -C "$DF_PROJECT_ROOT" worktree remove "$worktree_path" --force 2>/dev/null
    fi

    # Clean up branch if it was merged
    git -C "$DF_PROJECT_ROOT" branch -d "$branch" 2>/dev/null || true
}

# List all active worktrees
# Usage: worktree_list
worktree_list() {
    git -C "$DF_PROJECT_ROOT" worktree list --porcelain \
        | grep "^worktree ${_DF_WORKTREE_BASE}" \
        | sed 's/^worktree //'
}

# Clean up all dark-factory worktrees (for cleanup/reset)
# Usage: worktree_cleanup_all
worktree_cleanup_all() {
    local wt
    while IFS= read -r wt; do
        [[ -n "$wt" ]] && git -C "$DF_PROJECT_ROOT" worktree remove "$wt" --force 2>/dev/null
    done < <(worktree_list)
    # Prune stale worktree references
    git -C "$DF_PROJECT_ROOT" worktree prune
    # Clean up stale dark-factory/ branches (merged only, -d not -D)
    git -C "$DF_PROJECT_ROOT" branch --list 'dark-factory/*' | while IFS= read -r branch; do
        branch="${branch## }"  # trim leading whitespace
        git -C "$DF_PROJECT_ROOT" branch -d "$branch" 2>/dev/null || true
    done
}
```

### Modifications to `scripts/lib/git-ops.sh` (~40 lines changed)

Add worktree-aware variants:

```bash
# New function: commit within a worktree (no branch switching)
# Usage: gitops_commit_in_worktree <worktree_path> <spec> <run_id>
gitops_commit_in_worktree() {
    local wt_path="$1" spec="$2" run_id="$3"
    # Same staging logic as gitops_commit_changes but operates within $wt_path
    # Uses: git -C "$wt_path" add / git -C "$wt_path" commit
    # No checkout needed — worktree already on the right branch
}

# New function: push a worktree's branch
# Usage: gitops_push_worktree <worktree_path>
gitops_push_worktree() {
    local wt_path="$1"
    local branch
    branch=$(git -C "$wt_path" rev-parse --abbrev-ref HEAD)
    git -C "$wt_path" push --set-upstream "${GIT_REMOTE:-origin}" "$branch"
}

# New function: create PR from worktree branch
# Usage: gitops_create_pr_from_worktree <worktree_path> <spec> <base_branch>
gitops_create_pr_from_worktree() {
    # Reuses gitops_create_pr logic but resolves branch from worktree HEAD
    # PR body same structured format with QG + reviewer + metadata
}
```

### Modifications to `scripts/lib/docker.sh` (~25 lines changed)

**IMPORTANT:** The actual code uses `${PWD}` (not `$DF_PROJECT_ROOT`) in three places:
1. Line 322: `_DOCKER_ARGS+=(-v "${PWD}:/workspace:${project_mode}")` — project root mount
2. Line 511: `mkdir -p "${PWD}/${override}"` — RW override directory creation
3. Line 512: `_DOCKER_ARGS+=(-v "${PWD}/${override}:/workspace/${override}:rw")` — RW override mount

**Solution:** Introduce a `_DF_DOCKER_PROJECT_DIR` variable at the top of `_build_docker_base_args`, defaulting to `$PWD`. When `--worktree-path` is set, `dark-factory.sh` will `cd` into the worktree before sourcing libraries, making `$PWD` point to the worktree. This is the simplest approach because all path derivation in `dark-factory.sh` already assumes `$PWD` is the project root.

```bash
# In dark-factory.sh, when --worktree-path is set:
#   cd "$WORKTREE_PATH"
#   DF_PROJECT_ROOT="$WORKTREE_PATH"
# This makes $PWD = worktree, so all 3 docker.sh references resolve correctly.

# Additionally, add a safety variable to docker.sh for explicitness:
# At top of _build_docker_base_args():
local docker_project="${_DF_DOCKER_PROJECT_DIR:-$PWD}"
# Then replace all 3 ${PWD} references with ${docker_project}
```

This approach means `dark-factory.sh` with `--worktree-path` does an early `cd` into the worktree, and all downstream code (including docker.sh, quality-gate.sh, path derivation) works without further changes. The `_DF_DOCKER_PROJECT_DIR` override exists as a safety valve for cases where `cd` is not desirable.

### Modifications to `scripts/dark-factory.sh` (~30 lines changed)

Add `--worktree-path` CLI flag:

```bash
# New flag: --worktree-path /path/to/worktree
# When set (early, before library sourcing):
#   1. Validate the worktree path exists and is a git worktree
#   2. cd "$WORKTREE_PATH"  — makes $PWD = worktree for all downstream code
#   3. DF_PROJECT_ROOT="$WORKTREE_PATH"
#   4. _DF_IS_WORKTREE=true  — flag for conditional logic
#
# Conditional behavior when _DF_IS_WORKTREE=true:
#   - Skip gitops_create_branch (worktree already has its branch)
#   - Post-SHIP: use gitops_commit_in_worktree / gitops_push_worktree
#   - Write exit code to worktree dir for coordinator reaping
#   - Original project root stored in _DF_MAIN_PROJECT_ROOT for registry access
```

The key insight: by `cd`-ing into the worktree early, all downstream code works unchanged — `$PWD` references in `docker.sh`, path derivation for `SPEC_FILE`/`SCENARIOS_DIR`/etc., quality gate execution, and scenario running all resolve correctly against the worktree filesystem.

### `.gitignore` Addition

```
.dark-factory/worktrees/
```

---

## Sub-Phase 4.2: Plan Executor & Dependency Graph

### Problem

No mechanism exists to execute multiple specs as a coordinated unit with dependency ordering.

### Solution

New script `scripts/dark-factory-plan.sh` (~350 lines) that reads a plan, builds a dependency graph, and dispatches specs via parallel `dark-factory.sh` child processes.

### Plan File Format

Plans live in `.dark-factory/plans/`. Each plan file lists specs with their dependencies:

```markdown
# Plan: my-project-v1

## dark-factory-metadata
```yaml
plan-id: my-project-v1
created: 2026-03-04
max-concurrent: 3
```

## Specs

| Spec | Depends On | Priority |
|------|-----------|----------|
| auth-service | — | 1 |
| user-model | auth-service | 2 |
| payment-api | auth-service | 2 |
| checkout-flow | user-model, payment-api | 3 |
| notification-service | — | 1 |
| email-templates | notification-service | 2 |
```

The plan executor also reads `depends-on` from individual spec metadata blocks (already supported in `check_dependencies()`). The plan table provides an overview; spec metadata is the source of truth.

### New File: `scripts/dark-factory-plan.sh` (~350 lines)

```bash
#!/usr/bin/env bash
# Plan executor: parallel multi-spec pipeline with dependency ordering
set -euo pipefail

# Source libraries
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/common.sh"
source "${SCRIPT_DIR}/lib/state.sh"
source "${SCRIPT_DIR}/lib/worktree.sh"
source "${SCRIPT_DIR}/lib/notify.sh"

# ── CLI ──────────────────────────────────────────────────────────
# Usage: dark-factory-plan.sh <plan-name> [--max-concurrent N] [--dry-run] [--resume]

# ── Plan Parsing ─────────────────────────────────────────────────
# parse_plan(plan_file) → populates:
#   PLAN_SPECS[]       — ordered list of spec names
#   PLAN_DEPS[spec]    — space-delimited dependency list per spec
#   PLAN_PRIORITY[spec] — numeric priority (lower = earlier)
parse_plan() {
    local plan_file="$1"
    # Parse the markdown table in the plan file
    # Also read depends-on from each spec's metadata block as source of truth
    # Validate: all spec names pass validate_spec_name() (alphanumeric + .-_)
    #   — prevents path traversal and shell metacharacter injection
    # Validate: all referenced deps have corresponding spec files
    # Validate: no circular dependencies (DFS cycle detection)
    for spec in "${PLAN_SPECS[@]}"; do
        validate_spec_name "$spec" || {
            echo "ERROR: Invalid spec name in plan: '${spec}'" >&2
            return 1
        }
    done
}

# ── Dependency Graph ─────────────────────────────────────────────
# topological_sort() → DISPATCH_ORDER[] (specs in valid execution order)
# Uses Kahn's algorithm (iteratively remove nodes with zero in-degree)
topological_sort() {
    local -A in_degree
    local -a queue result

    # Initialize in-degrees
    for spec in "${PLAN_SPECS[@]}"; do
        in_degree[$spec]=0
    done
    for spec in "${PLAN_SPECS[@]}"; do
        for dep in ${PLAN_DEPS[$spec]:-}; do
            in_degree[$spec]=$(( ${in_degree[$spec]} + 1 ))
        done
    done

    # Seed queue with zero in-degree specs
    for spec in "${PLAN_SPECS[@]}"; do
        [[ ${in_degree[$spec]} -eq 0 ]] && queue+=("$spec")
    done

    # Process queue
    while [[ ${#queue[@]} -gt 0 ]]; do
        local current="${queue[0]}"
        queue=("${queue[@]:1}")
        result+=("$current")

        # Reduce in-degree for dependents
        for spec in "${PLAN_SPECS[@]}"; do
            for dep in ${PLAN_DEPS[$spec]:-}; do
                if [[ "$dep" == "$current" ]]; then
                    in_degree[$spec]=$(( ${in_degree[$spec]} - 1 ))
                    [[ ${in_degree[$spec]} -eq 0 ]] && queue+=("$spec")
                fi
            done
        done
    done

    # Cycle detection
    if [[ ${#result[@]} -ne ${#PLAN_SPECS[@]} ]]; then
        echo "ERROR: Circular dependency detected in plan" >&2
        return 1
    fi

    DISPATCH_ORDER=("${result[@]}")
}

# ── Dispatch Engine ──────────────────────────────────────────────
# Main loop: dispatch specs as their dependencies are satisfied
dispatch_plan() {
    local max_concurrent="${MAX_CONCURRENT_SPECS:-3}"
    local -A spec_status     # pending | running | shipped | failed | escalated
    local -A spec_pid        # PID of child dark-factory.sh process
    local -A spec_worktree   # worktree path per spec
    local -A spec_run_id     # run ID per spec
    local active_count=0

    # Initialize all as pending
    for spec in "${PLAN_SPECS[@]}"; do
        spec_status[$spec]="pending"
    done

    # Generate a plan-level RUN_ID (8 bytes = 16 hex chars for collision safety)
    local plan_run_id
    plan_run_id=$(od -An -tx1 -N8 /dev/urandom | tr -d ' \n')

    while true; do
        # Check for completed children via exit-code files (avoids bash wait/kill race)
        for spec in "${PLAN_SPECS[@]}"; do
            if [[ "${spec_status[$spec]}" == "running" ]]; then
                local exit_file="${spec_worktree[$spec]}/exit_code"
                if [[ -f "$exit_file" ]]; then
                    # Child wrote its exit code — reap the process
                    local exit_code
                    exit_code=$(cat "$exit_file")
                    wait "${spec_pid[$spec]}" 2>/dev/null || true  # clean up zombie
                    active_count=$((active_count - 1))

                    if [[ "$exit_code" -eq 0 ]]; then
                        spec_status[$spec]="shipped"
                        # Register shipped scenarios (use main project root for registry)
                        registry_register "${spec}" "${spec_worktree[$spec]}"
                        echo "✓ ${spec} — SHIPPED"
                    else
                        spec_status[$spec]="failed"
                        echo "✗ ${spec} — FAILED (exit ${exit_code})"
                    fi
                fi
            fi
        done

        # Dispatch ready specs (dependencies satisfied, under concurrency cap)
        for spec in "${DISPATCH_ORDER[@]}"; do
            [[ "${spec_status[$spec]}" != "pending" ]] && continue
            [[ $active_count -ge $max_concurrent ]] && break

            # Check for failed dependencies first (unrecoverable — skip dep check)
            local deps_blocked=false
            for dep in ${PLAN_DEPS[$spec]:-}; do
                if [[ "${spec_status[$dep]}" == "failed" || "${spec_status[$dep]}" == "escalated" ]]; then
                    spec_status[$spec]="failed"
                    echo "✗ ${spec} — BLOCKED (dependency ${dep} failed)"
                    deps_blocked=true
                    break
                fi
            done
            [[ "$deps_blocked" == "true" ]] && continue

            # Check all dependencies satisfied
            local deps_met=true
            for dep in ${PLAN_DEPS[$spec]:-}; do
                if [[ "${spec_status[$dep]}" != "shipped" ]]; then
                    deps_met=false
                    break
                fi
            done
            [[ "$deps_met" != "true" ]] && continue

            # Dispatch this spec
            local run_id
            run_id="${plan_run_id}-$(od -An -tx1 -N4 /dev/urandom | tr -d ' \n')"
            spec_run_id[$spec]="$run_id"

            local wt_path
            wt_path=$(worktree_create "$spec" "$run_id")
            spec_worktree[$spec]="$wt_path"

            # Launch dark-factory.sh in background, targeting the worktree
            # Child writes exit code to exit_code file for reliable reaping
            (
                "${SCRIPT_DIR}/dark-factory.sh" "$spec" \
                    --worktree-path "$wt_path" \
                    --ci --auto-pr \
                    >>"${DF_PROJECT_ROOT}/.dark-factory/audit/${spec}-plan-${plan_run_id}.log" 2>&1
                echo $? > "${wt_path}/exit_code"
            ) &

            spec_pid[$spec]=$!
            spec_status[$spec]="running"
            active_count=$((active_count + 1))
            echo "▸ ${spec} — dispatched (worktree: ${wt_path})"
        done

        # Check termination condition
        local all_done=true
        for spec in "${PLAN_SPECS[@]}"; do
            if [[ "${spec_status[$spec]}" == "pending" || "${spec_status[$spec]}" == "running" ]]; then
                all_done=false
                break
            fi
        done

        [[ "$all_done" == "true" ]] && break

        # Brief sleep to avoid busy-wait (100ms)
        sleep 0.1
    done

    # ── Summary Report ───────────────────────────────────────────
    echo ""
    echo "═══════════════════════════════════════"
    echo "  Plan Execution Summary"
    echo "═══════════════════════════════════════"
    local shipped=0 failed=0
    for spec in "${PLAN_SPECS[@]}"; do
        case "${spec_status[$spec]}" in
            shipped)  echo "  ✓ ${spec}"; shipped=$((shipped + 1)) ;;
            failed)   echo "  ✗ ${spec}"; failed=$((failed + 1)) ;;
            escalated) echo "  ⚠ ${spec} (escalated)"; failed=$((failed + 1)) ;;
            *)        echo "  ? ${spec} (${spec_status[$spec]})" ;;
        esac
    done
    echo "═══════════════════════════════════════"
    echo "  ${shipped} shipped, ${failed} failed"
    echo "═══════════════════════════════════════"

    # Cleanup worktrees for shipped specs (keep failed for debugging)
    for spec in "${PLAN_SPECS[@]}"; do
        if [[ "${spec_status[$spec]}" == "shipped" ]]; then
            worktree_remove "$spec" "${spec_run_id[$spec]}"
        fi
    done

    [[ $failed -gt 0 ]] && return 1
    return 0
}

# ── Main ─────────────────────────────────────────────────────────
main() {
    parse_plan "$PLAN_FILE"
    topological_sort
    # Optional: conflict_detection (Sub-Phase 4.4)
    dispatch_plan
}
```

### Signal Handling

The plan executor must handle interrupts gracefully. **Critical: associative arrays must be declared at script scope** (not inside `dispatch_plan`) so the signal handler can access them safely under `set -u`.

```bash
# Declared at SCRIPT SCOPE (before dispatch_plan, after parse_plan):
declare -A spec_status=()
declare -A spec_pid=()
declare -A spec_worktree=()
declare -A spec_run_id=()

# Register trap after arrays exist but before dispatch starts:
trap 'plan_cleanup' EXIT INT TERM

plan_cleanup() {
    echo "Shutting down plan execution..."
    # Guard: if PLAN_SPECS is not yet populated, nothing to clean up
    [[ ${#PLAN_SPECS[@]} -eq 0 ]] && return 0

    # Send SIGTERM to all running child processes
    for spec in "${PLAN_SPECS[@]}"; do
        if [[ "${spec_status[$spec]:-}" == "running" ]]; then
            kill "${spec_pid[$spec]}" 2>/dev/null || true
        fi
    done
    # Wait for children to exit (they have their own cleanup traps)
    wait 2>/dev/null || true
    # Cleanup worktrees based on WORKTREE_CLEANUP_ON_FAIL config
    for spec in "${PLAN_SPECS[@]}"; do
        if [[ "${spec_status[$spec]:-}" != "shipped" ]] && [[ -n "${spec_run_id[$spec]:-}" ]]; then
            if [[ "${WORKTREE_CLEANUP_ON_FAIL:-false}" == "true" ]]; then
                worktree_remove "$spec" "${spec_run_id[$spec]}"
            else
                echo "  Keeping worktree for failed spec: ${spec} (${spec_worktree[$spec]:-})"
            fi
        fi
    done
}
```

### Dependency Cycle Detection

Implemented in `topological_sort()` — if the result array is shorter than the input, a cycle exists. The executor reports which specs are involved:

```bash
# After topological sort, if cycle detected:
echo "Specs involved in cycle:"
for spec in "${PLAN_SPECS[@]}"; do
    local found=false
    for s in "${result[@]}"; do
        [[ "$s" == "$spec" ]] && found=true
    done
    [[ "$found" == "false" ]] && echo "  - ${spec}"
done
```

---

## Sub-Phase 4.3: Resource Governor

### Problem

Unbounded parallel spec execution would exhaust CPU, memory, and API token budgets. Each spec spawns multiple Docker containers (7 agent roles + proxy + networks).

### Solution

Resource governor integrated into the dispatch engine with three controls:

### 1. Concurrency Cap

Already shown in dispatch engine: `MAX_CONCURRENT_SPECS` (default: 3).

Resource estimation per concurrent spec:
- ~4 GB memory (agent container) × 1 active container = 4 GB per spec
- ~4 CPUs per active container
- ~2 network namespaces + 1 proxy container overhead
- For 3 concurrent: ~12 GB memory, ~12 CPUs peak

### 2. Token Budget

**Prerequisite changes to `metrics.sh`:** The existing `metrics_record_start` and `metrics_record_end` functions must be extended to emit two new fields when running under a plan:
- `plan_run_id` — the plan-level run identifier (passed via environment variable `_DF_PLAN_RUN_ID`)
- `turns_allocated` — the cumulative `_DF_TOTAL_TURNS_ALLOCATED` value at pipeline end (already tracked by `_df_check_turn_budget`)

```bash
# In metrics_record_end(), add to the JSON output:
#   "plan_run_id":"%s","turns_allocated":%s
# Only emit plan_run_id if $_DF_PLAN_RUN_ID is set (backward-compatible)
```

New function in `scripts/lib/metrics.sh` (~40 lines):

```bash
# Track cumulative turn allocation across all specs in a plan
# Usage: governor_check_budget <plan_run_id>
# Returns: 0 if within budget, 1 if exceeded
governor_check_budget() {
    local plan_run_id="$1"
    local max_budget="${PLAN_TOKEN_BUDGET:-0}"  # 0 = unlimited
    [[ "$max_budget" -eq 0 ]] && return 0

    local metrics_file="${DF_PROJECT_ROOT}/.dark-factory/metrics.jsonl"
    [[ ! -f "$metrics_file" ]] && return 0

    # Use jq to safely filter by plan_run_id and sum turns_allocated
    # (avoids grep injection and handles JSON properly)
    local total
    total=$(jq -r "select(.plan_run_id == \"${plan_run_id}\") | .turns_allocated // 0" \
        "$metrics_file" 2>/dev/null \
        | paste -sd+ - | bc 2>/dev/null || echo 0)

    if [[ "$total" -gt "$max_budget" ]]; then
        echo "WARNING: Plan turn budget exceeded (${total}/${max_budget})" >&2
        return 1
    fi
    return 0
}
```

**Note:** `jq` is required as a new dependency for plan execution. It is pre-installed in CI environments and Docker images. For local use, the plan executor validates `jq` availability in `governor_preflight()`.

### 3. System Resource Check

Pre-dispatch validation in the plan executor:

```bash
# Validate system can support requested concurrency
governor_preflight() {
    local max_concurrent="${MAX_CONCURRENT_SPECS:-3}"

    # Check memory (warn if < 4GB per concurrent slot)
    local mem_gb mem_label
    if [[ "$(uname)" == "Darwin" ]]; then
        # macOS: hw.memsize = total physical memory (no reliable "available" API)
        mem_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
        mem_label="total"
    else
        # Linux: MemAvailable = usable memory estimate
        mem_gb=$(( $(grep MemAvailable /proc/meminfo | awk '{print $2}') / 1048576 ))
        mem_label="available"
    fi
    local required_mem=$(( max_concurrent * 4 ))
    if [[ $mem_gb -lt $required_mem ]]; then
        echo "WARNING: ${mem_gb}GB ${mem_label} memory, ${required_mem}GB recommended for ${max_concurrent} concurrent specs" >&2
        echo "Consider reducing MAX_CONCURRENT_SPECS" >&2
    fi

    # Validate jq is available (required for token budget tracking)
    if ! command -v jq >/dev/null 2>&1; then
        echo "WARNING: jq not found — token budget tracking disabled" >&2
    fi

    # Check Docker daemon is responsive
    if ! docker info >/dev/null 2>&1; then
        echo "ERROR: Docker daemon not running" >&2
        return 1
    fi
}
```

---

## Sub-Phase 4.4: Cross-Spec Conflict Detection

### Problem

Two specs modifying the same files concurrently will produce merge conflicts when their branches are merged. Early detection saves wasted token spend.

### Solution

New function in `scripts/lib/worktree.sh` (~50 lines):

**Target file declaration:** Specs declare target files in their YAML metadata block (the `dark-factory-metadata` section that already supports `depends-on`):

```yaml
## dark-factory-metadata
```yaml
target-files:
  - src/auth/login.ts
  - src/auth/session.ts
  - src/middleware/auth.ts
depends-on: [user-model]
```
```

This structured approach avoids the brittleness of parsing backtick-quoted content from arbitrary spec prose (which would match CLI flags, function names, error messages, etc.).

```bash
# Detect file overlap between specs at plan validation time
# Usage: conflict_detect <plan_specs...>
# Returns: 0 if no conflicts, 1 if overlaps found (with report)
conflict_detect() {
    local -A file_owners  # file → space-delimited spec list

    for spec in "$@"; do
        local spec_file="${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md"
        [[ ! -f "$spec_file" ]] && continue

        # Extract target-files from structured YAML metadata block
        # Parse between "target-files:" and next non-list-item line
        local in_target_files=false
        local files=""
        while IFS= read -r line; do
            if [[ "$line" =~ ^target-files: ]]; then
                in_target_files=true
                continue
            fi
            if [[ "$in_target_files" == "true" ]]; then
                if [[ "$line" =~ ^[[:space:]]*-[[:space:]]+(.*) ]]; then
                    local file="${BASH_REMATCH[1]}"
                    file="${file## }"  # trim
                    file="${file%% }"
                    files="${files}${file}"$'\n'
                else
                    in_target_files=false
                fi
            fi
        done < "$spec_file"

        while IFS= read -r file; do
            [[ -z "$file" ]] && continue
            # Validate: must look like a file path (contains / or .)
            [[ "$file" != *[/.]* ]] && continue
            if [[ -n "${file_owners[$file]:-}" ]]; then
                file_owners[$file]="${file_owners[$file]} ${spec}"
            else
                file_owners[$file]="$spec"
            fi
        done <<< "$files"
    done

    # Report conflicts
    local has_conflicts=false
    for file in "${!file_owners[@]}"; do
        local owners="${file_owners[$file]}"
        local owner_count
        owner_count=$(echo "$owners" | wc -w)
        if [[ $owner_count -gt 1 ]]; then
            echo "CONFLICT: ${file} targeted by: ${owners}" >&2
            has_conflicts=true
        fi
    done

    [[ "$has_conflicts" == "true" ]] && return 1
    return 0
}
```

**Fallback for specs without `target-files` metadata:** Specs that lack the metadata are excluded from conflict detection (no false positives). The plan executor warns: "Spec X has no target-files metadata — skipping conflict analysis."

### Conflict Resolution Strategies

When conflicts are detected, the plan executor offers three modes (configurable via `CONFLICT_STRATEGY`):

| Strategy | Behavior |
|----------|----------|
| `abort` (default) | Abort plan execution, report conflicts |
| `serialize` | Force conflicting specs into sequential execution (add synthetic dependency edges) |
| `warn` | Log warning, proceed with parallel execution (merge conflicts handled at PR time) |

```bash
# In dispatch_plan(), before main loop:
if ! conflict_detect "${PLAN_SPECS[@]}"; then
    case "${CONFLICT_STRATEGY:-abort}" in
        abort)
            echo "ERROR: File conflicts detected. Resolve by adjusting specs or set CONFLICT_STRATEGY=serialize" >&2
            return 1
            ;;
        serialize)
            echo "WARNING: Serializing conflicting specs" >&2
            serialize_conflicting_specs  # Adds synthetic deps
            topological_sort  # Re-sort with new edges
            ;;
        warn)
            echo "WARNING: Proceeding despite file conflicts" >&2
            ;;
    esac
fi
```

---

## Sub-Phase 4.5: Shipped Scenario Registry

### Problem

The current regression system depends on `.dark-factory/state/*.state` files to identify shipped specs, then looks for scenarios in `.dark-factory/scenarios/{spec}/`. This is fragile:
- State files can be deleted or corrupted
- Scenarios live in the same directory as active spec scenarios
- No distinction between "currently testing" and "shipped and registered" scenarios
- Missing state silently skips regression (fixed in Phase 1 to warn, but still skips)

### Solution

Persistent shipped scenario registry at `.dark-factory/shipped-scenarios/`.

### New File: `scripts/lib/registry.sh` (~100 lines)

```bash
#!/usr/bin/env bash
# Shipped scenario registry — persistent, independent of state files

_DF_REGISTRY_DIR="${DF_PROJECT_ROOT}/.dark-factory/shipped-scenarios"

# Register a spec's scenarios after SHIP
# Usage: registry_register <spec_name> [worktree_path]
# Copies scenarios from the spec's scenario directory to the registry
registry_register() {
    local spec="$1"
    local source_root="${2:-$DF_PROJECT_ROOT}"
    local source_dir="${source_root}/.dark-factory/scenarios/${spec}"
    local target_dir="${_DF_REGISTRY_DIR}/${spec}"

    if [[ ! -d "$source_dir" ]] || [[ -z "$(ls -A "$source_dir" 2>/dev/null)" ]]; then
        echo "WARNING: No scenarios found for ${spec} to register" >&2
        return 1
    fi

    # Create registry entry (overwrite if re-shipped)
    rm -rf "$target_dir"
    mkdir -p "$target_dir"
    cp -r "${source_dir}/"* "$target_dir/"

    # Write metadata
    cat > "${target_dir}/.registry-meta" <<EOF
spec=${spec}
registered=$(date -u +%Y-%m-%dT%H:%M:%SZ)
scenario_count=$(find "$target_dir" -type f -name '*.test.*' -o -name '*.spec.*' -o -name 'test_*' | wc -l | tr -d ' ')
source_hash=$(portable_sha256 "${DF_PROJECT_ROOT}/.dark-factory/specs/${spec}.md" 2>/dev/null || echo "unknown")
EOF

    echo "Registered ${spec} scenarios to shipped registry"
}

# Deregister a spec (e.g., on rollback)
# Usage: registry_deregister <spec_name>
registry_deregister() {
    local spec="$1"
    rm -rf "${_DF_REGISTRY_DIR}/${spec}"
    echo "Deregistered ${spec} from shipped registry"
}

# List all registered specs
# Usage: registry_list
registry_list() {
    local dir
    for dir in "${_DF_REGISTRY_DIR}"/*/; do
        [[ -d "$dir" ]] && basename "$dir"
    done
}

# Get scenario directory for a registered spec
# Usage: registry_scenario_dir <spec_name>
registry_scenario_dir() {
    local dir="${_DF_REGISTRY_DIR}/$1"
    [[ -d "$dir" ]] && echo "$dir" || return 1
}

# Run full regression suite (all registered specs)
# Usage: registry_run_regression [--exclude <spec>] [--timeout <seconds>]
# Returns: 0 if all pass, 1 if any fail
#
# NOTE: run-scenarios.sh takes a spec NAME and resolves the directory as
# .dark-factory/scenarios/{spec}/. For registry regression, we symlink
# the shipped scenarios into the expected location before running.
registry_run_regression() {
    local exclude="" timeout="${REGRESSION_TIMEOUT:-300}"

    while [[ $# -gt 0 ]]; do
        case "$1" in
            --exclude) exclude="$2"; shift 2 ;;
            --timeout) timeout="$2"; shift 2 ;;
            *) shift ;;
        esac
    done

    local specs
    specs=$(registry_list)
    [[ -z "$specs" ]] && { echo "No shipped scenarios in registry"; return 0; }

    local total=0 passed=0 failed=0 failed_specs=""
    local regression_log_dir="${DF_PROJECT_ROOT}/.dark-factory/reports/regression"
    mkdir -p "$regression_log_dir"

    while IFS= read -r spec; do
        [[ "$spec" == "$exclude" ]] && continue
        total=$((total + 1))

        # Symlink registry scenarios into the location run-scenarios.sh expects
        local expected_dir="${DF_PROJECT_ROOT}/.dark-factory/scenarios/${spec}"
        local registry_dir
        registry_dir=$(registry_scenario_dir "$spec") || continue
        local needs_cleanup=false
        if [[ ! -d "$expected_dir" ]]; then
            ln -sf "$registry_dir" "$expected_dir"
            needs_cleanup=true
        fi

        local log_file="${regression_log_dir}/${spec}-regression.log"
        echo "  Running regression: ${spec}..."
        if "${_DF_SCRIPTS_DIR}/run-scenarios.sh" "$spec" \
            --timeout "$timeout" >"$log_file" 2>&1; then
            passed=$((passed + 1))
        else
            failed=$((failed + 1))
            failed_specs="${failed_specs} ${spec}"
            echo "    (details: ${log_file})"
        fi

        # Clean up symlink if we created it
        [[ "$needs_cleanup" == "true" ]] && rm -f "$expected_dir"
    done <<< "$specs"

    echo "Regression: ${passed}/${total} passed"
    [[ $failed -gt 0 ]] && {
        echo "FAILED:${failed_specs}"
        return 1
    }
    return 0
}

# NOTE: Selective regression (only re-running scenarios for specs whose target
# files overlap with changed files) is deferred to Phase 5 (Observability &
# Intelligence). It requires file coverage tracking in scenario metadata, which
# will be built alongside the observability infrastructure. For Phase 4, full
# regression is the only supported mode.
```

### Migration from State-File Regression

The existing regression in `dark-factory.sh` (lines 970-1062) and `run-regression.sh` both scan state files. These will be updated to use the registry:

```bash
# In dark-factory.sh post-SHIP section, replace:
#   for state_file in .dark-factory/state/*.state; do
#       grep -q "status=shipped" "$state_file" || continue
#       ...
# With:
registry_register "$SPEC_NAME" "${_DF_WORKTREE_PATH:-$DF_PROJECT_ROOT}"
if [[ "${REGRESSION_ON_SHIP}" == "true" ]]; then
    registry_run_regression --exclude "$SPEC_NAME"
fi
```

### Backward Compatibility

On first run after upgrade, if the registry is empty but shipped state files exist, auto-populate:

```bash
# registry_migrate_from_state() — one-time migration
registry_migrate_from_state() {
    [[ -d "$_DF_REGISTRY_DIR" ]] && [[ -n "$(ls -A "$_DF_REGISTRY_DIR" 2>/dev/null)" ]] && return 0

    echo "Migrating shipped scenarios to registry..."
    for state_file in "${DF_PROJECT_ROOT}/.dark-factory/state/"*.state; do
        [[ -f "$state_file" ]] || continue
        grep -q "status=shipped" "$state_file" || continue
        local spec
        spec=$(basename "$state_file" .state)
        local scenario_dir="${DF_PROJECT_ROOT}/.dark-factory/scenarios/${spec}"
        [[ -d "$scenario_dir" ]] && registry_register "$spec"
    done
}
```

---

## Sub-Phase 4.6: Full Regression as Merge Gate

### Problem

Per-spec regression after SHIP catches regressions within the spec's own scenarios. But it doesn't catch cases where spec F-4 breaks functionality that was validated by spec F-1's scenarios.

### Solution

After SHIP and after spec scenarios are registered, run the full registry regression suite against the current codebase (in the worktree). Block the PR if regression fails.

### Integration Point: `dark-factory.sh` Post-SHIP Flow

```bash
# After Oracle SHIP + registry_register:

# 1. Run full regression (all shipped scenarios) in the worktree
echo "Running full regression suite..."
if ! registry_run_regression --exclude "$SPEC_NAME" --timeout "$REGRESSION_TIMEOUT"; then
    case "${REGRESSION_POLICY:-block}" in
        block)
            write_state "status" "regression-failed"
            notify_regression_blocked "$SPEC_NAME"
            echo "REGRESSION FAILED — blocking ship"
            exit 1
            ;;
        warn)
            echo "WARNING: Regression failures detected (policy=warn, continuing)"
            ;;
    esac
fi

# 2. Create PR (only if regression passed or policy=warn)
gitops_commit_in_worktree "$_DF_WORKTREE_PATH" "$SPEC_NAME" "$RUN_ID"
gitops_push_worktree "$_DF_WORKTREE_PATH"
gitops_create_pr_from_worktree "$_DF_WORKTREE_PATH" "$SPEC_NAME" "$GIT_BASE_BRANCH"
```

### CI Merge Gate Enhancement

Update `.github/workflows/dark-factory-regression.yml` to use the registry:

```yaml
# Replace: scripts/run-regression.sh
# With:
- name: Run registry regression
  run: |
    source scripts/lib/common.sh
    source scripts/lib/registry.sh
    registry_run_regression --timeout 300
```

### Regression Cost Management

For large projects with many shipped specs, full regression becomes expensive. Phase 4 uses full regression only. Selective regression (only re-running scenarios for specs whose target files overlap with changed files) is deferred to Phase 5 where file coverage tracking will be built alongside the observability infrastructure.

---

## File Change Summary

### New Files

| File | Lines (est.) | Purpose |
|------|-------------|---------|
| `scripts/lib/worktree.sh` | ~120 | Git worktree lifecycle management |
| `scripts/lib/registry.sh` | ~150 | Shipped scenario registry |
| `scripts/dark-factory-plan.sh` | ~350 | Plan executor with parallel dispatch |

### Modified Files

| File | Changes (est.) | What Changes |
|------|---------------|--------------|
| `scripts/dark-factory.sh` | ~50 lines | `--worktree-path` flag, early `cd`, registry integration, exit-code file, post-SHIP flow |
| `scripts/lib/git-ops.sh` | ~70 lines | Worktree-aware commit/push/PR functions |
| `scripts/lib/docker.sh` | ~15 lines | `_DF_DOCKER_PROJECT_DIR` variable, replace 3x `$PWD`, add `_DOCKER_ARGS` concurrency comment |
| `scripts/lib/common.sh` | ~4 lines | Source `worktree.sh` and `registry.sh` |
| `scripts/lib/metrics.sh` | ~60 lines | `plan_run_id` + `turns_allocated` fields, governor functions, per-spec metrics files |
| `scripts/run-regression.sh` | ~20 lines | Use registry instead of state-file scan |
| `.github/workflows/dark-factory.yml` | ~30 lines | Plan-mode trigger, dependency-aware matrix |
| `.github/workflows/dark-factory-regression.yml` | ~10 lines | Use registry |
| `.gitignore` | ~2 lines | Add `.dark-factory/worktrees/`, `.dark-factory/shipped-scenarios/` |

### Total: ~670 new lines, ~260 modified lines ≈ ~930 lines of code changes
### Plus: ~200–300 lines of test infrastructure, CI updates, and documentation
### Realistic total: ~1,200–1,500 lines

---

## Config Additions

New config variables for `.dark-factory/config.sh`:

```bash
# ── Phase 4: Concurrent Execution ────────────────────────────────

# Maximum specs executing concurrently (default: 3)
MAX_CONCURRENT_SPECS=3

# Token budget for entire plan execution (0 = unlimited)
# Expressed in total agent turns across all specs
PLAN_TOKEN_BUDGET=0

# Conflict detection strategy: abort | serialize | warn
CONFLICT_STRATEGY=abort

# Whether to clean up worktrees for shipped specs (true) or keep them (false)
WORKTREE_CLEANUP_ON_SHIP=true

# Whether to clean up worktrees for failed specs (default: false for debugging)
WORKTREE_CLEANUP_ON_FAIL=false
```

**Note:** `REGRESSION_MODE=selective` is deferred to Phase 5 (requires file coverage tracking). Phase 4 uses full regression only.

---

## Testing Strategy

### Unit Tests (new spec for Dark Factory self-testing)

1. **Worktree lifecycle:**
   - `worktree_create` produces a valid worktree with correct branch
   - `worktree_remove` cleans up worktree and branch
   - `worktree_cleanup_all` handles no-worktrees gracefully
   - Resume: `worktree_create` with existing branch reuses it

2. **Dependency graph:**
   - Linear chain: A→B→C dispatches in order
   - Diamond: A→{B,C}→D dispatches B,C in parallel, then D
   - Cycle detection: A→B→A fails with error
   - Missing dependency: A depends on nonexistent spec → fails
   - Single spec with no deps: dispatches immediately

3. **Resource governor:**
   - `MAX_CONCURRENT_SPECS=1` enforces serial execution
   - Token budget exceeded → stops dispatching new specs
   - Preflight detects insufficient memory

4. **Conflict detection:**
   - Two specs targeting same file → detected
   - No overlap → clean pass
   - `serialize` strategy adds synthetic dependency edge

5. **Shipped scenario registry:**
   - `registry_register` copies scenarios and writes metadata
   - `registry_deregister` removes the entry
   - `registry_list` returns all registered specs
   - `registry_run_regression` runs all and reports results
   - `registry_migrate_from_state` populates from existing state files
   - Idempotent: re-register overwrites previous entry

6. **Integration test:**
   - Create 3 specs (A, B depends on A, C independent)
   - Run `dark-factory-plan.sh` in dry-run mode
   - Verify dispatch order: A and C start together, B starts after A ships

### Manual Validation

- Run plan with 2 real specs on a test repo to verify end-to-end worktree isolation
- Verify Docker containers bind-mount worktree paths correctly
- Verify PRs are created from correct branches
- Verify regression runs against merged code in worktree

---

## Migration & Backward Compatibility

### Fully Backward Compatible

1. **`dark-factory.sh` without `--worktree-path`** behaves exactly as before. All changes are additive — the worktree code path is only activated when `--worktree-path` is explicitly passed.

2. **Existing single-spec CI workflow** continues to work. The plan executor is a new, separate workflow.

3. **Existing regression** still works. The registry is additive — when empty, the registry migration function populates from state files. The old state-file-based regression code is replaced but produces identical behavior.

4. **Existing config** needs no changes. All new config variables have defaults.

### Migration Steps

1. Deploy Phase 4 code
2. On first run, `registry_migrate_from_state()` auto-populates registry from existing shipped state files
3. Begin using `dark-factory-plan.sh` for multi-spec projects
4. Single-spec usage continues with `dark-factory.sh` as before

---

## Risk Analysis

### High Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| Git worktree corruption on crash | Orphaned worktrees consume disk, stale branches | Cleanup trap in plan executor; `worktree_cleanup_all` utility; `.dark-factory/worktrees/` in `.gitignore` |
| Concurrent Docker resource exhaustion | OOM kills, container failures | Resource governor preflight check; configurable `MAX_CONCURRENT_SPECS`; per-container memory limits already enforced |
| Race condition on shared `metrics.jsonl` | Interleaved writes, corrupt JSONL | Use per-spec metrics files (`.dark-factory/metrics-{spec}.jsonl`) during plan execution; merge into `metrics.jsonl` after plan completes. POSIX `O_APPEND` atomicity guarantees apply only to individual `write()` calls, and bash `printf` may issue multiple writes. Per-spec files eliminate the race entirely. |
| `_DOCKER_ARGS` global array mutation | Unsafe if refactored to in-process parallelism | Safe for current fork-based design (each child process gets its own copy). Add code comment documenting this as an architectural constraint: "process-local, must not be shared across concurrent in-process invocations." |

### Medium Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| Token budget explosion with parallel specs | Cost overrun | `PLAN_TOKEN_BUDGET` cap; per-spec `MAX_TOTAL_AGENT_TURNS` still enforced |
| Merge conflicts between parallel spec branches | PR merge fails | Conflict detection at plan time; `serialize` strategy; merge queue handles at merge time |
| Stale worktree after failed cleanup | Disk usage, confusing state | `worktree_cleanup_all` command; periodic cleanup in CI |

### Low Risk

| Risk | Impact | Mitigation |
|------|--------|------------|
| Plan file parsing brittleness | Plan rejected | Spec metadata `depends-on` is source of truth; plan table is convenience |
| `set -e` interaction with child `wait` | Unexpected script exit | Dispatch loop uses exit-code files instead of `wait` return codes; `wait` call uses `|| true` to prevent `set -e` triggering on failed children. The dispatch loop itself runs with explicit exit-code checks, not `set -e` propagation. |

### Bash Version Compatibility

The plan executor uses **exit-code file signaling** instead of `wait -n` (bash 4.3+) or `wait -p` (bash 5.1+). Each child writes `$? > exit_code`; the coordinator polls with `kill -0` and reads the file. This is fully compatible with macOS's bash 3.2.

Other portability choices:
- `od -An -tx1 -N8 /dev/urandom | tr -d ' \n'` instead of `xxd -p` (xxd requires vim)
- `kill -0` + `sleep 0.1` polling instead of `wait -n`
- `declare -A` (associative arrays) requires bash 4.0+ — this is available on macOS via Homebrew (`brew install bash`) and is standard in CI environments. The plan executor should include a version check: `[[ ${BASH_VERSINFO[0]} -ge 4 ]] || { echo "ERROR: bash 4+ required"; exit 1; }`

---

## Implementation Order

Execute sub-phases in this order (some can overlap):

```
4.1 Worktree Isolation ──────────────────┐
4.5 Shipped Scenario Registry ───────────┤
                                         ├── 4.2 Plan Executor (depends on 4.1 + 4.5)
4.3 Resource Governor ───────────────────┤
4.4 Conflict Detection ─────────────────┘
                                         │
                                         └── 4.6 Regression Merge Gate (depends on 4.2 + 4.5)
```

**4.1 and 4.5 can be built and tested independently in parallel.**
**4.3 and 4.4 are small and can be built alongside 4.2.**
**4.6 requires 4.2 and 4.5 to be complete.**

---

## Review Changelog

Revisions based on Neo semantic code review (2026-03-04):

### Critical Fixes
| ID | Issue | Resolution |
|----|-------|------------|
| C1 | `docker.sh` uses `$PWD` not `$DF_PROJECT_ROOT` — plan's mount assumption was wrong | Redesigned: `dark-factory.sh` does early `cd` into worktree, making `$PWD` correct for all 3 docker.sh references. Added `_DF_DOCKER_PROJECT_DIR` safety variable. |
| C3 | `wait` after `kill -0` loses exit codes (returns 127 for reaped PIDs) | Redesigned dispatch loop: children write exit code to file (`${wt_path}/exit_code`); coordinator reads file instead of relying on `wait` return code. `wait` call uses `|| true` for cleanup only. |

### High Fixes
| ID | Issue | Resolution |
|----|-------|------------|
| H1 | Signal handler accesses uninitialized associative arrays under `set -u` | Moved `spec_status`, `spec_pid`, `spec_worktree`, `spec_run_id` to script scope. Added `${var:-}` guards in cleanup handler. Trap registered after array initialization. |
| H2 | Token budget governor references non-existent `total_turns` and `plan_run_id` fields in `metrics.jsonl` | Added prerequisite changes to `metrics.sh` to emit `plan_run_id` and `turns_allocated`. Switched from `grep` to `jq` for safe JSON querying. Added `jq` dependency validation. |
| H3 | Config symlink from worktree into main project crosses isolation boundary | Changed from `ln -sf` to `cp` — config is now snapshot-copied, preventing mid-execution drift and symlink safety concerns. |
| H4 | Conflict detection parsed all backtick content (CLI flags, function names, etc.) | Redesigned: specs declare `target-files:` in structured YAML metadata block. Parser only reads that section. Specs without metadata are skipped with warning. |
| H5 | `set -e` interaction with `wait` for failed children is bash-version-dependent | Eliminated by using exit-code file approach. `wait` is called with `|| true` for zombie cleanup only. |

### Medium Fixes
| ID | Issue | Resolution |
|----|-------|------------|
| M1 | Branch name collision risk with 32-bit entropy (4 bytes) | Increased to 64-bit: plan_run_id uses 8 bytes (`od -N8`), branch names use 16 hex chars. |
| M2 | `worktree_remove --force` can lose uncommitted work | Added pre-removal safety checks: verify branch is pushed and working tree is clean. Skip removal with warning if checks fail. |
| M3 | `registry_run_regression` swallows all output | Redirect per-spec output to log files under `.dark-factory/reports/regression/`. Print log path on failure. |
| M4 | `registry_run_regression` passes directory path but `run-scenarios.sh` expects spec name | Symlink registry scenarios into expected `.dark-factory/scenarios/{spec}/` location before running; clean up symlink after. |
| M5 | macOS memory check uses total memory, not available | Updated label to "total" on macOS (no reliable "available" API). Linux still uses `MemAvailable`. |
| M6 | `metrics.jsonl` write atomicity claim incorrect for regular files | Switched to per-spec metrics files during plan execution; merge after plan completes. |
| M7 | Selective regression is a TODO, not implemented | Removed `REGRESSION_MODE=selective` config option. Deferred to Phase 5 (requires file coverage tracking). |

### Low Fixes
| ID | Issue | Resolution |
|----|-------|------------|
| L1 | Plan file format mixes markdown table with YAML metadata | Clarified: spec metadata `depends-on` is source of truth. Plan table is display convenience. |
| L2 | `worktree_cleanup_all` does not clean up stale branches | Added branch cleanup loop: `git branch --list 'dark-factory/*'` with `-d` (safe delete). |
| L3 | `xxd -p` may not be available in minimal environments | Replaced all `xxd` usage with `od -An -tx1 -N8 /dev/urandom \| tr -d ' \n'`. |
| L4 | No spec name validation in plan parsing | Added `validate_spec_name()` call for every spec during `parse_plan()`. |

### Info Adjustments
| ID | Issue | Resolution |
|----|-------|------------|
| I1 | Line estimate ~835 was optimistic | Revised to ~1,200–1,500 lines including test infrastructure, CI updates, and documentation. |
