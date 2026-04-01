# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware
**Date:** 2026-03-31 16:53 UTC
**Spec:** 2026-03-30-auth-schemas-middleware
**Repo:** budget-brain-v2
**Goal:** Track pipeline improvement/regression metrics across attempts

---

## Attempt 1
**Started:** 2026-03-31 16:53 UTC

### Pre-run Setup
- Updated agents/templates from dark-factory source
- Rebuilt docker images (`dark-factory build node`)
- Reset repository state (restored tracked files, deleted untracked, cleared .dark-factory runtime artifacts)

### Pipeline Execution
- **Status:** FAILED
- **Outcome:** Scenario generation failed after 3 attempts (SA retry loop exhausted)

### Observations
1. **Spec pre-check:** 1 ambiguity warning ("etc." on line 21) — non-blocking
2. **Spec validation:** PASS (score 88, divergence LOW, 1 blocker)
3. **Scenario architect failed `no_source_reads` validation** — files `02-error-conditions.test.ts` and `03-security-adversarial.test.ts` contained `readFileSync` calls targeting source code files (`.ts` extensions), violating the information barrier
4. SA was retried 3 times (2 inner retries x ~2 outer retries) — all attempts produced the same violation
5. Scenarios were otherwise well-structured: 26 scenarios across 4 files covering contract, error, security, and database layers
6. The scenarios report shows good intent coverage (12/12 declared intents compiled) and proper free variable compliance
7. **Root cause:** The SA generates TypeScript unit tests that use `readFileSync` to inspect source file contents (e.g., checking for hardcoded secrets, RLS patterns). This is a legitimate testing strategy but conflicts with the `no_source_reads` barrier validation. The SA prompt/instructions may not sufficiently communicate that source file reads are prohibited even in test code.

### Metrics
- **Total duration:** ~13 min (16:53 - 17:06 UTC)
- **Spec validation:** PASS (2 min)
- **Scenario generation:** FAIL after 3 attempts (~10 min total)
- **Implementation iterations:** N/A (never reached)
- **Quality gate results:** N/A
- **Final verdict:** FAIL — blocked at scenario generation

---

## Attempt 2
**Started:** 2026-03-31 17:15 UTC
**Stopped:** 2026-03-31 ~18:03 UTC (manually killed — server startup issue unfixable within pipeline)

### Pre-run Setup
- Reset repository state (clean working tree)
- Updated agents/templates from dark-factory source
- Reused cached docker images

### Pipeline Execution
- **Status:** KILLED (manually stopped during iteration 2 oracle decision)
- **Outcome:** Server startup persistently failed across both iterations — Drizzle migration path resolution broken

### Scenario Generation (3 SA attempts)
1. **SA attempt 1:** Generated 6 files (4 `.scenarios.md` + 2 `.test.ts`). Failed validation:
   - `file_count` FAIL — only 2 `.test.ts` files counted (min 3); the 4 `.scenarios.md` files may not count toward minimum
   - `assertion_upper_bound` FAIL — `05-database-migration.test.ts` had 34 assertions (max 30)
   - `no_source_reads` PASS — improvement over attempt 1
2. **SA attempt 2:** Generated 3 files. Failed validation:
   - `file_count` FAIL — only 2 files (min 3)
   - `no_source_reads` FAIL — `03-security-validation.test.ts` read source files
3. **SA attempt 3:** Generated 3 `.scenarios.md` files (all HTTP behavioral, no `.test.ts`). **PASSED validation.**
   - Avoiding `.test.ts` entirely eliminated the `no_source_reads` problem
   - `00-tier1-contracts.test.ts` was auto-added by the pipeline

### Iteration 1 (implement -> gate -> review -> oracle)
- **Implementation:** Full auth system created (15 new files): DB schemas, auth middleware, handlers, routes, services, Drizzle migrations, env config
- **Pre-gate:** FAIL after 2 attempts
  - Build: PASS, Lint: FAIL (2 errors), Type Check: PASS
  - Lint errors: `drizzle.config.ts` tsconfig resolution, `eslint.config.js` URL not defined
- **Quality gate:** Build PASS, Lint FAIL, Type Check PASS, Blind Scenarios (executable) PASS (30/30 tier 1 contracts)
- **Server startup:** FAILED — `Can't find meta/_journal.json file` in Drizzle migrator
  - Root cause: migration files in `drizzle/` at project root, but migrator resolves path relative to `apps/api/dist/` inside the monorepo
- **HTTP behavioral scenarios:** 0% (server never started)
- **Deterministic score:** 30/60
- **Oracle decision:** NO-SHIP

### Iteration 2 (implement -> gate -> review -> oracle)
- **Implementation improvements:**
  - Fixed lint errors (eslint.config.js, drizzle.config.ts)
  - Pipeline auto-detected `test_cmd = 'pnpm -r run --if-present test'` and updated config.toml
- **Pre-gate:** PASS (Build PASS, Lint PASS, Type Check PASS)
- **Self-Tests:** FAIL — `comprehensive-test-runner.js` crashes on missing `DATABASE_URL` env var (no DB in gate container)
- **Quality gate:** Build PASS, Lint PASS, Type Check PASS, Self-Tests FAIL, Blind Scenarios (executable) PASS
- **Server startup:** FAILED again — same `Can't find meta/_journal.json` error (not fixed between iterations)
- **HTTP behavioral scenarios:** 0% (server never started)
- **Code Quality:** 20/20 (improved from 15/20)
- **Deterministic score:** 35/60
- **Oracle decision:** In progress when killed

### Key Findings
1. **SA `no_source_reads` is a persistent failure mode** — SA naturally wants to generate `.test.ts` files that inspect source code for security checks. Only succeeded when forced to all-HTTP-behavioral `.scenarios.md` format on 3rd attempt.
2. **Drizzle migration path resolution in monorepo is a blocking issue** — The migrator can't find `drizzle/meta/_journal.json` when running from `apps/api/dist/`. The implementer never fixed this across iterations despite it being the primary failure cause.
3. **Implementer added self-tests that require DATABASE_URL** — These crash in the gate container which has no DB. The implementer doesn't understand that gate containers run without service dependencies.
4. **Iteration feedback loop partially works** — Lint was fixed between iterations, but the critical server startup issue was not addressed. The feedback mechanism may not clearly communicate server startup failures.
5. **Tier 1 contracts (executable) pass reliably** — 30/30 both iterations. The structural/file-existence checks work well.

### Metrics
- **Total duration:** ~48 min (17:15 - 18:03 UTC)
- **Spec validation:** PASS (2 min)
- **Scenario generation:** PASS on 3rd attempt (~13 min)
- **Implementation iterations:** 2 (killed during 2nd oracle)
- **Quality gate pass rate:** 0% HTTP scenarios, 100% tier 1 contracts
- **Final verdict:** KILLED — server startup unfixable within pipeline

---

## Attempt 3
_Reserved_

---

## Attempt 4
_Reserved_
