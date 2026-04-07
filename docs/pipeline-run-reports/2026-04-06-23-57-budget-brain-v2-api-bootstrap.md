# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date (UTC):** 2026-04-06 23:57
**Repository:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Dark Factory version:** editable install from ~/sites/dark-factory (main @ d95a133)
**Run ID:** 6c41c4ae0c7b4bc6
**Model:** claude-sonnet-4-20250514 (all agents)

## Prior State (before reset)

Previous run was interrupted at iteration 2:
- `last_decision=NO-SHIP`, `smoke_result=failed`, `pass_rate=0`, `gate_exit_code=2`, `server_error=true`
- Modified files: `apps/api/package.json`, `apps/api/src/index.ts` (deleted), `package-lock.json`, `pnpm-lock.yaml`, plus untracked implementation files
- Architecture and review artifacts existed from prior iterations

**Comparison with this run:** Significant improvement — this run achieved smoke_result=passed (vs failed), pass_rate=70 (vs 0), server_error=false (vs true). The server startup failure that plagued the prior run is resolved.

## Preparation

- Restored tracked files, removed untracked files/dirs, cleaned all `.dark-factory` runtime artifacts
- Cleared Python `__pycache__` dirs
- Copied latest agents/templates to budget-brain-v2
- Reinstalled dark-factory (editable) — v1.0.0
- Rebuilt Docker images (`dark-factory build node`) — all layers cached
- Cleaned orphaned Docker containers/networks

---

## Run 1 — Iteration 1 (killed after Oracle NO-SHIP, before iter 2 implementer completed)

### Timeline

| Time (UTC) | Event | Wall (s) | Notes |
|------------|-------|----------|-------|
| 00:00:23 | SPEC_START | — | |
| 00:00:23 | spec-validator START | — | |
| 00:02:03 | spec-validator DONE | 100s | exit 0, 200 turns |
| 00:02:03 | architect START | — | |
| 00:04:23 | architect DONE | 140s | exit 0, 200 turns |
| 00:04:24 | scenario-architect START (generate) | — | |
| 00:06:18 | scenario-architect DONE (generate) | 114s | exit 0 |
| 00:06:18 | **COVERAGE_GAP** | — | 16 uncovered criteria (see below) |
| 00:06:18 | scenario-architect START (coverage_regen) | — | |
| 00:09:26 | scenario-architect DONE (coverage_regen) | 188s | exit 0, 23 anchors |
| 00:09:26 | **COVERAGE_PASS** | — | |
| 00:09:38 | scenario-architect START (repair) | — | |
| 00:12:00 | scenario-architect DONE (repair) | 142s | exit 0 |
| 00:12:00 | **BARRIER_VIOLATION** | — | scenario-architect referenced `src` |
| 00:12:01 | implementer START (attempt 1) | — | |
| 00:17:08 | implementer DONE (attempt 1) | 307s | exit 0, 200 turns |
| 00:17:12 | implementer START (attempt 2) | — | precheck retry? |
| 00:19:23 | implementer DONE (attempt 2) | 131s | exit 0, 200 turns |
| 00:19:30 | **SMOKE_COMPLETED** | — | success=true |
| 00:19:55 | **GATE_COMPLETED** | — | exit 1, pass_rate=70, 40 scenarios, 0 crashed |
| 00:19:55 | reviewer START | — | |
| 00:21:34 | reviewer DONE | 99s | exit 0 |
| 00:21:34 | oracle START (attempt 1) | — | |
| 00:22:31 | oracle DONE (attempt 1) | 57s | **SEMANTIC LEAKAGE** detected |
| 00:22:32 | oracle START (attempt 2) | — | re-run after leakage |
| 00:23:15 | oracle DONE (attempt 2) | 44s | NO-SHIP decision |
| 00:23:16 | iter 2 implementer START | — | **killed here** |

**Total wall time (start to kill):** ~23 minutes
**Estimated iteration 1 cost:** $84.00 (400 turns)

### Spec Validation

- **Verdict:** PASS (score 93, divergence LOW, 0 blockers)

### Coverage Gap (initial scenario generation)

First-pass scenario generation missed 16 criteria:
```
Uncovered: AC-1, AC-2, AC-4, AC-5, AC-6, AC-7, AC-9, AC-10,
           EC-1, EC-2, EC-3, EC-5, EC-6, EC-7, EC-8, EC-9
```
Coverage regen successfully filled all gaps (23 anchors total). This is a recurring pattern — the initial generate phase consistently undercovers, requiring the regen pass.

### Barrier Violation

```
[2026-04-07T00:12:00Z] BARRIER VIOLATION: scenario-architect output references src
```
The scenario-architect's repair phase output referenced `src` paths. This is a **defense-in-depth detection** — the primary barrier (tmpfs overlay masking `src`) was in place, so the agent couldn't actually read source code. However, the output text contained references to `src`, likely from the spec itself or from inference. The audit scanner correctly flagged it.

### Semantic Leakage

```
[2026-04-07T00:22:31Z] SEMANTIC LEAKAGE in oracle feedback: pattern '\b\d+%\s*(?:pass|fail)': ['70% pass']
```
The oracle's first attempt at writing feedback included the literal "70% pass" rate, which would leak test result specifics to the implementer. The pipeline detected this and re-ran the oracle, which produced clean feedback on the second attempt.

### Implementer Behavior

The implementer ran twice (307s + 131s). The second run appears to be a precheck-triggered retry — the first implementation likely failed the precheck (build/lint/type-check) and was re-run. Files created:
- `apps/api/src/server.ts` (new)
- `apps/api/src/common/` (new directory with modules)
- Modified: `apps/api/package.json`, `apps/api/src/index.ts`, `pnpm-lock.yaml`

### Quality Gate Results

| Gate | Status | Details |
|------|--------|---------|
| Build | PASS | Exit code 0 |
| Lint | PASS | Exit code 0 |
| Type Check | PASS | Exit code 0 |
| Self-Tests | SKIP | No command configured |
| Blind Scenarios (executable) | FAIL | Exit code 1 |
| Blind Scenarios (HTTP) | FAIL | 1/4 passed (25%) |
| Mutation Testing | SKIP | Not configured |

**Executable scenarios:** 1 test file with 4 tests — all 4 passed, but Vitest reported 1 unhandled error:
```
Error: done() callback is deprecated, use promise instead
❯ Socket.<anonymous> .dark-factory/scenarios/2026-03-28-api-bootstrap/05-server-startup.test.ts:61:11
```
This is a **scenario quality issue** — the scenario architect used the deprecated `done()` callback pattern, causing Vitest to report an error even though the tests passed.

**HTTP scenarios:** 28 passed, 12 failed out of 40 total (70% pass rate).

**Unrecognized assertion:** The HTTP scenario runner logged:
```
WARNING: unrecognized assertion (skipped): '- Body field "error.details.limit" equals 500'
```
This suggests the scenario architect wrote an assertion format the runner doesn't support.

### Failing HTTP Scenarios (12)

| Scenario | Category |
|----------|----------|
| malformed JSON returns validation error | Error handling (FR-3) |
| CORS handles preflight request | CORS (FR-1) |
| AppError returns structured error response | Error handling (FR-3) |
| ZodError returns 400 with field-level details | Error handling (FR-3) |
| Unknown Error returns 500 with generic message | Error handling (FR-3) |
| Non-Error thrown value returns 500 with generic message | Error handling (FR-3) |
| Password field in request body is redacted in logs | Security/Logging (FR-2) |
| Deeply nested sensitive field logging behavior | Security/Logging (FR-2) |
| Health check with error status returns degraded | Health (FR-4) |
| Health check function throws exception | Health (FR-4) |
| Rate limit exceeded returns 429 | Rate limiting |
| Rate limit enforcement per IP | Rate limiting |

**Failure pattern:** Error handling middleware (FR-3) is the largest cluster (5 failures), followed by logging/security (FR-2, 2 failures), health check edge cases (FR-4, 2 failures), rate limiting (2 failures), and CORS preflight (1 failure).

### Reviewer Assessment

- **Score: 82/100 (PASS)**
- Code Quality: 20/20, Security: 15/15, Spec Compliance: 28/30, Robustness: 9/10
- Test Results: 10/25 (dragged down by 70% pass rate)
- Identified 2 issues: CORS dev-only conditional, request logging gap

### Oracle Decision

**NO-SHIP** — Correct decision given 70% pass rate (target is 95%).

Key feedback items for iteration 2:
1. Fix CORS configuration (remove development-only conditional)
2. Implement explicit request lifecycle logging
3. Complete ZodError field extraction
4. Health check exception handling

### Oracle Feedback Classifications
```
FR-1: SPEC_AMBIGUOUS
FR-2: IMPL_BUG
FR-3: SCENARIO_CRASH
FR-4: IMPL_BUG
AC-1: SPEC_AMBIGUOUS
AC-3: IMPL_BUG
AC-4: SCENARIO_CRASH
AC-6: SCENARIO_CRASH
```
3 items classified as SCENARIO_CRASH (suggests some failures may be scenario quality issues rather than implementation bugs).

### State File (at kill)

```
spec=2026-03-28-api-bootstrap
run_id=6c41c4ae0c7b4bc6
spec_hash=173013a21fcd806f997bb17beb5cb93b65981dc253e96d4264fd5aae98f80b2a
status=running
completed_step=2
last_decision=NO-SHIP
iteration=2
phase=
smoke_result=
pass_rate=
gate_exit_code=
server_error=
```

---

## Key Observations / Regression-Improvement Metrics

### Improvements vs. Prior Run
| Metric | Prior Run | This Run | Delta |
|--------|-----------|----------|-------|
| Smoke test | FAILED (server_error=true) | PASSED | Fixed |
| Pass rate | 0% | 70% | +70pp |
| Gate exit code | 2 (crash) | 1 (fail) | Improved |
| Scenarios crashed | unknown | 0 | Clean |

### Issues to Investigate

1. **Scenario coverage gap on first pass:** 16 of ~23 criteria uncovered on initial generation. The regen pass fixes this, but the initial pass should be better. The scenario architect is spending ~300s on generate+regen+repair (114+188+142) when ideally a single pass would suffice.

2. **Scenario quality — deprecated patterns:** The executable scenario used `done()` callbacks deprecated by Vitest, causing spurious unhandled errors. The scenario architect should use modern async/await patterns.

3. **Scenario quality — unrecognized assertions:** At least one HTTP assertion format (`Body field "error.details.limit" equals 500`) wasn't recognized by the runner. Either the runner needs to support more formats, or the scenario architect needs stricter templates.

4. **Oracle semantic leakage:** First oracle attempt leaked "70% pass" — caught and re-run automatically. This is the pipeline working as designed, but it costs an extra oracle invocation (~$0.19).

5. **Barrier violation (scenario-architect → src):** The repair phase referenced `src` in output text. The primary barrier held (tmpfs mask), but the detection is worth tracking. May warrant investigation into what exactly was referenced.

6. **Implementer double-run:** The implementer ran twice (307s + 131s) before smoke test. Likely a precheck failure on first attempt. This adds ~130s and ~$21 in cost. Could the precheck failures be better communicated to reduce retries?

7. **CORS dev-only conditional:** This is a spec interpretation issue — the implementer restricted CORS to development mode, but the spec calls for environment-variable-driven configuration in all environments. The spec could be more explicit, or the feedback classifier correctly identified it as SPEC_AMBIGUOUS.

8. **FR-3 SCENARIO_CRASH classifications:** 3 feedback items classified as scenario crashes rather than implementation bugs. If the scenarios themselves are crashing rather than the implementation being wrong, this inflates the failure rate unfairly.
