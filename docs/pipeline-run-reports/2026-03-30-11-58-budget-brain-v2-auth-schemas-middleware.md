# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware

**Date:** 2026-03-30
**Spec:** `2026-03-30-auth-schemas-middleware.md`
**Target repo:** budget-brain-v2
**Pipeline version:** Post phases 1, 2, 3, 4 from path-to-first-ship.md
**Config:** multi_run_n=3, multi_run_k=2, autonomy=3, enterprise=true
**Previous runs:** None (first attempt for this spec)

---

## Context

This is the first pipeline run for the auth-schemas-middleware spec (F-2a). This spec defines:
- Tenant and User database schemas with RLS
- JWT authentication middleware using `jose`
- GET `/api/v1/auth/me` endpoint

The previous spec (user-settings-preferences) was ESCALATED after 3 iterations at 41% pass rate. This is a different, more foundational spec. The repo is clean with only the monorepo scaffold from F-1a.

**Spec pre-check issues (fixed before Run 1):**
- AC-4 through AC-8 had missing WHEN clauses (regex pre-check requirement)
- FR-3 had only 4 scenario intents (minimum 5) — added EC-2 intent
- Committed as `4405fb2`

---

## Run 1

**Start time:** 2026-03-30 ~12:07 (local)
**Outcome:** ESCALATED — 3 iterations, all at 61% pass rate (21/34 scenarios)
**Pipeline stage reached:** Full pipeline (escalation after max iterations)
**Total duration:** ~35 minutes

### Pre-run state
- budget-brain-v2 repo: clean (no modified tracked files, spec fix committed)
- No prior .dark-factory artifacts for this spec
- Docker images: rebuilt (all cached layers)
- Agents/templates: synced from dark-factory
- Config: config.toml (no config.sh)

### Timeline

| Time (local) | Event |
|------------|-------|
| 12:07 | Spec pre-check: PASS (1 ambiguity warning, non-blocking) |
| ~12:00 | Spec validation: PASS (score=92) |
| ~12:05 | SA generation: 5 files, 42 scenarios total |
| ~12:07 | SA validation + smoke test (3 tautological, SA repair triggered) |
| ~12:07 | Workspace check: PASS |
| ~12:12 | Iter 1: Pre-gate FAIL (attempt 1), then PASS (attempt 2) |
| ~12:20 | Iter 1: Quality gate — 61% (21/34), Build/Lint/TC all PASS |
| ~12:23 | Iter 1: Oracle: NO-SHIP |
| ~12:26 | Iter 2: Pre-gate PASS (attempt 1) |
| ~12:27 | Iter 2: Quality gate — 61% (21/34) |
| ~12:31 | Iter 2: Oracle: NO-SHIP |
| ~12:38 | Iter 3: Pre-gate FAIL (attempt 1), PASS (attempt 2) |
| ~12:41 | Iter 3: Quality gate — 61% (21/34) |
| ~12:42 | Escalation agent runs |
| ~12:42 | **ESCALATED** |

### Scenario Generation Results

5 scenario files, 34 effective scenarios (after SA repair removed 3 tautological + some adjustment):
- `01-database-schemas.test.ts` — schema structure verification
- `02-migrations.test.ts` — migration file content validation
- `03-auth-middleware.test.ts` — middleware behavior verification
- `04-api-routes.test.ts` — route/handler/schema verification
- `05-security-edge-cases.test.ts` — security and adversarial patterns

SA validation: all checks PASS. Smoke test caught 3 tautological scenarios and triggered SA repair.

### Implementation Results

| Iteration | Build | Lint | TypeCheck | Scenarios | Pass Rate |
|-----------|-------|------|-----------|-----------|-----------|
| 1 | FAIL->PASS | FAIL->PASS | FAIL->PASS | 21/34 | 61% |
| 2 | PASS | PASS | PASS | 21/34 | 61% |
| 3 | FAIL->PASS | FAIL->PASS | FAIL->PASS | 21/34 | 61% |

**What passed consistently (21 tests):**
- Auth middleware file existence and hook export
- Fastify request type augmentation
- Auth schemas with Zod validation
- Health endpoint exclusion from protection
- Structured error envelope format
- Auth routes and handlers file existence
- Response schema with user/tenant fields
- Route registration with main app
- User/tenant response schemas
- Error codes definition
- JWT secrets not hardcoded
- RLS policy in database schema
- User/tenant lookup graceful handling
- JWT expired token handling
- JWT claims validation
- Tenants/users schema files existence
- ClerkId unique constraint

**What failed consistently (13 tests, identical across all 3 iterations):**

*Migration/Drizzle (5):*
- `migration file exists and contains required SQL statements`
- `migration enables RLS on users table`
- `migration creates RLS policy for tenant isolation`
- `drizzle config file exists`
- `schema barrel export file exists`

*Code patterns (8):*
- `auth service file exists with user lookup logic` (path/pattern mismatch)
- `environment validation includes JWT config`
- `environment validation requires at least one JWT verification method`
- `response structure follows data envelope pattern`
- `logging configuration excludes sensitive data`
- `middleware handles malformed Authorization headers`
- `auth middleware protects against header injection`
- `users schema references tenants table correctly`

### Root Cause Analysis

**Implementer stagnation:** 0% improvement across 3 iterations. The same 13 tests failed every time.

**Primary blockers:**
1. **No migration files generated.** The implementer wrote Drizzle schemas but never ran `drizzle-kit generate` to create SQL migration files. The Docker container likely lacks a database connection or `drizzle-kit` binary, making this impossible.
2. **No drizzle.config.ts.** Required for migration generation but not created.
3. **Schema barrel export path mismatch.** The scenario expects `apps/api/src/db/schema/index.ts` to re-export, but the implementer may have used a different barrel pattern.
4. **Service file pattern mismatch.** The scenario looks for specific patterns (e.g., `findByClerkId`) in the auth service file.
5. **Environment validation gaps.** No startup validation that at least one of `CLERK_JWKS_URL` or `JWT_SECRET` is configured.
6. **Security hardening gaps.** No `trim()` on Bearer token extraction, no logging exclusion patterns for sensitive data.

### Pipeline Behavior Observations

**What worked well:**
- Spec validation: clean pass
- SA generation: good quality scenarios, tautological detection and repair worked
- Pre-gate retry mechanism: successfully caught and fixed build/lint/typecheck errors
- Build/lint/typecheck all PASS by end of each iteration
- Information barrier: no leakage detected
- 61% pass rate is a 20-point improvement over user-settings-preferences (41%)

**What needs improvement:**
- **Implementer stagnation (critical):** Zero improvement across 3 iterations. The oracle feedback addressed the right issues but the implementer couldn't execute the fixes.
- **Migration generation impossible in Docker:** The implementer can't run `drizzle-kit generate` without a database. This is an architectural limitation — the spec requires migration files but the implementation container has no DB.
- **Pre-gate oscillation:** Build/lint/typecheck failed on iterations 1 and 3 (passed on retry), suggesting the implementer introduces regressions.

---

## Progression Summary

| Run | Outcome | Pass Rate | Key Blocker |
|-----|---------|-----------|-------------|
| 1 | ESCALATED (3 iter) | 61% (stable) | No migration files, service pattern mismatches, env validation gaps |

---
