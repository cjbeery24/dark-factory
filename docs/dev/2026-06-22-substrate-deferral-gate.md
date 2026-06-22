# Substrate-deferral consistency gate (PROVISIONAL)

**Status:** shipped 2026-06-22. **The validator-time check is a PROVISIONAL hard
gate — re-evaluate (see "Re-evaluation trigger" below).**

## The defect it catches

A spec declares substrate primitives in `## Substrate Primitives`. A primitive
marked `verifiable = false` is **deferred**: its enforcement is scoped to a later
build phase and does not exist yet. If a **Blocking** (Non-Negotiable) acceptance
criterion asserts the runtime **OUTCOME** of such a deferred capability, that AC
is unsatisfiable this phase — its test cannot pass and it must not block ship.
(Distinct from a Blocking AC asserting only an in-scope **mechanism** — input
validation, error-throwing, a signature — which IS satisfiable now.)

Motivating case: bb spec-4 (`database-foundation`, F-1c). Blocking RLS
tenant-isolation ACs (AC-14/16/18, EC-5) asserted isolation outcomes over a
`verifiable=false` `tenant_isolation` primitive whose tables/policies/role were
deferred to F-2+. The TE wrote scenarios that couldn't pass (the test DB connects
as a superuser, which bypasses RLS), producing an 8/12 ENV_ISSUE → env-dominant
escalation — a confusing, expensive, *late* failure for what is a spec-scoping
contradiction knowable up front.

## Two layers (both deterministic where it counts)

1. **Validator-time gate (cheap, pre-pipeline)** —
   `darkfactory/spec/deferral_check.py`, run in `_step_spec_validation`. The
   **precondition is deterministic and stack-agnostic**: a `verifiable=false`
   primitive (a TOML field, any stack — *no* per-adapter rule taxonomy) AND ≥1
   candidate anchor exist. The candidate set is the **WIDE** set — every AC/EC
   not already `@untestable`/`vehicle`-annotated, NOT just the §8a Blocking set
   — so non-blocking deferred-outcome anchors (e.g. spec-4's EC-12) are caught
   here, not 2-3h later at the floor. When met, ONE **narrow, focused LLM call**
   judges: does each candidate assert the deferred capability's **outcome**
   (re-scope) or only an in-scope **mechanism** / a **different capability**
   (fine)? Flagged anchors → `DarkFactoryError`. Each flagged anchor →
   `@untestable:forward-reference`; the subset also in §8a → also dropped from
   Non-Negotiable. **Wide-set measured FP = 0** on spec-4 (K=8 × sonnet-4-6 +
   opus-4-8, adversarial resilience ACs included, perfectly consistent); it even
   caught EC-12, which a human (me) had mis-labeled — see the postmortem note.

2. **Coverage-floor gate (expensive, post-architect, fully deterministic)** —
   `coverage.check_substrate_deferral_consistency` (F/E2/E3), keyed on the
   architect's `covers`. This is the **deterministic backstop** for the
   false-NEGATIVE direction: anything the validator-time LLM gate MISSES still
   hard-fails here, so the left-shift never loses a catch. (The one mode the
   floor does NOT backstop: a validator-gate false POSITIVE silently
   under-covers an anchor — hence the re-eval trigger watches author-disputed
   `@untestable` annotations.)

## Why the LLM here is reliable (not the validator flip-flop)

The validator's general non-determinism comes from **open-ended** "find all
defects." This is the opposite: deterministic, stack-agnostic *inputs* + ONE
*bounded* judgment, run as a **separate focused call** (NOT folded into the
holistic validator prompt — that would re-expose the flip-flop). Measured
**16/16 EXACT** across `claude-sonnet-4-6` and `claude-opus-4-8` on the spec-4
case (`/tmp/deferral-llm-harness.py`): every run returned exactly
`{AC-14, AC-16, AC-18, EC-5}`, caught all outcome ACs, zero mechanism
false-positives.

## Re-evaluation trigger (why it's PROVISIONAL)

Make it a gate **for now**, but **re-evaluate if specs get stuck here
ILLEGITIMATELY** — i.e. a false positive blocks a spec whose Blocking AC is
genuinely an in-scope mechanism. The reliability evidence is **one realistic
case**; genuinely *ambiguous* outcome-vs-mechanism anchors are unmeasured. If
false positives appear:

- **Demote** from a hard gate to a REVISE-class advisory (author confirms), or
- add an **author override** marker, or
- run a **multi-spec adversarial sweep** to map where the LLM's reliability
  breaks down before keeping it a hard gate.

Demoting never loses the catch: the coverage-floor F/E2/E3 backstop (layer 2)
still fails closed — demotion only moves the catch later in the pipeline.

## Authoring loop

When the gate (or the floor) fires, `/df-rescope-deferred` consumes the findings
and applies the re-scope (mark outcome anchors `@untestable: forward-reference`,
drop them from Non-Negotiable) — **no hand-editing the spec**. `covers` stays
architect-owned (the architect infers it; see the postgres
`architect_addendum.md`), out of the product spec.
