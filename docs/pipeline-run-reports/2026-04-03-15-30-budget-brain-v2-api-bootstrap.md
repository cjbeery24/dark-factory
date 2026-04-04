# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date:** 2026-04-03 15:30 UTC
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Dark Factory Version:** HEAD (main), commit d4da44e
**Run ID:** 4aca22dab53b446b

## Pre-Run State

- Previous run ended with `status=interrupted`, `last_decision=NO-SHIP`, `iteration=3`, `pass_rate=0`
- Modified tracked files in repo from prior run: `apps/api/package.json`, `apps/api/src/index.ts`, `eslint.config.js`, `pnpm-lock.yaml`, `tsconfig.base.json`
- Untracked files from prior run: architecture artifacts, reviews, test files, server.ts, vitest config

## Pre-Run Actions

- [x] Updated agents and templates in budget-brain-v2
- [x] Rebuilt Docker images (`dark-factory build node`) — all images cached, built in ~16s
- [x] Reset repository state (git checkout, git clean, removed .dark-factory runtime artifacts)

---

## Run 1 — Killed early due to infrastructure bug

### Pipeline Execution

Pipeline started at 15:31:14Z and was manually killed at ~15:48Z during iteration 2.

**Reason for early termination:** The HTTP scenario runner fails to execute due to a Docker entrypoint conflict, making all scenario execution impossible. Every iteration would produce `pass_rate=0` with `scenario_total=0`, wasting API credits on futile retry loops.

### Agent Timings

| Agent | Wall Seconds | Notes |
|-------|-------------|-------|
| Spec Validator | 95.3s | Completed successfully |
| Architect | 144.1s | Completed successfully |
| Scenario Architect | 189.9s | Generated 5 scenario files |
| Implementer (iter 1) | 253.4s | Code implementation |
| Implementer (iter 1, self-test) | 134.7s | Self-test/smoke prep |
| Reviewer (iter 1) | 105.3s | Scored 70/100 |
| Oracle (iter 1) | 53.4s | NO-SHIP decision |
| **Total iter 1** | **~976s (~16min)** | |

### State File (at kill)

```
spec=2026-03-28-api-bootstrap
run_id=4aca22dab53b446b
last_decision=NO-SHIP
status=interrupted
iteration=2
completed_step=2
```

### Events Summary

```jsonl
{"ts":"2026-04-03T15:45:05Z","event":"SMOKE_COMPLETED","data":{"success":true,"attempt":1,"server_error":""}}
{"ts":"2026-04-03T15:45:10Z","event":"GATE_COMPLETED","data":{"exit_code":1,"pass_rate":0,"satisfaction":0,"server_error":"","agent_wall_seconds":388.09,"scenario_total":0,"scenario_crashed":0}}
{"ts":"2026-04-03T15:45:10Z","event":"ITER_COST","data":{"turns":400,"estimated_usd":84.0}}
```

Key observations from events:
- Smoke test passed — the server started and responded to health checks
- Gate failed with `exit_code=1`, `pass_rate=0`, **`scenario_total=0`** — no scenarios executed at all
- Estimated cost for iteration 1: $84.00

### Quality Gate Report

```markdown
| Gate | Status |
|------|--------|
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Self-Tests | SKIP |
| Mutation Testing | SKIP |

Result: PASS
```

Build/lint/typecheck all passed. The quality gate itself reported PASS but the scenario execution (separate from the gate summary) failed completely.

### HTTP Scenario Runner Log

```
error: unknown option '--base-url'
```

**This is the single line of output.** The scenario runner never started.

### Root Cause Analysis

**The `dark-factory-python` Docker image has:**
```
ENTRYPOINT ["claude", "-p", "--dangerously-skip-permissions"]
```

**The quality gate runs:**
```python
cmd = ["python3", "/opt/dark-factory/scripts/lib/http-scenario-runner.py",
       "--base-url", base_url, ...]
```

**What Docker actually executes:**
```
claude -p --dangerously-skip-permissions python3 /opt/dark-factory/scripts/lib/http-scenario-runner.py --base-url http://...
```

Claude Code's CLI parser sees `--base-url` as an unknown option and errors out. The `python3` command is never run directly — it becomes an argument to `claude`.

**Fix needed:** The `_run_format_a_runner()` and `_run_format_b_runner()` functions in `darkfactory/pipeline/quality_gate.py` need to either:
1. Override the entrypoint with `--entrypoint python3` in docker_args, or
2. Use a different image that doesn't have the claude entrypoint, or
3. The `dark-factory-python` image should not have a claude entrypoint (it's meant for running Python scripts, not claude agent sessions)

### Reviewer Assessment (iter 1)

The reviewer gave 70/100:
- Code Quality: 20/20 — excellent modular structure
- Security: 15/15 — proper env validation, redaction, CORS, rate limiting
- Spec Compliance: 24/30 — minor deviations in rate limit error format, uptime calc
- Test Results: 10/25 — 0% coverage (but this is because scenarios couldn't run, not necessarily the implementer's fault)
- Robustness: 8/10

### Oracle Feedback (iter 1)

NO-SHIP. Cited "complete absence of test coverage" as critical blocking issue. The oracle and reviewer both attributed the 0% pass rate to missing tests, but the actual cause was infrastructure — the scenario runner binary never ran.

---

## Issues Found

### CRITICAL: HTTP Scenario Runner Entrypoint Conflict

**Severity:** Pipeline-breaking — affects ALL specs, not just this one
**Component:** `darkfactory/pipeline/quality_gate.py` lines 1130-1141 + Docker image `dark-factory-python`
**Impact:** Zero scenarios can ever execute. The pipeline will always NO-SHIP and iterate 3 times before escalating, wasting ~$252 per spec in API credits.
**Reproducibility:** 100% — affects every pipeline run that uses Format A (HTTP) scenarios.

### MODERATE: Misleading Feedback Loop

The reviewer and oracle both attributed the 0% scenario pass rate to "missing tests" and asked the implementer to write tests. But the implementer can't fix this — it's an infrastructure bug. This creates a confusing feedback loop where the implementer is told to fix something outside its control.

### MINOR: Self-Tests SKIP

Self-tests were skipped. This may be expected if no vitest/jest config existed before the implementer ran, but it means the only validation of the implementation is build+lint+typecheck.

---

## Metrics

| Metric | Value |
|--------|-------|
| Total wall time (before kill) | ~17 minutes |
| Iterations completed | 1 (killed during 2) |
| Scenarios generated | 5 files |
| Scenarios executed | 0 |
| Pass rate | 0% |
| Estimated cost (iter 1) | $84.00 |
| Smoke test | Passed |
| Build/Lint/TypeCheck | All passed |
| Reviewer score | 70/100 |
| Final decision | NO-SHIP (infrastructure failure, not code quality) |
