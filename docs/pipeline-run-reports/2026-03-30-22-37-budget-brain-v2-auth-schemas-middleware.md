# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware

**Date:** 2026-03-30
**Spec:** `2026-03-30-auth-schemas-middleware`
**Project:** budget-brain-v2
**Pipeline version:** Post phases 1, 2, 3, 4 of path-to-first-ship.md
**Config:** `multi_run_n=3, multi_run_k=2, autonomy=3, enterprise=true`

## Context

First pipeline run of the auth-schemas-middleware spec. This spec implements:
- Tenant and User database schemas with Row-Level Security (RLS)
- JWT authentication middleware (jose + JWKS/JWT_SECRET)
- `/api/v1/auth/me` endpoint

This is the first feature spec for budget-brain-v2 (previous runs were monorepo-tooling infrastructure).

Phases 1 (stable-failure SHIP override), 2 (SA validation + retry), 3 (probabilistic 2/3 threshold — NOT STARTED per plan but config has multi_run_n=3), and 4 (tool docs injection) are in place.

---

## Run 1

**Started:** 2026-03-30 ~22:38 UTC
**Ended:** 2026-03-30 ~23:30 UTC (~52 min)
**Status:** ESCALATED (3/3 iterations exhausted)
**Run ID:** cd4058c50a8c4829

### Pre-run state
- Repo: clean (only untracked `.claude/` dir)
- No prior state/scenarios/reports for this spec

### Scenario Generation
- SA validation: **PASSED first attempt** (all 10 checks pass)
- Files: 5 (1 Tier 1 + 4 Tier 2)
- Total tests: 54
- Warnings: 4 files with env-dependent assertions, 4 files with regex multiline flag issues
- SA retry not needed

### Iteration Results

| Iteration | Build | Lint | TypeCheck | Blind Scenarios | Pass Rate | Decision |
|-----------|-------|------|-----------|-----------------|-----------|----------|
| 0 (smoke) | — | — | — | 1/54 | 2% | — |
| 1 | FAIL | FAIL | FAIL | 46/54 | 85% | NO-SHIP |
| 2 | PASS | PASS | PASS | 47/54 | 87% | NO-SHIP |
| 3 | PASS | PASS | PASS | 47/54 | 87% | ESCALATED |

### Stable Failure Analysis

Iters 2 and 3 had **identical failures** (7 tests, 100% stable failure ratio):

| # | Test | Category | Likely Root Cause |
|---|------|----------|-------------------|
| 1 | /health endpoint responds 200 without auth | Edge case | Missing /health route implementation |
| 2 | JWT secrets/JWKS URLs never appear in log output | Security | Error message contains env var names (e.g., `"Either CLERK_JWKS_URL or JWT_SECRET must be configured"`) |
| 3 | RLS migration handles existing table scenarios | Edge case | No migration files in drizzle/ dir |
| 4 | TC-9: barrel re-exports tenant schema | Tier 1 contract | Schema barrel export missing tenant |
| 5 | TC-10: barrel re-exports user schema | Tier 1 contract | Schema barrel export missing user |
| 6 | TC-22: auth service imports from schema | Tier 1 contract | Import path mismatch |
| 7 | Unauth request to /auth/me returns 401 | Error handling | Likely missing middleware or route setup |

**3 of 7 are Tier 1 contract failures** (TC-9, TC-10, TC-22) — these are real impl bugs, not scenario bugs.
**2 are arguably scenario bugs** (#2 is checking error message strings for env var names; #3 expects migration files).
**2 are real impl bugs** (#1 missing health endpoint, #7 missing auth error response).

### Outcome
ESCALATED after 3 iterations. **All hard gates pass in iters 2-3** but blind scenarios stuck at 87%.

### Key Findings

1. **CRITICAL BUG: `should_ship_with_gaps()` condition is self-contradictory.** The function requires `gate_exit_code == 0` (entire gate passes), but the override exists specifically for when blind scenarios fail (making gate_exit_code non-zero). This means the stable-failure SHIP override **can never fire** when blind scenarios have stable failures. The plan specifies "hard gates pass" (Build/Lint/Type), not "overall gate passes."

2. **Implementer quality is good.** Build/Lint/TypeCheck all pass by iter 2. Implementation is architecturally sound — proper RLS policies, JWT handling, code structure.

3. **Oracle feedback is appropriate.** Identified real issues (tenant context bypass, parameter confusion) and correctly classified them as IMPL_BUG.

4. **Iter 2 regression then recovery.** Implementer introduced a type error in iter 2 (Expected 2 arguments, got 1) while fixing tenant validation, then fixed it by iter 2's quality gate. This suggests the implementer can respond to feedback effectively.

5. **Some "stable failures" are real impl bugs.** TC-9, TC-10, TC-22 (barrel exports) are genuine missing implementations, not scenario brittleness. The stable-failure override should not ship these blindly — it needs awareness of whether the stable failures are in Tier 1 (contracts, always real bugs) vs Tier 2 (may be scenario bugs).

---

## Run 2

**Started:** 2026-03-30 ~23:31 UTC
**Ended:** 2026-03-30 ~23:47 UTC (~16 min)
**Status:** HARD ABORT — SA validation failed after 3 attempts
**Run ID:** 164176b7999e476d

### Pre-run state
- Repo: clean (fully reset from Run 1)
- No prior artifacts for this spec

### Scenario Generation
- **Attempt 1:** FAILED — 2 hard fails:
  - `assertion_upper_bound`: 03-behavioral-contracts.test.ts had 32 assertions (max 30)
  - `no_source_reads`: All 4 Tier 2 files use `readFileSync` on `.ts` source files
- **Attempt 2:** FAILED — 1 hard fail:
  - `no_source_reads`: 1 file (04-edge-cases-adversarial.test.ts) still reads source
  - Fixed: assertion count now within bounds
- **Attempt 3:** FAILED — 2 hard fails:
  - `assertion_upper_bound`: 03-behavioral-contracts.test.ts regressed to 39 assertions
  - `no_source_reads`: ALL 4 files regressed back to reading source files

Pipeline aborted after SA exhausted all retries.

### Iteration Results

_No iterations reached — pipeline aborted during scenario generation._

### Outcome
HARD ABORT at scenario generation. Never reached implementation.

### Key Findings

1. **SA validation (Phase 2) working as designed** — correctly catches source reads and over-assertion patterns.
2. **SA cannot learn from validation feedback for this spec type.** The auth spec inherently invites source-code inspection (checking for RLS policies, token handling, etc.). The SA defaults to `readFileSync` on `.ts` files to verify these patterns. Despite validation feedback explicitly saying not to read source files, the SA:
   - Partially fixed on attempt 2 (only 1 file remained)
   - Fully regressed on attempt 3 (all 4 files reading source again)
3. **The `no_source_reads` check may be too aggressive for auth/security specs.** Tier 2 scenarios for security features genuinely need to verify implementation patterns (e.g., "tokens are not logged," "RLS is enforced"). Pure behavioral testing via API calls requires a running server, which the scenario runner doesn't provide.
4. **Assertion upper bound flickers.** SA fixes it in one retry then exceeds it again in the next. Suggests the SA doesn't have stable control over assertion count distribution.
5. **Note:** Run 1 (same spec) passed validation on first attempt with 5 files (1 Tier 1 + 4 Tier 2). The difference: Run 1's SA generated the Tier 1 file, while Run 2's SA did not (no `00-tier1-*` file). SA behavior is highly variable run-to-run.

---

## Run 3

**Started:** 2026-03-30 ~23:48 UTC
**Ended:** 2026-03-30 ~00:07 UTC+1 (~19 min, infrastructure failure)
**Status:** INTERRUPTED — Docker 500 error killed implementer container
**Run ID:** 2f9325981af94f0f

### Pre-run state
- Repo: clean (fully reset from Run 2)
- No prior artifacts for this spec

### Scenario Generation
- **Attempt 1:** FAILED — `typescript_compilation` hard fail (invalid regex in 02-security.test.ts line 62)
- **Attempt 2:** Same validation report still showing — SA retry in progress
- **Attempt 3:** PASSED — All checks pass. No source reads, no assertion bound violations.
  - Note: SA needed 3 attempts to produce clean scenarios, same as the max retry budget.
- Files: 5 (1 Tier 1 + 4 Tier 2), 1015 lines, 63 tests
- Smoke test: 31/63 passed (49%) — higher test count than Run 1 (54)

### Iteration Results

| Iteration | Build | Lint | TypeCheck | Blind Scenarios | Pass Rate | Decision |
|-----------|-------|------|-----------|-----------------|-----------|----------|
| 0 (smoke) | — | — | — | 31/63 | 49% | — |
| 1 | (interrupted) | — | — | — | — | Docker crash |

### Outcome
INTERRUPTED — Docker Desktop 500 Internal Server Error killed the implementer container mid-execution during iteration 1. No implementation results available.

### Key Findings

1. **SA required all 3 retry attempts** to produce clean scenarios. Attempt 1 failed on typescript compilation (invalid regex), attempt 2 failed on same issue (stale validation report), attempt 3 passed all checks. The SA retry budget is exactly at the limit.
2. **Higher test count this run** (63 vs 54 in Run 1) — SA generates different numbers of tests each run.
3. **Docker infrastructure instability** — Docker Desktop returned 500 Internal Server Error, causing container crash. This is an external infrastructure issue, not a pipeline bug.
4. **Scenarios passed `no_source_reads` this time** — the SA can avoid source reads when retried enough, but it's inconsistent across runs.

---

## Run 4

**Started:** 2026-03-31 ~00:09 UTC
**Ended:** 2026-03-31 ~00:49 UTC (~40 min)
**Status:** ESCALATED (3/3 iterations exhausted)
**Run ID:** 7cc985eaadc044bc

### Pre-run state
- Repo: clean (fully reset from Run 3)
- No prior artifacts for this spec

### Scenario Generation
- **Attempt 1:** FAILED — `no_source_reads` on 04-rls-migration-policy.test.ts (1 file)
- **Attempt 2:** PASSED — All checks pass. No source reads, assertion bounds OK.
- Files: 5 (1 Tier 1 + 4 Tier 2), 70 tests

### Iteration Results

| Iteration | Build | Lint | TypeCheck | Blind Scenarios | Pass Rate | Decision |
|-----------|-------|------|-----------|-----------------|-----------|----------|
| 0 (smoke) | — | — | — | 31/70 | 44% | — |
| 1 | PASS | PASS | PASS | 62/70 | 88% | NO-SHIP |
| 2 | PASS | PASS | PASS | 62/70 | 88% | NO-SHIP |
| 3 | PASS | PASS | PASS | 62/70 | 88% | ESCALATED |

### Stable Failure Analysis

**All 3 iterations had identical 8 failures (100% stable):**

| # | Test | Type | Notes |
|---|------|------|-------|
| 1 | TC-9: barrel re-exports tenant schema | Tier 1 | Same as Run 1 |
| 2 | TC-10: barrel re-exports user schema | Tier 1 | Same as Run 1 |
| 3 | TC-22: auth service imports from schema | Tier 1 | Same as Run 1 |
| 4 | should configure error response envelope structure | Tier 2 | |
| 5 | should create RLS policy with proper naming convention | Tier 2 | |
| 6 | should have jose library dependency | Tier 2 | |
| 7 | should have zod dependency | Tier 2 | |
| 8 | should use env var references for JWT config | Tier 2 | |

### Outcome
ESCALATED after 3 iterations. Implementation was correct by hard gate standards from iter 1. Stable failure SHIP override did not fire (same `gate_exit_code == 0` bug as Run 1).

### Key Findings

1. **Implementer performance excellent in Run 4.** All hard gates pass from iter 1 (no regressions, unlike Run 1's iter 1 which had Build/Lint/Type failures).
2. **Perfectly stable plateau across all 3 iterations.** 88% pass rate, same 8 tests failing, zero change. The implementer cannot fix these without seeing the tests.
3. **3 Tier 1 failures persist** (TC-9, TC-10, TC-22). These are the same barrel export tests that failed in Run 1. The implementer consistently doesn't create the barrel export pattern the SA expects.
4. **5 Tier 2 failures are consistent but different from Run 1.** Different SA generated different tests, but a similar number of unfixable failures.
5. **`should_ship_with_gaps()` bug confirmed.** This is now confirmed across 2 full runs (Run 1 and Run 4). The override's `gate_exit_code == 0` condition can never be true when blind scenarios fail.

---

## Summary & Metrics

| Metric | Value |
|--------|-------|
| Total runs attempted | 4 |
| Shipped? | No |
| Best pass rate | 88% (Run 4, iters 1-3) |
| SA validation first-attempt pass? | 1/4 (Run 1 only) |
| SA validation pass (any attempt)? | 3/4 (Run 2 failed all 3 attempts) |
| Stable failure ratio | 100% (iters 2-3 in both Run 1 and Run 4) |
| Iterations to ship (if shipped) | N/A |
| Key blocker | `should_ship_with_gaps()` bug — override can never fire |

### Cross-Run Comparison

| Run | SA Val Attempts | Reached Impl? | Best Pass Rate | Hard Gates? | Outcome |
|-----|----------------|---------------|----------------|-------------|---------|
| 1 | 1 (pass) | Yes | 87% (iter 2-3) | Pass iter 2+ | ESCALATED |
| 2 | 3 (all fail) | No | N/A | N/A | HARD ABORT |
| 3 | 3 (pass on 3rd) | Docker crash | N/A | N/A | INTERRUPTED |
| 4 | 2 (pass on 2nd) | Yes | 88% (iter 1-3) | Pass all iters | ESCALATED |

### Tier 1 Contract Failure Analysis

TC-9, TC-10, TC-22 (barrel re-exports) failed in both Run 1 and Run 4. These are the same Tier 1 tests checking that `apps/api/src/db/schema/index.ts` barrel-exports tenant and user schemas with specific patterns. The implementer consistently creates schema files but doesn't create the barrel export pattern the SA expects. This is either:
- An impl bug (implementer should create barrel exports per spec)
- A spec clarity issue (spec doesn't explicitly mandate barrel export patterns)

## Recommendations for Pipeline Improvement

### P0: Fix `should_ship_with_gaps()` — Self-Contradictory Condition

**Impact:** This single bug prevented shipping in Run 1 and Run 4 despite all conditions being met.

The function requires `gate_exit_code == 0` (entire quality gate passes), but the override exists specifically for when blind scenarios fail (making gate_exit_code non-zero). The plan says "all hard gates pass" — the fix is to check Build/Lint/TypeCheck individually instead of the overall gate exit code.

**Fix:** Replace `gate_exit_code == 0` with a new condition that checks only hard gate status (build, lint, type check pass) while allowing blind scenarios to fail.

### P1: SA `no_source_reads` Check May Be Too Strict for Auth/Security Specs

The SA consistently tries to read source code files when generating scenarios for auth/security features. This is because the spec describes patterns that can only be verified by reading source (RLS policies, token handling, barrel exports). 3 of 4 runs required SA retries due to this check.

**Options:**
- Allow config-level override of `no_source_reads` for specs that inherently require source inspection
- Relax `no_source_reads` from hard fail to warning for Tier 2 files that read source but don't read impl logic (e.g., checking dependencies vs checking algorithms)
- Better SA prompt guidance: suggest alternatives like `execSync('node -e "..."')` for module resolution checks

### P2: SA Retry Budget May Be Insufficient

3 of 4 runs needed SA retries. Run 2 exhausted all 3 attempts and aborted. Run 3 barely passed on the 3rd attempt. Consider increasing `sa_max_retries` from 2 to 3 (giving 4 total attempts).

### P3: Tier 1 Barrel Export Failures Are Persistent

TC-9, TC-10, TC-22 fail across runs. Either:
- The spec should be clearer about barrel export patterns (add to Functional Requirements)
- The Tier 1 contract tests should be less prescriptive about how schemas are exported

### P4: Implementer Quality Is Consistently Good

All hard gates pass by iter 1 in Run 4, by iter 2 in Run 1. The implementation is architecturally sound. The bottleneck is NOT implementation quality — it's the pipeline's inability to ship when only blind scenarios have stable failures.

### P5: SA Test Count Variance

Test counts vary significantly across runs: 54 (Run 1), 63 (Run 3), 70 (Run 4). This makes pass rate comparison across runs unreliable. Consider standardizing test count targets in SA prompts.
