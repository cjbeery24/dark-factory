# Authorization Model — When to Pause and Ask

Process safeguard agreed during the 2026-04-25 architecture
review. Exists to prevent recurrence of the `d2fecbf` mistake
(reverted in `5130935`) — a one-line "tactical" change that
quietly converted a Reviewer/Oracle-only signal into an
Implementer-readable signal, violating the **validator-bridge
principle** (`docs/goals/dark-factory-intent.md`,
`docs/goals/agent-details.md` line 71). Tactical-fix
authorization does not waive these triggers; small diffs,
observability bumps, and code/comment alignment fixes are
exactly where audience-escalation mistakes hide.

## The two triggers

A change requires explicit authorization (pause-and-ask, not
ship-and-hope) when **either** trigger fires.

### 1. Audience escalation

A string previously consumed only by humans, the Oracle, or the
Reviewer becomes consumable by a barrier-restricted agent
(`IMPLEMENTER`, `SCENARIO_ARCHITECT`, `TEST_ENGINEER`,
`QA_HOLDOUT` — see `darkfactory/docker/barriers.py`) on a
subsequent iteration.

**Concrete test (the only test that matters):** does your diff
change which strings end up in a directory that a
barrier-restricted container can read? The directory list comes
from `barriers.py` — for each role, every directory **not** in
its `masked_dirs` is readable. Today that means at minimum:

- `.dark-factory/reports/` is readable by `ARCHITECT`,
  `REVIEWER`, `ORACLE`, `EVALUATOR`, `DEFECT_CLASSIFIER`. It is
  masked from `IMPLEMENTER` only when **the gate succeeds**;
  any change that promotes a row to gating makes the prior
  iteration's report contents the next iteration's input.
- `.dark-factory/scenarios/` is readable by `IMPLEMENTER`
  (intentional TDD-target read, Phase 4.6b.1).
- Project source dirs are readable by `IMPLEMENTER`,
  `ARCHITECT`, `SUBSTRATE_OWNER`.

The most common mechanism: **severity escalation** (non-blocking
→ blocking). A FAIL row that previously did not set
`GATE_FAILED=true` now sets it; a logged warning becomes a
non-zero exit; an advisory check moves to gating. Severity
escalation is not a separate trigger — it is the canonical path
to audience escalation, because the gate report's
*failure-causing* rows are the rows the next-iteration
Implementer will read.

### 2. Changed-surfaces declaration

Commits touching any of the following files must declare the
audience of changed strings in the commit body:

- `darkfactory/docker/barriers.py`
- `scripts/quality-gate.sh` report-row writes (rows piped to
  `$REPORT_FILE` / `$SUMMARY_FILE`)
- `scripts/lib/scenario_lint.py` and any new lint emitter
- Any new artifact written under `.dark-factory/` whose mask
  status is not already covered by `barriers.py`

The declaration is one line under `Audience:` — for example,
`Audience: Reviewer-only (gate report stays observability)` or
`Audience: Implementer-readable on iter-N+1 (validator-bridge
change, see ROADMAP.md Phase 2)`. The declaration is mechanical
— it reads like a checklist item, not a justification — and a
review that lands one of these files without it is the trigger
itself, regardless of trigger #1.

## Worked example: `d2fecbf`

Diff: a single line that added `GATE_FAILED=true` inside the
scenario-lint FAIL branch. The full lint-handling block lives
at `scripts/quality-gate.sh:1326-1361`; the actual diff was one
line in that block. Framed as a code/comment divergence fix —
the comment said "Fails the gate if issues are found" but the
code only wrote a FAIL row.

Why both triggers fired:

1. **Audience escalation.** Before the change, the lint phase
   wrote a FAIL row but did not gate. Reviewer/Oracle saw the
   row; the Implementer's next iteration did not, because a
   passing gate produced no Implementer-input report. After the
   change, a lint FAIL became a gate FAIL, which became the
   Implementer's iter-N+1 input. The strings inside `_LINT_OUT`
   — `spec-forbidden-shape:<raw-regex>` rule names, file paths,
   source excerpts (`scripts/lib/scenario_lint.py:260`) — were
   verbatim test-shape information. A Reviewer-audience artifact
   became an Implementer-audience artifact in one line.
2. **Changed-surfaces declaration.** The commit touched
   `scripts/quality-gate.sh` report rows. The body did not
   contain an `Audience:` line.

What pause-and-ask would have caught: the post-hoc validator-
bridge review (carried out in `5130935`) was fast — under an
hour to identify the leak surface and revert. Doing that review
*before* merging produces `5130935` directly, no round-trip.

The architectural fix — move §4a enforcement to a Test Engineer
pre-acceptance phase, where retry happens before scenarios cross
the barrier — is Phase 2 of `docs/ROADMAP.md`. The authorization
model is the process backstop while that structural fix is in
flight, and the standing rule afterward.

## When a trigger fires

Stop. Write the question up — what audience does the changed
string serve before, what audience after, is that promotion
intentional. Link it to `docs/ROADMAP.md` (or its successor) if
the answer is "yes, this is part of a barrier-design decision."
A barrier-design decision belongs in a documented plan, not a
one-line commit.
