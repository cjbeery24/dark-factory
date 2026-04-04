# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-04 20:56
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Run ID:** 823267f89a9343b4
**Final Outcome:** ESCALATED (after 2 iterations, stable 80% pass rate)

---

## Pipeline Timeline

| Time (UTC) | Event | Wall Time |
|------------|-------|-----------|
| 20:57:04 | SPEC_START | — |
| 20:57:04 | spec-validator START | — |
| 20:59:07 | spec-validator COMPLETED | 123s |
| 20:59:07 | architect START | — |
| 21:01:32 | architect COMPLETED | 145s |
| 21:01:32 | scenario-architect START | — |
| 21:03:55 | scenario-architect COMPLETED | 143s |
| 21:03:56 | implementer iter1 START | — |
| 21:08:34 | implementer iter1 COMPLETED | 277s |
| 21:08:38 | implementer (pre-gate) START | — |
| 21:09:43 | implementer (pre-gate) COMPLETED | 65s |
| 21:09:50 | SMOKE_COMPLETED (passed) | — |
| 21:10:05 | GATE_COMPLETED (80% pass rate) | — |
| 21:10:05 | reviewer iter1 START | — |
| 21:11:29 | reviewer iter1 COMPLETED | 84s |
| 21:11:29 | oracle iter1 START | — |
| 21:12:27 | oracle iter1 COMPLETED (NO-SHIP) | 59s |
| 21:12:27 | oracle (feedback) START | — |
| 21:13:13 | oracle (feedback) COMPLETED | 46s |
| 21:13:13 | implementer iter2 START | — |
| 21:15:30 | implementer iter2 COMPLETED | 137s |
| 21:15:34 | implementer (pre-gate) START | — |
| 21:16:45 | implementer (pre-gate) COMPLETED | 71s |
| 21:16:52 | SMOKE_COMPLETED (passed) | — |
| 21:17:07 | GATE_COMPLETED (80% pass rate) | — |
| 21:17:07 | reviewer iter2 START | — |
| 21:19:45 | reviewer iter2 COMPLETED | 159s |
| 21:19:46 | oracle iter2 START | — |
| 21:21:10 | oracle iter2 COMPLETED (NO-SHIP) | 85s |
| 21:21:11 | oracle (feedback) START | — |
| 21:22:27 | oracle (feedback) COMPLETED | 76s |
| 21:22:27 | escalation START | — |
| 21:23:38 | SPEC_ESCALATED | 71s |

**Total wall clock:** ~26.5 minutes (20:57:04 → 21:23:38)
**Total agent wall time:** 1,540s (25.7 min)

---

## Pre-Run State & Setup

- **Repo state:** Modified tracked files from prior runs (apps/api/package.json, index.ts, tsconfig, eslint.config, pnpm-lock.yaml) + untracked .dark-factory artifacts and impl files
- **Actions taken:** `git checkout -- .`, `git clean -fd`, cleared .dark-factory runtime dirs, cleared `__pycache__`, synced agents/templates, `pip install -e`, `dark-factory build node`, cleaned orphaned Docker containers (4 removed)
- **Spec pre-check:** 3 ambiguity warnings (terms: "appropriate", "etc." x2) — proceeding

---

## Iteration 1

### Implementation (277s)
First implementation pass. The implementer created a Fastify-based API server with:
- Environment validation via Zod
- Structured logging with Pino
- Global error handling (AppError class)
- Health check endpoint with registry pattern
- CORS and rate limiting plugins

### Quality Gate Results
```
| Gate          | Status |
|---------------|--------|
| Build         | PASS   |
| Lint          | PASS   |
| Type Check    | PASS   |
| Self-Tests    | SKIP   |
| Mutation      | SKIP   |
```

### Blind Scenario Results: 16/20 (80%)

**4 Failures:**

| Scenario | Failure Detail |
|----------|---------------|
| malformed JSON request triggers error handling | expected status 400, got 500 |
| CORS preflight from allowed origin | expected status 204, got 400 |
| health endpoint with query parameters | expected status 200, got 404 |
| health endpoint with trailing slash | expected status 200, got 404 |

### Category Breakdown
```
CAT_01: 4/4 (100%)  — Health endpoint core
CAT_02: 3/4 (75%)   — Error handling
CAT_03: 4/5 (80%)   — CORS & cross-cutting
CAT_04: 5/7 (71%)   — Edge cases
```

### Review Score: 87/100
```
Test Results:     15/25
Code Quality:     20/20
Security:         15/15
Spec Compliance:  28/30
Robustness:        9/10
```

### Oracle Decision: **NO-SHIP**
Feedback highlighted 3 issues: rate limit error envelope format, non-Error exception handling, and edge case gaps.

---

## Iteration 2

### Implementation (137s)
Shorter iteration — implementer attempted to fix the 4 failing scenarios based on barrier-safe feedback.

### Quality Gate Results
Identical to iteration 1: Build/Lint/TypeCheck PASS, Self-Tests/Mutation SKIP.

### Blind Scenario Results: 16/20 (80%) — NO CHANGE

**Same 4 failures, same error details:**
```
malformed JSON request triggers error handling  → expected status 400, got 500
CORS preflight from allowed origin              → expected status 204, got 400
health endpoint with query parameters           → expected status 200, got 404
health endpoint with trailing slash             → expected status 200, got 404
```

### Review Score: 87/100 (same)

### Oracle Decision: **NO-SHIP**
Classified as: 1 SPEC_AMBIGUOUS + 2 BRITTLE failures. Recommended escalation.

### Iter2 Feedback Classification of Failures

| Spec Section | Classification | Analysis |
|-------------|---------------|----------|
| FR-4 Health query params | BRITTLE | Spec doesn't constrain behavior with query parameters |
| FR-4 Health trailing slash | BRITTLE | Spec doesn't specify trailing slash normalization |
| FR-2 Malformed JSON | BRITTLE | Error format expectations unclear in spec |
| FR-1 CORS preflight | IMPL_BUG | Medium priority — actual implementation issue |

---

## Escalation

Pipeline escalated after 2 iterations with stable 80% pass rate. The escalation agent generated a detailed report classifying root causes as specification ambiguity and test brittleness rather than fundamental implementation problems.

---

## Key Observations

### Positive
1. **Pipeline ran clean end-to-end** — no crashes, no Docker failures, no infrastructure issues
2. **Smoke test passed both iterations** — server starts and responds correctly
3. **Hard quality gates passed** — Build, Lint, TypeCheck all clean
4. **High implementation quality** — 87/100 review score, excellent code quality and security
5. **Timely escalation** — pipeline correctly identified stable failures and escalated rather than burning a 3rd iteration
6. **Ship-with-gaps detection worked** — pipeline identified 4 stable failures and attempted ship-with-gaps path
7. **Pre-gate retry worked** — pre-gate failed attempt 1, succeeded attempt 2 in both iterations

### Issues / Regressions
1. **Stable failure pattern not resolved** — The implementer could not fix the 4 failing scenarios across 2 iterations. The barrier-safe feedback may not have been specific enough to guide the fix (feedback referenced spec sections but couldn't describe the actual HTTP status code mismatches).
2. **Oracle ran twice per iteration** — First for the decision, then for feedback generation. This is by design but adds ~100s per iteration.
3. **Ship-with-gaps was eligible but Oracle still said NO-SHIP** — Pipeline output shows "Ship-with-gaps eligible: 4 stable failure(s), pass rate 80%" but Oracle still decided NO-SHIP. The escalation report then classified 3/4 failures as BRITTLE and recommended shipping. This inconsistency suggests the Oracle's ship-with-gaps threshold or decision criteria may be too conservative.
4. **CORS preflight failure is a real implementation bug** — The scenario expected status 204 for OPTIONS preflight, got 400. This is a legitimate CORS issue that the implementer failed to fix.
5. **Trailing slash and query parameter failures are spec ambiguities** — The spec defines `GET /health` but doesn't say what happens with `/health/` or `/health?foo=bar`. The scenario architect over-specified these edge cases.
6. **Malformed JSON: 400 vs 500** — The scenario expected 400 for malformed JSON but got 500. The spec says malformed JSON should return 400 (EC-5), so this is an implementation bug the feedback didn't successfully guide the fix for.

### Metrics

| Metric | Value |
|--------|-------|
| Total pipeline duration | 26.5 min |
| Iterations completed | 2 |
| Final pass rate | 80% (stable) |
| Final review score | 87/100 |
| Spec pre-check warnings | 3 |
| Docker build | Cached (fast) |
| Server startup failures | 0 |
| Scenarios total | 20 |
| Scenarios passed | 16 |
| Scenarios failed (stable) | 4 |
| Agent invocations | 14 |
| Total agent wall time | 25.7 min |

### Failure Root Cause Breakdown

| Failure | Root Cause | Actionable? |
|---------|-----------|-------------|
| CORS preflight → 400 instead of 204 | Impl bug: Fastify CORS plugin not handling OPTIONS correctly | Yes — implementer needs more specific guidance |
| Malformed JSON → 500 instead of 400 | Impl bug: error handler not catching parse errors as 400 | Yes — spec clearly says 400 for EC-5 |
| Health with query params → 404 | Spec ambiguity: spec doesn't define query param behavior | Spec improvement needed |
| Health with trailing slash → 404 | Spec ambiguity: spec doesn't define trailing slash behavior | Spec improvement needed |

### Recommendations for Pipeline Improvement
1. **Scenario architect should not test behaviors the spec doesn't constrain** — the trailing slash and query parameter scenarios are testing implicit behavior not in the spec
2. **Oracle ship-with-gaps threshold may be too conservative** — 80% with documented BRITTLE classifications should probably ship
3. **Feedback specificity** — the barrier prevents sharing exact HTTP status mismatches, which makes it hard for the implementer to understand what's wrong. Consider whether the barrier-safe feedback format needs enrichment for HTTP scenario failures (e.g., "the expected HTTP status for CORS preflight requests is not being returned" rather than just "FR-1 CORS configuration has issues")
4. **Pre-gate failure on first attempt** both iterations suggests a timing or build cache issue worth investigating
