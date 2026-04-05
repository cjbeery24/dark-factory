# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

- **Date (UTC):** 2026-04-05 00:23
- **Repository:** budget-brain-v2
- **Spec:** 2026-03-28-api-bootstrap
- **Run ID:** 29e7f86473b147a3
- **Dark Factory version:** editable install from ~/sites/dark-factory
- **Model:** claude-sonnet-4-20250514 (all agents)
- **Outcome:** ESCALATED (failed to ship after 3 iterations)
- **Total wall time:** ~51 minutes (00:24:03 to 01:14:48)
- **Estimated total cost:** $168 (iter1: $84, iter2: $42, iter3: $42)

---

## Pipeline Phase Timings

| Phase | Wall Seconds | Notes |
|-------|-------------|-------|
| Spec Validator | 98s | Clean pass |
| Architect | 124s | Clean pass |
| Scenario Architect (generate) | 301s | 23 HTTP scenarios from 33 intents |
| Scenario Architect (validate) | 261s | Validation pass |
| **Iter 1 Implementer** | 273s + 110s | First attempt failed smoke (missing fastify dep), auto-retried |
| Iter 1 Gate | ~15s | 39% pass rate, 0% satisfaction |
| Iter 1 Reviewer | 122s | Score: 67.5/100 |
| Iter 1 Oracle | 60s | NO-SHIP |
| **Iter 2 Implementer** | 494s | Longest single agent run |
| Iter 2 Gate | ~15s | 78% pass rate, 0% satisfaction |
| Iter 2 Reviewer | 121s | Score: 82.5/100 |
| Iter 2 Oracle | 68s + 39s | Ran twice (anomaly — see observations) |
| **Iter 3 Implementer** | 83s | Shortest implementer run — plateau behavior |
| Iter 3 Gate | ~15s | 78% pass rate, 0% satisfaction (no improvement) |
| Iter 3 Escalation | 66s | Generated escalation report |

---

## Iteration Progression

| Metric | Iter 1 | Iter 2 | Iter 3 |
|--------|--------|--------|--------|
| Pass Rate | 39% | 78% | 78% |
| Satisfaction | 0% | 0% | 0% |
| Review Score | 67.5 | 82.5 | — |
| Trajectory | — | PLATEAU | PLATEAU |
| Build/Lint/Types | PASS | PASS | PASS |
| Smoke Test | PASS (attempt 2) | PASS (attempt 1) | PASS (attempt 1) |
| Decision | NO-SHIP | NO-SHIP | ESCALATED |

---

## Scenario Results (Final Iteration — 18/23 passing, 78%)

### Passing (18)
- GET /health follows success response envelope
- GET /health is not rate limited after many requests
- 404 for undefined routes returns structured error envelope
- POST/PUT/DELETE request to undefined route returns 404 with error envelope
- 404 error response follows error envelope format
- Rate limiting returns 429 with structured error envelope
- 429 for rate-limited requests returns structured error envelope
- Rate limit error response follows error envelope format
- CORS request with allowed origin includes proper headers
- CORS preflight for POST request with allowed origin
- Simple CORS request returns proper headers
- GET request with extremely long URL path
- POST request with very large JSON body
- Request with unusual Content-Type header
- Request with special characters in URL path
- Multiple concurrent requests to health endpoint

### Failing (5)

```
DF-SCENARIO: GET /health returns success with basic server check FAILED
  Detail: Step 1: body field 'data.uptime': 'number' not found in 4.954

DF-SCENARIO: GET /health includes uptime in seconds FAILED
  Detail: Step 1: body field 'data.uptime': 'number' not found in 4.955

DF-SCENARIO: CORS preflight request with allowed origin FAILED
  Detail: Step 1: expected status 204, got 400

DF-SCENARIO: Request validation on malformed JSON returns error envelope FAILED
  Detail: Step 1: expected status 400, got 500

DF-SCENARIO: POST request with empty body FAILED
  Detail: Step 1: expected status 404, got 500
```

---

## Diagnostic Data

### State File (final)
```
spec=2026-03-28-api-bootstrap
run_id=29e7f86473b147a3
spec_hash=3daed8fbb63262825e58555d4f3e7158e99d2306af1c1d57b5a9a9485ad36781
last_decision=NO-SHIP
server_error=
smoke_result=passed
phase=review
pass_rate=78
gate_exit_code=1
status=interrupted
iteration=4
completed_step=2
```

### Server Container Log (Iter 1 — smoke failure)
```
Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'fastify' imported from /project/apps/api/dist/server.js
    at Object.getPackageJSONURL (node:internal/modules/package_json_reader:314:9)
    at packageResolve (node:internal/modules/esm/resolve:767:81)
    ...
```
The implementer compiled JS to `dist/` but didn't add `fastify` as a production dependency in `apps/api/package.json`. The pipeline auto-retried with a second implementer run, which fixed it.

### Quality Gate Summary (final)
```
| Gate | Status |
|------|--------|
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Self-Tests | SKIP |
| Mutation Testing | SKIP |
```

### Feedback Classifications (all iterations)
All issues classified as `IMPL_BUG` or `BRITTLE` — no spec issues identified.

---

## Observations

### 1. Health endpoint `data.uptime` type assertion failure (SCENARIO BUG)
The two health endpoint failures show `body field 'data.uptime': 'number' not found in 4.954`. The value 4.954 *is* a number — this looks like the scenario runner's type assertion is failing incorrectly, possibly treating the value as a string or doing an exact-match comparison instead of a type check. **This is likely a scenario runner bug, not an implementation bug**, and it accounts for 2 of the 5 failures (40% of the gap).

### 2. CORS preflight returning 400 instead of 204
The CORS preflight scenario expects status 204 but gets 400. This could be either:
- The implementation's CORS plugin not handling OPTIONS properly
- The scenario sending a preflight without proper `Access-Control-Request-Method` header

### 3. Malformed JSON and empty body returning 500 instead of 400/404
Two scenarios expect the server to return 4xx for malformed/empty POST bodies, but the server returns 500. This suggests the error handler isn't catching body-parsing errors from Fastify properly.

### 4. Oracle ran twice in iteration 2
Events show two consecutive oracle AGENT_START/COMPLETED pairs for iter 2 (01:10:11-01:11:19 and 01:11:19-01:11:58). The first took 68s, the second 39s. This is anomalous and may indicate a retry or an issue with the oracle output parsing. Worth investigating in pipeline code.

### 5. Implementer ran twice in iteration 1
After the first implementer completed (00:53:07), a second implementer started at 00:54:26. This was the smoke-test-failure auto-retry, which is expected behavior — but the events log doesn't include a SMOKE_COMPLETED failure event before the retry. The smoke failure should be logged as an event for diagnostic completeness.

### 6. 0% satisfaction across all iterations despite 78% pass rate
The satisfaction metric remained at 0% even as pass rate doubled. This disconnect suggests either:
- The satisfaction calculation has a bug
- Satisfaction requires a threshold pass rate that was never reached
- Satisfaction measures something different from scenario pass rate

### 7. Iteration 3 implementer was very short (83s)
With the trajectory classified as PLATEAU, the iter 3 implementer barely did any work (83s vs 494s in iter 2). This suggests the implementer may have "given up" or made minimal changes, knowing the trajectory classification indicated a fundamentally different approach was needed but lacking the context to try one.

### 8. Scenario coverage gap for non-HTTP behavior
The scenario architect generated 23 HTTP scenarios from 33 intents, explicitly noting that many spec requirements (startup validation, logging, etc.) aren't testable via HTTP. This is correct behavior for the HTTP scenario format, but means ~30% of the spec is untested by scenarios.

---

## Improvement Opportunities

### Pipeline Issues
1. **Scenario runner type assertion bug** — The `'number' not found in 4.954` error suggests the runner is doing something wrong with numeric type assertions. Should be investigated and fixed.
2. **Missing smoke failure event** — When smoke test fails and triggers a retry, the failure should be logged as an event in events.jsonl.
3. **Oracle double-run** — Investigate why the oracle ran twice in iteration 2.
4. **Satisfaction metric** — The 0% satisfaction across all iterations while pass rate improved needs investigation. Is the metric calculated correctly?

### Spec/Agent Quality
5. **Dependency management** — The implementer's first attempt forgot to install production dependencies. The architecture manifest or implementer prompt could emphasize checking `package.json` dependencies.
6. **Error handler coverage** — The feedback correctly identified error envelope compliance issues that persisted across iterations. The implementer struggled to fully resolve these, possibly because the feedback was too general.

### Process
7. **Plateau detection could be more actionable** — The PLATEAU classification said "fundamentally different approach needed" but didn't provide specific guidance on what approach to try.
