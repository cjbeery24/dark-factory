# Pipeline Run Report: database-foundation — P1+P1.5+P4 Validation Run 2/4

**Target:** budget-brain-v2
**Spec:** 2026-03-28-database-foundation
**Run ID:** `dbb06048f9bb4627`
**Date:** 2026-04-20 17:39 UTC → 18:42 UTC (≈63 min)
**Outcome:** ESCALATED at iter 3 (NO-SHIP after 3 iterations)
**Purpose:** Validation Run 2/4 for the implementer self-correction bundle (P1/P1.5/P4). Free run (no anchor flags).
**Validation criterion:** plateau broken in ≥ 3/4 AND zero self-test regressions.
**This run contributes:** ambiguous — plateau pattern disrupted by a new failure mode (setup hang), not by improved implementer behavior. Zero self-test regressions confirmed.

## Iteration summary

| Iter | Pass rate | Scenarios | Gate stop | Self-Tests | Classifier defects | Trajectory |
|------|-----------|-----------|-----------|------------|-------------------|------------|
| 1 | — | — | **Setup FAIL** | — | — | — |
| 2 | — | — | **Setup FAIL** | — | — | **PLATEAU** |
| 3 | 52% (27/51) | 51 | Blind FAIL | **PASS** | 19 IMPL_BUG | MIXED |

Iter 1 and 2 both crashed at the **setup** gate (Drizzle migrations hung on `applying migrations...`). No pass rate recorded for either. Iter 3 finally broke through setup — Self-Tests PASS, 51 scenarios executed, 27 passed, 24 failed.

## New architect manifest — I1 changed

**I1 ARCHITECT_MANIFEST_HASH:** `e9ee1601bbbd0335be95e63c13f79134f49f057a6d160129e986c48bfd8f1e98` — **different from the stable `713f8c1ae01e…` hash** observed in 5 prior runs at this spec (V1-R1/R2/R3, V2-R2/R3, and Run 1/4). `cache_hit=true` but against a new cache key. This breaks the I1 stability pattern — Run 2 is not directly comparable to prior runs at I1=`713f…`.

Material differences vs prior manifest:
- File map diverged on db module layout (`src/db/schema/index.ts`, `src/db/schema/helpers.ts`, `src/db/tenant-context.ts`, `src/db/health-check.ts` — prior runs had a flatter `src/db/` layout)
- Test integration entry points now include `tenantContext` (Fastify plugin) and `{tenantIdColumn, tenantIdNullableColumn, timestamps}` as named exports — prior runs named these differently

This is the 1st observation of I1=`e9ee1601…` and warrants monitoring — if subsequent runs stabilize on this hash, the "713f" era is over; if they revert, this is a one-off variance blip.

## Coverage phase

- **COVERAGE_GAP** at ~961s — all four FRs uncovered after initial TE pass
- **COVERAGE_REGEN** — second TE invocation added 4 scenario files (8 → 12 scenarios, 28 anchors)
- **COVERAGE_PASS** at ~1265s

## Iter 1 — Setup FAIL

- Smoke: PASS (advisory)
- Gate: Install PASS → Setup **FAIL**
- Setup log tail:
  ```
  No config path provided, using default 'drizzle.config.ts'
  Reading config file '/project/apps/api/drizzle.config.ts'
  Using 'postgres' driver for database querying
  [⣷] applying migrations...
  ```
  (hung on migrations, never completed)

## Iter 2 — Setup FAIL (repeat)

- Implementer invoked with `IMPLEMENTER_TEMP_ZERO` (P4 live)
- Smoke: PASS (fatal)
- Gate: same Setup hang, identical log output
- TRAJECTORY_CLASSIFIED: **PLATEAU** (failure signature unchanged)

## Iter 3 — broke through setup

- `IMPLEMENTER_TEMP_ZERO` (P4 live)
- Smoke: PASS (fatal)
- Gate: Install PASS → Setup PASS → Build PASS → Lint PASS → Type Check PASS → **Self-Tests PASS** → Blind Scenarios FAIL → Mutation SKIP
- Final pass rate: **52% (27/51 scenarios)**, satisfaction_score=52
- Classifier: 19 defects, all IMPL_BUG
- TRAJECTORY_CLASSIFIED: **MIXED**
- Oracle: **NO-SHIP**, escalation fired

## Failure cluster (iter 3)

24 failing scenarios clustered around FR-2 (tenant context / RLS), FR-3 (schema helpers), FR-1 (Drizzle client surface), and AC-2 (env validation). Representative:

- `[FR-2] withTenantDb sets app.tenant_id GUC`
- `[FR-2] SET LOCAL tenant_id within transaction scope`
- `[FR-2] transaction rollback also rolls back SET LOCAL`
- `[FR-3] schema helpers export reusable tenantId column`
- `[FR-3] establishes app.tenant_id policy patterns`
- `[FR-1] creates postgres.js client`
- `[FR-1] supports transactions via db.transaction`
- `[AC-2] server exits with code 1 when DATABASE_URL is missing`
- `[EC-2] health check returns error when database is unreachable`
- `[EC-5] transaction rollback also rolls back SET LOCAL`
- `[EC-10] concurrent requests maintain tenant isolation`

Substantial overlap with Run 1's FR-2/FR-3 GUC cluster, plus new FR-1 client-surface failures tied to the new manifest's module layout.

## P1/P1.5/P4 instrumentation — did the new code paths run?

- **`IMPLEMENTER_TEMP_ZERO`** emitted for iter 2 and iter 3. **P4 live.**
- **No `SELF_TEST_REGRESSION_REJECTED`** — correct. Iter 1 and 2 never reached Self-Tests (setup failed first), iter 3 reached Self-Tests and passed. No PASS→FAIL transition to catch. **P1.5 ready but unexercised.**
- **P1 prompt augmentation** — iter 2 and iter 3 implementer prompts carried cumulative history + diff + preamble (inferred from PROMPT_WORD_COUNT deltas; direct verification would require prompt capture).

## V-dimension instrumentation

- **I1:** `e9ee1601…` (new, cache_hit=true)
- **I2:** 51 scenario_fingerprints (12 scenario files, multiple tests each)
- **I3:** iter 3 → 19 IMPL_BUG defects; iter 1 and 2 had no classifier output (setup FAIL path)
- **I4:** emitted for all 3 iterations

## State file (final)

```
spec=2026-03-28-database-foundation
run_id=dbb06048f9bb4627
completed_step=2
iteration=3
smoke_result=passed
pass_rate=59
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

Note: state-file `pass_rate=59` is the satisfaction-weighted metric; raw scenario pass rate is **52% (27/51)** per `scenario-counts.env`.

## Notable events

- `ARCHITECT_MANIFEST_HASH` — new hash `e9ee1601…` (1st observation)
- `COVERAGE_GAP` + `COVERAGE_REGEN` + `COVERAGE_PASS` — normal regen flow
- `SMOKE_COMPLETED` iter 1/2/3 — all success
- Setup FAIL iter 1 and 2, Setup PASS iter 3
- `IMPLEMENTER_TEMP_ZERO` iter 2 and iter 3
- `TRAJECTORY_CLASSIFIED` iter 2 = PLATEAU, iter 3 = MIXED
- `SPEC_ESCALATED` at 3761s

## Comparison vs prior runs

| Dimension | Run 1/4 (I1=713f) | **Run 2/4 (I1=e9ee)** |
|-----------|-------------------|-----------------------|
| Scenarios (blind) | 34 | 51 |
| Iter 1 outcome | 60% pass | Setup FAIL |
| Iter 2 outcome | 64% PLATEAU | Setup FAIL PLATEAU |
| Iter 3 outcome | (escalated at iter 2) | 52% MIXED |
| Iter 1→2 delta | +4pp | N/A (both setup FAIL) |
| Iter 2→3 delta | N/A | setup FAIL → 52% (noisy) |
| Self-tests final | PASS | PASS (iter 3 only) |
| Total defects final | 12 | 19 |
| Wall time | 60m | 63m |
| Outcome | ESCALATED | ESCALATED |

## Validation counter

**2/4 complete.**
- Plateau breaks: **0 clean** (Run 1 plateau stood; Run 2 iter 2→3 is a failure-mode transition, not a plateau break on pass-rate delta)
- Self-test regressions: **0**

Required: ≥ 3 plateau breaks AND 0 regressions across 4 runs. With 2 runs done and 0 clean breaks, all 2 remaining runs must break plateau — margin is zero.

## Analysis — what this run revealed

1. **Architect variance matters more than implementer variance.** Run 2's new I1 hash (`e9ee1601…`) produced a materially different module layout and a substantially larger scenario set (51 vs 34). Downstream failure modes were different. The plateau hypothesis assumed I1 stability; with I1 varying, comparing Run 2 iter-deltas to Run 1's plateau-break criterion is apples-to-oranges.

2. **Setup hangs on iter 1/2 suppressed the signal we came to measure.** P1/P4 got two iterations of no useful training — the implementer couldn't learn from blind-scenario feedback because blind scenarios never ran. Only iter 3 produced a measurable pass rate, and by then the iteration budget was exhausted. The P1/P4 bundle's effect on the iter-1→iter-2 delta cannot be assessed from this run.

3. **The migration-hang failure is itself a systems problem** worth flagging separately — the implementer chose a migration approach that deadlocks in the gate's postgres service. Two full iterations were spent on a variant of this. This isn't a plateau; it's the implementer being unable to observe which step failed (no timeout on the migrate command, no structured error) and therefore unable to converge.

4. **P1.5 self-test regression gate remains unexercised** across 2 validation runs (Run 1: PASS→PASS; Run 2: no Self-Tests reached iter 1/2, PASS iter 3). The gate is implemented correctly — it simply hasn't encountered its target condition.

## Follow-ups surfaced this run

1. **Migration-hang detection.** The `drizzle-kit migrate` command hangs with no timeout when it fails. Quality gate should wrap Setup in a timeout and emit a structured failure reason. Worth a separate spec.
2. **I1 stability monitoring.** The "713f" era may be ending. Suggest adding a pipeline-level flag when a new I1 is observed, prompting the operator to decide whether to continue collecting data at the new hash or pin the old one via `--anchor-manifest`.
3. **Validation-criterion reconsideration.** With 2/4 runs collected and the run-to-run architect variance exposed, the "≥ 3/4 plateau breaks" criterion may be unachievable not because the bundle doesn't work but because cross-I1 comparison isn't meaningful. Options: (a) re-run the validation anchored to a single I1 (requires `--anchor-manifest`), (b) widen the criterion to accept "either plateau break OR measurable iter-over-iter progress at Self-Tests level", (c) pause for feedback-sanitization relaxation work (see separate proposal in session notes) and re-run afterward.

## Next

**PAUSED** per user request. No Run 3 launched until the evaluation conversation concludes.
