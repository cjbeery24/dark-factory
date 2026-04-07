# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-07 02:08
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Dark Factory commit:** 2d76602 (Phase 2.6b)
**Run ID:** 5c4384d332334ce9
**Outcome:** Killed after iteration 2 oracle decision (PLATEAU)

---

## Pre-Run State

- Repo had modified tracked files and untracked .dark-factory artifacts from a prior pipeline run
- Prior run appears to have completed at least one iteration (oracle output iter1 exists)
- Modified files: `apps/api/package.json`, `apps/api/src/index.ts`, `pnpm-lock.yaml`, tsconfig build info files
- Untracked: `apps/api/src/common/`, `apps/api/src/server.ts`, architecture/precheck artifacts

## Reset Actions

- Restored tracked files via `git checkout .`
- Removed untracked files via `git clean -fd`
- Removed `.dark-factory/` runtime artifacts (architecture, precheck, reports, scenarios, state, audit, events, metrics, failures, shipped-scenarios)
- Cleared Python bytecode caches
- Reinstalled dark-factory (editable)
- Rebuilt Docker images (all cached, no rebuild needed)
- Cleaned orphaned Docker containers/networks

---

## Run 1

### Configuration

- Model: claude-sonnet-4-20250514
- Max turns per agent: 200
- Autonomy level: 3 (Autonomous)
- Run ID: 5c4384d332334ce9

### Pipeline Progress — Agent Timeline

| Phase | Agent | Start (UTC) | Wall Seconds | Notes |
|-------|-------|-------------|-------------|-------|
| Spec Validation | spec-validator | 02:09:30 | 178s | PASS (92/100, LOW divergence) |
| Architecture | architect | 02:12:29 | 167s | Produced manifest with 8 files |
| Scenario Generation | scenario-architect (generate) | 02:15:16 | 222s | 5 scenario files |
| Scenario Validation | scenario-architect (validate) | 02:18:58 | 279s | COVERAGE_GAP: 14 uncovered anchors |
| Coverage Regen | scenario-architect (coverage_regen) | 02:23:38 | 223s | Added 4 more files, 23 anchors total |
| Scenario Repair | scenario-architect (repair) | 02:27:32 | 112s | Final: 9 scenario files |
| **Iter 1: Implement** | implementer | 02:29:24 | 270s | Code written |
| Iter 1: Smoke Test | (built-in) | 02:34:03 | ~9s | PASS |
| Iter 1: Quality Gate | (built-in) | 02:34:03 | ~24s | FAIL (50% pass rate) |
| Iter 1: Review | reviewer | 02:34:27 | 83s | Score: 62/100 |
| Iter 1: Oracle (decision) | oracle | 02:35:50 | 48s | NO-SHIP |
| Iter 1: Oracle (feedback) | oracle | 02:36:39 | 49s | Wrote feedback-iter1.md |
| **Iter 2: Implement** | implementer | 02:37:28 | 191s | Attempted fixes |
| Iter 2: Smoke Test | (built-in) | 02:40:47 | ~8s | PASS |
| Iter 2: Quality Gate | (built-in) | 02:40:47 | ~25s | FAIL (50% pass rate) |
| Iter 2: Trajectory | (built-in) | 02:41:12 | — | PLATEAU classified |
| Iter 2: Review | reviewer | 02:41:12 | (killed) | Pipeline killed during review |

**Total wall time (start to kill):** ~32 minutes
**Estimated cost:** $84.00 ($42/iteration x 2 iterations)

### Final State File

```
spec=2026-03-28-api-bootstrap
run_id=5c4384d332334ce9
status=interrupted
iteration=2
completed_step=2
last_decision=NO-SHIP
smoke_result=passed
pass_rate=50
gate_exit_code=1
server_error=
```

### Coverage Gap Details

Initial scenario generation produced 5 files covering only a subset of requirements. 14 anchors were uncovered:
- FR-2 (structured logging)
- AC-1, AC-2, AC-4, AC-5, AC-6, AC-7, AC-10 (acceptance criteria)
- EC-1, EC-2, EC-3, EC-5, EC-6, EC-7 (edge cases)

Coverage regen successfully filled the gaps, bringing total to 23 anchors across 9 scenario files. This is a notable improvement in the coverage system working as intended.

### Quality Gate Report (Iteration 1)

| Gate | Status | Details |
|------|--------|---------|
| Build | PASS | Exit code 0 |
| Lint | **FAIL** | 4 errors (see below) |
| Type Check | PASS | Exit code 0 |
| Self-Tests | SKIP | No command configured |
| Blind Scenarios (executable) | **FAIL** | 3 passed, 8 failed out of 11 |
| Blind Scenarios (HTTP) | **FAIL** | 17 passed, 12 failed out of 29 |
| Mutation Testing | SKIP | Not configured |

**Combined: 20 passed / 40 total = 50%, Satisfaction: 50%**

#### Lint Failures

```
apps/api/src/common/error-handler.ts:7:3   'options' is defined but never used
apps/api/src/common/error-handler.ts:15:28  Unexpected any. Specify a different type
apps/api/src/common/health.ts:24:3          'options' is defined but never used
apps/api/src/server.ts:32:14                'crypto' is not defined (no-undef)
```

#### Executable Scenario Failures (iter1 & iter2 — identical)

| Test | Status | Root Cause |
|------|--------|------------|
| each request gets a unique requestId | FAIL | Empty set — requestId not being extracted from logs |
| authorization header value is redacted in log output | FAIL | curl-based test returning null status (test design issue?) |
| deeply nested sensitive fields are not redacted | FAIL | curl-based test returning null status |
| server starts with valid env vars and logs startup message | FAIL | Startup message format deviation |
| server exits with code 1 when PORT is not a valid number | FAIL | Env validation behavior mismatch |
| server exits with code 1 when NODE_ENV is unsupported | FAIL | Env validation behavior mismatch |
| server logs all invalid env vars when multiple are wrong | FAIL | Env validation behavior mismatch |
| server uses default values when env vars are not set | FAIL | Default value handling mismatch |

#### HTTP Scenario Failures (iter1)

| Category | Passed | Total | Notes |
|----------|--------|-------|-------|
| CAT_01 (health endpoint) | 4 | 4 | Perfect |
| CAT_02 (error conditions) | 5 | 5 | Perfect |
| CAT_03 (rate limiting) | 0 | 2 | Fully failing |
| CAT_04 (CORS security) | 2 | 4 | Partial — preflight failing |
| CAT_05 (envelope formats) | 6 | 6 | Perfect |
| CAT_06 (error handling types) | 0 | 4 | Fully failing |
| CAT_07 (health check errors) | 0 | 4 | Fully failing |
| CAT_08 (environment validation) | 0 | 5 | Fully failing (weight: 60) |
| CAT_09 (structured logging) | 0 | 3 | Fully failing (weight: 100) |

#### HTTP Scenario Runner Log

```
WARNING: unrecognized assertion (skipped): '- Body field "data.checks" contains field "server"'
WARNING: unrecognized assertion (skipped): '- Body field "data.checks" contains field "database"'
```

Two assertions in the health check scenarios used an unrecognized format and were skipped rather than evaluated.

### Quality Gate Report (Iteration 2)

Same results as iteration 1: **50% pass rate, 50% satisfaction**. No improvement.

Trajectory classification: **PLATEAU** — "Fundamentally different approach needed — same strategy is not working."

### Reviewer Assessment (Iteration 1)

**Overall Score: 62/100**

| Category | Score | Weight |
|----------|-------|--------|
| Test Results | 10/25 | 25% |
| Code Quality | 15/20 | 20% |
| Security | 15/15 | 15% |
| Spec Compliance | 22/30 | 30% |
| Robustness | 8/10 | 10% |

Key issues identified by reviewer:
1. Missing `crypto` import in `server.ts:32` — `crypto.randomUUID()` without importing `node:crypto`
2. Duplicate logger configuration — `common/logger.ts` creates standalone pino logger but server configures its own
3. Startup log format deviation — extra `time` field not in spec

### Oracle Decision (Iteration 1)

**NO-SHIP**. Key blocking issues:
1. Critical runtime error — missing crypto import
2. Quality gate failures — lint and blind scenarios at 50%
3. Low-confidence anchor failures across FR-1 through FR-4

Feedback classified all failures as **IMPL_BUG**.

---

## Observations & Analysis

### What Worked Well

1. **Spec validation** — passed cleanly at 92/100 with LOW divergence, 0 blockers
2. **Architecture manifest** — well-structured, proper file map with 8 files, data models, and API contracts
3. **Coverage gap detection and regen** — correctly identified 14 uncovered anchors and regenerated to fill them
4. **Smoke test** — server starts successfully on both iterations
5. **Oracle feedback quality** — clear, actionable, correctly classified all issues as IMPL_BUG
6. **Categories 01/02/05** — health endpoint, basic error conditions, and envelope formats all pass perfectly

### Issues Requiring Investigation

1. **PLATEAU at 50%** — Iteration 2 made zero progress on the same 20 failing scenarios. The implementer either:
   - Did not receive the feedback effectively
   - Made changes that didn't address the actual test assertions
   - The executable tests (08/09) may have design issues that make them unfixable (e.g., curl-based tests returning null status codes inside Docker containers)

2. **Executable scenario quality** — Tests 08 (environment validation) and 09 (structured logging) appear to have design problems:
   - Tests use `execSync`/`spawnSync` to start the server and check behavior, but may not work correctly in the Docker container environment
   - The logging tests use `curl` commands that return null, suggesting the server isn't reachable from within the test process
   - These scenarios were generated by coverage_regen (not initial generation), suggesting the regen produces lower-quality tests

3. **HTTP scenario runner skipping assertions** — Two health check assertions used an unrecognized format (`Body field "data.checks" contains field "server"`). This means the scenario validation step didn't catch an incompatible assertion format.

4. **Oracle runs twice** — The oracle agent ran twice per iteration (once for the decision, once for writing feedback). This appears intentional but doubles oracle cost.

5. **Lint failures persisting** — The `crypto` import issue is trivial but the implementer failed to fix it in iteration 2, suggesting the feedback didn't surface the specific line number or the implementer didn't run lint after making changes.

6. **All agents use max turns (200)** — Every agent event shows `turns: 200`, which appears to be a static estimate rather than actual turn count. This may be a reporting issue — the actual token usage isn't visible in events.

### Metrics Summary

| Metric | Value |
|--------|-------|
| Total wall time | ~32 min |
| Iterations completed | 2 (of 3 max) |
| Spec validation score | 92/100 |
| Coverage anchors | 23 |
| Iter 1 pass rate | 50% |
| Iter 2 pass rate | 50% (no change) |
| Trajectory | PLATEAU |
| Review score | 62/100 |
| Estimated cost | $84.00 |
| Scenario files | 9 (5 initial + 4 regen) |
| Total scenarios | 40 (11 executable + 29 HTTP) |

### Recommendations for Pipeline Improvement

1. **Investigate executable scenario quality from coverage_regen** — The regen-produced tests (08, 09) all fail. They may be using patterns that don't work in the Docker test environment (e.g., spawning subprocesses, using curl).

2. **Consider pre-validating scenario executability** — Run a dry-run/compile check on executable scenarios before handing off to the implementer. This would catch tests that can never pass due to environment constraints.

3. **Surface lint errors in feedback** — The oracle feedback mentioned "missing crypto import" but didn't give the exact fix. Adding lint output to the feedback could help the implementer fix trivial issues.

4. **Investigate why the implementer didn't fix lint issues in iter2** — The PLATEAU suggests the implementer may not be reading the feedback effectively or may be making unrelated changes.

5. **Clarify `turns` reporting** — All agent events show `turns: 200` which is the max, not actual. Actual turn count would be useful for understanding agent efficiency.

6. **HTTP scenario runner assertion format** — The `Body field "X" contains field "Y"` assertion format was unrecognized and silently skipped. Either the scenario architect should not generate this format, or the runner should support it.
