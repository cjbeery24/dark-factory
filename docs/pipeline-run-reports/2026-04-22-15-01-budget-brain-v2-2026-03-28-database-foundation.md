# Pipeline Run — 2026-03-28-database-foundation

- **Target project:** `../budget-brain-v2`
- **Run ID:** `e2d9548b3ad24590`
- **Spec hash:** `bead33ac4cf22c91351b5a1dc499cd60351b0fa448e40925aebe64b830473740`
- **Started:** 2026-04-22 ~21:04 UTC
- **Terminal event:** `SPEC_ESCALATED`
- **Wall time:** 3364s (~56 min)
- **Iterations ran:** 2 implementer iterations + 1 escalation (iter 3)
- **Outcome:** ESCALATED — pipeline surrendered after classifying the dominant failure mode as `SPEC_AMBIGUOUS`

## Outcome summary

Iter 1 gate: `pass_rate=0`, `exit_code=2`. Iter 2 gate: `pass_rate=55`, `exit_code=1`. Trajectory
was `IMPROVING`, but the defect classifier tagged 13 of 14 failing AC/EC anchors as
`SPEC_AMBIGUOUS` (all clustered around RLS policy enforcement / GUC semantics), which the
Oracle took as grounds to recommend escalation rather than a third attempt. A dedicated
`escalation` agent fired at iter 3 and produced a human-facing escalation report.

## State file

`$TARGET/.dark-factory/state/2026-03-28-database-foundation.state`:

```
spec=2026-03-28-database-foundation
run_id=e2d9548b3ad24590
spec_hash=bead33ac4cf22c91351b5a1dc499cd60351b0fa448e40925aebe64b830473740
completed_step=2
iteration=2
server_error=
smoke_result=passed
pass_rate=55
gate_exit_code=1
last_decision=NO-SHIP
phase=oracle_complete
status=escalated
```

## Phase timeline (from events.jsonl)

| t (s) | Event | Notes |
|---:|---|---|
| 0 | SPEC_START / RUN_START_CLEANUP / SPEC_ADAPTER_ACTIVATED | — |
| 141 | spec-validator complete | 154s wall, PASS |
| 396 | architect complete | 255s wall, 7-path manifest |
| 707 | qa-holdout generate complete | 311s wall, contracts staged |
| 1012 | test-engineer generate complete | 304s wall, 7 scenarios |
| 1518 | test-engineer validate complete | 505s wall, 6 scenarios after drop |
| 1518 | **COVERAGE_GAP** (attempt 1) | triggers coverage_regen |
| 1735 | test-engineer coverage_regen complete | 217s wall, 10 scenarios, COVERAGE_PASS |
| 1735 | **BARRIER_VIOLATION role=test-engineer path=apps/api/src** | output path-string detection; Docker mask blocked the read |
| 2399 | iter 1 implementer complete | 663s wall |
| 2414 | SMOKE_COMPLETED iter 1 | passed |
| 2418 | GATE_COMPLETED iter 1 | pass_rate=0, exit_code=2 |
| 2502 | iter 1 reviewer complete | 84s wall |
| 2565 | iter 1 oracle pass 1 complete | 63s wall — NO-SHIP |
| 2624 | iter 1 oracle pass 2 complete | 59s wall — FEEDBACK_WORD_COUNT emitted |
| 2866 | iter 2 implementer complete | 242s wall (`IMPLEMENTER_TEMP_ZERO`) |
| 2881 | SMOKE_COMPLETED iter 2 | passed |
| 2890 | GATE_COMPLETED iter 2 | pass_rate=55, exit_code=1, trajectory=IMPROVING |
| 2974 | defect-classifier complete | 84s wall |
| 3127 | iter 2 reviewer complete | 153s wall |
| 3218 | iter 2 oracle pass 1 complete | 90s wall |
| 3276 | iter 2 oracle pass 2 complete | 58s wall — FEEDBACK_WORD_COUNT emitted |
| 3276 | escalation agent start | iter 3 |
| 3364 | escalation complete → **SPEC_ESCALATED** | 88s wall |

## Quality gate summary (iter 2, final)

`$TARGET/.dark-factory/reports/2026-03-28-database-foundation-quality-gate-summary.md`:

```
# Quality Gate Summary: 2026-03-28-database-foundation
Date: 2026-04-22T21:52:30Z

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

Scenario counts (iter 2):

```
SATISFACTION_SCORE=53
SCENARIO_PASSED=21
SCENARIO_FAILED=17
SCENARIO_CRASHED=1
SCENARIO_TOTAL=38
SCENARIO_RATE=55
```

Review pre-pass scores (iter 2):

```
TEST_ANALYSIS_SCORE=15
CODE_QUALITY_SCORE=18
SECURITY_SCORE=15
DETERMINISTIC_TOTAL=48
FAILURE_PATTERN=isolated
```

## Substrate verification

`$TARGET/.dark-factory/reports/2026-03-28-database-foundation-substrate-findings.json`:

```json
{"status": "skip", "findings": []}
```

Substrate was marked SKIP. Gate table shows **Substrate Verification: SKIP** and
**Import Contract: FAIL** — both pointed at by the iter 2 feedback as root-cause symptoms.

## Iter 1 blocker (resolved in iter 2)

From `feedback-iter1.md`: setup gate got stuck with `drizzle-kit migrate` hanging at
"applying migrations...", preventing any behavioral testing. Iter 1 ended at pass_rate=0.

The `smoke-test-summary.md` file still records an earlier install failure:

```
ERR_PNPM_OUTDATED_LOCKFILE  Cannot install with "frozen-lockfile" because pnpm-lock.yaml
is not up to date with <ROOT>/apps/api/package.json
```

This reflects a prior attempt within iter 1 — by iter 2 the gate table shows Install PASS.

## Iter 2 defect classification (why escalation fired)

All 14 failing AC/EC anchors carry the same `internal_reason_code`:
`POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION`. Categorization (from `defects-iter2.json`):

| Anchor | Category | Area |
|---|---|---|
| AC-1, AC-7, AC-8, AC-10, AC-12 | SPEC_AMBIGUOUS | GUC availability + strict/nullable RLS policies |
| AC-5, AC-11, EC-5, EC-10 | SPEC_AMBIGUOUS | tenant-context middleware `SET LOCAL` semantics |
| AC-6, EC-4 | SPEC_AMBIGUOUS | error handling + NULLIF GUC conversion |
| AC-4, EC-9 | IMPL_BUG | migration command handling |
| FR-2 | SCENARIO_CRASH | RLS infrastructure |

The cluster of SPEC_AMBIGUOUS classifications around RLS/GUC was the trigger for
escalation. Oracle iter 2 output (verbatim from `oracle-output-iter2.md`):

> Most failures are classified as SPEC_AMBIGUOUS, indicating the specification may need
> clarification on RLS policy enforcement details and tenant isolation guarantees.

## Reviewer verdict (iter 2)

Score: **67/100 — REQUEST CHANGES**

```
| Dimension       | Score | Notes                                                 |
| Spec Compliance | 18/30 | Core infrastructure complete but RLS enforcement failing |
| Code Quality    | 18/20 | Clean code with minor long function issues           |
| Test Results    | 15/25 | 55% pass rate with critical security failures       |
| Security        |  9/15 | RLS policies not enforcing tenant isolation correctly|
| Robustness      |  7/10 | Good error handling but substrate issues affect reliability |
```

Reviewer's critical issue (verbatim):
> **apps/api/src/db/migrations/0000_baseline_rls_infrastructure.sql:1** — RLS policies
> not actually applied to test tables — Critical security violation: cross-tenant data
> leakage occurring — The baseline migration only documents patterns but tests indicate
> RLS is not enforcing tenant isolation. This violates AC-7 and AC-10 (non-negotiable
> criteria).

## Escalation report (iter 3)

From `escalation.md` — notable because it references **17 prior pipeline runs** (i.e. the
agent walked the 12 archived runs under `.dark-factory/archive/` plus prior history and
built a multi-run narrative):

> The 2026-03-28-database-foundation project has failed to ship after 17 separate
> pipeline runs spanning from 2026-03-28 to 2026-04-22. Despite a well-validated
> specification (95/100), every implementation attempt has failed quality gates due to
> critical security vulnerabilities around Row Level Security (RLS) policy enforcement.

The escalation agent's recommendation was: halt the loop, get an RLS specialist, consider
a spec revision rather than another impl attempt.

## Notable events

- **`BARRIER_VIOLATION role=test-engineer path=apps/api/src` (t=1735s)** — fired at the
  end of `coverage_regen`. The event payload names `apps/api/src`, which means the
  detector saw that string in the test-engineer's output. The container mask
  (`--tmpfs /project/apps/api/src:ro,size=0`) blocked any actual read, but the test
  engineer is emitting implementation-path references — most likely cribbed from the
  spec or inferred from `_contracts`. Not a confirmed information leak, but a signal
  the prompt is reproducing paths from spec text.
- **`COVERAGE_GAP` → `COVERAGE_REGEN` → `COVERAGE_PASS`** — the coverage loop
  kicked in once: 7 → (validate drops to 6) → 10 scenarios on regen.
- **`IMPLEMENTER_TEMP_ZERO iteration=2`** — temperature clamped to 0 on iter 2 for
  determinism. Pass rate jumped 0 → 55.
- **Two oracle invocations per iteration** — first for the ship/no-ship decision,
  second for feedback generation (FEEDBACK_WORD_COUNT emitted only on the second pass).

## Audit barrier spot-check

All analysis agents ran with `-v $TARGET:/project:ro` and appropriate masks. Spot-check
from `$TARGET/.dark-factory/audit/2026-03-28-database-foundation-e2d9548b3ad24590.log`:

| Agent | Source mask | Visible-test mask | `.git` mask |
|---|---|---|---|
| spec-validator | `--tmpfs /project/apps/api/src:ro,size=0` | — | ✓ `--tmpfs /project/.git:ro,size=0` |
| architect | — (needs source read) | masks `scenarios`, `audit`, `state`, `failures` | ✓ |
| qa-holdout | `--tmpfs /project/apps/api/src:ro,size=0` | `--tmpfs /project/.dark-factory/scenarios:ro,size=0` | ✓ |
| test-engineer | `--tmpfs /project/apps/api/src:ro,size=0` | `--tmpfs /project/.dark-factory/holdouts:ro,size=0` | ✓ |
| implementer | — (`:rw` on project) | masks `holdouts`, `reports`, `audit`, `state`, `failures` | ✓ |
| defect-classifier | — (`:ro` on project) | masks `audit`, `state`, `failures` | ✓ |
| reviewer | — (`:ro` on project) | masks `scenarios`, `holdouts`, `reports` | ✓ |
| oracle | — (`:ro` on project) | — | — |

**Observation (not a regression — consistent with architect-narrowed scope):**
`environment.toml` has `source_dirs = ["apps/api/src"]` even though the project has
`apps/api/src`, `apps/web/src`, `packages/shared/src` (per `dark-factory init` detection).
Only `apps/api/src` is masked on qa-holdout/test-engineer. This matches the known
limitation in CLAUDE.md (`--disallowedTools` covers only one source_dir); here Docker
also only masks the architect-chosen source_dir rather than the superset detected by
init. The implementer-side of this run never touched `apps/web/src` or
`packages/shared/src`, so it's scoping not leakage — but worth keeping in mind for
cross-stack specs.

## Diagnostic excerpts

### Classifier — root cause pattern

Every RLS/GUC failing test carries `internal_reason_code:
POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION`. That the classifier converges on a single
code across 10 different failing tests is a strong signal the failure is
implementation-level and systemic — not test flake.

### Iter 2 feedback — actionable items

From `feedback-iter2.md`, the one item that iter 2 classified as IMPL_BUG (not
SPEC_AMBIGUOUS) and therefore plausibly fixable in-pipeline:

> **AC-4, EC-9** — Migration infrastructure is fundamentally broken. Ensure
> drizzle.config.ts configuration is correct, verify database connection works, and
> confirm migration metadata files are properly generated.

Everything else was marked SPEC_AMBIGUOUS, which is what bumped the run into the
escalation path rather than a third implementer attempt.

## Takeaways

1. **Escalation fired early on a plateau, not a stall.** Trajectory was IMPROVING and
   iter 2 hit 55%. The promotion to escalation was driven by classification (SPEC_AMBIGUOUS
   majority), not by regression or stall. Worth verifying this is the desired policy for
   SPEC_AMBIGUOUS-heavy iterations — prior `docs/pipeline-run-reports/` history for this
   spec shows similar patterns.
2. **Drizzle migration hang recurred on iter 1.** `drizzle-kit migrate` hanging during
   setup blocked any behavioral signal on iter 1, forcing iter 2 to do both the infra
   fix and the behavioral work. Worth a targeted spec hint or precheck.
3. **Test-engineer barrier violation is worth investigating.** The detector caught
   `apps/api/src` as a path string in test-engineer output during coverage_regen. Docker
   masked the read, but if the test-engineer is synthesizing impl paths from the spec,
   the contract stubs or spec hints may be leaking path-shaped information. Review
   `contracts/` and the spec's code-reference patterns.
4. **Escalation report references 17 prior runs.** That the escalation agent synthesizes
   across archived runs is useful — but it also implies the archive is carrying
   long-lived signal into each new run's escalation output. Expected, just noting.

## Artifacts

- Quality gate: `$TARGET/.dark-factory/reports/2026-03-28-database-foundation-quality-gate-summary.md`
- Defects: `$TARGET/.dark-factory/reports/2026-03-28-database-foundation-defects-iter2.json`
- Feedback (iter 1): `$TARGET/.dark-factory/reviews/2026-03-28-database-foundation-feedback-iter1.md`
- Feedback (iter 2): `$TARGET/.dark-factory/reviews/2026-03-28-database-foundation-feedback-iter2.md`
- Escalation: `$TARGET/.dark-factory/reviews/2026-03-28-database-foundation-escalation.md`
- Review (iter 2): `$TARGET/.dark-factory/reviews/2026-03-28-database-foundation-review.md`
- Audit log: `$TARGET/.dark-factory/audit/2026-03-28-database-foundation-e2d9548b3ad24590.log`
- Archived run: `$TARGET/.dark-factory/archive/<ts>-2026-03-28-database-foundation-<short>/`
- Pipeline stdout/stderr: `/tmp/df-run-2026-03-28-database-foundation.log`
