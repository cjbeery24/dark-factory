# Pipeline Run Report: budget-brain-v2 / auth-schemas-middleware
**Date:** 2026-04-01 21:17 UTC
**Spec:** 2026-03-30-auth-schemas-middleware
**Repo:** budget-brain-v2
**Pipeline version:** latest (main)
**Previous run context:** Last run (18:30 UTC) escalated after 2 iterations. Server startup failed both times due to drizzle config location mismatch (implementer placed config at project root, but setup_cmd CWDs into apps/api/). Contract tests passed 30/30. Since then, pipeline gained runtime environment manifest support (arch manifest can declare runtime context, pipeline patches config from it).

---

## Run 1

### Pre-run state
- Repo has modified tracked files and untracked implementation artifacts from prior runs
- Reset: full clean (restore tracked, delete untracked, clear .dark-factory artifacts)

### Setup
- Updated agents and templates from dark-factory source
- Rebuilt Docker images: `dark-factory build node`
- Full repo reset to clean state

### Pipeline Execution
- **Status:** INTERRUPTED (spec validation failed)
- **Run ID:** 2e9795c92b154336
- **Duration:** ~3 minutes

### Spec Validation
- **Verdict:** PASS (score 89/100)
- **Divergence:** HIGH
- **Blockers:** 3 (missing dependencies, no DB config, no Fastify setup)
- **Pipeline result:** REJECTED — `divergence=HIGH (HIGH not allowed)`

The validator gave a passing score (89) and verdict (PASS), but rated divergence as HIGH because the current API is essentially empty (single 5-line file). The pipeline correctly blocked on HIGH divergence per `detection.py:157`.

### Key Observation: Non-deterministic Divergence Rating

**This is the same spec that passed validation with `divergence=LOW` in the 18:30 UTC run (score 88).** The codebase state is identical (both started from clean repo). The only difference is LLM non-determinism in the validator's divergence assessment.

This is a significant finding:
- The spec-validation divergence rating is unreliable across runs
- The same spec + codebase can get LOW or HIGH depending on the LLM call
- This means specs can non-deterministically fail validation, wasting pipeline runs

### Outcome
**INTERRUPTED** at spec validation. No implementation attempted.

---

## Run 2

### Pre-run state
- Repo already clean from Run 1 (no implementation was attempted)
- Clearing state file and retrying

### Setup
- No changes needed (agents/templates/images already current from Run 1)

### Pipeline Execution
- **Status:** ESCALATED (max iterations reached)
- **Run ID:** ff59d8e2a6b34c2a
- **Duration:** ~45 minutes
- **Iterations completed:** 2 (iter 1 + iter 2)

### Spec Validation
- **Verdict:** PASS (score 87/100, divergence LOW, 0 blockers)
- Passed this time (vs HIGH divergence in Run 1) — confirms non-determinism

### Architecture Manifest
- Manifest generated with runtime section (new feature)
- `runtime.server_setup_cmd` set to `cd apps/api && pnpm drizzle-kit push:pg`
- `drizzle.config.ts` in file_map at project root (same mismatch as prior runs)
- Manifest also declared `@neondatabase/serverless` as dependency

### Scenario Generation
- Same 5 files as prior runs (30 tier-1 contracts)

### Iteration 1 Results
- **Build:** FAIL (initial), then implementer fixed
  - Same node16 moduleResolution `.js` extension issues
  - Missing module declarations
- **Pre-gate after fix:** Build PASS, Lint FAIL, Type Check PASS
  - Lint: `drizzle.config.ts` at root — `no-undef` (process) + `no-non-null-assertion`
- **Blind scenarios:** 30/30 PASS (100%)
- **Server Startup:** FAIL — `Can't find meta/_journal.json file`
  - Implementer's migrate.ts calls drizzle-orm migrator expecting migration files
  - `setup_cmd` runs `drizzle-kit push:pg` (push mode, not migrate mode)
  - These are incompatible: push doesn't create migration files, but migrate.ts expects them
- **Review prepass:** 35/60 (CODE_QUALITY=15 due to lint fail)
- **Oracle:** NO-SHIP
- **Feedback:** Correctly identified migration config issue, lint violations

### Iteration 2 Results
- **Pre-gate:** Build PASS, Lint PASS, Type Check PASS (all fixed!)
- **Blind scenarios:** 30/30 PASS (100%)
- **Server Startup:** FAIL — `TypeError: fetch failed` in `@neondatabase/serverless`
  - **Root cause:** Implementer used `@neondatabase/serverless` driver which requires Neon's HTTP proxy
  - Pipeline provides standard PostgreSQL container (`postgres:16-alpine`)
  - Neon driver uses `fetch()` to connect over HTTP, standard postgres uses TCP
  - **This is a fundamental spec/infrastructure mismatch** — the driver literally cannot connect to a non-Neon postgres
- **Review prepass:** 40/60 (CODE_QUALITY=20 restored)
- **Oracle:** NO-SHIP
- **Escalation triggered** (max iterations)

### Escalation Report
- Still raw JSON format (same bug as prior runs — not clean markdown)
- Correctly identifies environmental blockers, not implementation quality issues
- Misidentifies root cause as "Neon instance not accessible" — actually it's driver incompatibility with standard postgres

### Key Observations

1. **Neon serverless driver incompatibility is the core blocker.** The spec mentions `@neondatabase/serverless` and the implementer uses it faithfully. But the pipeline's postgres service is standard PostgreSQL. The Neon HTTP driver cannot connect to standard postgres. This is a spec/infrastructure mismatch that no amount of iteration will fix.

2. **Non-deterministic divergence confirmed.** Same spec + same codebase state → Run 1 got `divergence=HIGH` (rejected), Run 2 got `divergence=LOW` (passed). This wastes pipeline runs.

3. **Category breakdown bug persists.** `scenario-counts.env` shows 30/30 PASS but `category-breakdown.env` shows `CAT_00_PASSED=0, CAT_00_TOTAL=30`. These should agree.

4. **Quality gate summary omits Server Startup FAIL.** Summary shows `Result: PASS`, but full report appends `| Server Startup | FAIL |` outside the structured table. This is misleading.

5. **Escalation report is still raw JSON.** Same bug as the 18:30 UTC run.

6. **Migration approach confusion.** Iter 1 failed because the code uses `migrate()` (expects migration files) but `setup_cmd` uses `push:pg` (pushes schema directly). Iter 2 the implementer switched to Neon HTTP migrator which fails for a different reason (can't reach Neon proxy).

7. **Runtime manifest didn't help.** The new runtime manifest feature emitted `server_setup_cmd` but the core issue (Neon driver incompatibility) is beyond what config patching can fix.

### Outcome
**ESCALATED** — 2 iterations. 30/30 tier-1 contracts pass. Server startup failed both iterations. Code quality excellent but non-operational due to Neon driver / standard postgres incompatibility.

---

## Run 3

### Pre-run state
- Same spec, same fundamental Neon driver issue
- This run is to verify behavior consistency and check if the implementer ever chooses a different driver

### Setup
- No config changes (testing same conditions)
- Full repo reset

### Pipeline Execution
- **Status:** ESCALATED (max iterations reached)
- **Run ID:** 1513a03c46d142f4
- **Duration:** ~50 minutes
- **Iterations completed:** 2 (iter 1 + iter 2)

### Spec Validation
- **Verdict:** PASS (score 92/100, divergence LOW, 0 blockers)
- Highest score across all 3 runs (89, 87, 92)

### Architecture Manifest
- **Key difference:** Architect chose `postgres` (standard TCP client) instead of `@neondatabase/serverless`
- This is a better match for the pipeline's standard postgres container
- Still placed `drizzle.config.ts` at project root

### Scenario Generation
- Same structure: 5 files, 30 tier-1 contracts

### Iteration 1 Results
- **Pre-gate:** Build PASS, Lint PASS, Type Check PASS (first try!)
  - Best first-attempt result across all runs
- **Blind scenarios:** 30/30 PASS (100%)
- **Server Startup:** FAIL — but server logs show it started successfully!
  - Logs: `Migrations completed successfully`, `Server listening at http://0.0.0.0:3000`
  - The quality gate recorded it as FAIL despite successful startup logs
  - **Root cause unclear:** Possible Docker networking issue preventing HTTP scenarios from reaching the server, or health check timing
- **Review prepass:** 40/60 (SERVER_ERROR=1)
- **Oracle:** NO-SHIP
- **Feedback:** Notes "disconnect between server startup and HTTP scenario execution" — correctly identifies that the server appears to start but tests can't reach it

### Iteration 2 Results
- **Pre-gate:** Build PASS, Lint PASS, Type Check PASS
- **Blind scenarios:** 30/30 PASS (100%)
- **Server Startup:** FAIL again — same logs showing successful startup
  - `Running database migrations...`
  - `Looking for migrations in: /project/drizzle`
  - `Migrations completed successfully`
  - `Server listening at http://0.0.0.0:3000`
  - `Budget Brain API server listening on 0.0.0.0:3000`
- **Review prepass:** 40/60 (SERVER_ERROR=1)
- **Oracle:** NO-SHIP
- **Escalation triggered**

### Key Observations

1. **Standard postgres driver used — correct choice.** The architect chose `postgres` (standard TCP) instead of `@neondatabase/serverless`. The implementer followed suit with `drizzle-orm/postgres-js`. This eliminates the driver incompatibility issue from Run 2.

2. **Server starts but quality gate reports FAIL.** Server logs clearly show successful startup (migrations + listening), but the quality gate marks it as FAIL. The HTTP scenarios couldn't reach the server. This is a Docker networking or timing issue, not an application issue.

3. **Clean first-attempt pre-gate.** Unlike Runs 1-2 where the implementer always had build/lint/typecheck failures on first attempt, Run 3 passed all three on the first try. Better implementation quality.

4. **Migration approach improved.** Implementer used programmatic migration with `drizzle-orm/postgres-js/migrator` and placed migration files in `/project/drizzle`. The `migrate()` call succeeded (`Migrations completed successfully`).

5. **Category breakdown bug persists.** Still shows `CAT_00_PASSED=0, CAT_00_TOTAL=30` while scenario-counts shows 30/30.

### Outcome
**ESCALATED** — 2 iterations. 30/30 tier-1 contracts pass. Server starts successfully but quality gate reports FAIL. Implementation quality best yet across all runs. The blocking issue is now Docker networking or quality gate server detection, not application code.

---

## Run 4
_Not attempted — user requested stop after Run 3._

---

## Summary & Metrics

| Metric | Run 1 | Run 2 | Run 3 |
|--------|-------|-------|-------|
| Outcome | INTERRUPTED | ESCALATED | ESCALATED |
| Spec Validation | FAIL (HIGH div) | PASS (LOW div) | PASS (LOW div) |
| Validation Score | 89 | 87 | 92 |
| Iterations | 0 | 2 | 2 |
| Pre-gate (first attempt) | N/A | FAIL (build) | PASS |
| Pre-gate (final) | N/A | PASS | PASS |
| Contract Pass Rate | N/A | 100% (30/30) | 100% (30/30) |
| Server Startup | N/A | FAIL (Neon driver) | FAIL (reports FAIL, logs show success) |
| Behavioral Scenarios | N/A | 0% (skipped) | 0% (skipped) |
| Review Prepass | N/A | 40/60 | 40/60 |
| Duration | ~3 min | ~45 min | ~50 min |
| Postgres Driver | N/A | @neondatabase/serverless | postgres (standard) |

## Pipeline Issues Identified

### Critical
1. **Server startup detection is broken.** In Run 3, the server clearly started (logs show migrations + listening), but the quality gate reported FAIL. This means the pipeline can't ship even correct implementations. Likely cause: Docker networking between server container and quality gate container, or health check timing/path.

2. **Non-deterministic divergence rating.** Identical spec + codebase state → Run 1 got HIGH (rejected), Runs 2-3 got LOW (passed). This wastes pipeline runs and is unpredictable. Scores varied 87-92 across runs.

### Moderate
3. **Category breakdown always shows 0/30.** `category-breakdown.env` consistently shows `CAT_00_PASSED=0` while `scenario-counts.env` shows 30/30 PASS. These disagree across all runs.

4. **Quality gate summary omits Server Startup FAIL.** Summary table shows `Result: PASS` but full report appends `| Server Startup | FAIL |` outside the structured table. This is misleading — the gate passes but server fails.

5. **Escalation report is raw JSON.** All runs produce the escalation report as a JSON blob (Claude API response format) rather than clean markdown.

6. **Architect can choose incompatible drivers.** Run 2's architect chose `@neondatabase/serverless` which is fundamentally incompatible with the pipeline's standard postgres container. The architect has no visibility into the pipeline's service infrastructure.

### Minor
7. **Smoke test summary file has duplicate entries.** Shows "Result: FAIL" repeated 4 times.

## Comparison Across All Runs (18:30 UTC through 21:17 UTC)

| Aspect | 18:30 UTC | Run 1 | Run 2 | Run 3 |
|--------|-----------|-------|-------|-------|
| Spec validation | PASS (88, LOW) | FAIL (89, HIGH) | PASS (87, LOW) | PASS (92, LOW) |
| Build first attempt | FAIL | N/A | FAIL | PASS |
| Server startup | FAIL (config path) | N/A | FAIL (Neon driver) | "FAIL" (actually started) |
| Postgres driver | @neondatabase | N/A | @neondatabase | postgres (standard) |
| Root failure | Config location | Divergence | Driver compat | Gate detection |
| Progress trend | ← worse | — | → better | → best yet |

**Overall trend: Improving.** Each run gets closer to shipping:
- 18:30: Wrong config location
- Run 2: Wrong database driver
- Run 3: Correct driver, server starts, gate detection is the only remaining blocker

## Recommendations for Pipeline Improvement

1. **Fix server startup detection.** The quality gate's server startup check needs to handle the case where the server reports listening but the gate can't reach it. Check Docker networking (server container may not be on the same network as the gate), health check URL resolution, and timing.

2. **Stabilize divergence rating.** Either:
   - Add temperature=0 to the validator LLM call
   - Use a deterministic heuristic (LOC ratio, file count, etc.) instead of LLM judgment
   - Retry on HIGH with a different seed before rejecting

3. **Fix category breakdown tracking.** The `category-breakdown.env` file consistently disagrees with `scenario-counts.env`. The category breakdown always shows 0 passed.

4. **Include Server Startup in the structured gate table.** It's currently appended after `## Result:`, which means the summary omits it.

5. **Parse escalation agent output properly.** Extract the markdown content from the Claude API JSON response instead of dumping raw JSON.

6. **Inform the architect about available services.** Pass the `[services]` section from config.toml to the architect so it knows a standard postgres container is available, not Neon.

