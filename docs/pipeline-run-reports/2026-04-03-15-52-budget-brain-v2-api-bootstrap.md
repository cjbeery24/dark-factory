# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-03 15:52
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Run ID:** 9061536af87f46a8
**Model:** claude-sonnet-4-20250514
**Outcome:** KILLED during iteration 3 (would have exhausted all 3 iterations with 0% pass rate)

---

## Pre-Run State

- Repository had leftover changes from previous pipeline runs (modified: apps/api/package.json, apps/api/src/index.ts, eslint.config.js, pnpm-lock.yaml; untracked: .dark-factory/architecture/, .dark-factory/reviews/, apps/api/src/common/, apps/api/src/server.ts)
- Existing state file: 2026-03-28-api-bootstrap.state
- Reset performed: `git checkout -- .`, `git clean -fd`, removed .dark-factory runtime artifacts
- Docker images rebuilt (all cached, no changes)
- Agents and templates synced via rsync

---

## Run 1 — Pipeline Execution

### Timeline (all times UTC)

| Time | Event | Duration |
|------|-------|----------|
| 15:53:40 | SPEC_START | — |
| 15:53:40 | spec-validator start | — |
| 15:55:05 | spec-validator complete | 85.6s |
| 15:55:05 | architect start | — |
| 15:57:29 | architect complete | 143.2s |
| 15:57:29 | scenario-architect start | — |
| 16:00:15 | scenario-architect complete | 165.9s |
| 16:00:17 | **Iteration 1**: implementer start (attempt 1) | — |
| 16:02:53 | implementer complete (attempt 1) | 156.0s |
| 16:02:54 | implementer start (attempt 1, retry?) | — |
| 16:06:21 | implementer complete | 206.9s |
| 16:07:44 | implementer start (attempt 1, retry?) | — |
| 16:22:44 | **implementer TIMEOUT** (exit 124) | 900.0s |
| 16:22:44 | implementer start (attempt 2) | — |
| 16:24:42 | implementer complete (attempt 2) | 118.2s |
| 16:24:45 | SMOKE_COMPLETED: success=true | — |
| 16:25:01 | GATE_COMPLETED: exit_code=1, **pass_rate=0, scenario_total=0** | — |
| 16:25:01 | ITER_COST: $126.00 | — |
| 16:25:01 | reviewer start | — |
| 16:27:31 | reviewer complete | 150.1s |
| 16:27:34 | oracle start (attempt 1) | — |
| 16:28:29 | oracle complete (attempt 1) | 54.9s |
| 16:28:30 | oracle start (attempt 1, retry?) | — |
| 16:29:35 | oracle complete | 64.7s |
| 16:29:35 | **Decision: NO-SHIP** | — |
| 16:29:37 | **Iteration 2**: implementer start | — |
| 16:34:40 | implementer complete | 303.9s |
| 16:35:37 | SMOKE_COMPLETED: success=true | — |
| 16:35:52 | GATE_COMPLETED: exit_code=1, **pass_rate=0, scenario_total=0** | — |
| 16:35:52 | ITER_COST: $42.00 | — |
| 16:35:52 | TRAJECTORY_CLASSIFIED: **PLATEAU** | — |
| 16:35:52 | reviewer start (attempt 1) | — |
| 16:39:16 | **reviewer FAILED** (exit 1) | 203.2s |
| 16:39:16 | reviewer start (attempt 2) | — |
| 16:42:51 | reviewer complete (attempt 2) | 215.9s |
| 16:42:52 | oracle start | — |
| 16:43:57 | oracle complete | 65.5s |
| 16:43:58 | oracle start (retry?) | — |
| 16:44:58 | oracle complete | 60.3s |
| 16:44:58 | **Decision: NO-SHIP** | — |
| 16:44:58 | **Iteration 3**: implementer start | — |
| ~16:52 | **KILLED by operator** (running ~7 min into iter 3 implementer) | — |

**Total wall time before kill:** ~58 minutes
**Estimated cost (iter 1 + iter 2):** $168.00 (iter 3 not completed)

### State File (final snapshot before kill)

```
spec=2026-03-28-api-bootstrap
run_id=9061536af87f46a8
status=running
completed_step=2
last_decision=NO-SHIP
iteration=3
phase=
smoke_result=
pass_rate=
gate_exit_code=
server_error=
```

### Quality Gate Results

**Iteration 1:**
```
| Gate       | Status | Details       |
|------------|--------|---------------|
| Build      | PASS   | Exit code 0   |
| Lint       | PASS   | Exit code 0   |
| Type Check | PASS   | Exit code 0   |
| Self-Tests | SKIP   | No command    |
| Mutation   | SKIP   | Not configured|
| Blind Scenarios (HTTP) | FAIL |     |
```
- Smoke test: PASS (server started on attempt 2)
- Scenarios executed: **0 out of 4 files** — parser could not parse any scenarios
- Gate exit code: 1

**Iteration 2:** Identical results — same 0 scenarios parsed.

### Container Logs

**Server startup log** (`df-9061536af87f46a8-server.log`):
```
error TS5033: Could not write file '/project/packages/shared/dist/index.d.ts.map': EROFS: read-only file system
error TS5033: Could not write file '/project/packages/shared/dist/index.js': EROFS: read-only file system
error TS5033: Could not write file '/project/packages/shared/dist/index.js.map': EROFS: read-only file system
error TS5033: Could not write file '/project/packages/shared/tsconfig.tsbuildinfo': EROFS: read-only file system
ELIFECYCLE  Command failed with exit code 2.
```
This was the first server startup attempt. The second attempt succeeded (smoke_result=passed), suggesting the server was started differently or the build artifacts were already present from the implementer's `:rw` container.

**HTTP Scenario Runner log** (`http-scenario-runner.log`):
```
ERROR: No scenarios could be parsed from any *.scenarios.md file
```

### Scenario Validation

```
| Check      | Status | Detail                              |
|------------|--------|-------------------------------------|
| file_count | PASS   | 4 markdown scenario file(s) found   |
| non_empty  | PASS   | 0 empty scenario file(s)            |
| has_sections| PASS  | 0 file(s) missing ## section headers |
```
Validation passed but the runner couldn't parse them — **validation checks are insufficient**.

### Review Score (Iteration 1)

Reviewer scored the implementation **47/60 (78% - PASS)**:
- Spec Compliance: 23/30 (rate limiting exemption missing)
- Robustness: 4/10 (no test coverage)
- Test Results: 10/25 (0% pass rate)
- Code Quality: 20/20
- Security: 15/15

---

## Critical Issues Found

### ISSUE 1: Scenario Format Mismatch (BLOCKING)

**Severity:** Pipeline-breaking
**Root cause:** The scenario architect generates scenarios in a freeform markdown format that the `http-scenario-runner.py` parser cannot parse.

**What the scenario architect generated:**
```markdown
## Health Endpoint Basic Contract

**Intent:** GET /health returns 200 with status "ok"...

\`\`\`http
Request:
GET /health
Host: localhost:3001
\`\`\`

\`\`\`http
Expect:
Status: 200
Content-Type: application/json
Body: { "data": { "status": "ok" } }
\`\`\`
```

**What the parser expects** (`http-scenario-runner.py:266-292`):
- Blocks separated by `---` lines
- Each block starts with `# Scenario: <name>` header
- Optional `# Requirement: <ref>` header
- `## Request` and `## Expect` section headers (not inside code blocks)

The parser (`parse_scenarios_file`) splits on `^---$`, then requires `^# Scenario:` inside each block. None of the generated scenario blocks match this pattern.

**Impact:** Every iteration gets pass_rate=0 with 0 scenarios, making the pipeline loop futilely through all 3 iterations. This is the **single root cause** of the pipeline failure.

**Fix needed:** Either:
1. Update the scenario architect agent prompt/reference to output the correct `# Scenario:` / `---` delimited format
2. Or update the parser to accept the freeform format the SA is currently generating

### ISSUE 2: Scenario Validation Too Shallow

**Severity:** Medium
**Description:** The scenario validation step (`scenario-validation.md`) only checks file_count, non_empty, and has_sections. It does NOT validate that scenarios are parseable by the HTTP scenario runner. This allows unparseable scenarios to pass validation and reach the gate, where they silently produce 0 results.

**Fix needed:** Add a validation step that actually invokes the parser in dry-run mode and reports unparseable blocks.

### ISSUE 3: Container Leak on Agent Timeout

**Severity:** Medium
**Description:** When the implementer timed out (exit 124 after 900s), the Docker container `loving_bhaskara` was not cleaned up. It remained running for 44+ minutes until manually killed. Meanwhile, new agent containers were started alongside it.

**Evidence:** `loving_bhaskara` container remained `Up 44 minutes` while newer containers came and went.

### ISSUE 4: Duplicate Agent Invocations (Unexplained)

**Severity:** Low-Medium
**Description:** Several agents appear to run twice within the same step:
- **Implementer in iter 1:** 3 AGENT_START events before the timeout, all showing `attempt:1`. The first two completed successfully (156s, 207s) before a third started and timed out (900s).
- **Oracle in both iterations:** Two AGENT_START/COMPLETED pairs per iteration, both showing `attempt:1`.

This may be by design (e.g., the first implementer run is a quick pass, second is the quality gate's inner loop) but the events log doesn't disambiguate these phases. The `phase` field in the events data is empty for all implementer/oracle invocations.

**Note:** The `phase` field in the state file was also empty throughout. The recently-added `phase` field on agent events (commit `017f2b5`) may not be propagating to the implementer/oracle roles.

### ISSUE 5: EROFS Error in Server Container

**Severity:** Low (workaround exists)
**Description:** First server startup attempt failed because TypeScript build tried to write to `packages/shared/dist/` which was on a read-only filesystem. The second attempt succeeded, suggesting the pipeline retried with different mount configuration or the build artifacts already existed.

The monorepo's `tsc --build` in `apps/api` follows project references to `packages/shared`, which triggers a rebuild and write to that package's `dist/` directory. If the server container mounts the project read-only, this will always fail on first build.

---

## Agent Performance Summary

| Agent | Iter 1 | Iter 2 | Notes |
|-------|--------|--------|-------|
| spec-validator | 85.6s | — | One-time, clean |
| architect | 143.2s | — | One-time, clean |
| scenario-architect | 165.9s | — | Generated 4 files in wrong format |
| implementer | 156s + 207s + 900s(timeout) + 118s = ~1381s total | 303.9s | Iter 1 had multiple sub-invocations |
| reviewer | 150.1s | 203.2s(fail) + 215.9s | Failed once in iter 2 |
| oracle | 54.9s + 64.7s | 65.5s + 60.3s | Runs twice per iteration |

---

## Metrics

| Metric | Value |
|--------|-------|
| Total wall time | ~58 min (killed during iter 3) |
| Iterations completed | 2 of 3 |
| Estimated cost | $168 ($126 iter1 + $42 iter2) |
| Scenarios generated | 4 files |
| Scenarios executed | 0 (format mismatch) |
| Pass rate | 0% (all iterations) |
| Static gates passed | Build, Lint, Type Check |
| Smoke test | Passed (both iterations) |
| Trajectory classification | PLATEAU |
| Container leaks observed | 1 (timed-out implementer) |
| Agent failures | 1 timeout (implementer iter1), 1 exit-1 (reviewer iter2) |

---

## Regression vs Previous Runs

Compared to the 6 previous pipeline run reports in `docs/pipeline-run-reports/`:
- The scenario format mismatch is a **recurring issue** — this same spec has been run multiple times with the same 0-scenario outcome
- The EROFS server build error was also observed in prior runs
- Container leak on timeout appears to be a persistent infrastructure issue

---

## Recommendations (not actioned — observation only)

1. **P0:** Fix scenario format — either update the scenario architect prompt/reference doc to use `# Scenario:` / `---` format, or update the parser. This is the single largest source of wasted compute across all pipeline runs.
2. **P1:** Add parsability check to scenario validation — fail early if the runner can't parse any scenarios, before spending time/money on implementation.
3. **P2:** Fix container cleanup on agent timeout — the pipeline should `docker kill` timed-out containers.
4. **P2:** Investigate duplicate agent invocations — clarify whether this is intentional (inner loops) and if so, add phase disambiguation to events.
5. **P3:** Investigate EROFS on first server build — may need `:rw` mount for the server container's build step, or pre-build shared packages before starting the server.
