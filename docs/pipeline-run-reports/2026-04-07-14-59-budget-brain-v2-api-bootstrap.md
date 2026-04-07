# Pipeline Run Report: budget-brain-v2 / 2026-03-28-api-bootstrap

**Date**: 2026-04-07 14:59 UTC
**Repository**: budget-brain-v2
**Spec**: 2026-03-28-api-bootstrap
**Dark Factory Version**: editable install from ~/sites/dark-factory
**Run ID**: 9d0169e111aa4ead
**Outcome**: Manually interrupted after scenario generation (per user request)

---

## Pre-Run State

- Budget-brain-v2 had modified `.dark-factory/reviews/` files and untracked `.dark-factory/architecture/` and `.dark-factory/precheck/` dirs from prior runs
- All runtime artifacts cleared during setup (events.jsonl, state, reports, scenarios, audit, architecture, precheck, failures)
- `shipped-scenarios/` (tracked in git) preserved

## Setup

### Repo Reset
- `git checkout -- .` + `git clean -fd` to restore tracked files and remove untracked
- Manually removed runtime artifact dirs: architecture, precheck, audit, events.jsonl, metrics.jsonl, failures, reports, scenarios, state, shipped-scenarios (restored from git after)
- Clean `git status` confirmed after reset

### Docker Build
- All images rebuilt from cache (no layer changes detected):
  - dark-factory-proxy (alpine:3.19)
  - dark-factory-base (node:22-alpine, claude-code@2.1.63, pnpm@9.15.9)
  - dark-factory-node
  - dark-factory-python
  - dark-factory-playwright
  - dark-factory-playwright-node

### Container Cleanup
- No orphaned containers or networks found from prior runs

### Other Setup
- Agents and templates synced from dark-factory to budget-brain-v2
- `pip install -e` re-ran successfully
- Python bytecode caches cleared

---

## Pipeline Run 1

### Configuration
- Language: node
- Model: claude-sonnet-4-20250514 (all agents)
- Autonomy level: 3 (fully autonomous)
- Enterprise features: enabled
- Build cmd: `pnpm install && pnpm run build`
- Lint cmd: `pnpm exec eslint .`
- Typecheck cmd: `pnpm -r typecheck`
- Scenario multi-run: n=3, k=2
- Test server: http://localhost:3000, /health, 45s timeout
- source_dirs: **not configured** (defaults to `["src"]`)

### Phase Progression

| Phase | Agent | Wall Time | Turns | Exit Code | Notes |
|-------|-------|-----------|-------|-----------|-------|
| Spec Validation | spec-validator | 189.6s | 200 | 0 | PASS, 90/100, LOW divergence |
| Architecture | architect | 145.2s | 200 | 0 | Produced manifest + environment files |
| Scenario Generation | scenario-architect | 209.7s | 200 | 0 | Initial pass: 5 files, coverage gaps detected |
| Coverage Regen | scenario-architect | 230.3s | 200 | 0 | Regen pass: 3 additional files, COVERAGE_PASS (23 anchors) |
| **Total wall time** | | **~775s (~13 min)** | | | Pipeline killed after scenarios completed |

### Observations

1. **Spec validation passed cleanly** (90/100, LOW divergence). No blockers identified.

2. **Architecture manifest produced 10 file entries** covering server.ts, config.ts, errors.ts, error-handler.ts, health.ts, logger.ts, index.ts, package.json. API contracts defined for GET /health. Environment deltas and lockfile also generated.

3. **Scenario generation needed a coverage regen pass.** Initial generation produced 5 scenario files but was missing coverage for 16 criteria:
   - AC-1, AC-2, AC-4, AC-5, AC-6, AC-7, AC-9
   - EC-1 through EC-9
   
   After regen, 3 additional files were produced (06-startup-validation.test.ts, 07-error-responses.scenarios.md, 08-logging-redaction.test.ts) and all 23 anchors passed coverage check.

4. **Mixed scenario formats**: The scenario architect produced both `.scenarios.md` files (5) and `.test.ts` files (2). The `.test.ts` files are actual TypeScript test code rather than declarative scenario descriptions. This is worth monitoring — the quality gate needs to be able to execute both formats.

5. **All agents hit the 200-turn limit** (estimated). The events show turns=200 for every agent. This could mean:
   - The turn limit is generous enough that agents naturally complete before hitting it
   - Or agents are being cut off at 200 turns. The exit_code=0 suggests they completed normally.

### Diagnostic Data

#### State File (final)
```
spec=2026-03-28-api-bootstrap
run_id=9d0169e111aa4ead
spec_hash=ff42f734228fdcb4ba029324ca695c00e4b16bb412dc06b89fa16181ce1ed293
status=interrupted
iteration=1
completed_step=2
```

#### Events JSONL (complete)
```jsonl
{"ts":"2026-04-07T15:00:44Z","event":"SPEC_START","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead"}
{"ts":"2026-04-07T15:00:44Z","event":"AGENT_START","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"role":"spec-validator","attempt":1}}
{"ts":"2026-04-07T15:03:54Z","event":"AGENT_COMPLETED","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"role":"spec-validator","turns":200,"exit_code":0,"model_id":"claude-sonnet-4-20250514","est_input_tokens":800000,"est_output_tokens":400000,"wall_seconds":189.65}}
{"ts":"2026-04-07T15:03:54Z","event":"AGENT_START","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"role":"architect","attempt":1}}
{"ts":"2026-04-07T15:06:19Z","event":"AGENT_COMPLETED","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"role":"architect","turns":200,"exit_code":0,"model_id":"claude-sonnet-4-20250514","est_input_tokens":800000,"est_output_tokens":400000,"wall_seconds":145.25}}
{"ts":"2026-04-07T15:06:20Z","event":"AGENT_START","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"role":"scenario-architect","attempt":1,"phase":"generate"}}
{"ts":"2026-04-07T15:09:49Z","event":"AGENT_COMPLETED","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"role":"scenario-architect","turns":200,"exit_code":0,"model_id":"claude-sonnet-4-20250514","est_input_tokens":800000,"est_output_tokens":400000,"wall_seconds":209.75,"phase":"generate"}}
{"ts":"2026-04-07T15:09:50Z","event":"COVERAGE_GAP","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"uncovered":["AC-1","AC-2","AC-4","AC-5","AC-6","AC-7","AC-9","EC-1","EC-2","EC-3","EC-4","EC-5","EC-6","EC-7","EC-8","EC-9"],"attempt":1}}
{"ts":"2026-04-07T15:09:50Z","event":"COVERAGE_REGEN","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"attempt":1,"uncovered":["AC-1","AC-2","AC-4","AC-5","AC-6","AC-7","AC-9","EC-1","EC-2","EC-3","EC-4","EC-5","EC-6","EC-7","EC-8","EC-9"]}}
{"ts":"2026-04-07T15:09:50Z","event":"AGENT_START","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"role":"scenario-architect","attempt":1,"phase":"coverage_regen"}}
{"ts":"2026-04-07T15:13:40Z","event":"AGENT_COMPLETED","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"role":"scenario-architect","turns":200,"exit_code":0,"model_id":"claude-sonnet-4-20250514","est_input_tokens":800000,"est_output_tokens":400000,"wall_seconds":230.27,"phase":"coverage_regen"}}
{"ts":"2026-04-07T15:13:40Z","event":"COVERAGE_PASS","spec":"2026-03-28-api-bootstrap","run_id":"9d0169e111aa4ead","data":{"anchor_count":23}}
```

#### Audit Log
The audit log (`2026-03-28-api-bootstrap-9d0169e111aa4ead.log`) captured full `docker run` commands for all 4 container launches, confirming:
- Spec validator: `:ro` project mount, reviews `:rw`
- Architect: `:ro` project mount, architecture `:rw`, tmpfs masks on scenarios/audit/state/failures/.git
- Scenario architect (both passes): `:ro` project mount, scenarios `:rw`, tmpfs mask on `/project/src` and `.git`

#### Container Logs
No container logs in `.dark-factory/reports/` — pipeline was interrupted before the quality gate phase which produces those logs.

#### Quality Gate Report
N/A — pipeline interrupted before implementation/gate phases.

#### Scenario Files Generated
```
01-contract.scenarios.md        (77 lines)
02-happy-path.scenarios.md      (71 lines)
03-error-conditions.scenarios.md (78 lines)
04-edge-cases.scenarios.md      (90 lines)
05-security.scenarios.md        (94 lines)
06-startup-validation.test.ts   (131 lines)  -- TypeScript test code
07-error-responses.scenarios.md (120 lines)
08-logging-redaction.test.ts    (146 lines)  -- TypeScript test code
Total: 807 lines across 8 files
```

---

## Findings & Recommendations

### CRITICAL: Source directory barrier not effective for monorepo

**Finding**: The scenario architect container masked `/project/src` (the default `source_dirs=["src"]`), but budget-brain-v2's actual source code lives in `apps/` and `packages/`. The `src/` directory exists but is empty. This means the scenario architect had **read access to all implementation source code** in `apps/` and `packages/`, defeating the information barrier.

**Root cause**: `config.toml` does not set `source_dirs`. The default is `["src"]`, which doesn't match the monorepo layout.

**Recommendation**: 
1. The `dark-factory init` command (or the config template) should auto-detect source directories for monorepos (look for `apps/`, `packages/`, `src/`, `lib/`, etc.)
2. Add a pre-flight check that warns if `source_dirs` only contains default values and the project has other likely source directories
3. For immediate fix: add `source_dirs = ["apps", "packages", "src"]` to budget-brain-v2's config.toml

### OBSERVATION: Coverage gap on initial scenario generation

16 of ~23 criteria were uncovered after the initial scenario generation pass. The regen pass filled them all, but that's a 70% miss rate on the first pass. The initial 5 scenario files focused on happy-path and contract scenarios, leaving most acceptance criteria (AC) and all edge cases (EC) uncovered.

**Recommendation**: Consider tuning the scenario architect prompt to emphasize AC/EC coverage upfront, or restructure the coverage check to be more lenient on the first pass if regen consistently fills gaps.

### OBSERVATION: Mixed scenario formats (.md and .test.ts)

The scenario architect produced both declarative `.scenarios.md` files and executable `.test.ts` files. This creates two different execution paths in the quality gate. Worth verifying both paths work correctly.

### OBSERVATION: Token estimates appear to be fixed values

All agents report `est_input_tokens=800000` and `est_output_tokens=400000` regardless of actual work done. These appear to be placeholder estimates rather than real token usage. Not useful for cost tracking.

### METRIC: Phase timing summary

| Phase | Time |
|-------|------|
| Spec validation | 3m 10s |
| Architecture | 2m 25s |
| Scenario generation (initial) | 3m 30s |
| Scenario generation (regen) | 3m 50s |
| **Total through scenarios** | **~13 min** |
