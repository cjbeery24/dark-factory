# Pipeline Run Report: budget-brain-v2 / api-bootstrap

**Date:** 2026-04-04 02:23 UTC
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Run ID:** 7405926ed99b4fb3
**Dark Factory Version:** latest (main branch)
**Model:** claude-sonnet-4-20250514

## Pre-Run State

- Previous run existed in `interrupted` state (phase=review, iteration=1, gate_exit_code=1, smoke_result=passed)
- Modified tracked files: `apps/api/package.json`, `apps/api/src/index.ts`, `apps/api/tsconfig.tsbuildinfo`, `package.json`, `pnpm-lock.yaml`
- Untracked files from previous run: `.dark-factory/architecture/`, `.dark-factory/reviews/`, `apps/api/src/common/`, `apps/api/src/server.ts`
- **Actions taken:** git checkout, git clean, removed runtime artifacts (state/reports/scenarios/reviews/architecture/audit/failures/events.jsonl)

## Preparation

- [x] Restored tracked files and cleaned untracked (preserving specs, plans, env, node_modules)
- [x] Removed .dark-factory runtime artifacts
- [x] Copied latest agents/ and templates/ to budget-brain-v2
- [x] Rebuilt all docker images (proxy, base, node, python, playwright, playwright-node)

---

## Run 1: ESCALATED (3 iterations, all 79%)

### Outcome
- **Result:** ESCALATED after 3 iterations
- **Total Wall Time:** ~47 minutes (02:24:37 → 03:11:37 UTC)
- **Total Estimated Cost:** $252 ($126 iter1 + $84 iter2 + $42 iter3)
- **Pass Rate:** 79% → 79% → 79% (PLATEAU)
- **Scenarios:** 27/34 passed, 7/34 failed, 0 crashed (consistent across all iterations)

### Timeline

| Time (UTC) | Event | Duration | Notes |
|-----------|-------|----------|-------|
| 02:24:37 | SPEC_START | — | |
| 02:24:37 | spec-validator start | 82s | |
| 02:26:00 | architect start | 222s | |
| 02:29:42 | scenario-architect start | 113s | Generated 34 scenarios across 5 files |
| 02:31:37 | **Iter 1: implementer start** | 183s | First pass |
| 02:34:41 | implementer (2nd pass) | 203s | Pre-gate failed, retry |
| 02:39:26 | implementer (3rd pass) | 147s | Fixed Fastify version mismatch |
| 02:41:56 | SMOKE_COMPLETED | — | Passed on attempt 2 |
| 02:42:02 | GATE_COMPLETED | — | 79% pass rate, exit_code=1 |
| 02:42:02 | reviewer start | 124s | |
| 02:44:06 | oracle start (x2) | 55s + 48s | Oracle ran twice; leakage patterns detected |
| 02:45:48 | **Iter 2: implementer start** | 691s | Long run (11.5 min) |
| 02:57:25 | implementer (2nd pass) | 357s | Pre-gate failed both attempts |
| 03:03:30 | SMOKE_COMPLETED | — | Passed on attempt 1 |
| 03:03:35 | GATE_COMPLETED | — | 79% pass rate, PLATEAU detected |
| 03:03:35 | reviewer start | 230s | |
| 03:07:26 | oracle start (x2) | 58s + 59s | Oracle ran twice again |
| 03:09:23 | **Iter 3: implementer start** | 66s | Very short run |
| 03:10:38 | SMOKE_COMPLETED | — | Passed on attempt 1 |
| 03:10:43 | GATE_COMPLETED | — | 79% pass rate, PLATEAU |
| 03:10:43 | escalation start | 54s | |
| 03:11:37 | SPEC_ESCALATED | — | |

### Agent Turn Counts & Anomalies

- Total agent turns: 1200 (anomaly: max recommended is 800)
- Iteration 1: 600 turns (3 implementer passes)
- Iteration 2: 400 turns (2 implementer passes)
- Iteration 3: 200 turns (1 implementer pass — very short, 66s)

### Scenario Results (Identical Across All 3 Iterations)

**Same 7 failures every iteration:**

| Scenario | Detail | Category |
|----------|--------|----------|
| unknown error returns 500 with generic message | expected status 405, got 400 | error-conditions |
| zod validation error returns 400 with field details | expected status 405, got 400 | error-conditions |
| multiple health checks execute concurrently | body missing field 'data.checks' | edge-cases |
| zod error with multiple fields returns all in details array | expected status 405, got 400 | edge-cases |
| password field redacted in request body logs | expected status 405, got 400 | security |
| email field redacted in request body logs | expected status 405, got 400 | security |
| rate limited request returns 429 with structured error | expected status 404, got 400 | rate-limiting |

**Category Breakdown:**
| Category | Passed | Total | Rate |
|----------|--------|-------|------|
| 01-contract | 8 | 8 | 100% |
| 02-error-conditions | 4 | 6 | 67% |
| 03-edge-cases | 6 | 8 | 75% |
| 04-security | 6 | 8 | 75% |
| 05-rate-limiting | 3 | 4 | 75% |

### Failure Analysis

**Pattern: 5 of 7 failures are `expected status 405, got 400`**

These scenarios appear to use a POST/PUT/PATCH to a route that should return 405 (Method Not Allowed), but the server is returning 400 instead. This suggests either:
1. **Scenario design issue:** The scenarios may be sending requests with invalid bodies that trigger Fastify's body parsing (400) before method validation (405).
2. **Implementation issue:** The server may not have proper method-not-allowed handling and instead falls through to body validation.

**Pattern: 1 failure is `body missing field 'data.checks'`**

The "multiple health checks execute concurrently" scenario expects `data.checks` in the response body, but the health endpoint doesn't return individual check results. This could be a spec ambiguity about health check response format.

**Pattern: 1 failure is `expected status 404, got 400`**

The rate limiting scenario expects 404 but gets 400. Similar issue — body parsing may be interfering with routing.

### Key Observations

1. **PLATEAU at 79% — The exact same 7 scenarios fail every iteration.** The implementer cannot fix these because the failures appear to be caused by scenario assumptions about HTTP method handling (405 vs 400) that don't match the implementation's behavior. The feedback loop doesn't help the implementer fix this because the barrier prevents them from seeing the specific scenario expectations.

2. **Implementer ran 3 times in iteration 1** due to:
   - First pass: built the app but installed `@fastify/cors` v8 (compatible with Fastify 4.x, not 5.x)
   - Second pass: pre-gate failed (likely same issue)
   - Third pass: fixed to `@fastify/cors` v10.x for Fastify 5.x
   - Server then started successfully

3. **Oracle ran twice per iteration** — the first oracle output was rejected due to barrier leakage patterns (including `79% pass`, `21% fail`, `expect.`). The retry produced clean feedback. This is the barrier enforcement working correctly but costing extra turns.

4. **Pre-gate failures in iteration 2** — lint violation (`non-null assertion in logging.test.ts:59:27`) that exhausted both pre-gate attempts. The implementer then had to do a full gate run with the lint issue, which still passed the gate's lint check (suggesting the pre-gate and gate check different things, or the gate report is from iteration 3).

5. **Iteration 3 was extremely short (66s)** — the implementer had already exhausted its useful context from iterations 1-2 and didn't make meaningful changes.

6. **Satisfaction score: 0%** across all iterations — this metric seems to be unrelated to the 79% pass rate.

### Server Container Log (Iteration 1 Failure)

```
FastifyError: fastify-plugin: @fastify/cors - expected '4.x' fastify version, '5.8.4' is installed
    at Object.checkVersion (/project/node_modules/.pnpm/fastify@5.8.4/node_modules/fastify/lib/plugin-utils.js:127:11)
    code: 'FST_ERR_PLUGIN_VERSION_MISMATCH',
    statusCode: 500
```

### Quality Gate Report (Final — Iteration 3)

```
| Gate        | Status | Details               |
|-------------|--------|-----------------------|
| Build       | PASS   | Exit code 0           |
| Lint        | PASS   | Exit code 0           |
| Type Check  | PASS   | Exit code 0           |
| Self-Tests  | SKIP   | No command configured  |
| Mutation    | SKIP   | MUTATION_CMD not set   |
```

### State File (Final)

```
spec=2026-03-28-api-bootstrap
run_id=7405926ed99b4fb3
status=interrupted
iteration=4
completed_step=2
last_decision=NO-SHIP
smoke_result=passed
phase=review
pass_rate=79
gate_exit_code=1
server_error=
```

### Pipeline Output Summary

```
Iteration 1/3:
  Pre-gate: failed (attempt 1), passed (attempt 2)
  Smoke check: failed (attempt 1), passed (attempt 2)
  Quality gate: 79% pass rate → NO-SHIP

Iteration 2/3:
  Pre-gate: failed (attempt 1), failed (attempt 2) — lint violation
  Smoke check: passed (attempt 1)
  Quality gate: 79% pass rate → NO-SHIP

Iteration 3/3:
  Pre-gate: passed (attempt 1)
  Smoke check: passed (attempt 1)
  Quality gate: 79% pass rate → ESCALATED
```

---

## Pipeline Improvement Observations

### Issues to Investigate

1. **405 vs 400 scenario failures (5/7 failures):** The scenarios expect 405 Method Not Allowed for endpoints that don't support certain HTTP methods. The server returns 400 instead, likely because Fastify's body parser runs before method validation. This is a **scenario design problem** — the scenario architect should either:
   - Not assume 405 behavior when testing request body logging (POST requests to test body field redaction could use a valid endpoint instead)
   - Or the spec should explicitly define method-not-allowed behavior

2. **Oracle leakage detection causing double runs:** The oracle's feedback is being rejected for including pass rate percentages and assertion patterns, requiring a retry. This is working as designed but doubles oracle cost.

3. **`data.checks` health check format (1 failure):** The scenario expects individual health check results in the response but the implementation only returns aggregate status. The spec may need to be more explicit about the health endpoint response format.

4. **Satisfaction score stuck at 0%:** Despite 79% scenario pass rate, satisfaction is 0%. The satisfaction metric logic should be reviewed — it may be treating any gate failure as 0% satisfaction.

5. **Iteration 3 was only 66 seconds** — the implementer had no new information to act on since the same 7 scenarios failed identically. The PLATEAU detection correctly identified this but could have escalated earlier (after iteration 2).

6. **Turn budget anomaly (1200 > 800 max):** The multiple implementer passes per iteration consume extra turns. The pre-gate retry loop (implementer → pre-gate → fail → implementer) amplifies this.

### Metrics for Future Comparison

| Metric | Value |
|--------|-------|
| Total wall time | 47 min |
| Estimated cost | $252 |
| Iterations | 3 (max) |
| Final pass rate | 79% |
| Scenario total | 34 |
| Scenarios passing | 27 |
| Scenarios failing | 7 |
| Scenarios crashed | 0 |
| Agent turns total | 1200 |
| Pre-gate failures | 3 (across all iterations) |
| Smoke failures | 1 (iteration 1 only) |
| Oracle retries | 2 (barrier leakage) |
| Trajectory | PLATEAU |
| Outcome | ESCALATED |
