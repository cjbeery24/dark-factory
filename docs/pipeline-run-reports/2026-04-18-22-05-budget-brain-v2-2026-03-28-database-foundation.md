# Pipeline Run Report: database-foundation — Phase 6 V1 Run 2/3

**Target:** budget-brain-v2
**Spec:** 2026-03-28-database-foundation
**Run ID:** `c8f494d618704097`
**Date:** 2026-04-18 22:05 UTC → 23:09 UTC (≈64 min)
**Outcome:** SHIPPED (Oracle SHIP, satisfaction=90)
**Purpose:** Phase 6 V1 free-run baseline, Run 2 of 3. No anchor flags. Artifacts preserved via archive mechanism (commit 51d11b9).

## Iteration summary

| Iter | Pass rate | Satisfaction | Scenarios | Crashed | Trajectory | Cost (est USD) |
|------|-----------|--------------|-----------|---------|------------|----------------|
| 1    | 0%        | 0%           | 0         | 0       | —          | $84            |
| 2    | 92%       | 90%          | 41        | 0       | IMPROVING  | $42            |

**Total cost:** ~$126. **Wall time:** 3846s (64.1 min). **Total turns:** 600.

## State file

```
spec=2026-03-28-database-foundation
run_id=c8f494d618704097
spec_hash=1d097e0e97f78a8c99b4a4a8eab5358f811cb348336b7dfce83a19bb35b65662
last_decision=NO-SHIP
iteration=2
smoke_result=passed
phase=oracle_complete
pass_rate=92
gate_exit_code=1
status=shipped
completed_step=99
```

(Note: `last_decision=NO-SHIP` reflects the mid-iter-2 reviewer/pre-oracle state; final Oracle emitted SHIP and `status=shipped`.)

## V-dimension instrumentation (I1/I2/I3/I4)

- **I1 architect_manifest_hash:** `2986fed1c571105ce1b50f3ac4d63512041cd3513177e4482447e67b87b69423`
  - Differs from Run 1 (`58fb4bbebd3510…`) — confirms architect non-determinism across identical starting conditions.
- **I2 scenario_fingerprint count:** 13 fingerprints emitted across initial + regen.
- **I3 classifier_io_hash (iter 2):** input `7409ed0bf84ad646…`, output `d9d3767ceb71d7a6…`, defects=3, failing=3.
- **I4 iteration_temporal_signature:**
  - Iter 0→1: current hash == prior hash `4f53cda18c2b…` (no-scenario baseline; vitest registered 0 tests in iter 1 — see §iter-1 note).
  - Iter 1→2: `tests_moved_to_pass=0`, `tests_moved_to_fail=3`, `unchanged=0`, delta `{BRITTLE: +2, IMPL_BUG: +1}` (41 scenarios materialized; 37 pass, 3 classified fails + 1 substrate fail).

## Iter 1 note — scenarios failed to register

`GATE_COMPLETED iter=1` reports `scenario_total=0` with `exit_code=2` (vitest "no tests found"). Install/Setup/Build/Lint/Type Check/Self-Tests all PASS; blind scenarios did not execute. Implementer attempt 1 and attempt 2 both produced `FILES_CHANGED count=9` but the test runner could not discover the scenario test files — likely a mount / tsconfig / resolver mismatch (vitest.config.ts path or tsconfig extends chain). Iter 2 implementer corrected whatever blocked discovery, and 41 scenarios registered cleanly.

This is a materially different failure shape from Run 1 (which had iter 1 partial-pass at 44%).

## Stable failures (iter 2, final)

Oracle classified these as acceptable for SHIP under ship-with-gaps (IMPROVING trajectory, 92% ≥ threshold):

1. `Edge Cases > @covers EC-4 > [EC-4] unset app.tenant_id GUC returns nullish and RLS fails closed`
   — **IMPL_BUG.** Classifier rationale: "implementation fails stated predicate that unset app.tenant_id GUC returns empty string and NULLIF conversion yields SQL NULL."
2. `Edge Cases > @covers EC-6 > [EC-6] connection pool handles resource exhaustion gracefully`
   — **BRITTLE.** Classifier rationale: "Test runs only 5 concurrent queries against default pool of 20 connections, not testing actual resource exhaustion as specified in EC-6."
3. `Postgres Substrate Layer > Transaction Isolation > [AC-10] concurrent transactions maintain separate tenant contexts`
   — **BRITTLE.** Classifier rationale: "uses raw postgres.js client.begin instead of spec-required withTenantDb/request.withDb infrastructure."
4. `Postgres Substrate Layer > app.tenant_id GUC can be set and read correctly` — **unclassified** (not in defects-iter2.json). Appears to be a substrate-level assertion adjacent to the same EC-4 GUC behavior; the classifier returned only 3 defects for 4 failing tests.

`CAT_00_PASSED=37 / CAT_00_TOTAL=41` → SATISFACTION_SCORE=90.

## Coverage amend flow (§4 insights)

Single COVERAGE cycle ran:

- **COVERAGE_GAP (attempt 1):** 16 uncovered anchors — `FR-1, FR-2, FR-3, FR-4, AC-7, AC-8, EC-1..EC-10` (minus EC-5/EC-8 differences). This large gap on initial TE output.
- **COVERAGE_REGEN (attempt 1):** TE rerun fired.
- **COVERAGE_PASS:** `anchor_count=28` after regen.

No `COVERAGE_ARCHITECT_AMEND` was needed (unlike Run 1, which fired one architect amend for the two missing entry points). The architect's initial entry_points in this run already included `apps/api/src/db/index.ts`, `apps/api/src/db/schema/helpers.ts`, and `apps/api/src/db/tenant-context.ts` — avoiding the route that Run 1 had to repair.

## Architect manifest validation retries (G1)

One retry fired during architect:

- **Attempt 2 failure:** `MANIFEST_VALIDATION_RETRY` — `Environment contract fold failed (parent_hash mismatch): 0003-2026-03-28-api-bootstrap.toml: parent_hash mismatch (delta says 527b1fe9b8fa, view is 7e12c220034d). This indicates a delta-log corruption — manual escalation required.`
- Architect retried and succeeded; `ARCHITECT_MANIFEST_HASH` emitted at 22:29:23Z.

Note: the parent_hash mismatch was a transient effect of an earlier aborted-run setup that was recovered by restoring the delta chain from HEAD before this run started. The architect's retry self-healed on attempt 3.

G1 retry budget consumed 1 of 2.

## Quality gate summary (iter 2, final)

```
Install: PASS | Setup: PASS | Build: PASS | Lint: PASS | Type Check: PASS
Self-Tests: PASS | Blind Scenarios: FAIL | Mutation Testing: SKIP

Tests: 4 failed | 37 passed | 0 skipped (41 total)
Failures: EC-4 (IMPL_BUG), EC-6 (BRITTLE), AC-10 (BRITTLE), substrate GUC (unclassified).
```

Substrate findings: `{"status": "skip", "findings": []}` (substrate verifier skipped — scheduled for future phase).

## Notable events

- **`MANIFEST_VALIDATION_RETRY`** ×1 during architect phase (described above).
- **`COVERAGE_REGEN`** ×1 (attempt 1), `COVERAGE_PASS` after regen (no architect amend needed).
- **Pre-gate retry (iter 1):** implementer attempt 2 ran (attempt 1 had output that tripped pre-gate — both attempts produced 9 files; R3 budget consumed 1 of 3).
- **`ORACLE_METRIC_DELTA`** delta=0 on both iterations (Oracle's claimed pass rate matched gate report exactly).
- **`SMOKE_COMPLETED`** success=true both iterations (iter 1 advisory, iter 2 fatal policy).
- **`TRAJECTORY_CLASSIFIED`** iter 2: IMPROVING ("Continue iterating — positive trajectory").
- **`SPEC_SHIPPED`** iterations=2, satisfaction=90.

No `BARRIER_VIOLATION` events this run.

## Observations vs. Run 1 (same V1 batch)

| Dimension | Run 1 | Run 2 |
|-----------|-------|-------|
| Outcome | ESCALATED @ iter 3 | SHIPPED @ iter 2 |
| Iter 1 pass rate | 44% | 0% |
| Peak pass rate | 80% | 92% |
| Iter count | 3 | 2 |
| Cost | $210 | $126 |
| Wall time | 73.8 min | 64.1 min |
| I1 manifest hash | `58fb4bbebd35…` | `2986fed1c571…` (different) |
| Coverage amend fired | yes (architect-entry-point ×2) | no (entry points correct first time) |
| G1 retries consumed | 2/3 | 1/2 |
| Stable failures at end | 2× ENV_ISSUE (drizzle-kit not found) | 3× {IMPL_BUG, BRITTLE×2} + 1 substrate |

**Key insight — entirely different failure modes on identical starting conditions:**
- Run 1 stalled on environment contract (drizzle-kit not discoverable in gate container) and plateaued at 80%.
- Run 2 had the same environment baseline but architect produced a different manifest that avoided the env-contract failure path; iter 1 hit a different discovery failure (vitest saw 0 tests); iter 2 resolved it cleanly and shipped at 92%.

This is strong V-dimension signal: **the architect's non-determinism alone (different I1 hash) produces materially different downstream trajectories**, even before considering TE/implementer variance.

## Variance sources (for V1 n=3 analysis)

Run 2 artifacts will be preserved under `.dark-factory/archived-runs/c8f494d618704097/` before Run 3 begins. Compare against Run 1 (`65f81a73cf2646ed`) and Run 3 (TBD):

- `architecture/<spec>-manifest.yaml` — architect output (I1 hash)
- `scenarios/<spec>/` — TE output (I2 fingerprints)
- `audit/gate-telemetry.jsonl` — classifier IO (I3 hashes)
- `reports/<spec>-defects-iter{N}.json` — defect categorization
- `state/`, `failures/`, `events.jsonl` — full timeline

## Follow-ups

1. **Iter 1 "vitest found 0 tests" regression** — 0-scenario-registration in iter 1 is a distinct failure class we haven't seen before. Worth adding a pre-gate check: "if `scenario_total < expected_min`, mark setup as failed rather than reporting a 0% pass rate." The current flow attributes this as a 0% pass rate, which misleads the Oracle's trajectory calculation in the iter 1→iter 2 step.
2. **Architect non-determinism is the dominant V-source so far** — Run 1 and Run 2 diverged primarily because the architect produced materially different manifests. This is the exact signal Phase 6 V2 (anchor-manifest) is designed to collapse; keep an eye on how much of this variance disappears when the manifest hash is locked.
3. **Unclassified 4th failure (substrate GUC)** — defect classifier returned 3 defects for 4 failing tests. Either the classifier silently dropped one, or substrate-layer tests are outside its scope. Worth verifying classifier input set.
4. **Coverage amend cost** — Run 1 needed architect amend; Run 2 did not. Whether this varies across V1 n=3 or is deterministic given the manifest shape is an open question for Run 3.

## Next

Run 3 of V1 baseline (this spec, same config, preserve Run 1 and Run 2 archives).
