# Pipeline Run Report: budget-brain-v2 / 2026-03-30-auth-schemas-middleware

**Date:** 2026-04-01  
**Repo:** budget-brain-v2  
**Spec:** 2026-03-30-auth-schemas-middleware  
**Goal:** Track pipeline improvement/regression metrics across runs

---

## Attempt #1

**Started:** 2026-04-01 16:27 UTC

### Pre-run Setup
- Updated agents/templates from dark-factory source
- Rebuilt Docker images
- Repo state: clean (no untracked files, no prior auth-spec artifacts)

### Pipeline Timeline

| Step | Duration (approx) | Result |
|------|-------------------|--------|
| Spec Validation | ~1 min | PASS (1 ambiguity warning: "etc.") |
| Architecture Manifest | ~4 min | PASS (manifest generated, postgresql env warning) |
| Scenario Architect | ~3 min | PASS (30 tests in 1 tier-1 file + 4 scenario docs) |
| Implementer | ~8 min | Completed (created auth feature: middleware, handlers, routes, schemas, service, db schemas, drizzle config, env config) |
| Quality Gate | ~1 min | PASS (build, lint, typecheck all pass; 30/30 blind scenarios pass; Server Setup FAIL) |
| Reviewer | ~2 min | APPROVE (85/100) |
| Oracle | immediate | **NO-SHIP** |
| Iteration 2 Implementer | started | Killed by user request |

### Key Findings

#### 1. NO-SHIP Root Cause: Drizzle Config Misplacement
- The implementer placed `drizzle.config.ts` at the monorepo root (`/project/drizzle.config.ts`)
- The `server_setup_cmd` runs `cd apps/api && pnpm drizzle-kit push:pg` — drizzle-kit looks for config relative to `apps/api/` and finds nothing
- Error: `No config path provided, using default path — /project/apps/api/drizzle.config.json file does not exist`
- This blocked server startup → 0% HTTP pass rate → Oracle NO-SHIP

#### 2. Quality Gate Blind Spot
- The quality gate reported "Blind Scenarios (executable) | PASS" with 30/30 tests passing
- However, all 30 tests were **static file existence/content checks** (readFileSync + pattern matching)
- No HTTP-level behavioral tests could execute because the server couldn't start
- The "Server Setup | FAIL" was noted in the report but didn't block the PASS verdict
- **Pipeline concern:** The quality gate PASS is misleading when the server can't start — static checks passing without runtime verification gives false confidence

#### 3. Reviewer Score vs Reality
- Reviewer gave 85/100 and APPROVE
- But the reviewer noted 0% satisfaction score and "potential gaps" without catching the critical server startup failure
- The feedback-iter1 file (written post-Oracle) correctly identified the drizzle config as the blocking issue

#### 4. Scenario Architecture
- SA generated only tier-1 (static) contracts — 30 file existence/content checks
- No tier-2+ HTTP scenarios were generated, meaning even if the server worked, there would be no runtime behavioral testing
- The scenarios checked correct file paths (`apps/api/src/features/auth/auth.handlers.ts`, etc.) which the implementer delivered

#### 5. Implementer Quality
- Code structure was good: clean separation (middleware, service, handlers, schemas)
- All static quality gates passed (build, lint, typecheck)
- The drizzle config placement was the single critical mistake
- Modified files: `apps/api/package.json`, `apps/api/src/index.ts`, `pnpm-lock.yaml` + many new files

### Metrics

| Metric | Value |
|--------|-------|
| Total iterations before kill | 1 complete + start of 2 |
| Blind scenario pass rate | 100% (30/30) — but all static |
| HTTP scenario pass rate | N/A — server couldn't start |
| Reviewer score | 85/100 |
| Oracle decision | NO-SHIP |
| Pipeline config issues | `server_setup_cmd` path vs drizzle config location mismatch |

### Outcome

**Killed after iteration 1 NO-SHIP** at user request. The pipeline correctly identified the server startup failure via the Oracle, but the quality gate PASS was misleading since it only validated static file checks. The core issue (drizzle config location) is an implementer mistake that the iteration loop should theoretically fix in iteration 2.
