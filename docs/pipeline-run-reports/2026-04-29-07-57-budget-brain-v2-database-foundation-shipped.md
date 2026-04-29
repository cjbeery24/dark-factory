# 2026-04-29 07:57 — budget-brain-v2 database-foundation SHIP_WITH_GAPS (escape-hatch ship)

## Outcome — honest framing (post-Neo amendment)

**SHIP_WITH_GAPS at iter-2. Wall clock ~76 minutes (under 90-min cap).
Substrate verification still FAILING iter-1 and iter-2 identically.**

This was the **Phase 5 discriminating run** for the higher-priority queue
arc (plan: `docs/HIGHER-PRIORITY-QUEUE-PLAN.md`, Neo-approved 2026-04-28).
The arc shipped Phase 1 (commit `5b02732`), Phases 2+4+4.5 (commit
`8351e5b`).

**Original framing in this report (now corrected via Neo amendment 2026-04-29)**:
"first end-to-end ship since the arc began", "actionability hypothesis
confirmed", "WIN with one wrinkle" on Phase 4. Each of those was rationalization.
The honest read:

- The Oracle's strict ship/no-ship decision was **NO-SHIP**. SHIP_WITH_GAPS
  fired because the failure set was *stable*, not *fixed*. The
  bootstrap_check substrate primitive failed in iter-1 and iter-2 with
  identical evidence strings (4 cascading tests, same root cause).
- The spec the framework was running has an **internal contradiction**:
  FR-3 says "the baseline migration itself creates no tables and applies
  no policies" while FR-1 / AC-5 / AC-24 require `__drizzle_migrations`
  records to exist (which can't happen without a real migration).
  The framework cannot fix a self-contradicting spec.
- The implementer's iter-2 attempted fix (`SELECT 1;` in baseline.sql)
  was **semantically wrong** — drizzle-kit's `ensureMigrationsTable`
  runs only when applying journal entries, and `SELECT 1;` doesn't
  change journal entries. The implementer read the canonical message,
  acted on it, and produced a fix the message's described mechanism
  predicts would work — but the message's described mechanism is itself
  **wrong about how drizzle-kit operates**. See "The hint-message bug"
  below.
- Compared to the 13:50 run (which escalated after 3 iterations), this
  run achieved a stable-failure-set faster — that IS framework progress.
  But "shipping a spec with substrate verification permanently failing"
  is not the same as "shipping a working spec." Calling it a category
  win is misleading; calling it "framework reached SHIP_WITH_GAPS escape
  faster" is honest.

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

### Phase 4 (cascade evidence injection) — ⚠️ MECHANICAL PATH UNVALIDATED IN PRODUCTION

**Original framing (corrected by Neo 2026-04-29)**: "WIN with one wrinkle".
**Honest framing**: the mechanical canonical-sourcing path that Phase 4
was designed to deliver did NOT fire in this run. The canonical message
reached the Oracle via a DIFFERENT path. Phase 4's load-bearing claim
remains unvalidated under live conditions.

**What was supposed to happen** (Phase 4 design):
1. Classifier emits `upstream_cause.primitive: "<finding code>"` (e.g.
   `"substrate-bootstrap-not-applied"`).
2. `_load_canonical_upstream_messages` reads substrate-findings.json,
   builds `{("substrate", "substrate-bootstrap-not-applied"): <message>}`.
3. `_format_cascade_summary` looks up `("substrate", "<finding code>")`,
   finds the canonical message, renders it verbatim into the cascade row.

**What actually happened**:
1. Classifier emitted `upstream_cause.primitive: "bootstrap_check"`
   (the substrate **primitive name** from the spec, NOT the finding
   `code`).
2. `_load_canonical_upstream_messages` built keyed on
   `("substrate", "substrate-bootstrap-not-applied")` — the actual code
   in the finding.
3. Lookup MISSED. Canonical-source path did not fire.
4. The longest-wins fallback rendered the classifier's `evidence` field
   (196 chars, missing the trailing "comment-only baseline" sentence
   that contains the "fix mechanism").
5. **The full canonical message reached the Oracle's fix-list anyway**
   — but via the older `_format_substrate_findings_inline` block (commit
   `c68b3f8`). That block has been carrying canonical findings into the
   Oracle prompt as a SEPARATE block since before Phase 4 was designed.
   The Oracle's fix-list composed its quote from THAT block, not from
   Phase 4's cascade row.

Phase 4 was designed to be the load-bearing carrier. In practice the
older c68b3f8 path is doing the work. **Phase 4's value-add over the
pre-existing path is currently zero in production** — until the
classifier-emitted primitive name matches the finding code, the
canonical-source lookup will continue to miss.

**Root cause of the name mismatch**: my `_CASCADE_DIRECTIVE_TEMPLATE`
example said `'bootstrap_check'` (the substrate-primitive name from
the spec's §4a `required_lifecycle_sequences` block). The classifier
copied the example value verbatim instead of using the actual
substrate-findings.json `code` field.

**Fix priority — BLOCKING before any future discriminating run** (was
"deferred" in the original framing — Neo corrected). Either:
- (a) Strengthen `_CASCADE_DIRECTIVE_TEMPLATE` to instruct the
  classifier explicitly: "use the exact `code` field from the
  substrate-findings.json finding, NOT a paraphrased name. Examples
  of valid codes are listed in your prompt's substrate-findings
  block — copy one of those literal strings into the primitive
  field." Replace or remove the misleading `'bootstrap_check'`
  example.
- (b) Add post-classifier validation in `_classify_once` that emits
  a warning AND fails the cross-validation if any
  `upstream_cause.primitive` doesn't match a substrate-findings.json
  `code` field for `stage == "substrate"` defects.
- Both is best.

**Status reclassification**: Phase 4 is "fix-not-fully-shipped",
not "shipped with follow-up bug." Until the live classifier emits
correct primitive names, Phase 4's mechanical canonical-sourcing
adds zero observable value over the pre-existing c68b3f8 path.

### Phase 4.5 (fixture-based test) — ✅ WIN (caught nothing during the run because it ran BEFORE)

The fixture tests passed during commit pre-checks (saved 90+ minutes by
exercising the canonical-source path against frozen 13:50 artifacts
where the primitive name DID match). The classifier-name-mismatch bug
this run surfaced wouldn't have been caught by the fixtures because
the fixture's defects file uses the matching name. **Fixture-based
testing isn't a substitute for live runs — it's a floor.**

### Implementer actionability — ⚠️ PARTIALLY VALIDATED

**Original framing (corrected by Neo 2026-04-29)**: "THE BIG WIN…
hypothesis validated… NOT a framework gap, spec/adapter concern."
**Honest framing**: the implementer read the canonical message and acted
on it (read-and-act yes). The fix the implementer produced was wrong
and the substrate primitive's hint was a contributing cause (which
makes this PARTIALLY a framework gap, not a clean externality).

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
table" and added a non-comment statement.** That's a legitimate framework
win: with the canonical message reaching the implementer prompt, it acted
on the hint. **But the hint described the wrong fix mechanism** (see
"The hint-message bug" below), so the implementer's syntactically-correct
action was semantically useless.

Validated: implementer reads canonical messages and acts on described
mechanisms.

NOT validated: implementer can produce correct fixes from substrate
hints. That depends on the hint being mechanistically correct, which
this one isn't.

### The hint-message bug (FRAMEWORK-OWNED, not external)

Per Neo's analysis (2026-04-29), the bootstrap_check primitive's hint
message at `darkfactory/adapters/postgres/adapter.py:1149-1156` is:

> "bootstrap_check {name} did not observe expected post-condition: SQL
> `{check}` returned no rows. Setup may have exited 0 without applying
> anything (e.g. drizzle-kit migrate against a comment-only baseline
> never creates the tracking table)."

**The mechanism named ("comment-only baseline never creates the tracking
table") is mechanistically incorrect for drizzle-kit.** The actual
mechanism: drizzle-kit's `ensureMigrationsTable` step (which creates
`__drizzle_migrations`) runs only when there is at least one journal
entry to apply. A comment-only SQL file WITH a journal entry produces
a tracking table (drizzle still applies the file, even if the file is
a no-op). A SQL file with no journal entry — or an empty `entries: []`
journal — produces nothing. **The trigger condition is "journal entry
exists for an unapplied migration", NOT "non-comment SQL exists."**

The implementer's `SELECT 1;` was useless because it doesn't create
journal entries. The implementer would have needed to add a journal
entry referencing the baseline (or for drizzle-kit to be re-run with
`db:generate`, which creates journal entries from schema changes).

Even more fundamentally: **the spec at FR-3 says "the baseline migration
itself creates no tables and applies no policies" while FR-1 / AC-5 /
AC-24 require `__drizzle_migrations` records to exist** (which can't
happen without an applied migration). The spec contradicts itself.
The framework cannot fix a self-contradicting spec.

### The user's architectural question — Neo's direct answer

> "Is this caused by trying to set up drizzle without any tables? Is
> this a flaw in the spec where we need to create at least one table,
> or should it be possible to set up drizzle with no tables defined yet?"

**Both spec and adapter share blame; the spec is the proximate bug.**

- **Spec contradiction**: FR-3 says baseline creates no tables, but
  FR-1 + FR-3 side-effects + AC-5 + AC-24 require `__drizzle_migrations`
  records. The "comment-only baseline" approach cannot satisfy the
  bootstrap_check declaration the architect emits from §4a. Either:
  - The baseline needs **a no-op DDL** (e.g. `CREATE TABLE IF NOT
    EXISTS _budget_brain_marker(); DROP TABLE _budget_brain_marker;`)
    so drizzle-kit has something to apply.
  - **OR** the bootstrap_check criterion needs softening to "journal
    seeded + migrate exit 0" rather than "tracking table exists."
  - **OR** the spec drops FR-3's "creates no tables" rule and accepts
    that the baseline must contain at least one DDL statement.
- **Adapter hint contributing**: the postgres adapter's bootstrap_check
  hint message names the wrong mechanism. Even if the spec were
  internally consistent, the hint would mis-direct an implementer.
  Better hint:
  - State the observed gap ("the `__drizzle_migrations` tracking table
    was not created by the Setup phase") and refuse to speculate on
    cause, OR
  - Name the actual fix lever: "drizzle-kit creates the tracking table
    only when applying a journal entry. Ensure (a) there is at least
    one entry in `meta/_journal.json`, AND (b) the corresponding SQL
    file applies cleanly."

**Direct answer to "should drizzle work with no tables defined yet"**:
yes, drizzle-kit CAN run without tables — but only if its journal has
no entries (empty migrations directory). The moment a journal entry
exists, drizzle-kit applies the corresponding SQL file and creates the
tracking table as a side effect. The spec's setup is a tricky middle
state: a journal entry exists pointing to a no-content file. drizzle-kit's
behavior in that case is undefined-by-design (the documentation assumes
real migrations).

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

## Action items (post-Neo amendment)

### BLOCKING before any future discriminating run

**Phase 4 follow-up commit** — Phase 4's mechanical canonical-sourcing
path has not yet fired correctly in any production run. The canonical
message has been reaching consumers via the older c68b3f8
`_format_substrate_findings_inline` path. Until the live classifier
emits primitive names matching substrate-findings.json `code` fields,
Phase 4's value-add over the pre-existing path is zero. Specifically:

1. **Cascade directive primitive-name fix.** Update
   `_CASCADE_DIRECTIVE_TEMPLATE` to instruct the classifier explicitly:
   "use the exact `code` field from the substrate-findings.json finding,
   NOT a paraphrased name. Examples of valid codes appear in the
   substrate-findings block of your prompt — copy a literal string."
   Replace or remove the misleading `'bootstrap_check'` example in the
   directive.

2. **Post-classifier validation in `_classify_once`.** Emit a
   `crash_split_mismatch`-style cross-validation error when any
   `upstream_cause.primitive` for `stage == "substrate"` doesn't match
   a `code` field in the substrate-findings.json. The retry loop
   re-prompts the classifier with the rule that fired.

3. **Regression test** using THIS run's defects-iter1.json (with
   primitive `"bootstrap_check"`) as a NEGATIVE fixture: assert that
   the canonical-source lookup MISSES on it (until #1 + #2 ship), and
   that after the fix, the same shape is rejected by the validator
   AND retry produces correct primitive names.

### BLOCKING — adapter hint-message rewrite (FRAMEWORK-OWNED)

Per Neo (2026-04-29), the bootstrap_check primitive's hint message at
`darkfactory/adapters/postgres/adapter.py:1149-1156` describes the
wrong mechanism for drizzle-kit. Rewrite to either:
- (a) State the observed gap and refuse to speculate on cause:
  "bootstrap_check {name} did not observe expected post-condition:
  SQL `{check}` returned no rows. The Setup phase exited 0 but the
  expected post-condition was not satisfied. Investigate whether the
  migration tool actually applied any work."
- (b) Name the actual mechanism for drizzle-kit specifically:
  "drizzle-kit creates the tracking table only when applying a journal
  entry. Ensure (a) `meta/_journal.json` has at least one entry, AND
  (b) the corresponding SQL file applies cleanly via drizzle-kit's
  internal apply step."
- Option (a) is cleaner — it's tool-agnostic. Adding tool-specific
  hint logic to a "tool-agnostic by design" adapter (line 1091:
  "Adapter Python deliberately holds zero migration-tool knowledge")
  is contract-violating.

### BLOCKING — spec contradiction (spec-author concern, but framework
should detect)

The database-foundation spec contradicts itself: FR-3 says "the baseline
migration itself creates no tables and applies no policies" while FR-1
/ AC-5 / AC-24 require `__drizzle_migrations` records (which can only
exist if an applied migration). The framework cannot resolve this; the
spec author must either:
- Add a no-op DDL statement to the baseline, OR
- Drop FR-3's "creates no tables" rule, OR
- Soften the bootstrap_check criterion to "migration tool exit 0 +
  journal seeded" rather than "tracking table exists"

**Framework-side improvement worth scoping**: spec-validator could
detect this class of contradiction (FR claiming "no tables" + AC
requiring tracking-table records) as a wire-language follow-up.
That's tangential to Phase 3's current scope, but a future enhancement.

### Phase 3 (last in the higher-priority queue arc) — option locked

Per the plan, Phase 3 (spec_validator wire-language FP +
non-determinism) was deferred until the Phase 5 outcome could inform
the option choice. Evidence from this run:

- **Spec validator score**: 95 this run, 98 prior, 75/87/92/95 across
  earlier database-foundation runs. Verdict PASS-stable across all
  observed runs (no flips). Score variance band 75-98 (23 points),
  never crosses any policy threshold.
- **Wire-language FP on AC-37 / §8b**: not directly observed this
  run.

**Option choice (locked)**: **(i) Pin temperature on spec-validator
agent.** Smallest change consistent with the evidence. Tame the score
variance via temperature pin; leave the wire-language regex unchanged
(the FP didn't reproduce here). Defer (ii)/(iii) until verdict starts
flipping.

### Implementer over-production / timeout (separate bug, surface for triage)

iter-1 attempt-1 timed out at 900s while writing test files for
auto-generated drizzle artifacts (`migrations/meta/_journal.test.ts`).
Symptomatic of unconstrained TDD scope-creep: the implementer reads
the visible scenarios (which test journal/baseline shape) and extends
that pattern to its own self-tests. Possible fixes:
- Add a directive in implementer prompt: "do NOT write self-tests for
  auto-generated artifacts (drizzle journal, lockfiles, etc.)"
- Tighten implementer's iteration time budget signaling so the agent
  knows to prioritize impl over tests when the wall-clock approaches
  the cap.

Capture as known issue; not blocking the queue arc.

## After this arc

Three of four queue items shipped (Phases 1, 2, 4+4.5) PLUS the
discriminating run completed (Phase 5). But Phase 4's mechanical path
remains unvalidated under live conditions — that's a real blocker for
Phase 4 being considered "done."

Sequencing for next session:
1. **First**: Phase 4 follow-up (cascade directive primitive-name fix +
   classifier-side validation + regression test). BLOCKING per Neo.
2. **Second**: adapter hint-message rewrite (option a — tool-agnostic).
3. **Third**: Phase 3 (spec-validator temperature pin).
4. **Fourth**: another database-foundation discriminating run with
   ALL of (1)-(3) shipped, to confirm Phase 4 mechanical sourcing
   actually fires under live conditions.
5. **Fifth**: revisit consumer-audit registry plan rewrite (Neo's
   B2 opportunity-cost is now closer to satisfied — but B1 chain
   modeling and B3 P0 ordering still need design work).

The 24-test increment from Phases 1-4.5 brought the unit suite to 3897
passing. Phase 4 follow-up + Phase 3 will add a few more.
