# 2026-04-29 07:57 — budget-brain-v2 database-foundation **SHIPPED**

## Outcome

**SHIP_WITH_GAPS** at iter-2. Wall clock ~76 minutes (under 90-min cap).

This is the **Phase 5 discriminating run** for the higher-priority queue arc
(plan: `docs/HIGHER-PRIORITY-QUEUE-PLAN.md`, Neo-approved 2026-04-28). The
arc shipped Phase 1 (commit `5b02732`), Phases 2+4+4.5 (commit `8351e5b`),
then this run validates them end-to-end. **First successful ship of the
database-foundation spec since the multi-session arc began.**

The 13:50 run (2026-04-28) was the contrasting case: same spec, escalated
after 3 iterations because the Oracle's fix-list paraphrased the
bootstrap_check message and dropped the fix mechanism. This run produced
a SHIP_WITH_GAPS outcome at iter-2 because the canonical message reached
the implementer verbatim — and the implementer ACTED on it.

## Iteration trajectory

| Iter | Pass rate | Gate | Substrate | Trajectory | Path |
|-----:|----------:|------|-----------|------------|------|
| 1 | 80% | FAIL | FAIL (2 findings: bootstrap-not-applied + guc-unregistered) | — | classifier → reviewer → oracle (NO-SHIP) |
| 2 | 80% | FAIL | FAIL (same 2) | PLATEAU | classifier → reviewer → oracle (**SHIP_WITH_GAPS**) |

**Stable failure detection** triggered the SHIP_WITH_GAPS promotion at iter-2:
the failing test set was identical across iter-1 and iter-2 (4 tests
cascading from `bootstrap_check`, plus 3 non-cascading IMPL_BUG/SPEC_AMBIGUOUS
issues), and the Oracle determined the framework couldn't get further
without spec-level intervention.

## State file (terminal)

```
spec=2026-03-28-database-foundation
run_id=92062a08fd9042f2
last_decision=NO-SHIP
iteration=2
smoke_result=passed
pass_rate=80
gate_exit_code=1
phase=oracle_complete
status=shipped
completed_step=99
```

`last_decision=NO-SHIP` + `status=shipped` looks contradictory but reflects
the SHIP_WITH_GAPS escape: Oracle's strict ship/no-ship decision was
NO-SHIP, but the stable-failure promotion ruled SHIP.

## Per-phase evaluation

### Phase 1 (Gap 3 follow-up: untracked-file capture + escape-hatch SHA fallback) — ✅ CLEAN WIN

Both iters produced complete patches in the archive. iter-1 has **17
file diffs** including 13 synthetic `/dev/null` blocks for new files
(the entire `apps/api/src/db/` tree, drizzle config, migrations
baseline + journal, schema helpers, tenant context, health check, plus
`.test.ts` files). Compare to the 13:50 run's iter-1 which captured
only **13 lines** (config.ts modifications only — the new files were
all dropped by git 2.50.1 `stash create -u`).

iter-2 has 14 file diffs (13 /dev/null) — the iter-2 implementer
modified existing files plus added a few more tests; all captured.

```
$ wc -l archive/.../iter-1/implementer-changes.patch
1098 lines, 38302 bytes (vs 13:50's 13 lines)

$ wc -l archive/.../iter-2/implementer-changes.patch
1042 lines, 36155 bytes (vs 13:50's MISSING)
```

**Phase 1b (escape-hatch SHA fallback)** didn't fire this run — both
Oracle calls completed normally so end_sha was captured at oracle-time.
The fallback path is unit-test-validated; live exercise requires a
future run that takes the escape-hatch path.

### Phase 2 (holdouts.md consumer — Reviewer wiring) — ✅ CLEAN WIN

**Reviewer's review.md mentions QA Holdout content verbatim**:

> "5. **Test the adversarial scenarios** — The QA Holdout report identifies
>  numerous edge cases around Unicode injection, oversized payloads, and
>  concurrency that should be validated against the implementation"

Pre-fix: holdouts.md was written with no consumer (only swept by
run_start_cleanup). Post-fix: Reviewer reads it via
`_format_holdouts_inline`, applies the role-side directive about
Robustness scoring (scored 6/10), and uses spec-gap callouts to
corroborate SPEC_AMBIGUOUS classifications.

### Phase 4 (cascade evidence injection) — ✅ WIN with one wrinkle

**The headline result**: the Oracle's iter-1 fix-list quoted the
**full canonical bootstrap_check message** including the fix mechanism:

> "1. **substrate/bootstrap_check** — Fix upstream substrate/bootstrap_check
>  failure. The `__drizzle_migrations` table was not created after setup,
>  cascading to 4 tests. Evidence: bootstrap_check
>  'drizzle-migrations-tracking-table' did not observe expected
>  post-condition: SQL `SELECT 1 FROM information_schema.tables WHERE
>  table_name='__drizzle_migrations'` returned no rows. **Setup may have
>  exited 0 without applying anything (e.g. drizzle-kit migrate against
>  a comment-only baseline never creates the tracking table).**"

Compare to 13:50: Oracle wrote "the migration system" instead of quoting
the canonical mechanism. The canonical message reached the
iter-2 implementer prompt verbatim, and the implementer **acted on it**
(see "Implementer actionability" below).

**The wrinkle (Phase 4 follow-up bug discovered)**: the classifier set
`upstream_cause.primitive: "bootstrap_check"`, but the substrate
finding's `code` is `substrate-bootstrap-not-applied`. **Mismatch** —
`_load_canonical_upstream_messages` builds `("substrate",
"substrate-bootstrap-not-applied"): <message>` while the cascade
summary looks up `("substrate", "bootstrap_check")` → key miss →
canonical-source path doesn't fire.

Why did the Oracle still get the full message? Two channels:
1. **`_format_substrate_findings_inline`** (older block from commit
   `c68b3f8`) carries the canonical findings in a SEPARATE Oracle
   prompt block. The Oracle composed its fix-list using both that
   block and Phase 4's cascade summary.
2. **Phase 4's longest-wins fallback** carried the classifier's
   evidence (196 chars, including "comment-only baseline never creates
   the tracking table" — captured this run, dropped in 13:50) into the
   cascade row.

So the framework had two redundant paths and at least one carried the
message correctly. But the Phase 4 mechanical canonical-sourcing path
itself didn't fire as designed.

**Root cause**: my `_CASCADE_DIRECTIVE_TEMPLATE` example said
`'bootstrap_check'` (the substrate primitive name from the spec, not
the finding code). The classifier copied the example value. The
example was misleading.

**Fix (deferred to Phase 4 follow-up commit)**: update the cascade
directive to instruct the classifier to use the EXACT `code` field
from the substrate finding (not a paraphrased name), and either
remove the misleading example OR replace it with `<finding-code>`
placeholder. Could also add post-classifier validation in
`_classify_once` that warns when `upstream_cause.primitive` doesn't
match any substrate-findings.json `code`.

### Phase 4.5 (fixture-based test) — ✅ WIN (caught nothing during the run because it ran BEFORE)

The fixture tests passed during commit pre-checks (saved 90+ minutes by
exercising the canonical-source path against frozen 13:50 artifacts
where the primitive name DID match). The classifier-name-mismatch bug
this run surfaced wouldn't have been caught by the fixtures because
the fixture's defects file uses the matching name. **Fixture-based
testing isn't a substitute for live runs — it's a floor.**

### Implementer actionability — ✅ THE BIG WIN

Compare iter-2 implementations across the two runs:

**13:50 iter-2 baseline.sql**: comment-only file (the original spec's
intent). Implementer didn't add anything despite the cascade fix-list
inlining the bootstrap_check message into the iter-2 escape-hatch
directive THREE TIMES.

**Phase 5 iter-2 baseline.sql**:
```sql
-- baseline migration ... (35 lines of doc comments) ...

-- Minimal statement to ensure drizzle migration tracking table is created
SELECT 1;
```

**The implementer read "comment-only baseline never creates the tracking
table" and added a non-comment statement.** The exact fix the canonical
message hinted at. Phase 4's actionability hypothesis: validated.

The fix didn't actually solve the substrate primitive's check (drizzle-
kit doesn't create the tracking table on `SELECT` statements — only on
schema-modifying CREATE/ALTER). That's spec/adapter-level: the
substrate primitive's hint message is technically wrong (or at least
incomplete). But that's NOT a framework gap — it's a postgres-adapter
or spec-author concern.

### SHIP_WITH_GAPS analysis

The Oracle saw at iter-2:
- Same 4 substrate-cascade failures (stable across iters)
- Same 3 non-cascade failures (1 IMPL_BUG, 2 SPEC_AMBIGUOUS, all stable)
- 80% pass rate, NO regression
- PLATEAU trajectory

Stable-failure-set + non-regressing pass rate triggered SHIP_WITH_GAPS.
The Oracle effectively decided: "the framework has done what it can;
the remaining failures are infra-level (substrate gaps the implementer
can't fix without different spec guidance) or one minor IMPL_BUG that's
not blocking. Ship and let the human decide."

This is the right outcome for this spec given the substrate-primitive's
hint-message limitation.

## Key events

- **Implementer iter-1 attempt-1 timed out at 900s** (15-min hard cap).
  Was actively writing test files when killed (over-producing self-tests
  for migration meta files, e.g. `_journal.test.ts`). Pipeline retried;
  attempt-2 succeeded in 382s.
- **Coverage loop closed in 1 regen attempt** (vs 2 on 13:50).
- **No `BARRIER_PATH_ECHO_DETECTED`** events.
- **No `ESCAPE_HATCH_FEEDBACK`** — both iter terminals went through normal
  classifier→reviewer→oracle. Different from 13:50 (which fired
  GATE_REGRESSION at iter-2 and TRAJECTORY_PLATEAU at iter-3).

## Comparison to 13:50

| Question | 13:50 (escalated) | Phase 5 (shipped) |
|----------|-------------------|-------------------|
| Spec shipped? | No | **YES (SHIP_WITH_GAPS at iter-2)** |
| Per-iter patches captured? | iter-1: misleading 13-line patch; iter-2/3: missing | iter-1: 1098 lines incl. db/ tree; iter-2: 1042 lines |
| Untracked db/ tree in patch? | No (git 2.50.1 skew) | **Yes (13 synthetic /dev/null blocks)** |
| holdouts.md consumed by Reviewer? | No (no consumer) | **Yes (review.md quotes content)** |
| Cascade summary aggregation? | 18 → 2 cascade entries | 4 → 1 cascade entry |
| Canonical bootstrap_check message reaches Oracle fix-list verbatim? | No (paraphrased to "the migration system") | **Yes (full canonical including fix mechanism)** |
| Implementer acted on the cascade fix? | No (3 iterations, no action) | **Yes (added `SELECT 1;` to baseline.sql at iter-2)** |
| Spec-validator score | 98 | 95 |

## Action items

### Phase 4 follow-up (queued, NOT blocking next work)

1. **Cascade directive primitive-name fix.** Update
   `_CASCADE_DIRECTIVE_TEMPLATE` so classifier emits the exact
   substrate-finding `code` (e.g. `substrate-bootstrap-not-applied`)
   not the substrate-primitive name (e.g. `bootstrap_check`). Either
   strengthen the directive's instruction OR replace the example value
   with `<finding-code>` placeholder OR add classifier-side validation
   warning when `upstream_cause.primitive` doesn't match any finding
   code in substrate-findings.json.

2. **Add a unit test** pinning the directive's example matches the
   intent. Currently the directive's `bootstrap_check` example was
   copied to the field literally — a regression test using a fixture
   with `(stage, primitive)` matching the substrate finding's `code`
   (already in the 13:50 fixture) plus a NEW fixture where the
   classifier paraphrased the name (this run's defects-iter1.json)
   would catch this.

### Now-unblocked: Phase 3 (LAST in the higher-priority queue arc)

Per the plan, Phase 3 (spec_validator wire-language FP +
non-determinism) was deferred until the Phase 5 outcome could inform
the option choice. Evidence from this run:

- **Spec validator score**: 95 this run, 98 prior, 75/87/92/95 across
  earlier database-foundation runs. **Verdict**: PASS-stable across
  all observed runs (no flips). **Score**: variance band 75-98 (23
  points), but never crosses any policy threshold.
- **Wire-language false-positive on AC-37 / §8b**: not directly
  observed this run because the spec passed validation — but the
  validator did NOT trigger AC-37 violations, suggesting either the
  FP is non-deterministic too OR the spec's foot-gun docs aren't
  triggering on every run.

**Option choice for Phase 3 (locked by Phase 5 evidence)**:
- (i) Pin temperature on spec-validator agent — sufficient for the
  verdict-stable observation. The score variance is cosmetic.
- (ii)/(iii) (mechanical extraction of structural rules) — NOT
  required by the evidence. Defer until verdict starts flipping.

Recommend **option (i)** — the smallest change consistent with the
evidence. Tame the score variance via temperature pin; leave the
wire-language regex itself unchanged (the FP didn't reproduce here).

### Out of scope for this arc (deferred to spec-author / postgres-adapter work)

- The substrate-bootstrap-not-applied check's hint message ("comment-only
  baseline never creates the tracking table") suggests adding a
  non-comment SQL statement is the fix. **`SELECT 1;` doesn't actually
  trigger drizzle-kit to create the tracking table** — only schema-
  modifying CREATE/ALTER does. The hint message should either name a
  schema-modifying statement (e.g. `CREATE TABLE IF NOT EXISTS
  __drizzle_migrations__placeholder()`) OR the substrate primitive
  should accept "drizzle-kit migrate exit 0" as success without
  cross-checking the tracking table.

## After this arc

Three of four queue items shipped + the discriminating run is a SHIP.
Phase 3 next, then the consumer-audit registry plan can be rewritten
per Neo's blockers (B1 chain modeling, B2 opportunity cost satisfied
by this arc, B3 P0 ordering).

The 24-test increment from Phases 1-4.5 brought the unit suite to 3897
passing. Phase 3's tests will add a few more.
