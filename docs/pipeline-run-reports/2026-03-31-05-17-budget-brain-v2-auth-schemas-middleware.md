# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware (Session 3)

**Date:** 2026-03-31
**Spec:** `2026-03-30-auth-schemas-middleware.md`
**Target repo:** budget-brain-v2
**Pipeline version:** Current main
**Previous sessions:** 2 (2026-03-30: ESCALATED 61%; 2026-03-31 01:40: 4 runs — 2 INTERRUPTED at SA, 1 ESCALATED 88%, 1 ESCALATED 79%)

---

## Context

Third session attempting the auth-schemas-middleware spec (F-2a). Key patterns from prior sessions:
- SA validation is the #1 blocker — 50% of runs interrupted before implementation (oscillation between `no_source_reads` and `assertion_upper_bound`)
- Best pass rate: 88% (51 scenarios, but build/lint/TC all FAIL)
- Best build quality: 79% pass rate with build/lint/TC all PASS
- Pattern-match brittleness in Format D scenarios is the dominant implementation failure mode
- Agent reliability varies wildly between runs (Run 2 had massive failures, Run 3 had none)

---

## Run 1

**Start time:** 2026-03-31 ~05:17 UTC
**Outcome:** INTERRUPTED — manually killed during iteration 1 (implementer running)
**Pipeline stage reached:** Iteration 1 implementation (step 2 complete, implementer active)
**Total duration:** ~20 minutes (05:17 — 05:37 UTC)

### Pre-run state
- budget-brain-v2 repo: fully reset (tracked files restored, untracked cleaned)
- .dark-factory artifacts: all auth-schemas-middleware artifacts removed (scenarios, reports, state, reviews, audit)
- Docker images: rebuilt (all cached)
- Agents/templates: synced from dark-factory

### Timeline

| Time (UTC) | Event |
|------------|-------|
| 05:17 | Pipeline start, spec pre-check |
| ~05:19 | Spec validation: PASS (container completed in ~2 min) |
| ~05:19 | SA generation attempt 1: 4 files (01-migration-security, 02-environment-validation, 03-error-conditions, 04-injection-security) |
| ~05:25 | SA validation FAIL (attempt 1) — specific violation unknown, scenarios cleared |
| ~05:27 | SA repair attempt 2: 4 files regenerated |
| ~05:32 | SA validation PASS (attempt 2) |
| ~05:32 | Tier-1 contracts generated (00-tier1-contracts.test.ts) |
| ~05:32 | Smoke test: 0 crashes, 1 tautological — triggered SA repair |
| ~05:33 | SA tautological repair: 01-migration-security.test.ts regenerated |
| ~05:35 | Implementer container started (iteration 1) |
| ~05:37 | **INTERRUPTED** — manually killed to investigate Format D issue |

### Scenario Generation Results

SA required 2 attempts to pass validation, then smoke test found 1 tautological scenario requiring repair. Final scenario set:

5 files, 52 total scenarios:
- `00-tier1-contracts.test.ts` — 30 tier-1 contract tests (auto-generated)
- `01-migration-security.test.ts` — 4 scenarios (RLS policy structure, migration SQL)
- `02-environment-validation.test.ts` — 5 scenarios (JWT config, startup env checks)
- `03-error-conditions.test.ts` — 6 scenarios (JWT error handling, DB failures)
- `04-injection-security.test.ts` — 7 scenarios (header injection, logging security)

### Key Observation: Format D (Artifact-Verification) Tests Only

**All SA-generated scenarios are Format D — they read source files with `readFileSync` and assert against regex patterns. No Format A (behavioral) tests were generated.**

Example patterns from the scenarios:
- `01-migration-security.test.ts`: reads `drizzle/*.sql` files, checks for `CREATE POLICY` regex patterns
- `04-injection-security.test.ts`: reads `apps/api/src/features/auth/*.ts`, checks for logging patterns via regex
- `02-environment-validation.test.ts`: reads source files, checks for env variable validation patterns
- `03-error-conditions.test.ts`: reads middleware/service files, checks for try/catch and error handling patterns

**Why Format A isn't generated:**

The spec covers auth schemas + JWT middleware + `/auth/me` endpoint — all features that require a running application (HTTP server, database connection, JWT verification) for behavioral testing. The SA container cannot run the app because:

1. **Source directories are masked** (`--tmpfs /project/src:ro,size=0`) — the SA can't see or build the code
2. **No database or runtime infrastructure** in the SA container — can't start the API server
3. **Read-only project mount** — can't install dependencies or build
4. **The SA correctly falls back to Format D** given these constraints, but Format D is fundamentally limited for this spec type

**This is the root cause of the pattern-match brittleness observed across all sessions.** The implementation can be functionally correct (handles JWT validation, returns proper error responses, enforces RLS) but still fail because the source code doesn't match the SA's expected regex patterns (e.g., exact try/catch structure, specific variable names, particular file paths).

### Implication for Pipeline

For specs that define HTTP endpoints, database schemas, and middleware (i.e., most backend features), Format D testing will always produce brittle scenarios. The pipeline needs Format A (behavioral) test capability — which requires the SA container to either:
1. Generate tests that run against the actual application (requiring a service sidecar or test harness), or
2. Have access to a test execution environment where the app can be started

Without this, the auth-schemas-middleware spec (and similar backend specs) will continue to hit a ceiling where functionally correct implementations fail on code pattern mismatches.

---

