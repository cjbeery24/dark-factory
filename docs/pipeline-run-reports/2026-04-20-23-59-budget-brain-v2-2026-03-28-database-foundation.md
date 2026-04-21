# Pipeline Run Report: 2026-03-28-database-foundation (run 3b1d2bc7)

**Target project:** budget-brain-v2
**Spec:** 2026-03-28-database-foundation
**Run ID:** `3b1d2bc73f5f41c6`
**Started:** 2026-04-20 23:16:31
**Terminal event:** `SPEC_ESCALATED` at 2382s (~40 min)

## Outcome: ESCALATED

Pipeline ran 2 full iterations. Both hit `gate_exit_code=1` with `pass_rate=72`, trajectory classified as `PLATEAU` at iter-2. Escalation agent fired at iter-3 (no third implementer pass) and the run terminated cleanly through the escalation path.

## Iterations

| Iter | Implementer | Smoke | Gate pass_rate | Defects | Oracle |
|-----:|------------:|------:|---------------:|--------:|-------:|
| 1 | OK (attempt 1: 393s; attempt 2 self-correct: 139s) | PASS | 72 (exit 1) | 9 | NO-SHIP |
| 2 | OK (173s) | PASS | 72 (exit 1) | 9 | NO-SHIP, trajectory=PLATEAU |
| 3 | — | — | — | — | ESCALATION (75s) |

## State file excerpt

```
spec=2026-03-28-database-foundation
run_id=3b1d2bc73f5f41c6
completed_step=2
iteration=2
server_error=
smoke_result=passed
pass_rate=72
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

## Quality gate summary (iter-2, final)

```
# Quality Gate Summary: 2026-03-28-database-foundation
Date: 2026-04-21T06:48:15Z

| Gate | Status |
|------|--------|
| Install | PASS |
| Setup | PASS |
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Substrate Verification | SKIP |
| Import Contract | FAIL |
| Self-Tests | PASS |
| Scenario Lint | PASS |
| Blind Scenarios (executable) | FAIL |
| Mutation Testing | SKIP |

## Result: FAIL
```

Notably: **Setup passed** this run. The drizzle-kit migrate hang that killed iter-1/2 of the prior 1230234d run did not reproduce. Same spec, same manifest contract, but the implementer produced different migration SQL that didn't deadlock.

## Scenario counters (iter-2)

```
SCENARIO_TOTAL=34      SCENARIO_PASSED=23       SCENARIO_FAILED=11
SCENARIO_CRASHED=0     SCENARIO_ASSERT_FAILED=11
SCENARIO_RATE=67       SATISFACTION_SCORE=67
```

## Substrate findings

```
{"status": "skip", "findings": []}
```

Substrate verification skipped this run — the architect did not require a substrate manifest preflight. (Contrast: prior runs invoked substrate.)

## Import Contract failure (verbatim gate excerpt)

```
| Import Contract | FAIL | 3 entry point(s) — one or more failed to resolve |

cmd: pnpm exec vitest run --config .df-vitest-resolution-check.config.ts \
     ../../.dark-factory/scratch/resolution-check/.../contract-00.test.ts \
     contract-01.test.ts contract-02.test.ts
cwd: apps/api
exit: 1

failed to load config from /project/apps/api/.df-vitest-resolution-check.config.ts

Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'vitest.config.ts'
  imported from /project/apps/api/.df-vitest-resolution-check.config.ts...
```

The generated resolution-check config referenced `vitest.config.ts` as an import specifier (not a relative path), which Node's ESM resolver treated as a package name. **This is a real pipeline-owned bug in the Import Contract phase's temp config generation** — see "New issue surfaced" below.

## Blind scenario failures (sample — 11 total)

```
FAIL  .../scenarios/2026-03-28-database-foundation/tenant-context.test.ts
  > Tenant Context Error Handling [FR-2]
  > [EC-3] GIVEN request.tenantId is undefined
    when request.withDb is called
    THEN AppError thrown with code TENANT_REQUIRED, status 401
AssertionError: expected 'Authentication required' to match /tenant.*required/i
- Expected: /tenant.*required/i
+ Received: "Authentication required"
```

Classification across both iterations (per defects-iter{1,2}.json, 9 defects each):
- FR-2, AC-5, AC-10, EC-3, EC-5, EC-10 — tenant context middleware and GUC isolation (IMPL_BUG, Critical)
- FR-1, AC-11, EC-1, EC-9 — database configuration and background jobs (IMPL_BUG, High)
- EC-6 — connection pool management (IMPL_BUG, Medium)

Stable failure set across iter-1 and iter-2 → PLATEAU trajectory → Oracle did not promote to SHIP_WITH_GAPS.

## Barrier mask spot-check (audit log)

Analysis agents (iter-1 launch timestamps):

```
role=qa-holdout       tmpfs /project/apps/api/src:ro,size=0
                      tmpfs /project/.dark-factory/scenarios:ro,size=0
                      tmpfs /project/.git:ro,size=0

role=test-engineer    tmpfs /project/apps/api/src:ro,size=0
                      tmpfs /project/.dark-factory/holdouts:ro,size=0
                      tmpfs /project/.git:ro,size=0

role=implementer      tmpfs /project/.dark-factory/holdouts:ro,size=0
                      tmpfs /project/.dark-factory/reports:ro,size=0
                      tmpfs /project/.dark-factory/audit:ro,size=0
                      tmpfs /project/.dark-factory/state:ro,size=0
                      tmpfs /project/.dark-factory/failures:ro,size=0
                      tmpfs /project/.git:ro,size=0
```

Barriers correctly applied: qa-holdout can't see scenarios, test-engineer can't see holdouts, implementer can't see holdouts/reports/audit/state/failures, `.git` masked on all. No `BARRIER_PATH_ECHO_DETECTED` events emitted.

## Change Validation

### 1. Import Contract gate phase (`scripts/lib/render_import_contract_phase.py`)

**Fired: YES.** Appears in both iter-1 and iter-2 gate reports at `| Import Contract | FAIL | 3 entry point(s) — one or more failed to resolve |`. Iter-1 diagnostic caught test imports of non-existent functions (`getTenantContext`, `setTenantContext`, `withTenantContext`, `checkDatabaseHealth`) — a legitimate import-contract violation where tests referenced the wrong module exports. Iter-2 surfaced a different failure mode (see "New issue" below).

**Behaved correctly for the iter-1 case:** the phase executed, emitted a clear diagnostic, and the iter-1 feedback picked it up:

> Import Contract: FAIL - Test imports non-existent functions (getTenantContext, setTenantContext, withTenantContext from tenant-context module; checkDatabaseHealth from health-check module). Actual exports are tenantContext plugin and registerDatabaseHealthCheck function.

**Iter-2 revealed a pipeline-side bug in the phase's own test config generation** — see "New issue surfaced" below.

### 2. Manifest contract context in Oracle feedback (`/df-write-feedback` Step 3.5)

**Fired: PARTIAL.** Iter-1 feedback items do not contain structured specifier/named-exports/module-system bullets. Iter-2 feedback, however, does include manifest-flavored prose in the Priority-Ordered Fix List:

> 3. **Import Contracts** — Export the `DrizzleTx` type from `db/index.ts` as required by the interface contract (section 5). Update test files to import actual exports: `tenantContext` plugin instead of individual functions, and `registerDatabaseHealthCheck` instead of `checkDatabaseHealth`.

"required by the interface contract (section 5)" + specific module path (`db/index.ts`) + specific named exports (`tenantContext`, `registerDatabaseHealthCheck`, `DrizzleTx`) match the manifest-contract injection's intent. Iter-2 also added a new "Prior-Iteration Blocker Resolution" table absent from iter-1 — a distinct structured section that did not exist in iter-1. This indicates the Step 3.5 injection path is running, but in prose form rather than as bullet-structured metadata.

### 3. MEDIUM precision auto-hint (`darkfactory/architecture/precision_hint.py`)

**Fired: NO (correctly).** Spec validation returned `divergence=LOW` (verified at `.dark-factory/reviews/2026-03-28-database-foundation-validation.status`):

```
verdict=PASS
score=95
divergence=LOW
blockers=0
```

No `2026-03-28-database-foundation-precision-hint.md` exists under `.dark-factory/architecture/`. The LOW-divergence skip is correct behavior — hint only writes on MEDIUM.

### 4. Per-run artifact archive (`darkfactory/pipeline/archive.py`)

**Fired: YES.** Archive directory created at:

```
.dark-factory/archive/20260420-231631-2026-03-28-database-foundation-3b1d2bc7/
├── final/
│   ├── events.jsonl
│   ├── meta.json
│   └── metrics.jsonl
├── iter-1/ (architecture, audit, precheck, reports, reviews, state)
├── iter-3/ (architecture, audit, precheck, reports, reviews, state)
└── run.json
```

`final/meta.json`:

```json
{
  "spec": "2026-03-28-database-foundation",
  "run_id": "3b1d2bc73f5f41c6",
  "status": "escalated",
  "final_iteration": 3,
  "completed_at": "2026-04-20 23:56:24"
}
```

**Noteworthy:** no `iter-2/` directory. The archive shows `iter-1/` and `iter-3/` only. The escalation iteration (iter=3 in events) appears to have triggered a snapshot that carries iter-2's artifacts under the `iter-3/` name. This is a latent bug in `snapshot_iteration`'s naming convention — the archived state/reviews inside `iter-3/` reference `iteration=2` internally. Post-mortem inspection is still possible, but the directory names lie about iteration boundaries.

## Phase D Validation — Non-Negotiable AC Contract

### Spec declaration

Spec has `## 8a. Non-Negotiable Acceptance Criteria` section with 6 blocking ACs.

### Precheck parsing (fix #3 validation)

`cat .dark-factory/precheck/2026-03-28-database-foundation.json`:

```
top-level keys: ['ambiguities', 'anchor_intent_issues', 'blocking_ac_issues',
                 'blocking_acs', 'contracts', 'criteria_issues', 'fatal',
                 'fr_count', 'placeholders', 'precision', 'scenario_intents',
                 'service_advisories', 'shape_claims', 'structure', 'summary']

blocking_acs: {'AC-6': True, 'AC-7': True, 'AC-10': True,
               'AC-12': True, 'AC-13': True, 'AC-14': True}

blocking_ac_issues: []
```

**Fix #3 VALIDATED.** On the 1230234d run (pre-fix), `blocking_acs` was absent from top-level keys entirely. This run has all 6 declared blocking ACs populated with `True` and an empty issues list (no unresolved IDs, no empty-without-justification). Serialization works.

### should_ship_with_gaps veto

**Not exercised.** Oracle never proposed SHIP_WITH_GAPS this run — iter-2's 72% pass rate is below the typical gaps threshold, and PLATEAU trajectory signals no progress. The veto path (`spec_run.ac_blocking` vs. failing AC set) did not get a chance to fire.

Failing ACs at iter-2 include AC-10 (tenant context) which *is* in the blocking set. If Oracle had proposed gaps-promotion, fix ed85a07 would have vetoed. Not a regression, just not covered by this run's terminal path.

## Fix #2 Validation — Abort → archive routing

**Not exercised.** The terminal path here was escalation (orchestrator routes through a different handler that already calls `snapshot_final`), not classifier abort. The defect classifier ran cleanly at iter-1 (9 defects, 106s) and iter-2 (9 defects, 103s) with no count_mismatch, bad_schema_version, or crash_split_mismatch. The `HardAbort` raise at `iteration.py:3580` did not fire.

**No regression either:** `final/meta.json` was written correctly with `status=escalated`, which is the expected behavior for the escalation terminal path (this path was already working pre-fix).

To exercise fix #2 we'd need the classifier to fail count_mismatch on a future run — still possible but didn't reproduce here.

## New issue surfaced: Import Contract phase generates invalid vitest config

The iter-2 `Import Contract: FAIL` diagnostic reveals a pipeline-side bug in the gate phase itself (not an implementation contract violation):

```
failed to load config from /project/apps/api/.df-vitest-resolution-check.config.ts

Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'vitest.config.ts'
  imported from /project/apps/api/.df-vitest-resolution-check.config.ts...
```

The auto-generated `.df-vitest-resolution-check.config.ts` apparently imports `vitest.config.ts` as a bare specifier (Node treats it as a package name), rather than as a relative path (`./vitest.config.ts` or `./vitest.config`). The result: the Import Contract gate phase fails to even start vitest, flagging 3 entry points as "failed to resolve" when the real cause is the gate's own config-templating bug.

**Impact:** Import Contract phase will always FAIL for projects that don't have a package named `vitest.config.ts` in their dependency tree — i.e., every realistic project. Iter-2's fix list correctly identifies some real type-export issues, but the gate verdict is polluted by the resolution-check tooling failure.

**Fix direction:** locate the generator for `.df-vitest-resolution-check.config.ts` (likely `scripts/lib/render_import_contract_phase.py` or a helper it calls) and ensure the vitest config import uses a relative path or a runtime `defineConfig()` inline.

## Comparison to prior runs

| Run | Terminal | Iters | Pass rate | Notable |
|-----|----------|------:|----------:|---------|
| d8268a4a (13:07) | SHIP | 2 | 80 | Original success |
| dc53cf0d (17:22) | SHIP_WITH_GAPS | 2 | 82 | Reproducibility |
| 1230234d (21:35) | ABORT (classifier) | 3 | — | Setup hang iter-1/2, count_mismatch iter-3 |
| **3b1d2bc7 (23:16)** | **ESCALATED** | **2** | **72** | **Setup passed, Import Contract bug** |

This run is the *first* since the Phase D changes where the classifier completed cleanly and Setup didn't hang. The escalation outcome reflects a real implementation regression (tenant context, health check wiring) compounded by the Import Contract phase's own generated-config bug inflating the failure count.

## Prioritized follow-ups

1. **Import Contract phase config generator** (new, surfaced this run). Fix the vitest config template to use a relative specifier. Without this, every project with a vitest setup will FAIL Import Contract regardless of actual contract compliance — false-FAIL that inflates iteration count.
2. **Archive iter-3 vs iter-2 directory naming** (minor). `snapshot_iteration` labels the escalation-triggered snapshot as `iter-3/` when it contains `iteration=2` state. Post-mortem-only noise; investigate whether the snapshot-key should use the *completed* iteration number, not the active-at-snapshot-time one.
3. **Fix #2 coverage** (deferred). The HardAbort routing change is unexercised by this run. No synthetic way to force it without triggering a classifier failure — will validate on a future run that hits count_mismatch organically.
4. **Classifier defect-count arithmetic** (still-open bug #1). Both iters emitted `defects=9` against `SCENARIO_FAILED=11`. Expected `expected_total` check at `iteration.py:3399` should have tripped `count_mismatch` — it didn't. Either the classifier's retry logic succeeded on a second attempt (no retry event visible), or the count-mismatch check is stricter than what actually happens. Worth a deliberate grep.

## Run artifacts index

- State: `.dark-factory/state/2026-03-28-database-foundation.state`
- Gate summary: `.dark-factory/reports/2026-03-28-database-foundation-quality-gate-summary.md`
- Gate full: `.dark-factory/reports/2026-03-28-database-foundation-quality-gate.md`
- Precheck: `.dark-factory/precheck/2026-03-28-database-foundation.json`
- Feedback: `.dark-factory/reviews/2026-03-28-database-foundation-feedback-iter{1,2}.md`
- Escalation: `.dark-factory/reviews/2026-03-28-database-foundation-escalation.md`
- Archive: `.dark-factory/archive/20260420-231631-2026-03-28-database-foundation-3b1d2bc7/`
- Audit: `.dark-factory/audit/2026-03-28-database-foundation-3b1d2bc73f5f41c6.log`
