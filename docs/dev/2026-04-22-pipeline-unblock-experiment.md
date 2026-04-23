# Pipeline Unblock Experiment Plan — 2026-04-22

**Status:** Planning → pre-experiment validation
**Driver:** 17 consecutive pipeline failures on `2026-03-28-database-foundation`
**Target:** `budget-brain-v2`
**Latest run:** `docs/pipeline-run-reports/2026-04-22-15-01-budget-brain-v2-2026-03-28-database-foundation.md`

> **Live progress tracked at `~/df-experiment/STATUS.md`**, outside any
> git repo so branch switches during trials don't obscure it. This doc
> holds frozen design decisions and analysis; progress checkboxes,
> trial results, and running notes live in STATUS.md.

## Problem framing

`database-foundation` (Postgres tenant RLS + `withTenantDb` middleware) has
failed 17 consecutive pipeline runs. The dominant failure mode across recent
runs is the test engineer producing scenarios that bypass the adapter
harness and write broken SQL:

- Parameterized `` tx`SET LOCAL app.tenant_id = '${x}'` `` — Postgres rejects
  `could not determine data type of parameter $1`.
- Peer-connection `current_setting('app.tenant_id')` — always returns `''`
  because SET LOCAL is tx-scoped to the app's own transaction.
- Owner-role RLS bypass (no `FORCE ROW LEVEL SECURITY`).
- Strict equality on `current_setting` returning `''` vs NULL ambiguity.

The classification hint at `darkfactory/adapters/postgres/adapter.py:121-138`
recognizes these patterns but stamps them `SPEC_AMBIGUOUS`, which routes them
into Oracle escalation instead of scenario regeneration. Result: every run
hits the same foot-guns and escalates. No progress across 17 attempts.

## Root cause analysis (three layers, evidence-cited)

1. **Harness-bypass is the immediate cause.** The adapter ships
   `assertTxGucEquals`, `assertTenantSeesOwnRows`, etc. Scenarios import the
   helpers (`iter-1/scenarios/.../tenant-context.test.ts:5`) but write raw
   broken SQL inside callbacks anyway (`tenant-context.test.ts:65, 150`).
   The callback-shaped API is a foot-gun: the caller *can* pass arbitrary
   SQL, and the test engineer reaches for what the AC text literally says
   (`SET LOCAL`), not the correct `set_config($1, $2, true)` form.

2. **Barrier blocks the test engineer from knowing the app's API shape.**
   The test engineer can't see impl source, so it can't write correct
   `arrange` steps. It guesses, and guesses wrong in consistent ways.
   Manifest `entry_points[].specifier` already bleeds file paths as a side
   channel — Track C Proposal 2 (`contract_signatures`) would extend this
   intentionally.

3. **Using the pipeline for a canonical pattern.** Postgres tenant RLS is
   well-documented (Supabase, PostgREST, Drizzle docs). The LLM knows the
   correct pattern from training data. Pipeline structure (spec-driven, role
   fragmentation, no pattern-naming handoff) suppresses that knowledge and
   forces re-derivation, which fails.

Full analysis in transcript history. See
`docs/pipeline-run-reports/2026-04-22-*.md` and earlier.

## Decisions (committed)

### Track A — micro-fixes to unblock the spec

Six mechanical changes that eliminate the immediate failure modes:

1. **Scratch-dir depth fix** — `scripts/lib/render_import_contract_phase.py:129-140`.
   Flatten `.dark-factory/scratch/resolution-check/<spec>/` to
   `.dark-factory/resolution-check/<spec>/`. The current path is 4 levels
   deep; scenarios are 3; manifest specifier `../../../` lands in
   `.dark-factory/` from scratch, breaking Import Contract.
2. **Specifier extension rewrite** — `render_import_contract_phase.py:166`.
   Manifest declares `import_extension: .js` but `entry_points[].specifier`
   fields use `.ts`. `_synth_scratch_file` reads `import_extension` into the
   dataclass but never applies it. Fix: rewrite specifier extension at
   scaffold time.
3. **Scenario-lint: `tx\`SET LOCAL ... ${...}\`` pattern** — add to
   `scripts/lib/scenario_lint.py`, reusing the regex from `adapter.py:121-138`.
4. **Scenario-lint: peer-client `current_setting('app.*')`** — same.
5. **Harness API split** — `adapters/postgres/contracts/ts/tx_guc.ts:35-44`.
   Replace `assertTxGucEquals(client, guc, expected, setupWithinTx)` with
   two narrower primitives:
   - `assertAppSetsTxGuc(client, guc, expected, appCallback)` — callback
     must invoke a named app symbol, not raw SQL.
   - `assertTxGucRoundTrips(client, guc, value)` — helper issues
     `set_config($1, $2, true)` internally.
   Validation #5 (below) confirmed zero correct callback callers exist;
   destructive refactor is safe.
6. **Archive-audit gate** — small check that errors if any destructive
   refactor would break scenarios with correct usage. Belt-and-suspenders
   for #5 and future splits.

### Track C Option B — safe subset only

Two low-risk Track C proposals survived adversarial review (rounds 1-3 with
neo). Ship only when Track A data is in.

- **Proposal 3** — reference examples under `adapters/<name>/references/`.
  Require source-pinned frontmatter (URL + SHA + reviewed-date). Zero leak of
  target-project info.
- **Proposal 4** — spec-writer mechanism-vs-observable rule. Pre-LLM regex
  gate in spec validator. Must ship with mechanism-token blocklist
  (`adapters/<name>/mechanism-tokens.toml`) or it's LLM-enforced-only, which
  paraphrase attacks defeat.

### Track C (deferred) — rejected for now

- **Proposal 1** (architect-named patterns) — taxonomy drift concern
  unresolved; defer until two specs show convergent pattern naming.
- **Proposal 2** (`contract_signatures`) — adversarial review concluded
  `P(net strengthening) ≈ 0.62` with 9 required mitigations. Governance
  overhead not justified by single-spec evidence. Revisit if 2+ additional
  specs exhibit the same integration-test-barrier-miss failure class.

### Pre-committed experiment design

Empirical test of whether Track A alone unblocks the spec. Three phases;
conditional escalation; control arm mandatory.

- **Phase 0** (N=3, control): pipeline `main` unchanged. Establishes
  current variance. If Phase 0 ships ≥2/3, ambient LLM improvement is
  doing the work and Track A's apparent efficacy is suspect.
- **Phase 0.5** (N=1, frozen scenarios): replay scenarios from a known-failed
  archive against Track A code. Separates scenario-variance from code-change
  efficacy.
- **Phase 1** (N=3, Track A): all six Track A changes on
  `experiment/phase-1-track-a` branch.
- **Phase 2** (conditional, N=3): + Proposal 4 with mechanism-token blocklist.
- **Phase 3** (conditional, N=3): + Proposal 3.

Decision rule (applied per phase, pre-committed):

| clean_ships / N | Verdict |
|---|---|
| ≥ 2/3 AND no known-bug recurrence in shipping trials | SUFFICIENT (ship this phase's delta, stop experiment) |
| 1/3 at N=3 | AMBIGUOUS (extend to N=6) |
| 0/3 | INSUFFICIENT (advance to next phase) |
| Any SPEC_SHIPPED with known-bug patterns in scenarios | Not a "clean ship" — lucky pass |

**Exit conditions:**

- Any phase SUFFICIENT → ship that phase's changes; stop.
- All phases INSUFFICIENT → spec is the problem, not the pipeline. Defer to
  manual spec rewrite.

## Progress tracker (snapshot)

> **Live version:** `~/df-experiment/STATUS.md`. The checkboxes below are
> the state at commit time; do not edit them to track progress — edit
> STATUS.md instead. This snapshot is for historical reference and for
> anyone reading this doc without access to the local experiment dir.

### Pre-experiment validation

- [x] Verify analyzer works against a real archive (smoke tested against
      `20260422-140413-...-e2d9548b`). Extracts outcome, iter count, gate
      metrics, classification counts, known-bug presence correctly.
- [x] Archive audit for `assertTxGucEquals` callers. **Result: 0 correct
      callback users.** Track A #5 is safe as destructive split.
      See transcript "Validation #5" section.
- [x] Retrospective lint check on archived scenarios. **Result: 3 proposed
      rules fire on all recent failures; 4th rule (strict_null_empty)
      verified via analyzer smoke.** One false-positive on `rls.ts` error
      message string — scope-limited to scenarios in production.
- [x] Confirm `config.toml` preserve trick works (scripted cp/mv around
      `init --update`). Required because non-interactive path clobbers
      config.toml — see `darkfactory/setup/init.py:125-134`.
- [x] Confirm terminal-event vocabulary is exactly
      `{SPEC_SHIPPED, SPEC_ESCALATED, SPEC_FAILED}`. No others emitted.
- [ ] Unit tests for new `render_import_contract_phase.py` depth + extension
      paths (requires Track A code).
- [ ] Unit tests for new `scenario_lint.py` rules (requires Track A code).
- [ ] Smoke pipeline run on a non-failing spec with Track A applied
      (requires Track A code + image rebuild).
- [ ] End-to-end archive-audit-gate check against all 17 archives
      (requires Track A #6 implementation).

### Track A implementation

- [ ] Branch `experiment/phase-1-track-a` off main.
- [ ] #1 scratch-dir flatten (`render_import_contract_phase.py:129-140`).
- [ ] #2 specifier extension rewrite (`render_import_contract_phase.py:166`).
- [ ] #3 scenario_lint rule for `SET LOCAL ${x}`.
- [ ] #4 scenario_lint rule for peer-client `current_setting('app.*')`.
- [ ] #5 harness split (`contracts/ts/tx_guc.ts`, update `index.ts`).
- [ ] #6 archive-audit-gate implementation.
- [ ] Unit tests for each change.

### Experiment infrastructure

- [x] `scripts/experiment/experiment-phase-setup.sh` (with `config.toml`
      backup/restore).
- [x] `scripts/experiment/experiment-trial.sh` (terminal-event regex
      narrowed to the three real events).
- [x] `scripts/experiment/analyze_trial.py` (pass_rate pulled from
      `GATE_COMPLETED` events, not `final/meta.json`).
- [x] `scripts/experiment/experiment-aggregate.py` (phase-level decision
      rule evaluator).
- [ ] Cost measurement from recent archives (token + wall-time per run)
      before committing budget to Phase 0.

### Experiment execution

- [ ] Phase 0 (control, N=3).
- [ ] Phase 0.5 (frozen scenarios, N=1).
- [ ] Phase 1 (Track A, N=3).
- [ ] Phase 2 (+ Proposal 4, N=3, conditional).
- [ ] Phase 3 (+ Proposal 3, N=3, conditional).
- [ ] Final aggregation + experiment report (commit one report, not per-trial).

## Key source references

Used repeatedly in analysis; pinned here for quick lookup.

| File | Role |
|---|---|
| `scripts/lib/render_import_contract_phase.py:129-140` | Scratch dir depth bug (Track A #1) |
| `scripts/lib/render_import_contract_phase.py:166` | Specifier extension rewrite (Track A #2) |
| `scripts/lib/scenario_lint.py` | Target for Track A #3, #4 (new rules) |
| `darkfactory/adapters/postgres/contracts/ts/tx_guc.ts:35-44` | Harness API to split (Track A #5) |
| `darkfactory/adapters/postgres/contracts/ts/isolation.ts:45, 92` | Correct `set_config` pattern reference |
| `darkfactory/adapters/postgres/adapter.py:121-138` | Classification hint regexes (lint rule source) |
| `darkfactory/pipeline/classification_hints.py` | Classification hint application (per-describe-body) |
| `darkfactory/pipeline/iteration.py:1151-1162` | Category vocab (closed set) + rollup priority |
| `darkfactory/pipeline/iteration.py:1627-1629` | Escalation branch (count-majority) |
| `darkfactory/setup/init.py:125-134` | `init --update` non-interactive config.toml clobber |
| `darkfactory/spec/manifest.py:197, 910-923` | `import_extension` field + validator |

## Known bug patterns (decision-rule inputs)

Pinned in `scripts/experiment/analyze_trial.py` `KNOWN_BUGS`:

1. `peer_client_guc_read` — `postgres(process.env.X) + current_setting('app.*')`
2. `parameterized_set_local` — `` tx`SET LOCAL ... ${...}` ``
3. `owner_bypass_missing_force` — `ENABLE ROW LEVEL SECURITY` without `FORCE`
4. `strict_null_empty_guc_check` — `current_setting('app.*') === null` or `=== ''`

A "clean ship" requires zero files with any of these patterns in the winning
trial's scenarios.

## Open questions / deferred decisions

- **Does Proposal 4 (mechanism-observable rule) hurt more than it helps?**
  Removes lexical hooks (`SET LOCAL`) that activate training-data knowledge.
  Answer depends on whether Phase 2 ships cleanly or degrades.
- **Is `SCENARIO_BUG` category worth the plumbing cost?** 5-7 file changes
  across `iteration.py`, `adapters/base.py`, `agents/oracle.md`,
  `agents/defect-classifier.md`, rollup priority. Only worth it if Track A
  is insufficient and we need to route classified scenario bugs to
  regeneration, not escalation.
- **Blueprint library for canonical infra patterns?** Considered and rejected
  in this round. Blueprints are a workaround for "pipeline can't use
  training-data knowledge"; better to fix the pipeline. Revisit only if 2+
  specs show the same "canonical pattern not applied" failure.
- **What cost is real?** Haven't measured per-trial cost from recent archive
  token counts. Guess: $40-80/trial, $600+ worst case for full experiment.
  Must verify before Phase 0.

## References to transcript-only analysis

Conversation with neo across rounds 1-4 covered:

- Adversarial barrier review of Track C (neo agent `acc54091e2ba0dabe`).
- Round 3 adversarial review with probabilistic verdict
  (`a8a0a345044dc8cc1`).
- Experiment protocol review (`ab9b731e1f64b5b1e`).
- Pre-experiment verification assumptions (`ad1602e4b5625b53b`).

These rounds drove the 9-mitigation requirement for Track C Option A (which
we deferred) and the pre-committed decision rule. Not persisted as
individual reports; cited here so future sessions know they happened.
