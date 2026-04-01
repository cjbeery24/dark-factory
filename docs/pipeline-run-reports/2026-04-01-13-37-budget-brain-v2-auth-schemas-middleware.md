# Pipeline Run Report: budget-brain-v2 / 2026-03-30-auth-schemas-middleware

**Date:** 2026-04-01
**Repo:** budget-brain-v2
**Spec:** 2026-03-30-auth-schemas-middleware
**Dark Factory Version:** Latest (post architecture-lock commit 61401f9)

---

## Attempt 1

**Started:** 2026-04-01 13:37 UTC
**Run ID:** 1a81c58e3c974183
**Total Duration:** ~50 minutes
**Final Result:** ESCALATED after 3 iterations

### Pre-run Setup
- Updated agents/templates from dark-factory
- Rebuilt Docker images (all layers cached)
- Reset repo state: restored tracked files, deleted all untracked, cleaned .dark-factory runtime artifacts
- Spec pre-check: 1 ambiguity warning (`etc.` on line 21) - proceeded

### Pipeline Execution Timeline

| Step | Duration (approx) | Result |
|------|-------------------|--------|
| Spec Validation | ~1 min | PASS (score 85, divergence LOW, 0 blockers) |
| Architecture Lock | ~3 min | Completed |
| Scenario Generation | ~4 min | 30 scenarios in 1 tier (tier1-contracts) |
| Smoke Test | ~1 min | 0/1 suite passed (expected - baseline) |
| **Iteration 1** | ~10 min | Implementation + Gate PASS (30/30 scenarios) |
| Iter 1 Oracle | ~3 min | **NO-SHIP** - missing migration journal (`meta/_journal.json`), server won't start |
| **Iteration 2** | ~8 min | Implementation + Gate PASS (30/30 scenarios) |
| Iter 2 Oracle | ~3 min | **NO-SHIP** - server starts but HTTP requests fail (0% HTTP pass rate) |
| **Iteration 3** | ~8 min | Implementation + Gate PASS (30/30 scenarios) |
| Iter 3 Review | ~3 min | APPROVED (90/100) but early escalation triggered |
| Escalation | ~1 min | Escalation report generated |

### Quality Gate Results (All 3 Iterations)

| Gate | Iter 1 | Iter 2 | Iter 3 |
|------|--------|--------|--------|
| Build | PASS | PASS | PASS |
| Lint | PASS | PASS | PASS |
| Type Check | PASS | PASS | PASS |
| Self-Tests | SKIP | SKIP | SKIP |
| Blind Scenarios | PASS (30/30) | PASS (30/30) | PASS (30/30) |

### Review Prepass Scores
- Test Analysis: 20/25
- Code Quality: 20/20
- Security: 15/15
- Deterministic Total: 55

### Key Observations

#### 1. Scenario Coverage Gap (CRITICAL)
All 30 blind scenarios were static contract tests (file existence, schema structure, exports). They verified that files exist and have correct exports, but **none tested the running server**. This meant the implementation could achieve 100% scenario pass rate while the server was completely non-functional. The oracle correctly identified this gap through the pre-gate server startup check.

**Impact:** 100% blind scenario pass rate was misleading - the implementation was broken in all 3 iterations at the integration level.

**Root Cause:** The scenario architect generated only `tier1-contracts` (static) scenarios. No `tier2-integration` or HTTP-based scenarios were created despite the spec describing HTTP endpoints (`GET /api/v1/auth/me`), JWT middleware, and database migrations.

#### 2. Pre-Gate Server Check Caught Real Issues
The pre-gate server startup validation was the actual quality gate that caught failures:
- **Iter 1:** Pre-gate failed (attempt 1), passed on retry - but server still had issues
- **Iter 2:** Pre-gate passed first try
- **Iter 3:** Pre-gate failed (attempt 1), passed on retry

The pre-gate's "pass on retry" behavior may be masking intermittent startup issues.

#### 3. Oracle NO-SHIP Decisions Were Correct
Despite 100% scenario pass rate and gate PASS, the oracle correctly identified:
- Iter 1: Missing `meta/_journal.json` - server can't start at all
- Iter 2: Server starts but HTTP requests fail (middleware/route registration issues)
- Iter 3: Reviewer approved (90/100) but trajectory=PLATEAU triggered early escalation

#### 4. Server Startup Timeout Issue
Pipeline output shows: `WARNING: server startup timed out: Server did not become healthy at http://localhost:3000/health within 60s`

The server logs show it listens on `http://127.0.0.1:3000` and `http://172.25.0.4:3000` but the health check hits `http://localhost:3000/health`. Inside the Docker container, `localhost` resolves to `127.0.0.1`, so this should work. However, there's a mismatch: the health check targets port 3000, but the proxy might be routing to port 80 (log shows `"host":"localhost:80"`). This could be the root cause of the "server starts but requests fail" issue.

#### 5. Early Escalation on PLATEAU Trajectory
After iteration 3, the pipeline detected `trajectory=PLATEAU` (no improvement across iterations despite 100% pass rate) and triggered early escalation. This is correct behavior - the pass rate wasn't improving because scenarios weren't testing the actual failure mode.

### Improvement Opportunities

1. **Scenario generation needs HTTP/integration tier:** When a spec describes HTTP endpoints, the scenario architect should generate executable HTTP tests, not just static file checks.

2. **Pre-gate retry behavior:** Pre-gate "failed then passed on retry" happened in 2/3 iterations. This retry-to-pass pattern may mask flaky infrastructure. Consider logging the failure reason even when retry succeeds.

3. **Server health check port configuration:** The `base_url` in config.toml is `http://localhost:3000` but the proxy network may introduce port mapping issues. The server container showed both port 3000 and port 80 in logs. This needs investigation.

4. **Category breakdown files:** `category-breakdown-executable.env` was only written once (iter0). No per-iteration category files were written for iter1-3. The `category-iter0.env` showed `CAT_00_PASSED=0 CAT_00_TOTAL=30` but the actual iter0 results showed all 30 failed (expected). The breakdown wasn't updated for subsequent iterations.

5. **Metrics tracking:** The `test-results-iterN.json` files contained only 1 entry (the test suite as a whole) rather than individual test cases. This makes per-test-case regression tracking impossible.

### Conclusion

The pipeline correctly identified that the implementation was broken and escalated after 3 iterations. However, the root cause of failure was a **scenario coverage gap** - all 30 scenarios tested static properties, none tested the running server. If the scenario architect had generated HTTP integration tests, the quality gate would have caught the server startup failure directly, and the feedback loop to the implementer would have been more specific.

The oracle's review-based NO-SHIP decisions were the safety net that caught what blind scenarios missed. This demonstrates the value of the multi-layered quality system but also highlights that **scenario quality matters more than scenario quantity**.

---
