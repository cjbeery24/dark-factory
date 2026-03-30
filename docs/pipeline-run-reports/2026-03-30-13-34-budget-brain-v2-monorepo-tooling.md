# Pipeline Run Report: budget-brain-v2 / 2026-03-28-monorepo-tooling

**Date:** 2026-03-30T13:34:14Z
**Project:** budget-brain-v2
**Spec:** 2026-03-28-monorepo-tooling
**Context:** Post-bugfix run. Two bugs fixed since last attempt:
1. Self-test auto-detection: node fallback now checks for "test" script in package.json before defaulting to `npm test`
2. Proxy race condition: CleanupManager now uses PID ownership guard to prevent atexit handler from destroying resources owned by a resumed pipeline

**Previous best result:** 65/66 scenarios passed (98%), blocked by self-test gate failure

## Pre-run Checklist
- [x] Update agents/templates in budget-brain-v2
- [x] Rebuild Docker images
- [x] Reset repo state
- [x] Run pipeline

## Run Log

### Pipeline Run (run_id: d492b434b8664ba6)

| Step | Time | Result |
|------|------|--------|
| Spec Validation | 06:38:23 → 06:39:59 | PASS (100% structure, 1 ambiguity warning) |
| Scenario Architect | 06:39:59 → 06:44:39 | PASS (36 active + 3 invariant contracts) |
| SA Validation + Smoke | 06:44:39 → 06:46:48 | PASS (repair completed and validated) |
| Iteration 1: Implement | 06:46:48 → 06:52:52 | Complete |
| Iteration 1: Quality Gate | 06:52:52 → 06:53:09 | Build/Lint/TypeCheck PASS, Self-Tests **SKIP**, Scenarios 45/53 (84%) |
| Iteration 1: Review + Oracle | 06:53:09 → 06:56:18 | **NO-SHIP** |
| Iteration 2: Implement | 06:56:19 → 07:01:28 | Complete |
| Iteration 2: Quality Gate | 07:01:28 → 07:01:47 | Build/Lint/TypeCheck PASS, Self-Tests SKIP, Scenarios 43/53 (81%) |
| Iteration 2: Review + Oracle | 07:01:47 → 07:06:10 | **NO-SHIP** |
| Iteration 3: Implement | 07:06:10 → 07:09:09 | Complete |
| Iteration 3: Quality Gate | 07:09:09 → 07:10:29 | Build/Lint/TypeCheck PASS, Self-Tests SKIP, Scenarios 46/53 (86%) |
| Final Decision | 07:10:29 | **ESCALATED** (max iterations reached) |

**Total duration:** ~32 minutes

## Bug Fix Validation

| Fix | Status | Evidence |
|-----|--------|----------|
| Self-test auto-detection | CONFIRMED WORKING | Self-Tests gate shows "SKIP - No command configured" instead of failing on `npm test` |
| Proxy race condition | NOT TESTED | No concurrent pipeline invocations in this run |
| Container `--workdir /project` | CONFIRMED WORKING | Implementer created files in project directory (iteration 1 produced 45/53 passing scenarios) |

## Failure Analysis

The 7 stable failures across iterations are concentrated in **linting/formatting scenarios** (file `03-linting-formatting.test.ts`):
- ESLint exits with code 1 for valid React code (expects 0) — React rules not properly configured
- ESLint exits with code 1 for valid Node code (expects 0) — Node rules not properly configured
- Prettier formatting tests fail — formatter not producing expected output
- 4 test files failing, 1 passing

The implementer correctly scaffolds the monorepo (pnpm workspace, TypeScript config inheritance, build pipeline) but struggles with ESLint plugin configuration for React/Node-specific rulesets in a multi-app monorepo context. This is a legitimate implementation complexity — ESLint v9 flat config with framework-specific plugins requires specific dependency and config patterns.

## Key Metrics

| Metric | Value |
|--------|-------|
| Best pass rate | 86% (iteration 3) |
| Scenario total | 53 |
| Stable failures | ~7 (linting/formatting) |
| Hard gates | All PASS (Build, Lint, TypeCheck) |
| Self-Tests | SKIP (fix confirmed) |

## Outcome

ESCALATED — requires human intervention on ESLint configuration for multi-app monorepo. The self-test auto-detection fix is validated and working. The pipeline mechanics are healthy.
