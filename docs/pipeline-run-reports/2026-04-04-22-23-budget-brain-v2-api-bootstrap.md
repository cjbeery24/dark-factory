# Pipeline Run Report: budget-brain-v2 / api-bootstrap

**Date:** 2026-04-04 22:23 UTC
**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Run ID:** 0e2919f83b674215
**Pipeline version:** HEAD of dark-factory main (b55d955)

---

## Pre-Run Setup

- Reset budget-brain-v2 repo: `git checkout -- .`, `git clean -fd` (preserved specs/config/plans)
- Cleared `.dark-factory/` runtime artifacts (scenarios, reports, reviews, audit, state, events, metrics)
- Cleared Python bytecode caches
- Updated agents/templates in target repo
- Reinstalled dark-factory (editable)
- Rebuilt Docker images (`dark-factory build node`) — all layers cached
- Cleaned orphaned Docker containers/networks

---

## Run 1

### Outcome: ABORTED — Scenario infrastructure failure after server setup error

### Timeline (all times UTC)

| Time | Event | Wall (s) | Notes |
|------|-------|----------|-------|
| 22:24:03 | SPEC_START | — | |
| 22:24:03–22:25:34 | spec-validator | 91s | PASS, score 92/100, divergence LOW |
| 22:25:35–22:34:56 | architect | 230s | Produced manifest with `npm install` setup cmd |
| 22:34:58–22:45:42 | implementer (1/3) | 230s | First agent session |
| 22:45:44–22:52:16 | implementer (2/3) | 222s | Second agent session |
| 22:52:22–22:53:46 | implementer (3/3) | 84s | Third agent session |
| 22:53:48 | SMOKE_COMPLETED | — | **FAILED** (attempt 2 of 2) |
| 22:53:53 | GATE_COMPLETED | — | exit_code=1, pass_rate=0, server_error=true |
| 22:53:53 | Pipeline aborted | — | Scenario infrastructure failure |

**Real elapsed:** ~30 minutes
**Total agent wall time:** ~857s (14.3 min)

### Scenario Generation: Skipped (reused from prior run)

The pipeline skipped scenario generation because `.dark-factory/scenarios/2026-03-28-api-bootstrap/` already contained 4 scenario files (20 scenarios total) from a previous run (823267f89a9343b4). Despite running `rm -rf .dark-factory/scenarios/*` during prep, the pipeline's `has_scenario_files()` check found existing scenarios at step time.

**Possible explanation:** The scenarios directory was re-populated between cleanup and pipeline start. However, no scenario-architect AGENT_START/COMPLETED events exist for run 0e2919f83b674215, confirming the skip. The scenarios appear to have survived cleanup — possibly `rm -rf .dark-factory/scenarios/*` only removed directory entries but the dir was recreated by the architect step, or the glob didn't match correctly with subdirectories.

### Root Cause: Architect declared `npm install` in a pnpm workspace

The architecture manifest (`2026-03-28-api-bootstrap-manifest.yaml`) declared:

```yaml
runtime:
  server_setup_cmd: "cd apps/api && npm install"
  test_server_cmd: "cd apps/api && npm run dev"
```

Budget-brain-v2 is a **pnpm workspace** project. The `workspace:*` protocol in `package.json` dependencies is pnpm-specific and not understood by npm:

```
SMOKE_COMPLETED: success=False
  server_setup_cmd failed (exit 1):
  npm error code EUNSUPPORTEDPROTOCOL
  npm error Unsupported URL Type "workspace:": workspace:*
```

The correct command should be `pnpm install` from the project root (not `cd apps/api && npm install`).

### Implementer: 3 Agent Sessions

The implementer ran 3 separate agent sessions totaling 536s of wall time. This is the pre-gate pattern where the quality gate script runs build/lint/typecheck within the implementer container as a pre-check:

1. Session 1 (230s): Main implementation
2. Session 2 (222s): Pre-gate fix attempt 1 (failed)
3. Session 3 (84s): Pre-gate fix attempt 2 (passed)

Pre-gate eventually passed (build, lint, typecheck all PASS), meaning the TypeScript code itself compiles and lints fine.

### Quality Gate Results

```
| Gate        | Status |
|-------------|--------|
| Build       | PASS   |
| Lint        | PASS   |
| Type Check  | PASS   |
| Self-Tests  | SKIP   |
| Mut. Test   | SKIP   |
| Server Setup| FAIL   |
```

Server setup failed → smoke test failed → 0 scenarios executed → pipeline detected "4 scenario files exist but 0 scenarios executed" and aborted.

### Review Pre-Pass (from prior run data, stale)

The `review-prepass.env` and `review-prepass.md` files contain data from the prior run's iteration 2 (where server actually started and 80% scenarios passed):

```
TEST_ANALYSIS_SCORE=15
CODE_QUALITY_SCORE=20
SECURITY_SCORE=15
DETERMINISTIC_TOTAL=50
FAILURE_PATTERN=isolated
```

This data is stale for the current run but indicates the underlying implementation code quality is decent when the server can actually start.

### Scenario Pass Rates (from prior run, for reference)

From run 823267f89a9343b4 (same spec, same day), when the server did start:

| Category | Passed | Total |
|----------|--------|-------|
| 01 - Health Endpoint | 4 | 4 |
| 02 - Error Handling | 3 | 4 |
| 03 - CORS & Cross-Cutting | 4 | 5 |
| 04 - Edge Cases | 5 | 7 |
| **Total** | **16** | **20** (80%) |

Failed scenarios from that run:
- `malformed JSON request triggers error handling`: expected 400, got 500
- `CORS preflight from allowed origin`: expected 204, got 400
- `health endpoint with query parameters`: expected 200, got 404
- `health endpoint with trailing slash`: expected 200, got 404

### Diagnostic Excerpts

**State file (final):**
```
spec=2026-03-28-api-bootstrap
run_id=0e2919f83b674215
smoke_result=failed
phase=review
pass_rate=0
gate_exit_code=1
server_error=true
status=interrupted
iteration=1
completed_step=2
```

**Pipeline output (abort reason):**
```
WARNING: server smoke exhausted after 2 attempts; last error:
server_setup_cmd failed (exit 1):
npm error code EUNSUPPORTEDPROTOCOL
npm error Unsupported URL Type "workspace:": workspace:*

WARNING: no scenario count fragments found for spec 2026-03-28-api-bootstrap
ERROR: scenario infrastructure failure: 4 scenario file(s) exist but 0 scenarios were executed
```

**Events JSONL (smoke failure):**
```json
{"ts":"2026-04-04T22:53:48Z","event":"SMOKE_COMPLETED","data":{"success":false,"attempt":2,
  "server_error":"server_setup_cmd failed (exit 1):\nnpm error code EUNSUPPORTEDPROTOCOL\n
  npm error Unsupported URL Type \"workspace:\": workspace:*\n"}}
```

---

## Observations & Pipeline Improvement Notes

### 1. Architect uses npm instead of pnpm (RECURRING)
The architect consistently declares `npm install` for `server_setup_cmd` despite the project using pnpm workspaces. This has been the blocking issue on multiple api-bootstrap runs. The architect agent should detect the package manager from lockfile presence (`pnpm-lock.yaml` → pnpm, `package-lock.json` → npm, `yarn.lock` → yarn) and use the correct one in the manifest's runtime section.

### 2. Scenario reuse across runs
The pipeline's `has_scenario_files()` check causes scenarios from previous runs to be reused silently. This can be good (saves time/cost) or bad (stale scenarios). In this case, scenarios from a prior run were reused without generating new ones, even though the run was supposed to be fresh. The cleanup step should be more aggressive, or the pipeline should have a `--fresh` flag behavior that forces regeneration.

### 3. Implementer ran 3 agent sessions (536s total)
The pre-gate retry mechanism triggered twice before passing. This is expensive at ~$84 estimated per iteration (with padded estimates). The first pre-gate failure likely meant the implementer needed to fix build/lint issues.

### 4. Pipeline correctly aborted on infrastructure failure
The `scenario infrastructure failure` check correctly detected that 4 scenario files existed but 0 were executed (because the server never started), and aborted instead of wasting budget on review/oracle/iteration cycles. This is the correct behavior.

### 5. No scenario architect phase in event log
Despite the pipeline output saying "Generating blind test scenarios... Scenarios generated", no scenario-architect agent events appear in the events log. This confirms the scenarios were reused, but the pipeline output is misleading — it should say "Using existing scenarios" instead of "Scenarios generated".

### 6. Stale report artifacts
The `test-results-iter1.json` and `test-results-iter2.json` files contain results from the prior run, not the current one. The `review-prepass` data is also from the prior run. These should have been cleared during cleanup or the pipeline should overwrite them per-run.
