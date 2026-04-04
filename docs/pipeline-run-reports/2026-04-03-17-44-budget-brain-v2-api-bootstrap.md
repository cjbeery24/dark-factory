# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-03 17:44
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Run ID:** 1b281f07ddf340f2

---

## Pre-Run State

- Previous run existed: `status=interrupted`, `iteration=3`, `last_decision=NO-SHIP`
- Modified tracked files from prior run: `apps/api/package.json`, `apps/api/src/index.ts`, `pnpm-lock.yaml`, `package.json`, tsbuildinfo files
- Untracked files from prior run: `.dark-factory/architecture/`, `.dark-factory/reviews/`, `apps/api/src/__tests__/`, `apps/api/src/common/`, `apps/api/src/server.ts`, `apps/api/vitest.config.ts`
- Actions taken: full repo reset (git checkout + clean, removed .dark-factory runtime artifacts)
- Docker images: rebuilt via `dark-factory build node` (all layers cached)

---

## Run 1

### Pipeline Invocation
- **Command:** `dark-factory run 2026-03-28-api-bootstrap`
- **Start time (UTC):** 2026-04-03 17:45:56
- **End time (UTC):** 2026-04-03 ~18:04:16
- **Total wall time:** ~18 min 20s
- **Outcome:** ABORTED — scenario infrastructure failure

### Phase Progression

| Phase | Agent | Start | Wall (s) | Exit | Notes |
|-------|-------|-------|----------|------|-------|
| Spec Validation | spec-validator | 17:45:56 | 100.2 | 0 | Passed with 3 ambiguity warnings |
| Architecture | architect | 17:47:37 | 199.4 | 0 | Completed successfully |
| Scenario Generation | scenario-architect | 17:50:59 | 171.4 | 0 | Generated 3 files, 23 parseable blocks |
| Implementation (iter 1) | implementer | 17:54:06 | 357.2 | 0 | First pass |
| Implementation (iter 1, attempt 2) | implementer | 18:00:39 | 206.2 | 0 | Self-test pass (pre-gate) |
| Smoke Test | — | 18:04:11 | — | — | **PASSED** |
| Quality Gate | — | 18:04:11 | — | 1 | Build/lint/typecheck PASS, scenarios FAIL |
| **ABORT** | — | 18:04:16 | — | — | Scenario infrastructure failure detected |

### Root Cause: Non-ASCII URL in Scenario File

The Scenario Architect generated a unicode test scenario in `03-edge-cases.scenarios.md:65-74`:

```markdown
# Scenario: undefined route with unicode characters
# Requirement: Cross-Cutting

## Request
GET /api/测试/пользователь

## Expect
- Status: 404
- Body contains field "error"
- Body field "error.code" equals "NOT_FOUND"
```

Python's `http.client.HTTPConnection.putrequest()` calls `request.encode('ascii')` which crashes on the Chinese/Cyrillic characters:

```
UnicodeEncodeError: 'ascii' codec can't encode characters in position 9-10: ordinal not in range(128)
```

### Cascade Effect

The `http-scenario-runner.py` iterates scenarios in `main()` at line 566-567:

```python
for scenario in scenarios:
    ok, detail = run_scenario(scenario, args.base_url, args.timeout)
```

There is **no try/except** around individual scenario execution. When the unicode scenario crashes, the entire runner process exits with code 1. Result: **0 of 23 scenarios executed**, even though the 22 ASCII scenarios would have run fine.

### Two Bugs Identified

1. **http-scenario-runner.py (pipeline bug):** No per-scenario error isolation. One bad scenario kills the entire run. The runner should catch exceptions per-scenario and mark the individual scenario as FAIL/ERROR, then continue executing the rest.

2. **http-scenario-runner.py (pipeline bug):** Non-ASCII URLs need to be percent-encoded before passing to `urllib.request.urlopen()`. The runner should `urllib.parse.quote()` the path component for non-ASCII characters.

3. **Scenario Architect (behavioral):** Generated a valid edge-case test (unicode in URL paths) that the runner infrastructure doesn't support. This is arguably good test design — the implementation *should* handle unicode URLs gracefully. The bug is in the runner, not the scenario.

### Positive Observations

- **Spec validation** correctly flagged 3 ambiguity warnings ("appropriate", "etc." x2) but proceeded — appropriate behavior
- **Scenario validation** correctly reported 23 parseable blocks across 3 files — the scenario format is valid
- **Smoke test passed** — the server starts and responds to health checks
- **Pre-gate checks all passed** — build, lint, type check all green
- **Abort-on-infrastructure-failure** worked correctly: the pipeline detected 3 scenario files exist but 0 executed and aborted instead of wasting budget on futile NO-SHIP iterations
- **Implementer produced clean code**: 5 files changed (586 insertions), all build/lint/typecheck passing

### Diagnostic Data

#### State File (final)
```
spec=2026-03-28-api-bootstrap
run_id=1b281f07ddf340f2
server_error=
smoke_result=passed
phase=review
pass_rate=0
gate_exit_code=1
status=interrupted
iteration=1
completed_step=2
```

#### Events JSONL (complete)
```jsonl
{"ts":"2026-04-03T17:45:56Z","event":"SPEC_START","spec":"2026-03-28-api-bootstrap","run_id":"1b281f07ddf340f2"}
{"ts":"2026-04-03T17:45:56Z","event":"AGENT_START","data":{"role":"spec-validator","attempt":1}}
{"ts":"2026-04-03T17:47:37Z","event":"AGENT_COMPLETED","data":{"role":"spec-validator","wall_seconds":100.25}}
{"ts":"2026-04-03T17:47:37Z","event":"AGENT_START","data":{"role":"architect","attempt":1}}
{"ts":"2026-04-03T17:50:56Z","event":"AGENT_COMPLETED","data":{"role":"architect","wall_seconds":199.38}}
{"ts":"2026-04-03T17:50:59Z","event":"AGENT_START","data":{"role":"scenario-architect","attempt":1,"phase":"generate"}}
{"ts":"2026-04-03T17:53:51Z","event":"AGENT_COMPLETED","data":{"role":"scenario-architect","wall_seconds":171.37,"phase":"generate"}}
{"ts":"2026-04-03T17:54:06Z","event":"AGENT_START","iter":1,"data":{"role":"implementer","attempt":1}}
{"ts":"2026-04-03T18:00:04Z","event":"AGENT_COMPLETED","iter":1,"data":{"role":"implementer","wall_seconds":357.22}}
{"ts":"2026-04-03T18:00:39Z","event":"AGENT_START","iter":1,"data":{"role":"implementer","attempt":1}}
{"ts":"2026-04-03T18:04:05Z","event":"AGENT_COMPLETED","iter":1,"data":{"role":"implementer","wall_seconds":206.23}}
{"ts":"2026-04-03T18:04:11Z","event":"SMOKE_COMPLETED","iter":1,"data":{"success":true}}
{"ts":"2026-04-03T18:04:16Z","event":"GATE_COMPLETED","iter":1,"data":{"exit_code":1,"pass_rate":0,"scenario_total":0,"scenario_crashed":0}}
```

#### Quality Gate Summary
```
| Gate        | Status | Details              |
|-------------|--------|----------------------|
| Build       | PASS   | Exit code 0          |
| Lint        | PASS   | Exit code 0          |
| Type Check  | PASS   | Exit code 0          |
| Self-Tests  | SKIP   | No command configured |
| Mutation    | SKIP   | Not configured       |
| Blind Scenarios (HTTP) | FAIL |             |
```

#### Scenario Validation
```
| Check       | Status | Detail                                          |
|-------------|--------|-------------------------------------------------|
| file_count  | PASS   | 3 markdown scenario file(s) found (min 3)       |
| non_empty   | PASS   | 0 empty scenario file(s)                         |
| has_sections| PASS   | 0 file(s) missing ## section headers             |
| parseable   | PASS   | 23 scenario block(s) parseable across 3 file(s)  |
```

#### Scenario Counts (env)
```
SCENARIO_PASSED=0
SCENARIO_FAILED=0
SCENARIO_TOTAL=0
SCENARIO_RATE=0
```

#### HTTP Scenario Runner Log (full)
```
UnicodeEncodeError: 'ascii' codec can't encode characters in position 9-10: ordinal not in range(128)
```
(Full traceback in `reports/2026-03-28-api-bootstrap/http-scenario-runner.log`)

### Pipeline Warnings (from stdout)
```
WARNING: ambiguous term "appropriate" on line 223
WARNING: ambiguous term "etc." on line 235
WARNING: ambiguous term "etc." on line 425
WARNING: spec pre-check: 3 ambiguity warnings (proceeding)
WARNING: HTTP scenario runner exited 1: [traceback]
WARNING: no scenario count fragments found for spec 2026-03-28-api-bootstrap
ERROR: scenario infrastructure failure: 3 scenario file(s) exist but 0 scenarios were executed
```

---

## Summary & Action Items

### Pipeline Bugs to Fix

1. **[P0] http-scenario-runner.py — no per-scenario error isolation**
   - Impact: One bad scenario kills the entire run (0/23 executed)
   - Fix: Wrap `run_scenario()` call in try/except, mark individual scenario as ERROR, continue
   - Location: `scripts/lib/http-scenario-runner.py:566-567`

2. **[P1] http-scenario-runner.py — no URL percent-encoding for non-ASCII paths**
   - Impact: Unicode URLs (valid HTTP edge case) crash the runner
   - Fix: `urllib.parse.quote()` the URL path before building the request
   - Location: `scripts/lib/http-scenario-runner.py:374` (urlopen call)

### Metrics

| Metric | Value |
|--------|-------|
| Total wall time | ~18 min 20s |
| Agent time (sum) | ~1034s (~17.2 min) |
| Iterations completed | 0 (aborted during iter 1 gate) |
| Scenarios generated | 23 |
| Scenarios executed | 0 |
| Pre-gate checks passed | 3/3 (build, lint, typecheck) |
| Smoke test | PASSED |
| Final outcome | ABORTED (infrastructure failure) |
