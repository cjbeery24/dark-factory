# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware (Attempt 2)

**Date:** 2026-03-31
**Spec:** `2026-03-30-auth-schemas-middleware.md`
**Target repo:** budget-brain-v2
**Pipeline version:** Post phases 1, 2, 3, 4 from path-to-first-ship.md
**Config:** multi_run_n=3, multi_run_k=2, autonomy=3, enterprise=true
**Previous runs:** 1 (2026-03-30, ESCALATED at 61% after 3 iterations)

---

## Context

Second attempt at running the auth-schemas-middleware spec (F-2a). Previous run escalated at 61% pass rate (21/34 scenarios) with 0% improvement across 3 iterations. Primary blockers were: no migration files generated (no DB in Docker), service pattern mismatches, env validation gaps, and security hardening gaps.

This run uses the same spec without modifications. The goal is to observe whether pipeline behavior has changed after phases 1-4 improvements.

---

## Run 1 (of this session)

**Start time:** 2026-03-31 ~01:40 UTC
**Outcome:** INTERRUPTED — SA validation failed after 3 attempts
**Pipeline stage reached:** Scenario generation (step 1)
**Total duration:** ~20 minutes

### Pre-run state
- budget-brain-v2 repo: reset (tracked files restored, untracked cleaned, .dark-factory artifacts cleared)
- Docker images: rebuilt (all cached)
- Agents/templates: synced from dark-factory

### Timeline

| Time (UTC) | Event |
|------------|-------|
| 01:40 | Pre-run setup begins |
| 01:41 | Spec pre-check: PASS (1 ambiguity warning — "etc." on line 21) |
| 01:42 | Spec validation: PASS (container ran ~2min) |
| ~01:44 | SA generation attempt 1: 4 files, 32 scenarios |
| ~01:47 | SA validation FAIL: `no_source_reads` on 01-migration-schema.test.ts |
| ~01:48 | SA repair: removed failing file, scenarios dir cleared |
| ~01:52 | SA generation attempt 2: 4 new files regenerated |
| ~01:55 | SA validation FAIL: `assertion_upper_bound` on 04-security-adversarial.test.ts (38 assertions, max 30) |
| ~01:56 | SA repair attempt 3 |
| ~01:59 | SA validation FAIL again (same issue persisted) |
| ~02:00 | **INTERRUPTED** — SA validation failed after 3 attempts |

### Scenario Generation Results

**Attempt 1:**
- 4 files: 01-migration-schema.test.ts, 02-middleware-patterns.test.ts, 03-error-conditions.test.ts, 04-security-adversarial.test.ts
- 32 scenarios across 4 files
- SA validation: FAIL (`no_source_reads` — 01-migration-schema.test.ts was reading source files)

**Attempt 2 (after repair):**
- 4 files regenerated with same names
- SA validation: FAIL (`assertion_upper_bound` — 04-security-adversarial.test.ts had 38 assertions, max 30)
- The `no_source_reads` issue was fixed, but a new violation was introduced

**Attempt 3 (after repair):**
- SA validation: FAIL (same `assertion_upper_bound` issue persisted)
- Pipeline terminated after 3 failed validation attempts

### Root Cause Analysis

**SA cannot stay within assertion_upper_bound (30) for security/adversarial scenarios.** The SA generated a comprehensive security file (38 assertions) that exceeds the 30-assertion cap. The SA repair was unable to resolve this — it likely needs to split the file into two, but the repair agent either doesn't know about the cap or doesn't know how to split files.

**Additionally:**
- The first attempt had a `no_source_reads` violation where the migration schema test was reading actual source files — a barrier violation that the SA shouldn't do in artifact-verification tests
- The SA repair cycle is expensive (~3-5 min per attempt) and burns through 3 attempts without resolving the issue
- The repair cleared ALL scenario files and regenerated from scratch, rather than just fixing the problematic file

### Pipeline Behavior Observations

**What worked well:**
- Spec pre-check and validation: quick and clean
- `no_source_reads` detection caught a barrier violation on attempt 1
- SA repair successfully fixed the `no_source_reads` issue in attempt 2
- Multiple validation checks running correctly (assertion bounds, source reads, etc.)

**What needs improvement:**
- **SA repair doesn't address assertion_upper_bound well (critical blocker):** The repair agent fails to split large test files or reduce assertion count. This caused 2 of 3 repair attempts to fail on the same issue.
- **SA repair is destructive:** Clearing all scenario files and regenerating from scratch is wasteful. Only the failing file should be replaced/repaired.
- **No feedback loop between validation failures:** The SA repair doesn't seem to receive the specific validation failure details (e.g., "04-security-adversarial.test.ts has 38 assertions, max is 30").

---

## Run 2 (of this session)

**Start time:** 2026-03-31 ~02:01 UTC
**Outcome:** ESCALATED — 3 iterations, all at 88% pass rate (45/51 scenarios)
**Pipeline stage reached:** Full pipeline (escalation after max iterations)
**Total duration:** ~16 minutes

### Pre-run state
- budget-brain-v2 repo: reset (tracked files restored, untracked cleaned, artifacts cleared)
- Docker images: same (cached)
- Agents/templates: synced

### Timeline

| Time (UTC) | Event |
|------------|-------|
| 02:01 | Pipeline start, spec pre-check PASS |
| ~02:03 | Spec validation: PASS |
| ~02:06 | SA generation: 4 files + tier-1 contracts = 51 total scenarios |
| ~02:07 | SA validation: PASS (all checks pass on first attempt) |
| ~02:07 | Smoke test: all scenarios fail cleanly (healthy baseline) |
| ~02:10 | Iter 1: Implementation complete |
| ~02:13 | Iter 1: Pre-gate FAIL (attempt 1) — build/lint/typecheck all fail |
| ~02:14 | Iter 1: Pre-gate FAIL (attempt 2) — same failures |
| ~02:15 | Iter 1: Quality gate — 88% (45/51), Build/Lint/TC all FAIL |
| ~02:15 | Iter 1: Reviewer FAILED (both attempts), fallback to deterministic pre-pass |
| ~02:15 | Iter 1: Oracle FAILED (both attempts), NO-SHIP by default |
| ~02:16 | Iter 2: Implementer FAILED (both attempts) — no feedback file |
| ~02:16 | Iter 2: Pre-gate FAIL (both attempts) |
| ~02:16 | Iter 2: Quality gate — 88% (45/51) |
| ~02:16 | Iter 2: Reviewer/Oracle FAILED, NO-SHIP |
| ~02:17 | Iter 3: Implementer FAILED (both attempts) |
| ~02:17 | Iter 3: Pre-gate FAIL (both attempts) |
| ~02:17 | Iter 3: Quality gate — 88% (45/51) |
| ~02:17 | Early escalation: trajectory=PLATEAU at iteration 3 |
| ~02:17 | Escalation agent FAILED (both attempts) |
| ~02:17 | **ESCALATED** |

### Scenario Generation Results

SA validation passed on first attempt this time (vs 3 failures in Run 1).

4 scenario files + 1 tier-1 contract file = 51 total scenarios:
- `00-tier1-contracts.test.ts` — 30 tier-1 contract tests (auto-generated)
- `01-contract.test.ts` — 4 scenarios (migration/schema structure)
- `02-error-conditions.test.ts` — 6 scenarios (JWT/auth error handling)
- `03-security.test.ts` — 6 scenarios (secret handling, injection protection)
- `04-edge-cases.test.ts` — 5 scenarios (env validation, malformed headers, JWKS errors)

### Implementation Results

| Iteration | Build | Lint | TypeCheck | Scenarios | Pass Rate | Notes |
|-----------|-------|------|-----------|-----------|-----------|-------|
| 1 | FAIL | FAIL | FAIL | 45/51 | 88% | Pre-gate exhausted |
| 2 | FAIL | FAIL | FAIL | 45/51 | 88% | Implementer failed, no changes |
| 3 | FAIL | FAIL | FAIL | 45/51 | 88% | Implementer failed, no changes |

**What passed (45 scenarios):**
- All 30 tier-1 contract tests
- RLS policy restrictive mode verification
- Migration SQL table creation patterns
- Response data envelope structure
- Token extraction with trim/split safety
- JWT secrets not in log output
- Header injection protection
- RLS tenant isolation enforcement
- Env validation for JWT config at startup
- Malformed Authorization header handling
- JWT missing org_id graceful handling
- Concurrent request safety
- Very long JWT token handling
- Unauthenticated /auth/me returns 401 structured error
- Valid JWT but no user returns USER_NOT_FOUND
- Valid JWT but no tenant returns TENANT_NOT_FOUND

**What failed consistently (6 scenarios):**
1. `JWT with missing sub claim is rejected` — pattern mismatch in middleware
2. `database connection failure during auth is handled with proper error response` — no DB error handling pattern
3. `malformed JWKS response from endpoint is handled gracefully` — no JWKS error handling pattern
4. `expired JWT triggers catch block and returns 401` — try/catch pattern not detected
5. `/health endpoint responds 200 without any auth header` — health route exclusion pattern mismatch
6. `failed auth logs warn-level message with request path but no token content` — logging pattern mismatch

### Root Cause Analysis

**Significant improvement from 61% → 88%.** The implementer produced much better code this time:
- Created `drizzle.config.ts` (missing in prior run)
- Created `drizzle/` migration directory (missing in prior run)
- Created proper auth service, middleware, and route structure
- 30/30 tier-1 contracts passed

**Remaining 6 failures are all code pattern mismatches in the SA scenarios:**
- The scenarios look for specific code patterns (try/catch around JWKS, specific health route exclusion logic) that the implementation handles differently
- Build/lint/typecheck failures are real (TS2532 "possibly undefined" errors, missing .js extensions, lint issues) but the pre-gate fixer couldn't resolve them

**Critical issue: massive agent failures.** Implementer, reviewer, oracle, and escalation agents all failed on their primary attempts. The pipeline relied heavily on retry logic and fallback mechanisms. Specifically:
- Iterations 2 and 3 had implementer failures on both attempts = no code changes
- "feedback file missing for iteration N" = oracle feedback wasn't generated
- Reviewer fallback to deterministic pre-pass scores (no AI review)
- This means iterations 2 and 3 were essentially wasted — same code, same results

### Pipeline Behavior Observations

**What worked well:**
- SA validation passed first try (vs 3 failures in Run 1)
- 88% pass rate (27-point improvement over prior run's 61%)
- Tier-1 contracts: 30/30 passed
- Early escalation detection (trajectory=PLATEAU) worked correctly
- Quality gate correctly reported results despite build failures
- Retry and fallback mechanisms kept pipeline moving

**What needs improvement:**
- **Agent reliability (critical):** Implementer, reviewer, oracle all failed on first attempts. Iterations 2-3 had implementer fail on BOTH attempts, meaning zero code changes were possible.
- **Feedback loop broken:** Oracle failures meant no feedback files, which meant implementer in later iterations had nothing to work with ("implementing from spec only")
- **Pre-gate fixer ineffective:** Could not fix TypeScript strict-null errors or missing file extensions. Both pre-gate attempts failed identically.
- **Build/lint/typecheck never passed:** Real TS errors (possibly undefined, missing .js extensions, lint violations) persisted through all iterations.
- **Wasted iterations:** With implementer failing, iterations 2-3 re-ran quality gate on identical code — pure waste.

---

## Run 3 (of this session)

**Start time:** 2026-03-31 ~02:20 UTC
**Outcome:** ESCALATED — 2 iterations at 79% pass rate (38/48 scenarios), early escalation due to BRITTLE classification
**Pipeline stage reached:** Full pipeline (early escalation after 2 iterations)
**Total duration:** ~38 minutes

### Pre-run state
- budget-brain-v2 repo: reset (tracked files restored, untracked cleaned)
- Docker images: same (cached)
- Agents/templates: synced
- **Note:** Reports from Run 2 were not fully cleaned (leftover iter1-3 category files). Pipeline overwrites what it needs.

### Timeline

| Time (UTC) | Event |
|------------|-------|
| 02:20 | Pipeline start, spec pre-check PASS |
| ~02:22 | Spec validation: PASS |
| ~02:24 | SA generation attempt 1: 3 files (01-error-conditions, 02-security-scenarios, 03-contract-behavior) |
| ~02:27 | SA validation FAIL: `assertion_upper_bound` on 03-contract-behavior.test.ts (45 assertions, max 30) |
| ~02:30 | SA repair: scenarios cleared, regenerated |
| ~02:33 | SA validation PASS (assertion count within bounds after repair) |
| ~02:33 | Smoke test: 0 crashes, 18 tautological — triggered SA repair |
| ~02:37 | SA validation PASS (final) |
| ~02:40 | Iter 1: Implementation complete |
| ~02:43 | Iter 1: Pre-gate FAIL (attempt 1) |
| ~02:50 | Iter 1: Pre-gate PASS (attempt 2) — **Build/Lint/TC all PASS** |
| ~02:52 | Iter 1: Quality gate — 79% (38/48) |
| ~02:54 | Iter 1: Review complete, Oracle: NO-SHIP |
| ~02:57 | Iter 2: Implementation complete |
| ~02:57 | Iter 2: Pre-gate PASS (attempt 1) |
| ~02:58 | Iter 2: Quality gate — 79% (38/48) |
| ~02:58 | Iter 2: Review complete, Oracle: NO-SHIP |
| ~02:58 | **Early escalation:** majority SPEC_AMBIGUOUS/BRITTLE failures (0 SPEC_AMBIGUOUS + 2 BRITTLE out of 3) |
| ~02:59 | Escalation agent complete |
| ~02:59 | **ESCALATED** |

### Scenario Generation Results

SA needed 2 attempts to pass validation (assertion_upper_bound on first attempt, 45 > 30). Then smoke test found 18 tautological scenarios, triggering SA repair. Final result:

3 SA files + 1 tier-1 contract file = 48 total scenarios:
- `00-tier1-contracts.test.ts` — 30 tier-1 contract tests (auto-generated)
- `01-error-conditions.test.ts` — 5 scenarios
- `02-security-scenarios.test.ts` — 3 scenarios
- `03-contract-behavior.test.ts` — 10 scenarios (reduced from 45 to fit within bounds)

**Note:** 18 tautological scenarios detected and removed by smoke test (significant — suggests SA was generating tests that always pass regardless of implementation).

### Implementation Results

| Iteration | Build | Lint | TypeCheck | Scenarios | Pass Rate | Notes |
|-----------|-------|------|-----------|-----------|-----------|-------|
| 1 | PASS | PASS | PASS | 38/48 | 79% | Pre-gate fix worked! |
| 2 | PASS | PASS | PASS | 38/48 | 79% | No improvement |

**What passed (38 scenarios):**
- All 30 tier-1 contract tests (30/30)
- JWT secrets not in log output
- Header injection protection
- RLS restrictive mode verification
- Health endpoint excluded from auth
- Auth routes define /auth/me endpoint
- Auth service uses proper DB schema imports
- Env validation for JWT config
- Middleware excludes non-protected routes

**What failed consistently (10 scenarios):**
1. `auth middleware uses safe token extraction to prevent injection attacks` — pattern mismatch
2. `failed authentication logs warn-level messages without exposing token content` — logging pattern
3. `migration SQL creates both tables with correct column types for security` — column type expectations
4. `auth schemas define correct response data envelope structure` — envelope pattern
5. `auth handlers implement proper response structure` — handler response format
6. `auth middleware handles expired JWT with try/catch error handling` — try/catch detection
7. `auth middleware handles malformed Authorization header safely` — safety check pattern
8. `auth service returns USER_NOT_FOUND for valid JWT with no local user` — error code pattern
9. `auth service returns TENANT_NOT_FOUND for valid JWT with no local tenant` — error code pattern
10. `auth routes return structured error envelope for unauthenticated requests` — envelope format

### Root Cause Analysis

**Build quality improved dramatically:** Pre-gate fixer successfully resolved build/lint/typecheck errors this time (vs Run 2 where they never passed). This is the first run where all three gates passed.

**Pass rate dropped from 88% → 79%** despite better build quality. Reason: different SA scenarios were generated. Run 2 had 51 scenarios (6 failing), Run 3 had 48 scenarios (10 failing). The SA generates different test patterns each time, some more brittle than others.

**BRITTLE classification triggered early escalation.** The pipeline's failure classification identified 2/3 failure categories as BRITTLE (scenarios testing implementation patterns rather than behavioral correctness). This appropriately escalated early rather than wasting a 3rd iteration.

**Key failures are SA pattern brittleness:**
- Most failing tests look for specific regex patterns in source files (e.g., exact try/catch structure, specific log message format) rather than testing actual behavior
- The implementation is functionally correct but doesn't match the SA's expected code patterns

### Pipeline Behavior Observations

**What worked well:**
- **Pre-gate fixer actually worked:** Build/lint/typecheck all PASS for the first time across all runs
- **Early escalation:** BRITTLE classification correctly identified unproductive iterations and cut run short (2 iter vs 3)
- **Reviewer and oracle functioned:** No agent failures (vs Run 2 where all agents failed)
- **SA tautological detection:** Caught 18 always-pass scenarios and removed them
- **Faster iteration:** Each iteration completed more quickly with agents functioning properly
- **Escalation report generated successfully** (failed in Run 2)

**What needs improvement:**
- **SA assertion_upper_bound still a problem:** First attempt failed with 45 assertions (max 30). This is the 3rd run in a row where SA generates files exceeding the cap.
- **SA scenario brittleness:** 10 of 18 SA-generated scenarios are brittle pattern-match tests that check regex against source code rather than testing actual behavior. These fail even when the implementation is functionally correct.
- **No pass rate improvement between iterations:** 79% → 79% (plateau). The implementer received feedback but couldn't fix the pattern mismatches.
- **Different SA scenarios each run makes comparison difficult:** Run 2 (51 scenarios, 88%), Run 3 (48 scenarios, 79%) — the variable scenario set makes it hard to track actual implementation improvement.

---

## Run 4 (of this session)

**Start time:** 2026-03-31 ~03:20 UTC
**Outcome:** INTERRUPTED — SA validation failed after 3 attempts
**Pipeline stage reached:** Scenario generation (step 1)
**Total duration:** ~18 minutes

### Pre-run state
- budget-brain-v2 repo: fully reset (all artifacts, reports, state cleaned)
- Docker images: same (cached)
- Agents/templates: synced

### Timeline

| Time (UTC) | Event |
|------------|-------|
| 03:20 | Pipeline start, spec pre-check PASS |
| ~03:22 | Spec validation: PASS |
| ~03:24 | SA generation attempt 1: 4 files |
| ~03:25 | SA validation FAIL: `no_source_reads` on 3 of 4 files |
| ~03:28 | SA repair attempt 2: 5 files regenerated |
| ~03:30 | SA validation FAIL: `assertion_upper_bound` on 1 file (fixed `no_source_reads`) |
| ~03:33 | SA repair attempt 3: 5 files regenerated |
| ~03:35 | SA validation FAIL: `no_source_reads` on ALL 5 files (regressed!) |
| ~03:35 | **INTERRUPTED** — SA validation failed after 3 attempts |

### Root Cause Analysis

**SA oscillation between two violations:** The SA repair cycle alternates between fixing `no_source_reads` (by removing source file reads) and introducing `assertion_upper_bound` violations (by consolidating into fewer, larger files), or vice versa. On attempt 3, it regressed fully back to `no_source_reads` on all 5 files.

**This confirms the SA repair loop is fundamentally broken for this spec:**
- Attempt 1: `no_source_reads` FAIL (3/4 files)
- Attempt 2: `assertion_upper_bound` FAIL (fixed `no_source_reads`)
- Attempt 3: `no_source_reads` FAIL (5/5 files — full regression)

The SA cannot simultaneously satisfy both constraints (no source reads + max 30 assertions per file) for this spec's complexity level.

### Pipeline Behavior Observations

**SA repair is in a deadlock for this spec.** The spec requires testing auth middleware, database schemas, and security patterns — topics that naturally tempt the SA into either reading source files or writing comprehensive test suites that exceed assertion limits. The repair agent lacks the meta-awareness to balance both constraints simultaneously.

---

## Progression Summary

| Run | Outcome | Pass Rate | Scenarios | Build/Lint/TC | Key Blocker |
|-----|---------|-----------|-----------|---------------|-------------|
| Prior (2026-03-30) | ESCALATED (3 iter) | 61% | 34 | Mixed | No migration files, service pattern mismatches |
| 1 | INTERRUPTED (SA) | N/A | — | N/A | assertion_upper_bound (38 > 30) |
| 2 | ESCALATED (3 iter) | 88% | 51 | All FAIL | Agent failures, 6 pattern mismatches |
| 3 | ESCALATED (2 iter) | 79% | 48 | All PASS | SA brittleness, 10 pattern mismatches |
| 4 | INTERRUPTED (SA) | N/A | — | N/A | no_source_reads ↔ assertion_upper_bound oscillation |

### Cross-Run Observations

1. **SA validation is the #1 pipeline reliability issue:** 2 of 4 runs (50%) were INTERRUPTED before implementation because SA couldn't generate valid scenarios. The SA oscillates between `no_source_reads` and `assertion_upper_bound` violations.
2. **SA scenario variability causes inconsistent pass rates:** 61% → 88% → 79% across runs that reached implementation, with different scenario sets each time. Hard to isolate implementation vs scenario quality.
3. **Build quality improved significantly:** Run 3 achieved Build/Lint/TC all PASS (first time ever). Pre-gate fixer is effective when given the chance.
4. **Agent reliability is inconsistent:** Run 2 had massive agent failures (impl/reviewer/oracle); Run 3 had none. Intermittent, not systematic.
5. **Pattern-match brittleness is the dominant failure mode for implementations that run:** SA scenarios use regex to check source code patterns rather than testing behavior. Fundamental limitation of Format D (artifact-verification) testing.
6. **SA repair loop needs architectural improvement:** The current repair strategy (clear all files, regenerate from scratch) doesn't work when multiple constraints conflict. The repair agent needs to:
   - Receive specific failure details with actionable guidance
   - Fix only the failing file(s), not regenerate everything
   - Be aware of constraint interactions (source reads vs assertion count)

### Recommendations for Pipeline Improvement

1. **Increase SA repair iterations** from 3 to 5, or implement targeted repair (fix only failing file)
2. **Pass validation errors directly to the SA repair prompt** with explicit constraint values (e.g., "file X has 38 assertions, max is 30 — split into 2 files")
3. **Consider raising assertion_upper_bound** to 40-50 for security-heavy specs, or make it configurable per spec
4. **Address Format D brittleness** — investigate whether scenarios can use more robust assertion patterns (AST-based, structural) instead of regex matching against source code
5. **Stabilize SA scenario generation** — current run-to-run variability (34 to 51 scenarios, 61% to 88% pass rate) makes it impossible to measure implementation improvement

---
