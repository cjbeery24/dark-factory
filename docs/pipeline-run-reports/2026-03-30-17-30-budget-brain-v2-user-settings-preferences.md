# Pipeline Run Report: budget-brain-v2 / user-settings-preferences

**Date:** 2026-03-30
**Spec:** `2026-03-28-user-settings-preferences.md`
**Target repo:** budget-brain-v2
**Pipeline version:** Post phases 1, 2, 3, 4 from path-to-first-ship.md
**Config:** multi_run_n=3, multi_run_k=2, autonomy=3, enterprise=true
**Previous runs:** 5 prior attempts across 2 sessions — blocked at SA validation (tsc), spec validation (false positive), workspace check (hardcoded scripts)
**Changes since last run:** workspace_check.py now derives required scripts from config.toml; cleanup.py updates; orchestrator tweaks; detection.py updates; contracts.py updates

---

## Context

This is the 6th overall attempt at running user-settings-preferences through the pipeline. Previous sessions:
- Session 1 (3 runs): All blocked at SA validation (tsc check failures in pnpm monorepo)
- Session 2, Run 1: Blocked at spec validation (LLM false positive on Drizzle naming)
- Session 2, Run 2: Blocked at workspace consistency check (hardcoded `{test, lint, type-check}` scripts)

Key pipeline changes since last run:
- `workspace_check.py`: Now derives required scripts from config.toml gate commands instead of hardcoding
- `cleanup.py`: Updated cleanup logic
- `orchestrator.py`: Minor tweaks
- `detection.py`: Updated spec validation parsing
- `contracts.py`: Updated spec contract logic
- `quality-gate.sh`: Script updates

Config note: `test_cmd` is not defined in config.toml, so `derive_required_scripts` extracts `{lint, typecheck}` from `lint_cmd` and `typecheck_cmd`.

---

## Run 1

**Start time:** 2026-03-30 ~16:32 (UTC) / ~09:32 (local)
**Outcome:** BLOCKED — Workspace consistency check: missing `lint` script in all workspace packages
**Pipeline stage reached:** Between Step 2 (SA generation) and Step 3 (Implementation loop)

### Pre-run state
- budget-brain-v2 repo: clean (`git checkout -- .` + `git clean -fd`)
- Previous .dark-factory artifacts for this spec: cleaned (scenarios, state, reports, audit, reviews)
- Docker images: rebuilt fresh (all cached layers)
- Agents/templates: synced from dark-factory
- Config: config.toml only (no config.sh)

### Timeline
1. **Spec pre-check:** PASS — 2 ambiguity warnings for "etc." (non-blocking)
2. **Step 1 — Spec Validation:** PASS — verdict=PASS score=88 divergence=LOW blockers=0
3. **Step 2 — Scenario Generation:** 3 SA container attempts
   - Final output: 4 scenario files, 35 total scenarios
   - All 10 SA validation checks PASSED
4. **Workspace consistency check:** HARD FAIL — missing `lint` in all 3 workspace packages

### Fix Applied
Changed `lint_cmd` from `"pnpm -r lint"` (recursive, requires workspace scripts) to `"pnpm exec eslint ."` (root-level, matches actual project setup). Committed as `93bb47e`.

---

## Run 2

**Start time:** 2026-03-30 ~16:56 (UTC) / ~09:56 (local)
**Outcome:** ESCALATED — 3 iterations, all at 41% pass rate (12/29 scenarios)
**Pipeline stage reached:** Full pipeline completion (escalation after max iterations)
**Total duration:** ~75 minutes

### Pre-run state
- budget-brain-v2 repo: clean + lint_cmd fix committed
- All .dark-factory artifacts for this spec: cleaned
- Docker images: same build (cached)

### Timeline

| Time (UTC) | Event | Containers |
|------------|-------|------------|
| 16:56 | Spec pre-check: PASS (2 ambiguity warnings) | 0 |
| 16:57 | Spec validation: PASS (score=88, blockers=0) | 1 (spec-validator) |
| 16:59 | SA attempt 1 starts | 2 |
| 17:03 | SA attempt 2 starts (attempt 1 failed assertion_upper_bound) | 3 |
| 17:08 | SA attempt 3 starts (attempt 2 also failed — 35 assertions in one file) | 4 |
| 17:12 | SA validation: all 10 checks PASS | 4 |
| 17:12 | Workspace check: PASS | — |
| 17:12 | Smoke test: all scenarios fail cleanly (healthy) | — |
| 17:13 | Iteration 1: Implementer starts | 5 |
| 17:23 | Iteration 1: Quality gate (pre-gate attempt 1 FAIL) | 6 |
| 17:28 | Iteration 1: Pre-gate attempt 2 FAIL (lint on test-api.js) | 6 |
| 17:28 | Iteration 1: Full gate runs — 41% pass rate | 6 |
| 17:33 | Iteration 1: Review + Oracle: NO-SHIP | 8 |
| 17:34 | Oracle feedback leakage detected: "10/25 scenarios" blocked | 9 |
| 17:36 | Iteration 2: Implementer starts | 10 |
| 17:42 | Iteration 2: Pre-gate PASS (all 3 gates pass) | 11 |
| 17:42 | Iteration 2: Full gate — 41% pass rate | 11 |
| 17:48 | Iteration 2: Oracle: NO-SHIP | 12 |
| 17:49 | Iteration 3: Implementer starts | 13 |
| 17:55 | Iteration 3: Pre-gate attempt 1 FAIL, attempt 2 PASS | 14 |
| 17:56 | Iteration 3: Full gate — 41% pass rate | 14 |
| 18:01 | Escalation agent runs | 15 |
| 18:02 | **ESCALATED** | — |

### Scenario Generation Results

4 scenario files, 29 total scenarios (reduced from 35 after SA retry for assertion limits):
- `01-database-schema.test.ts` — 5 scenarios (FR-1)
- `02-api-endpoints.test.ts` — 9 scenarios (FR-2)
- `03-ui-components.test.ts` — 9 scenarios (FR-3)
- `04-cross-cutting.test.ts` — 6 scenarios (cross-cutting)

SA validation: 10/10 checks PASS. Warnings: 3 files use `toMatch` without `/m` flag.

### Implementation Results

| Iteration | Build | Lint | Typecheck | Scenarios | Pass Rate |
|-----------|-------|------|-----------|-----------|-----------|
| 1 | FAIL→PASS | FAIL | FAIL→PASS | 12/29 | 41% |
| 2 | PASS | PASS | PASS | 12/29 | 41% |
| 3 | PASS | FAIL→PASS | PASS | 12/29 | 41% |

**What passed consistently (12 tests):**
- `enforces unique constraint on userId column`
- `defines correct default values for user preferences`
- `defines validation schema with required field check`
- `uses strict zod schema that strips unknown fields`
- `validates currency code format in schema`
- `displays success message after successful save`
- `handles and displays error messages`
- `implements accessibility features for form`
- `includes USD as currency option in form`
- `shows email fallback placeholder for display name field`
- `returns structured error responses`
- `implements service layer for business logic separation`

**What failed consistently (17 tests):**
- Database: `creates user_preferences schema with all required columns`, `enables RLS on user_preferences table`, `creates migration with tenant isolation policy`
- API: `implements GET handler`, `implements PUT handler`, `handles missing preferences`, `ensures updatedAt`, `defines routes`, `defines response schema`
- UI: `implements settings page with form fields`, `handles form submission`, `disables save button`, `shows loading state`
- Cross-cutting: `implements logging`, `uses tenant-scoped context`, `integrates with auth middleware`, `applies rate limiting`

### Root Cause Analysis

The implementer consistently failed on the same 17 tests across all 3 iterations. The escalation report identified **4 critical blockers**:

1. **Missing database migration** — `drizzle-kit generate` was never executed, so migration file doesn't match schema. Schema uses `timestamp` instead of spec-required `timestamptz`.
2. **No RLS policy** — Row-Level Security for tenant isolation completely absent from migration.
3. **Mock authentication** — Frontend hardcodes `'mock-jwt-token'` instead of using Clerk `useAuth()`.
4. **Console-based logging** — No structured logging, missing correlation IDs.

The implementer focused on business logic and code quality (which it did well — 0 lint issues, proper error handling, good architecture) but missed the infrastructure requirements. The oracle feedback tried to redirect but the same gaps persisted.

### Pipeline Behavior Observations

**What worked well:**
- Spec validation: clean pass, no false positives
- SA generation: retry mechanism worked (failed assertion_upper_bound, regenerated successfully)
- SA validation: 10/10 checks, tsc graceful skip confirmed working
- Workspace check: passed with updated lint_cmd
- Smoke test: correctly identified healthy scenario crash pattern (pre-implementation)
- Pre-gate retry: automatically retried and detected improvements (build/lint/typecheck all eventually passed)
- Information barrier: caught oracle feedback leaking "10/25 scenarios" to implementer
- Iteration loop: completed all 3 iterations with proper feedback flow
- Escalation: generated comprehensive escalation report with root cause analysis

**What needs improvement:**
- Implementer stagnation: 0% improvement across 3 iterations (41% → 41% → 41%). The same 17 tests failed every time with the same root causes. The implementer received detailed feedback pointing at specific files and fix instructions but didn't execute them.
- Pre-gate instability: lint failures oscillated (FAIL→PASS→FAIL→PASS). The implementer sometimes introduced lint issues then fixed them on retry.
- Oracle feedback had to be regenerated due to barrier leakage. The pattern `\b\d+\s*/\s*\d+\s*(?:passed|failed|scenarios?|tests?)\b` correctly caught "10/25 scenarios" but cost an extra agent invocation.

---

## Progression Across All Runs (7 total)

| Run | Session | Blocked at | Root cause | Result |
|-----|---------|-----------|------------|--------|
| 1-3 | Session 1 | SA validation | tsc check failures in pnpm monorepo | Fixed in `0a2605b` |
| 4 | Session 2 | Spec validation | LLM false positive on Drizzle naming | Non-deterministic |
| 5 | Session 2 | Workspace check | Hardcoded `{test, lint, type-check}` scripts | Fixed in workspace_check.py |
| 6 | Session 3 | Workspace check | Config says `pnpm -r lint` but packages lack `lint` | Fixed: changed lint_cmd |
| 7 | Session 3 | Implementation (ESCALATED) | Implementer can't fix DB migration/RLS/auth gaps | 41% pass rate, 3 iterations |

### Pipeline Health Assessment

| Component | Status | Notes |
|-----------|--------|-------|
| Spec validation | WORKING | Passed cleanly, no false positives |
| SA generation | WORKING | 4 files, 29 scenarios, retry mechanism works |
| SA validation | WORKING | 10/10 checks pass, tsc graceful skip confirmed |
| Workspace check | WORKING | Correctly derived from config, passed |
| Smoke test | WORKING | Pre-implementation crash detection healthy |
| Pre-gate (build/lint/typecheck) | WORKING | Retry mechanism catches improvements |
| Implementation loop | WORKING (mechanically) | Loop executes but implementer stagnates |
| Information barrier | WORKING | Caught oracle leakage, forced regeneration |
| Review + Oracle | WORKING | Generated detailed feedback with fix instructions |
| Escalation | WORKING | Comprehensive report with root causes and estimates |

### Key Insights

1. **First complete pipeline run ever for this spec.** All components functioned correctly. The pipeline ran end-to-end for the first time.
2. **Implementer stagnation is the new bottleneck.** With all infrastructure/config issues resolved, the implementer's inability to address feedback is now the limiting factor.
3. **The feedback→fix loop isn't working.** The oracle gave specific, actionable feedback (file paths, code snippets, fix instructions) but the implementer didn't execute the fixes. This suggests the implementer may be operating within constraints (e.g., Docker environment limitations, missing tools) that prevent it from running `drizzle-kit generate` or integrating Clerk auth.
4. **SA scenario quality was excellent.** 29 scenarios with good coverage, proper free variable respect, and clean validation. The scenarios correctly caught real implementation gaps.
5. **Pre-gate improvements show the implementer CAN learn** — build and typecheck went from FAIL to PASS. But the deeper infrastructure issues (migrations, RLS, auth) require capabilities beyond code editing.
