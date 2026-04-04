# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware
**Date:** 2026-04-02 05:07 UTC
**Spec:** 2026-03-30-auth-schemas-middleware
**Repo:** budget-brain-v2
**Pipeline version:** latest (main)
**Previous run context:** Prior session (2026-04-01 21:17 UTC) ran 3 attempts. Best result was Run 3: server started successfully (logs confirmed) but quality gate reported FAIL — Docker networking or health check issue. 30/30 contracts pass consistently. Key pipeline issues identified: non-deterministic divergence rating, server startup detection broken, category breakdown bug.

---

## Run 1

### Pre-run state
- Repo has modified tracked files and untracked implementation artifacts from prior runs
- Reset: full clean (restore tracked, delete untracked, clear .dark-factory runtime artifacts + config.sh)

### Setup
- Updated agents and templates from dark-factory source
- Rebuilt Docker images: `dark-factory build node`
- Full repo reset to clean state

### Pipeline Execution
- **Status:** ESCALATED (early — ENV_ISSUE classification)
- **Run ID:** dbdcef61704f4b54
- **Duration:** ~30 minutes
- **Iterations completed:** 1 (early escalation after iter 1)

### Spec Validation
- **Verdict:** PASS (no divergence rejection this time)
- Ambiguity warning: "etc." on line 21 (non-blocking)

### Architecture Manifest
- Chose `postgres` (standard TCP driver) — correct for pipeline's `postgres:16-alpine`
- Correctly noted that `drizzle.config.ts` should be at `apps/api/drizzle.config.ts`
- Runtime manifest `server_setup_cmd: cd apps/api && pnpm drizzle-kit push:pg`

### Scenario Generation
- 4 markdown scenario files + 1 executable tier-1 contracts test (30 tests)
- Smoke test: all scenarios fail cleanly (pre-implementation — expected and healthy)

### Iteration 1 Results
- **Pre-gate attempt 1:** Build FAIL, Lint FAIL, Type Check FAIL
  - TypeScript errors: `FastifyReply`, `FastifyInstance` not exported from `"fastify"` module
  - 12 TypeScript errors total (import issues, implicit any types, circular references)
  - Lint: unused `fastify` import in `index.ts`
- **Pre-gate attempt 2:** Build PASS, Lint PASS, Type Check PASS (all fixed!)
- **Blind scenarios:** 29/30 PASS (96%)
  - **TC-25 FAIL:** "environment validation file exists with JWT config" — `grep -rl 'JWT_SECRET\|CLERK_JWKS_URL' apps/api/src/config/env.ts apps/api/src/env.ts apps/api/src/features/auth/config.ts` — none of those paths matched
- **Server Setup:** FAIL
  - Drizzle config uses `dialect: 'postgresql'` (drizzle-kit v0.25+ syntax) but installed version is v0.20.18 which requires `driver: 'pg'`
  - Error: `Invalid input Only "pg" is available options for "--driver"`
- **Review prepass:** 40/60 (TEST_ANALYSIS=5 due to server failure, CODE_QUALITY=20/20, SECURITY=15/15)
- **Oracle:** NO-SHIP

### Early Escalation
- **Triggered by:** All 3 failure classifications were `ENV_ISSUE`
- Pipeline detected that the failures are environment/config issues outside the Implementer's scope
- Only 1 iteration ran instead of the usual 2 — this is a new pipeline improvement (ENV_ISSUE-based early escalation)
- Escalation report was properly formatted markdown (not raw JSON — bug from prior runs appears fixed)

### Escalation Report Quality
- **Format:** Clean markdown (fixed from prior runs' raw JSON)
- **Content:** Correctly identifies drizzle config version mismatch, missing scripts, missing env validation
- **Root cause accuracy:** Partially correct — identifies drizzle config issue but also flags missing `start` script and `db:migrate` script which may or may not be actual blockers depending on pipeline setup_cmd
- **Score:** 70/100 — reasonable given the issues
- **Recommendation:** Expedited iteration 2 focused on config fixes

### Key Observations

1. **Early escalation is a good improvement.** The pipeline correctly detected all failures were ENV_ISSUE and stopped after 1 iteration instead of wasting time on 2 more iterations that would hit the same config blocker. This saved ~20 minutes.

2. **Spec validation passed consistently.** No divergence rejection this time. Combined with prior session: 3/4 runs passed validation (1 random HIGH divergence in the first session).

3. **Pre-gate self-healing worked.** Build/lint/typecheck all failed on attempt 1, then the implementer fixed all issues and passed on attempt 2. This 2-attempt pre-gate is effective.

4. **29/30 contracts pass.** Only TC-25 fails because the implementer put JWT config in a different location than what the scenario tests grep for. The scenario tests check 3 hardcoded paths (`apps/api/src/config/env.ts`, `apps/api/src/env.ts`, `apps/api/src/features/auth/config.ts`) — if the implementer puts env validation elsewhere, it fails.

5. **Drizzle version mismatch persists.** The spec mentions `drizzle-orm` and the pipeline installs whatever version `pnpm install` resolves. The installed `drizzle-kit` (v0.20.18) uses `driver: 'pg'` syntax, but the implementer writes `dialect: 'postgresql'` (v0.25+ syntax). This is a recurring issue.

6. **Escalation report format fixed.** Prior runs produced raw JSON blobs; this run produced clean markdown. This was a known bug that's now resolved.

7. **Category breakdown format changed.** Now shows `CAT_00_PASSED=0, CAT_00_TOTAL=1` (1 executable scenario file, 0 passed) instead of the old `CAT_00_PASSED=0, CAT_00_TOTAL=30`. This may be intentional — tracking by file rather than by individual test case.

### Outcome
**ESCALATED** — 1 iteration (early, ENV_ISSUE classification). 29/30 contracts pass. Server startup fails due to drizzle-kit version mismatch. Code quality excellent (20/20). Security perfect (15/15). The blocker is config/environment, not implementation quality.

---

## Summary & Metrics

| Metric | Run 1 |
|--------|-------|
| Outcome | ESCALATED (early) |
| Spec Validation | PASS |
| Iterations | 1 (early escalation) |
| Pre-gate (first attempt) | FAIL (build/lint/typecheck) |
| Pre-gate (second attempt) | PASS |
| Contract Pass Rate | 96% (29/30) |
| Server Startup | FAIL (drizzle-kit version mismatch) |
| Review Prepass | 40/60 |
| Code Quality | 20/20 |
| Security | 15/15 |
| Duration | ~30 min |
| Postgres Driver | postgres (standard TCP) |
| Escalation Trigger | ENV_ISSUE classification (all 3 failures) |

## Comparison with Prior Session (2026-04-01 21:17 UTC)

| Aspect | Prior Run 1 | Prior Run 2 | Prior Run 3 | This Run 1 |
|--------|-------------|-------------|-------------|------------|
| Spec validation | FAIL (HIGH div) | PASS (LOW div) | PASS (LOW div) | PASS |
| Iterations | 0 | 2 | 2 | 1 (early esc) |
| Build first attempt | N/A | FAIL | PASS | FAIL |
| Build final | N/A | PASS | PASS | PASS |
| Contracts | N/A | 30/30 | 30/30 | 29/30 |
| Server startup | N/A | FAIL (Neon driver) | FAIL (gate detection) | FAIL (drizzle ver) |
| Postgres driver | N/A | @neondatabase | postgres | postgres |
| Escalation format | N/A | Raw JSON | Raw JSON | Clean markdown |
| Root failure | Divergence | Driver compat | Gate detection | Drizzle version |

## Pipeline Improvements Observed (vs Prior Session)

1. **Early escalation on ENV_ISSUE** — New behavior. Pipeline stops after 1 iteration when all failures classify as ENV_ISSUE. Saves significant time.
2. **Escalation report format fixed** — Now outputs clean markdown instead of raw JSON blob.
3. **Pre-gate retry** — Pipeline retries pre-gate (2 attempts) giving the implementer a chance to self-correct build/lint/typecheck issues before running full gate.
4. **Smoke test is a pre-implementation health check** — Confirms scenarios are well-formed (all fail cleanly before any code exists).

## Persistent Issues

1. **Server startup still fails.** Different root cause each run (Neon driver, gate detection, drizzle version mismatch), but the server never successfully starts and passes the quality gate.

2. **Drizzle version mismatch.** The implementer consistently writes modern drizzle-kit config syntax (`dialect: 'postgresql'`) but the installed version expects legacy syntax (`driver: 'pg'`). The architect manifest doesn't specify which drizzle-kit version to install.

3. **TC-25 environment validation path mismatch.** The scenario tests grep for JWT config in 3 hardcoded paths. If the implementer puts config elsewhere, the test fails. This is a brittle test — it should be more flexible about where env validation lives.

4. **Category breakdown semantics unclear.** Shows `CAT_00_TOTAL=1` (1 file?) vs prior `CAT_00_TOTAL=30` (30 tests?). The meaning has changed between runs.

## Recommendations for Next Steps

1. **Pin drizzle-kit version in spec or manifest.** The spec should declare which drizzle-kit version to use, or the architecture manifest should specify the version. The implementer has no way to know which version is installed in the container.

2. **Make TC-25 more flexible.** Instead of grepping 3 hardcoded paths, search the entire `apps/api/src` tree for JWT config validation.

3. **Investigate server startup detection.** Prior Run 3 showed the server starts successfully (logs confirmed) but the gate still reports FAIL. This run shows a genuine startup failure (drizzle config). The gate needs better diagnostics — it should log the actual error from the server process.

4. **Consider passing installed package versions to the implementer.** The implementer could check `pnpm list drizzle-kit` to see which version is installed and use the correct config syntax.
