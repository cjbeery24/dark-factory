# 2026-04-28 — Three structural framework gaps fixed (Gap 3, Gap 1, Gap 2)

## What this is

Not a pipeline-execution report. This is a framework-changes report — the
three structural gaps queued at the end of the 2026-04-27 session
(commits `24a3dd9`, `99ef182`, `c68b3f8`, `9b70d95`, `62d487a`) were
implemented, reviewed by Neo + Linus per the standing rule, and shipped
as three commits on `experiment/phase-4-product-owner`:

- `5d7fb29` — Gap 3: per-iteration implementer diff captured into archive
- `1892d42` — Gap 1: substrate-prep emission record (schema-level non-claim)
- `e2b77a6` — Gap 2: upstream_cause schema + classifier directive

All three closed instances of the **persisted-but-not-consumed**
anti-pattern (see `~/.claude/projects/-Users-me-sites-dark-factory/memory/project_persisted_but_not_consumed_pattern.md`).
End-to-end pipeline validation against `database-foundation` is the
next discriminating experiment; this session was unit-test-only.

Test suite at session end: **3873 passing, 1 skipped, 207 subtests
passed** (vs the prior baseline of ~3824).

## Build order

Built **Gap 3 → Gap 1 → Gap 2** because downstream gaps consume
upstream artifacts and this order eliminated rework. The reasoning
held through the implementation.

## Per-gap summary

### Gap 3 — per-iteration diff capture (commit `5d7fb29`)

**Root cause.** `count_files_changed` (git/ops.py:252) discarded the
path list after summing into `iter_state.files_changed`.
`emit_files_changed` persisted only the count to events.jsonl. Iter-1
vs iter-2 implementer intent could not be diagnosed from existing
artifacts — only "implementer touched 17 files in iter-1 and 23 files
in iter-2" was knowable.

**Fix.** New `capture_iteration_diff` helper in `darkfactory/git/ops.py`:
runs `git diff <start>..<end>` with explicit pathspecs (source/test/
manifests INCLUDED; lockfiles, build artifacts, `.dark-factory/` tree
EXCLUDED). 1 MB cap → degrades to `--stat` + first/last 500 lines with
truncation marker. Defensive 2x ceiling drops the body if even the
truncated form exceeds it. New `snapshot_iteration_diff_for_completed`
in `darkfactory/pipeline/archive.py` wraps the helper and folds the
SHA-pair-resolution + capture into one call invoked from all four
`snapshot_iteration` sites (iter-loop top + ship/escalate/abort
handlers in orchestrator).

**Reviews caught.**
- Linus #1 (real bug): lockfile excludes used `:(exclude)package-lock.json`
  literal form — only matches repo-root file. Fixed to glob form
  `:(exclude,glob)**/package-lock.json` so monorepo nested lockfiles
  are also excluded.
- Linus #2: `_truncate_diff_for_archive` swallowed `--stat` failure
  silently. Added log.debug.
- Neo S2-A: footgun-removal in `diff_sha_pair_for_completed` — return
  `("", "")` when EITHER SHA is missing (single-source-of-truth contract).
- Neo S2-B: defensive byte-ceiling clamp on truncated assembly in case
  the truncated form itself exceeds the trigger cap.
- Linus #5: collapsed 4 call sites into one `snapshot_iteration_diff_for_completed`
  wrapper.
- Linus #3: promoted diff-capture-failed log from debug to info.

### Gap 1 — substrate-prep schema-level non-claim (commit `1892d42`)

**Root cause.** v1's `no_op: true` was a verification claim by the
agent, encoded in the schema. The agent's check was file-presence only
(`agents/substrate-owner.md` lines 38-46: "if package.json shows
drizzle-kit but `_journal.json` is absent, that's a gap"); the
post-condition (`__drizzle_migrations` table created after migrate)
was unobserved. Run 5b iter-1 emitted `no_op: true` while the gate
was about to FAIL substrate verification.

**Fix.** Schema v2 of `.dark-factory/architecture/substrate-prep/<spec>.json`:

- `claim` field REQUIRED, only legal value is `"none"`. Parser rejects
  any other value (including JSON `null`) with an explicit error so the
  agent is re-prompted with the rejection rule.
- `commands` → `commands_emitted`, `evidence` → `evidence_inspected`
  (disambiguation rename).
- `files_seeded` is forward-looking declarative (agent-asserted, NOT
  verified — documented explicitly in agent.md).
- `stage_exit` is **orchestrator-only** (not in agent emission). The
  agent has no channel to describe its own attempt status; giving it
  one would reintroduce the writer ambiguity that bit `no_op`.
- v1 emissions rejected outright via `schema_version` mismatch — no
  migration shim. Hybrid v1/v2 payloads (v1 field names alongside
  `schema_version: 2`) trigger explicit parse errors so the agent
  re-emits with the right field names rather than having data
  silently dropped.

`SubstratePrepResult` drops `no_op: bool`. A test pins absence of
`no_op` / `verified` fields — adding either back would require
deleting the assertion.

**Reviews caught.**
- Linus #1 (real bug): `agents/substrate-owner.md:154` still had
  residual v1 prose telling the agent to "emit `no_op: true` with
  `evidence`". Agent reads the whole file; first attempt would always
  be rejected. Scrubbed.
- Neo #1: my initial v2 had `stage_exit` in the agent emission with
  `"abandoned"` as a legal value — Neo flagged that as a verification-
  failure-claim channel, AND that the orchestrator was overwriting it
  on retries-exhausted. Same writer-ambiguity that bit `no_op`. Made
  `stage_exit` orchestrator-only.
- Linus #2: 5-tuple return from `_parse_agent_output` was past the
  readability cliff. Converted to `ParsedAgentOutput` dataclass.
- Linus + Neo: hybrid-payload tests originally asserted silent
  acceptance ("the v1 fields are dropped on the floor") with a
  docstring claiming the test pinned rejection. Either the test or
  the parser was lying — fixed both: parser now errors on v1 field
  names alongside v2 schema_version, and the test asserts the error.
- Linus #5 nit: JSON `null` for required fields untested. Added.

### Gap 2 — upstream_cause schema + cascade detection (commit `e2b77a6`)

**Root cause.** The defect-classifier had no input channel for upstream
evidence (`substrate-findings.json` and `pre-gate-summary.md` never
reached its prompt) and no schema slot for cascade detection. Run 5b
iter-2 produced 18 SPEC_AMBIGUOUS records all carrying
`POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION` in their rationale prose —
the classifier RECOGNIZED the cascade but had no structured channel
to express it.

**Fix.** Inline upstream evidence into the classifier prompt + add
optional `upstream_cause: {stage, primitive, evidence}` field to
each defect (additive, no `schema_version` bump). Cascade directive
prefers SCENARIO_CRASH over SPEC_AMBIGUOUS for cascading failures.

Oracle-side: `_format_cascade_summary` aggregates defects by
(stage, primitive). Renders when EITHER any group has ≥2 tests
(shared cause) OR ≥2 distinct causes appear (the "your substrate
is on fire in 5 different ways" structural signal). Per-defect
`(upstream: stage/primitive)` tag inline in the defect list.
Oracle-side role directive at the prompt-construction site
instructs collapse of N cascading entries into one fix-list line.
`agents/oracle.md` gets a new "Upstream Cascades (Gap 2 contract)"
section so the role-side contract is documented, not just rendered.

Did NOT add a 7th category (taxonomy fork ripples through Oracle,
gate renderer, escalation rules, post-mortem schemas — a six-month
failure mode per neo's prior review).

**Reviews caught.**
- Neo BLOCKER #1 (the recurring anti-pattern reappearing one layer
  up): I added the cascade summary rendering but the Oracle had no
  documented contract for acting on it. The directive lived only
  inside the helper's header text. Fixed by adding both a prompt-
  side directive at the call site (parallel to the existing
  `substrate_block` directive) AND a new agent.md section.
- Neo BLOCKER #2: no end-to-end test that classifier-written
  `upstream_cause` flows through `_format_defects_inline` to the
  Oracle prompt. Future refactors of `_step_oracle` could silently
  break cascade visibility. Added two tests.
- Linus #6a (real bug): malformed `upstream_cause` shapes (missing
  stage/primitive, empty dict, wrong type) would aggregate into a
  fake `?/?` bucket and produce a bogus "cascades to N tests" line.
  Now skipped from aggregation; per-defect inline tags still render.
- Linus #6b: `_format_pre_gate_summary_inline` didn't catch
  `UnicodeDecodeError`. Binary garbage in the summary file would
  crash the classifier step. Switched to `errors="replace"`.
- Linus #2 / Neo #4: ≥2 threshold suppressed the N-distinct-causes
  signal. Now renders block when EITHER any group ≥2 OR ≥2 distinct
  causes appear.
- Linus #7: log.debug when defects ARE tagged but nothing aggregates
  (operator visibility for the suppressed-signal case).
- Linus #1 nit: hoisted 30-line `cascade_directive` f-string to
  module-level constant.
- Linus #3 / Neo #3: `schema_version: 1` with new optional field
  contradicted agent.md's "monotonic — never overload it" clause.
  Amended agent.md to document the additivity policy explicitly.

## What did NOT change

- **No 7th defect category.** Per the user's plan and Neo's prior
  review: top-level taxonomy fork ripples through Oracle, gate-report
  renderer, escalation rules, post-mortem schemas. Six-month failure
  mode. The optional `upstream_cause` field is structured metadata
  about a defect's pre-conditions, not a category.
- **No iteration-loop cascade short-circuit** (task #57 from prior
  session). Out of batch — flagged in memory if substantive new
  evidence surfaces; otherwise deferred.
- **No consumer-audit registry.** The systemic fix Neo identified for
  the persisted-but-not-consumed pattern (a documented map of every
  persisted artifact to its expected consumers, enforced by a test).
  Targeted fixes shipped first; the registry remains the right
  systemic move and is still deferred.

## Reviews discipline

Standing rule from prior session: Neo design + Linus code review
BEFORE every framework commit. I broke this rule twice in the prior
session. This session: each of the three commits got both reviewers
in parallel before commit, applied substantive fixes from both, and
documented which review caught what in the commit message.

The reviews paid for themselves on every gap:

- Gap 3: Linus caught a real bug (lockfile glob form).
- Gap 1: Linus caught a real bug (residual v1 prose in agent.md).
  Neo redirected the design (orchestrator-only `stage_exit`).
- Gap 2: Neo caught a BLOCKER (Oracle had no contract for the new
  data — recurring anti-pattern). Linus caught a real bug (`?/?`
  aggregation footgun + missing `UnicodeDecodeError` catch).

## What's next

End-to-end pipeline validation. Recommended discriminating run:
re-execute `database-foundation` against `bb-v2` and check whether
iter-2 produces a Cascade Summary block instead of 18 individual
SPEC_AMBIGUOUS records. The unit tests pin the helper-level
behavior; only a live run will exercise the full classifier→Oracle
flow with real LLM-produced upstream tags.

The two remaining database-foundation deferred bugs (spec_validator
wire-language false-positive + holdouts.md not consumed in iteration
loop) are still open. See `~/.claude/projects/-Users-me-sites-dark-factory/memory/project_database_foundation_deferred_bugs.md`.

## Test suite

| Suite | Pass | Skip | Subtests |
|-------|------|------|----------|
| `tests/unit/` + `tests/adapters/` | 3873 | 1 | 207 |

Three new test classes from this session:

- `TestCaptureIterationDiff` (6 tests) + `TestSnapshotIterationDiff` +
  `TestSnapshotIterationDiffForCompleted` + `TestDiffShaPairForCompleted`
  (Gap 3, ~17 tests across `test_git_ops.py` + `test_archive.py`)
- `TestSchemaV2NonClaimEnforcement` + `TestParsedAgentOutputDataclass`
  + reworked `TestParseAgentOutput` (Gap 1, ~12 new tests in
  `test_substrate_prep.py`)
- `TestFormatPreGateSummaryInline` + `TestFormatCascadeSummary` +
  `TestFormatCascadeSummaryMalformedUpstream` +
  `TestFormatDefectsInlineUpstreamCause` +
  `TestStepClassifyCascadeContextInPrompt` + 2 new methods on
  `TestOracleDefectsAndGateOutcomes` (Gap 2, ~19 new tests in
  `test_iteration.py`)
