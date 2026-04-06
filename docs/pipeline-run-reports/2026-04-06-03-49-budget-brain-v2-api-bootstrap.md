# Pipeline Run Report: budget-brain-v2 / api-bootstrap

**Date:** 2026-04-06 03:49 UTC
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Starting commit:** c299b7c
**Run ID:** 82282978ac88419c
**Final Status:** ESCALATED (after 3 iterations, all at 48% pass rate)

---

## Timeline Summary

| Event | Timestamp (UTC) | Wall Time (s) | Notes |
|-------|-----------------|---------------|-------|
| SPEC_START | 03:50:11 | — | |
| Spec Validator done | 03:51:39 | 88s | |
| Architect done | 03:55:14 | 214s | |
| Scenario Architect done | 03:59:50 | 276s | 5 scenario files, 33 tests |
| Iter 1 Implementer done | 04:05:30 | 295s | |
| Iter 1 Gate done | 04:08:43 | — | **48% pass (16/33)** |
| Iter 1 Reviewer done | 04:10:23 | 100s | Score: 77/100 |
| Iter 1 Oracle done | 04:11:18 | 55s | NO-SHIP |
| Iter 2 Implementer done | 04:14:16 | 177s | |
| Iter 2 Gate done | 04:17:03 | — | **48% pass (16/33)** — PLATEAU |
| Iter 2 Reviewer done | 04:19:33 | 150s | |
| Iter 2 Oracle done | 04:21:49 | 59s+77s | NO-SHIP (oracle ran twice) |
| Iter 3 Implementer done | 04:24:21 | 152s | |
| Iter 3 Gate done | 04:26:22 | — | **48% pass (16/33)** — PLATEAU |
| Escalation agent done | 04:27:33 | 71s | SPEC_ESCALATED |

**Total wall time:** ~37.5 minutes (03:50 → 04:28)
**Estimated cost:** $252 (3 iterations × $84/iter)

---

## Pre-Run Warnings

```
WARNING: ambiguous term "appropriate" on line 223
WARNING: ambiguous term "etc." on line 235
WARNING: ambiguous term "etc." on line 425
WARNING: spec pre-check: 3 ambiguity warnings (proceeding)
```

---

## Iteration Results

### Pass Rate Trajectory: FLAT PLATEAU

| Iteration | Pass Rate | Satisfaction | Trajectory |
|-----------|-----------|-------------|------------|
| 1 | 48% (16/33) | 48 | — |
| 2 | 48% (16/33) | 48 | PLATEAU |
| 3 | 48% (16/33) | 48 | PLATEAU |

### Category Breakdown (identical across all 3 iterations)

| Category | Passed | Total | Weight |
|----------|--------|-------|--------|
| 01 (Contract) | 1 | 1 | 100 |
| 02 (Happy Path) | 0 | 5 | 100 |
| 03 (Edge Cases) | 0 | 4 | 80 |
| 04 (Error Conditions) | 0 | 6 | 90 |
| 05 (Security) | 0 | 2 | 80 |

Only contract tests (file-level structural checks) passed. All behavioral categories scored 0.

### Smoke Test

```
SCENARIO_PASSED=0
SCENARIO_FAILED=1
SCENARIO_TOTAL=1
SCENARIO_RATE=0
SCENARIO_CRASHED=0
SCENARIO_ASSERT_FAILED=1
```

Smoke test failed, which is expected (confirms scenarios fail cleanly before implementation).

### Pre-Gate

Pre-gate failed on first attempt in all 3 iterations, passed on second attempt each time. Build/lint/type-check all passed consistently.

---

## Test Results Detail (Iteration 1 = Iteration 3)

### Passing Tests (16/33)
- 01-contract.test.ts: All 8 contract tests ✅
- 429 for rate-limited requests returns structured error envelope ✅
- password field in request body is redacted in log output ✅
- email field in request body is redacted in log output ✅
- CORS rejects requests from disallowed origins ✅
- GET /health does not require authentication ✅
- GET /health is not rate limited ✅
- GET /health returns 200 with ok status and server check ✅
- deeply nested objects in logs maintain redaction only on configured paths ✅

### Failing Tests (17/33)
- server exits with code 1 when PORT is not a valid number ❌
- server exits with code 1 when NODE_ENV is unsupported value ❌
- 404 for undefined routes returns structured error envelope ❌
- unknown Error returns 500 with generic message and no stack trace ❌
- ZodError returns 400 with field-level validation details ❌
- GET /health returns degraded when registered check returns error ❌
- authorization header value is redacted in log output ❌
- error responses never leak file paths stack traces or internal details ❌
- server logs startup message with port and env ❌
- request generates unique requestId in logs ❌
- response time is logged as number in milliseconds ❌
- request log contains method, url, statusCode ❌
- server uses default values when env vars not set ❌ (5s timeout)
- server logs all invalid env vars when multiple are wrong ❌
- health check that throws exception is caught and reported as error ❌ (5s timeout)
- ZodError with multiple field errors returns all in details array ❌
- non-Error thrown value returns 500 with generic message ❌

### Failure Pattern Analysis

The failing tests cluster into distinct categories:

1. **Server process spawning tests (5 tests):** Tests that spawn server processes and inspect stdout/stderr are all failing. The scenarios assume a specific startup log format that the implementation doesn't match. Two of these hit 5-second timeouts.

2. **Error envelope format tests (5 tests):** 404, ZodError, non-Error, unknown Error — all expect `{ error: { code: "...", message: "..." } }` envelope format. The implementation likely uses a different response structure.

3. **Request logging format tests (3 tests):** requestId, response time, method/url/statusCode — scenarios expect specific structured log fields that the implementation doesn't produce in the expected format.

4. **Health check edge cases (2 tests):** Degraded status and exception-catching — likely implementation doesn't handle these edge cases.

5. **Authorization header redaction (1 test):** Different from other redaction tests that pass — may be checking a specific log path vs body field redaction.

---

## Quality Gate Report (Final Iteration)

```
| Gate          | Status | Details      |
|---------------|--------|--------------|
| Build         | PASS   | Exit code 0  |
| Lint          | PASS   | Exit code 0  |
| Type Check    | PASS   | Exit code 0  |
| Self-Tests    | SKIP   | No command configured |
| Blind Scenarios | FAIL | Exit code 1  |
| Mutation Testing | SKIP | Not configured |
```

### Unhandled Error in Test Suite
```
SyntaxError: Unexpected number in JSON at position 2 (line 1 column 3)
❯ Socket.<anonymous> 02-happy-path.test.ts:141:31
```
The happy-path scenario tries to `JSON.parse()` the server's stdout to extract port/env info. This parsing fails because the server output doesn't match the expected format — this is a **scenario-implementation mismatch** where the scenario makes assumptions about log format that the spec may be ambiguous about.

---

## Warnings and Anomalies

```
WARNING: oracle feedback contains 1 leakage patterns
WARNING: oracle feedback failed validation (attempt 1/3): 
  Feedback violated 1 barrier rule(s): pattern '\b\d+%\s*(?:pass|fail)': ['48% pass']
WARNING: anomaly: spec used 1200 total turns (max 800)
WARNING: early escalation: trajectory=TrajectoryType.PLATEAU at iteration 3
```

### Barrier Leakage
The oracle's first feedback attempt included "48% pass" — a literal pass rate percentage that leaks scenario results to the implementer. The barrier validation caught this and the oracle retried, but this happened in iteration 1's feedback. Worth investigating whether the cleaned retry actually removed all leakage.

### Turn Anomaly
Total turns across all iterations: 1200 (max expected: 800). Each iteration used ~400 turns. This is inflated because the implementer and quality gate each get separate agent runs within a single iteration.

### Oracle Double-Run (Iteration 2)
The oracle ran twice in iteration 2 (77s + 59s). This appears to be the oracle-retry mechanism when feedback validation fails — the first attempt had barrier leakage, so it retried.

---

## Scenario Validation

All scenario validation checks passed:
- 5 files, minimum 3 required ✅
- No empty test bodies ✅
- No source file reads (barrier respected) ✅
- No hardcoded container paths ✅
- **Warning:** 1 file has environment-dependent assertions (02-happy-path.test.ts)
- **Warning:** 1 file uses `toMatch` without `/m` flag (01-contract.test.ts)

---

## Key Findings

### 1. Complete Iteration Stall (Critical)
Zero progress across 3 iterations. The implementer received feedback each time but made no effective changes to the failing tests. The same 16 tests passed and 17 tests failed in every iteration. This is the most critical issue — the iteration loop is not producing improvement.

**Possible causes:**
- The feedback is too abstract (references FR-N/AC-N spec sections but doesn't tell the implementer what specific output format to match)
- The implementer lacks visibility into what the tests actually expect (by design — information barrier)
- The implementer may be making changes that don't affect the failing test paths
- The oracle barrier leakage filter may be stripping useful signal from the feedback

### 2. Scenario-Implementation Format Mismatch (Major)
The scenarios make specific assumptions about log format (JSON-parseable structured logs, specific field names) that the implementation doesn't match. Since neither the implementer nor scenario architect can see each other's work, the spec is the only shared contract — and it appears insufficient to align them on log format details.

### 3. Server Process Spawning Fragility (Major)
5 tests spawn server subprocesses and inspect stdout/stderr. These tests are inherently fragile:
- They depend on exact startup message format
- They use `JSON.parse()` on output buffers that may contain multiple lines
- They hit 5-second timeouts when the server doesn't produce the expected output
- The `SyntaxError` in the test suite is from this pattern

### 4. Pre-Gate Requires 2 Attempts (Minor)
Pre-gate (build/lint/type-check) consistently failed on first attempt but passed on retry. This suggests a transient build issue (maybe missing `npm install` or stale cache before first attempt).

### 5. Feedback Quality (Observation)
- Iteration 1 feedback: 9 concrete issues identified, all classified as IMPL_BUG
- Iteration 2 feedback: More abstract, referencing FR-N sections, classified mix of IMPL_BUG and SPEC_AMBIGUOUS
- The feedback gets increasingly abstract across iterations while the implementer makes no progress — suggests the feedback channel isn't providing actionable enough guidance

### 6. Cost Efficiency (Observation)
$252 estimated for a run that produced no shippable result. Each iteration costs $84. The turn budget anomaly (1200 > 800 max) suggests the spec may be too large for single-pass implementation.

---

## Metrics Summary

| Metric | Value |
|--------|-------|
| Total wall time | 37.5 min |
| Estimated cost | $252 |
| Iterations | 3 (all NO-SHIP) |
| Final pass rate | 48% |
| Improvement per iteration | 0% |
| Trajectory | PLATEAU (all iterations) |
| Agent runs | 17 total |
| Anomalies | 1 (spec_turns) |
| Barrier violations | 1 (oracle feedback leaked pass rate) |
