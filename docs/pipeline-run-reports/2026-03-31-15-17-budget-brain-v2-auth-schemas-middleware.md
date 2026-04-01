# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware
**Date:** 2026-03-31 15:17 UTC
**Spec:** 2026-03-30-auth-schemas-middleware
**Repo:** budget-brain-v2

---

## Attempt 1

**Start time:** 15:17 UTC

### Pre-run Setup
- Updated agents/templates from dark-factory source
- Rebuilt docker images
- Reset repo: restored tracked files, deleted untracked files/dirs, removed .dark-factory runtime artifacts

### Pipeline Output
- Spec pre-check: PASS (1 ambiguity warning for "etc." — non-blocking)
- Scenario generation: 3 SA runs all failed validation
- Pipeline status: `interrupted` at step 1 (scenario generation)

### Observations
1. **`file_count` FAIL**: Only 2 `.test.ts` files generated (minimum is 3). The SA generated 3 `.scenarios.md` files AND 2 `.test.ts` files (mixed format), but validation counted only the `.test.ts` files for the file_count check.
2. **`no_source_reads` FAIL (CRITICAL)**: Both `.test.ts` files use `readFileSync` to read implementation source code:
   - `01-schema-migration-validation.test.ts`: reads `apps/api/src/config/env.ts`, `apps/api/src/db/schema/index.ts`
   - `02-security-logging.test.ts`: reads `apps/api/src/features/auth/auth.middleware.ts`, `auth.service.ts`, `auth.handlers.ts`, `auth.schemas.ts`
3. **Root cause**: The SA chose a "mixed format" approach — HTTP behavioral `.scenarios.md` for endpoint testing + executable `.test.ts` for "schema/migration validation". The `.test.ts` files inherently need to read source files to validate schema structure, which violates the barrier.
4. **SA agent instruction gap**: The SA was told not to read source code itself (barrier), but it generated test files that READ source code at runtime. The `no_source_reads` validator correctly catches this.
5. All 3 SA attempts produced the same pattern — mixed format with source-reading `.test.ts` files. The SA doesn't learn from validation failures within the same run.

### Result
**FAILED** — Scenario generation failed validation after 3 attempts. Pipeline never reached implementation phase.

---

## Attempt 2

**Start time:** ~15:37 UTC
**End time:** ~16:15 UTC

### Pre-run Setup
- Reset repo (checkout tracked, clean untracked, remove auth-schemas-middleware artifacts/state)
- Agents/templates and docker images from attempt 1 reused

### Pipeline Output
- Spec pre-check: PASS (same ambiguity warning)
- Scenario generation: PASS on first SA run (3 `.scenarios.md` + 1 `.test.ts` — mixed format)
- Scenario validation: PASS (3 markdown files found, all checks pass)
- **Iteration 1:**
  - Smoke test: FAIL (0/1 — server startup timeout)
  - Implementation complete
  - Pre-gate attempt 1: FAIL (lint errors — `no-non-null-assertion`, `no-undef`)
  - Pre-gate attempt 2: PASS
  - Quality gate: PASS (build/lint/typecheck pass, executable scenarios 30/30 = 100%)
  - BUT: HTTP behavioral scenarios 0/30 (server startup timeout)
  - Oracle: **NO-SHIP** (feedback had barrier leakage: "all scenarios")
- **Iteration 2:**
  - Pre-gate: PASS
  - Quality gate: PASS (executable 30/30, HTTP 0/30)
  - Oracle: **NO-SHIP** (feedback had barrier leakage: "0% pass")
- **Iteration 3:**
  - Pre-gate: PASS
  - Quality gate: PASS (executable 30/30, HTTP 0/30)
  - Oracle: **NO-SHIP** → **ESCALATED**

### Observations
1. **Scenario generation improved**: SA chose mixed format (.scenarios.md + .test.ts) without source-reading violations this time. Validation passed on first multi-run.
2. **Server startup timeout is the core blocker**: Every iteration hits `Server did not become healthy at http://localhost:3000/health within 30s`. The API requires a running Postgres database, Drizzle migrations, and proper env vars — the quality gate spins up postgres as a service but the server_cmd (`cd apps/api && node dist/index.js`) likely fails because:
   - Missing DATABASE_URL env var injection into the server process
   - Or migration hasn't run before server start
   - Or the server expects other env vars not provided
3. **Executable tests pass 100% but Oracle sees 0%**: The `category-breakdown-executable.env` shows 0/30 (from the HTTP behavioral scenarios), while `scenario-counts-executable.env` shows 30/30 (from the `.test.ts` file). The Oracle receives the 0% HTTP pass rate and repeatedly NO-SHIPs.
4. **Oracle barrier leakage**: Feedback validation catches "all scenarios" and "0% pass" as barrier violations. The system self-corrects (retries up to 3 times) but the patterns still slip through.
5. **Code quality is good**: Build, lint, typecheck all pass by iteration 2+. The implementation is architecturally sound.
6. **Missing `scenario-counts.env`**: The pipeline warns about this file not being found — it expects a non-executable scenario counts file that doesn't exist.

### Root Cause
The HTTP behavioral scenarios (.scenarios.md) require a running server, but the test server cannot start within the Docker quality gate container because:
- The `server_cmd` is `cd apps/api && node dist/index.js`  
- This needs DATABASE_URL and JWT_SECRET environment variables
- The postgres service injects these into the quality gate container, but likely the server starts before postgres is ready, or the env vars aren't propagated to the server subprocess

### Result
**ESCALATED** — 3 iterations exhausted. Executable tests 100%, HTTP behavioral tests 0% due to server startup failure.

---

## Attempt 3

**Start time:** ~16:13 UTC
**End time:** ~16:20 UTC (manually killed)

### Pre-run Setup
- Reset repo, cleaned containers

### Pipeline Output
- Spec pre-check: PASS
- Scenario generation: SA run 1 produced 3 `.scenarios.md` files (good format), but second SA run in multi-run validation produced only 2 files
- Scenario validation: FAIL (`file_count` — 2 files, min 3). `no_source_reads` PASS.
- Pipeline was in SA retry when manually killed

### Observations
1. **`file_count` remains flaky**: The SA inconsistently generates 2 vs 3 scenario files across runs. The multi-run validation (N=3, K=2) means all 3 runs need to independently produce ≥3 files.
2. **No source reads this time**: The SA correctly avoided `.test.ts` format with source reads.
3. **Killed early**: Given the server startup timeout would block this attempt the same way as attempt 2 even if scenarios passed, continuing was not productive.

### Result
**KILLED** — Manually terminated. Scenario generation failing on file_count, and even if it passed, the server startup timeout would block HTTP scenarios again.

---

## Summary / Metrics
| Metric | Value |
|--------|-------|
| Total attempts | 3 (of max 4) |
| Final result | Not shipped. Stopped early — systemic blocker identified. |
| Key blockers | 1. Server startup timeout (30s) prevents HTTP behavioral scenarios from running. 2. SA file_count flakiness (2 vs 3 files). |
| Pipeline improvements needed | See below |

## Pipeline Issues Identified

### Issue 1: Server Startup Timeout (CRITICAL — blocks shipping)
**Impact:** HTTP behavioral scenarios always get 0% pass rate, Oracle always NO-SHIPs.
**Details:** The test server (`cd apps/api && node dist/index.js`) needs a healthy Postgres and env vars. The pipeline's postgres service container injects `DATABASE_URL` and `JWT_SECRET` into the quality gate container env, but the server subprocess may not receive them, or may start before postgres is ready. The server also runs Drizzle migrations on startup which requires a live database connection.
**Possible fixes:**
- Investigate whether `services.inject_env` vars are actually available to `server_cmd` subprocess
- Add a wait-for-postgres step before starting the server
- Increase `startup_timeout` from 30s to 60s+
- Add better diagnostics (capture server stderr on startup failure)

### Issue 2: SA File Count Flakiness (MODERATE — blocks scenario generation)
**Impact:** SA sometimes generates only 2 scenario files when minimum is 3, causing validation to fail and waste SA retries.
**Details:** With multi-run N=3, all 3 independent SA runs must produce ≥3 files. This is a stochastic issue — the SA sometimes splits scenarios into 2 files vs 3.
**Possible fixes:**
- Lower minimum file count from 3 to 2 for specs with limited functional scope
- Add file count guidance to SA agent instructions
- Make the minimum configurable per-spec

### Issue 3: Oracle Barrier Leakage (LOW — self-correcting but noisy)
**Impact:** Oracle feedback validation catches phrases like "all scenarios" and "0% pass" as barrier violations. The retry mechanism handles it (up to 3 attempts) but adds noise to logs.
**Possible fixes:**
- Refine Oracle prompt to avoid absolute scenario count language
- Tune barrier regex to allow "0% pass rate" in Oracle feedback (it's a metric summary, not a test-name leak)

### Issue 4: Category Breakdown vs Scenario Counts Discrepancy (LOW — confusing metrics)
**Impact:** `category-breakdown-executable.env` shows 0/30 (HTTP behavioral) while `scenario-counts-executable.env` shows 30/30 (unit `.test.ts`). The Oracle sees the 0% from the category breakdown.
**Details:** When both HTTP behavioral and executable scenarios exist, the metrics are reported separately but the Oracle may only see one metric. This creates a misleading picture where the implementation actually passes all executable tests but gets NO-SHIPped for HTTP failures.
