# Pipeline Run Report: budget-brain-v2 / api-bootstrap

**Date (UTC):** 2026-04-02 20:35
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Dark Factory Version:** latest (main branch)
**Run ID:** e4616b55344c400f

---

## Pre-Run State

- Previous pipeline run artifacts exist (state, scenarios, reports, reviews, architecture)
- Modified tracked files: `apps/api/package.json`, `apps/api/src/index.ts`, `apps/api/tsconfig.tsbuildinfo`, `package.json`, `pnpm-lock.yaml`
- Untracked files from prior run: `apps/api/src/common/`, `apps/api/src/server.ts`, `.dark-factory/architecture/`, `.dark-factory/reviews/`
- Repo reset and artifacts cleaned before running

---

## Run 1

### Setup
- [x] Agents/templates synced (already in sync, no diff)
- [x] Docker images rebuilt (all cached, no changes)
- [x] Repo reset (tracked files restored, untracked removed, .dark-factory runtime cleaned)

### Pipeline Execution
- **Start time (UTC):** 2026-04-02 20:36
- **End time (UTC):** 2026-04-02 21:40 (approx)
- **Final status:** ESCALATED (interrupted after max iterations)
- **Iterations completed:** 3 (all NO-SHIP)
- **Total duration:** ~64 minutes

### Phase Observations

#### Spec Validation
- **Status:** PASS (score 92/100, divergence LOW, 0 blockers)
- **Duration:** ~2 minutes
- **Notes:** 3 ambiguity warnings on spec (ambiguous terms: "appropriate", "etc." x2). Proceeded despite warnings.

#### Architecture
- **Status:** PASS
- **Duration:** ~2 minutes
- **Notes:** Manifest generated with 8 files mapped, 3 data models, 1 API contract. Specified `test_base_url: http://localhost:3001` (port 3001 per spec), but config.toml overrides to port 3000. Runtime section included `server_setup_cmd` and `test_server_cmd`.

#### Scenario Generation
- **Status:** PASS (3 scenario files)
- **Duration:** ~2 minutes
- **Notes:** Generated 3 files: `01-contract-happy-path`, `02-error-conditions`, `03-edge-cases-security`. Scenario validation passed (file count, non-empty, has sections all OK).

#### Implementation (Iteration 1)
- **Status:** Completed, code produced
- **Duration:** ~4 minutes
- **Notes:** Created `apps/api/src/server.ts`, `apps/api/src/common/` (config.ts, logger.ts, errors.ts, error-handler.ts, health.ts), modified `apps/api/package.json`, `apps/api/src/index.ts`, `pnpm-lock.yaml`

#### Pre-Gate (Iteration 1)
- **Status:** FAIL
- **Issues:**
  1. Build FAIL: pnpm lockfile out of date (implementer added deps but didn't run `pnpm install`)
  2. Lint FAIL: `opts` unused variable in `error-handler.ts`
  3. Type Check FAIL: `apps/web` pre-existing type errors

#### Pre-Gate (Iteration 1, after implementer fix)
- **Status:** FAIL (1 remaining issue)
- **Issues:** Lint FAIL: `opts` unused variable in `error-handler.ts` (1 error)
- Build PASS, Type Check PASS

#### Quality Gate (Iteration 1)
- **Status:** FAIL
- **Gate results:** Build PASS, Lint FAIL, Type Check PASS, Self-Tests SKIP, Server Startup FAIL
- **Scenario results:** 0 passed, 0 failed, 0 total (server never became healthy)
- **Server log:** Server started successfully on port 3000, but pipeline reported startup failure

#### Review (Iteration 1)
- **Score:** 45/100
- **Duration:** ~2 minutes
- **Notes:** Code quality 15/20, Security 15/15, Spec compliance 12/30

#### Oracle (Iteration 1)
- **Decision:** NO-SHIP
- **Duration:** ~15 minutes (unusually long)
- **Key feedback:** Port 3000 vs spec 3001, lint failure, startup log format, module resolution

---

#### Implementation (Iteration 2)
- **Duration:** ~4 minutes
- **Notes:** Fixed lint error, fixed lockfile issue

#### Pre-Gate (Iteration 2)
- **Status:** PASS (Build PASS, Lint PASS, Type Check PASS)

#### Quality Gate (Iteration 2)
- **Status:** FAIL
- **Gate results:** Build PASS, Lint PASS, Type Check PASS, Self-Tests SKIP, Server Startup FAIL
- **Scenario results:** 0 passed, 0 failed, 0 total
- **Server log:** Identical to iteration 1 - server started on port 3000, listening successfully, but pipeline reports startup failure

#### Review (Iteration 2)
- **Score:** 68/100 (improved from 45)
- **Notes:** Code quality 20/20, Security 15/15, Spec compliance 25/30

#### Oracle (Iteration 2)
- **Decision:** NO-SHIP
- **Key feedback:** Runtime dependency resolution failure - `@fastify/cors` not found when running compiled dist/server.js

---

#### Iteration 3
- **Status:** NO-SHIP → ESCALATED
- **Notes:** Same server startup failure pattern. Pipeline reached max iterations and generated escalation report.

---

### Critical Issue: Server Startup Failure (Pipeline Bug)

**Symptom:** Server logs show successful startup on port 3000, but pipeline reports "Server Startup FAIL" and 0 scenarios execute.

**Server container log (both iterations):**
```
Server listening at http://127.0.0.1:3000
Server listening at http://172.25.0.3:3000
Server started
    port: 3000
    env: "development"
```

**Root cause analysis:**

1. **Port mismatch (unlikely):** Config has `base_url = "http://localhost:3000"`, pipeline publishes port 3000 and sets `PORT=3000`. Server listens on 3000. Port matches.

2. **Health endpoint unreachable (likely):** The `_wait_for_server()` function polls `http://localhost:3000/health` from the host. The port is published to `127.0.0.1:3000`. However, the server container uses a **tmpfs overlay on node_modules** (`build_platform_isolation_overlays`). The setup command (`cd apps/api && pnpm run build`) compiles TypeScript, but when `node dist/index.js` runs, the runtime dependencies (fastify, pino, etc.) may not be resolvable because:
   - The tmpfs overlay replaces the host's node_modules
   - `pnpm install` is prepended by `_dep_install_prefix()`, but it runs at the project root, not inside `apps/api`
   - In a pnpm workspace, dependencies are hoisted/linked. The tmpfs overlay likely breaks the symlink chain.

3. **Contradiction in server logs:** The server logs show successful startup, but the oracle/reviewer says `ERR_MODULE_NOT_FOUND` for `@fastify/cors`. This suggests:
   - The server starts its Node.js process
   - Fastify itself loads (or a partial import works)
   - But route registration fails silently, leaving no health endpoint registered
   - OR: The server logs come from `pnpm install` output + a partial startup that crashes after logging

**Impact:** This is a **systematic pipeline infrastructure issue** for pnpm workspace monorepos. The implementer cannot fix it because the server startup happens in a different container (the quality gate's server container), not the implementer's container. The code is correct but the deployment environment breaks dependency resolution.

### Additional Observations

1. **Oracle latency:** The Oracle agent took 15 minutes in iteration 1 and 8-10 minutes in iteration 2. This is slow for what should be a decision-making step.

2. **Review quality:** The reviewer fabricated error evidence. In iteration 2, it cited `Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@fastify/cors'` but this error message doesn't appear in any pipeline artifacts. The reviewer appears to be speculating about the root cause rather than citing actual evidence.

3. **Feedback quality:** The Oracle/feedback writer changed the root cause diagnosis between iterations:
   - Iter 1: "Import path resolution failures (.js extensions)"
   - Iter 2: "Runtime dependency resolution failure"
   Neither was verifiable from the available evidence since the actual health check failure reason isn't captured in the reports.

4. **Spec ambiguity:** Spec says PORT default 3001, but config.toml says base_url port 3000. The architect's manifest tried to set port 3001 via `test_base_url`, but config.toml takes precedence. This created confusion in feedback but wasn't the actual failure cause.

5. **Pre-gate runs twice:** In iteration 1, the pre-gate ran, failed, the implementer fixed issues, then it ran again. This is expected behavior (the implementer gets to fix static issues before the full quality gate).

### Key Metrics
- **Total pipeline duration:** ~64 minutes
- **Iterations:** 3 (all NO-SHIP)
- **Static gates (iteration 2+):** All PASS (Build, Lint, Type Check)
- **Runtime gates:** All FAIL (Server Startup)
- **Scenario pass rate:** 0% (all iterations)

### Findings & Recommendations

#### Pipeline Issues to Fix
1. **CRITICAL: Server container dependency resolution in pnpm workspaces** - The tmpfs overlay on node_modules breaks pnpm's symlink-based dependency resolution. The `_dep_install_prefix()` runs `pnpm install` at the project root, but the tmpfs overlay may not properly propagate hoisted dependencies to workspace packages. This needs investigation and a fix in `build_platform_isolation_overlays()` or the server container startup flow.

2. **MEDIUM: Health check failure details not captured** - When `_wait_for_server()` times out, the error message says "Server did not become healthy" but doesn't capture what the server returned (connection refused, 404, 500, etc.). Adding the actual error to the quality gate report would help diagnose issues.

3. **MEDIUM: Server container stdout/stderr not captured for health check phase** - The server log file only captures the startup phase. If the server crashes after startup (e.g., during route registration), that error wouldn't be captured.

4. **LOW: Oracle agent latency** - 15 minutes for the oracle decision in iteration 1 seems excessive. May need investigation into prompt size or model performance.

5. **LOW: Reviewer evidence fabrication** - The reviewer cited specific error messages that don't appear in pipeline artifacts. The reviewer prompt may need adjustment to emphasize citing only evidence from actual reports, not speculating.

#### Config Issues
6. **LOW: Config/manifest port mismatch** - config.toml sets port 3000, manifest says 3001 per spec. The `patch_config_from_runtime` fallback logic means config always wins. Either the config should not set `base_url` (let the manifest decide), or the manifest should defer to config. The current behavior is correct (config takes precedence) but creates confusion in feedback.
