# Ship-with-gaps policy fix — evidence + root cause + proposed fix

**Status:** EVIDENCE + FIX PROPOSAL for critical review (Neo + Codex). Authorization-sensitive
(ship-authority gate) → AUTHORIZATION-MODEL + Neo+Linus before landing.

## What happened (run7, spec-5, shipped at iteration 1 despite reviewer REQUEST CHANGES)

Trace from `budget-brain-v2/.dark-factory/run6-fresh.log` (run7) lines 40-43 + 66-68:
```
Sprint 16: Oracle NO-SHIP with all deterministic signals green (hard_gates=pass,
           scenarios=52/52 pass, security HIGH findings=0) — re-prompting to neutralize
           any security/score-driven NO-SHIP
Sprint 16: Oracle security reconsideration result: SHIP (was NO-SHIP)
Sprint 16: reconsidered Oracle output has missing/mismatched METRICS — accepting decision SHIP as-is
  Oracle: SHIP
SHIPPED
```
Two oracle invocations (events 10:24→10:26:44 = pass 1; 10:26:45→10:30:25 = reconsideration; the
`stream_oracle_*re_*` sidecars = the reconsideration). Oracle pass-1 wrote `oracle-output-iter1.md`
= **NO-SHIP** (reviewer REQUEST CHANGES: whitespace-token→401 spec deviation + JWKS-caching NFR +
observability NFR). The reconsideration flipped it to **SHIP**.

## Root cause (corrects BOTH earlier hypotheses)
It was **NOT** `should_ship_with_gaps` (the stable-failure plateau path — can't fire at iter1, no
failure set) NOR a clean first-pass Oracle SHIP. It was the **Sprint-16 deterministic-security
reconsideration backstop** (`darkfactory/pipeline/iteration.py:6102-6151`, fn
`_reconsider_oracle_security` at `:5632`). The `SHIP_WITH_GAPS` label is the post-hoc scorecard tag.

Three compounding defects:
1. **The trigger keys ONLY on deterministic signals** (`:6112-6119`: NO-SHIP + not composite-blocked
   + security_high==0 + hard_gates_pass + scenario_fail==0). It does **not** consult the reviewer
   verdict or the NATURE of the NO-SHIP. So it fires on EVERY green-deterministic NO-SHIP — assuming
   it must be security/score-driven (a fabricated LLM finding). But a NO-SHIP can be a legitimate
   **reviewer-driven spec-compliance/robustness** blocker, which this path then wrongly neutralizes.
2. **It relies on the LLM oracle to self-KEEP its NO-SHIP** for "concrete non-security blockers"
   (the re-prompt says "output SHIP UNLESS…"). The oracle CAVED to SHIP despite having named 3
   concrete non-security gaps in pass-1. A soft LLM nudge is the only thing protecting a legitimate
   spec-compliance NO-SHIP — and it didn't hold.
3. **It accepts a malformed reconsidered SHIP** (`:6144-6151`): the reconsidered output had
   missing/mismatched METRICS, yet the code accepts the SHIP "as-is" to avoid a re-prompt that
   "could revert the reconsideration." So an unreliable flip-to-SHIP is taken.
4. **`reviewer_verdict` never gates** (`models/pipeline.py:46` declared, never consumed). REQUEST
   CHANGES has no machine-enforced consequence at any SHIP floor (`:6153+` are all deterministic).

Net: a reviewer-identified **behavioral spec-contract deviation** (whitespace→401, an `IMPL_BUG`:
spec says X, impl does Y) was auto-shipped at iteration 1 with 2 iterations unused — exactly the
"test-gaming / passed-the-tests-but-not-the-spec" failure the framework exists to prevent, and
contrary to the intent doc's "ship-with-gaps = last resort after the 3-iteration cap is exhausted."

## Proposed fix (deterministic; honors "behavioral spec-deviation can't ship-with-gaps unless out of budget")
**A new deterministic SHIP floor** at the oracle decision gate (alongside the existing floors at
`:6153+`), consuming structured signals (NOT regex over prose):

```
# After decision is finalized (incl. any Sprint-16 reconsideration):
if decision == "SHIP" \
   and spec_run.iteration < config.max_iterations \
   and reviewer_verdict == "REQUEST_CHANGES" \
   and any(gap.is_behavioral_spec_deviation for gap in structured_gaps):
    log.warning("SHIP withheld: a reviewer-identified behavioral spec-contract deviation "
                "remains and iteration budget is not exhausted (%d/%d) — forcing NO-SHIP to iterate",
                spec_run.iteration, config.max_iterations)
    decision = "NO-SHIP"
```
- **`is_behavioral_spec_deviation`** comes from the STRUCTURED signal already produced: the oracle's
  `CLASSIFICATIONS` block (`IMPL_BUG: spec says X, impl does Y` referencing an FR/EC behavioral
  clause) and/or the reviewer's structured gap list with a spec-section ref. NOT a `'401' in text`
  regex (per the no-per-stack-bespoke-tooling rule). If the structured tag isn't reliably available,
  that's a prerequisite sub-fix (have the reviewer/oracle emit a structured per-gap
  `category ∈ {spec_contract, nfr, security}` + `spec_ref`).
- **Effect:** a behavioral spec-contract deviation forces an ITERATION while budget remains; only
  when `iteration >= max_iterations` (budget exhausted) may it ship-with-gaps (documented in the
  defect registry) — matching the Yassine intent (last resort).
- **Pure NFR gaps** (caching, logging) do NOT trip this floor → they may ship-with-gaps as today.

**Supporting fixes (same change):**
- **Narrow the Sprint-16 trigger** (`:6112`) so it only fires for NO-SHIPs that are genuinely
  security/score-driven (no concrete non-security blocker cited) — its actual intended scope. A
  reviewer-driven spec-compliance NO-SHIP should not enter reconsideration at all while budget
  remains.
- **Do not accept a reconsidered SHIP with missing/mismatched METRICS** (`:6144`) — treat a
  malformed reconsideration as "keep NO-SHIP" rather than shipping on an unreliable flip.
- **Consume `reviewer_verdict`** as a first-class gate input (it's currently dead — the
  persisted-but-not-consumed anti-pattern).

## Critical review outcome (Neo + Codex, 2026-06-24) — trace CONFIRMED, fix CORRECTED

Both independently **confirmed the Sprint-16 mechanism trace** (verified link-by-link against the
code; not `should_ship_with_gaps`, not a clean SHIP). But both flagged the draft fix as **not yet
sound** — it would swap one failure mode for the other. Revised design:

**Correction 1 — the durable fix is COVERAGE, not a prose-trusting floor (Neo, decisive).**
Sprint-16 is sound *because security claims have a deterministic corroborator (the SAST scanner)*.
A "behavioral spec-contract deviation" has **no** deterministic corroborator — `is_behavioral_spec_deviation`
is LLM-authored prose, *exactly as fabricable as the security finding Sprint-16 distrusts*. Making
it a bare hard floor re-introduces the false-NO-SHIP. **The right home for "impl does Y, spec says
X" is a FAILING SCENARIO.** If FR-2 SI-3 (whitespace→401) had a holdout/scenario asserting it, the
impl's `.trim()` would FAIL it → `scenario_fail_count > 0` → **the Sprint-16 trigger never fires**
(it requires `scenario_fail_count == 0`). So the REAL root cause is a **Test-Engineer/QA-Holdout
coverage gap** (no scenario covered SI-3; the JWKS path isn't injected in CI). **Primary fix:
ensure behavioral spec clauses get scenario/holdout coverage so deviations fail the gate
deterministically.** This matches the ground-truth→SAST→convert-LLM→bespoke priority order.

**Correction 2 — if a floor is still used, require CORROBORATION (two-source), not a single LLM tag.**
Trust `spec_contract_deviation` only when **two independent structured sources concur** (reviewer
gap list AND oracle CLASSIFICATIONS naming the same FR/EC as IMPL_BUG). Frame the residual risk
honestly: erring toward iterate-on-claimed-deviation is the *less-bad* error (a wasted iteration <
shipping a real contract violation) — but it is NOT a clean fix, and single-source prose is not
defensible as a hard floor.

**Correction 3 — PREREQUISITE producers don't exist (both).** `reviewer_verdict` (models/pipeline.py:46)
is not just unconsumed — it has **no producer** (never assigned); the reviewer's VERDICT is free
text in `{spec}-review.md`, never parsed. There is **no per-gap `{spec_contract|nfr|security}`
category** anywhere (`parse_feedback_classifications` at :2047 is section→IMPL_BUG, no contract/nfr
split, no spec_ref-as-corroboration). The floor cannot run on signals that don't exist → building
the producers (parsed reviewer verdict + per-gap category + spec_ref) is a **blocking prerequisite**,
not a footnote (surface-the-need / persisted-but-not-consumed).

**Correction 4 — policy: ESCALATE at budget-exhaustion, do NOT ship-with-gaps (both).** A behavioral
spec-contract deviation surviving the iteration cap is a *correctness* failure (the framework
shipped code violating its own contract) — that is the "edge case" the intent reserves for human
escalation, not a "documented gap." Three-way policy: **block (iterate) while budget remains →
ESCALATE at exhaustion** for spec-contract deviations; **NFR gaps → ship-with-gaps** at exhaustion.

**Correction 5 — land the `:6144` metrics-mismatch fix INDEPENDENTLY (both).** A reconsidered SHIP
whose own METRICS are missing/mismatched should **fail-closed → keep NO-SHIP** (you can't trust the
decision in an output you judge malformed). NOT "re-run the metrics loop" (Neo's trap: a
context-free re-prompt could revert a legitimate security reconsideration). This is small, fully
deterministic (the gate pass-rate IS the corroborator), corroborated, and correct regardless of the
bigger fix — ship it first.

**Placement: BOTH (a) gate the Sprint-16 trigger + (b) a path-independent post-decision floor** —
(b) is the mandatory safety (any SHIP path), (a) avoids the wasteful "Sprint-16 flips to SHIP, floor
flips it back" two-floors-fighting pattern.

**Overall: Neo APPROVE-WITH-CHANGES; Codex "net-neutral until corroborated."** Sequence: (1) the
`:6144` fail-closed fix now; (2) investigate/close the scenario-coverage gap (the durable root-cause
fix); (3) only then, if needed, a corroborated floor + the producer prerequisites; (4) escalate
(not ship) spec-deviations at exhaustion. All authorization-sensitive (ship-authority) → Neo+Linus.

## Open question for reviewers (superseded by the review outcome above)
- Is the new floor better placed as (a) a *gate on the Sprint-16 trigger* (don't reconsider a
  spec-compliance NO-SHIP) vs (b) a *post-decision floor* (force NO-SHIP after reconsideration)? (b)
  is more robust (catches a SHIP from any path), (a) avoids a wasted re-prompt. Possibly both.
- Where exactly should the structured `spec_contract` vs `nfr` gap tag originate — reviewer output,
  oracle CLASSIFICATIONS, or the defect classifier? (Single source of truth; no regex.)
- Interaction with escalation: when budget IS exhausted and a behavioral spec-deviation remains,
  should it ship-with-gaps or ESCALATE (human decision)? The intent doc says "escalates OR ships
  with documented gaps" — which, for a spec-contract deviation specifically?
