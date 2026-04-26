# Classification Flow Readiness — `scenario_author_retry_exhausted`

> ROADMAP.md Phase 2 B1-cls-audit deliverable. Verifies that the new
> abort classification Phase 2 B1 will introduce flows correctly
> through four downstream surfaces, or names the smallest fix needed
> if it does not.
>
> Read-only investigation. No code changed by this audit.
> Date: 2026-04-25.

## Background

ROADMAP.md Phase 2 B1 introduces a host-side `te_pre_acceptance_check`
phase between Scenario Architect emission and gate prep. On
exhaustion of the Test Engineer's pre-acceptance retry budget, the
pipeline aborts with classification `scenario_author_retry_exhausted`.
B1-cls-audit's job is to confirm the four downstream surfaces below
either accept the new classification cleanly or name the work
required.

## Surfaces audited

### (a) Abort routing — **READY**

**Anchor:** `darkfactory/pipeline/orchestrator.py:794-813`

The `(DarkFactoryError, BudgetExceeded)` catch (broadened from
`(HardAbort, BudgetExceeded)` by `6d4d0ec`/`a50cd1c` on 2026-04-21)
routes any `HardAbort` raise through `_handle_abort`, which calls
`snapshot_iteration` + `snapshot_final` and emits `SPEC_FAILED`.
A new B1-side raise of the form
`HardAbort("scenario_author_retry_exhausted: ...", error_code=...)`
will route correctly without orchestrator changes.

### (b) Oracle decision logic — **READY** (terminal before Oracle)

**Anchor:** Oracle invocation at `iteration.py:2884` (`_step_oracle`)
runs only after Implementer + gate + classification. The TE
pre-acceptance phase aborts before any Implementer attempt, so the
abort is structurally terminal-before-Oracle. No Oracle code path
needs to learn about `scenario_author_retry_exhausted`; the abort
exits via the orchestrator-level catch in (a) and never reaches
the Oracle step.

### (c) `analyze_trial.py` classifier — **READY**

**Anchor:** `scripts/experiment/analyze_trial.py:164-180`
(`classification_counts()`)

The classifier accepts arbitrary `category` strings — no enum, no
whitelist. New classifications surface as new keys in the output
JSON without code changes. The post-hoc analyzer will report
`classification_counts: {"scenario_author_retry_exhausted": N}`
once Phase 2 B1 lands and produces matching defect entries.

### (d) Failure-archive serializer — **NEEDS-WORK**

**Anchor:** `darkfactory/pipeline/archive.py:280-289`
(`snapshot_final` — meta.json construction)

`snapshot_final` writes `final/meta.json` with five fields: `spec`,
`run_id`, `status`, `final_iteration`, `completed_at`. The abort
reason (`spec_run.abort_reason`, set at
`orchestrator.py:4874` in `_handle_abort`'s upstream path; written
to the state file at `:4877`) is **not** propagated to the archive's
`meta.json`.

A post-hoc consumer reading the archive can determine that the run
aborted (`status: "aborted"`) but cannot distinguish a B1 abort
(`scenario_author_retry_exhausted`) from any other abort
(classifier crash, hard timeout, preflight violation, etc.). This
is a pre-existing gap that B1's exit criterion ("`B1-cls-audit`
findings either fixed or filed as separate tickets with explicit
rationale") flagged in advance.

**Smallest fix.** Two-line plumbing change:

1. Add `abort_reason: str = ""` parameter to `snapshot_final`
   (`archive.py:249`).
2. Conditionally include it in the meta dict: when
   `status == "aborted"` and `abort_reason` is truthy, set
   `meta["abort_reason"] = abort_reason`.
3. Pass `spec_run.abort_reason` from each `snapshot_final` call
   site in `orchestrator.py` (`:4611`, `:4854`, `:4902`). The
   value is already on `spec_run` at every call site.

Estimated work: 0.5 days including a regression test that pins the
serialized field.

## Findings summary

| Surface | Status | Anchor |
|---|---|---|
| (a) Abort routing | READY | `orchestrator.py:794-813` |
| (b) Oracle decision | READY (terminal-before-Oracle) | `iteration.py:2884` |
| (c) `analyze_trial.py` | READY | `analyze_trial.py:164-180` |
| (d) Archive serializer | **NEEDS-WORK** | `archive.py:280-289` |

## Disposition

Surface (d) is the only blocker. Two options for resolution
sequencing:

1. **Fold into B1 implementation.** B1's 2-week estimate already
   includes the `abort_reason` plumbing. This is the recommended
   path — the change is co-located with the new classification
   itself and ships in the same commit as B1's TE retry budget
   bookkeeping. ROADMAP.md Phase 2 exit criterion ("B1-cls-audit
   findings either fixed or filed as separate tickets") satisfied
   by the same commit.
2. **File as a separate ticket.** Land the meta.json fix
   independently before B1. Smaller blast radius, but adds a commit
   that is not load-bearing for any other phase. Not recommended
   absent a reason to land the fix early (e.g., another in-flight
   abort-classification work).

This audit recommends option 1: B1 owns the meta.json plumbing as
part of its retry-budget bookkeeping work.

The remaining three surfaces (a, b, c) require no work — Phase 2
B1 can proceed against them as designed.

## Memory entries verified during this audit

- `project_pipeline_unresolved_bugs.md` item #2 ("Abort path
  bypasses archive finalizer", marked RESOLVED 2026-04-21) — confirmed.
  `orchestrator.py:794` catches `(DarkFactoryError, BudgetExceeded)`
  as documented.
- `project_pipeline_unresolved_bugs.md` item #3 (Phase D serializer
  gap on `blocking_acs`/`blocking_ac_issues`) — orthogonal to this
  audit. The B1-cls-audit's serializer concern is on `meta.json`'s
  `abort_reason`, not on the precheck advisory's blocking IDs.
  Both are serialization gaps in different files and should be
  tracked separately.
