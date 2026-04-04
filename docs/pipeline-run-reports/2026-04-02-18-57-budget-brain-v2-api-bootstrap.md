# Pipeline Run Report: budget-brain-v2 / api-bootstrap

**Date:** 2026-04-02 18:57 UTC
**Spec:** `2026-03-28-api-bootstrap`
**Repository:** budget-brain-v2
**Goal:** Test pipeline run for improvement/regression tracking

---

## Pre-Run Setup

- [x] Agents/templates synced to target repo
- [x] Docker images rebuilt (`dark-factory build node`) — all layers cached, build succeeded
- [x] Repository state reset (tracked files restored, untracked removed, runtime artifacts cleared)

---

## Run 1

**Start time:** 2026-04-02 19:01 UTC
**End time:** 2026-04-02 19:50 UTC (killed manually during iteration 2 review/oracle)
**Pipeline command:** `dark-factory run 2026-03-28-api-bootstrap`
**Run ID:** `a6bbdfd7db71421c`

### Stage Timings

| Stage | Approx Start (UTC) | Approx End (UTC) | Duration | Status |
|-------|---------------------|-------------------|----------|--------|
| Spec Validation | 19:01 | 19:03 | ~2 min | PASS (score 92, divergence LOW, 0 blockers) |
| Architecture | 19:03 | 19:05 | ~2 min | PASS (manifest generated) |
| Scenario Generation | 19:05 | 19:09 | ~4 min | PASS (6 scenario files, smoke test passed) |
| **Iteration 1** | | | | |
| Implementation (iter 1) | 19:09 | 19:19 | ~10 min | Completed (files created) |
| Pre-Gate (iter 1) | 19:19 | 19:19 | <1 min | PASS (build, lint, typecheck) |
| Quality Gate (iter 1) | 19:19 | 19:25 | ~6 min | PASS (static) / FAIL (server startup) |
| Scenario Testing (iter 1) | 19:25 | 19:27 | ~2 min | 0/0 — all skipped (server_error) |
| Review (iter 1) | 19:27 | 19:31 | ~4 min | Score: 53/100 |
| Oracle (iter 1) | 19:31 | 19:35 | ~4 min | NO-SHIP |
| **Iteration 2** | | | | |
| Implementation (iter 2) | 19:35 | 19:39 | ~4 min | Completed (no apparent changes) |
| Pre-Gate (iter 2) | 19:39 | 19:39 | <1 min | PASS |
| Quality Gate (iter 2) | 19:39 | 19:44 | ~5 min | PASS (static) / FAIL (server startup) |
| Scenario Testing (iter 2) | 19:44 | 19:47 | ~3 min | 0/0 — all skipped (server_error) |
| Review/Oracle (iter 2) | 19:47 | — | — | Killed during this phase |

### Observations

1. **Spec validation was clean.** Score 92, LOW divergence, 0 blockers, 3 minor ambiguity warnings ("appropriate", "etc." x2).

2. **Architecture manifest was well-formed.** Created 8 files in `file_map`, defined data models, API contracts, dependencies, and runtime config. The manifest correctly specified `server_setup_cmd`, `test_server_cmd`, and `test_base_url`.

3. **Scenario generation produced 6 files** covering health endpoint, error conditions, rate limiting, CORS/security, request handling, and success envelopes. Smoke test and scenario validation both passed.

4. **Implementation was structurally sound.** Build, lint, and type check all passed in both iterations. Code quality scored 20/20, security 15/15. The implementer created a well-structured Fastify app with proper plugin architecture.

5. **Server startup consistently failed in the scenario testing container.** The error was `ERR_MODULE_NOT_FOUND: Cannot find package 'fastify'`. This is the root cause of all failures.

6. **Iteration 2 made no meaningful progress.** The implementer received feedback about server startup failure but could not fix it because the issue is infrastructure-level (dependency availability in the server container), not a code bug.

### Root Cause Analysis: Server Startup Failure

The `build_cmd` in config.toml runs `pnpm install && pnpm run build` inside the **quality gate container**. This installs dependencies and compiles TypeScript. However, the **server container** (`df-{run_id}-server`) runs `cd apps/api && node dist/index.js` but does not have access to the `node_modules` installed during the build step.

The quality gate container and server container share the project bind mount, but:
- `pnpm install` in the quality gate container writes to `node_modules/` on the host
- The server container should see these files via the shared bind mount
- Yet the server reports `Cannot find package 'fastify'`

**Possible causes:**
- The server container starts before `pnpm install` has fully written to the bind mount
- pnpm's `.pnpm-store` or symlink structure isn't resolving correctly inside the server container
- The `server_setup_cmd` (`cd apps/api && pnpm build`) runs in a context that doesn't trigger `pnpm install`
- Workspace symlinks in `node_modules` may not resolve correctly when the pnpm store is outside the bind mount

**This is a pipeline infrastructure issue, not an implementation bug.** The implementer cannot fix it.

### Issues Encountered

| # | Issue | Severity | Category | Notes |
|---|-------|----------|----------|-------|
| 1 | Server container cannot find `fastify` dependency | **BLOCKING** | Pipeline infra | Prevents all behavioral scenario testing |
| 2 | Iteration loop wastes tokens on unfixable issue | Medium | Pipeline logic | Implementer gets feedback about ENV_ISSUE but has no way to fix container deps |
| 3 | Quality gate reports PASS despite server startup FAIL | Low | Reporting | The `Server Startup | FAIL` line is appended after `## Result: PASS`, making the gate result misleading |
| 4 | Scenario counts show 0/0 instead of N skipped | Low | Reporting | Should report e.g. "0/24 (24 skipped: server_error)" for clarity |

### Outcome

**Killed manually** after iteration 2 review/oracle phase. The same server startup failure repeated identically in both iterations with no path to resolution by the implementer agent.

---

## Summary & Metrics

| Metric | Value |
|--------|-------|
| Total pipeline duration | ~49 min (killed) |
| Iterations completed | 1 complete, 2nd in progress |
| Final outcome | Killed (blocking infra issue) |
| Blocking issues | 1 — server container can't resolve pnpm workspace dependencies |
| Spec validation score | 92 |
| Code quality score | 20/20 |
| Security score | 15/15 |
| Scenario pass rate | 0% (0/0, all skipped) |
| Review score (iter 1) | 53/100 |
| Oracle decision (iter 1) | NO-SHIP |

## Recommendations for Pipeline Improvement

1. **Fix server container dependency resolution.** The server container needs `pnpm install` (or equivalent) before running `node dist/index.js`. Options:
   - Run `server_setup_cmd` with an implicit `pnpm install` prefix
   - Ensure the pnpm store is within the bind mount so workspace symlinks resolve
   - Add a pre-start check in the server container that verifies dependencies exist

2. **Detect unfixable ENV_ISSUE earlier.** If the Oracle classifies a failure as `ENV_ISSUE` and the implementer has no mechanism to fix it, the pipeline should escalate immediately rather than burning another iteration on the same problem.

3. **Improve quality gate reporting.** The gate should clearly FAIL (not PASS) when `Server Startup` fails, or at minimum surface it prominently in the summary.

4. **Report skipped scenario counts.** When scenarios are skipped due to server_error, the count should reflect the total expected scenarios, not 0/0.
