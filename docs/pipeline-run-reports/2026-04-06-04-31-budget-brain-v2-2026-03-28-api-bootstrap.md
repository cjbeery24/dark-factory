# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-06 04:31
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Dark Factory version:** editable install from ~/sites/dark-factory

---

## Pre-Run State

- budget-brain-v2 had modified tracked files (`apps/api/package.json`, `apps/api/src/index.ts`, `pnpm-lock.yaml`) and untracked `.dark-factory` artifacts from prior pipeline runs
- Full `git checkout -- . && git clean -fd` performed
- `.dark-factory/state/`, `reports/`, `scenarios/`, `audit/`, `architecture/`, `failures/` cleared
- **NOTE:** In the second reset (Run 1b), `.dark-factory/scenarios/` was NOT re-cleared. Scenarios from a prior run persisted into this pipeline run (see Run 1 observations).
- Python bytecode caches cleared
- Docker images rebuilt (all CACHED)
- Orphan Docker containers/networks removed

---

## Run 1 — Aborted (Dual-Process Corruption)

### What Happened

I accidentally started **two** `dark-factory run` processes nearly simultaneously:
1. First via `dark-factory run ... &` (backgrounded in bash) — PID 52662
2. Second via `Bash` tool `run_in_background=true` — PID 52864

Both processes shared the same `run_id` (`82282978ac88419c`) because they computed it from the same spec hash at the same time. The `.pid` file was claimed by PID 52864.

When I killed the orphan (52662), the run state was corrupted. The surviving process resumed from a **pre-existing state file** left over from an earlier (pre-test) run showing `iteration=4`, `phase=review`, `status=interrupted`, `last_decision=NO-SHIP`, `pass_rate=48`.

Because `iteration=4` was beyond `max_iterations=3`, the surviving process immediately ran the escalation agent and exited:

```
WARNING: previous run status is 'running' — may have crashed without interrupt handler. Resuming from last known step. Use --fresh to start over.
Resuming from step 2, iteration 4 (use --fresh to start over)
Checking spec...
Running scenario smoke test...
  Smoke test: all scenarios fail cleanly (healthy)
Starting iteration loop (max 3 iterations)
Running escalation agent...
ESCALATED: 2026-03-28-api-bootstrap (after 0 iterations)
```

### Pipeline Observation: Stale State Reuse Risk

My initial `rm -rf .dark-factory/state/*` failed to clear the state file because **the running pipeline process recreated it** after my cleanup. This is a reasonable behavior, but combined with:
- No `--fresh` flag by default
- No spec-hash mismatch protection (the state file matched the spec hash, so resume was "valid")
- Resume from `iteration=4` still proceeds without warning the user that max iterations was already exceeded

...this means a crashed prior run can poison a fresh attempt unless the user knows to use `--fresh`.

### Outcome

Aborted and restarted with `--fresh`. Cleaned Docker containers and state files by hand before Run 1b.

---

## Run 1b — Full Pipeline Execution (ESCALATED after 3 iterations)

### Configuration

- **Run ID:** `2fa901a3a2744da2`
- **Model:** `claude-sonnet-4-20250514` (all agents)
- **Max iterations:** 3
- **Autonomy:** level 3
- **Enterprise:** enabled
- **Start:** 2026-04-06T04:37:39Z
- **End:** 2026-04-06T05:10:33Z
- **Total wall time:** ~33 minutes

### Phase Timeline (from events.jsonl)

| Phase | Agent | Start | Wall (s) |
|---|---|---|---|
| Pre-flight | spec-validator | 04:37:39 | 82 |
| Plan | architect | 04:39:01 | 244 |
| Iter 1 impl A | implementer | 04:43:51 | 190 |
| Iter 1 impl B (pre-gate retry) | implementer | 04:47:02 | 167 |
| Iter 1 smoke | — | 04:49:54 | passed |
| Iter 1 gate | quality-gate | — | 357 |
| Iter 1 review | reviewer | 04:50:39 | 96 |
| Iter 1 oracle A | oracle | 04:52:15 | 50 |
| Iter 1 oracle B | oracle | 04:53:05 | 45 |
| Iter 2 impl A | implementer | 04:53:50 | ~210 |
| Iter 2 impl B (pre-gate retry) | implementer | — | — |
| Iter 2 gate | quality-gate | 04:58:23 | 217 |
| Iter 2 review | reviewer | 04:58:23 | 168 |
| Iter 2 oracle A+B | oracle | 05:01:11 | 67+75 |
| Iter 3 impl A | implementer | 05:03:33 | 186 |
| Iter 3 impl B (pre-gate retry) | implementer | 05:06:44 | 112 |
| Iter 3 gate | quality-gate | — | 298 |
| Iter 3 escalation | escalation | 05:09:23 | 70 |
| ESCALATED | — | 05:10:33 | — |

### Scenario Results (identical across all 3 iterations)

```
SCENARIO_TOTAL=27
SCENARIO_PASSED=9
SCENARIO_FAILED=18
SCENARIO_ASSERT_FAILED=18
SCENARIO_CRASHED=0
SCENARIO_RATE=33
SATISFACTION_SCORE=33
```

### Category Breakdown

```
CAT_01  1/1    (weight 100)   PASS  — contracts
CAT_02  0/1    (weight 100)   FAIL
CAT_03  0/5    (weight 80)    FAIL
CAT_04  0/7    (weight 90)    FAIL
CAT_05  0/6    (weight 80)    FAIL
```

Only category 01 (presumably the contract tests) consistently passed. Every behavioral/edge-case/error-handling/security category returned 0/N across all three iterations.

### Quality Gate Summary (iter 3, unchanged across iterations)

| Gate | Status |
|------|--------|
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Self-Tests | SKIP |
| Blind Scenarios (executable) | **FAIL** |
| Mutation Testing | SKIP |

### Per-Iteration Gate Events

```json
iter=1: {"exit_code":1,"pass_rate":33,"satisfaction":33,"agent_wall_seconds":356.85,"scenario_total":27,"scenario_crashed":0}
iter=2: {"exit_code":1,"pass_rate":33,"satisfaction":33,"agent_wall_seconds":216.63,"scenario_total":27,"scenario_crashed":0}
iter=3: {"exit_code":1,"pass_rate":33,"satisfaction":33,"agent_wall_seconds":298.18,"scenario_total":27,"scenario_crashed":0}
```

### Trajectory Classification

Iter 2 and iter 3 both classified as:
```
PLATEAU — "Fundamentally different approach needed — same strategy is not working."
```

Iter 1 did not emit a TRAJECTORY_CLASSIFIED event (expected — baseline).

### Cost

```
iter 1: $84.00 (400 turns)
iter 2: $84.00 (400 turns)
iter 3: $84.00 (400 turns)
────────────────────────────
total:  $252.00 (1200 turns, iteration loop only)
```

Plus spec-validator + architect + escalation agents (unmetered in ITER_COST events). The estimate is per-iteration only and likely represents a token cap (~800K input + 400K output × $0.03 + $0.15 = ceiling estimate, not actual).

---

## Key Findings

### 1. 🔴 HARD PLATEAU: Feedback Loop Completely Ineffective

All three iterations produced **exactly 33% pass rate with identical 9/18 passed/failed counts**. The oracle's NO-SHIP feedback and the trajectory classifier's PLATEAU warning did not change the outcome. This is the most significant finding: **the iteration loop provided zero improvement across 3 iterations and ~$252 spent**.

Possible causes:
- The information barrier between implementer and scenarios may be too opaque — the implementer cannot see *which* categories are failing, only that something is wrong
- Feedback is spec-section-anchored (per CLAUDE.md), but the implementer may be rewriting the same code the same way each iteration
- The escalation report itself notes: "92% spec compliance but 33% runtime success" — suggesting a runtime/environment mismatch the implementer cannot diagnose without seeing the test environment

### 2. 🟡 Scenarios Were Reused from Prior Run

No `AGENT_START` event for `scenario-architect` appeared in `events.jsonl` (only spec-validator, architect, implementer, reviewer, oracle, escalation). However, `.dark-factory/scenarios/2026-03-28-api-bootstrap/` contained 5 test files. These were reused from a prior run because I did not explicitly clean `scenarios/` in my Run 1b reset. This did not invalidate the run (spec hash matched) but it means we did not exercise scenario generation in this run — only the iteration loop.

**Observation:** The pipeline silently reused cached scenarios with no event indicating this. A `SCENARIOS_REUSED` event (or equivalent) would help distinguish "fresh generation" from "cache hit" in future reports.

### 3. 🟡 Implementer Runs Twice Per Iteration (Pre-Gate Retry)

Every iteration showed two `role="implementer"` AGENT_START/COMPLETED pairs. Cross-referenced against the stdout:

```
  Implementing...
  Implementation complete
  Running pre-gate (attempt 1/2)...
  Pre-gate failed (attempt 1)
  Running pre-gate (attempt 2/2)...
  Pre-gate passed
```

The second "implementer" event is actually the implementer being re-invoked to fix a pre-gate failure. This is by design, but:

- **The event log labels both runs as `role="implementer"` with `attempt=1`** — there is no way to distinguish "first implementation" from "pre-gate fix retry" in the event stream without cross-referencing stdout
- The pre-gate fails on **attempt 1 of all 3 iterations**, consistently, and passes on attempt 2. This suggests a systematic, predictable first-attempt failure that could be diagnosed and prevented upstream (e.g., a missing setup step the implementer always forgets).

**Recommendation:** Tag pre-gate-retry invocations with `attempt=2` or a distinct role like `implementer-pregate-fix` in events.

### 4. 🟡 Oracle Runs Twice Per Iteration

Same pattern as implementer — two `role="oracle"` AGENT_START/COMPLETED pairs per iteration. Example iter 1:
```
04:52:15 → 04:53:05 (50s) oracle
04:53:05 → 04:53:50 (45s) oracle
```
No intervening event suggests why. This is either a retry for ambiguous output (recall the recent commit `97e1892 Fix oracle decision parser to handle JSON-wrapped and markdown output`) or a similar by-design retry.

**Recommendation:** Same as #3 — distinguish retry attempts in events.

### 5. 🟡 Dual-Process Contamination Easy to Trigger

Accidentally starting two runs back-to-back produced a poisoned state that silently resumed from `iteration=4` on the next invocation and immediately escalated. Possible mitigations:
- Check `.pid` file for liveness at start and refuse to start if another process owns it
- On `--fresh`, proactively delete the pid file and state file
- Reject resume if `iteration >= max_iterations` (abort with clear error instead of immediately escalating)

### 6. 🟢 Infrastructure Worked Cleanly

- Docker images all cached (fast rebuild)
- Container isolation functioned correctly
- Smoke test passed every iteration
- No crashed scenarios (all 18 failures were assertion failures, not infrastructure failures)
- Pre-gate retry mechanism worked (attempt 2 always passed)

### 7. 🟢 Spec Validator Warnings Surfaced

Three ambiguity warnings were logged during spec pre-check:
```
WARNING: ambiguous term "appropriate" on line 223: | EC-9 | CORS request from disallowed origin | ...
WARNING: ambiguous term "etc." on line 235: | ...
WARNING: ambiguous term "etc." on line 425: | FV-5 | AppError inheritance | ...
WARNING: spec pre-check: 3 ambiguity warnings (proceeding)
```
These are the kind of soft signals that can correlate with later trouble. Worth verifying whether the scenarios that failed in categories 02-05 correspond to the ambiguous spec sections (EC-9, EC-10, FV-5) or are unrelated.

---

## Escalation Report Highlights

From `reviews/2026-03-28-api-bootstrap-escalation.md`, the escalation agent identified:

- **Runtime vs static analysis disconnect**: 92% spec validation + 82% implementation review vs 33% scenario pass rate
- Suspected causes:
  - `process.exit(1)` behavior in scenario test environment
  - Logging format mismatch (JSON structure doesn't match spec exactly in runtime)
  - `z.coerce.number()` edge case handling ("abc" → NaN)
  - Fastify request lifecycle hook timing
  - Global error handler propagation

These are precisely the kinds of issues the implementer cannot diagnose without access to scenario test output — which is exactly what the information barrier forbids.

---

## Metrics Summary

| Metric | Value |
|---|---|
| Total wall time | ~33 min |
| Iterations | 3/3 (escalated) |
| Pass rate (all iters) | 33% (9/27) |
| Scenarios crashed | 0 |
| Pre-gate failures | 3/3 iterations (attempt 1), 0/3 (attempt 2) |
| Smoke tests | 3/3 passed |
| Oracle retries | 3/3 iterations double-invoked |
| Implementer retries | 3/3 iterations double-invoked |
| Estimated cost | $252 (iter loop) + unmetered pre/post |
| Trajectory | PLATEAU (iter 2, 3) |
| Final decision | ESCALATED |

---

## Recommendations for Pipeline Improvement

1. **Address the plateau**: When 3 iterations show identical pass rates, the oracle feedback mechanism is demonstrably not helping. Consider surfacing *category-level failure counts* (not specific assertion text) to the implementer on PLATEAU trajectory, as a last-resort signal before escalation.
2. **Differentiate retry attempts in events**: Pre-gate-fix implementer runs and oracle-parser-retry runs should have distinct role labels or `attempt` fields so event analysis is unambiguous.
3. **Emit `SCENARIOS_REUSED` event** when scenarios are loaded from cache rather than generated.
4. **Add resume guardrails**: Refuse resume if `iteration >= max_iterations` or if a conflicting pid is alive. Warn loudly on stale state detection.
5. **Investigate pre-gate attempt-1 failure**: It is 100% reproducible across 3 iterations — likely fixable upstream (missing dependency install? missing build step? lockfile mismatch?).
6. **Correlate ambiguous spec warnings with failing categories** to validate whether ambiguity predicts failure.
