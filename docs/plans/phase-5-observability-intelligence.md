# Phase 5: Observability & Intelligence — Implementation Plan

**Date:** 2026-03-05
**Scope:** Plan execution durability, telemetry & cost tracking, anomaly detection & intelligence
**Depends on:** Phase 4 (Concurrent Execution) — completed 2026-03-05
**Estimated Duration:** Weeks 13–18

---

## Table of Contents

1. [Design Principles](#1-design-principles)
2. [Workstream A: Plan Execution Durability](#2-workstream-a-plan-execution-durability)
3. [Workstream B: Structured Event System](#3-workstream-b-structured-event-system)
4. [Workstream C: Cost Attribution & Budget Enforcement](#4-workstream-c-cost-attribution--budget-enforcement)
5. [Workstream D: Anomaly Detection](#5-workstream-d-anomaly-detection)
6. [Workstream E: Trust Scoring & Dynamic Autonomy](#6-workstream-e-trust-scoring--dynamic-autonomy)
7. [Workstream F: Dashboards & Alerting](#7-workstream-f-dashboards--alerting)
8. [File Inventory](#8-file-inventory)
9. [Configuration Variables](#9-configuration-variables)
10. [Test Plan](#10-test-plan)
11. [Implementation Order & Dependencies](#11-implementation-order--dependencies)
12. [Risk Assessment](#12-risk-assessment)

---

## 1. Design Principles

1. **Bash-native first.** The pipeline is bash. All Phase 5 components are bash functions that emit structured JSONL. No external runtime dependencies (no Temporal, no Redis, no Prometheus daemon) are introduced in this phase. External integrations (Grafana, OTEL Collector) are optional consumers that read the structured output.

2. **Append-only event log as single source of truth.** Replace the current dual-format output (text audit log + summary metrics.jsonl) with a unified structured event stream (`events.jsonl`). The audit log remains for human readability; `events.jsonl` is the machine-readable source.

3. **Canonical events file path.** All processes — the plan executor parent, worktree subprocesses, and quality gate containers — write to a single `events.jsonl` at the **original project root** (not the worktree root). The path is set once by the plan executor (or `dark-factory.sh` in single-spec mode) and exported as `_DF_EVENTS_FILE` to all children. See [B6. Worktree Event Path Resolution](#b6-worktree-event-path-resolution).

4. **Backward-compatible.** Existing `metrics.jsonl` continues to be written (deprecated but not removed). New systems read `events.jsonl`. Migration path: consumers can read both during transition.

5. **Cost of new code < value of new signal.** Each workstream must justify its line count. Prefer 50 lines of bash that emit the right event over 500 lines of infrastructure.

6. **Testable in isolation.** Every new function is unit-testable with the existing `test-helpers.sh` framework. No Docker required for unit tests.

7. **`jq` is a soft dependency.** Features that require `jq` (trust scoring, baseline anomaly detection, CLI dashboard, cost summary) degrade gracefully when `jq` is absent. The core event emitter and threshold-based anomaly detection work without `jq`. See [jq Degradation Matrix](#jq-degradation-matrix) in the risk assessment.

---

## 2. Workstream A: Plan Execution Durability

### Problem

`dispatch_plan` tracks spec status in bash associative arrays (`spec_status[]`). If the plan executor process dies (OOM, CI timeout, `kill -9`), all progress is lost. Re-running the plan re-executes every spec, including those that already shipped.

### Design

#### A1. Plan Checkpoint File

**File:** `.dark-factory/state/plan-{name}-{run_id}.jsonl`

Each line is a JSON event recording one spec's completion:

```json
{"spec":"auth-v2","status":"shipped","exit_code":0,"ts":"2026-03-05T14:22:01Z","wall_s":342,"pr_url":"https://...","run_id":"a1b2c3d4","spec_hash":"abcd1234"}
{"spec":"search-v3","status":"failed","exit_code":1,"ts":"2026-03-05T14:25:33Z","wall_s":198,"run_id":"e5f6g7h8","spec_hash":"efgh5678","failure_reason":"regression_blocked"}
```

**Implementation:**

- New function `plan_checkpoint_write(plan_name, plan_run_id, spec, status, exit_code, extras...)` in `scripts/lib/plan-state.sh`
- Called in `dispatch_plan` immediately after each spec's exit code is collected (line ~465 in current `dark-factory-plan.sh`)
- **Append-only:** Each call appends one JSON line via `printf '%s\n' "$json" >> "$checkpoint_file"`. No full-file rewrite. For crash safety, call `sync` after the append (ensures the line is flushed to disk before the next spec starts).
- `extras` is a variadic list of `key=value` pairs added to the JSON object
- Includes `spec_hash` (SHA-256 of the spec file) in each checkpoint line for resume validation

#### A2. Plan Resume on Re-run

**Implementation:**

- New function `plan_checkpoint_load(plan_name)` finds the most recent checkpoint file by parsing the `ts` field from the first JSON line in each matching checkpoint file (more reliable than filesystem mtime). Falls back to lexicographic sort of filenames if `jq` is unavailable.
- New function `plan_checkpoint_completed_specs(checkpoint_file)` returns list of specs with `status` in `{shipped, budget_skipped}` — these are the statuses that indicate the spec should not be re-run. Specs with `status` in `{failed, escalated}` are re-run on resume.
- In `dispatch_plan`, after building the dependency graph and before the dispatch loop:
  1. Call `plan_checkpoint_load`
  2. If checkpoint exists and `--resume` flag is set (or `PLAN_AUTO_RESUME=true`):
     - For each completed spec, verify the current spec file hash matches the checkpoint's `spec_hash`
     - If hash matches: mark as `shipped` in `spec_status[]`, skip during dispatch
     - If hash differs: log warning `"PLAN_RESUME: spec {name} changed since checkpoint, will re-execute"` and leave as `pending`
     - Log: `"PLAN_RESUME: Skipping {N} previously-shipped specs: {list}"`
     - Emit `PLAN_RESUME` event
  3. If checkpoint exists but `--fresh` flag is set, ignore checkpoint

#### A3. Plan Wall-Time Timeout

**Implementation:**

- New config: `PLAN_MAX_WALL_TIME_S` (default: 0 = unlimited)
- In `dispatch_plan`, record `plan_start_epoch=$(date +%s)` before the dispatch loop (using epoch seconds, not bash `SECONDS`, to avoid subshell reset issues)
- In the poll loop, check: `if (( PLAN_MAX_WALL_TIME_S > 0 && $(date +%s) - plan_start_epoch > PLAN_MAX_WALL_TIME_S )); then`
  - Set a `_plan_timed_out=true` flag
  - Send `SIGTERM` to all running spec PIDs: `for pid in "${!spec_pid[@]}"; do kill -TERM "$pid" 2>/dev/null; done`
  - Wait up to 10s for graceful shutdown, then `SIGKILL`
  - Write checkpoint for any specs that completed during grace period
  - Emit `PLAN_TIMEOUT` event
  - Return exit code 2 (distinct from 1=spec failures)

#### A4. Registry Regression Locking

**Current state:** `registry_register` and `registry_deregister` use `_df_acquire_lock`. `registry_run_regression` does not.

**Implementation:**

- Add `_df_acquire_lock "$_DF_REGISTRY_DIR" "regression"` at the start of `registry_run_regression`
- Use `trap ... RETURN` pattern (same as register/deregister)
- This prevents two concurrent specs from running regression simultaneously (which would corrupt scenario symlinks)

---

## 3. Workstream B: Structured Event System

### Problem

The current system writes:
- **Audit log** (`.dark-factory/audit/{spec}-{run_id}.log`): Human-readable text, per-spec, no schema
- **Metrics** (`.dark-factory/metrics.jsonl`): Two records per spec run (start + end), summary only

Neither format supports: per-agent timing, per-gate results, per-iteration granularity, plan-level events, or machine consumption without fragile text parsing.

### Design

#### B1. Event Schema

**File:** `.dark-factory/events.jsonl` (gitignored, append-only)

Every event has a common envelope:

```json
{
  "ts": "2026-03-05T14:22:01.123Z",
  "event": "AGENT_COMPLETED",
  "spec": "auth-v2",
  "run_id": "a1b2c3d4",
  "plan_run_id": "e5f6g7h8",
  "iter": 2,
  "data": { ... }
}
```

**Event types (23 total):**

| Event | Emitter | Data Fields |
|-------|---------|-------------|
| `PLAN_START` | plan executor (parent) | `plan_name`, `spec_count`, `max_concurrent`, `token_budget` |
| `PLAN_COMPLETE` | plan executor (parent) | `shipped_count`, `failed_count`, `skipped_count`, `wall_s` |
| `PLAN_TIMEOUT` | plan executor (parent) | `wall_s`, `limit_s`, `running_specs` |
| `PLAN_RESUME` | plan executor (parent) | `checkpoint_file`, `skipped_count`, `remaining_count` |
| `SPEC_START` | dark-factory.sh | `spec_hash`, `autonomy_level`, `max_iters`, `predicted_iters` |
| `SPEC_DISPATCHED` | plan executor (parent) | `worktree_path`, `priority`, `depends_on` |
| `SPEC_SHIPPED` | dark-factory.sh | `iters`, `satisfaction_score`, `pass_rate`, `wall_s` |
| `SPEC_FAILED` | dark-factory.sh | `reason`, `iters`, `last_satisfaction` |
| `SPEC_ESCALATED` | dark-factory.sh | `reason`, `iters`, `trajectory_class` |
| `AGENT_START` | docker.sh (host) | `agent_role`, `max_turns` |
| `AGENT_COMPLETED` | docker.sh (host) | `agent_role`, `exit_code`, `wall_s`, `turns_allocated` |
| `AGENT_FAILED` | docker.sh (host) | `agent_role`, `exit_code`, `wall_s`, `error` |
| `GATE_COMPLETED` | dark-factory.sh (host, post-container) | `build`, `lint`, `typecheck`, `selftest`, `scenarios`, `mutation`, `wall_s` |
| `SATISFACTION_SCORED` | satisfaction.sh | `score`, `pass_rate`, `multi_run_n`, `multi_run_k` |
| `TRAJECTORY_CLASSIFIED` | satisfaction.sh | `classification`, `scores`, `strategy` |
| `REGRESSION_RUN` | registry.sh | `spec_count`, `passed`, `failed`, `wall_s` |
| `BUDGET_EXCEEDED` | cost.sh | `allocated_turns`, `estimated_usd`, `limit_usd`, `spec` |
| `BUDGET_ALERT` | cost.sh | `percent`, `estimated_usd`, `limit_usd` |
| `BARRIER_VIOLATION` | audit.sh | `agent_role`, `violation_type`, `detail` |
| `COST_SUMMARY` | cost.sh | `total_turns`, `total_wall_s`, `estimated_usd`, `by_agent` |
| `ANOMALY_DETECTED` | anomaly.sh | `type`, `severity`, `metric`, `current`, `limit` |
| `TRUST_SCORED` | trust.sh | `score`, `effective_autonomy`, `dimensions` |
| `ITER_COST` | cost.sh | `iter`, `turns`, `wall_s`, `estimated_usd` |

**Note on `SPEC_DISPATCHED`:** This event is emitted by the **parent** plan executor process *before* the `&` background fork, not by the child. The parent knows the worktree path, priority, and dependencies. The child (dark-factory.sh) will emit its own `SPEC_START` once it begins execution.

**Note on `GATE_COMPLETED`:** This event is emitted by `dark-factory.sh` on the host *after* the quality gate container exits, not from inside the container. The host process parses the gate's output files (already written to the bind-mounted `.dark-factory/reports/` directory) and emits the event. This avoids the container needing write access to the events file.

#### B2. Event Emitter Function

**File:** `scripts/lib/events.sh`

The event emitter uses `jq -n` for safe JSON construction (no manual escaping). Falls back to a simpler `printf`-based emitter when `jq` is unavailable.

```bash
# Detect jq availability once at source time
_DF_HAS_JQ=false
command -v jq >/dev/null 2>&1 && _DF_HAS_JQ=true

# Portable high-resolution timestamp
_df_timestamp() {
    # Try GNU date (Linux, Homebrew gdate on macOS)
    if command -v gdate >/dev/null 2>&1; then
        gdate -u +"%Y-%m-%dT%H:%M:%S.%3NZ"
    elif date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" 2>/dev/null | grep -q 'N'; then
        # %N not supported (macOS native date outputs literal 'N')
        date -u +"%Y-%m-%dT%H:%M:%SZ"
    else
        date -u +"%Y-%m-%dT%H:%M:%S.%3NZ"
    fi
}

# Core emitter — all other emit_* functions call this
# Usage: emit_event "EVENT_NAME" "key1=val1" "key2=val2" ...
# Values are always treated as strings in JSON. For complex payloads
# with nested objects or arrays, construct the JSON externally and pass
# the entire data block as a single key=value pair.
emit_event() {
    local event_name="$1"; shift
    local events_file="${_DF_EVENTS_FILE:?_DF_EVENTS_FILE must be set}"

    # Ensure directory exists
    mkdir -p "$(dirname "$events_file")"

    local ts
    ts=$(_df_timestamp)

    if $_DF_HAS_JQ; then
        # Build jq args for safe JSON construction
        local jq_args=("--arg" "ts" "$ts" "--arg" "event" "$event_name")
        local jq_filter='{ts: $ts, event: $event'

        # Add context from globals (if set)
        if [[ -n "${_DF_CURRENT_SPEC:-}" ]]; then
            jq_args+=("--arg" "spec" "$_DF_CURRENT_SPEC")
            jq_filter+=', spec: $spec'
        fi
        if [[ -n "${_DF_RUN_ID:-}" ]]; then
            jq_args+=("--arg" "run_id" "$_DF_RUN_ID")
            jq_filter+=', run_id: $run_id'
        fi
        if [[ -n "${_DF_PLAN_RUN_ID:-}" ]]; then
            jq_args+=("--arg" "plan_run_id" "$_DF_PLAN_RUN_ID")
            jq_filter+=', plan_run_id: $plan_run_id'
        fi
        if [[ -n "${_DF_CURRENT_ITER:-}" ]]; then
            jq_args+=("--argjson" "iter" "$_DF_CURRENT_ITER")
            jq_filter+=', iter: $iter'
        fi

        # Add data fields from args
        jq_filter+=', data: {'
        local first=true
        for kv in "$@"; do
            local key="${kv%%=*}" val="${kv#*=}"
            $first || jq_filter+=','
            jq_args+=("--arg" "data_$key" "$val")
            jq_filter+=" \"$key\": \$data_$key"
            first=false
        done
        jq_filter+='}}'

        jq -n -c "${jq_args[@]}" "$jq_filter" >> "$events_file"
    else
        # Fallback: minimal JSON with basic escaping (no jq)
        local json="{\"ts\":\"$ts\",\"event\":\"$event_name\""
        [[ -n "${_DF_CURRENT_SPEC:-}" ]] && json+=",\"spec\":\"$_DF_CURRENT_SPEC\""
        [[ -n "${_DF_RUN_ID:-}" ]] && json+=",\"run_id\":\"$_DF_RUN_ID\""
        [[ -n "${_DF_PLAN_RUN_ID:-}" ]] && json+=",\"plan_run_id\":\"$_DF_PLAN_RUN_ID\""
        [[ -n "${_DF_CURRENT_ITER:-}" ]] && json+=",\"iter\":$_DF_CURRENT_ITER"
        json+=",\"data\":{"
        local first=true
        for kv in "$@"; do
            local key="${kv%%=*}" val="${kv#*=}"
            $first || json+=","
            # Escape backslashes, double quotes, and replace actual newlines
            val=$(printf '%s' "$val" | awk '{gsub(/\\/, "\\\\"); gsub(/"/, "\\\""); printf "%s", (NR>1 ? "\\n" : "") $0}')
            json+="\"$key\":\"$val\""
            first=false
        done
        json+="}}"
        printf '%s\n' "$json" >> "$events_file"
    fi
}

# Convenience wrappers for all 23 event types
emit_plan_start()           { emit_event "PLAN_START" "$@"; }
emit_plan_complete()        { emit_event "PLAN_COMPLETE" "$@"; }
emit_plan_timeout()         { emit_event "PLAN_TIMEOUT" "$@"; }
emit_plan_resume()          { emit_event "PLAN_RESUME" "$@"; }
emit_spec_start()           { emit_event "SPEC_START" "$@"; }
emit_spec_dispatched()      { emit_event "SPEC_DISPATCHED" "$@"; }
emit_spec_shipped()         { emit_event "SPEC_SHIPPED" "$@"; }
emit_spec_failed()          { emit_event "SPEC_FAILED" "$@"; }
emit_spec_escalated()       { emit_event "SPEC_ESCALATED" "$@"; }
emit_agent_start()          { emit_event "AGENT_START" "$@"; }
emit_agent_completed()      { emit_event "AGENT_COMPLETED" "$@"; }
emit_agent_failed()         { emit_event "AGENT_FAILED" "$@"; }
emit_gate_completed()       { emit_event "GATE_COMPLETED" "$@"; }
emit_satisfaction_scored()   { emit_event "SATISFACTION_SCORED" "$@"; }
emit_trajectory_classified() { emit_event "TRAJECTORY_CLASSIFIED" "$@"; }
emit_regression_run()       { emit_event "REGRESSION_RUN" "$@"; }
emit_budget_exceeded()      { emit_event "BUDGET_EXCEEDED" "$@"; }
emit_budget_alert()         { emit_event "BUDGET_ALERT" "$@"; }
emit_barrier_violation()    { emit_event "BARRIER_VIOLATION" "$@"; }
emit_cost_summary()         { emit_event "COST_SUMMARY" "$@"; }
emit_anomaly_detected()     { emit_event "ANOMALY_DETECTED" "$@"; }
emit_trust_scored()         { emit_event "TRUST_SCORED" "$@"; }
emit_iter_cost()            { emit_event "ITER_COST" "$@"; }
```

#### B3. Instrumentation Points

Add `emit_*` calls at these locations:

| Location | Event | Current Code Point |
|----------|-------|-------------------|
| `dark-factory-plan.sh:dispatch_plan` top | `PLAN_START` | After plan_run_id generation (parent process) |
| `dark-factory-plan.sh:dispatch_plan` end | `PLAN_COMPLETE` | Before return (parent process) |
| `dark-factory-plan.sh` before spec `&` fork | `SPEC_DISPATCHED` | Parent process, before `(...)&` |
| `dark-factory.sh` pipeline start | `SPEC_START` | After `metrics_record_start` |
| `dark-factory.sh` SHIP block | `SPEC_SHIPPED` | Before `metrics_record_end` |
| `dark-factory.sh` escalation | `SPEC_ESCALATED` | Before `metrics_record_end` |
| `dark-factory.sh` failure | `SPEC_FAILED` | Before `metrics_record_end` |
| `docker.sh:invoke_agent` pre-run | `AGENT_START` | Before `run_agent` call (inside retry loop, per attempt) |
| `docker.sh:invoke_agent` post-run | `AGENT_COMPLETED` or `AGENT_FAILED` | After `run_agent` return (inside retry loop, per attempt) |
| `dark-factory.sh` after quality gate returns | `GATE_COMPLETED` | After `run_quality_gate`, parse output files on host |
| `satisfaction.sh:_df_run_scenarios_satisfaction` | `SATISFACTION_SCORED` | After score calculation |
| `satisfaction.sh:_df_classify_trajectory` | `TRAJECTORY_CLASSIFIED` | After classification |
| `registry.sh:registry_run_regression` end | `REGRESSION_RUN` | Before return |
| `cost.sh:cost_check_budget` on exceed | `BUDGET_EXCEEDED` | At budget exceeded branch |
| `cost.sh:cost_check_budget` on threshold | `BUDGET_ALERT` | At alert threshold cross |
| `audit.sh:validate_oracle_feedback` on violation | `BARRIER_VIOLATION` | At leakage detection |

#### B4. Agent Timing Capture

**Problem:** `invoke_agent` calls `run_agent` but doesn't capture wall-clock time or turns used.

**Implementation:**

- Wrap `run_agent` call in `invoke_agent` with timing, **inside the retry loop** (per-attempt timing) and **outside** (cumulative):
  ```bash
  local cumulative_wall_s=0
  for attempt in $(seq 1 "$retries"); do
      local attempt_start=$SECONDS
      emit_agent_start "agent_role=$role" "max_turns=$AGENT_MAX_TURNS"
      run_agent "$role" "$prompt"
      local agent_rc=$?
      local attempt_wall_s=$(( SECONDS - attempt_start ))
      cumulative_wall_s=$(( cumulative_wall_s + attempt_wall_s ))
      if (( agent_rc == 0 )); then
          emit_agent_completed "agent_role=$role" "exit_code=$agent_rc" \
              "wall_s=$attempt_wall_s" "turns_allocated=$AGENT_MAX_TURNS"
          break
      else
          emit_agent_failed "agent_role=$role" "exit_code=$agent_rc" \
              "wall_s=$attempt_wall_s" "error=agent_exit_$agent_rc"
      fi
  done
  ```
- **Turns tracking limitation:** Claude Code does not expose actual token counts or turns used in a reliably parseable format. The `turns_allocated` field records the `AGENT_MAX_TURNS` budget allocated to the agent — this is the upper bound, not actual usage. Actual turn/token tracking requires Anthropic API usage reporting and is deferred to Phase 7. Cost estimates use `turns_allocated` as a conservative proxy.
- Track in-memory cumulative per-spec: `_DF_SPEC_TOTAL_TURNS=$(( _DF_SPEC_TOTAL_TURNS + AGENT_MAX_TURNS ))`

#### B5. Backward Compatibility

- `metrics.sh` functions (`metrics_record_start`, `metrics_record_end`) continue writing `metrics.jsonl` unchanged
- `audit.sh` functions (`audit_log`) continue writing text audit logs unchanged
- New `events.sh` is sourced alongside existing libraries in `common.sh`
- `events.jsonl` added to `.dark-factory/.gitignore`

#### B6. Worktree Event Path Resolution

**Problem:** During plan execution, each spec runs in a git worktree. If `_DF_EVENTS_FILE` defaults to `$DF_PROJECT_ROOT/.dark-factory/events.jsonl`, and `DF_PROJECT_ROOT` is overridden to the worktree path in each child process, events will scatter across per-worktree files — invisible to the dashboard and trust calculator.

**Solution:**

1. In `dark-factory-plan.sh`, before dispatching any specs, set and export:
   ```bash
   export _DF_EVENTS_FILE="$DF_PROJECT_ROOT/.dark-factory/events.jsonl"
   ```
   This uses the **real** project root (before any worktree override) and exports it to all child subshells.

2. In `dark-factory.sh`, when running in worktree mode (`--worktree-path`), check if `_DF_EVENTS_FILE` is already set (inherited from plan executor). If so, do not override it. If not set (standalone single-spec mode), set it to the current `$DF_PROJECT_ROOT/.dark-factory/events.jsonl`.

3. The quality gate runs inside Docker. The events file is on the host. `GATE_COMPLETED` events are emitted by `dark-factory.sh` on the host *after* the container exits, not from inside the container. No container needs to write to `events.jsonl`.

**Write concurrency:** Multiple spec subprocesses may append to the same `events.jsonl` simultaneously. Each `emit_event` call produces a single `printf '%s\n'` (no-jq path) or `jq -n -c ... >>` (jq path). Both produce a single write of one line. On Linux (ext4, btrfs) and macOS (APFS), `O_APPEND` writes of small buffers (< PIPE_BUF = 4096 bytes) are atomic. Events are typically 200-500 bytes. For additional safety, the `flock` utility is used if available: `flock "$events_file.lock" -c "jq ... >> $events_file"`. If `flock` is unavailable (macOS without coreutils), the raw append is used with the documented atomicity assumption.

---

## 4. Workstream C: Cost Attribution & Budget Enforcement

### Problem

The current budget system tracks turns (not tokens or dollars). Turn budget is per-spec (`MAX_TOTAL_AGENT_TURNS`) or per-plan (`PLAN_TOKEN_BUDGET`), but both are advisory for plans (post-hoc check via `jq` on metrics.jsonl). There's no per-agent cost breakdown, no USD tracking, no budget alerts.

### Design

#### C1. Per-Agent Cost Tracking

**Approach:** Turn-based cost estimation. Actual token tracking is not available in Phase 5 (see B4 limitation).

**Implementation in `scripts/lib/cost.sh`:**

```bash
# Estimate cost from turns (conservative: uses allocated turns, not actual)
# Uses awk for floating-point arithmetic (available everywhere, unlike bc)
cost_estimate_from_turns() {
    local turns="$1"
    local input_rate="${COST_PER_INPUT_TOKEN:-0.000015}"
    local output_rate="${COST_PER_OUTPUT_TOKEN:-0.000075}"
    local est_input="${COST_EST_INPUT_TOKENS_PER_TURN:-4000}"
    local est_output="${COST_EST_OUTPUT_TOKENS_PER_TURN:-2000}"

    awk -v t="$turns" -v ir="$input_rate" -v or="$output_rate" \
        -v ei="$est_input" -v eo="$est_output" \
        'BEGIN { printf "%.2f", t * ei * ir + t * eo * or }'
}

# Compare two float values: returns 0 if a > b, 1 otherwise
_cost_float_gt() {
    awk -v a="$1" -v b="$2" 'BEGIN { exit (a > b) ? 0 : 1 }'
}
```

**Why `awk` instead of `bc`:** `awk` is POSIX-mandated and available in every shell environment including minimal Alpine containers. `bc` is often missing in CI environments and Docker images. Using `awk` for all floating-point arithmetic eliminates the need for fallback code paths.

#### C2. Per-Spec Cost Attribution

**Implementation:**

- New function `cost_spec_summary(spec, run_id)` reads `events.jsonl` via `jq` and sums:
  - Total allocated turns across all `AGENT_COMPLETED` events for this spec+run_id
  - Total wall-time
  - Estimated USD (via `cost_estimate_from_turns`)
  - Breakdown by agent role
- Called at spec completion, result emitted as `COST_SUMMARY` event
- **Requires `jq`.** When `jq` is unavailable, emits a simplified `COST_SUMMARY` using the in-memory cumulative `_DF_SPEC_TOTAL_TURNS` counter (no by-agent breakdown).

#### C3. Per-Iteration Cost Tracking

**Problem identified by review:** The production readiness analysis (section 4.8) explicitly calls for "per-iteration cost growth (cost increase across NO-SHIP cycles)."

**Implementation:**

- At the end of each iteration in the implementation loop (after quality gate, before oracle), emit `ITER_COST`:
  ```json
  {"event": "ITER_COST", "spec": "auth-v2", "iter": 2, "data": {"turns": 200, "wall_s": 450, "estimated_usd": "3.00"}}
  ```
- Track per-iteration turns in-memory: `_DF_ITER_TURNS` reset to 0 at iteration start, incremented by each `invoke_agent` call within the iteration.
- The CLI dashboard (F1) can then show: "Iteration 1: $2.00, Iteration 2: $3.50, Iteration 3: $4.20 (escalating)"

#### C4. USD Budget Cap

**New config variables:**

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `COST_BUDGET_USD` | float | 0 (unlimited) | Max estimated USD per spec run |
| `PLAN_COST_BUDGET_USD` | float | 0 (unlimited) | Max estimated USD per plan run |
| `COST_ALERT_THRESHOLDS` | string | "50,80,100" | Comma-separated percentages for alerts |

**Implementation:**

```bash
# Check budget after each agent completes. Uses in-memory counter.
cost_check_budget() {
    local budget="${COST_BUDGET_USD:-0}"
    [[ "$budget" == "0" ]] && return 0  # unlimited

    local estimated
    estimated=$(cost_estimate_from_turns "$_DF_SPEC_TOTAL_TURNS")

    # Check alert thresholds
    local IFS=','
    for threshold in ${COST_ALERT_THRESHOLDS:-50,80,100}; do
        local limit_at_threshold
        limit_at_threshold=$(awk -v b="$budget" -v t="$threshold" 'BEGIN { printf "%.2f", b * t / 100 }')
        if _cost_float_gt "$estimated" "$limit_at_threshold"; then
            # Only alert once per threshold (track in _DF_COST_ALERTED_*)
            local alerted_var="_DF_COST_ALERTED_${threshold}"
            if [[ -z "${!alerted_var:-}" ]]; then
                eval "$alerted_var=1"
                emit_budget_alert "percent=$threshold" "estimated_usd=$estimated" "limit_usd=$budget"
                notify_budget_alert "${_DF_CURRENT_SPEC}" "$threshold" "$estimated" "$budget"
            fi
        fi
    done

    # Hard cap: abort if over 100%
    if _cost_float_gt "$estimated" "$budget"; then
        emit_budget_exceeded "allocated_turns=$_DF_SPEC_TOTAL_TURNS" \
            "estimated_usd=$estimated" "limit_usd=$budget" "spec=${_DF_CURRENT_SPEC}"
        return 1  # caller should abort the spec
    fi
    return 0
}
```

#### C5. Budget Alert Notifications

**Implementation in `scripts/lib/notify.sh`:**

- New function `notify_budget_alert(spec, percent, estimated_usd, budget_usd)`
- Sends to Slack and generic webhook (same pattern as existing notification functions)
- Slack message: yellow attachment with cost details

#### C6. Regression Cost Tracking

**Implementation:**

- `registry_run_regression` already computes wall-time (add `SECONDS` tracking)
- Emit `REGRESSION_RUN` event with `wall_s`, `spec_count`, `passed`, `failed`
- New function `cost_regression_summary()` reads regression events from `events.jsonl` and computes:
  - Average regression duration over last N runs
  - Cost trend (increasing/stable/decreasing)
  - Projected cost at current shipped-spec growth rate

---

## 5. Workstream D: Anomaly Detection

### Problem

No automated detection of unusual agent behavior. A runaway agent that writes 500 files or consumes 100K tokens is only visible after the fact in logs.

### Design

#### D1. Threshold-Based Detection

**File:** `scripts/lib/anomaly.sh`

**Hard thresholds (configurable):**

| Metric | Config Variable | Default | Action | Notes |
|--------|----------------|---------|--------|-------|
| Agent turns (allocated) | `ANOMALY_MAX_AGENT_TURNS` | 150 | Block (kill agent) | Post-hoc check after agent completes |
| Spec total turns | `ANOMALY_MAX_SPEC_TURNS` | 800 | Block (abort spec) | Cumulative across all agents+iterations |
| Spec total wall-time | `ANOMALY_MAX_SPEC_WALL_S` | 7200 (2hr) | Block (abort spec) | Cumulative wall-time |
| Files written per agent | `ANOMALY_MAX_FILES_WRITTEN` | 50 | Warn + escalate | Checked post-agent via `git diff --stat` |
| Iteration count (no improvement) | `ANOMALY_MAX_STALE_ITERS` | 2 | Escalate | Leverages existing trajectory classification |

**Note on `ANOMALY_MAX_AGENT_WALL_S`:** Removed. The existing `AGENT_TIMEOUT` (default 600s) already kills agents that exceed their time limit via `timeout` command in `_execute_container`. Adding a separate anomaly wall-time threshold that defaults to 1800s would never trigger before `AGENT_TIMEOUT` kills the agent. Instead, `AGENT_TIMEOUT` serves as the wall-time anomaly bound. If users want a tighter anomaly threshold, they should lower `AGENT_TIMEOUT`.

**Implementation:**

```bash
# Check agent-level anomalies after agent completes
anomaly_check_agent() {
    local role="$1" turns_allocated="$2" wall_s="$3"
    local max_turns="${ANOMALY_MAX_AGENT_TURNS:-150}"

    if (( turns_allocated > max_turns )); then
        emit_anomaly_detected "type=agent_turns_exceeded" "role=$role" \
            "turns=$turns_allocated" "limit=$max_turns" "severity=block"
        return 1
    fi
    return 0
}

# Check spec-level anomalies after each iteration
anomaly_check_spec() {
    local total_turns="$1" total_wall_s="$2"
    local max_turns="${ANOMALY_MAX_SPEC_TURNS:-800}"
    local max_wall="${ANOMALY_MAX_SPEC_WALL_S:-7200}"

    if (( total_turns > max_turns )); then
        emit_anomaly_detected "type=spec_turns_exceeded" \
            "current=$total_turns" "limit=$max_turns" "severity=block"
        return 1
    fi
    if (( total_wall_s > max_wall )); then
        emit_anomaly_detected "type=spec_wall_exceeded" \
            "current=$total_wall_s" "limit=$max_wall" "severity=block"
        return 1
    fi
    return 0
}
```

#### D2. Rolling Baseline Detection

**Approach:** Compare current run metrics against historical averages from `events.jsonl`. **Requires `jq`.**

**Implementation:**

```bash
# Compute rolling baseline from last N spec runs
# Returns "mean stddev" as space-separated integers (scaled ×100 for precision)
# Requires jq.
anomaly_compute_baseline() {
    local metric="$1" window="${2:-${ANOMALY_BASELINE_WINDOW:-20}}"
    $_DF_HAS_JQ || return 1  # Cannot compute without jq

    # Extract metric values from recent events using jq
    # jq handles all float math internally, returns integers scaled ×100
    local result
    result=$(jq -r --arg metric "$metric" --argjson window "$window" '
        [inputs | select(.event == "SPEC_SHIPPED" or .event == "SPEC_FAILED")]
        | .[-$window:]
        | [.[].data[$metric] // 0 | tonumber]
        | if length < 5 then "insufficient" else
            (add / length) as $mean |
            (map(. - $mean | . * .) | add / length | sqrt) as $stddev |
            "\($mean|round) \($stddev|round)"
          end
    ' "$_DF_EVENTS_FILE" 2>/dev/null)

    [[ "$result" == "insufficient" ]] && return 1
    [[ -z "$result" ]] && return 1
    echo "$result"
}

# Check if current value is anomalous (> 2 stddev from mean)
# All comparisons use integer arithmetic (values are integers or scaled ×100)
anomaly_check_baseline() {
    local metric="$1" current_value="$2"
    local baseline
    baseline=$(anomaly_compute_baseline "$metric") || return 0  # no baseline = no check
    local mean="${baseline%% *}" stddev="${baseline##* }"
    local threshold=$(( mean + 2 * stddev ))

    if (( current_value > threshold )); then
        emit_anomaly_detected "type=baseline_deviation" "metric=$metric" \
            "current=$current_value" "mean=$mean" "stddev=$stddev" "severity=warn"
        return 1
    fi
    return 0
}
```

**Metrics tracked by baseline:**
- `wall_s` — Total wall-time per spec
- `iters` — Iteration count to ship
- Agent turns per role (queried separately)

**Practical note:** Rolling baseline requires `jq` and >= 5 completed runs. When unavailable, fall back to hard thresholds only. The `anomaly_check_baseline` function returns 0 (no anomaly) when baseline cannot be computed.

#### D3. Composite Anomaly Scoring

**Implementation:**

```bash
# Combine multiple weak signals into a composite score
# Inputs: individual signal flags (0 or 1) with point values
anomaly_composite_score() {
    local turns_flag="${1:-0}"       # +30 if high turns
    local wall_flag="${2:-0}"        # +20 if slow
    local trajectory_flag="${3:-0}"  # +25 if flat/oscillating trajectory
    local files_flag="${4:-0}"       # +15 if high file count
    local barrier_flag="${5:-0}"     # +40 if barrier violation

    local score=$(( turns_flag * 30 + wall_flag * 20 + trajectory_flag * 25 + files_flag * 15 + barrier_flag * 40 ))

    if (( score >= 70 )); then
        emit_anomaly_detected "type=composite" "score=$score" "severity=block"
        return 1  # caller should abort spec
    elif (( score >= 40 )); then
        emit_anomaly_detected "type=composite" "score=$score" "severity=escalate"
        notify_anomaly "${_DF_CURRENT_SPEC:-unknown}" "composite" "escalate" "score=$score"
    elif (( score >= 20 )); then
        emit_anomaly_detected "type=composite" "score=$score" "severity=warn"
    fi
    return 0
}
```

**Score thresholds:**
- `>= 70`: **Block** — abort spec, emit event
- `>= 40`: **Escalate** — notify human, continue with caution, emit event
- `>= 20`: **Warn** — log only, emit event
- `< 20`: **Normal** — no action

#### D4. Integration Points

- **Per-agent (inside retry loop):** After `invoke_agent` returns each attempt, call `anomaly_check_agent`. If it returns 1, do not retry — propagate failure.
- **Per-iteration:** After quality gate in `dark-factory.sh`, call `anomaly_check_spec` with `_DF_SPEC_TOTAL_TURNS` and cumulative wall-time. Also check trajectory: if Phase 3's `_df_classify_trajectory` returned `PLATEAU` or `OSCILLATING`, set `trajectory_flag=1` for composite scoring.
- **Per-spec completion:** Call `anomaly_composite_score` with all accumulated flags. Include composite score in the `SPEC_SHIPPED`/`SPEC_FAILED`/`SPEC_ESCALATED` event as an additional data field.
- **Blocking action:** If any anomaly check returns 1 with severity=block, the caller must abort the current spec with `SPEC_FAILED` and `reason=anomaly_blocked`.

---

## 6. Workstream E: Trust Scoring & Dynamic Autonomy

### Problem

`AUTONOMY_LEVEL` is a static integer. The system treats all specs identically regardless of historical performance.

### Design

#### E1. Trust Score Calculator

**File:** `scripts/lib/trust.sh`

**Requires `jq`.** When `jq` is unavailable, `trust_calculate` returns the cold-start default (50) and logs a warning. Dynamic autonomy effectively becomes a no-op.

**Input dimensions:**

| Dimension | Weight | Source | Scale |
|-----------|--------|--------|-------|
| Historical first-pass ship rate | 0.30 | events.jsonl: SPEC_SHIPPED with `iters=1` / total shipped | 0–100 |
| Average iterations to ship | 0.20 | events.jsonl: mean iters for SPEC_SHIPPED | 0–100 (inverse) |
| Recent regression failure rate | 0.20 | events.jsonl: REGRESSION_RUN with `failed>0` / total | 0–100 (inverse) |
| Spec complexity proxy | 0.15 | target-files count × dependency count | 0–100 |
| Module familiarity | 0.15 | events.jsonl: prior shipped specs touching same target-files | 0–100 |

**Implementation:**

```bash
# Returns trust score as integer 0-100
# Requires jq for historical data queries.
trust_calculate() {
    local spec="$1"
    local min_history="${TRUST_MIN_HISTORY:-5}"

    $_DF_HAS_JQ || { echo "50"; return 0; }  # Cold start without jq

    # Count shipped specs in history
    local shipped_count
    shipped_count=$(jq -s '[.[] | select(.event == "SPEC_SHIPPED")] | length' "$_DF_EVENTS_FILE" 2>/dev/null || echo "0")
    if (( shipped_count < min_history )); then
        echo "50"  # Insufficient history
        return 0
    fi

    local first_pass_rate avg_iters regression_fail_rate complexity familiarity

    first_pass_rate=$(_trust_first_pass_rate)       # 0-100
    avg_iters=$(_trust_avg_iterations)               # 0-100 (inverted: fewer iters = higher score)
    regression_fail_rate=$(_trust_regression_fail_rate)  # 0-100 (inverted)
    complexity=$(_trust_spec_complexity "$spec")      # 0-100 (inverted: lower complexity = higher score)
    familiarity=$(_trust_module_familiarity "$spec")  # 0-100

    # Weighted combination (integer arithmetic, all inputs clamped 0-100)
    local score=$(( first_pass_rate * 30 / 100 + avg_iters * 20 / 100 + regression_fail_rate * 20 / 100 + complexity * 15 / 100 + familiarity * 15 / 100 ))

    # Clamp to 0-100
    (( score < 0 )) && score=0
    (( score > 100 )) && score=100

    echo "$score"
}

# First-pass ship rate as integer 0-100
_trust_first_pass_rate() {
    jq -s '
        [.[] | select(.event == "SPEC_SHIPPED")] |
        if length == 0 then 50
        else ([.[] | select(.data.iters == "1")] | length) * 100 / length
        end
    ' "$_DF_EVENTS_FILE" 2>/dev/null || echo "50"
}

# Average iterations, inverted to 0-100 scale
# 1 iter → 100, 2 → 75, 3 → 50, 4 → 25, >=5 → 0
_trust_avg_iterations() {
    local avg
    avg=$(jq -s '
        [.[] | select(.event == "SPEC_SHIPPED") | .data.iters | tonumber] |
        if length == 0 then 2
        else add / length
        end | round
    ' "$_DF_EVENTS_FILE" 2>/dev/null || echo "2")

    # Clamp avg_iters to 1-5 range before inversion
    (( avg < 1 )) && avg=1
    (( avg > 5 )) && avg=5
    echo $(( (5 - avg) * 25 ))  # 1→100, 2→75, 3→50, 4→25, 5→0
}

# Regression failure rate, inverted (more failures = lower score)
_trust_regression_fail_rate() {
    jq -s '
        [.[] | select(.event == "REGRESSION_RUN")] |
        if length == 0 then 80
        else (1 - ([.[] | select(.data.failed | tonumber > 0)] | length) / length) * 100 | round
        end
    ' "$_DF_EVENTS_FILE" 2>/dev/null || echo "80"
}

# Spec complexity as inverted 0-100 (simpler = higher score)
_trust_spec_complexity() {
    local spec="$1"
    local spec_file="$DF_PROJECT_ROOT/.dark-factory/specs/${spec}.md"
    [[ -f "$spec_file" ]] || { echo "50"; return 0; }

    local target_files deps
    target_files=$(grep -c '^- ' <<< "$(sed -n '/^target-files:/,/^[a-z]/p' "$spec_file" | grep '^- ')" 2>/dev/null || echo "0")
    deps=$(grep -c '^- ' <<< "$(sed -n '/^depends-on:/,/^[a-z]/p' "$spec_file" | grep '^- ')" 2>/dev/null || echo "0")

    local raw_complexity=$(( target_files * 10 + deps * 20 ))
    # Cap at 100, invert
    (( raw_complexity > 100 )) && raw_complexity=100
    echo $(( 100 - raw_complexity ))
}

# Module familiarity: how many prior shipped specs touched overlapping files
_trust_module_familiarity() {
    local spec="$1"
    local spec_file="$DF_PROJECT_ROOT/.dark-factory/specs/${spec}.md"
    [[ -f "$spec_file" ]] || { echo "50"; return 0; }

    # Extract target files from spec
    local targets
    targets=$(sed -n '/^target-files:/,/^[a-z]/p' "$spec_file" | grep '^- ' | sed 's/^- //')
    [[ -z "$targets" ]] && { echo "50"; return 0; }

    # Count shipped specs in history as a project-maturity proxy.
    # NOTE: This does not check file-level overlap — it measures how many
    # specs the project has shipped total, capped at 10. Per-file overlap
    # requires SPEC_SHIPPED events to carry target-files metadata, which
    # is a Phase 7 enhancement. For Phase 5, project maturity correlates
    # well enough with module familiarity at the 0.15 weight.
    local familiar_count
    familiar_count=$(jq -s '
        [.[] | select(.event == "SPEC_SHIPPED")] | length |
        if . == 0 then 0 elif . > 10 then 10 else . end
    ' "$_DF_EVENTS_FILE" 2>/dev/null || echo "0")

    # Scale to 0-100 (10+ shipped specs = max familiarity)
    echo $(( familiar_count * 10 ))
}
```

**Cold start:** When `events.jsonl` has fewer than `TRUST_MIN_HISTORY` (default: 5) `SPEC_SHIPPED` events, return a default trust score of 50.

#### E2. Dynamic Autonomy Selection

**Trust-to-autonomy mapping:**

| Trust Score | Effective Autonomy | Behavior Delta from Static |
|-------------|-------------------|---------------------------|
| 0–29 | 1 (Assisted) | Pause for human confirmation even if `AUTONOMY_LEVEL=3` |
| 30–59 | 2 (Supervised) | Auto-proceed but require human-visible notification |
| 60–79 | 3 (Autonomous) | Standard autonomous pipeline |
| 80–100 | 3+ (High-trust) | Auto-merge via `gh pr merge --auto` if `AUTO_MERGE_HIGH_TRUST=true` |

**Implementation:**

- New config: `DYNAMIC_AUTONOMY=false` (opt-in, default off)
- When enabled, in `dark-factory.sh` after spec validation:
  1. Call `trust_calculate "$spec_name"`
  2. Compute `effective_autonomy` from trust score:
     ```bash
     trust_to_autonomy() {
         local score="$1"
         if (( score >= 80 )); then echo "3"   # high-trust (3+ handled separately)
         elif (( score >= 60 )); then echo "3"
         elif (( score >= 30 )); then echo "2"
         else echo "1"
         fi
     }
     ```
  3. Apply ceiling: `effective_autonomy=$(( effective_autonomy < AUTONOMY_LEVEL ? effective_autonomy : AUTONOMY_LEVEL ))`
  4. Override `AUTONOMY_LEVEL=$effective_autonomy` for this run
  5. Emit `TRUST_SCORED` event with trust score, dimensions, effective autonomy
  6. Log: `"TRUST: score=$score effective_autonomy=$effective_autonomy (static=$original_autonomy)"`

**Override rules:**
- Static `AUTONOMY_LEVEL` is a **ceiling** — dynamic autonomy can lower it but never raise it above the configured value
- `AUTONOMY_LEVEL=1` always wins (explicit human-in-the-loop cannot be overridden by trust)

**`AUTO_MERGE_HIGH_TRUST` mechanics:**
- When trust score >= 80 AND `AUTO_MERGE_HIGH_TRUST=true` AND `AUTO_PR=true`:
  - After PR creation, call `gh pr merge --auto --squash "$pr_url"` to enable auto-merge
  - GitHub will merge the PR once all required status checks pass
  - This is additive to existing git-ops flow — it adds `--auto` to the merge, not a separate path

#### E3. Trust Feedback Loop

After each spec ships or fails, the trust score implicitly updates because `events.jsonl` contains the new outcome. No separate "update" step is needed — the calculator always reads current history.

After regression failures, the effect is immediate: the next spec run will see the regression failure in history and compute a lower trust score.

#### E4. Convergence Prediction

**Purpose:** Before starting a spec, predict how many iterations it will likely need. Use this to set iteration expectations and allocate token budget.

**Implementation:**

```bash
# Predict iterations needed for a spec
trust_predict_iterations() {
    local spec="$1"

    $_DF_HAS_JQ || { echo "2"; return 0; }  # Default without jq

    local complexity
    complexity=$(_trust_spec_complexity "$spec")

    # Historical average for specs in similar complexity range (±20 points)
    local predicted
    predicted=$(jq -s --argjson complexity "$complexity" '
        [.[] | select(.event == "SPEC_SHIPPED") | .data.iters | tonumber] |
        if length < 3 then null
        else add / length | round | if . < 1 then 1 elif . > 5 then 5 else . end
        end
    ' "$_DF_EVENTS_FILE" 2>/dev/null)

    if [[ -z "$predicted" || "$predicted" == "null" ]]; then
        # Default based on complexity
        if (( complexity < 30 )); then echo "3"      # complex (low inverted score)
        elif (( complexity < 60 )); then echo "2"
        else echo "1"                                  # simple
        fi
        return
    fi

    echo "$predicted"
}
```

- Predicted iterations logged at spec start
- Used to set `MAX_ITERATIONS` dynamically if `DYNAMIC_ITERATIONS=true`: `MAX_ITERATIONS=$(( predicted + 1 ))` (add 1 for buffer)
- Emitted in `SPEC_START` event as `predicted_iters`

---

## 7. Workstream F: Dashboards & Alerting

### Problem

All data is in flat files with no visualization or alerting.

### Design

#### F1. CLI Summary Dashboard

**Priority:** This is the most immediately useful deliverable — a CLI command that reads `events.jsonl` and prints a formatted summary. **Requires `jq`.**

**File:** `scripts/df-dashboard.sh`

**Usage:** `bash scripts/df-dashboard.sh [--last N] [--spec SPEC] [--plan PLAN]`

**Output sections:**

```
═══ Dark Factory Dashboard ═══════════════════════════════════════

  Pipeline Health (last 30 days)
  ─────────────────────────────
  Specs shipped:     47/52 (90.4%)
  Avg iterations:    1.8
  Avg wall-time:     8m 23s
  First-pass rate:   62%

  Cost Summary
  ─────────────
  Total turns:       12,847
  Est. USD:          $193.50
  Avg per spec:      $4.11
  Budget remaining:  $806.50 / $1,000.00

  Per-Iteration Cost Growth (last 5 specs)
  ─────────────────────────────────────────
  auth-v2:     iter1=$2.00 → iter2=$3.50  (+75%)
  search-v3:   iter1=$2.10  (first-pass)
  payments-v1: iter1=$2.50 → iter2=$3.00 → iter3=$4.20  (+68%)

  Trust Score
  ───────────
  Global:            72 / 100
  Trend:             ↑ improving (was 65 last week)

  Recent Anomalies
  ────────────────
  [WARN] auth-v2: 156 turns (baseline: 89 ± 23)
  [BLOCK] payments-v1: barrier violation detected

  Recent Runs
  ───────────
  auth-v2       SHIPPED  iter=2  sat=94%  $5.20  12m ago
  search-v3     SHIPPED  iter=1  sat=98%  $2.10  1h ago
  payments-v1   FAILED   iter=3  sat=67%  $8.90  2h ago
═══════════════════════════════════════════════════════════════════
```

**Implementation:** Pure bash + `jq` for JSON parsing. Color output via ANSI codes (respects `NO_COLOR` env var). Exits with error message if `jq` is not available.

#### F2. OTEL-Compatible Export

**File:** `scripts/df-export-otel.sh`

**Purpose:** Transform `events.jsonl` into OTEL-compatible JSON for ingestion by Grafana Tempo (traces) and Prometheus (metrics).

**Trace mapping:**
- One trace per spec run (`run_id` = trace ID)
- Spans: `SPEC_START` → `SPEC_SHIPPED`/`SPEC_FAILED` (root span), `AGENT_START` → `AGENT_COMPLETED` (child spans)
- Span attributes follow GenAI OTEL semantic conventions

**Metrics mapping:**
- `df.spec.duration_s` (histogram)
- `df.spec.iterations` (histogram)
- `df.spec.satisfaction_score` (gauge)
- `df.agent.turns` (histogram, by role)
- `df.agent.duration_s` (histogram, by role)
- `df.cost.estimated_usd` (counter)
- `df.cost.per_iteration_usd` (histogram)
- `df.regression.duration_s` (histogram)
- `df.trust.score` (gauge)

**Implementation:** `jq` transforms `events.jsonl` → OTLP JSON format. Output to stdout or file. Can be piped to `otel-cli` or `curl`'d to an OTEL Collector endpoint.

**This is a convenience script, not a runtime dependency.** The pipeline never calls it. Users or CI run it post-hoc to feed dashboarding tools.

#### F3. Grafana Dashboard Templates

**Directory:** `dashboards/`

**Files:**
- `dashboards/pipeline-health.json` — Pipeline success rates, durations, iteration distributions
- `dashboards/agent-performance.json` — Per-agent turns, wall-time, error rates
- `dashboards/cost-tracking.json` — Running totals, per-spec costs, per-iteration cost growth trends
- `dashboards/trust-scores.json` — Trust over time, per-module breakdown

**These are static JSON files.** They reference Prometheus data source and the metrics exported by `df-export-otel.sh`. Users import them into their Grafana instance.

#### F4. Alert Webhook Integration

**Existing:** `notify.sh` sends to Slack and generic webhooks for pipeline events.

**Extension:**

- Add anomaly detection alerts: `notify_anomaly(spec, type, severity, detail)`
- Add budget alerts: `notify_budget_alert(spec, percent, usd_used, usd_limit)`
- Add trust alerts: `notify_trust_change(spec, old_score, new_score, reason)`

**Format:** Same JSON envelope as existing generic webhook, new event types:
- `anomaly_detected`
- `budget_alert`
- `trust_changed`

---

## 8. File Inventory

### New Files

| File | Purpose | Lines (est.) |
|------|---------|-------------|
| `scripts/lib/events.sh` | Structured event emitter (B1–B6) | ~180 |
| `scripts/lib/plan-state.sh` | Plan checkpoint/resume (A1–A2) | ~150 |
| `scripts/lib/cost.sh` | Cost estimation & attribution (C1–C6) | ~180 |
| `scripts/lib/anomaly.sh` | Anomaly detection engine (D1–D4) | ~200 |
| `scripts/lib/trust.sh` | Trust scoring & dynamic autonomy (E1–E4) | ~300 |
| `scripts/df-dashboard.sh` | CLI dashboard (F1) | ~300 |
| `scripts/df-export-otel.sh` | OTEL export (F2) | ~200 |
| `scripts/df-events-migrate.sh` | Migrate metrics.jsonl → events.jsonl | ~80 |
| `scripts/df-events-rotate.sh` | Rotate events.jsonl by size | ~60 |
| `tests/test-events.sh` | Event system tests | ~200 |
| `tests/test-plan-state.sh` | Plan checkpoint tests | ~200 |
| `tests/test-cost.sh` | Cost attribution tests | ~150 |
| `tests/test-anomaly.sh` | Anomaly detection tests | ~170 |
| `tests/test-trust.sh` | Trust scoring tests | ~220 |
| `tests/test-events-integration.sh` | Full event flow integration test | ~150 |
| `dashboards/pipeline-health.json` | Grafana dashboard | ~200 |
| `dashboards/agent-performance.json` | Grafana dashboard | ~200 |
| `dashboards/cost-tracking.json` | Grafana dashboard | ~200 |
| `dashboards/trust-scores.json` | Grafana dashboard | ~200 |

### Modified Files

| File | Changes |
|------|---------|
| `scripts/lib/common.sh` | Source `events.sh`, `plan-state.sh`, `cost.sh`, `anomaly.sh`, `trust.sh` |
| `scripts/dark-factory-plan.sh` | Plan checkpoint write/load, plan events, wall-time timeout, `_DF_EVENTS_FILE` export |
| `scripts/dark-factory.sh` | Spec events, trust scoring, anomaly checks, cost summary, per-iteration cost, `_DF_EVENTS_FILE` init |
| `scripts/lib/docker.sh` | Agent timing in `invoke_agent` (inside retry loop), agent events, cumulative turn counter |
| `scripts/lib/satisfaction.sh` | Satisfaction/trajectory events |
| `scripts/lib/registry.sh` | Regression locking, regression events |
| `scripts/lib/metrics.sh` | Bridge: budget events delegated to cost.sh |
| `scripts/lib/audit.sh` | Barrier violation events |
| `scripts/lib/notify.sh` | Anomaly/budget/trust alert functions |
| `scripts/lib/git-ops.sh` | Auto-merge for high-trust specs |
| `.dark-factory/.gitignore` | Add `events.jsonl` |

### Migration Script

**`scripts/df-events-migrate.sh`:** One-time utility to backfill `events.jsonl` from existing `metrics.jsonl` for projects with Phase 1–4 history. Converts each metrics.jsonl end record into a `SPEC_SHIPPED` or `SPEC_FAILED` event. This provides trust scoring and baseline anomaly detection with immediate historical context instead of requiring a cold start.

**Total new code:** ~2,980 lines (libraries + tests + migration)
**Total modified code:** ~350 lines of insertions across existing files

---

## 9. Configuration Variables

### New Variables (all optional with defaults)

| Variable | Type | Default | Workstream | Description |
|----------|------|---------|------------|-------------|
| `PLAN_AUTO_RESUME` | bool | false | A | Auto-resume from checkpoint on re-run |
| `PLAN_MAX_WALL_TIME_S` | int | 0 | A | Plan wall-time limit (0=unlimited) |
| `COST_BUDGET_USD` | float | 0 | C | Per-spec USD budget (0=unlimited) |
| `PLAN_COST_BUDGET_USD` | float | 0 | C | Per-plan USD budget (0=unlimited) |
| `COST_ALERT_THRESHOLDS` | string | "50,80,100" | C | Alert percentage thresholds |
| `COST_PER_INPUT_TOKEN` | float | 0.000015 | C | USD per input token |
| `COST_PER_OUTPUT_TOKEN` | float | 0.000075 | C | USD per output token |
| `COST_EST_INPUT_TOKENS_PER_TURN` | int | 4000 | C | Estimated input tokens/turn |
| `COST_EST_OUTPUT_TOKENS_PER_TURN` | int | 2000 | C | Estimated output tokens/turn |
| `ANOMALY_MAX_AGENT_TURNS` | int | 150 | D | Hard limit: allocated turns per agent |
| `ANOMALY_MAX_SPEC_TURNS` | int | 800 | D | Hard limit: total turns per spec |
| `ANOMALY_MAX_SPEC_WALL_S` | int | 7200 | D | Hard limit: total seconds per spec |
| `ANOMALY_MAX_FILES_WRITTEN` | int | 50 | D | Hard limit: files per agent |
| `ANOMALY_MAX_STALE_ITERS` | int | 2 | D | Max iterations without improvement |
| `ANOMALY_BASELINE_WINDOW` | int | 20 | D | Rolling baseline sample size |
| `DYNAMIC_AUTONOMY` | bool | false | E | Enable trust-based autonomy |
| `DYNAMIC_ITERATIONS` | bool | false | E | Enable predicted iteration counts |
| `AUTO_MERGE_HIGH_TRUST` | bool | false | E | Auto-merge via `gh pr merge --auto` when trust > 80 |
| `TRUST_MIN_HISTORY` | int | 5 | E | Min shipped specs for trust scoring |

---

## 10. Test Plan

### Test Strategy

Every new library file gets a corresponding test file. Tests use the existing `test-helpers.sh` framework with `setup_tmpdir` for isolation. An additional integration test (`test-events-integration.sh`) validates the end-to-end event flow.

### Test Files

#### `tests/test-events.sh` (~200 lines)

1. `emit_event` writes valid JSON to events file (validate with `jq`)
2. `emit_event` includes context globals when set
3. `emit_event` omits context globals when unset
4. `emit_event` handles special characters in values (quotes, newlines, backslashes)
5. `emit_event` with jq path produces identical schema to no-jq path
6. Convenience wrappers (`emit_plan_start`, etc.) emit correct event types — test all 23
7. Concurrent `emit_event` calls produce valid JSONL (spawn 20 subshells, verify all lines are valid JSON)
8. Missing events directory is created automatically
9. `_DF_EVENTS_FILE` override works for test isolation
10. `_df_timestamp` returns ISO 8601 format
11. No-jq fallback: `_DF_HAS_JQ=false` still produces valid JSON
12. Large values (>1000 chars) don't corrupt the file

#### `tests/test-plan-state.sh` (~200 lines)

1. `plan_checkpoint_write` creates file with correct JSON
2. `plan_checkpoint_write` appends (not overwrites) on subsequent calls
3. `plan_checkpoint_load` returns latest checkpoint by timestamp in first line
4. `plan_checkpoint_load` returns empty when no checkpoint exists
5. `plan_checkpoint_completed_specs` returns shipped specs
6. `plan_checkpoint_completed_specs` returns budget_skipped specs
7. `plan_checkpoint_completed_specs` excludes failed specs
8. `plan_checkpoint_completed_specs` excludes escalated specs
9. Resume integration: shipped specs skipped in dispatch loop (mock test)
10. Resume with changed spec hash forces re-execution
11. `--fresh` flag ignores existing checkpoint
12. Checkpoint append is durable (verify `sync` behavior)
13. Wall-time timeout sends SIGTERM to children
14. Wall-time timeout emits PLAN_TIMEOUT event
15. Wall-time timeout returns exit code 2
16. Wall-time uses `$(date +%s)` not `$SECONDS`

#### `tests/test-cost.sh` (~150 lines)

1. `cost_estimate_from_turns` with default rates
2. `cost_estimate_from_turns` with custom rates
3. `cost_estimate_from_turns` with zero turns returns 0.00
4. `cost_estimate_from_turns` works without `bc` (uses `awk`)
5. `_cost_float_gt` correctly compares float values
6. `cost_spec_summary` aggregates events correctly (with jq)
7. `cost_spec_summary` fallback without jq uses in-memory counter
8. `cost_check_budget` under budget returns 0
9. `cost_check_budget` over budget returns 1 and emits BUDGET_EXCEEDED
10. `cost_check_budget` alert thresholds trigger at correct percentages
11. `cost_check_budget` alerts only fire once per threshold (idempotent)
12. Per-iteration cost tracking: `ITER_COST` events emitted at correct points

#### `tests/test-anomaly.sh` (~170 lines)

1. `anomaly_check_agent` passes below all thresholds
2. `anomaly_check_agent` blocks on turns exceeded
3. `anomaly_check_spec` blocks on total turns exceeded
4. `anomaly_check_spec` blocks on total wall-time exceeded
5. `anomaly_compute_baseline` returns "insufficient" with < 5 events
6. `anomaly_compute_baseline` calculates mean and stddev correctly (verify against known data)
7. `anomaly_check_baseline` passes within 2 stddev
8. `anomaly_check_baseline` warns beyond 2 stddev
9. `anomaly_check_baseline` returns 0 (no anomaly) without jq
10. `anomaly_check_baseline` returns 0 (no anomaly) with insufficient history
11. `anomaly_composite_score` normal (< 20) → return 0, no event
12. `anomaly_composite_score` warn (20–39) → return 0, emit warn event
13. `anomaly_composite_score` escalate (40–69) → return 0, emit escalate event, notify
14. `anomaly_composite_score` block (≥ 70) → return 1, emit block event
15. Composite with barrier violation alone (40 points) → escalate

#### `tests/test-trust.sh` (~220 lines)

1. `trust_calculate` returns 50 with no history (cold start)
2. `trust_calculate` returns 50 without jq
3. `trust_calculate` returns high score (>75) with all-first-pass history
4. `trust_calculate` returns low score (<30) with many-failure history
5. `_trust_first_pass_rate` computes correctly from events
6. `_trust_avg_iterations` computes correctly — verify clamping at boundaries
7. `_trust_avg_iterations` with avg_iters=0 → clamps to 1 → score 100
8. `_trust_avg_iterations` with avg_iters=10 → clamps to 5 → score 0
9. `_trust_regression_fail_rate` computes correctly
10. `_trust_spec_complexity` scales with target-files count
11. `_trust_spec_complexity` caps at 100 (inversion floor is 0)
12. `_trust_module_familiarity` scales with prior specs
13. `trust_to_autonomy`: score < 30 → autonomy 1
14. `trust_to_autonomy`: score 30-59 → autonomy 2
15. `trust_to_autonomy`: score 60-79 → autonomy 3
16. `trust_to_autonomy`: score ≥ 80 → autonomy 3
17. Ceiling: trust=90, AUTONOMY_LEVEL=2 → effective=2
18. Ceiling: AUTONOMY_LEVEL=1 always wins regardless of trust
19. `trust_predict_iterations` returns sensible defaults with no history
20. `trust_predict_iterations` uses historical data when available
21. All dimension functions handle empty events file gracefully
22. All dimension functions return integers (no floats)

#### `tests/test-events-integration.sh` (~150 lines)

Integration test that simulates a minimal pipeline flow using mock scripts:

1. Mock plan executor dispatches 2 specs
2. Mock dark-factory.sh emits SPEC_START, AGENT_START, AGENT_COMPLETED, GATE_COMPLETED, SPEC_SHIPPED events
3. Verify all expected event types appear in events.jsonl in chronological order
4. Verify all events are valid JSON (pipe through `jq`)
5. Verify plan-level events have `plan_run_id` but no `spec`
6. Verify spec-level events have both `spec` and `run_id`
7. Verify worktree subprocesses write to the shared events file (not per-worktree)
8. Verify `COST_SUMMARY` event appears at spec completion
9. Verify concurrent event writes from 2 mock specs produce valid JSONL

---

## 11. Implementation Order & Dependencies

### Dependency Graph

```
B1 (Event Schema)
├── B2 (Event Emitter) ────────────────────────┐
│   ├── B3 (Instrumentation) ──────────────────┤
│   ├── B4 (Agent Timing) ─────────────────────┤
│   ├── B5 (Backward Compat) ─────────────────┤
│   └── B6 (Worktree Path Resolution) ────────┤
│                                               │
├── A1 (Plan Checkpoint) ← B2                  │
│   ├── A2 (Plan Resume) ← A1                 │
│   └── A3 (Wall-Time Timeout) ← A1           │
│                                               │
├── C1 (Per-Agent Cost) ← B2, B4              │
│   ├── C2 (Per-Spec Cost) ← C1               │
│   ├── C3 (Per-Iteration Cost) ← C1          │
│   ├── C4 (USD Budget) ← C2                  │
│   ├── C5 (Budget Alerts) ← C4               │
│   └── C6 (Regression Cost) ← B2             │
│                                               │
├── D1 (Threshold Anomaly) ← B2               │
│   ├── D2 (Baseline Anomaly) ← B2            │
│   ├── D3 (Composite Score) ← D1, D2         │
│   └── D4 (Integration) ← D3                 │
│                                               │
├── E1 (Trust Calculator) ← B2                │
│   ├── E2 (Dynamic Autonomy) ← E1            │
│   ├── E3 (Trust Feedback) ← E1              │
│   └── E4 (Convergence) ← E1                 │
│                                               │
├── F1 (CLI Dashboard) ← B2                   │
│   ├── F2 (OTEL Export) ← B2                 │
│   └── F3 (Grafana Templates) ← F2           │
│                                               │
├── A4 (Registry Regression Lock) — independent│
│                                               │
└── Migration Script ← B1                      │
```

### Implementation Sprints

**Sprint 1 (Week 13–14): Foundation**
1. `events.sh` — Event emitter with jq and no-jq paths (B1, B2, B6)
2. `tests/test-events.sh`
3. Instrumentation in `dark-factory.sh`, `docker.sh` (B3, B4)
4. Backward compatibility verification (B5)
5. `plan-state.sh` — Checkpoint/resume (A1, A2)
6. `tests/test-plan-state.sh`
7. Registry regression locking (A4)
8. `df-events-migrate.sh` — Migration script

**Sprint 2 (Week 15–16): Cost & Anomaly**
1. Plan wall-time timeout (A3)
2. `cost.sh` — Cost tracking with awk (C1, C2, C3, C4)
3. `tests/test-cost.sh`
4. Budget alert notifications (C5)
5. Regression cost tracking (C6)
6. `anomaly.sh` — Detection engine (D1, D2, D3)
7. `tests/test-anomaly.sh`
8. Anomaly integration points (D4)

**Sprint 3 (Week 17–18): Intelligence & Dashboards**
1. `trust.sh` — Trust scoring with all dimension functions (E1)
2. Dynamic autonomy + auto-merge (E2, E3)
3. Convergence prediction (E4)
4. `tests/test-trust.sh`
5. `df-dashboard.sh` — CLI dashboard (F1)
6. `df-export-otel.sh` — OTEL export (F2)
7. Grafana templates (F3)
8. Alert extensions in `notify.sh` (F4)
9. `tests/test-events-integration.sh` — End-to-end integration test

---

## 12. Risk Assessment

### Technical Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| `jq` not available in all environments | Medium | High | See jq degradation matrix below |
| `events.jsonl` grows unbounded | Medium | Medium | See event log rotation strategy below |
| Concurrent event writes corrupt JSON | Low | Medium | `flock` when available; O_APPEND atomicity for small writes; integration test validates |
| Trust scoring oscillates due to small history | Medium | Low | `TRUST_MIN_HISTORY` threshold (default: 5); cold-start default of 50 |
| Plan resume skips specs that should re-run | Low | High | Spec hash validation on resume; `--fresh` escape hatch |
| Turn-based cost estimates inaccurate | High | Low | Document ±50% accuracy; tunable `COST_EST_*` variables; real token tracking Phase 7 |

### jq Degradation Matrix

| Feature | Without `jq` | With `jq` |
|---------|-------------|-----------|
| Event emission | Works (printf fallback) | Works (safe JSON via jq -n) |
| Threshold anomaly detection | Works | Works |
| Baseline anomaly detection | **Disabled** (returns 0 = no anomaly) | Works |
| Cost estimation (per-agent) | Works (awk) | Works |
| Cost summary (by-agent breakdown) | **Simplified** (total only) | Full breakdown |
| Trust scoring | **Disabled** (returns 50 = cold start) | Works |
| Dynamic autonomy | **Effectively disabled** (always mid-range) | Works |
| CLI dashboard | **Not available** (error + exit) | Works |
| OTEL export | **Not available** | Works |

**Startup check:** `common.sh` sets `_DF_HAS_JQ=true/false` at source time. If `jq` is absent and `DYNAMIC_AUTONOMY=true` or `ANOMALY_BASELINE_WINDOW > 0`, log a warning: `"WARN: jq not found — trust scoring and baseline anomaly detection disabled"`.

### Event Log Rotation Strategy

**File:** `scripts/df-events-rotate.sh`

**Strategy:** Rotate by size (default: 10MB, configurable via `EVENTS_MAX_SIZE_MB`).

**Behavior:**
1. If `events.jsonl` exceeds the size limit:
   - Move to `events.jsonl.1` (overwriting any existing `.1`)
   - Create fresh `events.jsonl`
2. Keep at most 3 rotated files (`events.jsonl.1`, `.2`, `.3`)
3. Trust scoring and baseline detection always read the current file only (rolling window inherently limits lookback)
4. Dashboard can optionally read rotated files with `--include-rotated`

**Trigger:** Run `df-events-rotate.sh` as a cron job, CI post-step, or manually. Not called automatically by the pipeline (rotation during a run could lose events).

### Scope Risks

| Risk | Mitigation |
|------|------------|
| Workstream F (dashboards) delays core workstreams | F is lowest priority; can be deferred without impacting pipeline functionality |
| OTEL export complexity | F2 is a post-hoc utility, not runtime. Start with metrics only, add traces later |
| Cost estimation accuracy | Document ±50% accuracy. Real token tracking is Phase 7 |

### What This Phase Does NOT Include (deferred)

- **Real token tracking via API** — Requires Anthropic API usage endpoint (Phase 7)
- **Strategy switching on futile iterations** — Stays as trajectory classification + human escalation (Phase 7)
- **Selective regression by file overlap** — Phase 7
- **Durable execution via Temporal** — Phase 7+
- **Multi-model agent selection** — Phase 7
- **Spec sanitization / prompt injection detection** — Phase 7
- **Langfuse / LLM-specific observability** — Out of scope; the OTEL export + Grafana approach provides equivalent visibility for non-token metrics. Real LLM observability requires token-level tracking (Phase 7).
- **Per spec-type trust dimensions** — Phase 5 computes trust globally. Per-type segmentation (feature vs. bug-fix vs. refactor) requires spec metadata standardization and is a Phase 7 enhancement.
- **"Downgrade capabilities" anomaly response** — The plan supports block, escalate, and warn severities. "Downgrade" (e.g., reducing max turns for the next agent) adds complexity with limited value over blocking. Deferred.

---

## Acceptance Criteria

Phase 5 is complete when:

1. **Plan durability:** A plan executor crash followed by re-run with `--resume` skips previously-shipped specs and completes the remaining ones
2. **Event stream:** All 23 event types are emitted at their documented instrumentation points
3. **Worktree events:** Events from worktree subprocesses appear in the shared `events.jsonl` at the project root
4. **Cost tracking:** Per-spec cost summary is emitted on spec completion, with by-agent breakdown (when `jq` available); per-iteration `ITER_COST` events show cost growth across NO-SHIP cycles
5. **Budget enforcement:** Exceeding `COST_BUDGET_USD` aborts the spec; crossing alert thresholds fires notifications
6. **Anomaly detection:** Hard threshold violations block agents; baseline deviations emit warnings (when `jq` available)
7. **Trust scoring:** `DYNAMIC_AUTONOMY=true` adjusts effective autonomy level per spec based on historical data (when `jq` available)
8. **CLI dashboard:** `bash scripts/df-dashboard.sh` displays pipeline health, cost (including per-iteration growth), trust, and recent runs
9. **All tests pass:** 6 new test files with ≥90 total test cases, all green
10. **Backward compatibility:** Existing single-spec and multi-spec pipelines work identically with no configuration changes
11. **jq degradation:** All pipeline-critical paths work without `jq`; only analytics/intelligence features degrade
12. **Migration:** `df-events-migrate.sh` successfully backfills events from existing `metrics.jsonl`
13. **Documentation:** Phase 5 row in production readiness analysis updated with completion status
