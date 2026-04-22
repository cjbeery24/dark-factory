# Pipeline Run Report: 2026-03-28-database-foundation (post fix-pack)

- **Date:** 2026-04-22
- **Start → terminal:** 18:09Z → 19:07Z (wall time **~58 min**)
- **Target project:** `budget-brain-v2` (fresh `apps/api/` reset pre-run)
- **Framework SHA:** `40f8e36` (`feat(pipeline): database-foundation escalation fix pack (6 tasks)`)
- **Run ID:** `d7b0c5b54d684523`
- **Outcome:** **ESCALATED** (3 iterations, max; trajectory `PLATEAU`)

## Purpose of this run

Validate the six-task consensus fix pack from `docs/plans/2026-04-22-database-foundation-escalation-fixes.md` against the same spec/target that produced the 2026-04-21 ESCALATED run (archive `4736bbd9`). Task sequence shipped: **45 → 42 → 40 → 43 → 44 → 41**.

## Iteration trajectory

| Iter | Impl wall (s) | Pass rate | Hard gates | Classifier signal | Result |
|-----:|---:|---:|---|---|---|
| 1 | 583 | **75%** | Substrate FAIL, Import Contract FAIL, Scenarios FAIL | `SPEC_AMBIGUOUS` on AC-7/8/12 | NO-SHIP |
| 2 | 321 | **69%** (regression) | Substrate FAIL, Import Contract FAIL, Scenarios FAIL | `ENV_ISSUE` (Vitest module resolution) | NO-SHIP (`ITERATION_REGRESSION`) |
| 3 (×3 attempts) | 389+124+74 | **75%** | Substrate FAIL, Import Contract FAIL, Scenarios FAIL | (escalation) | `TRAJECTORY=PLATEAU` → **ESCALATED** |

## Final state file

```
spec=2026-03-28-database-foundation
run_id=d7b0c5b54d684523
completed_step=2
last_decision=NO-SHIP
iteration=3
server_error=
smoke_result=passed
phase=review
pass_rate=75
gate_exit_code=1
status=escalated
```

## Final quality gate summary

```
| Gate                          | Status |
|-------------------------------|--------|
| Install                       | PASS   |
| Setup                         | PASS   |
| Build                         | PASS   |
| Lint                          | PASS   |
| Type Check                    | PASS   |
| Substrate Verification        | FAIL   |
| Import Contract               | FAIL   |
| Self-Tests                    | PASS   |
| Scenario Lint                 | PASS   |
| Blind Scenarios (executable)  | FAIL   |
| Mutation Testing              | SKIP   |
```

## Substrate findings (identical across all three iterations)

```json
{
  "status": "fail",
  "findings": [
    {
      "severity": "error",
      "code": "substrate-guc-unregistered",
      "message": "GUC 'app.tenant_id' is not registered after setup; check `SELECT current_setting('app.tenant_id', true)` returned NULL",
      "path": "/manifest.toml"
    },
    {
      "severity": "error",
      "code": "substrate-rls-table-missing",
      "message": "table 'baseline_pattern' does not exist in pg_tables; the migration probably never ran CREATE TABLE",
      "path": "/manifest.toml"
    }
  ]
}
```

**The same two findings persisted across all three iterations without ever being addressed by an implementer.** The iter-2 feedback explicitly notes: _"No changes in diff to `apps/api/src/db/` files"_ — the implementer never touched the database layer between iter 1 and iter 2, and per the iter-3 escalation archive, only cosmetic changes in iter 3 attempts.

## Where the fix pack worked as intended

- **Task 45 (setup-phase hang detection)**: Setup gate reported `PASS` cleanly across all three iterations — no hang, no `SETUP_HANG` event. (Prior April-21 runs had hangs; this run's PG sidecar came up healthy.)
- **Task 42 (TS type-only exports precheck)**: No precheck rejection events. The architect manifest's `exports[]` did not include type-only declarations.
- **Task 43 (RLS `force=true`)**: The architect's emitted substrate manifest did not opt into `force=true` because the spec doesn't mandate it; the backward-compat default-off path (legacy manifest shape) worked correctly — no spurious `substrate-rls-no-force-check` or `substrate-rls-not-forced` findings.
- **Task 44 (`assertTxGucEquals`)**: `CONTRACTS_STAGED` event confirmed `tx_guc.ts` landed at `.dark-factory/scenarios/<spec>/_contracts/postgres/tx_guc.ts` — first in-production validation of the Task 44 staging path. `ls` of the staged dir confirmed all five helpers present.
- **Task 41 (`verifiable` field)**: No `[[rls_table]]` entries carried `verifiable = false` this run; backward-compat default-on ran as designed.
- **Task 40 (classification hints)**: Zero `BARRIER_PATH_ECHO_DETECTED` events. Implementer-visible feedback used only spec-anchor vocabulary (FR-N / AC-N / EC-N + behavioral category). Internal reason codes (`substrate-*`, `POSTGRES_*`) stayed in `reports/*-substrate-findings.json` and `reports/*-defects-iterN.json` — both `tmpfs`-masked on the implementer container (verified in audit: `--tmpfs /project/.dark-factory/reports:ro,size=0`).
- **Substrate verification itself**: The gate CAUGHT the impl defect on iter 1 (and every iter after), producing actionable machine-readable findings. This is the mechanism working correctly.

## Where the run revealed a new gap (next follow-up)

**The gate-side substrate findings did not translate into impl-actionable feedback.** Across three iterations:

- Iter 1 feedback classified AC-7/8/12 as `SPEC_AMBIGUOUS` with the fix-list directive _"Re-read sections 8a and the substrate primitive definitions."_ The implementer re-read the spec, tweaked Fastify wiring and SQL interpolation in `server.ts` + `health.ts`, and **never opened `apps/api/src/db/`**.
- Iter 2 feedback escalated to _"ENV_ISSUE — Vitest module resolution failure; operator intervention required"_ after the implementer's wiring changes introduced a test-loader regression. The underlying substrate defects were unchanged.
- Iter 3 repeated the same scenario failures at the same pass rate; trajectory classifier correctly fired `PLATEAU` and the escalation agent took over.

**Root cause of the steering failure**: When a substrate-layer gate finding carries a specific impl-actionable code (`substrate-rls-table-missing`, `substrate-guc-unregistered`), the feedback composer currently collapses it into an AC-level re-read suggestion. The implementer has no mechanism to see _"your migration file doesn't CREATE TABLE for the `[[rls_table]]` primitive"_ — the closest guidance they receive is _"re-read the substrate primitives section"_, which is too abstract.

**Proposed follow-up (to file, barrier-safe)**: extend the feedback composer to recognize the substrate-findings finding codes on the pipeline-layer side (where `reports/` is readable) and emit a dedicated **impl-actionable spec-anchor bullet** for each. Example translation:

- `substrate-rls-table-missing` for table `X` → _"AC-N substrate primitive — migration DDL missing: the `[[rls_table]]` entry emitted for this AC has no corresponding `CREATE TABLE` in the migration."_
- `substrate-guc-unregistered` for name `Y` → _"AC-N substrate primitive — GUC registration missing: the `[[guc]]` entry emitted for this AC is not registered after setup."_

Vocabulary stays spec-anchor (AC-N, behavioral category). Internal codes stay in `reports/` (implementer-masked). No barrier violation. This is the gate-side parallel of Task 40's scenario-side classification hints.

## Notable events

- `COVERAGE_GAP attempt=1` → `COVERAGE_ARCHITECT_AMEND` → `COVERAGE_REGEN` → `COVERAGE_PASS`: coverage audit fired once on iter 1 and closed cleanly after an architect amend (adds 12+ new scenarios in `phase=coverage_regen`). Not a regression — this is the normal pipeline recovery path.
- `ITERATION_REGRESSION` fired on iter 2 (75 → 69). Confirmed the implementer's iter-2 changes broke adjacent scenarios.
- `IMPLEMENTER_TEMP_ZERO` on iter 2 and iter 3 (deterministic temperature for focused fix-up iterations).
- Iter 3 ran **three implementer attempts** within the iteration (attempt 1: 389s; attempt 2: 124s; attempt 3: 74s). Pipeline's per-iteration self-correct loop was exercised.
- `TRAJECTORY_CLASSIFIED iteration=3 trajectory=PLATEAU` is the trigger for auto-escalation.

## Server/service errors

None observed. `server_error=` empty across all iterations. Smoke passed each time.

## Barrier audit (spot-check)

Verified from `.dark-factory/audit/2026-03-28-database-foundation-d7b0c5b54d684523.log`:

- **spec-validator**: `-v .../budget-brain-v2:/project:ro`, `--tmpfs /project/apps/api/src:ro,size=0`, `--tmpfs /project/.git:ro,size=0`.
- **qa-holdout**: source masked (`apps/api/src`), scenarios masked (`.dark-factory/scenarios`), `.git` masked.
- **test-engineer**: source masked, holdouts masked, `.git` masked. `_contracts/` mounted read-only into scenarios subtree.
- **architect**: scenarios/audit/state/failures/.git all `tmpfs :ro,size=0`.
- **implementer**: holdouts/reports/audit/state/failures/.git all `tmpfs :ro,size=0`. Project mount `:rw` to allow source edits.
- **`BARRIER_PATH_ECHO_DETECTED` count: 0** across the entire run.

## Cost vs prior ESCALATED run

- Prior run (`4736bbd9`, 2026-04-21): ~75 min + "~180M cache-read tokens" per the pre-run notes.
- This run: ~58 min, `ITER_COST` events emitted on iter 1 and iter 3 (not iter 2 — classifier routed to ENV_ISSUE and skipped some accounting). Under the skill's 90-min hard cap.

Cheaper than the prior ESCALATED run despite doing the same full spec-validator/qa-holdout/test-engineer/coverage-regen/3-iter loop, because the fix pack avoided the prior run's multi-hour setup-hang burn.

## Bottom line

The six-task fix pack did what it was designed to do: **the failure class that ESCALATED the April-21 run no longer ESCALATES the same way.** Setup is stable, substrate verification surfaces the impl defect immediately and cleanly, barrier enforcement holds. The pipeline correctly detects plateau and escalates under budget.

What the fix pack did NOT do is **close the steering loop from substrate-layer gate findings to impl-actionable feedback**. That's a distinct, newly-identified gap — the scenario-side Task 40 hints catch scenario-file patterns; the gate-side parallel is still missing.

Follow-up to file: **"Substrate-findings → feedback translation pass"** (pipeline layer, barrier-safe).
