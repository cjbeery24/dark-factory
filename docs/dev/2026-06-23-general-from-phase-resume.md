# Design: General `--from-phase <N>` Resume (#11 follow-up)

**Status:** DESIGN — not implemented. Neo + Linus gate required before build.
**Date:** 2026-06-23
**Predecessor:** `cbd4754` `feat(run): --from-phase gate — re-enter at the gate
with the existing impl (#11)` — shipped the gate-only special case.

## Problem

`--from-phase` today accepts only `gate` (`cli.py:107 choices=["gate"]`). It is
**seed-and-reuse**: it requires a *complete, correct* on-disk implementation plus
prior scenarios + manifest, skips validation/architecture/test-engineer + the
first implementer, and re-runs the gate against the existing impl. It is **not**
a general "resume from phase N" mechanism.

`--resume` resumes an *interrupted* run from the recorded `completed_step`, but
cannot jump to an arbitrary phase or clear downstream work.

**Gap exposed by spec-5 run3 (2026-06-23):** a run killed mid-implementer
(session-teardown SIGTERM) left an *incomplete* impl + valid scenarios on disk.
Neither flag could cheaply resume it:
- `--from-phase gate` needs a *finished* impl (it would gate a partial impl).
- `--resume` would re-enter at `completed_step 2` and **redo the entire ~1h
  scenario-generation block** (including the 1800s TE-validate wall).

The only clean option was a full `--fresh` restart (~5h), after manually
resetting the half-written implementer source to the spec-4 baseline and
clearing run artifacts by hand — exactly the bookkeeping a general `--from-phase`
should automate.

## Proposed: generalize `--from-phase` to the ordered checkpoint enum

Expose re-entry at each **existing `completed_step` checkpoint**. Resuming at
phase N: (a) require the prior artifacts for phases *before* N to be present;
(b) **clear** artifacts produced at-or-after N (so they regenerate); (c) mutate
state to N's entry; (d) run forward.

### Phase model — maps 1:1 to the existing `completed_step` boundaries

| `--from-phase` | Requires (seed) | Clears (regenerate) | `completed_step` set to |
|----------------|-----------------|---------------------|--------------------------|
| `validation`   | — (spec only)   | everything (manifest, scenarios, holdouts, impl→baseline, reports, reviews) | 0 |
| `architecture` | validation (step≥1) | manifest, substrate manifests, scenarios, holdouts, impl→baseline, reports | 1 |
| `scenarios`    | manifest + env (step≥2) | **scenarios + holdouts (together)**, impl→baseline, reports | 2 |
| `implementer`  | scenarios + holdouts + manifest (step≥3) | impl source→baseline, reports, gate artifacts | 3 |
| `gate`         | complete impl + scenarios + manifest | — (existing behavior) | 3 + `skip_implementer_once` |

`gate` remains exactly the current `cbd4754` behavior (seed-and-reuse, no clear).

### **Hard constraint: scenario generation is ONE atomic phase (TE + QA)**

Since `3235126` the Test Engineer (scenarios) and QA Holdout (holdouts) run
**concurrently** as a single parallel block between `completed_step` 2 and 3.
Therefore:

- The enum **must not** offer `test-engineer` and `qa-holdout` as separate
  re-entry points. There is exactly one resume point for this work: `scenarios`.
- **You cannot resume "after QA, before TE" (or vice-versa).** They are co-equal
  siblings generated blind from the *same* spec + manifest; there is no ordering
  between them and no coherent intermediate state. Resuming `scenarios` clears
  **both** the `scenarios/` and `holdouts/` dirs and regenerates the whole block.
- Rationale beyond "they're parallel": scenarios and holdouts are a **matched
  set** for one spec/manifest version. Keeping one while regenerating the other
  would let a stale-spec holdout grade a fresh-spec implementation (a barrier-
  coherence hazard). Atomic regeneration keeps the set consistent.

This already matches the `completed_step` model (step 3 = "test-engineer +
coverage-floor" as one checkpoint) — the generalization must *preserve* that
atomicity, not break it apart.

## Mechanics

1. **Artifact dependency map (declarative).** Each phase declares what it
   *produces*. `clear-set(N)` = union of products of phases ≥ N; `keep-set(N)` =
   products of phases < N (the seed, validated present or the run rejects, as
   `_from_phase_gate_missing_artifacts` already does for `gate`).
2. **Source reset for `implementer` and earlier.** "Clear downstream impl" means
   resetting the *project source* to its pre-spec baseline — for a build-chain
   spec, the prior shipped spec's committed state (`git checkout` tracked +
   `git clean` untracked, scoped to source dirs). This is the manual step we did
   for run3→run4; it must be automated and **fail-closed** (refuse if the working
   tree has uncommitted changes it can't attribute to the current spec, to avoid
   nuking unrelated work).
3. **State mutation.** Set `completed_step` per the table, `iteration=1`,
   `status=running`, clear `abort_reason`. (`gate` additionally sets
   `skip_implementer_once`.)
4. **Stale-validation guard.** Re-entering after `validation` requires the
   on-disk spec hash to match `validated_spec_hash` (reuse the `--skip-validation`
   check); a spec edited since the seeded validation must force `validation`.
5. **Coverage-floor.** `scenarios` re-runs the floor with regen enabled;
   `implementer`/`gate` re-run it verify-only (as `gate` does today).

## Open questions for review

- Should `--from-phase scenarios` regenerate via the parallel path unconditionally,
  or honor `scenario_parallel_generation`? (Lean: honor the config — resume should
  mirror a fresh run's generation path.)
- Source-reset baseline detection for non-build-chain / standalone specs (no
  `depends-on` shipped baseline) — reset to `HEAD`? Require `--force`?
- Interaction with `--resume` (interrupted state): keep them distinct, or have
  `--resume` infer the safe `--from-phase` from `completed_step`?

## Non-goals

- Sub-phase resume *within* a phase (e.g., TE coverage_regen vs repair).
- Resuming a *parallel sibling* independently (explicitly forbidden — see the
  atomicity constraint).

## Gate

Neo (design) + Linus (code) before implementation, per framework-change policy.
Keep off the critical path while spec-5 (and the build-chain campaign) is in
flight — this is tooling, not a pipeline-correctness fix.
