---
name: df-rescope-deferred
description: Apply the pipeline's substrate-deferral-consistency findings to a spec — re-scope anchors that assert a deferred (verifiable=false) capability. No hand-authoring.
argument-hint: "<spec-name>"
---

> **No Docker isolation.** Skills run in your local Claude Code session without
> container-enforced filesystem barriers. This skill edits
> `.dark-factory/specs/<spec>.md` — an input artifact consumed by later
> barrier-enforced pipeline agents. It never writes implementation code.

# Re-scope Deferred Anchors

## Purpose

The coverage-floor **deferral-consistency gate** (F / E2 / E3 in
`darkfactory/scenarios/coverage.py`) fails a spec when a `verifiable=false`
substrate primitive is deferred *asymmetrically*: the gate skips verifying it,
but the spec still asserts its runtime outcome through anchors that the Test
Engineer will write unsatisfiable scenarios for. The pipeline writes a
structured report naming exactly which anchors to re-scope and how.

This skill **consumes that report and applies the re-scope** — so the fix is
driven by the framework's own findings, never by hand-authored guesswork or
private knowledge of the stack. You transcribe the findings; you do not invent
which anchors are deferred.

## When to use

After a pipeline run (or `dark-factory run … --from-phase gate`) aborts with
`substrate_deferral_inconsistency`. The abort message points here.

## Pre-flight Checks

STOP with a clear error if any fails:

1. **Spec exists:** `.dark-factory/specs/<spec>.md`.
2. **Report exists:** `.dark-factory/reviews/<spec>-deferral-consistency.json`.
   If it is missing, the gate has not run / not fired — tell the user to run
   `dark-factory run <spec>` (or `… --from-phase gate`) first. **Do NOT
   hand-derive the findings** — the report is the only sanctioned input.

## Steps

### 1. Read the report

Load `.dark-factory/reviews/<spec>-deferral-consistency.json`. It is a list of
findings, each with `kind`, `primitive`, `anchor`, `message`. Kinds:

- `MISSING_VERIFIABLE_FALSE` — a substrate primitive (`primitive`) that defaults
  to gate-verified, but whose enabling infrastructure the spec defers. It must
  be marked `verifiable=false` (the ELICITATION gate; handled in step 2a, before
  the anchor re-scope).
- `INERT_DEFERRAL` (F) — a `verifiable=false` primitive with no `covers`.
- `BLOCKING_OVER_DEFERRED` (E2) — a covered-deferred anchor that is **Blocking**.
- `LIVE_SCENARIO_OVER_DEFERRED` (E3) — a covered-deferred anchor that is still a
  live Test-Engineer scenario target.

Group the anchor findings (E2/E3) by `anchor` (both can name the same anchor).

### 2a. Apply elicitation findings (`MISSING_VERIFIABLE_FALSE`) — mark the primitive

For each `MISSING_VERIFIABLE_FALSE` finding, locate the primitive's entry in the
spec's `## Substrate Primitives` fenced TOML block (the `[[<primitive>]]` table)
and add `verifiable = false` to it (if a type has multiple entries, add it to
the deferred one). Add a brief TOML comment stating the deferral is because the
enabling infrastructure is out-of-scope this phase — framework-/stack-agnostic,
no mechanism detail. Do NOT add `covers` (that is architect-owned). This is the
ROOT fix: it converts the primitive from "the gate will try to verify it and
fail late" to "explicitly deferred this phase." After this, the deferral gate
(step 2) may surface anchor findings on the next validate — that is expected and
correct (elicit `verifiable=false` → then re-scope the anchors that asserted it).

### 2. Apply the re-scope — strictly from the findings

For each **distinct anchor** named by an E2 or E3 finding, locate its row in the
spec's AC table (`| ID | Criterion | Blocking |`) or EC table.

- **If the anchor has an E3 (`LIVE_SCENARIO_OVER_DEFERRED`) finding** — set the
  row's 3rd-column (Blocking) cell to:

  ```
  @untestable: forward-reference — asserts the runtime outcome of the
  `<primitive>` substrate primitive, which is verifiable=false (deferred to a
  later build phase); becomes verifiable when that primitive lands.
  ```

  Substitute `<primitive>` from the finding. This excludes the anchor from
  Tier-2 scenario generation (the orchestrator strips `@untestable` /
  `vehicle:` anchors from the scored id lists) — so the TE stops writing a
  scenario the deferred capability cannot satisfy. The reason text comes from
  the finding's own rationale; do **not** add stack-specific mechanism detail
  (that is contamination — keep it framework-/stack-agnostic).

- **If the anchor has a `BLOCKING_OVER_DEFERRED` (E2) finding** — ALSO remove it
  from the **Non-Negotiable Acceptance Criteria** section (the `## …
  Non-Negotiable …` bullet list). A deferred anchor cannot be a ship-blocker
  this phase. Removing it is de-escalation (a blocking signal → non-blocking),
  which crosses no information barrier (see `docs/AUTHORIZATION-MODEL.md`).
  Delete only the bullet for that anchor; leave the rest of the section intact.
  If removing it empties the section, leave a one-line justification paragraph
  rather than an empty section (the validator requires one or the other).

- **`INERT_DEFERRAL` (F) findings are NOT fixed here.** `covers` is
  architect-owned (the architect infers it on the next run — see the postgres
  `architect_addendum.md`). An F finding on a fresh run means the architect
  failed to emit `covers`; report it to the user as an architect-output issue,
  do not add `covers` to the spec (that is coverage-floor plumbing and must not
  live in the product spec).

### 3. Do NOT touch anything else

- Do not rewrite ACs, re-run an authoring interview, or change the mechanism
  ACs (anchors with no E2/E3 finding stay exactly as they are).
- Do not change `## Substrate Primitives`, FRs, or any anchor the report does
  not name. This is a surgical, report-driven transform — its entire scope is
  the anchors the findings list.

### 4. Report and re-validate

- Print a table: anchor → action taken (E3 → annotated `@untestable`; E2 →
  removed from Non-Negotiable).
- Suggest the next step: `dark-factory validate <spec>` then re-run the
  pipeline (`dark-factory run <spec>`). The deferral gate should now pass
  (the re-scoped anchors are excluded from scenarios and the blocking set),
  and the architect's `covers` credits them as deferred at the floor.

## Integrity note

Every edit this skill makes is named by a deterministic framework finding. If
you find yourself wanting to re-scope an anchor the report did **not** list,
STOP — that is exactly the masking-with-expert-knowledge failure mode. The
correct response is to make the framework surface it (extend the gate), not to
hand-apply it here.
