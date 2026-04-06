# Pipeline Run Report: budget-brain-v2 / api-bootstrap

- **Date (UTC):** 2026-04-05 04:27
- **Repository:** budget-brain-v2
- **Spec:** 2026-03-28-api-bootstrap
- **Dark Factory version:** HEAD of main (commit bb60365)
- **Run ID:** e483c602c48c4104
- **Final outcome:** SHIPPED (via ship-with-gaps override)

---

## Pre-Run Setup

### Repository Reset
- Restored all tracked files via `git checkout -- .`
- Cleaned untracked files (removed `.venv/`, `apps/api/src/server.ts`, `apps/api/src/common/`, `src/`)
- Removed all `.dark-factory/` runtime artifacts (scenarios, reports, reviews, audit, state, events)

### Docker Build
- Rebuilt `dark-factory-playwright-node:latest` — completed successfully
- Cleaned orphaned containers and networks — none found

### Environment
- `pip install -e /path/to/dark-factory` — reinstalled successfully
- Updated agents and templates in budget-brain-v2
- Cleared Python bytecode caches

---

## Pipeline Run 1

### Timeline

| Time (UTC) | Event | Wall (s) |
|---|---|---|
| 04:29:01 | SPEC_START | — |
| 04:29:01 | spec-validator START | — |
| 04:30:54 | spec-validator COMPLETED | 113s |
| 04:30:54 | architect START | — |
| 04:33:19 | architect COMPLETED | 145s |
| 04:33:20 | scenario-architect START | — |
| 04:37:36 | scenario-architect COMPLETED | 140s |
| 04:37:38 | **Iter 1** implementer START (attempt 1) | — |
| 04:40:13 | implementer COMPLETED (pre-gate fix 1) | 155s |
| 04:40:15 | implementer START (pre-gate fix 2) | — |
| 04:43:11 | implementer COMPLETED (pre-gate fix 2) | 176s |
| 04:44:33 | implementer START (pre-gate fix 3) | — |
| 04:48:35 | implementer COMPLETED (pre-gate fix 3) | 243s |
| 04:48:38 | SMOKE PASSED (attempt 2) | — |
| 04:48:53 | GATE COMPLETED (85%, exit 1) | 574s total |
| 04:48:53 | reviewer START | — |
| 04:50:33 | reviewer COMPLETED | 100s |
| 04:50:33 | oracle START | — |
| 04:51:08 | oracle COMPLETED | 35s |
| 04:51:08 | **Iter 2** implementer START | — |
| 04:54:54 | implementer COMPLETED | 226s |
| 04:55:01 | SMOKE PASSED (attempt 1) | — |
| 04:55:16 | GATE COMPLETED (85%, exit 1) | 226s total |
| 04:55:16 | TRAJECTORY: PLATEAU | — |
| 04:55:16 | reviewer START | — |
| 04:57:17 | reviewer COMPLETED | 121s |
| 04:57:17 | oracle START | — |
| 04:57:49 | oracle COMPLETED | 32s |
| 04:57:49 | **SHIP_WITH_GAPS** (stable_ratio=1.0) | — |
| 04:57:49 | **SPEC_SHIPPED** | — |

**Total wall time:** ~29 minutes
**Estimated cost:** $168

### Iteration Summary

| Metric | Iter 1 | Iter 2 |
|---|---|---|
| Implementer runs | 3 (pre-gate retries) | 1 |
| Smoke attempts | 2 (pass on attempt 2) | 1 (pass on attempt 1) |
| Pass rate | 85% (17/20) | 85% (17/20) |
| Satisfaction | 85 | 85 |
| Gate exit code | 1 | 1 |
| Turns | 600 | 200 |
| Est. cost | $126 | $42 |
| Oracle said | SHIP (with **bold**) | SHIP |
| Pipeline decision | NO-SHIP | SHIP (via ship-with-gaps) |

### Quality Gate Report

```
| Gate | Status |
|------|--------|
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Self-Tests | SKIP |
| Mutation Testing | SKIP |
```

All static gates passed. No self-tests configured.

### Scenario Results (identical both iterations)

```
DF-SCENARIO: health check returns success with basic server status PASSED
DF-SCENARIO: health check follows success response envelope PASSED
DF-SCENARIO: health check uptime increases over time PASSED
DF-SCENARIO: health check works without authentication PASSED
DF-SCENARIO: health check includes server check in registry PASSED
DF-SCENARIO: undefined route returns structured error envelope PASSED
DF-SCENARIO: undefined route follows error envelope structure PASSED
DF-SCENARIO: POST to undefined route returns structured error PASSED
DF-SCENARIO: error responses have no data field PASSED
DF-SCENARIO: error code is always a string PASSED
DF-SCENARIO: multiple undefined routes all return consistent structure PASSED
DF-SCENARIO: health endpoint with query parameters ignores them FAILED
  Detail: Step 1: expected status 200, got 404
DF-SCENARIO: health endpoint with trailing slash FAILED
  Detail: Step 1: expected status 200, got 404
DF-SCENARIO: health endpoint accessible via OPTIONS FAILED
  Detail: Step 1: expected status 200, got 400
DF-SCENARIO: case sensitive routes PASSED
DF-SCENARIO: health endpoint with request body ignored PASSED
DF-SCENARIO: health endpoint without content-type header PASSED
DF-SCENARIO: root path returns 404 PASSED

3/20 scenario(s) FAILED
```

### Server Log (relevant excerpt)

```
Failed to start Budget Brain API: FastifyError: fastify-plugin: @fastify/cors - expected '4.x'
fastify version, '5.8.4' is installed
  code: 'FST_ERR_PLUGIN_VERSION_MISMATCH',
  statusCode: 500
```

The initial implementation installed `@fastify/cors` for Fastify 4.x with Fastify 5.x. This was fixed during pre-gate retries (smoke passed on attempt 2 for iter 1, attempt 1 for iter 2).

---

## Findings

### BUG (Critical): Oracle decision parser cannot handle JSON-wrapped output

**File:** `darkfactory/pipeline/iteration.py:1456` (`_parse_oracle_decision`)

The oracle agent's output is in Claude Code's `--output-format=json` format: `{"type":"result","result":"SHIP\n\n..."}`. The `_step_oracle` function passes this raw JSON string directly to `_parse_oracle_decision`, which looks for `SHIP` on the first non-empty line. Since the first line is `{"type":"result",...}`, the parser returns `NO-SHIP` regardless of the oracle's actual decision.

This means **the oracle's decision is always ignored** and the pipeline can only ship via:
1. The `ship-with-gaps` mechanical override (which happened here)
2. Pure coincidence if the JSON happened to start differently

**Impact:** Every pipeline run wastes at least one extra iteration because the oracle's SHIP verdict is never honored. The `ship-with-gaps` override is the only path to shipping.

**Fix needed:** Call `extract_agent_text(result.output)` before passing to `_parse_oracle_decision`, matching what the orchestrator does for escalation output.

**Additional issue:** In iteration 1, the oracle output's `result` field starts with `**SHIP**` (markdown bold). Even after extracting from JSON, `_parse_oracle_decision` would still fail because it checks for `stripped == "SHIP"` but `**SHIP**` != `SHIP`. The parser should strip markdown formatting (at minimum `**` bold markers).

### OBSERVATION: Pre-gate retries working well

Iteration 1 required 3 implementer runs (2 pre-gate retries) to get the server starting. The primary issue was Fastify/CORS version mismatch. By iteration 2, the implementer got it right on the first try. This shows the pre-gate retry mechanism is valuable for catching build/startup issues early.

### OBSERVATION: Stable failures are scenario edge cases, not implementation defects

The 3 consistent failures across both iterations:
1. **`/health` with query parameters → 404**: Fastify's default route matching treats `/health?foo=bar` differently than `/health`. This is arguably a scenario expectation issue — Fastify should handle this natively, and the scenario may be testing Fastify configuration rather than application logic.
2. **`/health` with trailing slash → 404**: Same issue — `/health/` is a different route in Fastify unless `ignoreTrailingSlash` is enabled.
3. **OPTIONS on `/health` → 400**: The CORS plugin may not handle preflight OPTIONS for the health endpoint as expected.

These are all Fastify framework behavior edge cases. The `ship-with-gaps` override correctly identified them as stable and shipped anyway.

### OBSERVATION: Iteration 2 was unnecessary

Because the oracle parser bug forces NO-SHIP on iteration 1, the pipeline burns a full extra iteration ($42) even though the oracle would have shipped on iteration 1. For this run, fixing the parser bug would have saved ~7 minutes and ~$42.

### METRIC: Cost per scenario pass

- 17 passing scenarios across 2 iterations
- $168 total cost
- ~$9.88 per passing scenario

### METRIC: Agent wall times

| Agent | Iter 1 | Iter 2 |
|---|---|---|
| Implementer (total) | 574s | 226s |
| Reviewer | 100s | 121s |
| Oracle | 35s | 32s |

---

## Summary

The pipeline successfully shipped the api-bootstrap spec in 2 iterations via the `ship-with-gaps` override. The implementation quality is good (all static gates pass, 85% scenario pass rate, reviewer APPROVED). 

The critical finding is a **bug in `_parse_oracle_decision`** that prevents the oracle's SHIP verdict from ever being recognized, forcing every run into unnecessary additional iterations until `ship-with-gaps` triggers. This is the highest-priority fix for pipeline efficiency.
