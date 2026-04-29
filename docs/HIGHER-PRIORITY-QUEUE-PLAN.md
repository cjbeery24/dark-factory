# Higher-Priority Queue — Implementation Plan

**Status**: Neo-approved with changes (2026-04-28). Starting Phase 1.
**Author**: Caleb (drafted with Claude Code).
**Motivation**: Neo named these four items in the
`docs/CONSUMER-AUDIT-REGISTRY-PLAN.md` deferred-stub as the work that
must ship before the consumer-audit registry rewrite is feasible.

## Goal

Ship the four items in Neo's higher-priority queue:

1. **Gap 3 follow-up** — untracked-file capture in
   `capture_iteration_diff` + capture `iteration_end_sha` at gate time
   so escape-hatch paths produce patches.
2. **Deferred bug #2 (holdouts.md not consumed)** — thread holdouts.md
   content into Oracle prompt OR reviewer prompt.
3. **Deferred bug #1 (spec_validator wire-language FP + non-determinism)**
   — prose-vs-prescription detection + tame validator variance.
4. **Database-foundation discriminating re-run** — preceded by a
   mechanical injection of bootstrap_check `evidence` verbatim into
   the cascade-summary rendering. Disambiguates spec-vs-implementer
   from the 2026-04-28 13:50 non-ship.

## Sequencing (Neo-corrected, expanded after Phase 5)

**Original arc** (shipped commits `5b02732`, `8351e5b`, `996243a`, `22bfeee`):
`Phase 1 → Phase 2 → Phase 4 → Phase 4.5 → Phase 5 → Phase 3`

**Post-Phase-5 expansion** (Neo critical review of run + spec contradiction
findings, 2026-04-29) — items 6-10 added:

`Phase 6 → Phase 7 → Phase 3 → Phase 3.5 → Phase 5b`

| # | Phase | Status | Why |
|---|-------|--------|-----|
| 1 | Gap 3 follow-up | ✅ shipped (`5b02732`) | |
| 2 | holdouts.md consumer | ✅ shipped (`8351e5b`) | |
| 4 | cascade evidence injection | ✅ shipped (`8351e5b`) — but mechanical path UNFIRED in production | |
| 4.5 | fixture-based test | ✅ shipped (`8351e5b`) | |
| 5 | discriminating re-run | ✅ ran (`996243a`, amended `22bfeee`) — SHIP_WITH_GAPS escape | |
| **6** | **Phase 4 follow-up: classifier primitive-name fix** | 🚧 BLOCKING | Phase 4's mechanical canonical-sourcing missed in production because classifier emitted "bootstrap_check" not "substrate-bootstrap-not-applied". Fix `_CASCADE_DIRECTIVE_TEMPLATE` + add post-classifier validation + regression test using Phase 5 defects-iter1.json as negative fixture. |
| **7** | **Adapter hint-message rewrite** | 🚧 BLOCKING | postgres `_check_bootstrap` hint says "comment-only baseline never creates the tracking table" — mechanistically wrong. drizzle-kit's `ensureMigrationsTable` runs only when applying journal entries. Rewrite to tool-agnostic ("state observed gap, refuse to speculate"). |
| **3** | **spec-validator temperature pin** | 🚧 last in original arc | Score variance 75-98 across 6 runs; verdict PASS-stable. Option (i) — temperature pin only. Defer (ii)/(iii). |
| **3.5** | **NEW: cross-section consistency checks** | 🚧 NEW (Neo 2026-04-29) | The spec that just shipped contains a load-bearing FR-3 §62 vs §70/AC-24/EC-11 contradiction NEITHER the validator NOR the densifier caught. Every existing validation surface is shape-based or word-pattern-based; none reason over semantic consistency. Two-layer fix: densifier Step 4.5 (catches at authoring time) + validator Consistency Check promoted from one bullet to full procedure with claim ledger + status field. |
| **5b** | **discriminating re-run** | 🚧 after 6+7+3+3.5 | With cascade primitive-name fixed, hint message tool-agnostic, validator temperature pinned, and consistency checks present, run database-foundation again to confirm Phase 4 mechanical path fires AND that consistency checks catch contradictions in real specs. |

**Why Phase 3 last (Neo's SF1)**: spec_validator is highest-effort and
lowest-current-impact (PASS verdict stable; only score noisy). Phase 5's
outcome may make option (ii)/(iii) obviously needed or obviously moot.
Doing it earlier risks 1-2 sessions of work the run renders retrospectively
unnecessary.

## Linus checkpoint cadence (Neo-corrected)

Three passes total, not five:
- After Phase 1 (Gap 3 follow-up — single commit, two changes)
- After Phase 2 + Phase 4 + Phase 4.5 bundled (helper additions + cascade
  rendering tweak + fixture-based unit test — small surface)
- After Phase 3 (last)

Neo design pass at the START of each phase per standing rule. Phase 5
is a pipeline run, not code — Linus N/A; Neo writes the run-report
review at the end.

## Non-goals

- The consumer-audit registry. Explicitly deferred until items 1-4 ship.
- Spec amendments to the target project (`bb-v2/.dark-factory/specs/`).
  Per memory `feedback_no_manual_project_edits.md`.
- Refactoring `_capture_iteration_end_sha`'s three other consumers
  (B1 idle check, Oracle diff stat, ref pinning). Per Neo's prior
  Gap 3 mid-run review: keep surgical scope.

## Phasing

### Phase 1: Gap 3 follow-up (single commit)

**Two sub-items in one commit since they share the same surface:**

**1a. Untracked-file capture in `capture_iteration_diff`.**
- After `git diff` succeeds in `darkfactory/git/ops.py:capture_iteration_diff`,
  run `git ls-files --others --exclude-standard -- <pathspecs>` against
  `project_dir`.
- For each untracked file matching the include filter (and not matching
  `_EXCLUDE_PATHSPECS`), append a synthetic block to the diff:
  ```
  diff --git /dev/null b/<path>
  new file mode 100644
  --- /dev/null
  +++ b/<path>
  @@ -0,0 +1,<N> @@
  +<line 1>
  ...
  ```
- Bound per-file size at 200 KB before the existing 1 MB cap kicks in.
  Binary files: emit `Binary files differ` instead of content.

**1b. Fallback SHA capture in `snapshot_iteration_diff_for_completed`
(Neo SF2 — pick option B explicitly).**
- Do NOT move `_capture_iteration_end_sha` from `_step_oracle`. Moving
  it touches three other consumers' contracts (B1 idle check, Oracle
  diff stat, ref pinning) — different commit, different review surface.
- Instead: in `snapshot_iteration_diff_for_completed`, when the resolved
  `end_sha` is empty (i.e. `_step_oracle` was bypassed via escape-hatch),
  attempt a fresh capture against the working tree at archive time.
- Add a one-line comment naming the rejected alternative (gate-time
  capture move) so a future maintainer knows why we picked the fallback.

**Tests:**
- `tests/unit/test_git_ops.py::TestCaptureIterationDiff` extensions:
  - `test_untracked_files_appended_as_synthetic_blocks`
  - `test_untracked_binary_file_emits_binary_marker`
  - `test_untracked_files_excluded_per_pathspec`
  - `test_untracked_files_above_per_file_cap_truncated`
- `tests/unit/test_archive.py::TestSnapshotIterationDiffForCompleted`
  extension:
  - `test_fallback_captures_when_end_sha_empty` — simulate
    `_step_oracle` not called, assert patch still produced.

**Reviews:**
- Neo design pass: confirm option B ordering and Phase 1a's working-tree-
  vs-end_sha read concern.
- Linus code pass: subprocess hazards (binary handling, large untracked
  file streaming, ls-files exit codes), pathspec filter on untracked
  paths, test honesty.

**Effort**: 1 session.

### Phase 2: Deferred bug #2 — holdouts.md consumer

**Investigation step (before coding):**
- Read `qa-holdout` agent's output contract — what does it write into
  `holdouts.md`?
- Read existing write site at `orchestrator.py:3429`.
- Decide consumer: Oracle prompt (changes ship/no-ship calculus) vs
  Reviewer prompt only (changes review depth).
- Decision criterion: does adversarial-coverage info change whether a
  90% pass rate should ship? If yes, Oracle. If no, Reviewer.

**Implementation:**
- New helper in iteration.py: `_format_holdouts_inline(project_dir,
  spec_name)` — parallel pattern to `_format_substrate_findings_inline`
  and `_format_defects_inline`. Reads holdouts.md, returns prompt-ready
  block, bounded length.
- Wire into chosen consumer's prompt construction site.

**Tests:**
- `TestFormatHoldoutsInline`: missing-file, normal, truncated.
- Chosen-consumer extension: `test_holdouts_block_present_when_file_exists`.

**Reviews:** bundled with Phase 4 + 4.5 for one Linus pass.

**Effort**: 1 session.

### Phase 4: Discriminating-run prep (Neo-rescoped — mechanical injection)

**Neo B1 correction**: the 13:50 run showed the **classifier already
preserved** the bootstrap_check message verbatim in `defects.json`
(report lines 221-225). The information loss was at the **Oracle's own
paraphrase** — the Oracle saw the message and wrote "the migration
system" instead of quoting it.

**Therefore the fix is mechanical injection at the rendering site, NOT
a prompt-template tweak at the classifier or Oracle.** Prompt-instructions
asking the Oracle to "quote verbatim" are exactly the failure mode this
fix is trying to fix.

**Implementation:**
- In `_format_cascade_summary` (iteration.py): when an
  `upstream_cause.evidence` field is present, render it verbatim as
  part of the cascade-row output. New row format:
  ```
  - **substrate/bootstrap_check** — cascades to N test(s):
    Evidence: <evidence string from upstream_cause, quoted verbatim>
    Tests: `t1`, `t2`, ... +K more
  ```
- Optionally also: in `_format_defects_inline`, surface the per-defect
  `upstream_cause.evidence` inline alongside the existing `(upstream:
  stage/primitive)` tag.
- Rationale comment at the call site naming the failure mode this
  guards against (Oracle paraphrase removing fix mechanism).

**Reviews:** bundled with Phase 2 + 4.5 for one Linus pass.

**Effort**: 0.5 session (small, focused change).

### Phase 4.5: Fixture-based prompt-construction evidence test (Neo B2)

**Neo's R5-done-right.** Before burning a 90-minute live run, prove
mechanically that the evidence flows through the new code path.

**Implementation:**
- Save the 13:50 run's `defects-iter1.json` and
  `substrate-findings.json` as test fixtures under
  `tests/unit/fixtures/13_50_run/`.
- New test class `TestBootstrapCheckMessagePropagation`:
  - `test_evidence_field_appears_verbatim_in_cascade_summary` — load
    fixtures, call `_format_cascade_summary`, assert the
    bootstrap_check `message` substring appears verbatim in the
    output.
  - `test_evidence_field_appears_in_oracle_prompt` — load fixtures,
    construct the Oracle prompt via `_step_oracle`'s prompt-build
    path (or refactored to be testable), assert the message appears
    verbatim.
  - `test_evidence_appears_in_iter2_escape_hatch_directive` — the
    13:50 run's iter-2 escape-hatch directive inlined iter-1's
    feedback verbatim. With the Phase 4 change active, the inlined
    feedback should now contain the bootstrap_check message
    verbatim.

**If these tests fail with Phase 4 active**: Phase 4 didn't actually
solve the problem; rethink before Phase 5.

**If these tests pass**: Phase 5 has at least the floor evidence that
the message reaches the implementer's prompt — actionability becomes
the only remaining open question.

**Reviews:** bundled with Phase 2 + 4 for one Linus pass.

**Effort**: 0.5 session.

### Phase 5: Discriminating re-run + report

**The actual experiment.** Per the `/df-pipeline-run` skill but with:
- Phase 0 reset (clean tree, prior implementer output gone)
- Steps 1-4 unchanged
- Step 5 launch — same spec (`2026-03-28-database-foundation`)
- Step 6 monitor as usual
- Step 7 report focuses on:
  - Did Phase 4's mechanical injection put bootstrap_check evidence
    verbatim into the implementer's iter-2 prompt? (Phase 4.5 already
    answered "yes" at the unit level — Phase 5 confirms at the live
    level.)
  - Did the implementer act on it?
  - Did Gap 3's fix produce per-iter patches for ALL iterations
    (including escape-hatch ones)?
- Step 8 commit report.

**Accept any outcome as evidence:**
- Spec ships → strong validation.
- Spec doesn't ship but implementer's prompt carries the verbatim
  message → spec is genuinely ambiguous; spec-author work needed
  (out of scope).
- Spec doesn't ship and message gets dropped despite Phase 4 → Phase
  4 is wrong; Phase 4.5 should have caught this — investigate test
  honesty.

**Effort**: 1 session.

**Neo writes the run-report review.** Linus N/A (no code change in
this phase).

### Phase 6: Phase 4 follow-up — classifier primitive-name fix (BLOCKING)

**Surfaced by Phase 5 discriminating run (2026-04-29)**: classifier
emitted `upstream_cause.primitive: "bootstrap_check"` instead of
`"substrate-bootstrap-not-applied"` (the substrate-findings.json
`code` field). `_load_canonical_upstream_messages` keyed on the
finding `code`; lookup missed; Phase 4's mechanical canonical-sourcing
path did not fire. Canonical message reached the Oracle via the OLDER
`_format_substrate_findings_inline` path (commit `c68b3f8`). Phase 4's
load-bearing claim is currently unvalidated under live conditions.

**Root cause**: `_CASCADE_DIRECTIVE_TEMPLATE` example value said
`'bootstrap_check'` (the substrate-primitive name from the spec, NOT
the finding `code`). The classifier copied the example value verbatim.

**Fix scope**:
1. **Strengthen `_CASCADE_DIRECTIVE_TEMPLATE`** to instruct: "use the
   exact `code` field from the substrate-findings.json finding, NOT a
   paraphrased name. Examples of valid codes appear in the substrate-
   findings block of your prompt — copy a literal string from there."
   Replace or remove the misleading `'bootstrap_check'` example.
2. **Post-classifier validation in `_classify_once`**: when any
   defect carries `upstream_cause.primitive` for `stage="substrate"`
   that doesn't match a `code` in substrate-findings.json, emit a
   `crash_split_mismatch`-style cross-validation error. Retry loop
   re-prompts the classifier with the rule that fired.
3. **Regression test** using the Phase 5 run's `defects-iter1.json`
   (where primitive was `"bootstrap_check"`) as a NEGATIVE fixture:
   assert canonical-source lookup MISSES on it pre-fix; assert the
   validator REJECTS it post-fix.

**Neo design + Linus code review** before commit per standing rule.

**Effort**: 1 session.

### Phase 7: Adapter hint-message rewrite (BLOCKING)

**Surfaced by Phase 5 + Neo critical review (2026-04-29)**: the
postgres adapter's bootstrap_check primitive at
`darkfactory/adapters/postgres/adapter.py:1149-1156` says the failure
mode is "drizzle-kit migrate against a comment-only baseline never
creates the tracking table." The implementer (correctly reading this)
added `SELECT 1;` to baseline.sql. The check still failed. **The hint
mechanism is wrong**: drizzle-kit's `ensureMigrationsTable` runs only
when applying journal entries — `SELECT 1;` doesn't change journal
entries.

The adapter file itself documents at line 1091 "Adapter Python
deliberately holds zero migration-tool knowledge". The current hint
violates that contract.

**Fix**: rewrite to tool-agnostic framing. Option (a) per Neo's review:

```python
message=(
    f"bootstrap_check {name!r} did not observe expected post-condition: "
    f"SQL `{check}` returned no rows. The Setup phase exited 0 but the "
    f"expected post-condition was not satisfied. Investigate whether the "
    f"migration tool actually applied any work."
)
```

Refuses to speculate on cause. Restores the "tool-agnostic by design"
contract.

**Tests**: pin the new message text + assert it does NOT name any
tool-specific mechanism.

**Neo design + Linus code review** before commit.

**Effort**: 0.5 session (small, focused change).

### Phase 3: Deferred bug #1 — spec_validator wire-language FP + non-determinism (option i locked)

**Re-evaluation triggered by Phase 5's outcome.**

If Phase 5 produced a stable PASS verdict despite re-running:
- Option (i) is sufficient — pin temperature on the spec-validator
  agent and ship.
- Skip (ii)/(iii) until verdict starts flipping.

If Phase 5 surfaced a verdict flip or a new false-positive on the
re-run:
- Promote to option (ii) — move the deterministic checks (regex,
  structural) out of the LLM and into a mechanical validator.
- Option (iii) (full mechanical move for the wire-language family)
  remains deferred unless the evidence forces it.

**3a. Prose-vs-prescription detection in wire_language_rule_1.**
- Investigate where the regex lives.
- Regex must NOT fire inside scopes that signal description (rationale
  headings, forbidden code blocks, comment lines).
- Tests pinning that AC-37 (the run-4 reproducer from
  `project_database_foundation_deferred_bugs.md`) does NOT trigger.

**3b. Tame validator non-determinism (option choice locked AFTER
Phase 5 outcome).**

**Reviews:**
- Neo at start: lock the option choice based on Phase 5 evidence.
- Linus at end: regex correctness on prose-vs-prescription scopes.

**Effort**: 1-2 sessions depending on option lock.

### Phase 3.5: cross-section consistency checks (NEW, 2026-04-29)

**Motivation**: Phase 5's run shipped a spec containing a load-bearing
contradiction NEITHER the validator NOR the densifier caught:

- FR-3 §62 (line 62 of bb-v2 spec): "The baseline migration itself
  creates no tables and applies no policies"
- FR-3 §70 (line 70): "The `__drizzle_migrations` tracking table is
  created in the database when the baseline migration is applied"
- AC-24, AC-25, EC-11: all reference `__drizzle_migrations` records
  the baseline must produce

The contradiction was **latent in the original prose** (FR-3 §62 was
hand-written that way) and **made testable by densification** (which
added AC-24 / EC-11 / FR-3 §70 expanding the side effects).

**Neo's diagnosis**: every existing validation surface — precheck.py,
spec-validator agent, `/df-write-spec`, `/df-densify-spec` — is
shape-based or word-pattern-based. None reason over semantic
consistency. The spec-validator's "Consistency Check" is one bullet
without procedure, no rubric weight, no scoring entry. The densifier's
mandate is "produce 30-50 ACs," not "verify the parent FR is
internally consistent before fanning out."

**Fix scope** (two layers):

**3.5a. Densifier Step 4.5** (catches at authoring time, where the
contradiction is born):
- File: `.claude/skills/df-densify-spec/SKILL.md`
- After densifier produces new ACs, walk each AC and re-read its
  parent FR's prose (`Processing` + `Side effects` + `Output` fields).
- For each AC, ask: "Does this AC contradict any clause in its parent
  FR or any sibling AC of the same FR?"
- Emit structured JSON. Hard-stop on contradiction; present to user
  before writing.

**3.5b. Validator Consistency Check promotion** (safety net for hand-
written contradictions and post-densification edits):
- File: `agents/spec-validator.md`
- Promote "Consistency Check" from one bullet to full procedure with:
  - Per-FR claim ledger (every claim from `Processing` / `Side
    effects` / `Output`, plus every THEN clause from tagged ACs, plus
    every expected behavior from referencing ECs)
  - Walk the ledger; flag contradicting pairs
  - New `consistency_score` row in the Completeness Checklist that
    contributes to `blockers` on FAIL
  - Machine-readable `consistency=PASS|FAIL` field in the `.status` file

**3.5c. Multi-fixture regression suite** (avoid tuning to one
contradiction):
- ≥3 contradiction fixtures: real FR-3 case + 2 synthetic
- Synthetic fixtures: hand-corrupt fresh densified specs to introduce
  AC-vs-FR, AC-vs-AC, EC-vs-EC contradictions
- Run upgraded densifier ≥5 times against each fixture; require ≥4/5
  catch rate with the same diagnostic anchor before declaring shipped
- Same protocol for the validator Consistency Check

**3.5d. Regression run on bb-v2's pre-densified spec**:
- Reset `bb-v2/.dark-factory/specs/2026-03-28-database-foundation.md`
  to commit `18f8239` ("rewrite to observable behavior" — pre-densify)
- Run upgraded `/df-densify-spec` against it
- Assert Step 4.5 catches the FR-3 contradiction during densification
- **DO NOT amend bb-v2's spec.** Regression asserts "tooling detects
  the bug," not "tooling fixes the bug." Per `feedback_no_manual_project_edits.md`,
  spec amendment is a separate human-curation task on the target
  project.

**Neo design + Linus code review** before commit. Densifier changes
are LLM-prompt; validator changes are LLM-prompt + status-field plumbing.
Both reviews care about prompt-template honesty (S5 from Phase 2+4+4.5
review) — make sure the new directives actually structure the work,
not just describe it.

**Effort**: 2-3 sessions.
- Session 1: Step 4.5 + validator promotion + structural tests
- Session 2: build 3 fixtures + run 5x reproducibility suite
- Session 3 (possibly): apply to reset bb-v2 spec, capture results

## Estimated total

5-6 sessions. Phase 1 first, Phase 5 fifth, Phase 3 last.

## Risks

**R1 — Phase 4 mechanical injection conflicts with cascade-summary
brevity.** Adding verbatim evidence per cascade row increases prompt
size. Bound by the existing `_INLINE_MAX_CHARS` cap; Phase 4.5 tests
confirm the message reaches the prompt without overflow.

**R2 — Phase 5 budget overrun.** The 13:50 run took 83 minutes. Phase
1's fallback SHA capture adds negligible runtime; Phase 4's mechanical
injection is a render-time string operation. No expected regression.
Hard cap remains 90 minutes per the skill.

**R3 — Phase 3 option choice forced too aggressive.** If Phase 5's
spec_validator produces a verdict flip, the temptation is option (iii)
(full mechanical). Mitigation: Neo design pass at start of Phase 3
must justify (iii) over (ii) explicitly; (ii) is the right middle.

**R4 — Phase 5 spec STILL doesn't ship.** Plausible. The non-ship
becomes useful evidence when paired with Phase 4.5's prompt-evidence
test — together they isolate spec-quality from framework-quality.

## Acceptance criteria

The arc is "shipped" when:

1. ✅ Phase 1 commit lands; iter-1/2/3 all produce patches in Phase 5's
   run.
2. ✅ Phase 2 commit lands; holdouts.md content reaches its chosen
   consumer (verified via test + visible in Phase 5's prompt).
3. ✅ Phase 4 commit lands; mechanical injection verified by Phase 4.5
   fixture tests.
4. ✅ Phase 5 run produces a report attributing outcome to either:
   - SHIP (definitive validation), OR
   - NO-SHIP, with the report distinguishing "framework didn't carry
     the evidence" from "framework carried the evidence; implementer
     didn't act."
5. ✅ Phase 3 commit lands; AC-37 reproducer test passes; option (ii)
   or (iii) lands only if Phase 5 evidence justified it.
6. ✅ The arc's report explicitly evaluates whether each item helped.

## After this arc

If 1-6 hold, the consumer-audit registry rewrite (per the deferred
stub) becomes feasible. Re-read Neo's three blockers (B1 chain
modeling, B2 opportunity cost, B3 P0 ordering) before starting. B2 is
satisfied by this arc shipping; B1 and B3 still need design changes.

## Neo's review summary (2026-04-28)

Verdict: **approve-with-changes**. Two blockers (Phase 4 wrong hop,
Phase 5 tests wrong hypothesis) addressed by rescoping Phase 4 +
adding Phase 4.5. Three should-fix items addressed by reordering
Phase 3 to last, picking Phase 1b option B explicitly, bundling Linus
to 3 passes. Two nits dropped: terse-message conditional (over-
engineering), registry-seeding-as-we-go (wrong shape would create
rework).
