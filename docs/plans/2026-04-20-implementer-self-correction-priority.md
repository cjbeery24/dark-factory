# Plan Revision — Implementer Self-Correction Priority

**Date**: 2026-04-20
**Status**: APPROVED — neo-reviewed 2026-04-20, consensus reached
**Extends**: `docs/plans/2026-04-18-iter1-artifact-and-implementer-retry.md` (supersedes Phase 6 V3/V4 and reshapes Phase 8a)
**Target**: `~/sites/dark-factory`

---

## Motivation

Phase 6 V1 (n=3) and V2 (n=3) are complete. Six run reports in `docs/pipeline-run-reports/`. The data does not support the original sequencing, which assumed we needed variance decomposition (V3 anchor-scenarios, V4 anchor-environment) before deciding what to fix. Two observations motivate this revision:

**1. We already know LLMs produce variance on identical inputs.** Running V3 to measure *how much* implementer variance exists on locked scenarios would refine a number but not change the fix list. The decisions V3 was designed to drive (ship 5.7b? build 8a?) will be made the same way regardless of the V3 outcome.

**2. Within-run iteration data already answers the implementer-on-locked-scenarios question.** TE runs once per pipeline run; iter 1 → iter 2 → iter 3 implementer calls all see the same scenario set. Every iteration transition is already an "implementer-variance-on-locked-scenarios" datapoint. The 6 runs give us 6 iter-1→iter-2 transitions without needing V3.

Reading those 6 transitions:

| Run | Iter 1 → Iter 2 behavior |
|-----|--------------------------|
| V1-R1 | +36pp then plateau at 80% |
| V1-R2 | vitest-discovery fix → 92% ship |
| V1-R3 | **identical failure set, immediate plateau** |
| V2-R1 | vitest-discovery fix → 77% then plateau |
| V2-R2 | **identical failure set, immediate plateau** |
| V2-R3 | +12pp blind but **self-test regression** |

3 of 6 runs the implementer either failed to move on identical scenarios (plateau) or moved forward in one dimension and regressed in another. The implementer's self-correction loop is broken. This is the outcome bottleneck, not TE output variance.

## Consequence for the prior plan

The prior plan (`2026-04-18-iter1-artifact-and-implementer-retry.md`) prioritized:
1. Phase 5.7b (TE output-volume floor) before Phase 6 execution.
2. Phase 6 V2/V3/V4 to produce implementer-variance signal.
3. Phase 8 (including 8a cumulative feedback history) gated on "V3 iter-1 pass-rate range ≥ 10pp."

V1+V2 data invalidates this ordering:
- 5.7b attacks TE variance. Even with TE volume constrained, the plateau/regression pattern above would still occur — TE is upstream of the observed bottleneck.
- V3 is measurement, not a fix. The fixes it would trigger (8a, 8c) are better justified directly by qualitative V1/V2 signal.
- The V3 gate on Phase 8a is blocking work that the existing data already justifies.

## Revised plan

### Priority 1 — Reshape Phase 8a: implementer self-correction loop

**Target**: `darkfactory/pipeline/feedback.py` (new or extend), implementer prompt, iteration-loop wiring.

On iter ≥ 2, the implementer prompt includes:

**1.1 Cumulative chronological failure history** across all prior iterations. Each entry: iteration number, anchor ID (FR-N / EC-N), failure category, classifier rationale. Chronological, not deduped — the cadence itself is signal ("FR-7 failed in iter 1 AND iter 2 — try a different approach" is information the implementer needs).

**1.2 Diff of iter N-1 changes** — what the implementer wrote on the previous iteration, as a file-scoped unified diff. Gives the implementer visibility into its own prior attempt.

**1.3 Structured root-cause preamble** (replaces the prior draft's "architectural permission clause"). On iter ≥ 2, the implementer is *required* to produce a structured preamble before writing code:

```
## Root cause analysis (required iter ≥ 2)
- Primary failure from iter N-1: <anchor ID> — <one-line description>
- Root cause: <location file:line + one-sentence mechanism>
- Proposed fix scope: [local-patch | signature-change | module-rework]
- Why this differs from the iter N-1 attempt: <one sentence>
```

This is auditable (post-hoc grep), forces naming a concrete location, and surfaces the scope-of-change decision explicitly instead of hoping a permission clause nudges it. The `fix scope` tag also gives us a metric — if iter-2 uses `local-patch` on a run that plateaus, the preamble itself is evidence of the failure mode.

**1.4 Current iter-N-1 feedback file** (unchanged from today).

**1.5 Self-test-regression hard gate** (new, mechanical — directly addresses V2-R3). In the iteration loop, after quality gate:
- If iter-N Self-Tests failed where iter-(N-1) Self-Tests passed → reject iter-N output.
- On rejection: log event, do NOT advance state; fall through to the next iteration using iter-(N-1) code as the working base.
- If iter-N exhausts the iteration budget on regression rejections → escalate with `stable_failure_reason="self_test_regression"`.

This is orthogonal to the prompt changes: even if the preamble fails to change behavior, the gate prevents blind-up/self-test-down outcomes like V2-R3 from shipping.

**Barrier threat model (revised from prior draft).** The prior Phase 8a plan mandated dedupe + substring audit citing a "coverage-frequency side channel" (FR-7 mentioned 3× leaks that FR-7 has 3 test cases). That framing was wrong — scenarios are already visible, but classifier rationales and failure-category distributions are NOT derivable from the scenario directory alone, so raw chronological feedback does expose *something* new. However, the real threat that justifies masking is **holdout/barrier-artifact leak** (scenario names, assertion text, file paths outside `.dark-factory/scenarios/`), not cadence. The audit test below targets that threat directly:

**Audit test (barrier assertion).** Unit test: cumulative history payload contains no strings matching:
- File paths outside `.dark-factory/scenarios/` (allowlist: spec path, scenario dir paths).
- Holdout scenario file names (compared against `.dark-factory/holdouts/` listing in test fixture).
- Holdout assertion text (compared against holdout file contents in test fixture).

If any of these match, the test fails and dedupe/filtering is reintroduced as fallback.

**Acceptance**:
- Unit test: iter-3 implementer prompt contains iter-1 and iter-2 feedback text in chronological order.
- Unit test: iter-2 prompt contains diff of iter-1 changes (non-empty diff for non-trivial iter-1 changes).
- Unit test: iter-2 prompt contains the structured root-cause preamble template verbatim.
- Unit test: barrier audit (above) passes on a constructed fixture with known holdout contents.
- Unit test: self-test-regression gate rejects iter-N when iter-(N-1) Self-Tests passed and iter-N failed; does not reject on FAIL→FAIL (different concern; 1.5 handles the clear PASS→FAIL case only in V1).
- End-to-end validation: see "Validation plan" below.

**Effort**: medium. ~3-4 days (feedback module, prompt changes, regression gate, unit tests, iteration-loop wiring).

**Metrics**:
- `plateau_broken_fraction` across validation runs.
- `self_test_regression_rejections` — count per run.
- `root_cause_preamble.fix_scope` distribution — `local-patch` rate correlates with plateau risk.

### Priority 2 — Investigate V2-R3 self-test regression (forcing function for Priority 1 prompt)

**Target**: qualitative analysis doc, `docs/analysis/2026-04-20-v2r3-self-test-regression.md`.

**What it does**: Read the iter-1 vs iter-2 implementer diff from `archived-runs/f90861497182494c/` for the `withTenantDb` call site (apps/api/src/db/index.ts:74). Produce a **binary outcome** that drives Priority 1 prompt design:

1. What architectural choice did iter-1 make for the callback signature?
2. What change did iter-2 introduce that broke it?
3. Was the change locally-scoped to fix a blind scenario, or an unrelated refactor?

**Forcing function (reviewer feedback):** Priority 2 must exit with one of:

- **Outcome A — confirms architectural-patching hypothesis.** Produce a 1–3 sentence worked example that is inserted verbatim into the Priority 1 root-cause-preamble template as an illustrative case ("e.g., iter-1 chose callback signature `fn(tx)`; patching individual call sites instead of updating the signature causes regressions in unrelated consumers"). Priority 1 ships with the worked example embedded.

- **Outcome B — falsifies architectural-patching hypothesis.** If the diff shows the regression was caused by something else (e.g., a genuine implementer mis-edit, a dependency version change, a TE barrier violation bleed-through), Priority 1 prompt design is reopened. Do NOT ship the preamble-as-designed until it has been adjusted to address the actual failure mode.

Priority 2 is a **hard precondition** on Priority 1 shipping. No "read the diff and vaguely inform the prompt" — exit with A or B, on paper, before Priority 1 merges.

**Effort**: low. 2-4 hours of diff-reading + decision.

**Acceptance**:
- Analysis doc shipped with explicit `outcome: A` or `outcome: B` tag in frontmatter.
- If A: Priority 1 prompt contains the worked example.
- If B: Priority 1 plan revision issued before implementation begins.

### Priority 3 — Fix anchor-manifest substrate invalidation

**Target**: `darkfactory/pipeline/architect.py` (or wherever `--anchor-manifest` is handled).

**What it does**: When `--anchor-manifest PATH` is passed, auto-seed substrate manifests from the anchor's sibling directory:

```
<anchor_dir>/substrate-manifests/ → .dark-factory/architecture/substrate-manifests/
```

This replaces the current manual `cp` workaround documented in V2-R1 and V2-R2 reports. Pipeline-level fix option (a) from V2-R1 follow-up #1.

**Why now**: Low-cost, isolates future anchor experiments from a known footgun. Independent of Priority 1/2 — can ship in parallel.

**Acceptance**:
- Unit test: `--anchor-manifest` with sibling `substrate-manifests/` directory → substrate files present at runtime path before architect invocation.
- Unit test: `--anchor-manifest` without sibling `substrate-manifests/` → warning logged, pipeline proceeds (no silent failure).
- Integration: re-run V2-R2 setup without manual `cp` step → anchor holds (`cache_hit=true`).

**Effort**: low. ~0.5 day.

### Priority 4 — Implementer temperature=0 on iter ≥ 2 (was Phase 8c)

**Target**: `darkfactory/pipeline/iteration.py`, implementer `invoke_agent` call.

**What it does**: iter-1 uses default temperature (exploration); iter ≥ 2 uses `temperature=0` (exploitation against specific feedback).

**Why now**: Trivial cost. Ship alongside Priority 1 since they touch adjacent code. Reduces one orthogonal variance source so Priority 1's validation measurement is cleaner.

**Acceptance**: unchanged from prior plan (Phase 8c, lines 187-205 of 2026-04-18 plan).

**Effort**: trivial. Single config change, unit test, event logging.

## Sequencing

```
Priority 2 (V2-R3 analysis, forcing function) ──┐
                                                │  Outcome A → worked example into prompt
                                                │  Outcome B → reopen Priority 1 design
                                                ▼
Priority 1 (reshaped 8a + 1.5 regression gate) ──┐
Priority 4 (temp=0 iter 2+) ─────────────────────┤  ship together
                                                 ▼
              Full-bundle validation — n=4 free runs on database-foundation
                                                 │
                                                 ▼
   Plateau broken in ≥ 3 of 4 AND zero self-test regressions?
                                                 │
                                                 ├── YES → Ablation arm (n=4, history+diff only)
                                                 │            │
                                                 │            ▼
                                                 │   Isolate preamble contribution. Ship broader
                                                 │   validation if ablation confirms preamble
                                                 │   carries a detectable share.
                                                 │
                                                 └── NO  → SKIP ablation (stop-rule: no point
                                                           isolating components of a null effect).
                                                           Triage:
                                                           - If self-test regressions drove failure:
                                                             revisit 1.5 gate logic.
                                                           - If plateaus persist: escalate to Phase 8b
                                                             (auto-split on plateau) or model upgrade
                                                             evaluation.
                                                           - If neither: revisit 5.7b.

Priority 3 (substrate auto-seed) ── ships independently, no dependency on 1/2/4.
```

**Statistical framing.** Under H0 (no intervention effect), base rates from V1+V2 (n=6): plateau ≈ 50%, self-test regression ≈ 17%. Joint false-positive for "plateau broken ≥ 3/4 AND zero regressions" under H0 ≈ 0.17 — tight enough at n=4 given budget constraints. Neo-confirmed.

## Deferred / dropped

- **Phase 6 V3 (anchor-scenarios n=3):** dropped. Within-run iter-1→iter-2 transitions give us 6 implementer-on-locked-scenarios datapoints from the existing runs. *Note:* V3 is not strictly equivalent to within-run — it would measure cold-start implementer variance on identical scenarios, whereas within-run conditions iter-2 on iter-1's code. Different causal graphs. V3 stays in reserve if Priority 1 fails and we need to distinguish "same implementer can't revise well" from "implementer variance is too high regardless." Also unresolved: whether `--anchor-scenarios` fully locks TE output across runs, or whether TE non-determinism would still leak in.
- **Phase 6 V4 (anchor-environment):** dropped. No open question V4 would answer.
- **Phase 5.7a (V1 artifact comparison + anchor selection):** no longer needed; was input to V2/V3 planning.
- **Phase 5.7b (TE output-volume floor):** demoted. Keep as a build-later item if Priority 1 validation fails AND triage points at residual TE volume as the bottleneck. The per-anchor-breadth rule is the load-bearing part if revisited — drop the scenario-count framing.
- **Phase 8b (auto-split on plateau):** deferred. Depends on whether Priority 1 eliminates plateaus. If plateaus persist after Priority 1, 8b's invariant-failing-anchor extraction becomes the explicit fallback.

## Risks & mitigations

| Risk | Mitigation |
|------|-----------|
| Raw chronological feedback leaks information across the barrier | Barrier audit test (Priority 1 acceptance) asserts no holdout scenario names, assertion text, or paths outside `.dark-factory/scenarios/` appear in generated history. If the test fails, fall back to dedupe/filtering. Coverage-frequency cadence is NOT treated as a leak — it's signal the implementer needs. |
| Root-cause preamble degrades into boilerplate (implementer fills slots mechanically without real analysis) | Monitor `fix_scope` tag distribution. If `local-patch` rate exceeds ~70% on iter-2 on plateau runs, the preamble is not driving genuine reconsideration — revisit prompt wording or escalate to spec-split (8b). |
| Self-test-regression hard gate (1.5) creates starvation (implementer can't advance because every attempt regresses somewhere) | If regression rejections exhaust iteration budget, escalate with `stable_failure_reason="self_test_regression"` rather than shipping broken code. Budget already 3 iters — acceptable ceiling. |
| n=4 validation still underpowered relative to n≥8 | Accept. Joint criterion (plateau + regression) brings H0 false-positive to ~0.17; n=4 is affordable and neo-confirmed as tight enough. If validation is borderline (e.g., 3/4 pass but one run ambiguous), extend to n=6 rather than shipping on weak evidence. |
| V2-R3 analysis (Priority 2) falsifies architectural-patching hypothesis (Outcome B) | Priority 1 prompt design is reopened before implementation. This is by design — Priority 2 is a forcing function, not window-dressing. |
| Priority 3 substrate auto-seed reveals deeper architect-cache coupling issue | Priority 3 becomes a scoped investigation instead of a ship; does not block Priority 1/4. |
| Full-bundle validation passes but ablation shows preamble contributes nothing | Keep preamble anyway if it doesn't hurt (it produces auditable `fix_scope` metadata). Document negative result. Do not generalize "prompts don't matter" — one negative ablation is not proof. |

## Success criteria

- **Priority 2**: analysis doc shipped with explicit `outcome: A` or `outcome: B` frontmatter tag. If A, worked example inserted into Priority 1 prompt. If B, Priority 1 plan revision issued before implementation.
- **Priority 1 + 4**: shipped together; all unit tests pass (including barrier audit); n=4 validation runs completed on `database-foundation`; plateau pattern broken in ≥ 3 of 4 runs AND zero self-test regressions across those runs.
- **Ablation arm (conditional)**: runs only if full-bundle validation passes. Isolates preamble contribution. Result documented but does not block forward progress.
- **Priority 3**: substrate auto-seed shipped; anchor experiments no longer require manual `cp` step.
- No Phase 6 V3/V4 runs executed unless Priority 1 validation fails and triage justifies the additional measurement.

## Out of scope

- **Ensemble sampling on retries** — still rejected per neo's prior review.
- **Fix Planner agent** — still rejected.
- **Cross-run lessons-learned registry** — still deferred.
- **Model upgrade of the implementer** — out of scope for this plan, but documented trigger conditions:
  - **Trigger**: full-bundle validation fails AND ablation arm (or triage) shows no combination of prompt/feedback changes produces detectable improvement on `database-foundation`.
  - **Action**: separate evaluation proposal — compare current implementer (Opus 4.7 as of 2026-04-20) against alternative models on a fixed database-foundation iter-2 task, cost and pass-rate tracked.
  - **Deadline for revisiting**: if Priority 1 fails, do not defer model-upgrade evaluation beyond one iteration of additional plan revision. Don't let it drift.
