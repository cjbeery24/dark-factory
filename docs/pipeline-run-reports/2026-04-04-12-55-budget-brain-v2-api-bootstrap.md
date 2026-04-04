# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-04 12:55
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Run ID:** 809fd92cf73c4be4
**Model:** claude-sonnet-4-20250514 (all agents)
**Outcome:** ESCALATED after 3 iterations (all at 84% pass rate)
**Total wall time:** ~41 minutes (12:56:55 → 13:37:21)

---

## Pre-Run Setup

- Restored tracked files (`git checkout -- .`), deleted untracked files (`git clean -fd`)
- Removed `.dark-factory/` runtime artifacts (scenarios, reviews, reports, audit, state, architecture, events.jsonl, failures)
- Updated agents and templates from dark-factory source
- Rebuilt Docker images (`dark-factory build node`) — completed successfully

---

## Run 1 (single run, 3 iterations)

### Phase Progression Timeline

| Phase | Start (UTC) | Wall (s) | Exit | Notes |
|-------|------------|----------|------|-------|
| Spec Validator | 12:56:55 | 97.9 | 0 | 3 ambiguity warnings (proceeded) |
| Architect | 12:58:33 | 175.3 | 0 | |
| Scenario Architect | 13:01:29 | 182.2 | 0 | 25 HTTP scenarios from 33 intents |
| **Iter 1: Implementer** | 13:04:33 | 292.8 + 61.2 | 0 | Ran twice (smoke fail on 1st attempt?) |
| Iter 1: Smoke | 13:10:38 | — | pass | attempt=1 |
| Iter 1: Gate | 13:10:42 | — | 1 | **84% (21/25)**, 0 crashed |
| Iter 1: Reviewer | 13:10:42 | 99.8 | 0 | |
| Iter 1: Oracle | 13:12:22 | 81.1 + 53.2 | 0 | Ran twice — **NO-SHIP** |
| **Iter 2: Implementer** | 13:14:37 | 285.6 + 202.2 | 0 | Ran twice again |
| Iter 2: Smoke | 13:22:57 | — | pass | attempt=1 |
| Iter 2: Gate | 13:23:01 | — | 1 | **84% (21/25)**, trajectory=PLATEAU |
| Iter 2: Reviewer | 13:23:01 | 213.5 | 0 | |
| Iter 2: Oracle | 13:26:35 | 72.2 | 0 | **NO-SHIP** |
| **Iter 3: Implementer** | 13:27:47 | 247.1 + 77.2 | 0 | Ran twice again |
| Iter 3: Smoke | 13:33:24 | — | pass | attempt=1 |
| Iter 3: Gate | 13:33:28 | — | 1 | **84% (21/25)**, trajectory=PLATEAU |
| Iter 3: Reviewer | 13:33:28 | 122.6 | 0 | |
| Iter 3: Oracle | 13:35:31 | 37.0 | 0 | **NO-SHIP** |
| Escalation | 13:36:08 | 72.9 | 0 | SPEC_ESCALATED after 3 iterations |

### Cost Summary

| Iteration | Estimated USD |
|-----------|--------------|
| Iter 1 | $84.00 |
| Iter 2 | $84.00 |
| Iter 3 | $84.00 |
| **Total** | **$252.00** (estimated) |

### Anomaly Detected

```
{"anomaly_type":"spec_turns","score":0,"detail":"turns=1200 > max=800"}
```

Total turns (1200) exceeded maximum expected (800), triggered after iteration 3.

### Quality Gate Results (Final Iteration)

| Gate | Status |
|------|--------|
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Self-Tests | SKIP (not configured) |
| Mutation Testing | SKIP (not configured) |
| **Blind Scenarios** | **FAIL (84%)** |

### Failing Scenarios (Identical Across All 3 Iterations)

From `http-scenario-runner.log`:

```
DF-SCENARIO: malformed JSON request returns validation error FAILED
  Detail: Step 1: expected status 400, got 500

DF-SCENARIO: request with invalid content type header FAILED
  Detail: Step 1: expected status 400, got 404

DF-SCENARIO: empty POST request body FAILED
  Detail: Step 1: expected status 400, got 500

DF-SCENARIO: health endpoint ignores query parameters FAILED
  Detail: Step 1: expected status 200, got 404
```

**Category breakdown** (from `category-breakdown.env`):
```
CAT_01_PASSED=5  / CAT_01_TOTAL=5   (100%)
CAT_02_PASSED=3  / CAT_02_TOTAL=6   (50%)
CAT_03_PASSED=6  / CAT_03_TOTAL=7   (86%)
CAT_04_PASSED=7  / CAT_04_TOTAL=7   (100%)
```

### Review Assessment

**Overall Score: 77/100** — Recommendation: APPROVE

The reviewer found:
- All 10 acceptance criteria covered
- Excellent code quality, security, and architecture
- Near-perfect spec compliance (28/30)
- Strong robustness (9/10)

Despite the APPROVE recommendation, the oracle correctly NO-SHIPped due to the 84% scenario pass rate (below 100% threshold).

### Iteration Feedback Analysis

The iter 1 feedback cited "Missing Test Infrastructure" and "Runtime Functionality Issues" as blocking. However, the same 4 scenarios failed identically across all 3 iterations, indicating:

1. The feedback was not actionable enough to help the implementer fix these specific failures
2. The failures are likely scenario-implementation mismatches (e.g., scenarios expect `400` for malformed JSON but Fastify returns `500`, scenarios hit `/health?params` but the route doesn't match query-parameterized paths)

---

## Key Observations

### 1. Perfect Plateau Pattern
All 3 iterations produced exactly 84% (21/25) with the same 4 failures. The pipeline correctly classified this as PLATEAU and noted "Fundamentally different approach needed." However, the implementer agent was unable to break through — the feedback loop did not provide enough specific guidance about what was expected vs. what was produced.

### 2. Implementer Double-Run Pattern
In every iteration, the implementer ran twice:
- Iter 1: 293s + 61s
- Iter 2: 286s + 202s
- Iter 3: 247s + 77s

The first run completes, then a second run starts immediately. This appears to be a smoke test failure on the first attempt triggering an implementer retry. The second run passes smoke. This doubles implementer wall time and cost without improving outcomes.

### 3. Oracle Double-Run (Iter 1 Only)
In iteration 1, the oracle ran twice (81s + 53s). This didn't recur in iterations 2 or 3.

### 4. Scenario-Implementation Gap
The 4 failing scenarios reveal genuine edge cases where Fastify's default behavior differs from what the scenarios expect:
- **Malformed JSON → 500 instead of 400**: Fastify's default JSON parser throws a 500, not a structured 400
- **Invalid content-type → 404 instead of 400**: Fastify routes by content-type; an invalid one misses the route entirely
- **Empty POST body → 500 instead of 400**: Similar to malformed JSON
- **Health with query params → 404 instead of 200**: The route may not accept query parameters

These suggest the scenario architect generated reasonable expectations, but the implementer didn't add middleware to transform these Fastify default behaviors into spec-compliant 400 responses. The barrier prevents the implementer from seeing what specifically the scenarios expect, so the feedback must bridge this gap — and it didn't.

### 5. Feedback Quality Issue
The oracle/reviewer feedback was generic ("investigate scenario failures") rather than pointing to specific spec sections that describe the expected behavior for these edge cases. Better feedback anchored to spec sections (e.g., "FR-3 requires malformed JSON to return 400 with structured error envelope") would give the implementer more to work with.

### 6. Cost Efficiency
$252 estimated for a failed run is significant. The pipeline burned through 3 identical iterations without improvement. An early exit on detected PLATEAU (after iter 2 showed no improvement) could have saved ~$84.

---

## Metrics Summary

| Metric | Value |
|--------|-------|
| Total wall time | ~41 min |
| Estimated cost | $252 |
| Iterations completed | 3 |
| Final pass rate | 84% (21/25) |
| Pass rate trajectory | 84 → 84 → 84 (PLATEAU) |
| Scenarios crashed | 0 |
| Smoke failures | 0 (after retries) |
| Server errors | 0 |
| Final decision | NO-SHIP → ESCALATED |
| Anomalies | spec_turns (1200 > 800) |
| Satisfaction score | 0 (all iterations) |

---

## Regression/Improvement Comparison

Compared to the previous run of this spec (run_id `aee3be068d4e4af9`, also escalated at 84% after 4 iterations):
- Same outcome (ESCALATED)
- Same pass rate (84%)
- Same failing tests
- This run: 3 iterations (vs. 4 prior — likely due to config change or interrupted state)
- The underlying issue is persistent: these 4 edge cases are a blind spot for the implementer

---

## Actionable Insights for Pipeline Improvement

1. **Early PLATEAU exit**: When pass_rate is identical across 2 consecutive iterations with the same failing tests, consider escalating early rather than burning a 3rd iteration
2. **Implementer double-run investigation**: The pattern of implementer running twice per iteration wastes time and cost — investigate whether the first run's smoke failure is avoidable
3. **Feedback specificity**: The oracle feedback should reference specific spec sections and expected behaviors (e.g., "EC-6: malformed JSON must return 400 per FR-3") rather than generic guidance
4. **Scenario reasonableness check**: Consider whether scenarios that test Fastify's default behavior (which may differ from spec expectations) should be validated against framework capabilities during scenario generation
