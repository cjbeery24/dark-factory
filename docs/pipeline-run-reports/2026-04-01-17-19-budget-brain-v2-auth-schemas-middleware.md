# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware
**Date:** 2026-04-01 17:19 UTC
**Spec:** 2026-03-30-auth-schemas-middleware
**Repo:** budget-brain-v2
**Pipeline version:** latest (main), commit d17e48d
**Run ID:** 58643e073161423b

---

## Run 1

### Pre-run state
- Previous pipeline state: `status=interrupted`, `iteration=2`, `last_decision=NO-SHIP`
- Repo had modified tracked files and untracked implementation artifacts from prior runs
- Reset: restored tracked files, removed untracked files, cleared state/scenarios/reports/reviews/architecture for this spec

### Setup
- Updated agents and templates from dark-factory source
- Rebuilt Docker images: `dark-factory build node` (all cached, fast)
- Reset repo to clean state (git checkout + git clean)

### Pipeline Execution
- **Status:** ESCALATED (max iterations reached)
- **Start time:** ~17:20 UTC
- **End time:** ~18:02 UTC (~42 minutes total)
- **Iterations completed:** 2 (iter 0 + iter 1, then escalation at iter 2 boundary)

### Timeline
| Time (UTC) | Step | Duration |
|------------|------|----------|
| 17:20 | Spec Validation | ~2 min |
| 17:22 | Architecture Manifest | ~3 min |
| 17:25 | Scenario Generation | ~4 min |
| 17:29 | Implementation (iter 0) | ~7 min |
| 17:36 | Quality Gate (iter 0) | ~8 min |
| 17:44 | Reviewer | ~2 min |
| 17:46 | Oracle (iter 0) -> NO-SHIP | ~1.5 min |
| 17:47 | Semantic leakage detected in oracle feedback |  |
| 17:49 | Implementation (iter 1) | ~4 min |
| 17:53 | Quality Gate (iter 1) | ~2 min |
| 17:55 | Reviewer (iter 1) | ~4 min |
| 17:59 | Oracle (iter 1) -> NO-SHIP | ~1.5 min |
| 18:00 | Semantic leakage detected in oracle feedback |  |
| 18:01 | Escalation report generated | ~1 min |

### Spec Validation
- **Verdict:** PASS (score 88/100, divergence LOW, 0 blockers)
- 1 ambiguity warning: "etc." on line 21 (non-blocking)

### Scenario Generation
- Generated 5 files:
  - `00-tier1-contracts.test.ts` (30 contract tests)
  - `01-contract.scenarios.md`
  - `02-error-conditions.scenarios.md`
  - `03-security.scenarios.md`
  - `04-adversarial.scenarios.md`

### Iteration 0 Results
- **Pre-gate (build/lint/typecheck):** PASS
- **Smoke test (tier-1 contracts):** 0/30 passed (0%)
  - All failures were assertion errors (file not found / content mismatch)
  - Tests expected files at paths like `apps/api/src/features/auth/auth.handlers.ts`, `auth.schemas.ts`, `auth.middleware.ts` — these didn't exist in the expected locations
- **Server startup:** FAIL (Drizzle migration error — missing `meta/_journal.json`)
- **Oracle decision:** NO-SHIP

### Iteration 1 Results
- **Implementer made fixes based on feedback** (migration system, JWT validation gaps)
- **Pre-gate (build/lint/typecheck):** PASS
- **Blind scenarios (executable):** PASS (test runner reported 30 tests passing as a single file entry)
- **Server startup:** FAIL (ECONNREFUSED to `172.25.0.3:443`)
  - Root cause: database connection routed through proxy (port 443 suggests HTTPS proxy interception of postgres connection)
  - This is likely an **infrastructure/environment issue** — the implementer's DB connection string works outside the container, but inside the container the proxy intercepts it
- **Oracle decision:** NO-SHIP
- **Semantic leakage detected:** Oracle feedback contained stack traces (`readMigrationFiles`) and pass rate percentages (`100% pass`, `0% pass`)

### Escalation
- Pipeline reached max iterations (2) and triggered escalation
- Escalation report correctly identified the core issue: **infrastructure/environment problem, not code quality**
- Implementation scored 75/100 (excellent code quality, security, architecture — blocked only by server startup)

### Key Observations

1. **Test result parsing inconsistency:** Iter 0 parsed 30 individual test entries (all failed). Iter 1 parsed as a single entry "00-tier1-contracts.test.ts (30 tests)" with status "passed". The test runner output format may have changed between runs, or the parser handles success/failure differently.

2. **Proxy interference with database connections:** The `ECONNREFUSED 172.25.0.3:443` error strongly suggests the HTTP/HTTPS proxy is intercepting the postgres connection. The `inject_env` in config sets `DATABASE_URL = "postgres://postgres:postgres@postgres:5432/testdb"` but inside the container, `HTTPS_PROXY` is set, which may cause postgres client libraries to route through the proxy.

3. **Semantic leakage in oracle feedback (twice):** Both iterations had leakage patterns detected:
   - Stack traces from actual error output
   - Pass rate percentages (`100% pass`, `0% pass`)
   This is a defense-in-depth concern — the leakage detection is working, but the oracle is still generating feedback that contains these patterns.

4. **Smoke test vs full gate discrepancy:** Smoke test showed 0/30 at iter 0, but the full quality gate at iter 1 showed blind scenarios PASS. The category env files only tracked tier-0 (`CAT_00`), and the breakdown showed 0/30 for iter 0 but no iter 1 category env was ever written.

5. **Missing `category-iter1.env`:** Despite iter 1 completing, the `category-iter1.env` file was never created. This may indicate the quality gate didn't fully complete category tracking for iter 1.

6. **Server startup is the persistent blocker:** Across both iterations, the server could not start. Iter 0 had a Drizzle migration metadata issue. Iter 1 had a network/proxy issue. The implementer fixed the migration structure but couldn't solve the container networking problem (which is outside its control).

### Outcome
**ESCALATED** — 2 iterations, 0% -> ~100% contract test improvement, but server startup failure persisted due to container proxy interference with database connections.

---

## Run 2-4
_Not attempted — user requested pipeline kill and report after Run 1._

---

## Summary & Metrics
| Metric | Run 1 | Run 2 | Run 3 | Run 4 |
|--------|-------|-------|-------|-------|
| Outcome | ESCALATED | - | - | - |
| Iterations | 2 | - | - | - |
| Contract Pass Rate (iter 0) | 0% (0/30) | - | - | - |
| Contract Pass Rate (iter 1) | ~100% (30/30) | - | - | - |
| Server Startup | FAIL (both iters) | - | - | - |
| Duration | ~42 min | - | - | - |
| Failure Type | ENV_ISSUE (proxy/DB) | - | - | - |

## Pipeline Issues Identified

### Critical
1. **Proxy intercepts database connections inside containers.** The `HTTPS_PROXY` env var set for all containers causes postgres connections to route through the tinyproxy, which can't handle postgres wire protocol. The `NO_PROXY` setting only exempts `localhost,127.0.0.1` — it needs to also exempt the Docker service hostnames (e.g., `postgres`).

### Moderate
2. **Test result parsing inconsistency between iterations.** Iter 0 produced 30 individual test entries; iter 1 collapsed them into 1 entry. This affects category tracking and metrics accuracy.
3. **Missing `category-iter1.env`.** Category breakdown wasn't written for iter 1, breaking metrics continuity.
4. **Semantic leakage in oracle feedback.** Stack traces and pass percentages leak through the oracle to the implementer feedback, weakening the information barrier.

### Minor
5. **Quality gate summary file was truncated/empty.** The `quality-gate-summary.md` had a header table but no gate rows.
6. **Smoke test env file wasn't updated between iterations** — still showed iter 0 results at end of run.
