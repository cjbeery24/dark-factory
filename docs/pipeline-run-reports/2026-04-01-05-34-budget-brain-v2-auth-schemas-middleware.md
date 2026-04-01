# Pipeline Run Report: budget-brain-v2 / 2026-03-30-auth-schemas-middleware

**Date:** 2026-04-01 05:34 UTC
**Repository:** budget-brain-v2
**Spec:** 2026-03-30-auth-schemas-middleware
**Dark Factory Version:** Latest (main branch)
**Previous runs:** 3 runs on 2026-03-31 (2 ESCALATED, 1 SHIPPED via mechanical override)

---

## Run 1

**Start time:** 2026-04-01 05:38 UTC
**End time:** ~2026-04-01 06:25 UTC (~47 min)
**Outcome:** ESCALATED after 3 iterations

### Pre-run State
- Repository reset to clean state (tracked files restored, untracked deleted)
- All .dark-factory runtime artifacts for this spec removed (state, scenarios, reports, reviews, audit, failures)
- config.sh removed, config.toml restored to committed state
- Docker images rebuilt (all cached, no changes)
- Agents and templates synced from dark-factory main

### Pipeline Progress

| Step | Status | Notes |
|------|--------|-------|
| Spec validation | PASS | 1 ambiguity warning ("etc.") |
| Architecture lock | PASS | Manifest produced, flagged drizzle-kit not in base image |
| Scenario generation | PASS | 30 static scenarios + executable HTTP scenarios |
| Smoke test | PASS | All scenarios fail cleanly |
| **Iter 1** implementation | Complete | Pre-gate FAILED both attempts (lint) |
| Iter 1 quality gate | 100% (file-level) | 30/30 static passed as single file |
| Iter 1 review | 85/100 PASS | |
| Iter 1 oracle | **NO-SHIP** | drizzle-kit server startup failure |
| **Iter 2** implementation | Complete | Pre-gate failed attempt 1, passed attempt 2 |
| Iter 2 quality gate | 100% (file-level) | |
| Iter 2 review | PASS | |
| Iter 2 oracle | **NO-SHIP** | Same drizzle-kit blocker |
| **Iter 3** implementation | Complete | Pre-gate failed attempt 1, passed attempt 2 |
| Iter 3 quality gate | 100% (file-level) | |
| Escalation | **ESCALATED** | trajectory=PLATEAU at iteration 3 |

### Category Breakdown by Iteration

| Iteration | Static Tests | Executable Tests | Notes |
|-----------|-------------|------------------|-------|
| 0 (smoke) | 0/30 | — | Expected: all fail cleanly |
| 1 | 0/1 (file-level) | Blocked | drizzle-kit missing |
| 2 | 0/1 (file-level) | Blocked | drizzle-kit missing |
| 3 | — (no iter3 category file) | Blocked | drizzle-kit missing |

### Key Observations

1. **Persistent drizzle-kit blocker:** All 3 iterations failed HTTP scenario execution because `pnpm drizzle-kit push` fails with `ERR_PNPM_RECURSIVE_EXEC_FIRST_FAIL Command "drizzle-kit" not found`. The implementer cannot fix this — it's a container environment issue where drizzle-kit isn't available even though the architect manifest flagged `install_via: pnpm install drizzle-kit --save-dev`.

2. **Quality gate vs Oracle disagreement:** Quality gate reports 100% pass rate (counting the test file as passing), but Oracle correctly identifies that HTTP scenarios can't run and blocks shipping. This is the right behavior — the oracle is more discerning than the gate.

3. **No mechanical override triggered:** Unlike the previous session's Run 3 (which shipped via mechanical override at 96% after 2 stable failures), this run escalated after 3 iterations. The difference appears to be satisfaction score — 0% satisfaction this run vs 96% in the previous run. This suggests the override criteria have changed or the scoring is different.

4. **Pre-gate lint failure on iter1:** Both pre-gate attempts failed on iteration 1 (lint). On iters 2 and 3, attempt 1 failed but attempt 2 passed. The implementer produces lint-failing code on first pass consistently.

5. **Review quality good (85/100):** The reviewer correctly assessed the implementation as good code with proper security, RLS, and JWT handling — the issue is purely the drizzle-kit container environment, not code quality.

6. **Feedback loop ineffective for env issues:** The oracle feedback correctly identifies drizzle-kit as the blocker every iteration, but the implementer cannot resolve a container toolchain problem by editing code.

### Comparison to Previous Runs (2026-03-31)

| Metric | Previous Run 1 | Previous Run 2 | Previous Run 3 | This Run 1 |
|--------|---------------|---------------|---------------|------------|
| Outcome | ESCALATED | ESCALATED | SHIPPED (mech override) | ESCALATED |
| Iterations | 3/3 | 3/3 | 2/3 | 3/3 |
| Duration | ~58 min | ~68 min | ~38 min | ~47 min |
| Static tests iter1 | 29/30 | varies | 29/30 | 30/30 (file-level) |
| Key blocker | drizzle-kit + TC-25 | drizzle-kit + TC-25 | TC-25 (shipped anyway) | drizzle-kit |
| Review score | 92/100 | — | — | 85/100 |
| Mech override? | No | No | Yes (96%) | No |

### Issues Found

1. **BLOCKING: drizzle-kit not available in quality gate container** — The architect manifest correctly identifies drizzle-kit needs installation but the quality gate container doesn't execute the `install_via` command. The `server_setup_cmd` fails with exit 254. This is the same issue across all runs.

2. **Category counting inconsistency** — Iter0 shows 0/30 (individual test count), but iter1+ shows 0/1 (file-level count). The counting methodology changed between smoke test and regular iterations.

3. **Scenario counts vs category mismatch** — `scenario-counts.env` reports 30/30 = 100%, but `category-iter2.env` reports 0/1. The quality gate summary says PASS. Multiple conflicting signals.

4. **Mechanical override did not trigger** — Despite 100% quality gate pass rate and PLATEAU trajectory, the override didn't fire. In the previous session it triggered at 96% with 1 stable failure. The override logic may have changed, or the 0% satisfaction score prevented it.

---

## Runs 2-4

Not attempted. The drizzle-kit container environment issue is a persistent blocker that the implementer cannot fix through code changes. Running more attempts would produce the same result (ESCALATED after 3 iterations due to server startup failure).

The root cause is a pipeline/infrastructure issue: the quality gate container does not install dependencies specified in the architect's manifest `install_via` field before running `server_setup_cmd`.

---

## Summary

| Run | Outcome | Duration | Iterations | Key Issue |
|-----|---------|----------|------------|-----------|
| 1 | ESCALATED | ~47 min | 3/3 | drizzle-kit not available in container |

## Pipeline Improvement Opportunities

1. **Manifest install_via execution:** The architect manifest correctly identifies that drizzle-kit needs `pnpm install drizzle-kit --save-dev`, but this instruction is not executed in the quality gate container. The pipeline should execute `install_via` commands before running `server_setup_cmd`.

2. **Category counting consistency:** The transition from individual test counting (iter0: 0/30) to file-level counting (iter1+: 0/1) makes cross-iteration comparisons misleading.

3. **Mechanical override sensitivity:** The override should consider whether "failures" are due to environment issues vs code issues. A 100% static test pass rate with environment-blocked HTTP scenarios should be treated differently than actual test failures.

4. **Feedback loop limitation:** The feedback system correctly identifies the drizzle-kit issue but the implementer can only modify code, not container configuration. The pipeline should detect environment-class failures and either auto-remediate or escalate faster.

5. **Pre-gate lint consistency:** The implementer consistently produces lint-failing code on first implementation pass. Consider providing lint rules as part of the implementation prompt or architecture manifest.
