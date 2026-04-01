# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware
**Date:** 2026-04-01 18:30 UTC
**Spec:** 2026-03-30-auth-schemas-middleware
**Repo:** budget-brain-v2
**Pipeline version:** latest (main), commits up to `1e3fc0a`
**Run ID:** 12649dee5ffd435d
**Previous run context:** Last run (17:19 UTC) escalated due to proxy intercepting DB connections. Fixes committed: `2ccd17f` (exempt service hostnames from NO_PROXY) and `1e3fc0a` (extract build_proxy_env_args).

---

## Run 1

### Pre-run state
- Previous pipeline state file removed (was `status=interrupted`, `iteration=2`, `last_decision=NO-SHIP`)
- Repo had modified tracked files and untracked implementation artifacts from prior runs
- Reset: `git checkout -- .` + `git clean -fd` (preserving specs, config, agents, templates, plans)
- Cleared state/scenarios/reports/audit artifacts for this spec

### Setup
- Updated agents and templates from dark-factory source
- Rebuilt Docker images: `dark-factory build node` (all cached, fast build)
- Full repo reset to clean state

### Pipeline Execution
- **Status:** ESCALATED (max iterations reached)
- **Start time:** ~18:30 UTC
- **End time:** ~19:20 UTC (~50 minutes total)
- **Iterations completed:** 2 (iter 0 + iter 1, escalation at iter 2 boundary)

### Timeline
| Time (UTC) | Step | Duration | Notes |
|------------|------|----------|-------|
| 18:30 | Pipeline start | - | Run ID 12649dee |
| ~18:32 | Spec Validation | ~2 min | PASS (score 88/100) |
| ~18:34 | Architecture Manifest | ~4 min | Manifest generated |
| ~18:38 | Scenario Generation | ~4 min | 5 files generated |
| ~18:42 | Implementation (iter 0) | ~10 min | Full auth system created |
| ~18:42 | Quality Gate - smoke test (iter 0) | ~1 min | 0/30 contracts, build issues |
| ~18:54 | Quality Gate - pre-gate (iter 1) | ~1 min | Build PASS after implementer fixed |
| ~18:55 | Quality Gate - full (iter 1) | ~6 min | Build/Lint/TC PASS, Scenarios PASS, Server FAIL |
| ~19:01 | Review prepass + Reviewer (iter 1) | ~4 min | Score: 40/60 deterministic, 85/100 review |
| ~19:05 | Oracle (iter 1) | ~1 min | NO-SHIP |
| ~19:06 | Implementation (iter 2) | ~6 min | Fixes attempted |
| ~19:12 | Quality Gate (iter 2) | ~3 min | Build/Lint/TC PASS, Scenarios PASS, Server FAIL |
| ~19:15 | Review + Oracle (iter 2) | ~5 min | NO-SHIP, ESCALATION |

### Spec Validation
- **Verdict:** PASS (score 88/100, divergence LOW, 0 blockers)
- 1 ambiguity warning: "etc." on line 21 (non-blocking)

### Scenario Generation
- Generated 5 files:
  - `00-tier1-contracts.test.ts` (30 contract tests)
  - `01-contract.scenarios.md`
  - `02-error-conditions.scenarios.md`
  - `03-security.scenarios.md`
  - `04-edge-cases.scenarios.md`

### Iteration 0 Results
- **Build:** FAIL (TypeScript errors)
  - Missing `.js` extensions for `node16` moduleResolution
  - Missing `auth.service` module declaration
  - Pino logger overload mismatches
- **Smoke test (tier-1 contracts):** 0/30 passed
  - Tests expected files at `apps/api/src/features/auth/auth.handlers.ts` etc. — files existed but build hadn't compiled
- **Outcome:** Moved to iteration 1 with fixes

### Iteration 1 Results
- **Pre-gate (Build/Lint/Typecheck):** ALL PASS
- **Blind scenarios (executable, tier-1):** 30/30 PASS (100%)
- **Server startup:** FAIL
  - `drizzle.config.json file does not exist` — drizzle-kit looking for JSON config
  - Root cause: `setup_cmd` in config.toml runs `cd apps/api && pnpm drizzle-kit push:pg`, so drizzle-kit looks for config in `apps/api/`. Implementer placed `drizzle.config.ts` at project root.
  - All behavioral/HTTP scenarios SKIPPED due to server not starting
- **Review prepass:** 40/60 (TEST_ANALYSIS=5/25, CODE_QUALITY=20/20, SECURITY=15/15)
- **Review score:** 85/100 — "exceptional spec compliance", perfect architecture, security
- **Oracle decision:** NO-SHIP
- **Feedback to implementer:** Fix drizzle config (EC-1), add tenant JWT validation (FR-3), add observability logging (FR-7)

### Iteration 2 Results
- **Pre-gate (Build/Lint/Typecheck):** ALL PASS
- **Blind scenarios (executable, tier-1):** 30/30 PASS (100%)
- **Server startup:** FAIL (same `drizzle.config.json file does not exist` error)
  - Implementer did NOT resolve the config placement/format issue
- **Review prepass:** 40/60 (same scores)
- **Oracle decision:** NO-SHIP
- **Escalation triggered** (max iterations reached)

### Root Cause Analysis: Server Startup Failure

The persistent server startup failure is caused by a **config.toml / implementer mismatch**:

1. **config.toml** specifies: `setup_cmd = "cd apps/api && pnpm drizzle-kit push:pg"`
2. This changes CWD to `apps/api/` before running drizzle-kit
3. Drizzle-kit looks for config at `apps/api/drizzle.config.json` (default path)
4. **Implementer** created `drizzle.config.ts` at the **project root** (not in `apps/api/`)
5. Additionally, drizzle-kit v0.20.x looks for `.json` by default, not `.ts`

This mismatch persisted across both iterations because the feedback correctly identified the symptom but the implementer never created a config file in the right location or changed the setup_cmd.

**NO_PROXY fix confirmed working:** The proxy-interception-of-DB-connections issue from the previous run (17:19 UTC) did NOT recur. The `2ccd17f` commit (exempt service hostnames from NO_PROXY) successfully resolved that infrastructure problem. Server errors this run were purely application-level (drizzle config not found).

### Key Observations

1. **NO_PROXY fix worked.** The critical infrastructure issue from the previous run (proxy intercepting postgres connections) is resolved. No `ECONNREFUSED :443` errors.

2. **Implementer can't fix drizzle config placement.** Two iterations of feedback about "missing drizzle configuration" didn't lead to the implementer placing the config in `apps/api/` or adjusting the setup_cmd. The feedback referenced EC-1 and TC-11/12/13 but didn't explicitly tell the implementer WHERE to put the file relative to the setup_cmd's CWD.

3. **Category tracking inconsistency persists.** `category-breakdown.env` shows `CAT_00_PASSED=0, CAT_00_TOTAL=30` while `scenario-counts.env` shows `SCENARIO_PASSED=30`. These should agree when there's only one category.

4. **Quality gate summary doesn't show Server Setup FAIL.** The `quality-gate-summary.md` shows all gates as PASS, but the full `quality-gate.md` has `| Server Setup | FAIL |` appended at the bottom. The summary is incomplete.

5. **Escalation report format issue.** The escalation report was written as a raw JSON blob (Claude's API response format) rather than clean markdown. The actual escalation content is nested inside a `result` field of the JSON.

6. **Iter 0 smoke test ran before build succeeded.** The smoke test at iter 0 ran tier-1 contracts against source files that existed but the build had failed. The 0/30 result is misleading — 27 tests actually found the files and checked content, while 3 failed because the specific files were missing. But since the build didn't succeed, the server couldn't have started anyway.

7. **Two-phase quality gate.** The pipeline appears to run two gate phases per iteration:
   - Phase 1 (smoke test): Runs tier-1 contracts only, before the full gate
   - Phase 2 (full gate): Runs pre-gate checks (build/lint/typecheck), then blind scenarios, then server setup + behavioral scenarios

### Outcome
**ESCALATED** — 2 iterations. 30/30 tier-1 contracts pass. Server startup blocked all behavioral scenarios. Code quality excellent but non-operational.

---

## Runs 2-4
_Not attempted — user requested kill and report after Run 1._

---

## Summary & Metrics
| Metric | Run 1 |
|--------|-------|
| Outcome | ESCALATED |
| Iterations | 2 |
| Contract Pass Rate (final) | 100% (30/30) |
| Server Startup | FAIL (both iters) |
| Behavioral Scenarios | 0% (all skipped) |
| Review Score | 85/100 |
| Duration | ~50 min |
| Failure Type | Config mismatch (drizzle.config location) |

## Pipeline Issues Identified

### Critical
1. **Drizzle config location mismatch.** `setup_cmd` CWDs into `apps/api/` but implementer places config at project root. Feedback doesn't explicitly communicate the CWD context to the implementer.

### Moderate
2. **Category tracking vs scenario counts disagree.** `category-breakdown.env` shows 0/30 while `scenario-counts.env` shows 30/30 for the same iteration.
3. **Quality gate summary omits Server Setup FAIL.** The summary table shows all PASS, but the full report has Server Setup FAIL at the bottom — not part of the structured table.
4. **Escalation report is raw JSON.** The oracle agent's output is the full Claude API response JSON, not clean markdown.

### Minor
5. **Iter 0 smoke test runs before build succeeds.** The smoke test can report misleading file-not-found errors when the real issue is a build failure.

## Comparison with Previous Run (17:19 UTC)

| Aspect | Run at 17:19 | Run at 18:30 |
|--------|-------------|-------------|
| NO_PROXY/proxy issue | FAIL (proxy intercepted postgres) | FIXED (no proxy errors) |
| Build (iter 0) | PASS | FAIL (TS errors) |
| Build (iter 1) | PASS | PASS |
| Tier-1 contracts | 0% -> ~100% | 0% -> 100% |
| Server startup | FAIL (proxy, then migration) | FAIL (drizzle config location) |
| Behavioral scenarios | 0% (skipped) | 0% (skipped) |
| Root cause | Infrastructure (proxy) | Application (config placement) |
| Oracle feedback leakage | 2 instances detected | Not flagged (check needed) |

**Key improvement:** The NO_PROXY fix (`2ccd17f`) successfully eliminated the proxy infrastructure blocker. The remaining blocker is now purely an application-level config issue — the implementer needs to understand the CWD context of the setup_cmd.
