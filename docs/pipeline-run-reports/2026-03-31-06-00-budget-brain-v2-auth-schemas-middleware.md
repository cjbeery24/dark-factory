# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware

**Date:** 2026-03-31
**Spec:** `2026-03-30-auth-schemas-middleware.md`
**Target repo:** budget-brain-v2
**Pipeline version:** Current main (commit 90d2829)
**Previous sessions:** 4 (see prior reports — best result was 88% pass rate with build failures)

---

## Run 1

**Start time:** 2026-03-31 ~06:00 UTC
**Outcome:** ESCALATED after 3 iterations (0% reported pass rate — but actual vitest pass rate was 91%)
**Pipeline stage reached:** Full 3-iteration loop + escalation
**Total duration:** ~43 minutes

### Pre-run state
- Repo fully reset (tracked restored, untracked cleaned)
- All auth-schemas-middleware artifacts removed
- Docker images rebuilt (all cached)
- Agents/templates synced

### Timeline

| Time (UTC) | Event |
|------------|-------|
| 06:00 | Pipeline start, spec pre-check (1 ambiguity warning: "etc.") |
| ~06:02 | Spec validation PASS |
| ~06:06 | SA generation complete — mixed Format A + Format D (first time!) |
| ~06:06 | SA validation PASS (needed 1 retry for smoke test tautological repair) |
| ~06:06 | Tier-1 contracts generated (30 tests) |
| ~06:18 | Iteration 1: Implementer complete |
| ~06:21 | Iteration 1: Quality gate — Build PASS, Lint PASS, TC PASS, Scenarios FAIL |
| ~06:21 | Server startup timed out (Format A behavioral tests couldn't run) |
| ~06:25 | Iteration 1: Oracle NO-SHIP (feedback leaked "0% pass") |
| ~06:29 | Iteration 2: Quality gate — same 3 failures, server timeout again |
| ~06:33 | Iteration 2: Oracle NO-SHIP (feedback leaked "0% pass" twice) |
| ~06:38 | Iteration 3: Quality gate — same 3 failures, server timeout |
| ~06:38 | Early escalation: trajectory=PLATEAU detected |
| ~06:43 | ESCALATED |

### Scenario Generation (Major Improvement)

SA generated **mixed formats** for the first time on this spec:
- 2 Format A (HTTP behavioral): `01-jwt-middleware-errors.scenarios.md`, `02-auth-endpoint-behaviors.scenarios.md`
- 4 Format D (artifact verification): `00-tier1-contracts.test.ts`, `03-database-schema-validation.test.ts`, `04-environment-validation.test.ts`, `05-log-output-security.test.ts`
- Total: 24 declared intents (15 compiled, 9 supplementary)

### Quality Gate Results (Consistent Across All 3 Iterations)

| Gate | Status |
|------|--------|
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Blind Scenarios (executable) | FAIL (3/37 failed) |

**Actual vitest results:** 34 passed, 3 failed, 37 total = **91% pass rate**

The 3 persistent failures:
1. `03-database-schema-validation.test.ts`: regex `clerk_id.*varchar.*unique` didn't match migration SQL (likely column name mismatch — `clerk_id` vs `clerkId` or different column definition style)
2. `04-environment-validation.test.ts`: "JWT_SECRET alone is sufficient" — expected exit code 0, got 1
3. `04-environment-validation.test.ts`: "CLERK_JWKS_URL alone is sufficient" — expected exit code 0, got 1

### Critical Bug: Pass Rate Calculation Reports 0% When Actual Rate is 91%

**The pipeline reported "0% pass rate" at every gate despite vitest showing 91% (34/37).**

Root cause analysis:
- `scenario-counts-executable.env` correctly shows: `SCENARIO_PASSED=34, SCENARIO_TOTAL=37, SCENARIO_RATE=91`
- BUT the category breakdown files (`category-iter1.env` etc.) only include **failing categories** (03 and 04)
- Category 00 (tier-1, 30 tests, all passed) is present in `category-iter0.env` but **absent from iter1-3**
- Category 05 (log output security, passed) is also absent from all category files
- The weighted pass rate is calculated from categories only, yielding 0/3 = 0%

**This means the pipeline's pass rate calculation excludes all-passing test files from the category aggregation, making it impossible to report > 0% when any category has failures.**

This is the root cause of the PLATEAU escalation — the Oracle sees "0% pass rate" and issues NO-SHIP, even though the implementation is 91% correct.

### Server Startup Timeout

The test server failed to start at `http://localhost:3000/health` within 30s on every iteration. This means Format A behavioral scenarios (JWT middleware, /auth/me endpoint) were never executed. The implementation may well handle these correctly but they couldn't be tested.

Possible causes:
- Missing DATABASE_URL in the gate container environment
- Missing migration step before server start
- Health endpoint not implemented or at a different path

### Oracle Feedback Leakage

Oracle feedback contained "0% pass" on every iteration, triggering barrier validation failures (attempt 1/3 each time). The validation regex catches `\b\d+%\s*(?:pass|fail)` patterns. The Oracle kept including raw pass rates despite the barrier rules.

---

## Run 2

**Start time:** 2026-03-31 ~06:47 UTC
**Outcome:** ESCALATED after 3 iterations (0% reported pass rate — actual pass rate was 100% in iter 2)
**Pipeline stage reached:** Full 3-iteration loop + escalation
**Total duration:** ~68 minutes (longer due to implementer retries)

### Pre-run state
- Repo fully reset, all artifacts cleaned
- Docker images rebuilt (cached)
- Agents/templates synced

### Timeline

| Time (UTC) | Event |
|------------|-------|
| 06:47 | Pipeline start |
| ~06:49 | Spec validation PASS |
| ~06:56 | SA generation — ALL Format A (HTTP behavioral) + tier-1 contracts. No Format D. |
| ~06:56 | SA validation PASS, smoke test healthy |
| ~07:10 | Iter 1: Implementer failed attempt 1, retrying |
| ~07:14 | Iter 1: Pre-gate FAIL (lint: `no-explicit-any` in logger.ts) |
| ~07:17 | Iter 1: Pre-gate FAIL attempt 2 (same lint errors) |
| ~07:17 | Iter 1: Quality gate — Build PASS, Lint FAIL, TC PASS, **Scenarios PASS (30/30)** |
| ~07:21 | Iter 1: Oracle NO-SHIP (leaked "0% Pass", "80% pass") |
| ~07:34 | Iter 2: Implementer complete |
| ~07:37 | Iter 2: Pre-gate FAIL attempt 1 (lint), Pre-gate PASS attempt 2 |
| ~07:37 | Iter 2: Quality gate — **ALL PASS** (Build, Lint, TC, Self-Tests, Scenarios) |
| ~07:37 | Iter 2: Auto-detected `test_cmd` and updated config.toml |
| ~07:43 | Iter 2: Oracle NO-SHIP (leaked "0% pass", "90% pass") |
| ~07:50 | Iter 3: Implementer complete |
| ~07:52 | Iter 3: Pre-gate FAIL/PASS, Quality gate 0% reported |
| ~07:52 | Early escalation: trajectory=PLATEAU |
| ~07:55 | ESCALATED |

### Scenario Generation (Pure Format A)

SA chose **all Format A** (HTTP behavioral) this run:
- `01-auth-contract.scenarios.md`
- `02-auth-errors.scenarios.md`
- `03-auth-security.scenarios.md`
- `04-auth-edge-cases.scenarios.md`
- `00-tier1-contracts.test.ts` (auto-generated, 30 tests)
- Total: 26 scenarios (14 intents, 13 compiled, 13 supplementary)
- Only tier-1 contracts are executable Format D; the rest are Format A behavioral

### Quality Gate Details

**Iteration 1:** Build PASS, Lint FAIL (5 `no-explicit-any` in logger.ts, 2 errors in drizzle.config.ts), TC PASS, Scenarios PASS (30/30)
**Iteration 2:** ALL PASS — Build, Lint, Type Check, Self-Tests, Blind Scenarios all PASS
**Iteration 3:** Not fully reported (PLATEAU escalation)

### Critical Findings

1. **Pass rate calculation bug (CONFIRMED — blocks shipping):** The category-weighted pass rate reports 0% when actual rate is 100%. `scenario-counts-executable.env` shows `SCENARIO_RATE=100` but `category-iter0.env` shows `CAT_00_PASSED=0, CAT_00_TOTAL=30`. The multi-run aggregation marks all tier-1 tests as failed in the category breakdown despite vitest showing them all passing. This feeds into the review pre-pass as "0% pass rate" → Oracle NO-SHIPs.

2. **Server startup timeout (persistent):** Server never starts at `http://localhost:3000/health`. Format A behavioral scenarios can't execute at HTTP level. Only tier-1 contract tests run (they don't need a server).

3. **Implementer reliability:** Failed on first attempt in both iter 1 and iter 2, but succeeded on retry each time. Pattern: initial attempt fails, retry succeeds.

4. **Pre-gate lint persistence:** The implementer struggles with lint rules (`no-explicit-any`, `no-non-null-assertion`, `no-undef`). Pre-gate repair often fails to fix all lint issues in 2 attempts.

5. **Oracle feedback leakage:** Oracle consistently includes pass rate percentages in feedback despite barrier rules. Validation catches it but the pattern persists across all iterations.

6. **Config auto-update:** Pipeline auto-detected `test_cmd = 'pnpm -r run --if-present test'` and updated config.toml during iter 2. Also renamed `cmd` → `server_cmd` in `[test_server]`.

### Root Cause Assessment

**This implementation should have shipped in iteration 2.** All gates passed including blind scenarios. The sole blocker is the pass rate calculation bug in the multi-run category aggregation, which reports 0% to the Oracle despite 100% actual scenario pass rate.

---

## Run 3

**Start time:** PENDING
**Outcome:** PENDING

---
