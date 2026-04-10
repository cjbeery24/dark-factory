# Pipeline Run Report: budget-brain-v2 / 2026-03-28-database-foundation

**Date:** 2026-04-10 01:11 UTC
**Repo:** budget-brain-v2 (pnpm monorepo, Node/TypeScript)
**Spec:** 2026-03-28-database-foundation
**Dark Factory version:** Phase 6 (commit 8d13b85) — tsc partitioning, safe unsanitization, post-repair smoke
**Previous best:** 81% pass rate (Phase 5, run_id f1eb0a51c77246f8, escalated after 2 iterations)

## Purpose

Verify Phase 6 fixes improve scenario pass rate beyond the 81% plateau. Key changes:
1. tsc Check 9 partitions import-resolution vs code errors (no longer masks real bugs)
2. sanitize_crash_output preserves safe error messages (ReferenceError, TypeError, etc.) with path redaction
3. Post-repair smoke re-runs scenarios after repair to verify crashes are fixed

**Environment reset:** Full clean (git restore, untracked delete, runtime artifacts wiped, architecture environment wiped, docker images rebuilt, orphaned containers/networks cleaned).

## Run 1

**run_id:** 73d7977162de4d6c
**Start time:** 2026-04-10 01:11:49 UTC
**End time:** 2026-04-10 01:54:53 UTC
**Total wall time:** ~43 minutes
**Outcome:** ESCALATED after 2 iterations at 37% pass rate (PLATEAU trajectory)
**Estimated cost:** ~$84 (2 iterations x $42/iter)

### Phase Transitions (from events.jsonl)

| Time (UTC) | Event | Role | Wall (s) | Notes |
|------------|-------|------|----------|-------|
| 01:11:49 | SPEC_START | | | |
| 01:11:49 | AGENT_START | spec-validator | | |
| 01:13:19 | AGENT_COMPLETED | spec-validator | 91s | |
| 01:13:19 | AGENT_START | architect | | |
| 01:15:44 | AGENT_COMPLETED | architect | 144s | postgres-test service declared |
| 01:15:44 | AGENT_START | qa-holdout | | phase=generate |
| 01:21:06 | AGENT_COMPLETED | qa-holdout | 323s | |
| 01:21:07 | AGENT_START | test-engineer | | phase=generate |
| 01:24:25 | AGENT_COMPLETED | test-engineer | 198s | |
| 01:24:25 | COVERAGE_GAP | | | uncovered: FR-1, FR-2, FR-3, FR-4 |
| 01:24:25 | AGENT_START | test-engineer | | phase=coverage_regen |
| 01:28:14 | AGENT_COMPLETED | test-engineer | 228s | 26 anchor tests |
| 01:28:14 | COVERAGE_PASS | | | |
| 01:28:16 | AGENT_START (iter 1) | implementer | | |
| 01:34:03 | AGENT_COMPLETED | implementer | 346s | 10 files changed |
| 01:34:07 | SMOKE_COMPLETED | | | success=true, policy=advisory |
| 01:34:18 | GATE_COMPLETED | | | **exit_code=1, pass_rate=37, crashed=0** |
| 01:35:45 | AGENT_COMPLETED | defect-classifier | 87s | |
| 01:37:43 | AGENT_COMPLETED | reviewer | 117s | |
| 01:38:41 | AGENT_COMPLETED | oracle | 58s | NO-SHIP |
| 01:39:24 | AGENT_START (iter 2) | implementer | | |
| 01:43:29 | AGENT_COMPLETED | implementer | 245s | 10 files changed |
| 01:43:33 | SMOKE_COMPLETED | | | success=true, policy=fatal |
| 01:43:45 | GATE_COMPLETED | | | **exit_code=1, pass_rate=37, crashed=0** |
| 01:43:45 | TRAJECTORY_CLASSIFIED | | | PLATEAU: "Fundamentally different approach needed" |
| 01:52:45 | AGENT_COMPLETED | oracle | 87s | NO-SHIP |
| 01:53:32 | AGENT_START | escalation | | |
| 01:54:53 | SPEC_ESCALATED | | | 2 iterations completed |

### State File (final)

```
spec=2026-03-28-database-foundation
run_id=73d7977162de4d6c
completed_step=2
iteration=2
server_error=
smoke_result=passed
pass_rate=37
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

### Scenario Counts (both iterations identical)

```
SCENARIO_PASSED=3
SCENARIO_FAILED=5
SCENARIO_TOTAL=8       # only 8 of 26 vitest tests actually ran
SCENARIO_CRASHED=0     # KEY: zero crashes (Phase 5/6 working)
SCENARIO_ASSERT_FAILED=5
```

### Quality Gate (iteration 2)

| Gate | Status |
|------|--------|
| Install | PASS |
| Build | PASS |
| Lint | PASS (was FAIL in iter 1 — implementer fixed) |
| Type Check | PASS |
| Self-Tests | PASS |
| Blind Scenarios | FAIL (37% — 3 passed, 5 failed, 18 skipped) |

### Scenario Validation

| Check | Status | Detail |
|-------|--------|--------|
| file_count | PASS | 3 files (min 3) |
| assertions | PASS | |
| trivial | PASS | |
| empty_bodies | PASS | |
| typescript_compilation | PASS | **tsc not available (skipped)** |
| no_source_reads | PASS | |

**Warning:** `env_dependent` — 1 file (database-connection.test.ts) flagged for environment-dependent assertions.

### Failing Tests (from vitest output)

1. **[FR-1] Drizzle ORM config** — scenario expects `schema: './src/db/schema/index.ts'` but impl uses `schema: './dist/db/schema/index.js'`. Classic spec-vs-impl mismatch: scenario hardcodes expected path.

2. **[AC-1] Drizzle client connects with valid DATABASE_URL** — assertion failure (no detail in JSON)

3. **[AC-2] Server exits with code 1 when DATABASE_URL missing** — assertion failure

4. **[EC-1] Server fails to start when DATABASE_URL missing** — assertion failure

5. **[EC-6] Connection pool exhausted** — `expected 0 to be greater than 0` — scenario creates concurrent connections expecting some to be rejected, but all succeed (pool not actually exhausted)

### Critical Finding: 18 Skipped Tests (69% of total)

Vitest reported 26 total tests but only 8 ran. The 18 skipped tests have **no explicit `.skip` patterns** in the scenario files. Most likely cause: `beforeAll` hooks that attempt database connections fail, causing all tests in those `describe` blocks to be skipped by vitest.

This is a **massive pass-rate suppressor**. If those 18 tests ran and even half passed, the rate would jump from 37% to 50%+. The gate only tracks the 8 that ran, so SCENARIO_TOTAL=8 masks the scope of the problem.

**7 scenario files exist:**
- database-connection.test.ts
- fr-1-drizzle-orm.test.ts
- fr-2-tenant-middleware.test.ts
- fr-3-rls-policies.test.ts
- fr-4-health-registration.test.ts
- health-check.test.ts
- tenant-context.test.ts

The fr-2, fr-3, fr-4, health-check, and tenant-context files likely all require a live DB connection that's failing.

### Barrier Enforcement (verified from audit log)

All barriers correctly enforced:
- **Architect:** scenarios, holdouts, audit, state, failures, .git masked
- **QA Holdout:** source_dirs (apps/api/src, apps/web/src, packages/shared/src), scenarios, .git masked
- **Test Engineer:** source_dirs, holdouts, .git masked
- **Implementer:** holdouts, reports, audit, state, failures, .git masked; `:rw` project mount
- **Defect Classifier:** audit, state, failures, .git masked
- **Reviewer:** scenarios, holdouts, reports, .git masked
- `NO_PROXY=localhost,127.0.0.1,postgres-test` correctly set for service-aware agents

### Escalation Report Highlights

The escalation agent identified:
- **SQL injection vulnerability** in tenant ID handling: `await tx.execute(drizzleSql.raw(\`SET LOCAL app.tenant_id = '${tenantId}'\`))` — string interpolation, not parameterized
- Implementation scored 82/100 by reviewer (good structure, security issue blocks ship)
- Blocks all dependent features (F-2 through F-14)

## Comparison to Previous Runs

| Run | Phase | Pass Rate | Crashes | Iterations | Outcome |
|-----|-------|-----------|---------|------------|---------|
| 4 (pre-Phase 5) | Phase 4.5c | 74% | 2 | 2 | Escalated |
| 5 (Phase 5) | Phase 5 | 81% | 0 | 2 | Escalated |
| **6 (Phase 6)** | **Phase 6** | **37%** | **0** | **2** | **Escalated** |

### Regression Analysis

**Phase 6 regressed from 81% to 37%.** However, this is NOT a Phase 6 regression — it's a fresh run with newly generated scenarios (environment wiped). Previous runs reused scenarios across iterations; this run generated new ones from scratch. Key differences:

1. **Zero crashes** — consistent with Phase 5+6 (was 2 in Phase 4)
2. **New scenario set** — 7 files with 26 tests (previous had different scenarios). Coverage regen produced more DB-dependent scenarios that silently skip.
3. **18 skipped tests** — the dominant issue. These aren't counted in pass rate but represent scenarios that can't execute, likely due to DB connectivity failures in `beforeAll` hooks.

## Pipeline Bug Reports

### BUG-1: Skipped tests not surfaced in scenario counts (HIGH)

`SCENARIO_TOTAL=8` when vitest reports 26 total (18 skipped). The pipeline only counts tests that produce a pass/fail verdict. Tests that skip due to `beforeAll` failures are invisible to the pass-rate calculation. This masks the real problem — scenarios that require a live database can't connect.

**Impact:** Pass rate of 37% (3/8) looks like an impl problem. Real picture is 3/26 = 11.5% coverage — 69% of tests can't even run.

**Suggested fix:** Parse vitest's skipped count and report it separately. Surface "X tests skipped" as a warning in the gate report. Consider counting skipped tests as failures or at least flagging when >25% of tests skip.

### BUG-2: tsc validation skipped — "tsc not available" (MEDIUM)

Scenario validation reports `typescript_compilation | PASS | tsc not available (skipped)`. The Phase 6 tsc partitioning fix never ran because `tsc` isn't installed in the validation context. This means the fix is untested in practice for this run.

**Impact:** tsc partitioning can't catch scenario code errors during validation. Phase 6 Fix 1 is dead code for this run.

**Note:** The gate container (which runs vitest) has tsc available, so compilation errors surface at runtime. But pre-validation should catch them earlier.

### BUG-3: `beforeAll` DB failures cause silent test skips (HIGH)

Scenarios that connect to postgres in `beforeAll` silently skip all their tests when the connection fails, instead of failing loudly. This is a vitest behavior, but the pipeline should detect and report it.

**Possible root causes:**
- The gate container can't reach the postgres-test service (network isolation issue?)
- Database isn't initialized with the schema the scenarios expect
- The implementer's migration code doesn't run before scenarios execute
- `beforeAll` catches connection errors and calls `skip()` instead of `fail()`

## Key Takeaways

1. **Phase 6 fixes are mechanically correct** — zero crashes confirms Phase 5/6 working. But the scenario set changed, so direct pass-rate comparison to Phase 5 is invalid.
2. **The dominant blocker is now silent test skips**, not crashes or scenario quality. 69% of tests don't execute.
3. **The pipeline needs visibility into skipped tests.** Current metrics mask the real coverage gap.
4. **Database-dependent specs need the pipeline to verify DB connectivity** before running scenarios. A pre-scenario health check that confirms the service is reachable would catch this early.
