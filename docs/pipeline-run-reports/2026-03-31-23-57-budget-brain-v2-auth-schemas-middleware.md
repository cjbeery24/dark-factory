# Pipeline Run Report: budget-brain-v2 / 2026-03-30-auth-schemas-middleware

**Date:** 2026-03-31 23:57 UTC
**Repository:** budget-brain-v2
**Spec:** 2026-03-30-auth-schemas-middleware
**Dark Factory Version:** Latest (main branch)

---

## Run 1

**Start time:** 2026-03-31 23:57 UTC
**End time:** ~2026-04-01 00:55 UTC (~58 min)
**Outcome:** ESCALATED after 3 iterations

### Pre-run State
- Repository reset to clean state (tracked files restored, untracked deleted, .dark-factory artifacts cleared)
- Docker images rebuilt
- Agents/templates synced

### Pipeline Progress
1. Spec validation: PASS (1 ambiguity warning for "etc.")
2. Scenario generation: 30 static scenarios + 1 executable HTTP scenario generated
3. Smoke test: all scenarios fail cleanly (healthy)
4. **Iteration 1:** 0/30 static tests passed, 0/1 executable passed → implementation completely missed file locations
5. **Iteration 2 (after feedback):** 29/30 static, 0/1 executable → TC-25 (env validation with JWT config) still failing; server startup blocked by missing drizzle-kit
6. **Iteration 3:** 29/30 static, 0/1 executable → same issues persist
7. Early escalation triggered: trajectory=PLATEAU at iteration 3

### Observations
- **Massive improvement iter0→iter1**: 0/30 → 29/30 static tests. The feedback loop worked well for code structure issues.
- **Stuck on TC-25**: "environment validation file exists with JWT config" failed all 3 iterations. The scenario likely expects a specific file path or pattern the implementer keeps missing.
- **Server startup failure persistent**: `drizzle-kit` not found (exit 254) blocked all HTTP scenario execution across all iterations. The feedback clearly identified this but the implementer couldn't resolve it — likely because drizzle-kit is a devDependency and the container environment runs with production deps only, or the dependency wasn't added to the right package.json.
- **Pre-gate failed on attempts 1 of iter1 and iter2**, succeeded on retry. Iter3 pre-gate passed first try. This suggests build/lint issues being fixed between pre-gate retries.
- **Review score: 92/100** — excellent implementation quality despite deployment failure.
- **Security issues from iter1 feedback** (SQL injection, non-functional RLS, missing tenant validation) were all resolved by iter2.

### Issues Found
1. **BLOCKING: drizzle-kit not available in test container** — The `server_setup_cmd` runs `pnpm drizzle-kit push` which fails because drizzle-kit isn't installed. This blocked all HTTP scenario testing for all 3 iterations. The implementer added drizzle-kit but it may not be available in the container's pnpm environment.
2. **TC-25 persistent failure**: Environment validation file with JWT config not found. Could be a file path mismatch between what the scenario expects and what the implementer creates.
3. **Trajectory classification**: Pipeline correctly identified PLATEAU pattern and triggered early escalation — good behavior.
4. **Feedback quality**: Excellent — identified SQL injection, RLS gaps, and dependency issues clearly with code examples.

---

## Run 2

**Start time:** ~2026-04-01 00:57 UTC
**End time:** ~2026-04-01 02:05 UTC (~68 min)
**Outcome:** ESCALATED after 3 iterations

### Pipeline Progress
1. Spec validation: PASS
2. Scenario generation: scenarios generated successfully
3. Smoke test: all fail cleanly
4. **Iteration 1:** Quality gate reported 100% pass rate, Oracle: NO-SHIP
5. **Iteration 2:** Implementer failed attempt 1/2 (retry needed), quality gate 100%, Oracle: NO-SHIP
6. **Iteration 3:** Quality gate 100%, early escalation (PLATEAU trajectory)

### Observations
- **Test result format changed**: Iter1 showed "1/1 passed" as a single file-level entry vs iter0 showing 30 individual TCs. This is a result parsing inconsistency — the quality gate reports 100% but the category breakdown shows 0/30.
- **Iter0 → Iter1 pattern identical to Run 1**: 0/30 → improvements after feedback
- **Implementer failed on attempt 1 in iter2**: Required retry (agent container crashed or timed out after 19+ min)
- **Same drizzle-kit blocker**: `server_setup_cmd` fails with `ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL Command "drizzle-kit" not found` — the implementer cannot resolve this because it's an environment/container issue, not a code issue
- **Quality gate shows 100% but Oracle says NO-SHIP**: The gate counts the file-level "1/1 passed" as 100%, but Oracle correctly sees 0% HTTP scenario pass rate and blocks shipping
- **Longer runtime than Run 1**: ~68 min vs ~58 min due to implementer retry in iter2

### Issues Found
1. **BLOCKING (same as Run 1): drizzle-kit not available** — persistent across all runs; this is fundamentally a container environment issue that the implementer cannot fix by editing code alone
2. **Result parsing inconsistency**: Quality gate reports 100% when all static tests pass at file level, but the category breakdown uses a different counting method. This may lead to misleading pass rates.
3. **Implementer crash/timeout in iter2**: The implementer container ran for 19+ minutes and needed a retry — may have exhausted max turns or hit an error
4. **Feedback loop ineffective for env issues**: The feedback correctly identifies drizzle-kit as the blocker each time, but the implementer can't solve it because it's a container toolchain issue

---

## Run 3

**Start time:** ~2026-04-01 02:12 UTC (first attempt failed validation, retried ~02:15)
**End time:** ~2026-04-01 02:50 UTC (~38 min from retry)
**Outcome:** SHIPPED (mechanical override) after 2 iterations

### Notes
- First attempt at Run 3 failed spec validation (verdict=FAIL). This is non-deterministic — the same spec passed validation in Runs 1 and 2. Re-ran and validation passed on retry.

### Pipeline Progress
1. Spec validation: FAIL on first attempt, PASS on retry
2. Scenario generation: scenarios generated
3. Smoke test: all fail cleanly
4. **Iteration 1:** 0/30 → feedback → 29/30 (TC-25 failing), 96% pass rate, Oracle: NO-SHIP
5. **Oracle feedback leakage**: Feedback validation caught barrier violations 2 times — oracle used "no scenarios" and "all scenarios" phrases. Retried feedback generation each time.
6. **Iteration 2:** 29/30 (TC-25 still failing), 96% pass rate
7. **Mechanical SHIP override**: Pipeline detected 1 stable failure (TC-25 same across iters) and 96% pass rate → auto-shipped

### Observations
- **Spec validation non-determinism**: The same spec that passed validation in Runs 1 and 2 failed on the first attempt of Run 3. This is a reliability concern — the validator agent is not fully deterministic.
- **Mechanical SHIP override worked**: After 2 iterations with the same single stable failure (TC-25), the pipeline correctly identified a plateau with high pass rate and auto-shipped. This is the intended behavior for scenarios where a single test may be unreasonable or the implementation is otherwise complete.
- **Barrier leakage detection working**: The feedback validation caught the oracle using scenario-specific language ("no scenarios", "all scenarios") and forced regeneration. Good defense-in-depth behavior.
- **Same TC-25 failure across all runs**: "environment validation file exists with JWT config" — this test consistently fails. Either the scenario expects a very specific file path/pattern that differs from what any implementer produces, or the test itself has unreasonable expectations.
- **Same iter0 pattern**: 0/30 on first implementation, 29/30 after feedback — identical to Runs 1 and 2.
- **Pre-gate failed on iter1 attempt 1**: Same as previous runs, pre-gate needs 2 attempts for iter1.
- **Shorter total time**: ~38 min vs 58 min (Run 1) and 68 min (Run 2) — shipping after 2 iterations instead of exhausting all 3 saves significant time.
- **State file discrepancy**: The state file shows `status=interrupted` even though pipeline output says SHIPPED. This is a potential bug in state management.

### Issues Found
1. **Spec validation non-determinism**: Same spec fails/passes validation randomly across runs
2. **TC-25 appears to be an unreasonable or mis-targeted scenario**: It never passes across any run or iteration
3. **Oracle feedback barrier leakage**: Oracle naturally gravitates toward mentioning "scenarios" — barrier validation catches this but wastes time regenerating
4. **State file not updated to shipped**: The state file still shows `interrupted` instead of `shipped` after mechanical override
5. **Mechanical override saved this run**: Without it, this would have been another ESCALATED outcome

---

## Run 4

Not needed — Run 3 shipped successfully.

---

## Summary

| Run | Outcome | Duration | Iterations | Key Issue |
|-----|---------|----------|------------|-----------|
| 1   | ESCALATED | ~58 min | 3/3 | drizzle-kit missing + TC-25 |
| 2   | ESCALATED | ~68 min | 3/3 | drizzle-kit missing + TC-25 + implementer crash |
| 3   | SHIPPED (mechanical override) | ~38 min | 2/3 | TC-25 stable failure, override triggered |

## Cross-Run Observations

### Consistent Patterns
1. **Iter0 always 0/30**: The implementer consistently produces files at wrong locations on first pass. The feedback loop is essential.
2. **Iter1 always 29/30**: After feedback, the implementer reliably fixes almost everything except TC-25.
3. **TC-25 never passes**: "environment validation file exists with JWT config" — 0/9 iterations across 3 runs. This appears to be a scenario-implementation mismatch.
4. **Pre-gate typically fails on iter1 attempt 1**: Build/lint issues on first pass, resolved by retry.
5. **drizzle-kit server startup failure**: Present in all runs but only blocked Runs 1 and 2 because they relied on Oracle judgment. Run 3 used mechanical override.

### Pipeline Improvement Opportunities
1. **Iter0 blind-pass problem**: The implementer should be better at matching expected file paths from the spec on the first pass. This wastes an entire iteration every time.
2. **TC-25 scenario quality**: The scenario appears to have expectations that don't match what any implementer produces. Need to investigate if this is a scenario bug or a spec ambiguity.
3. **drizzle-kit in container**: The server_setup_cmd relies on drizzle-kit being available. If the container doesn't have it, all HTTP scenarios fail. This is an environment configuration issue.
4. **Spec validation determinism**: The validator should produce consistent results for the same input.
5. **Oracle feedback leakage**: The oracle often references "scenarios" in feedback, triggering barrier violations. The feedback prompt should be more explicitly constrained.
6. **Mechanical override value**: The stable failure detection + mechanical override is what ultimately allowed shipping. Without it, this spec would never ship due to TC-25.
7. **State file consistency**: The state file should reflect the actual pipeline outcome (SHIPPED vs interrupted).
