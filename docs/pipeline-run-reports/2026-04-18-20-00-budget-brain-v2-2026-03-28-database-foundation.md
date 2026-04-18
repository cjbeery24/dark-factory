# Pipeline Run Report: database-foundation — Phase 6 V1 Run 1/3

**Target:** budget-brain-v2
**Spec:** 2026-03-28-database-foundation
**Run ID:** `65f81a73cf2646ed`
**Date:** 2026-04-18 20:00 UTC → 21:14 UTC (≈74 min)
**Outcome:** ESCALATED
**Purpose:** Phase 6 V1 free-run baseline, Run 1 of 3. No anchor flags. Artifacts preserved via archive mechanism (commit 51d11b9).

## Iteration summary

| Iter | Pass rate | Satisfaction | Scenarios | Crashed | Trajectory | Cost (est USD) |
|------|-----------|--------------|-----------|---------|------------|----------------|
| 1    | 44%       | 40%          | 9         | 0       | —          | $126           |
| 2    | 80%       | 80%          | 10        | 0       | IMPROVING  | $42            |
| 3    | 80%       | 80%          | 10        | 0       | PLATEAU    | $42            |

**Total cost:** ~$210. **Wall time:** 4428s (73.8 min).

## State file

```
spec=2026-03-28-database-foundation
run_id=65f81a73cf2646ed
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
completed_step=2
last_decision=NO-SHIP
iteration=3
smoke_result=passed
phase=review
pass_rate=80
gate_exit_code=1
status=escalated
```

## V-dimension instrumentation (I1/I2/I3/I4)

- **I1 architect_manifest_hash:** `58fb4bbebd35103bfb88095bb3dea7773a64e2d85f0f2e8f93c3af48df84f4fa`
- **I2 scenario_fingerprint count:** 13 fingerprints emitted across initial + amend regen + iter-2/3 reruns
- **I3 classifier_io_hash (iter 2):** input `a67b2e21bdaa…`, output `f3305c720f6f…`, defects=2, failing=2
- **I4 iteration_temporal_signature:**
  - Iter 1→2: `tests_moved_to_pass=0`, `tests_moved_to_fail=5`, `unchanged=0`, delta `{IMPL_BUG: +3, BRITTLE: +2}`
  - Iter 2→3: `tests_moved_to_pass=3`, `tests_moved_to_fail=0`, `unchanged=2`, delta `{ENV_ISSUE: +2, IMPL_BUG: -3, BRITTLE: -2}`

## Stable failures (iter 2 + iter 3)

Both failing tests are `ENV_ISSUE` per the defect classifier:

1. `Database Migration Scripts > [AC-4] db:migrate applies pending migrations to database`
   — Fails with `Error: Command failed: pnpm run db:migrate` → `drizzle-kit: not found`.
2. `Database Migration Scripts > [EC-9] migration runs are idempotent against database with existing data`
   — Same error. Classifier rationale: "missing binary in environment prevents migration command execution."

The TE correctly flagged AC-4 and EC-9 as `spec-vehicle-mismatch` (subprocess required) in COVERAGE-GAPS.md, but generated scenarios for them anyway using `execSync('pnpm run db:migrate')`. The scenarios fail because the test container doesn't have `drizzle-kit` installed/available. This is an environment-contract issue, not an implementation bug — the architect didn't declare drizzle-kit as a devDependency discoverable inside the test harness, or the gate container doesn't install it.

## Coverage amend flow (§4 insights)

Coverage floor detected gaps after initial TE output. Categorization:

- **spec-vehicle-mismatch (5):** AC-2, AC-3, AC-4, EC-1, EC-9 — subprocess execution / process lifecycle control. Surfaced via `COVERAGE_SPEC_VEHICLE_MISMATCH`; not routed to architect (by design).
- **architect-entry-point (4):** AC-1, AC-9, EC-2, EC-8 — needed `apps/api/src/server.ts` and `apps/api/src/db/health-check.ts` exposed in `test_integration.entry_points`.
- **category-missing (2):** EC-7, EC-10 — TE drift; emitted `COVERAGE_GAP_CATEGORY_MISSING` warning.

`COVERAGE_ARCHITECT_AMEND` fired once; architect added the two entry points; TE regen covered the architect-fixable gaps. `COVERAGE_PASS` emitted after regen.

## Architect manifest validation retries (G1)

First architect attempt failed validation twice before succeeding:

- **Attempt 2 failure:** `data_models 'RLSInfrastructure': location 'apps/api/src/db/migrations' not found in file_map`
- **Attempt 3 failure:** `environment[0]/[1]: missing 'tool'`; `runtime amendment path not allowed: 'commands.db_generate' / 'commands.db_migrate' / 'commands.db_studio' / 'layout.migration_dir' / 'layout.schema_dir'`
- Attempt 4 succeeded (`ARCHITECT_MANIFEST_HASH` emitted).

G1 budget consumed most of its capacity here — worth noting.

## Quality gate summary (iter 3, final)

```
Install: PASS | Setup: PASS | Build: PASS | Lint: PASS | Type Check: PASS
Self-Tests: FAIL | Blind Scenarios: FAIL | Mutation Testing: SKIP

Tests: 2 failed | 44 passed | 25 skipped (71 total, 17 files)
Both failures: execSync('pnpm run db:migrate') → drizzle-kit: not found
```

Substrate findings: `{"status": "skip", "findings": []}` (substrate verifier skipped — scheduled for future phase).

## Notable events

- **`BARRIER_VIOLATION`** ×2 (iter 1 implementer, attempts 1 and 2): implementer tried to read `.git` (masked tmpfs). Defense-in-depth caught it; no data leaked. Third attempt ran clean.
- **Pre-gate retries:** 2 retries fired before iter 1's implementer produced clean output. R3 budget consumed 2 of 3.
- **`MANIFEST_VALIDATION_RETRY`** ×2 during architect phase (described above).
- **`COVERAGE_ARCHITECT_AMEND_COMPLETED`** outcome=success (1/2 amend budget used).
- **`ORACLE_METRIC_DELTA`** delta=0 on both iter 1 and iter 2 (Oracle claimed pass rate matched gate report).
- **`SMOKE_COMPLETED`** success=true all iterations (server boots clean).

## Observations vs. Phase 5 benchmark

Phase 5 3-run iter-1 pass rates: 0% / 0% / 91% (last was interrupted).
This run iter-1: 44%. Falls inside the previously observed envelope but with a materially different shape — iter 1 produced a partially working implementation that iter 2 improved to 80%, at which point ENV_ISSUE failures froze the trajectory.

**Key differences from Phase 5:**
- Coverage amend fired and worked correctly (Phase 5 runs did not exercise this path).
- Pre-gate retry budget consumed (R3 first real use — prior Phase 5 runs all passed on attempt 1).
- Temporal signature delta is cleaner than expected — no regressions in the iter 2→3 transition, just unchanged ENV_ISSUE failures.

## Variance sources (for V1 n=3 analysis)

Preserved under `.dark-factory/archived-runs/65f81a73cf2646ed/`. Compare against Run 2 and Run 3:

- `architecture/<spec>-manifest.yaml` — architect output (I1 hash)
- `scenarios/<spec>/` — TE output (I2 fingerprints)
- `audit/gate-telemetry.jsonl` — classifier IO (I3 hashes)
- `reports/<spec>-defects-iter{N}.json` — defect categorization
- `state/`, `failures/`, `events.jsonl` — full timeline

## Follow-ups

1. **Drizzle-kit environment contract gap** — architect's environment declaration doesn't surface `drizzle-kit` to the gate container. Either architect should add it to dev dependencies with a discoverable shim in the gate harness, or the spec's AC-4/EC-9 should be scoped out of unit-TE coverage (`spec-vehicle-mismatch` tells us this, but scenarios still got generated).
2. **Why did TE generate execSync tests for AC-4/EC-9 after flagging them spec-vehicle-mismatch?** The amend flow classifies but doesn't instruct the TE to drop those anchors. Review `agents/test-engineer.md` Hard Rule #9 semantics — if category=spec-vehicle-mismatch, should the TE omit the scenario rather than write a subprocess test?
3. **G1 architect retries** used 2 of 3 — worth confirming across V1 n=3 whether the architect first-attempt validation failure is a persistent issue or noise.

## Next

Run 2 of V1 baseline (this spec, same config, preserve Run 1 archive).
