# Cross-Spec (and Spec-vs-Code) Consistency — a structural gap

**Trigger:** spec-5 (auth-schemas-middleware) FR-1 mandates the server entrypoint run
migrations programmatically (`drizzle-orm/migrator`) on startup. Its declared dependency
F-1c (database-foundation) §2 FR-1 states the startup path **MUST NOT** invoke the
`migrate(...)` programmatic helper — migrations are CLI-only. A direct, unsatisfiable
contradiction between two specs authored separately by `/df-write-spec`, decomposed by
`/df-write-plan`. The spec-validator caught it **only incidentally and non-deterministically**
(1 of N adapter-off runs; others surfaced different blockers or PASSed).

## What happens now (the gap)
- `/df-write-plan` decomposes a project into ordered, dependency-linked specs. `/df-write-spec`
  authors each **individually**.
- The spec-validator checks each spec for **internal** completeness/precision/consistency.
  The Cross-Section Contradiction Procedure (T1–T5) is **within one spec**.
- There is **no systematic cross-SPEC consistency gate.** A later spec can contradict a
  dependency's hard constraint and nothing deterministically catches it.
- The validator *can* read sibling specs (specs are not masked, unlike source), so it *can*
  catch cross-spec contradictions — but only **incidentally + non-deterministically** (it has
  to choose to look, then elevate it over the spec's other issues). Not a gate.
- The framework *does* have narrow cross-spec machinery — the substrate **deferral gates** +
  `audit-deferred` (cross-spec deferred-obligation audit). So cross-spec awareness exists for
  **deferred obligations**, but not for general **constraint contradictions**.

## Why it matters (given docs/goals)
The goals are lights-out autonomy where humans supply **only locked specs**; the spec SET *is*
the contract. For lights-out to hold, that contract must be consistent **across** specs (and,
in brownfield, vs existing code) — not merely per-spec. A cross-spec contradiction is the #1
named challenge ("spec ambiguity → agents guess/hallucinate") **at plan scope**. The framework
invested heavily in per-spec quality + the test-gaming barriers, but treats the spec set as a
bag of independent contracts. This **scales badly**: build-chain depth × spec count grows the
contradiction surface with no systematic guard.

The reference (Yassine) model locates cross-cutting consistency in **ADRs + canonical
identifiers + API contracts locked at the Architecture phase** — shared, versioned, referenced
by every feature. But even it has no explicit *gate* checking a later feature against earlier
locked constraints; it relies on each feature's architect honoring the shared contract.

## Is it a larger issue with the framework / the bb specs? Yes, both.
- **Framework:** missing a systematic cross-spec (and brownfield spec-vs-code) consistency gate.
  The per-spec model + code-blind validator are **greenfield-per-feature** assumptions; they
  don't cover the integrity of the spec *set* or brownfield spec-vs-code.
- **bb specs:** spec-5 contradicting F-1c implies `/df-write-spec` authored specs without
  enforcing cross-spec invariants → bb likely harbors **other latent cross-spec contradictions**.
  The bb spec set needs a consistency audit the framework can't currently perform.

## Field grounding (research)
- Established RE techniques: automated **consistency checking** (type errors, nondeterminism,
  missing cases, circular defs); **contradiction detection** via SAT-solver over logicalized
  requirements; **traceability** (link each requirement to source/design/impl/test);
  **dependency-relationship validation** (are referenced components defined + logically linked);
  periodic scans that re-check dependencies when a requirement changes. (DTIC/ACM TOSEM
  automated-consistency work; recent arXiv: SAT-solver contradiction detection in high-level
  requirements; Graph-RAG LLM traceability.)
- 2026 SDD landscape (Spec Kit, Kiro, OpenSpec, BMAD, Tessl…): specs are the source of truth.
  **Brownfield-specific finding:** existing systems' contracts are **implicit** and
  under-documented; the recommended workflow **first understands/extracts the current system**,
  then writes **narrow** specs for the change that **explicitly reference existing patterns/
  services** ("follow pattern in X", "use existing Y service") to stop the agent inventing.

## Proposed handling
### Greenfield build-chain (bb)
1. **Plan-level invariant registry (ADR-style).** `/df-write-plan` extracts + locks project-wide
   invariants (e.g. "migrations CLI-only", "tenant isolation via `app.tenant_id` GUC") as a
   shared, versioned contract — the SSOT for cross-spec constraints. Mirrors Yassine's locked
   ADRs/contracts.
2. **Systematic cross-spec consistency gate.** When validating spec X: deterministically load
   X's `depends-on` specs + the plan invariants, extract their MUST/MUST-NOT constraints, and
   check X's requirements don't contradict them. This **extends T1–T5 from cross-section to
   cross-SPEC.** The validator already can read sibling specs — make it a *required* step, not
   discretionary. (Ground-truth/structural extraction of MUST-NOT clauses first; LLM as the
   semantic sensor — per the no-bespoke / LLM-as-sensor policy.)
3. **Authoring-time surfacing.** `/df-write-spec`, when authoring X (depends-on F-1c), loads
   F-1c's constraints and flags contradictions **at authoring** — surface the need early, don't
   only catch it at validation.

### Brownfield (specs on an existing codebase)
The barrier tension: the **code-blind** validator can't check spec-vs-existing-code (and we keep
it code-blind to prevent test-gaming). Resolution:
1. **Contract-extraction step** captures the existing system's *implicit* contracts (public
   APIs, schemas, invariants) into an explicit, versioned artifact — extend `environment.toml`/
   the "living contract" to **code-level** contracts.
2. **Validate the spec against the extracted contract** (explicit text), not raw source —
   preserving code-blindness while enabling spec-vs-code consistency.
3. **Spec references existing patterns/services explicitly** (per the brownfield SDD finding).
This separates the WHO: a non-barrier extractor (or deterministic extraction) produces the
contract; the code-blind validator checks spec-vs-contract.

## Unifying principle
Make the spec SET a **consistent, interconnected contract** (plan invariants + cross-spec gate +
brownfield extracted code-contracts) — while preserving the code-blindness that prevents
test-gaming. This is requirements-consistency/traceability applied to the autonomous model.

## Open questions for review
- Is the cross-spec gate's contradiction detection feasibly general/deterministic, or does it
  collapse into per-stack semantics (the bespoke treadmill)?
- Does a plan-level invariant registry over-constrain (premature lock-in) vs the per-spec
  flexibility the current model has?
- Brownfield: can contract-extraction be trusted without breaking the barrier or becoming an
  O(stacks) treadmill?
- Is this the right LAYER (plan vs validate vs author) for the fix?
