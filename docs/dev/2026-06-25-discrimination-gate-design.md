# Discrimination Gate — Design Plan (B-as-spine, A co-fix, sensor off the trust path)

**Date:** 2026-06-25
**Status:** DRAFT — for Neo + Linus review before any build.

## Problem

run20 mis-shipped: the impl `.trim()`s a whitespace-padded auth token and accepts
it (→200) where the spec requires 401. Root cause is **test-level inertness**: the
QA-holdout's whitespace test used a non-resolvable "ghost" token, so it returns
401 from *identity failure* regardless of the `.trim()` bug — GREEN but it does not
discriminate the behavior it targets. All 89 holdouts passed; nothing was red.
This is Yassine's named "test gaming / overfitting" failure
(`docs/goals/dark-factory-intent.md`).

**Discrimination — "does this test flip PASS→FAIL when the targeted behavior
breaks" — is an EXECUTION property.** It is knowable only by running the test
against a broken impl; reading spec/test text never establishes it. (Neo 3rd-pass
ruling, 2026-06-25.)

## North-star fit (the two owner constraints)

1. **Lights-out post-validator** — no human in the loop after spec validation; a
   future feature auto-converts disclosed gaps into the next spec and re-runs.
2. **High confidence / no false confidence** — not 100% perfect implementation,
   but ~99% + faithfully report anything missing, so there is never false
   confidence in what was built. (Yassine measured 96–98% + a defect registry;
   this is the same bar.)

The decisive consequence of (2): a disclosure registry only delivers "no false
confidence" if **the detector of what's-missing has a legible residual.** An
LLM-sensor with an unmeasured false-negative rate just relocates false confidence
up one level (we'd be falsely confident the sensor caught the inert tests). A
mutation gate's residual is *enumerable* ("verified against operator set M;
residual = bugs ∉ M") — that legibility is the whole point.

## Design principles (hard constraints on this build)

- **B (structural, execution-based discrimination) is the spine.** A (spec-text
  precision) is a cheap co-fix; the LLM-sensor is OFF the trust path.
- **No stack-specific or bespoke code in the framework.** No hand-authored
  strawmen, no per-defect rules, no per-language mutation logic.
- **Tool orchestration is automatic + detection-driven off the project**, NOT a
  hard-coded curated tool list. The framework knows an *abstract mutation
  contract*; the concrete tool is detected and declared per-project by the
  architect agent (exactly how `build_cmd`/`test_cmd`/`lint_cmd` already work).
- **Graceful degradation, never hard-fail for a missing capability.** No mutation
  capability available → DISCLOSE "discrimination unverified," ship per the
  blocking rules. The gate reduces *confidence claims*, it does not block on
  tooling absence.
- **No LLM judges discrimination.** Trigger is by AC *type* (structural), not by
  LLM-assessed risk. The sensor's only permitted residual role is a non-gating
  *scheduler* (ordering B under a time budget) and it may NEVER exclude an AC
  from B.

## How tool orchestration stays general (the load-bearing design)

The framework defines an **abstract discrimination capability**, not a tool:

```
discrimination_check(holdout_subset, impl) -> SurvivingMutantReport
  # "Run coverage-guided mutation on the impl lines the holdout_subset COVERS;
  #  report mutants on those covered lines that NO holdout in the subset kills."
```

Three properties make this curation-free:

1. **The concrete command is project-declared, architect-detected.** Extend the
   environment contract with a `mutation` capability (supersedes the existing
   opt-in `MUTATION_CMD` noted in CLAUDE.md). The architect — which already reads
   the project to detect build/test/lint commands — detects whether a mutation
   capability exists or is addable and declares the command (e.g. it finds the
   project's own mutation dev-dependency / script, or adds the ecosystem-standard
   one). The framework's code contains **no tool names**; it runs the declared
   command. A new ecosystem needs zero framework change — only the architect's
   per-project detection.

2. **Line targeting comes from COVERAGE, not an AC→line map.** Standard mutation
   tools are coverage-guided: they mutate lines the tests execute. So "mutate the
   lines this holdout covers, require the holdout to kill those mutants" needs no
   bespoke AC→line wiring — the holdout's own coverage trace supplies the scope.
   This is exactly why run20 is caught structurally: the ghost-token holdout
   *executes* the token-handling lines (covers them) but its assertion (always
   401 via identity failure) fails to *kill* a mutant on the token-acceptance
   path → surviving mutant on a covered line → the holdout is exposed as inert.

3. **Result normalization is by contract/standard, not per-tool parsing.** The
   framework consumes either (a) the cross-tool mutation-report standard schema
   (general-by-standard, the same way a SARIF consumer is tool-agnostic), or (b)
   a minimal exit-code+scope contract the architect's wrapper satisfies ("exit
   non-zero + emit `<path>.json` listing surviving mutants by file:line"). No
   tool-specific output parsing lives in the framework. **Open question for
   review:** which of (a)/(b) is the more general normalization — decide in
   Phase 1.

> Same pattern the pipeline already uses for SAST: it orchestrates the project's
> Semgrep but the framework consumes a normalized finding shape, not Semgrep
> internals. Mutation is the identical move one layer over.

## The gate mechanism (B)

- **Scope = the behavioral-rejection AC slice only** — `security` / `error` /
  `adversarial` (the exact slice the scenario-intent-promotion work already
  isolates; the addressability gate guarantees each such intent HAS a mapped,
  demanded test). Triggered by AC *type*, deterministically. NOT all ACs (cost),
  NOT LLM-selected (no vouching).
- **Runs at the existing gate step**, after implementation, where the holdout
  suite already executes in the ephemeral environment — an extension of an
  existing execution phase, not a new subsystem.
- **Predicate:** for each behavioral-rejection holdout, the mutants on the lines
  it covers must be killed by the holdout (subset). A surviving mutant on a
  covered line ⇒ the holdout executes but does not discriminate ⇒ **inert**.
- **On inert → regenerate** (QA-Holdout, still source-blind, re-derives from
  spec) under the existing iteration cap. **A feeds this convergence:** a
  source-blind QA can only write a *discriminating* replacement if the spec pins
  the discriminating precondition (e.g. "a whitespace-padded *otherwise-valid*
  token → 401"). If the spec is vague, the regenerate loop may reproduce
  inertness → falls through to disclosure. This is why A is a genuine co-fix, not
  just a pre-filter.
- **On still-inert after cap → DISCLOSE** (below), and block iff the AC is
  Non-Negotiable (§8a) — reusing the existing blocking-veto machinery.

## Disclosure grounded in B (the legible-residual carrier)

Extend the defect registry / ship-with-gaps with a **discrimination dimension**,
orthogonal to pass/fail:

- `verified-discriminating` — B confirmed the holdout kills the targeted mutants.
- `unverified` — no mutation capability for this project/path (degraded mode).
- `proven-inert → regenerated` — B caught it, regen fixed it.
- `proven-inert → unresolved` — B caught it, regen did not converge (disclosed;
  blocks iff Non-Negotiable).

Each carries the **mutation-indexed residual**: "verified against operator set M;
residual = behaviors ∉ M." This is the honest carrier the future auto-respec loop
can **converge** on, because B is a *sink* (it clears flags by execution); an
LLM-sensor is a doubt-accumulator with no clearing mechanism and would make the
loop non-terminating (and Goodhart the spec toward grader-pleasing phrasing).

## A — cheap co-fix (authoring time, light, stack-agnostic)

- **`/df-write-spec` guidance:** for each behavioral-rejection AC, state the
  *discriminating precondition* — what makes the rejected input otherwise-valid
  (so a source-blind QA writes a killing test, and B's regen loop converges).
- **Optional deterministic advisory** (non-gating) where the under-specification
  is structurally detectable (a rejection AC whose THEN is a status/error but
  whose GIVEN names no positive qualifier). Kept advisory to avoid the
  semantic-classifier treadmill; **A is NOT the gate — B is.**

## Non-goals / guardrails (explicit)

- ❌ No LLM on the discrimination trust path. Optional non-gating scheduler only.
- ❌ No hard-coded tool list, no per-stack mutation code, no curated strawmen.
- ❌ No hard-fail for missing mutation capability → disclose-and-degrade.
- ❌ A is not a hard gate; do not let "spec looks precise" stand in for execution.

## Phasing (measure before default-ON)

- **Phase 0 — Measurement pilot (no new gate).** On a representative spec
  (spec-5, or a small held-out one), run coverage-guided mutation on the
  behavioral-rejection slice *out of band* and measure: wall-time added, mutants
  per AC, equivalent-mutant noise rate, and whether it flags the run20 inert
  holdout. **This gates whether default-ON is feasible** given the campaign's
  timeout history. Output: a cost/coverage number, not a feature.
- **Phase 1 — Abstract mutation capability + architect detection.** Environment-
  contract `mutation` capability; architect detection/declaration; result
  normalization (decide schema vs exit-code contract); graceful degradation. No
  gate yet — just the capability + a CLI to run it and read the normalized report.
- **Phase 2 — The discrimination gate (B).** Wire the capability into the gate for
  the behavioral-rejection slice; inert → regen (existing cap) → disclose/block.
- **Phase 3 — Disclosure dimension + registry.** The four-state discrimination
  field + mutation-indexed residual; the auto-respec sink contract.
- **Phase 4 — A co-fix.** `/df-write-spec` guidance + optional advisory.

## Open questions for Neo / Linus

1. Result normalization: cross-tool standard schema vs minimal exit-code+scope
   contract — which is genuinely more general and least bespoke?
2. Kill threshold: must the holdout kill **100%** of mutants on its covered lines,
   or ≥X%? (run20 needs the *specific* token-path mutant killed; a percentage
   threshold could let exactly that one slip. Lean strict, scoped narrow.)
3. Equivalent / beneficial mutants on a possibly-buggy submitted impl — how to
   bound this noise so the legible residual stays honest (and the regen loop
   isn't chasing equivalent mutants)?
4. Barrier: the architect declaring a mutation command reads source (allowed) and
   leaks nothing to the source-blind QA (the command is a build capability, like
   `build_cmd`). Confirm against `docs/AUTHORIZATION-MODEL.md`.
5. Does coverage-guided "kill mutants on the holdout's own covered lines" fully
   capture discrimination, or are there inert holdouts that cover the *wrong*
   lines entirely (cover nothing on the targeted path)? If a holdout covers
   nothing on the path, mutation finds no targeted mutants to require — does that
   need a *coverage-of-the-targeted-behavior* check upstream of mutation?
   (Possible gap: an inert test that doesn't even execute the buggy line.)

---

# REVISION (2026-06-25, pm) — Neo + Linus reviews + the run20 trace

Three inputs landed after the draft above: a Neo design review (**REVISE**), a
Linus contract/feasibility review (**FIX-FIRST**), and the run20 validation
trace. They reframe the problem and correct several load-bearing claims. **This
revision supersedes the earlier framing where noted, and replaces the Phasing
section.** Discipline note: the divergence/temperature conclusions at the end are
**MEASUREMENT-GATED — no change is made there until the ablation runs.**

## R1. Reframe: run20 is a four-hole alignment failure (not "ambiguity vs. test")

| Hole | Fix it alone → run20 prevented? |
|---|---|
| **H1** spec ambiguity (whitespace = invalidating *or* normalizable) | **YES** — a faithful impl rejects |
| H2 implementer deviation (`trim`→200 vs stated "401") | yes, not directly actionable |
| **H3** orphan/addressability (`ac_id=—`, invisible to coverage) | **NO** — only forces "a whitespace-shaped test exists," still inert |
| **H4** test inertness (ghost token → 401 for the wrong reason) | **YES** — a resolvable token flips PASS→FAIL on `trim` |

Two **independently-sufficient** defenses: **H1 (kill ambiguity pre-lock)** and
**H4 (a discriminating test — this doc)**. The shipped addressability gate is
**H3 — a precondition, sufficient for neither alone** (exactly what §4 admits).
So discrimination is NOT a derivative of ambiguity: H4 catches a class H1 cannot
(an honest impl bug against a perfectly clear spec, hidden by a lazy source-blind
test). Both stay first-class.

## R2. The run20 trace — a RECALL failure, not a policy failure

Archived `…-validation.status`: **`divergence=LOW`**; the report says *"no
unresolved contract exists across the three sketches."* So the validator's
implementation-divergence sketch **missed** the trim-vs-reject fork — it did not
*ignore* a HIGH, it graded LOW. Consequence: the `detection.py:249`
proceed-on-HIGH policy hole (`log.warning("divergence=HIGH — proceeding (barriers
catch real ambiguity downstream)")`) is **real but SEPARATE** — it did not fire
here, so "make HIGH actionable" would have caught nothing for run20. The run20
upstream hole is **recall** (the grader's false-LOW), not policy.

## R3. Label honesty (Neo) — sensitivity ≠ discrimination [CRITICAL]

A surviving mutant ⇒ `unverified (inert-or-equivalent)`, **never**
`proven-inert`. A passing mutation gate ⇒ `mutation-sensitive`, **never**
`verified-discriminating`. Equivalent/beneficial mutants on a *suspect* impl
always survive (undecidable), so green proves assertion-sensitivity to
perturbation, NOT discrimination of the spec'd behavior. With the original
labels, B becomes a *new structural vouching surface* — the exact thing we
removed from the LLM. Equivalent-mutant bounding is a **Phase-0 EXIT criterion**;
if it can't be bounded below the false-block tolerance, **blocking is off the
table → disclosure-only**.

## R4. Two false precedents in the draft (Linus) — corrected

- The SAST analogy ("§How tool orchestration stays general") is **backwards**.
  `darkfactory/review/sast.py` *hardcodes* `"semgrep"`, vendors its ruleset, pins
  the version, parses Semgrep's JSON internals, and hand-maintains CWE join
  tables — the OPPOSITE of tool-agnostic. The real precedent is
  `environment.toml commands.*` (opaque shell strings + exit code).
- There is therefore **no existing "run a project-declared command and parse a
  rich normalized report" path** in the codebase. The plan is *inventing* one,
  not extending one. The existing `_run_mutation_gate` (`quality_gate.py:2385`)
  only scrapes an aggregate score via a tool-specific regex (`mutation score:
  NN`) — no per-mutant / file:line / per-test / coverage data.

## R5. The generality crux: per-holdout kill ATTRIBUTION (Linus) [MUST-RESOLVE]

The predicate "does *this* holdout kill the mutants on its covered lines" needs
**per-test kill attribution**, which is **rare**: StrykerJS `--coverageAnalysis
perTest` has it; mutmut / cosmic-ray / PIT don't expose it portably. There is **no
adopted cross-tool mutation report standard** (mutation-testing-elements = the
Stryker family). So the only options are: (a) **isolate** — run mutation with only
that holdout in the suite (tool-agnostic but N× cost + per-tool single-test
config), or (b) consume Stryker `perTest` JSON (**JS-only gate in a general
coat**). The exit-code+`<path>.json` contract is buildable but **relocates the
bespoke work into the architect's per-project wrapper** (native→contract
translation + attribution) — moved, not removed. **Say this plainly; do not claim
the treadmill is gone.**

## R6. Cost: a new subsystem, not an extension [correction to lines 107-109]

The holdout suite runs **once**; mutation runs it **once per mutant** (N×M), and
per-holdout isolation multiplies again. `gate_timeout=300s`; this campaign's
history is timeout postmortems. Coverage instrumentation is **net-new** (the gate
collects none today). So B is a new long-running subsystem — Phase-0-gated.

## R7. Barriers (both reviewers) [d2fecbf class — pin before building]

- The inert-signal handed to the **source-blind QA** for regen must be
  **spec-text ONLY** — never mutant `file:line` / source structure (else a
  source-blind agent gets a map of the implementation).
- The surviving-mutant report is a **map of holdout blind spots** (test-gaming
  gold for an overfitting implementer) → **must be masked from the Implementer**
  (it already would be under `.dark-factory/reports/` masking — state it).

## R8. Graceful degradation must be LOUD (Linus)

"No capability → disclose unverified, ship" must be a **counted, non-zero-cost
residual surfaced at ship** ("X of Y behavioral-rejection ACs unverified"), NOT a
silent SKIP (the existing `quality-gate.sh:1791` `SKIP` is the bug). Otherwise the
equilibrium is: every architect declares no capability / one that times out →
gate inert-by-default = the exact failure lifted to the gate level. The future
auto-respec loop must be FORCED to act on the unverified count.

## R9. NEW — the cheap deterministic candidate: positive-control / differential assertion

Every behavioral-rejection holdout must **also assert that the SAME fixture
WITHOUT the rejection-trigger SUCCEEDS** — so the rejection is *attributable* to
the targeted input, not to an independent cause. run20's ghost-token test **fails
this automatically** (a clean ghost token also 401s → the success leg fails →
exposed). Properties: **deterministic, stack-agnostic, no mutation engine, no
LLM.** It also closes this doc's open-Q#5 (the "test never executes the buggy
line" inert case) more cheaply than coverage-guided mutation. Enforced by: QA
prompt (write the paired control) + a structural lint (the rejection holdout
contains a success leg on the same fixture) + execution (both legs must hold).

**This is a CANDIDATE to be the PRIMARY H4 defense, with mutation demoted to a
high-stakes/Non-Negotiable fallback.** It needs its own measurement (Phase -1):
does it catch run20 in practice, and what is its false-positive rate on
legitimate rejection tests that have no natural positive control (e.g. malformed
input where there is no "valid-except-for-X" sibling)?

## R10. B's first-class status — preserved but reframed (Neo Q5)

B's claim is NOT "catches run20" (H1 + the positive-control can). It is
**constraint-2: precision/disambiguation is a doubt-REDUCER (no legible
residual); mutation is a doubt-MEASURER (enumerable residual `∉ M`).** A measurer
is what makes the disclosure registry honest and the auto-respec loop a
terminating *sink*. So a measurer stays in the architecture — but the cheap
positive-control likely covers most of the H4 class, leaving mutation as the
high-stakes backstop. **Decide the split after the Phase -1 / Phase 0
measurements, not before.**

## R11. Revised phasing (measurement-first — replaces the earlier Phasing)

- **Phase -1 — MEASUREMENT ONLY, no build, no mechanism decisions.** (a) the
  divergence-recall ablation (R12); (b) a positive-control catch + false-positive
  probe against run20 and a handful of rejection ACs; (c) the mutation two-tool /
  two-ecosystem CONTRACT-feasibility probe (R5) incl. equivalent-mutant rate.
- **Phase 0 — feasibility gates.** Expand the old Phase 0 from one-spec cost to
  two-tool/two-ecosystem contract feasibility; equivalent-mutant bounding is an
  EXIT criterion (R3). If only Stryker satisfies the contract → mutation is
  JS-only → fall back to positive-control as the general gate.
- **Phase 1 — disclosure label-semantics + state machine FIRST** (R3; must
  precede any blocking).
- **Phase 2 — positive-control lint** (cheap, deterministic) — likely the first
  real gate.
- **Phase 3 — mutation gate** only if Phase-0 feasibility passes; else mutation
  stays opt-in / high-stakes-only.
- **Phase 4 — A authoring guidance** (run before/parallel to the ablation to
  isolate its lift).

## R12. Upstream — divergence recall + temp=0 (MEASUREMENT-GATED; NO CHANGE YET)

The validator graded run20 `divergence=LOW`. The `temp=0` pin on `SPEC_VALIDATOR`
(`agents.py:164`, chosen for score-variance compression) is a **predicted**
co-cause of modal-collapse / under-recall — its own watch-item anticipated this —
**alongside** the orphan-ness (no named AC for the sketch to diverge against) and
the soft "(not a server crash)" wording. Three entangled co-causes, **N=1**.

Deeper issue: divergence is a **distributional** quantity (how much do valid
interpretations spread) being measured as a **single-sample point estimate**.
`temp=0` = a fixed point (apparent variance 0 → systematically LOW); `temp>0`
single = a noisy point; **neither measures spread — an ENSEMBLE does.** So "just
raise the temperature" is NOT the fix (single sample, re-adds score variance).

**DECISION GATE — do NOT change `temp=0` or the divergence mechanism until this
ablation runs and is read:**
- Matrix: `{temp=0 single, temp>0 single, temp=0 ensemble, temp>0 ensemble}` ×
  `{orphan SI-3, named AC-13, tightened wording}` on the whitespace contract.
- Measure: does the sketch grade the trim-vs-reject fork **HIGH** (recall)?
- Only then decide whether to: decouple score from divergence, anchor the sketch
  on named ACs (addressability already does this — measure its lift), and/or move
  divergence to an ensemble. Until measured: **`temp=0` STAYS; divergence STAYS a
  non-gating sensor + conservative-default pin.**

## R13. Net leverage ranking (post-revision)

1. **Addressability gate (shipped, this branch)** — deterministic, false-blocks
   nothing, kills H3 AND gives the divergence sketch a named target (recall lift,
   to be measured). Validate end-to-end (the paused spec-5 re-run).
2. **Positive-control / differential requirement (R9)** — cheap, deterministic,
   stack-agnostic; directly kills the H4 inertness class. Candidate primary H4
   defense. Measure first (Phase -1).
3. **Mutation gate (B)** — first-class as the doubt-MEASURER / legible-residual
   carrier, but cost- and feasibility-gated (Phase 0); high-stakes backstop if
   the contract isn't general.
4. **Divergence-recall / temp=0** — MEASUREMENT-GATED; sensor only until the
   ablation is read.

---

# PHASE -1 MEASUREMENT RESULTS

## M-11 — positive-control / differential probe (task #11)

**Catch (run20): CONFIRMED.** The differential requirement ("the same fixture
WITHOUT the rejection-trigger must produce the success outcome") forces AC-13's
positive leg to be "clean token for a SEEDED user → 200." run20's ghost-token
whitespace holdout fails that leg automatically (clean ghost token → 401
USER_NOT_FOUND, not 200) → exposed as non-attributable. No mutation, no LLM.
(Grounded in this session's Neo+Codex verification of the real holdout source:
`validFormatToken()` signs a non-resolvable ghost user; the double-space test
asserts only `not.toBe(500)`.)

**False-positive analysis (spec-5 AC-1..AC-15):**

| Class | ACs | Differential applies? |
|---|---|---|
| Input-attributable rejection (clean valid sibling → success) | 1,2,4,5,8,**13**,14,11,12 | YES — load-bearing; forces a resolvable fixture, kills the ghost-token class |
| Isolation / quantity property | 9,10 | YES under a *generalized* differential (assert included AND excluded case on one fixture) |
| Non-attributable INVARIANT (response/log content) | 3,15 (no secrets) | NO — mis-fires; no success/failure leg on a targeted input |
| Out of slice / other vehicle | 6 (happy path), 7 (subprocess) | N/A |

**Finding:** powerful + load-bearing for the input-attributable-rejection class
(9/15, incl. run20); genuinely mis-fires on pure invariants (AC-3, AC-15). The FP
**dissolves with the enforcement shape**: NOT "every security AC needs a positive
control," but **"the AC declares a differential pair (both legs, one fixture); a
structural lint requires the holdout to honor both; execution verifies both."**
Invariant ACs have no differential pair to declare → naturally exempt, and already
route to the existing forbidden-pattern/content scanners (`patterns.py`), not the
discrimination gate. No semantic classifier, no per-stack code.

**Verdict:** positive-control is a **viable PRIMARY H4 defense** for the rejection
class (deterministic, stack-agnostic, no mutation/LLM), enforced as
authoring-declared differential + structural lint + execution. It **demotes
mutation (B)** to (a) non-rejection behavioral classes the differential can't
express and (b) an optional high-stakes legible-residual measurer — confirming
#12's lowered urgency. **Honest limit:** analytical walk over one spec's 15 ACs +
the verified run20 holdout; the FP *rate* is extrapolated from the (robust)
structural classification, not measured at corpus scale → a multi-spec corpus
check is the follow-on before making it a hard gate.

## M-10 — divergence-recall ablation (task #10)

Three cuts, all evidence-grounded (no full-pipeline runs needed):

**Cut 1 — named-AC + firmer wording, holding temp=0 (free, from artifacts):** the
ORIGINAL orphan spec-5 graded `divergence=LOW`; the REVISED spec-5 (named AC-13 +
"401 with code UNAUTHORIZED" wording) **ALSO graded `LOW`**, identical reasoning
("no unresolved contract across the three sketches"), whitespace never surfaced as
an axis in either. → **Naming the AC alone did NOT lift recall at temp=0.**

**Cut 2 — ensemble on the whitespace contract (5 independent samplers, pointed at
the axis):** **5/5 graded `DIVERGENCE: HIGH`, `AGREE: no`**, each surfacing the
exact trim→200 vs reject→401 fork, each blaming the "(not a server crash)"
parenthetical for licensing the lenient read. → **temp=0 modal-collapse
CONFIRMED**: the identical contract the single temp=0 validator calls LOW, a
5-sample ensemble calls HIGH unanimously. Ensemble fixes **grading** recall.

**Cut 3 — axis-SELECTION (3 samplers given full FR-2, asked to RANK divergence
axes, unprompted about whitespace):** **0/3 surfaced the trim-vs-reject fork.**
All read intent-3's "returns 401" at face value → treated the only divergence as
*which 401 body*. All 3 instead surfaced OTHER real FR-2 divergences the pipeline
validator also missed (unmapped USER_NOT_FOUND→status; missing-claim→500). →
**Ensemble does NOT fix axis-selection recall**; the affirmative "returns 401"
wording suppresses the fork even for independent samplers, AND the grader's
recall gap is systemic (multiple missed axes), not a one-off.

**Verdict:**
- The dominant lever for the rejection class is **A-layer WORDING precision**, not
  the grader: state the discriminating contract ("a whitespace-padded
  OTHERWISE-VALID token MUST be rejected; the server MUST NOT trim/normalize
  before verification"). "Returns 401" asserts the outcome and hides the fork.
- **Do not rely on the divergence grader (even ensembled) as the ambiguity gate** —
  axis-selection recall is unreliable. Ensemble is a marginal grading improvement
  that re-adds the score variance the temp=0 pin protects (would need
  score/divergence decoupling for a now-smaller benefit).
- This **elevates the downstream positive-control net (M-11)**: it catches exactly
  the case the upstream grader cannot be trusted to surface.

**Honest limits / confounds:** ensemble samplers are a proxy (different
model/context than the pinned validator, default temp; Cut-3 used FR-2 alone, not
the whole spec — a *favorable* condition that still missed it, so the whole-spec
validator would do no better). N is small. These measurements INFORM; per the
owner's gate, no change to `temp=0` or the divergence mechanism is made on them —
the actionable output is (a) A-layer wording guidance for rejection ACs, (b)
positive-control as the reliable net, (c) ensemble-the-grader DEMOTED to low
priority.

## M-12 — mutation two-tool feasibility (task #12) — capability assessment

Empirical two-ecosystem run **DEFERRED as low-leverage** (M-11 demoted mutation to
a fallback; no mutation tooling present locally — only `npx stryker`, mutmut/
cosmic-ray absent; bb declares no capability; a real run = multi-hour setup). The
architectural verdict is decidable from tool *capabilities* (verifies Linus R5):

**Per-test kill attribution (the generality crux):**
- StrykerJS `--coverageAnalysis perTest` — HAS it (which test killed which
  mutant). **Stryker family only** (JS/TS; Stryker.NET; Stryker4s).
- mutmut, cosmic-ray (Python), PIT (Java) — run the suite per mutant, report
  survived/killed against the WHOLE suite; **no portable per-test attribution**.
- The "mutation-testing-elements" JSON schema = **Stryker-org standard** (Stryker
  family adopts; others need third-party converters). → "consume a standard
  report" = consume Stryker's = **JS-family, NOT general.** Confirmed.

**The only tool-agnostic path is ISOLATE** (run mutation with only the target
holdout in the suite — works on any tool via the test command). Cost:
`N_holdouts × (M_mutants × 1 test-exec) + coverage`. It relocates a small
per-ecosystem knob ("scope to one test": Stryker `--testFilter`, pytest `-k`,
vitest `-t`) into the **architect's declared command/wrapper**, not framework code
— so the framework stays tool-agnostic, but the treadmill is *relocated, not
removed* (as R5 said).

**Cost (isolate, run20 file):** auth.middleware.ts ~50 lines → ~20-40 mutants on
the token region; 1 isolated holdout → seconds–~1 min; ~7 behavioral-rejection
holdouts → minutes. Bounded for a small feature *because* of the
behavioral-rejection-slice scoping; scales with file-size × holdout-count → tens
of minutes on a large spec (collides with `gate_timeout=300s`).

**Equivalent-mutant rate:** undecidable in general; empirically ~5-20%. On a
suspect impl these are false-survivors → cannot block on a bare survivor; the
equivalent fraction must be part of the disclosed residual (keeps it legible,
honest).

**Verdict:** the tool-agnostic contract IS achievable but **only via isolate**
(N× cost + relocated per-project scope-knob); the standard-report path is
Stryker-only. Given M-11, mutation's residual value is (a) non-rejection
behavioral classes the differential can't express, and (b) a legible-residual
MEASURER for high-stakes/Non-Negotiable ACs — both **OPT-IN / high-stakes only,
NOT a default gate.** Re-trigger the empirical two-ecosystem campaign only when a
real spec needs mutation for a class positive-control cannot cover.

## M-11b — positive-control behavior: catch-and-fix vs escalate

The positive-control's core contribution is **converting a silent green-but-inert
test into a RED one** that re-enters the pipeline's existing catch-and-fix loop
(classify → regenerate/implement → iterate, within the retry cap). Before, the
inert test passed green and the issue was *invisible* — nothing could act on it.
So it is mostly **catch-and-fix during the run**, not escalate:

- **Inert test (run20 class) → caught + fixed autonomously.** Mostly *preventive*
  (forces the QA to write a seeded differential pair, so the ghost-token test is
  never generated); if a non-conforming holdout slips, the structural lint
  (missing leg) or execution (clean leg → 401 not 200) catches it → regenerate
  (source-blind QA, within cap). Converges autonomously **provided the spec
  states the discriminating case** (the A-layer wording — the QA needs a "valid"
  case to seed).
- **Impl bug (`.trim()`) → caught + fixed autonomously.** The discriminating
  holdout runs RED against the buggy impl (clean → 200 ✓, whitespace → 200 ✗
  where 401 expected) → implementer iterates on behavioral feedback → conforms.
  Standard TDD loop, within cap.
- **Escalation is only the cap-exhausted fallback** (QA can't produce a passing
  control because the spec has no clear valid sibling; or the implementer can't
  pass the discriminating test in the retries).

**Caveat — genuine spec ambiguity is resolved by FIAT, not escalated.** If
trim-tolerate vs reject is genuinely underspecified and the QA wrote the *reject*
reading, the red test makes the implementer conform to *reject* — a
self-consistent result that **may not be the author's intent**, with no signal a
choice was made. The positive-control only knows the test is *attributable*, not
that its interpretation matches the author's. **Only A (pre-lock disambiguation)
closes this** — which is why M-10/M-11 land on both levers, not one.

**Build wrinkle — routing the red correctly (must design, not assume).** A red
"clean leg → 401" is ambiguous to the defect classifier: it could be an impl bug
(impl wrongly rejects valid tokens) OR a test-fixture bug (ghost user). Mis-routed
to the implementer, it wastes the cap "fixing" a non-bug → escalate. So the
positive-control build MUST specify the enforcement point + routing. Cleanest
candidate: enforce that a rejection holdout's success leg reuses a
**known-resolvable fixture the happy-path scenarios already establish** (the spec
defines the seed; a ghost token violates it) — so the success leg is guaranteed
to pass for a correct impl, and a failing success leg classifies as a
**fixture/holdout defect → regenerate**, not an impl defect. Resolve this in the
positive-control design phase.

---

# P1 DESIGN — positive-control build (spike complete)

## Spike findings (verified in code)
- **Defect classifier taxonomy** (`agents/defect-classifier.md`): `IMPL_BUG /
  SPEC_AMBIGUOUS / ENV_ISSUE / SCENARIO_CRASH / BRITTLE / OUT_OF_SCOPE / UNKNOWN`
  — **no fixture/holdout-defect category.** A runtime control-leg failure would
  mis-route to `IMPL_BUG`. → route deterministically, NOT via the classifier.
- **`scenario_lint.py`** (`lint_file`/`lint_dir`/`LintIssue`, rule constants) +
  **`te_pre_acceptance_check`** config + `_step_te_pre_acceptance` orchestrator
  step (runs the lint with retries before acceptance) = the gen-time hook.
- **`HOLDOUT_FAILED_IDS`** (sidecar → `gate_result.holdout_failed_ids`) reports
  per-test failures by OPAQUE id → enables **per-leg deterministic routing**.
- `agents/qa-holdout.md` = the prompt to change. Barrier-clean (QA stays
  source-blind; both legs are spec-derived; it seeds per the spec data model).

## The differential pair (the convention)
For each behavioral-rejection AC (security/error/adversarial slice), the
QA-Holdout writes TWO framework-recognizable, fixture-sharing holdout tests:
- **CONTROL leg** — the clean/valid input on a spec-seeded resolvable fixture →
  success (non-401). Carries a marked id (e.g. `hb:<id>` + a `control` marker).
- **REJECTION leg** — the decorated input (whitespace/expired/etc.) → 401.

## Enforcement at two points (reuses existing machinery; no new subsystem)
1. **Gen-time structural lint** (new `scenario_lint.py` rule, run via the
   pre-acceptance step extended to holdouts): a behavioral-rejection holdout MUST
   contain a marked control leg paired with its rejection leg. Catches **missing
   controls** deterministically, no impl, no LLM.
2. **Runtime deterministic routing** (orchestrator, on `HOLDOUT_FAILED_IDS`):
   - a failing **control-leg id** → FIXTURE/inert-holdout defect → **regenerate
     the holdout** (source-blind QA), NOT the implementer. Bypasses the classifier
     mis-route entirely (the id is framework-marked → deterministic).
   - a failing **rejection-leg id with the control passing** → the discriminating
     signal → normal `IMPL_BUG` / `SPEC_AMBIGUOUS` classification → implementer.
3. **Disclose/escalate**: if regen can't produce a passing control within the cap
   → disclose "discrimination unverified for AC-N (no attributable control)" +
   block iff Non-Negotiable (existing veto machinery).

**Why structure-at-gen + semantics-at-runtime (not static ghost detection):** the
lint checks STRUCTURE (both legs present + tagged) — fully general, no test
parsing. A ghost fixture slips the structural lint but its control leg FAILS at
runtime → routed to regen (not impl). So ghosts are caught + routed correctly
without any stack-specific "is this fixture seeded" analysis.

## Properties
- **Catch-and-fix, not escalate** (escalate only on cap-exhaustion) — restores the
  M-11b behavior with correct routing.
- **~Zero extra runtime** (one more HTTP assertion per rejection holdout; no
  mutation, no N× blowup).
- **Stack-agnostic / no bespoke** (a test-STRUCTURE requirement + a framework-level
  marker tag; the lint checks tag presence, not language semantics).
- **Barrier-clean** (QA source-blind; legs spec-derived).
- New pieces only: one lint rule, the QA-prompt convention, the deterministic
  per-leg routing, the disclosure state. No new defect category, no new subsystem.

## Open questions for Neo/Linus (before code)
1. The control-leg MARKER: how does the QA reliably mark the control leg so the
   gate's opaque-id path and the orchestrator routing can recognize it, without a
   stack-specific naming convention? (reuse the `hb:` holdout-bridge id scheme?)
2. Pairing integrity: how does the framework know control-leg X pairs with
   rejection-leg Y (same AC, same fixture) deterministically? A shared anchor +
   a `:control` suffix on the id?
3. Does extending the pre-acceptance lint to HOLDOUTS (currently TE-scenarios)
   cross any barrier or duplicate the QA's source-blind generation contract?
4. The "seeded resolvable fixture" the control leg needs — does the QA have the
   spec's seed/data-model to construct it source-blind, or does this need a
   spec/fixture convention (ties to P2 wording)?

---

# P1 REVIEW — REVISE (Neo + Linus). Build design is unsound + under-counted.

Concept holds (M-11 catch is real, off the LLM trust path). The BUILD as drafted
has correctness holes and 3 net-new subsystems mislabeled as "reuse." Required
changes before any code:

## Unsound core [Neo #1 — highest-risk]
"control-leg-fail ⇒ fixture defect ⇒ regenerate holdout" is **wrong**. A control
leg fails in TWO disjoint cases: (A) ghost fixture → fixture defect (regen
correct); (B) the impl wrongly REJECTS a valid token — a real **IMPL_BUG**
(impl-too-strict, the symmetric twin of run20, the differential's *uniquely
valuable* catch). Routing both to QA-regen **masks the impl-too-strict bug** →
lights-out REGRESSION (today's holdout-fail→implementer path routes it correctly;
the new per-leg router breaks it). The id says WHICH leg failed, never WHY.
**Fix:** disambiguate by the AC's mapped HAPPY-PATH **visible scenario** status —
control-fail + happy-path GREEN ⇒ fixture defect → regen; control-fail +
happy-path RED/absent ⇒ IMPL_BUG → implementer. **Bonus: routing on the visible
scenario (Implementer-readable, not masked) AVOIDS the passed-holdout-id barrier
widening below.**

## Attribution is unenforceable [Neo #4, Linus Q1/Q2]
The lint checks the control TAG, not the ATTRIBUTION (same fixture modulo
trigger). A QA can pass lint + execution with control=`userA-clean→200`,
rejection=`userB-ghost-padded→401` — both green, **inertness recurs at the
pairing level (run20 one level up)**. A shared `pair_id`/`fixture_id` is a
QA-*declared* proxy, not structural proof. → enforce via **manifest-consistency**
(every rejection entry has a paired control entry), and **DISCLOSE that
attribution is a declared proxy, never proven** (R3 label-honesty applied to
pairing). The gen-lint catches MISSING controls, NOT broken ones — the run20
catch needs EXECUTION + sound routing (so it is NOT a cheap gen-lint-only).

## Scope correction — THREE net-new subsystems, not "reuse" [Linus C1-C4]
- **C1 — holdout gen-lint is net-new.** `_step_te_pre_acceptance`
  (`orchestrator.py:4594`) hardcodes the TE *scenarios* dir and retries the **TE**
  agent. Linting holdouts = new target dir + new **QA-container** retry path.
- **C2 — there is NO router on holdout ids today (load-bearing).**
  `holdout_failed_ids` has ONE consumer (`iteration.py:6538` →
  `build_holdout_feedback` → implementer feedback string). No fork. And the opaque
  `hb:` token can't carry leg-type: `holdout-bridge.json` is 2 fields
  `{test_id, spec_anchor}` and `qa-holdout.md:47` **forbids** adding fields. →
  schema extension (`leg`,`pair_id`) against a prompt that forbids it + a new
  consumer.
- **C3 — mid-loop holdout-regen does not exist.** The iteration loop re-invokes
  the **Implementer** only; the QA runs **once**, pre-loop. Control-fail→regen =
  a new iteration branch re-invoking the QA container.
- **C4 — confirming "control PASSED" needs passed-ids the sidecar deliberately
  omits** (barrier-minimal; `scenarios.sh` extracts only failing tokens). Emitting
  passed-ids is a **d2fecbf-class barrier widening** → AUTHORIZATION-MODEL.md.
  **AVOID by routing on the visible happy-path scenario (Neo #1) instead.**

## Honest framing fixes
- **Catch-vs-disclose [Neo #2]:** it's **disclose-by-default unless the spec
  affords a constructible valid-credential path**; catch-and-fix overstated. Needs
  a corpus measurement of the constructible-control rate, and a validator-checked
  guarantee the success leg is constructible from spec-visible contracts.
- **By-fiat residual [Neo #5]:** wire the deterministic A-advisory
  (under-specified rejection AC, doc line ~146) into the disclosure registry as an
  **"interpretation-locked / not author-confirmed"** state — don't defer
  fiat-legibility entirely to P2.
- **Separate regen budget [Neo #3, Linus Q3]:** runtime holdout-regen must NOT
  share the implementer `max_iters` (starvation/premature-escalation); mirror
  `te_pre_acceptance_max_retries`.
- **Adversarial narrowing [Neo #3]:** the control mandate attaches to DECLARED
  differential ACs ONLY; never constrain free adversarial exploration / invariant
  ACs (those route to `patterns.py`). Partition must be spec-declared, not a
  per-test LLM call.
- **PASS/FAIL only in framework code [Neo #6]** — no `401`/`200` constants
  (stack-agnosticism).

## Revised cost verdict
P1 is a **multi-subsystem, routing-touching build** (holdout gen-lint + per-leg
router + mid-loop holdout-regen + manifest schema ext + disclosure states), NOT a
cheap lint. M-11's "cheap primary defense" was right at the test-SHAPE level and
**understated the pipeline-integration cost**. It is barrier-CLEAN if routing uses
the visible happy-path scenario (avoiding the passed-id widening) — that's the key
design choice that keeps it out of AUTHORIZATION-MODEL territory.

## P1 end-to-end routing design (resolves Linus must-resolve; barrier-clean)

**Data:** `holdout-bridge.json` entries gain optional `leg: control|rejection` +
`pair_id` (`_load_manifest` already passes unknown keys through; QA prompt
amended to emit them for differential ACs). Back-compat: absent ⇒ ordinary
holdout.

**Gen-time (host-side lint, new):** for each AC **declared differential**, the
manifest must contain a control+rejection pair sharing `pair_id`
(manifest-consistency — guarantees the control EXISTS). Missing ⇒ QA-regen (new
QA-retry path, separate budget). Catches MISSING controls. No source-parse, no
per-stack code.

**Runtime (host-side router in `iteration.py`, beside the existing manifest load
at 6533):** for each failed id look up its `leg`:
- `rejection` failed, paired `control` NOT in failed_ids ⇒ rejection is
  attributable ⇒ IMPL_BUG/SPEC_AMBIGUOUS ⇒ existing implementer feedback.
- `control` failed ⇒ cross-check the AC's mapped VISIBLE happy-path scenario
  (Implementer-readable, NOT masked): green ⇒ fixture defect ⇒ QA holdout-regen;
  red/absent ⇒ IMPL_BUG (impl-too-strict) ⇒ implementer. **(Neo #1 fix.)**

**Why barrier-clean (no passed-id widening, resolves Linus C4):** gen-lint
guarantees the control EXISTS; the gate integrity predicate (passed+failed==total,
no skips) guarantees it RAN; so "control NOT in failed_ids" ⇒ it PASSED — inferred
without emitting passed-ids. Routing uses the visible happy-path scenario status,
not masked holdout internals.

**Honest residuals (disclose, don't claim):** same-fixture attribution is a
QA-declared `pair_id` proxy, never proven; constructible-control depends on the
spec; by-fiat ambiguity → an "interpretation-locked" disclosure state fed by the
A-advisory.

**DEPENDENCY → P2 first:** the "declared differential" trigger is an AUTHORING
signal (which rejection ACs are input-attributable vs invariant). Per M-11 + Neo,
this partition must be spec-declared, not an LLM call. So P1's lint/router consume
a P2 authoring concept. **Build P2's differential-declaration authoring lever
first; P1 builds on it.**

## M-P2 — differential-rejection authoring guidance: EFFECTIVE (measured)

Paired experiment on the run20 whitespace contract — 3 authors WITHOUT the
`/df-write-spec` differential guidance vs 3 WITH it; same intent
("whitespace token returns 401 (not a server crash)"); measure whether the output
AC is *discriminating* (names an otherwise-valid baseline that MUST succeed + a
no-normalize clause).

- **Control 3/3 — under-specified** (run20-class): "whitespace token → 401 + no
  5xx/crash"; NO valid baseline, NO no-normalize clause.
- **Treatment 3/3 — discriminating:** each named an explicit positive control
  ("the same token sent clean MUST return 200") AND a "MUST NOT trim/strip/
  collapse/normalize/coerce" clause (one: "no lenient coercion that would let a
  padded token authenticate").

**Verdict: clean 0/3 → 3/3 flip.** The guidance reliably produces the attributable
differential the run20 spec lacked. P2 (the authoring lever) is confirmed
effective. (Limit: N=3 on one contract; the flip is unambiguous but a broader
contract set would tighten the estimate.)

## M-P1 — does the QA write an HONEST differential? 4/4 YES → router DEFERRED

Probe: 4 source-blind QA agents, each given the discriminating AC-13 (a P2 output)
+ the differential-pair convention + the spec data model, asked to write the two
legs + manifest. **4/4 wrote an honest pair:** control leg SEEDS a real
user/tenant and signs a resolvable token → asserts 200; rejection leg = SAME
fixture + only the whitespace → asserts 401; correct `leg`/`pair_id` manifest.
**Zero ghosts.** (Limit: probe, contrived data model — not a full pipeline run;
P3 is the end-to-end confirmation.)

**Consequence — evidence-driven re-scope of P1:**
- The run20 inertness is PREVENTED AT GENERATION by the cheap, sound, MEASURED
  levers: **P2 (discriminating spec) + the QA differential convention**. Both
  built + measured (M-P2 0/3→3/3; M-P1 4/4 honest).
- The **gen-time pairing lint** (`differential.py`, built + 12 tests) is the cheap
  deterministic backstop for the "QA omitted the control" case — wire it into a
  holdout pre-acceptance step when desired (net-new per Linus C1, barrier-clean).
- The **runtime router + mid-loop holdout-regen (Stage 3)** is DEFERRED: Linus's
  re-pass found it not cleanly buildable (needs a net-new anchor→positive-control-
  test→PASS/FAIL join + per-id ran-certification that pressures the passed-id
  barrier), AND M-P1 shows it's a low-frequency backstop (the QA reliably writes
  honest controls). **Re-trigger:** a real run (P3) showing a QA dishonest/broken
  control that self-fails and mis-routes.

**Honest residual (no false confidence):** deferring the router means the
"dishonest control" case (rare per M-P1) relies on QA compliance, not a structural
execution guarantee — a bounded, documented gap. A dishonest control self-FAILS
its own 200 assertion at the gate (it can't pass on a ghost), so it is never a
silent green; the only deferred part is auto-ROUTING that failure (today it routes
via the existing implementer-feedback path — a known, bounded mis-route, not a
mis-ship).

## Net conclusion (post-measurement)
The run20 mis-ship class is closed by **THREE cheap, sound, measured, stack-
agnostic levers**, not the heavy mutation/router machinery we started with:
1. **Addressability gate** (shipped) — makes the behavioral intent a demanded,
   named AC.
2. **P2 differential-wording** (built + measured 0/3→3/3) — makes the spec
   discriminating (kills the H1 ambiguity that suppressed the fork).
3. **QA differential convention** (built + measured 4/4) — makes the holdout
   attributable (kills the H4 inertness at generation).
Backstops, by cost: gen-lint (built, wire when desired) < runtime router
(deferred, measured-rarely-needed) < mutation (opt-in, high-stakes only). The
divergence grader stays a non-gating sensor (M-10).

## M-gate — validator differential-classification gate: EFFECTIVE (measured)

Built (Neo design ruling): `check_differential_markers` + `check_differential_
intent_blockers` in precheck — a MAPPED security/error/adversarial AC lacking a
§8 `disc:differential`/`disc:invariant: <reason>` marker → REVISE (disjoint from
the orphan gate; deterministic; false-blocks nothing; reason required on invariant;
opt-outs counted+disclosed loudly). 18 tests (gate + pairing + slice). Full unit
suite 4720 passed.

**Cargo-cult probe (Neo's highest-risk assumption):** 4 authors given ONLY the
gate's REVISE feedback on the original under-specified AC-13. **4/4 produced a
GENUINE differential** — `disc:differential` + an otherwise-valid baseline that
MUST succeed (200) + a MUST-NOT-normalize/trim/coerce clause. **Zero bare stamps.**
Robust to marker placement (criterion cell / 4th column / parenthetical all
parse).

**The chain is now empirically validated end-to-end (authoring side):**
- M-gate 4/4: the VALIDATOR surfaces the need (REVISE) → genuine differential
  content. (Closes the loop the owner's rule requires — the fix is no longer
  skill-prose-only.)
- M-P2 0/3→3/3: the skill guidance → discriminating differential.
- M-P1 4/4: the source-blind QA → honest control+rejection pair.

**Residual (disclosed, not closed):** the gate forces CLASSIFICATION, not CONTENT
— a marked-but-weak AC slips (caught downstream by execution + QA honesty);
`disc:invariant` is an unverifiable opt-out (bounded by reason+disclosure); by-fiat
ambiguity persists. N is small (4) and the meta-agent's optimization target is the
gate itself — a corpus-scale cargo-cult measurement remains the real confirmation.
