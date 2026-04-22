# Plan — Iteration Loop Steering Invariants

Source: observed during the post-fix-pack re-run of `2026-03-28-database-foundation`
(run `d7b0c5b54d684523`, 2026-04-22 18:09→19:08Z, ESCALATED in 3 iterations). The
six-task fix pack (commit `40f8e36`) hardened the gates. This run surfaced that
the iteration loop's steering signal is broken in two independent ways downstream
of the gates.

Diagnosis (confirmed by Linus + Neo adversarial review):

- **Post-gate pipeline ran on iter-1 only.** On iter-2 and iter-3 the iteration
  loop hit *four* pre-review escape hatches and skipped classifier, reviewer,
  oracle, and feedback composer entirely. No `AGENT_START role:{reviewer,oracle,defect-classifier}`
  events after iter-1. No `feedback-iter3.md` file exists. Docker audit log
  shows no post-gate containers for these roles after `18:50:16Z`.
  - Priority 1.5 self-test regression guard (`iteration.py:2315-2357`) — `continue`
  - R1 regression guard (`iteration.py:2359-2424`) — `continue`
  - B1 early-escalation at iter 2 (`iteration.py:2553-2569`) — `return False`
  - Trajectory early-escalation at iter≥3 (`iteration.py:2573-2598`) — `return False`
- **Cross-run artifact contamination.** Stale files from an 11-hour-older prior
  run survived into this run: `feedback-iter2.md` mtime `07:21:58Z`,
  `oracle-output-iter{2,3}.md` mtimes `07:22:10Z` / `07:39:15Z`. Iter-3 implementer
  ran 3 attempts × 600 turns × $126 against a stale "ENV_ISSUE — operator
  intervention required" feedback file from a prior run. The escalation agent
  read stale oracle outputs and produced a confabulated report (cited "16+
  iterations since April 20", review score 81/100, Install gate failed — none
  matched this run's state).

Root causes:
1. The two pre-review escape hatches above (three, counting Priority 1.5)
   short-circuit the post-gate pipeline without emitting any current-iteration
   feedback, leaving the next implementer (or the escalation agent) with no
   fresh inputs.
2. `.dark-factory/archived-runs/` (skill-owned) is a legacy duplicate of
   `.dark-factory/archive/` (pipeline-owned). The skill's Step 1b wipe list
   omits `reviews/`, so the skill's nominal cleanup misses feedback/oracle files.

## Implementation order: 1 → 2 → 3

Task 1 (the per-run cleanup invariant) is the load-bearing fix — it blocks
cross-run contamination at the pipeline layer and is sufficient on its own to
prevent the most severe observed failure mode ($126 blind-retry loop against
stale feedback). Task 1 can ship alone.

Task 2 (escape-hatch feedback invariant) is the follow-on — it closes the
within-run gap so iterations that exit via the three escape hatches still leave
fresh inputs for the next iter / escalation agent.

Task 3 (delete `archived-runs/`) is cleanup of a now-redundant legacy path.
Independent of Tasks 1 + 2; can ship any time after Task 1.

## Task 1 — `dark-factory run` enforces the per-spec artifact invariant at run start

**Fixes:** stale per-iter artifacts from prior runs of the same spec leak into
the current run and mislead the implementer and the escalation agent.

**How:** add a start-cleanup step to `dark-factory run`, invoked once before
the first agent container launches. Extend `darkfactory/pipeline/cleanup.py`
(or add `run_start_cleanup.py` alongside) — the existing module handles
process/resource cleanup, not artifacts. Call from the CLI entrypoint
(`darkfactory/cli.py` or wherever `run` resolves).

**Lossless by construction.** The pipeline's own `snapshot_iteration` (called
at the top of each iteration loop, `iteration.py:1973`, and at every terminal
handler — ship at `orchestrator.py:4565`, escalate at `4808`, abort at `4856`)
plus `snapshot_final` preserve per-iter artifacts under
`archive/<prior-ts>-<spec>-<short-id>/`. At the next run's start, everything
being purged is already in that archive. The cleanup reads only from the live
working tree; it does not touch `archive/`.

Files to purge (spec-scoped globs — no blanket wipe):
- `reviews/<spec>-feedback-iter*.md`
- `reviews/<spec>-oracle-output-iter*.md`
- `reviews/<spec>-review.md`
- `reviews/<spec>-escalation.md`
- `reports/<spec>-defects-iter*.json`
- `reports/<spec>-*-iter*.env`
- `reports/<spec>-substrate-findings.json`
- `reports/<spec>-quality-gate*.md`
- `reports/<spec>-smoke-test*.md`
- `reports/<spec>-pre-gate*.md`
- `reports/<spec>-review-prepass*`
- `reports/<spec>-scenario-validation.md`
- `reports/<spec>-scenario-counts*.env`
- `reports/<spec>-holdouts.md`

Files to preserve:
- `reviews/<spec>-validation.md` and `reviews/<spec>-validation.status` —
  pre-flight inputs read by `dark-factory run` itself. The in-pipeline
  spec-validator overwrites them at runtime.
- Everything under `architecture/`, `specs/`, `plans/`, `templates/`,
  `agents/`, `config.toml`, `project-hints.toml` — inputs, not outputs.

Emit a `RUN_START_CLEANUP` event to `events.jsonl` recording counts and
filenames purged, so post-mortem can audit what was swept.

**Pathological case (SIGKILL mid-run):** if a prior run was SIGKILL'd before
any terminal handler ran, per-iter snapshots taken at the next-iter loop top
(`iteration.py:1973`) still captured everything up to the last completed
iteration boundary. Only the in-flight mid-iteration state is lost — which is
not useful to the next run. Accept the edge; no rescue logic.

**Why not in the skill only:** the skill is orchestration convenience.
Correctness invariants belong in the CLI so programmatic `dark-factory run`
invocations (CI, power users, other orchestrators) get the same guarantee
the skill provides.

**Acceptance criteria:**
- Unit test: given a fake `.dark-factory/` seeded with each glob above plus
  each preserved file, cleanup deletes the globbed files and preserves
  `validation.md` / `validation.status` / `specs/` / `plans/` / `architecture/` /
  `scenarios/` / `config.toml` / `project-hints.toml`.
- Integration: a run against a seeded project emits exactly one
  `RUN_START_CLEANUP` event with `count > 0`.
- Back-to-back run check: run the pipeline twice on the same spec; assert no
  per-iter file in run 2's working tree has mtime older than run 2's start time.

## Task 2 — All three iteration-loop escape hatches write feedback + emit `ITER_COST` before exiting

**Fixes:** four escape-hatch paths currently skip the post-gate pipeline
without emitting current-iteration feedback:

- `iteration.py:2315-2357` (Priority 1.5, self-test regression) — `continue`
- `iteration.py:2359-2424` (R1, regression guard) — `continue`
- `iteration.py:2553-2569` (B1 early-escalation at iter 2) — `return False`
- `iteration.py:2573-2598` (trajectory early-escalation, PLATEAU at iter≥3) — `return False`

Result: iter-N+1's implementer prompt points at a non-existent
`feedback-iter<N>.md`, or — before Task 1 — reads a stale cross-run file.
The escalation agent has no current-iteration review to anchor on.

**How:** extract a helper
`write_escape_hatch_feedback(spec_run, iter_state, reason, project_dir)` and
call it from all four escape hatches before the `continue` / `return False`.

Also emit `ITER_COST` at the two `continue`-path sites (Priority 1.5 and R1)
before the helper call. These fire at `iteration.py:2315+` and `:2359+`, which
are *before* the normal `ITER_COST` emission point at `:2484-2492`, so the
`continue` skips it — this is exactly the iter-2 gap folded in from follow-up
G of the earlier plan. The two `return False`-path sites (B1 at `:2553+` and
trajectory at `:2573+`) fire *after* `:2484`, so `ITER_COST` has already been
emitted on those paths — no duplicate needed.

Helper contract: write `reviews/<spec>-feedback-iter<N>.md` with:
- Header identifying the reason — one of `SELF_TEST_REGRESSION`,
  `GATE_REGRESSION`, `B1_EARLY_ESCALATION`, `TRAJECTORY_PLATEAU`.
- Numeric delta block: `exit_code`, `pass_rate`, `scenario_total`,
  `satisfaction_score` for current-iter vs prior-iter (regression cases)
  or pass-rate history + trajectory classification + strategy recommendation
  (trajectory case).
- Directive appropriate to the reason:
  - Regression cases (Priority 1.5, R1): "Your changes regressed the gate
    outcome. The rollback restored iter N-1's source tree. Treat iter N-1's
    guidance (below) as your starting point; do NOT re-introduce the
    rolled-back changes."
  - Early-escalation cases (B1, trajectory): "Iteration did not converge
    (pass-rate history [...]; stable failing set [AC-IDs]). This run is
    escalating."
- Stable failure set as a count + list of AC-IDs (trajectory case), already
  computed at `iteration.py:2504-2516`.
- Inlined `feedback-iter<N-1>.md` contents, **if the file exists**. The
  implementer prompt on iter-N+1 reads only `feedback-iter<N>.md` (not N-1),
  so inlining is the only way to surface N-1's guidance.

**Edge case — no prior feedback:** `feedback-iter<N-1>.md` may not exist if
the prior iteration exited via the build-fail path at
`iteration.py:2604-2607` (`gate_result.exit_code == 2`, skip review/oracle,
`continue`). Handle gracefully: write header + numeric delta only; omit the
inlined block; note in the directive that no prior feedback is available.

**Barrier audit:**
- Spec-anchor vocabulary only (FR/AC/EC IDs + behavioral category). Pass-rate
  numbers, exit codes, and rollback git refs are safe — the implementer already
  sees the spec path.
- Inlined `feedback-iter<N-1>.md` was produced by the oracle under the same
  barrier rules, so re-inlining preserves compliance.
- No scenario names, no assertion messages, no internal classifier codes.

**Why not route regression through full classifier/review/oracle:** the
post-regression state is *worse* than iter N-1 (that's what "regression" means).
Re-running review + oracle on a state we just rolled back from wastes budget
and risks Oracle signing off on a regressed version. Synthetic pipeline-layer
feedback is cheaper, barrier-clean, and tells the implementer exactly what it
needs to know.

**Why merge all four hatches into one task:** four call sites, one invariant,
one helper. Splitting invents duplication.

**Acceptance criteria:**
- Unit test: force Priority 1.5 self-test regression at iter-2; assert
  `feedback-iter2.md` exists, contains `SELF_TEST_REGRESSION` reason, contains
  iter-1 numeric delta, contains inlined iter-1 feedback.
- Unit test: force R1 regression at iter-2; same assertions with `GATE_REGRESSION`.
- Unit test: force B1 early-escalation at iter-2; assert `feedback-iter2.md`
  exists, contains `B1_EARLY_ESCALATION` reason.
- Unit test: force PLATEAU trajectory at iter-3; assert `feedback-iter3.md`
  exists, contains `TRAJECTORY_PLATEAU` reason, contains pass-rate history.
- Unit test (edge): force R1 at iter-2 where iter-1 exited via build-fail
  (no `feedback-iter1.md`); assert graceful omission of inlined block, no crash.
- Barrier test: run the helper's output through the existing barrier scanner;
  assert no scenario names, no assertion messages, no file:line refs beyond
  the spec path.
- Events test: `ITER_COST` fires for every iteration that reached the gate,
  including iters that `continue`d through Priority 1.5 / R1. (B1 and
  trajectory paths already emit via the normal path at `:2484`.)

## Task 3 — `.dark-factory/archive/` is the single archival surface; delete `archived-runs/`

**Fixes:** `archived-runs/` is a legacy skill-owned flat snapshot that duplicates
(more poorly) what `archive/` captures per-iter. Keeping both creates two
sources of truth and places the "cleanup protocol" on the wrong layer. The
skill's archive loop omitted `reviews/`, which was the proximate cause of
this run's contamination — Task 1 fixes the invariant, Task 3 removes the
legacy path so the bug can't reappear there.

**How:**
1. Grep for any remaining `archived-runs` consumer — docs, agents, anchor-flag
   resolution in `dark-factory run`, CI scripts, V-dimension tooling. Expected:
   only the skill's Step 1a loop and the skill's "Anchor paths for V-dimension
   runs" table (SKILL.md lines 79-87). If anything else reads from
   `archived-runs/`, route it through `archive/<ts-spec-shortid>/` first.
2. Delete Step 1a's `cp -r` archive loop from
   `.claude/skills/df-pipeline-run/SKILL.md` (lines 46-71).
3. Update SKILL.md "Anchor paths for V-dimension runs" table (lines 79-87) to
   point at `archive/<timestamp>-<spec>-<short-id>/iter-1/architecture/...`
   paths.
4. Remove the `archived-runs/` directory from the budget-brain-v2 working tree
   (one-time, after step 1 verifies no consumers).

**Why not keep both:** two archival paths means contributors fix bugs in one
and miss the other. The skill's loop missing `reviews/` was exactly that bug.

**Acceptance criteria:**
- `grep -r 'archived-runs' ~/sites/dark-factory/` returns zero
  results outside `docs/plans/` historical notes and `docs/pipeline-run-reports/`.
- SKILL.md no longer contains the `cp -r` loop; the anchor-paths table points
  at `archive/` paths.
- Manual smoke: a fresh run on a project with a pre-existing `archived-runs/`
  directory succeeds and does not re-populate it.

## Follow-ups (filed, not in scope)

A. **Escalation agent read-set contract.** Restrict to
   `reviews/<spec>-feedback-iter*.md`, `reports/<spec>-*-iter*.json/env`, and
   `archive/<current-run-id>/` only — never unfiltered `reviews/` or `reports/`.
   Filter by `run_id` via state file at escalation-agent launch. Lower priority
   once Task 1 blocks cross-run contamination at the source.

B. **Substrate findings → feedback translation pass** (from
   `docs/plans/2026-04-22-database-foundation-escalation-fixes.md` lines 108-129).
   Translates `substrate-rls-table-missing` / `substrate-guc-unregistered` into
   impl-actionable AC-anchored bullets. Downstream of Task 2 — the synthetic
   regression feedback is the minimum; this is the quality improvement for the
   happy post-gate path.

C. **Iter-1 feedback composer "no hard gates failed" bug.** `feedback-iter1.md`
   in this run reports `"Non-Scenario Gate Failures: None - all hard gates passed"`
   while `GATE_COMPLETED exit_code=1` with Substrate FAIL, Import Contract FAIL,
   Blind Scenarios FAIL. Separate code-path bug — composer on first iteration
   is reading a pre-substrate gate-status field.

D. **Classifier defers to reviewer file:line anchors.** When reviewer output
   contains `file:line` patterns, classifier should prefer IMPL_BUG over
   SPEC_AMBIGUOUS. Today classifier runs before reviewer and feedback consumes
   classifier output only.

E. **Pass-rate 65 vs 69 discrepancy.** iter-2 `scenario-counts.env` says
   `SCENARIO_RATE=65 PASSED=29 TOTAL=44`; `GATE_COMPLETED` event says
   `pass_rate:69`. Divergent computation — find the second source of truth.

F. **Trajectory classifier requires stable suite size.** This run compared
   pass rates across `scenario_total` 12 → 44 → 12 and called PLATEAU.
   Refuse to classify or rebaseline when `scenario_total` changes.

G. **Plan-doc hygiene** on
   `docs/plans/2026-04-22-database-foundation-escalation-fixes.md` — flip Tasks
   40/43/44/41 from "next" to "shipped (validated in run d7b0c5b5 2026-04-22)".
