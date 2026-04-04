# Pipeline Run Report: budget-brain-v2 / api-bootstrap

**Date:** 2026-04-03 00:57 UTC
**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Run ID:** c0553458d3364ff0
**Dark Factory version:** latest (main branch)
**Model:** claude-sonnet-4-20250514 (all agents)

## Pre-Run State

- Previous run ended: `status=interrupted`, `iteration=3`, `last_decision=NO-SHIP`, `smoke_result=failed`, `server_error=true`
- Tracked files modified from previous run: `apps/api/package.json`, `apps/api/src/index.ts`, `apps/api/tsconfig.tsbuildinfo`, `pnpm-lock.yaml`
- Untracked artifacts: `.dark-factory/architecture/`, `.dark-factory/reviews/`, `apps/api/src/common/`, `apps/api/src/server.ts`

## Prep Steps

- [x] Updated agents/templates in budget-brain-v2 (manual copy, `dark-factory sync` not available)
- [x] Rebuilt Docker images (`dark-factory build node` — all cached, no rebuild needed)
- [x] Reset repo state (`git checkout -- .`, `git clean -fd`, removed .dark-factory runtime artifacts)
- [x] Pipeline started at 00:58:41 UTC

---

## Run 1 (only run)

### Timeline & Events

Total wall time: ~99 minutes (00:58:41 — 02:37:53 UTC)

| Time (UTC) | Event | Duration | Notes |
|---|---|---|---|
| 00:58:42 | spec-validator START | | |
| 01:00:39 | spec-validator DONE | 117s | 3 ambiguity warnings, passed |
| 01:00:39 | architect START | | |
| 01:11:35 | architect DONE | 144s | |
| 01:11:35 | scenario-architect START | | |
| 01:24:03 | scenario-architect DONE | 320s | 4 scenario files generated |
| 01:24:05 | **iter1** implementer START (attempt 1) | | |
| 01:26:44 | iter1 implementer DONE (attempt 1) | 159s | workspace-check retry |
| 01:26:46 | iter1 implementer START (attempt 2) | | |
| 01:29:42 | iter1 implementer DONE (attempt 2) | 176s | workspace-check retry |
| 01:31:04 | iter1 implementer START (attempt 3) | | |
| 01:32:30 | iter1 implementer DONE (attempt 3) | 86s | passed workspace-check |
| 01:32:33 | iter1 SMOKE_COMPLETED | | success=true, attempt=2 |
| 01:32:48 | iter1 GATE_COMPLETED | | **exit_code=1, pass_rate=0, scenario_total=0** |
| 01:32:48 | iter1 ITER_COST | | turns=600, est_usd=$126 |
| 01:32:48 | iter1 reviewer START | | |
| 01:34:33 | iter1 reviewer DONE | 105s | score 81/100 |
| 01:34:34 | iter1 oracle START | | |
| 01:35:36 | iter1 oracle DONE | 63s | **NO-SHIP** |
| 01:35:37 | **iter2** implementer START (attempt 1) | | |
| 02:03:03 | iter2 implementer DONE (attempt 1) | 722s | maxed 200 turns |
| 02:03:09 | iter2 implementer START (attempt 2) | | workspace-check retry |
| 02:30:45 | iter2 implementer DONE (attempt 2) | 456s | |
| 02:30:52 | iter2 SMOKE_COMPLETED | | success=true, attempt=1 |
| 02:31:08 | iter2 GATE_COMPLETED | | **exit_code=1, pass_rate=0, scenario_total=0** |
| 02:31:08 | ANOMALY_DETECTED | | turns=1000 > max=800 |
| 02:31:08 | iter2 ITER_COST | | turns=400, est_usd=$84 |
| 02:31:08 | TRAJECTORY_CLASSIFIED | | **PLATEAU** |
| 02:31:08 | iter2 reviewer START | | |
| 02:35:22 | iter2 reviewer DONE | 254s | score 80/100 |
| 02:35:22 | iter2 oracle START | | |
| 02:36:42 | iter2 oracle DONE | 81s | **NO-SHIP** |
| 02:36:43 | escalation START | | |
| 02:37:53 | escalation DONE | 71s | |
| 02:37:53 | **SPEC_ESCALATED** | | 2 iterations completed |

### Final State File

```
spec=2026-03-28-api-bootstrap
run_id=c0553458d3364ff0
server_error=
smoke_result=passed
pass_rate=0
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=interrupted
iteration=3
completed_step=2
```

### Quality Gate Reports

**Iter 1 — Pre-Gate (workspace check):**
```
PRE_GATE_PASSED=true
```

**Iter 1 — Quality Gate:**
```
| Gate       | Status | Details      |
|------------|--------|-------------|
| Build      | PASS   | Exit code 0 |
| Lint       | PASS   | Exit code 0 |
| Type Check | PASS   | Exit code 0 |
| Self-Tests | SKIP   | No command configured |
| Mutation   | SKIP   | Not configured |
| Blind Scenarios (HTTP) | FAIL | scenario_total=0, pass_rate=0 |
```

**Iter 2 — identical results.**

### Server Log (from container startup)

```
Scope: all 4 workspace projects
Packages: +221
Done in 470ms using pnpm v9.15.9
Failed to start server: FastifyError: fastify-plugin: @fastify/cors - expected '4.x' fastify version, '5.8.4' is installed
  code: 'FST_ERR_PLUGIN_VERSION_MISMATCH'
```

This was from the first implementer attempt. The CORS version mismatch was resolved by the 3rd implementer attempt (iter 1), after which the smoke test passed.

### Scenario Files Generated

4 files in `.dark-factory/scenarios/2026-03-28-api-bootstrap/`:
- `01-health-endpoint.scenarios.md`
- `02-error-handling.scenarios.md`
- `03-cors-security.scenarios.md`
- `04-additional-scenarios.scenarios.md`

All validated (file_count PASS, non_empty PASS, has_sections PASS).

### Cost Summary

| Iteration | Agent Turns | Estimated USD |
|-----------|------------|---------------|
| 1 | 600 | $126.00 |
| 2 | 400 | $84.00 |
| **Total** | **1000** | **~$210.00** |

Note: This does not include pre-iteration agents (spec-validator, architect, scenario-architect) or post-iteration agents (escalation).

---

## Critical Findings

### FINDING 1: Zero scenario execution across both iterations (P0)

**The most important finding.** Despite 4 well-structured scenario files existing and passing validation, the quality gate reported `scenario_total=0` in both iterations. The scenarios were never executed against the running server.

**Evidence:**
- Iter 1: `{"exit_code":1,"pass_rate":0,"satisfaction":0,"server_error":"","scenario_total":0,"scenario_crashed":0}`
- Iter 2: `{"exit_code":1,"pass_rate":0,"satisfaction":0,"server_error":"","scenario_total":0,"scenario_crashed":0}`
- Smoke test passed in both iterations (server was reachable at localhost:3001)
- Scenario files were present and validated

**Impact:** This is a systemic pipeline failure. The quality gate is not executing blind HTTP scenarios even when the server is healthy and scenario files exist. This makes the entire feedback loop useless — the implementer keeps getting told "0% pass rate" but the scenarios were never run, so there's nothing actionable to fix.

**Root cause hypothesis:** The quality gate agent or quality-gate.sh script is not finding or executing the scenario files. Possible issues:
1. The quality gate script doesn't know how to discover/run `.scenarios.md` files
2. The scenario runner is not being invoked inside the gate container
3. The gate reports pass for build/lint/typecheck but doesn't have logic to actually execute HTTP scenario files
4. The scenarios use markdown-described HTTP requests that require an agent to interpret and execute them — there may be no automated execution path

### FINDING 2: Implementer workspace-check retry pattern is expensive (P1)

In both iterations, the implementer had to be retried multiple times before passing the workspace-check:
- Iter 1: 3 attempts (159s + 176s + 86s = 421s total)
- Iter 2: 2 attempts (722s + 456s = 1178s total)

The workspace-check is necessary but the retries consume significant time and turns. The implementer's first attempt often produces valid code that fails workspace-check for unknown reasons — possibly because node_modules are on tmpfs and need `pnpm install` but the implementer doesn't always do this.

### FINDING 3: Feedback loop is circular and unproductive (P1)

The reviewer and oracle correctly identify that `pass_rate=0%` blocks shipping, but the feedback they give is misleading:
- "Implement comprehensive test suite covering all acceptance criteria"
- "Fix test runner infrastructure and execute full test suite"

These instructions tell the implementer to write unit/integration tests. But the actual problem is that the **quality gate doesn't execute the blind HTTP scenarios** — the implementer can't fix this because it's a pipeline infrastructure issue, not an implementation issue. The feedback creates a circular loop where the implementer keeps trying to add test infrastructure that won't affect the blind scenario execution.

### FINDING 4: Spec ambiguity warnings not blocking (low priority)

3 warnings during spec pre-check:
- Line 223: ambiguous term "appropriate" in EC-9 (CORS headers)
- Line 235: ambiguous term "etc." in error handling
- Line 425: ambiguous term "etc." in FV-5

These are minor and didn't cause issues.

### FINDING 5: Implementation quality is consistently high (positive)

Both iterations scored 80-81/100 from the reviewer:
- Code Quality: 20/20
- Security: 15/15
- Spec Compliance: 26-27/30
- Robustness: 9/10
- Test Results: 10/25 (only category failing, and it's due to the scenario execution pipeline issue)

The implementer correctly produced: Fastify 5.x server, zod env validation, pino structured logging, AppError class, health check registry, CORS, rate limiting.

### FINDING 6: Escalation report is overly verbose and misidentifies root cause

The escalation agent wrote a 243-line report that frames the failure as "testing infrastructure failure" and recommends "Emergency Architecture Review" and "Incident Response Team Assembly." The actual root cause is simpler: the quality gate doesn't execute HTTP scenarios. The escalation agent doesn't have access to the pipeline's own code, so it can only speculate based on the symptoms.

---

## Metrics Comparison vs Previous Run

| Metric | Previous Run | This Run | Change |
|--------|------------|----------|--------|
| Iterations completed | 3 | 2 | Improved (escalated earlier) |
| Smoke test | Failed all | Passed iter 1 (attempt 2), iter 2 (attempt 1) | Improved |
| Server error | true | false (after iter 1 attempt 3) | Improved |
| Pass rate | 0 | 0 | No change |
| Scenario total | 0 | 0 | No change |
| PLATEAU detection | N/A | Yes (iter 2) | New feature working |
| Total est. cost | unknown | ~$210+ | |

---

## Recommendations for Pipeline Improvement

1. **Investigate and fix blind HTTP scenario execution** — This is the #1 priority. The quality gate needs to actually run the HTTP scenarios against the server. Check `quality-gate.sh` and the gate agent's scenario runner logic.

2. **Reduce implementer workspace-check retries** — Investigate why the first implementer attempt frequently fails workspace-check. Consider pre-installing dependencies or providing clearer instructions to the implementer about running `pnpm install`.

3. **Improve feedback accuracy when scenarios aren't executed** — When `scenario_total=0`, the feedback should say "No scenarios were executed (pipeline infrastructure issue)" rather than "0% pass rate — implement tests."

4. **Add pipeline self-diagnostics** — When `scenario_total=0` despite scenario files existing, the pipeline should log a warning explaining *why* no scenarios ran.

5. **Cap implementer time** — Iter 2 implementer used 1178s across 2 attempts with 200 turns each. Consider lower turn limits for implementer retries.
