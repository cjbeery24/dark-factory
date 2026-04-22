# Pipeline Run — budget-brain-v2 / 2026-03-28-database-foundation

**Run ID:** `4736bbd9506148ef`
**Date:** 2026-04-22 04:06Z → 04:59Z (~53 min wall)
**Outcome:** **SHIP** (via `SHIP_WITH_GAPS` — PLATEAU trajectory, 80% pass rate, 7 stable failures all classified `IMPL_BUG`)
**Iterations:** 2 (both at 80%, identical stable-failure set)

First end-to-end SHIP for this spec after 10+ prior attempts. SHIP path was the stable-failure promotion of an Oracle NO-SHIP on iter 2, not a clean pass.

## State file

```
spec=2026-03-28-database-foundation
run_id=4736bbd9506148ef
spec_hash=074b9f06483d3a99d9ce368ddc31d5706cbeb99724ef5c2f95b2508dc4b11330
last_decision=NO-SHIP
iteration=2
smoke_result=passed
pass_rate=80
gate_exit_code=1
phase=oracle_complete
status=shipped
```

`last_decision=NO-SHIP` alongside `status=shipped` is the signature of SHIP_WITH_GAPS: the Oracle said NO-SHIP and the trajectory+stable-failure gate promoted it.

## Context going in

Run followed two framework commits:
- `a50cd1c` — scenario-infra fast-fail bypass on Substrate/Import Contract FAIL (preserved from prior work)
- `85a8e18` — spec fix for AC-5 malformed `sql'...'` tagged-template syntax (paired with the new `sql_tag_syntax` precheck in `darkfactory/adapters/postgres/sql_tag_syntax.py`)

The precheck caught the AC-5 defect mechanically on pre-run validation; the spec edit closed the LLM-variance window that caused 1230234d and e614a176 to hit it stochastically.

## Quality gate — final iteration

```
| Gate                         | Status |
|------------------------------|--------|
| Install                      | PASS   |
| Setup                        | PASS   |
| Build                        | PASS   |
| Lint                         | PASS   |
| Type Check                   | PASS   |
| Substrate Verification       | SKIP   |
| Import Contract              | FAIL   |
| Self-Tests                   | PASS   |
| Scenario Lint                | PASS   |
| Blind Scenarios (executable) | FAIL   |
| Mutation Testing             | SKIP   |

Result: FAIL
```

Gate result FAIL was overridden by Oracle → SHIP_WITH_GAPS. Two surfaces explain the FAIL:

### Import Contract FAIL — the `.js`-suffix-on-`.ts`-source defect, again

4 contracts emitted by the architect could not be resolved:

```
Error: Failed to load url ../../../apps/api/src/db/index.js          (contract-00)
Error: Failed to load url ../../../apps/api/src/db/tenant-context.js (contract-01)
Error: Failed to load url ../../../apps/api/src/db/schema/helpers.js (contract-02)
Error: Failed to load url ../../../apps/api/src/db/health-check.js   (contract-03)
```

Architect continues to declare `.js` suffixes on TypeScript source files. The files exist at their `.ts` equivalents — this is pure suffix-mismatch in the emitted import-contract manifest. Same defect class as prior runs (documented in `project_pipeline_unresolved_bugs.md` as bug #8). The fast-fail-bypass (commit `a50cd1c`) ensured scenarios still ran; otherwise this would have terminated the run.

### Blind Scenarios FAIL — 7 stable IMPL_BUGs, 80% pass

36 scenarios / 29 passed / 7 failed / 0 skipped / 0 crashed. All 7 failures persisted across both iterations (ratio 1.0 — fully stable), which is precisely the condition that qualifies a PLATEAU for SHIP_WITH_GAPS.

```
| # | Anchor | Category | Area                                           |
|---|--------|----------|------------------------------------------------|
| 1 | EC-6   | IMPL_BUG | pool config under load                         |
| 2 | EC-4   | IMPL_BUG | unset app.tenant_id GUC fail-closed            |
| 3 | AC-9   | IMPL_BUG | /health degraded status when DB unreachable    |
| 4 | AC-8   | IMPL_BUG | nullable RLS — different-tenant view           |
| 5 | AC-8   | IMPL_BUG | nullable RLS — tenant + null-tenant rows       |
| 6 | FR-3   | IMPL_BUG | nullable RLS — unset-context returns system    |
| 7 | EC-5   | IMPL_BUG | SET LOCAL rollback on mid-transaction failure  |
```

Representative failure excerpt (FR-3, nullable RLS):

```
AssertionError: expected [ { id: 1, …(3) }, …(3) ] to have a length of 2 but got 4
- Expected 2
+ Received 4
  .dark-factory/scenarios/2026-03-28-database-foundation/substrate-nullable-rls.test.ts:123
```

The nullable RLS policy returns too many rows; the implementation's NULL-handling doesn't filter correctly. Five of the seven stable failures cluster around RLS/GUC behavior — consistent signal that the policy predicate and GUC fallback patterns are the next thing to fix (spec-level or impl-level).

## Substrate verification

```json
{"status": "skip", "findings": []}
```

Gate reports `[pipeline] No substrate manifest required`. Current spec does not emit a substrate manifest that triggers the verifier; no GUC/RLS invariant checks ran at the substrate layer. The seven substrate-related failures surfaced through scenario execution instead, which is why they landed as IMPL_BUG classifications rather than SUBSTRATE_VIOLATION.

## Notable events

- **Coverage regen loop** — first test-engineer pass left `FR-2 FR-4 AC-3 AC-4 AC-9 EC-8` uncovered; a second regen pass closed down to `AC-3 AC-4 AC-9`; a third pass cleared coverage. Three test-engineer runs on iter 1 (~850s total in test generation).
- **Smoke** — PASSED iter 1 (advisory) and iter 2 (fatal), both without server error. The boot gate held.
- **Implementer iter 1 — 3 attempts.** 351s → 105s → 78s. Between attempt 1 and attempt 2 the pipeline logged a `BARRIER_VIOLATION` for `role=implementer path=.git`. Attempt 1 tried to read the masked `.git/` mount; the mask held (empty tmpfs), attempt 2 proceeded cleanly. Behavioral failure, not a barrier breach.
- **Implementer iter 2 — 2 attempts.** 337s + 30s. No barrier violations.
- **Trajectory** — `PLATEAU` at iter 2. Strategy field: `"Fundamentally different approach needed — same strategy is not working."` Identical stable-failure set across iter 1 and iter 2 (ratio 1.0) is exactly what PLATEAU classifies on.
- **SHIP_WITH_GAPS** — fired at iter 2 with `oracle_original=NO-SHIP`, `stable_failures=7`, `stable_failure_ratio=1.0`, `pass_rate=80`. Promoted NO-SHIP → SHIP on the strength of full failure stability above the 70% pass floor.
- **`defect-classifier count_mismatch`** — did NOT fire this run. Both iterations produced clean JSON with 7 defects matching 7 failing tests, `exit_code=0`. This is the first run where the classifier consumed its full input without the count mismatch bug. Not conclusive that the bug is fixed (haven't touched classifier code) — could be input-shape luck.

## Agent wall times

```
spec-validator   196s
architect        175s
qa-holdout       356s
test-engineer    324s + 231s + 309s  = 864s (generate + 2× coverage_regen)
implementer i1   351s + 105s + 78s   = 534s (3 attempts, BARRIER_VIOLATION on #1)
defect-classi i1  91s
reviewer i1       95s
oracle i1         92s
implementer i2   337s + 30s          = 367s (2 attempts)
defect-classi i2  78s
reviewer i2      172s
oracle i2        123s
```

Dominant costs: qa-holdout + test-engineer (~1220s, ~40% of wall), followed by implementer iter 1 (534s).

## Audit log spot-check

No barrier-mask violations other than the single iter-1 implementer `.git` read flagged by `BARRIER_VIOLATION`. Source-dir tmpfs masks on qa-holdout / test-engineer containers remained intact (confirmed via absence of further `BARRIER_VIOLATION` events — the event is emitted on any masked-path access attempt).

## What this run tells us

1. **`sql_tag_syntax` precheck worked exactly as designed.** No AC-5 regression; validator variance eliminated for that defect class.
2. **SHIP_WITH_GAPS is load-bearing.** Without the PLATEAU-driven promotion the pipeline would have returned NO-SHIP on iter 2 despite stable 80% coverage of the spec. This is the first observed production SHIP via that path on budget-brain-v2.
3. **Import Contract `.js`-suffix bug still latent.** Bypass prevents a termination, but the emitted manifest is still wrong. Separate fix needed — likely architect prompt or post-architect manifest normalization.
4. **Substrate verification never ran.** The manifest isn't being emitted in a shape the verifier accepts. Investigate whether the architect is producing one at all and, if so, why the gate decides `No substrate manifest required`.
5. **Nullable RLS cluster is the dominant real gap.** 5 of 7 stable failures are in GUC/RLS behavior. Followup: decide whether to refine AC-8/FR-3/EC-4 precondition wording, add a substrate contract for nullable RLS, or accept the gap and ship a bugfix spec.
6. **`defect-classifier count_mismatch` silent this run** — worth watching, not yet resolved.

## Followups (not ship-blocking)

- [ ] Architect `.js`-on-`.ts` manifest defect — open ticket; needs prompt/postprocess fix.
- [ ] Substrate manifest emission — verify the architect produces one and why the verifier is skipping.
- [ ] Nullable RLS policy — implementation bug cluster; next spec or hotfix.
- [ ] Classifier count_mismatch — monitor across runs; capture input hash when it next fires.
