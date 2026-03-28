# Pipeline Run Report: budget-brain / 2026-03-24-project-scaffolding

**Date:** 2026-03-28
**Spec:** 2026-03-24-project-scaffolding (F-1: Project Scaffolding)
**Target project:** budget-brain (pnpm monorepo with React + Fastify + shared types)
**Goal:** Track pipeline improvement/regression metrics across iterative runs

---

## Run 1

**Started:** 2026-03-28T06:49 (local) / 13:49 UTC
**Dark Factory version:** HEAD of main (ba90835)
**Pre-run changes:** Initial baseline — no pipeline fixes yet
**Run ID:** dfaebcc855b04313

### Pre-run State
- Budget-brain repo reset to clean state (git checkout + clean)
- Docker images rebuilt (all cached, verified)
- Agents/templates synced from dark-factory source
- Removed: reports, reviews, audit, state, scenarios, config.sh

### Pipeline Output
```
Spec validation passed
Scenarios generated (6 files, ~77 tests)
Smoke test: all scenarios fail cleanly (healthy)
Iteration 1/3: 90% pass rate -> NO-SHIP
Iteration 2/3: 85% pass rate -> NO-SHIP (REGRESSION)
Iteration 3/3: 89% pass rate -> NO-SHIP
Early escalation: trajectory=PLATEAU at iteration 3
ESCALATED after 3 iterations
```

### Observations

1. **Scenario quality issues (SCENARIO_BUGs):**
   - `06-edge-cases.test.ts` checks `pkg.dependencies['@tailwindcss/vite']` but it's in `devDependencies` (correct location)
   - Tests expect literal `process.env.PORT` in server.ts, but implementation properly abstracts through `env.ts`
   - Tests expect TypeScript in per-package `devDependencies`, but monorepo correctly uses root-level shared deps
   - These 4 tests fail across ALL 3 iterations — they cannot be fixed by the implementer

2. **Oracle CLASSIFICATIONS block never generated:**
   - All 3 oracle calls logged "CLASSIFICATIONS block absent from oracle feedback"
   - All failures defaulted to IMPL_BUG — reclassification logic never activated
   - 1 retry attempt insufficient to get compliance

3. **Semantic leakage detected:**
   - Oracle included "66/77 passed" in feedback (iteration 2)
   - Detected by audit checker but only warned + 1 retry, then accepted

4. **Regression in iteration 2:**
   - Went from 7 failures (iter1) to 11 failures (iter2) before recovering to 8 (iter3)
   - Implementer introduced new failures while trying to fix feedback items

### Issues Found

| # | Issue | Category | Impact |
|---|-------|----------|--------|
| 1 | Scenarios check `dependencies` instead of `devDependencies` for build-time packages | SCENARIO_BUG | Persistent failure, unfixable by implementer |
| 2 | Scenarios expect literal `process.env.PORT` instead of env abstraction pattern | SCENARIO_BUG | Persistent failure, unfixable by implementer |
| 3 | Scenarios expect per-package TypeScript deps instead of monorepo root pattern | SCENARIO_BUG | Persistent failure, unfixable by implementer |
| 4 | Oracle never outputs CLASSIFICATIONS block | ORACLE_COMPLIANCE | Reclassification logic dead code |
| 5 | Semantic leakage in oracle feedback ("66/77 passed") | BARRIER_LEAK | Weak enforcement (1 retry then accept) |
| 6 | Iteration regression (iter2 worse than iter1) | ITERATION_QUALITY | Implementer introduces new failures while fixing feedback |

### Metrics
| Metric | Value |
|--------|-------|
| Exit code | 2 (ESCALATED) |
| Wall time | ~38 min |
| Iterations | 3 |
| Scenario pass rate | 90% / 85% / 89% |
| Build/lint/type pass | YES (all iters) |

---

## Run 2

_Pending — requires pipeline fixes first_

---

## Run 3

_Pending_

---

## Run 4

_Pending_

---

## Summary & Trends

| Run | Exit | Wall Time | Iterations | Scenario Pass Rate | Issues Found |
|-----|------|-----------|------------|-------------------|--------------|
| 1 | ESCALATED | 38m | 3 | 90/85/89% | 6 issues (4 SCENARIO_BUG, 1 ORACLE, 1 BARRIER) |
| 2 | - | - | - | - | - |
| 3 | - | - | - | - | - |
| 4 | - | - | - | - | - |

## Root Cause Analysis

### Primary: Scenario Architect generates brittle tests
The scenario architect writes tests that are implementation-prescriptive rather than behavior-verifying. Examples:
- Checking which JSON field (`dependencies` vs `devDependencies`) a package appears in
- Requiring literal string patterns (`process.env.PORT`) instead of testing behavior
- Assuming specific dependency management patterns instead of testing outcomes

**Impact:** Creates unfixable failures that waste all 3 iterations and guarantee escalation.

### Secondary: Oracle CLASSIFICATIONS compliance failure
The oracle agent definition requires a `<!-- CLASSIFICATIONS: {...} -->` block but the LLM never generates it. The pipeline treats this as a backward-compatibility warning and proceeds, causing:
- All failures default to IMPL_BUG
- Reclassification logic (IMPL_BUG -> SPEC_AMBIGUOUS after 2 iters) never fires
- Pipeline cannot distinguish scenario bugs from implementation bugs
