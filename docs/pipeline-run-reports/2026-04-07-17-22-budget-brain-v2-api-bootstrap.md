# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date:** 2026-04-07 17:22 UTC
**Repo:** budget-brain-v2
**Spec:** 2026-03-28-api-bootstrap
**Dark Factory Commit:** 8c2ea7c (Auto-detect monorepo source_dirs in backcompat synthesizer)

---

## Pre-Run Checklist

- [x] Repository reset (tracked files restored, untracked cleaned)
- [x] Runtime artifacts cleared (scenarios/, reports/, audit/, state/, failures/, events.jsonl)
- [x] Architecture files removed (environment.toml, bootstrap delta)
- [x] Python bytecode caches cleared
- [x] dark-factory init --update run
- [x] pip install -e completed
- [x] Docker images rebuilt (all layers cached)
- [x] Orphaned containers/networks cleaned

---

## Run 1

### Pipeline Invocation
```
dark-factory run 2026-03-28-api-bootstrap
```

**Start Time:** 2026-04-07 17:24:03 UTC
**End Time:** 2026-04-07 17:42:13 UTC (approx)
**Total Duration:** ~18 minutes
**Exit Code:** Non-zero (interrupted)
**Final Decision:** N/A — pipeline interrupted before reaching implementation phase

### Outcome: INTERRUPTED at Step 2.6 (Workspace Script Consistency Pre-flight)

The pipeline completed spec validation, architecture lock, and scenario generation (with 2 coverage regen rounds), then was blocked by the workspace consistency check that runs before the iteration loop.

### Phase Progression

| Phase | Agent | Status | Wall Time (s) | Turns | Model |
|-------|-------|--------|---------------|-------|-------|
| Spec Validation | spec-validator | PASS | 157.0 | 200 | claude-sonnet-4-20250514 |
| Architecture Lock | architect | PASS | 166.6 | 200 | claude-sonnet-4-20250514 |
| Scenario Gen (initial) | scenario-architect | PASS | 151.2 | 200 | claude-sonnet-4-20250514 |
| Coverage Regen #1 | scenario-architect | PASS | 384.0 | 200 | claude-sonnet-4-20250514 |
| Coverage Regen #2 | scenario-architect | PASS | 228.8 | 200 | claude-sonnet-4-20250514 |
| Workspace Check | (no container) | FAIL | — | — | — |

### Events Summary (from events.jsonl)

```json
{"ts":"2026-04-07T17:24:03Z","event":"SPEC_START","spec":"2026-03-28-api-bootstrap","run_id":"35f8051f565f49dc"}
{"ts":"2026-04-07T17:24:04Z","event":"AGENT_START","data":{"role":"spec-validator","attempt":1}}
{"ts":"2026-04-07T17:26:41Z","event":"AGENT_COMPLETED","data":{"role":"spec-validator","exit_code":0,"wall_seconds":156.95}}
{"ts":"2026-04-07T17:26:41Z","event":"AGENT_START","data":{"role":"architect","attempt":1}}
{"ts":"2026-04-07T17:29:28Z","event":"AGENT_COMPLETED","data":{"role":"architect","exit_code":0,"wall_seconds":166.60}}
{"ts":"2026-04-07T17:29:28Z","event":"AGENT_START","data":{"role":"scenario-architect","attempt":1,"phase":"generate"}}
{"ts":"2026-04-07T17:31:59Z","event":"AGENT_COMPLETED","data":{"role":"scenario-architect","exit_code":0,"wall_seconds":151.18,"phase":"generate"}}
{"ts":"2026-04-07T17:31:59Z","event":"COVERAGE_GAP","data":{"uncovered":["AC-1","AC-2","AC-4","AC-5","AC-6","AC-7","AC-10","EC-1","EC-2","EC-3","EC-4","EC-5","EC-6","EC-7","EC-8","EC-9"],"attempt":1}}
{"ts":"2026-04-07T17:31:59Z","event":"COVERAGE_REGEN","data":{"attempt":1,"uncovered":[...16 items...]}}
{"ts":"2026-04-07T17:32:00Z","event":"AGENT_START","data":{"role":"scenario-architect","attempt":1,"phase":"coverage_regen"}}
{"ts":"2026-04-07T17:38:24Z","event":"AGENT_COMPLETED","data":{"role":"scenario-architect","exit_code":0,"wall_seconds":383.96,"phase":"coverage_regen"}}
{"ts":"2026-04-07T17:38:24Z","event":"COVERAGE_GAP","data":{"uncovered":["EC-4","EC-9"],"attempt":2}}
{"ts":"2026-04-07T17:38:24Z","event":"COVERAGE_REGEN","data":{"attempt":2,"uncovered":["EC-4","EC-9"]}}
{"ts":"2026-04-07T17:38:24Z","event":"AGENT_START","data":{"role":"scenario-architect","attempt":1,"phase":"coverage_regen"}}
{"ts":"2026-04-07T17:42:13Z","event":"AGENT_COMPLETED","data":{"role":"scenario-architect","exit_code":0,"wall_seconds":228.84,"phase":"coverage_regen"}}
{"ts":"2026-04-07T17:42:13Z","event":"COVERAGE_PASS","data":{"anchor_count":23}}
```

No BARRIER_PATH_ECHO_DETECTED or SMOKE_COMPLETED events (pipeline never reached those phases).

### State File
```
spec=2026-03-28-api-bootstrap
run_id=35f8051f565f49dc
spec_hash=ff42f734228fdcb4ba029324ca695c00e4b16bb412dc06b89fa16181ce1ed293
status=interrupted
iteration=1
completed_step=2
```

### Blocking Error

The pipeline printed a workspace consistency check error and then halted after scenario generation:

```
ERROR: error: Workspace consistency check failed — fix before running the pipeline:
  apps/api: missing 'lint', 'test'
  apps/web: missing 'lint', 'test'
  packages/shared: missing 'lint', 'test'

Add the missing scripts to each package.json listed above.
```

This error comes from Step 2.6 in the orchestrator (`orchestrator.py:454-476`). The workspace check runs after `completed_step=2` (scenarios done) but before `completed_step=3` (iteration loop). It verifies that all workspace packages have the scripts required by the configured `lint_cmd` and `test_cmd`. Since the target project's package.json files lack `lint` and `test` scripts, this check raises a `DarkFactoryError` which triggers the interrupt handler.

**Root cause:** The `2026-03-28-api-bootstrap` spec is a bootstrap spec — the project doesn't have lint/test scripts yet because this spec is supposed to *create* them. The workspace check assumes these scripts already exist, creating a chicken-and-egg problem for bootstrap specs.

### Scenario Generation Analysis

**Coverage journey:**
1. Initial generation: 4 `.scenarios.md` files, 30 scenario blocks parseable
2. Coverage gap detected: 16 uncovered anchors (AC-1,2,4,5,6,7,10 + EC-1-9)
3. Regen #1 (384s): Narrowed to 2 uncovered (EC-4, EC-9) — major improvement
4. Regen #2 (229s): All 23 anchors covered — COVERAGE_PASS

**Scenario files produced:**
- `01-health-endpoint.scenarios.md`
- `02-error-handling.scenarios.md`
- `03-cors-rate-limiting.scenarios.md`
- `04-edge-cases.scenarios.md`
- `05-startup-validation.test.ts`
- `06-log-redaction.test.ts`
- `07-error-responses.test.ts`
- `08-health-degraded.test.ts`
- `09-rate-limiting.scenarios.md`
- `10-undefined-routes.scenarios.md`
- `11-cors-header-omission.scenarios.md`

**Coverage gaps (final):**
- All uncovered_anchors: `[]` (none)
- Weakly covered: FR-1, FR-2, FR-3, FR-4, AC-1, AC-3, AC-4, AC-6
- Duplicate tags: FR-3 (2), FR-4 (3)

### Audit Log — Barrier Verification

The Scenario Architect's docker run argv correctly masks all three source_dirs:

```
--tmpfs /project/apps/api/src:ro,size=0
--tmpfs /project/apps/web/src:ro,size=0
--tmpfs /project/packages/shared/src:ro,size=0
--tmpfs /project/.git:ro,size=0
```

All 3 scenario-architect container launches (lines 3, 4, 5 in audit log) have identical masking. The source_dirs auto-detection from the backcompat synthesizer is working correctly.

The architect container (line 2) correctly masks:
```
--tmpfs /project/.dark-factory/scenarios:ro,size=0
--tmpfs /project/.dark-factory/audit:ro,size=0
--tmpfs /project/.dark-factory/state:ro,size=0
--tmpfs /project/.dark-factory/failures:ro,size=0
--tmpfs /project/.git:ro,size=0
```

### Quality Gate Report
N/A — pipeline never reached the quality gate phase.

### Container Logs
N/A — no service containers were started.

### Health Check Errors
N/A — no health checks were attempted.

---

## Issues / Observations

### BUG: Workspace consistency check blocks bootstrap specs (P1)
**Symptom:** Pipeline interrupted at Step 2.6 with "Workspace consistency check failed" for missing lint/test scripts.
**Root cause:** The workspace check (`orchestrator.py:454-476`) requires all workspace packages to have `lint` and `test` scripts before the iteration loop can start. But bootstrap specs exist specifically to *create* these scripts — they can't exist yet.
**Impact:** Bootstrap specs can never reach the implementation phase. This is a blocker for any fresh project setup.
**Suggested fix:** Either skip the workspace check for bootstrap specs, or make the check aware that the implementer will create these scripts.

### OBSERVATION: Duplicate CLI error message
The workspace check error appears twice — once at the top of the pipeline output (before "Checking spec...") and once as the blocking error at Step 2.6. The first occurrence doesn't halt the pipeline. This suggests there's an early non-fatal check that prints the error and a later fatal check that actually blocks. The duplication may confuse users.

### OBSERVATION: Coverage regen is effective but expensive
- Initial generation covered only 7/23 anchors (30%)
- Regen #1 covered 21/23 anchors (91%) — massive improvement
- Regen #2 covered 23/23 anchors (100%)
- Total scenario generation wall time: 151 + 384 + 229 = 764s (~12.7 min)
- This is 70% of the total pipeline runtime

### OBSERVATION: Large "weakly covered" set
Despite COVERAGE_PASS, 8 anchors are flagged as weakly covered (FR-1 through FR-4, AC-1, AC-3, AC-4, AC-6). This suggests the coverage checker is conservative — it passes on anchor tag presence but flags quality concerns.

### OBSERVATION: Mixed scenario formats
The scenario architect produced both `.scenarios.md` (HTTP behavioral) and `.test.ts` (unit test) files. Files 05-08 are `.test.ts` format while the rest are `.scenarios.md`. This format mixing may cause issues with the quality gate runner if it expects a single format.

---

## Metrics

| Metric | Value |
|--------|-------|
| Total pipeline wall time | ~18 min |
| Spec validator wall time | 157s |
| Architect wall time | 167s |
| Scenario gen total wall time | 764s |
| Coverage regen rounds | 2 |
| Final anchor count | 23 |
| Scenario files produced | 11 |
| Scenario blocks parseable | 30 |
| Phases completed | 3 (validation, architecture, scenarios) |
| Phases blocked | 1 (workspace check) |
| Docker containers launched | 5 |
| Barrier violations detected | 0 |
