# Pipeline Run Report: budget-brain-v2 / api-bootstrap

**Date:** 2026-04-02 16:36 UTC
**Spec:** 2026-03-28-api-bootstrap
**Target Repo:** budget-brain-v2
**Pipeline Version:** latest (main branch, commit ffc6a70)
**Run ID:** 85b5dd80a6a94d8b

---

## Pre-Run Setup

### Repo State Before Reset
- 2 untracked files in `.dark-factory/reviews/` (auth-schemas-middleware validation artifacts from prior run)
- All tracked files clean
- Runtime artifacts from previous runs (monorepo-tooling, auth-schemas-middleware) present in reports/, reviews/, audit/, state/, shipped-scenarios/

### Agents/Templates Update
- Copied latest agents/ and templates/ from dark-factory to budget-brain-v2/.dark-factory/
- No issues

### Docker Image Build
- `dark-factory build node` completed successfully
- All layers cached (proxy, base, node, playwright, playwright-node)
- 2 warnings: `InvalidDefaultArgInFrom` for proxy and base Dockerfiles (cosmetic, non-blocking)
- claude-code@2.1.63 verified

### Repo Reset
- Ran `git clean -fd` to remove untracked files
- Removed runtime artifacts from reports/, reviews/ (untracked), audit/, state/, shipped-scenarios/
- Restored tracked files (monorepo-tooling reviews + shipped-scenarios) via `git checkout`
- Final state: clean working tree

---

## Pipeline Run 1

### Start Time
- 16:38 UTC (from audit log first CONTAINER entry)

### Spec Pre-Check
- 3 ambiguity warnings (non-blocking):
  - "appropriate" on line 223 (EC-9 CORS description)
  - "etc." on line 235 (Non-Error thrown description)
  - "etc." on line 425 (FV-5 AppError inheritance)
- Proceeded despite warnings

### Agent Phases

#### Spec Validation (16:38 - 16:39)
- **Status:** PASS
- **Duration:** ~100 seconds
- **Score:** 91/100, divergence=LOW, blockers=0
- **Model:** claude-sonnet-4-20250514
- **Observations:** Clean pass, no issues

#### Architecture (16:39 - 16:42)
- **Status:** PASS
- **Duration:** ~140 seconds
- **Observations:** Produced 6.3KB architecture manifest. Correctly identified monorepo structure, TypeScript/ESM conventions, pnpm workspaces. Identified `apps/api` as target directory.

#### Scenario Generation (16:42 - 16:45)
- **Status:** PASS
- **Duration:** ~180 seconds
- **Observations:** Generated 5 scenario files:
  - 01-server-contract.scenarios.md (1.9KB)
  - 02-error-handling.scenarios.md (2.7KB)
  - 03-health-checks.scenarios.md (2.6KB)
  - 04-security-logging.scenarios.md (3.2KB)
  - 05-edge-cases.scenarios.md (3.4KB)
- Scenario validation: PASS (5 files, min 3, all non-empty, all have sections)
- Smoke test: PASS (mutation testing skipped)

#### Implementation - Iteration 1 (16:45 - 16:54)
- **Status:** Completed with pre-gate fix cycle
- **Duration:** ~9 minutes (including 2 pre-gate attempts)
- **Observations:**
  - First pre-gate FAIL: 3 lint errors
    - 2x `@typescript-eslint/no-explicit-any` in logger.ts
    - 1x `no-undef` for `crypto` in server.ts
  - Implementer self-corrected (ran 3 container invocations total in this phase)
  - Second pre-gate: PASS (build, lint, type check all pass)
  - Files created: `apps/api/src/server.ts`, `apps/api/src/common/` (config.ts, logger.ts, errors.ts, error-handler.ts, health.ts), `apps/api/drizzle.config.ts`, `apps/api/src/db/schema/index.ts`
  - **Issue:** Created database files (drizzle.config.ts, db/schema/index.ts) not in spec scope

#### Quality Gate - Iteration 1 (16:55 - 17:02)
- **Status:** PARTIAL PASS (static gates pass, server startup FAIL)
- **Duration:** ~7 minutes
- **Gate Results:**
  - Build: PASS
  - Lint: PASS
  - Type Check: PASS
  - Self-Tests: SKIP (no command configured)
  - Mutation Testing: SKIP
  - **Server Startup: FAIL**
- **Scenario Test Results:** 0 passed / 0 failed / 0 total (all skipped due to server error)
- **Observations:** Server + postgres containers spun up but server failed to respond to HTTP requests

#### Review - Iteration 1 (17:02 - 17:04)
- **Status:** Completed
- **Duration:** ~2 minutes
- **Deterministic Pre-Pass:** 35/60
  - Test Results Analysis: 0/25 (server_error pattern)
  - Code Quality: 20/20
  - Security: 15/15

#### Oracle Decision - Iteration 1 (17:04 - 17:05)
- **Decision:** NO-SHIP
- **Observations:**
  - Semantic leakage detected in feedback: pattern `\b\d+%\s*(?:pass|fail)` matched `'0% pass'` twice
  - Oracle correctly identified server startup as critical blocker
  - Feedback classifications: FR-1=SCENARIO_CRASH, FR-2=SPEC_AMBIGUOUS, FR-3=IMPL_BUG, FR-4=SCENARIO_CRASH

---

#### Implementation - Iteration 2 (17:06 - 17:08)
- **Status:** Completed
- **Duration:** ~4 minutes (faster, built on previous work)
- **Observations:**
  - Pre-gate: PASS (build, lint, type check)
  - deleted package-lock.json (fully committing to pnpm)
  - pnpm-lock.yaml grew significantly (+1682 lines)
  - drizzle.config.ts and db/ directory STILL present despite feedback recommending removal

#### Quality Gate - Iteration 2 (17:08 - 17:17)
- **Status:** PARTIAL PASS (same pattern as iteration 1)
- **Gate Results:** Build PASS, Lint PASS, Type Check PASS, Self-Tests SKIP, Server Startup FAIL
- **Scenario Tests:** 0/0/0 again
- **Observations:** Identical failure pattern. Server still not responding to HTTP requests.

#### Review - Iteration 2 (17:17 - 17:19)
- **Status:** Completed
- **Deterministic Pre-Pass:** 35/60 (identical to iteration 1)
  - Test Results Analysis: 0/25
  - Code Quality: 20/20
  - Security: 15/15

#### Oracle Decision - Iteration 2 (17:19 - 17:22)
- **Decision:** NO-SHIP
- **Feedback:** Comprehensive 6KB document correctly identifying:
  - Server startup failure as critical blocker
  - Database dependencies as scope creep
  - Module resolution/ESM compatibility as likely root cause
  - All FRs classified as IMPL_BUG

---

#### Escalation (17:22 - 17:26)
- Pipeline exhausted iteration budget (max 2 NO-SHIP iterations before escalation)
- Escalation report generated (large, verbose)
- Final status: `interrupted` (escalated)

### Pipeline Metrics (from metrics.jsonl)
- **Wall time:** 2717 seconds (~45.3 minutes)
- **Total turns:** 1000
- **Iterations:** 2
- **Final status:** escalated

---

## Key Issues Identified

### 1. Server Startup Failure (Root Cause: Unknown from pipeline perspective)
- The implemented server logs startup success but cannot handle HTTP requests
- This blocked ALL scenario testing across both iterations
- Likely causes: ESM/CJS module issues, database connection failures, port binding problems
- The implementer could not self-diagnose because it has no access to the quality gate's server startup logs

### 2. Scope Creep: Database Dependencies
- The spec explicitly covers only server bootstrap (Fastify, logging, error handling, health checks)
- The implementer added drizzle-orm, pg, and database schema files not in the spec
- Iteration 2 feedback explicitly called out database removal, but implementer did not remove them
- This likely contributed to the server startup failure (database connection attempt with no database)

### 3. Semantic Leakage in Oracle Feedback
- Oracle feedback contained `'0% pass'` pattern twice
- Leakage detection caught it but the feedback was still delivered to the implementer
- This is a defense-in-depth concern: leakage was detected but not blocked

### 4. Scenario Count: Zero Across All Iterations
- The scenario-counts.env file was only written once (after iteration 1) and never updated
- Iteration 2 quality gate produced the same 0/0/0 pattern but didn't update scenario-counts.env
- The scenario-counts.env timestamp stayed at 10:02 while quality-gate.md was updated at 10:17

### 5. Implementer Feedback Loop Ineffective for Server Startup Issues
- The Oracle feedback correctly identified server startup as the problem
- But the feedback is necessarily vague (can't reference specific test output)
- The implementer couldn't debug the actual runtime error because:
  a) It doesn't see quality gate logs
  b) It can't run the server in the same Docker networking context
  c) The HOST=0.0.0.0 injection (recently added in commit 84ec1cb) may not be reaching the app correctly

### 6. Quality Gate Summary Initially Empty
- The initial quality-gate-summary.md had no gates listed (empty table)
- It was later overwritten with proper content
- Suggests a race condition or early-write issue in the quality gate flow

---

## Overall Observations

1. **Pipeline mechanics worked correctly:** All phases ran in order, barriers were enforced, containers were properly isolated, escalation happened on schedule
2. **Spec validation and scenario generation were strong:** 91/100 spec score, 5 comprehensive scenario files
3. **Code quality was excellent:** 20/20 on code quality, 15/15 on security, zero lint/type issues (after self-fix)
4. **Runtime verification is the Achilles' heel:** The entire pipeline value proposition depends on the server starting in Docker, and when it doesn't, the pipeline has no way to recover
5. **Iteration feedback doesn't help with infrastructure problems:** The Oracle can describe symptoms but can't help debug Docker networking, module resolution, or port binding issues
6. **The pipeline correctly escalated:** After 2 iterations of the same fundamental problem, escalation was the right call

## Improvement Recommendations

### Pipeline Improvements
1. **Server startup diagnostics:** Capture and surface server container stdout/stderr when server startup fails. Include these in the quality gate report so the implementer can see the actual error.
2. **Pre-implementation server smoke test:** Before the full implementer runs, verify that a basic "hello world" server can start in the Docker container with the project's configuration. This catches infrastructure issues early.
3. **Scope guard in implementer prompt:** Explicitly instruct the implementer NOT to add dependencies or files outside the spec's scope. The architecture manifest should serve as a checklist.
4. **Semantic leakage blocking (not just detection):** When leakage is detected in Oracle feedback, sanitize or rewrite the feedback before delivering it.
5. **Iteration-specific scenario counts:** Update scenario-counts.env on each iteration, not just the first.
6. **HOST=0.0.0.0 verification:** The recent commit 84ec1cb added HOST injection — verify it's working for Fastify servers specifically (Fastify needs `host: '0.0.0.0'` in `listen()` call, not just an env var).

### Spec Improvements
1. **Add explicit "not in scope" section** listing database, auth, and other deferred features to prevent implementer scope creep
2. **Add a minimal server startup test command** the implementer can use to self-verify
