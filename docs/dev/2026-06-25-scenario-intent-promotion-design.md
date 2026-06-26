# Scenario-Intent Promotion — Design Plan

**Date:** 2026-06-25
**Motivation:** run20 (spec-5 `auth-schemas-middleware`) mis-shipped a real spec
deviation: `auth.middleware.ts` `.trim()`s a whitespace-padded Bearer token and
accepts it (→200), where FR-2 Scenario-Intent #3 requires 401. Dual review
(Neo + Codex) converged on a structural root cause: **FR-2 SI-3 is an
*orphan* Scenario-Intent — `ac_id = —` — so it is invisible to every coverage
mechanism by construction.** It exists in the manifest only as a per-anchor
*count*; no addressable anchor demands a scenario for it.

This plan closes the **addressability gap**. It is the necessary precondition for
(but explicitly NOT the same as) the separate discriminating-power/mutation
follow-on. See "Scope boundary" below — we must not over-claim.

---

## 1. Current state (verified against code)

### The §9 Scenario-Intent table already carries a mapping column
```
### FR-2: Implement JWT authentication middleware
| # | Intent                                                                  | Layer    | ac_id |
|---|-------------------------------------------------------------------------|----------|-------|
| 1 | Expired JWT to a protected route returns 401 with code `UNAUTHORIZED`   | error    | AC-1  |
| 2 | Malformed Authorization header ... returns 401                          | error    | AC-2  |
| 3 | Bearer token with leading/trailing whitespace ... returns 401           | security | —     |   ← ORPHAN
| 4 | Failed auth response body contains no token content ...                 | security | AC-3  |
| 5 | Neither CLERK_JWKS_URL nor JWT_SECRET configured ... startup failure    | edge     | AC-7  |
```

### How the pieces consume it today
- `extract_anchor_manifest` (`darkfactory/spec/precheck.py:738`) walks §9 row by
  row. For each row it credits (a) the current `### FR-N` sub-heading anchor and
  (b) **any anchor id appearing inline in the row text**. So a *mapped* intent
  (`... AC-1`) increments `declared_intents["AC-1"]` AND `["FR-2"]`; an *orphan*
  (`—`) increments only `["FR-2"]`. Everything is stored as **`dict[anchor → int]`
  counts** — the intent itself is not addressable.
- `compute_coverage` (`darkfactory/scenarios/coverage.py:294`) demands **≥1
  scenario per FR/AC/EC anchor** (`uncovered_anchors`). `0 < count <
  declared_intents` → `weakly_covered`, which is **"Oracle prior only, NOT a gate
  trigger"** (`coverage.py:54`).
- `check_anchor_intent_coverage` (`precheck.py:845`) is the only intent-related
  *gate*: it requires every declared FR/AC to **appear** in §9. It feeds `passed`
  (soft-block / REVISE), not `fatal` (`precheck.py:2198`). It checks **anchor →
  intent presence**, never **intent → anchor mapping**.

### The hole
There is no check that a §9 intent *maps to* an addressable anchor. An orphan
security/error intent rides its parent FR's single-scenario floor: as long as FR-2
has ≥1 scenario (for any of its 5 intents), the floor passes and the whitespace
contract can be silently uncovered. That is exactly what happened.

---

## 2. Goal

Make every *testable behavioral* Scenario-Intent an **addressable, demandable**
coverage obligation, so a security/error intent cannot be silently left uncovered.

---

## 2a. First-round review (REVISE) — what changed

A first design-review pass (code-grounded, not yet a true Neo ruling — the
`neo:neo` post-processor returned only usage docs; re-running) returned **REVISE**
and materially improved the design. Verified findings, now folded in:

- **The original "validator BLOCK forcing AC-promotion" was wrong.** It would turn
  the canonical (already-shipped) spec into a **7-blocker REVISE**: orphan
  (`ac_id = —`) counts are FR-1 ×5, FR-2 ×1, FR-3 ×2, Cross-Cutting ×1, and most
  of FR-1's (RLS isolation, FK integrity, unique constraint) are **correctly**
  orphaned — covered by the `[[tenant_isolation]]` substrate primitive + DDL, not
  by per-AC scenarios. Forcing the author to promote each is the authoring
  treadmill we reject.
- **The orphan bug is a `weakly_covered` case the framework already computes and
  then ignores.** `compute_coverage` flags `0 < count < declared_intents` as
  `weakly_covered`, explicitly *"Oracle prior only, NOT a gate trigger"*
  (`coverage.py:54`). run20 shipped because FR-2 declared 5 intents and the floor
  was satisfied by **1** scenario. → **Gate at the coverage FLOOR (scenario-gen
  time, on the TE's real output), not at the validator (authoring time).** Zero
  author-facing blockers; acts closer to the bug.
- **Drop the §9 escape-hatch vocabulary.** Putting `subprocess`/`@untestable` in
  the `ac_id` column is a second divergent parse site for vocabulary that lives in
  the §8 AC table (`precheck.py:1556-1583`) — the two-sources-of-truth trap Linus
  already killed for `forbidden_patterns` (`anchors.py:38`). Vehicle declarations
  stay in §8.
- **FR-1 latent hole surfaced:** `[[tenant_isolation]]` has **no `covers` list**,
  so FR-1's RLS orphans are *also* uncovered-but-shipped today — but the correct
  remedy is the substrate-`covers` path, not a scenario. The design must route
  such orphans to the existing floor-remediation menu, not demand a scenario.

Accepted outright: keep Design X over Y (namespace decision, §6); keep the
structural §9 parser + `manifest.intents` (the floor AND the future
discrimination check both need typed intents); the new logic crosses no barrier
but touches a gate-report row → **declare audience** on the commit per
`docs/AUTHORIZATION-MODEL.md` (the `d2fecbf` class).

---

## 3. Revised design — floor-gated orphan-intent obligation

Gate at the **coverage floor**, not the validator. Keep the namespace minimal
(Design X: no `SI-N` id system). Two coordinated changes + a skill nudge.

### 3.1 Structural §9 parser + manifest enrichment (deterministic, cheap — KEEP)
Parse the §9 table (`| # | Intent | Layer | ac_id |`) into typed records:
```python
@dataclass(frozen=True)
class ScenarioIntent:
    parent: str        # "FR-2" (the ### sub-heading) — may be non-FR (HTTP verb / free-text / ungrouped)
    index: int         # 3 (the # column — positional within parent)
    text: str          # intent prose
    layer: str         # "security" | "error" | "contract" | "edge_cases" | "adversarial" | ...
    mapped_anchor: str | None  # "AC-3", or None when ac_id == "—" / absent column
```
Persist on `AnchorManifest` as additive field `intents: list[ScenarioIntent]`.
`from_json_path` reconstructs via `[ScenarioIntent(**d) for d in
data.get("intents", [])]` with per-entry try/except (matching the existing
fail-soft `return None` discipline). Forward-investment: the discrimination
follow-on reads layer/text/parent from here.

**Robustness requirements (from review):** `_INTENT_ROW_RE` only requires ≥3
columns, so a 3-column §9 table has no `ac_id` → treat "no 4th column" as
orphan/unknown, never crash. `parent` is not always `FR-N` (HTTP-verb, free-text,
`(ungrouped)` headings exist) — the layer logic must not assume an FR parent.
Unknown/missing layer → fail-soft (treated as non-blocking). Load-bearing tests:
column-count variants, non-FR parents, malformed rows, unknown layers.

### 3.2 Floor gating — the fork (THE open decision; see §6)
The floor reads `manifest.intents` and refuses to pass when a **security/error
(behavioral-rejection) orphan intent** is in the unmet coverage deficit. Two
variants differ only in **granularity** (machinery vs precision):

- **(A) Coarse count** — promote `weakly_covered` to a gate trigger for the
  security/error slice: fail when `per_anchor_counts[parent] <` the count of
  uncovered security/error orphans under that parent. No handle, no credit-regex
  or TE-prompt change. **Weakness:** demands "one more `[FR-2]` scenario," not "a
  whitespace scenario" — the TE can satisfy it with an off-target FR-2 test.
- **(B) Per-intent handle** — mint a deterministic handle `FR-2#3` (parent +
  index) per orphan security/error intent; the floor demands a scenario crediting
  that exact handle; the credit machinery learns the `FR-N#M` form and the TE
  prompt teaches tagging `[FR-2#3]`. **Pins the specific behavior.** Cost: a
  credit-regex addition + a TE-prompt line (the things X was praised for avoiding).

**Both variants** route a genuinely-unsatisfiable orphan (e.g. FR-1 RLS, covered
by substrate not a scenario) through the **existing** `render_floor_remediation` +
`_FLOOR_VEHICLE_MENU` (`coverage.py:799`) — the author declares substrate `covers`
/ `@untestable` / `subprocess` / re-scope **in §8**, reusing that machinery rather
than inventing a §9 sentinel.

### 3.3 `/df-write-spec` skill — light nudge (NOT a hard authoring mandate)
Add to the §9 step/checklist: a security/error intent that no anchor or declared
vehicle covers will be demanded as a scenario at the floor — prefer mapping it to
an AC or declaring its vehicle in §8 up front. This *informs*, it does not gate at
authoring time (the floor gates at gen time).

---

## 4. Scope boundary (state plainly; do not over-claim)

This closes the **addressability/visibility** gap: a behavioral-rejection intent
becomes a demanded coverage obligation. It does **NOT** guarantee the resulting
scenario **discriminates** the bug. Even variant B only forces a *whitespace-shaped*
test to exist; the ghost-token/lenient-assertion inertness is the deferred
**discriminating-power / mutation-oracle** check. So this change **would not, by
itself, have prevented run20** — its value is being the necessary precondition the
discrimination check attaches to, plus eliminating the strictly-worse zero-scenario
case. The honest delta between A and B is narrow: A forces one-more-FR-2-test, B
forces a whitespace-shaped test; neither forces a *killing* test.

---

## 5. Implementation steps (after a real Neo ruling on the §6 fork)

1. `ScenarioIntent` dataclass + structural §9 parser in `precheck.py` (extend
   `check_scenario_intents`, which already locates §9 + sub-groups). Tests:
   mapped / orphan / column-count variants / non-FR parents / malformed / unknown
   layer.
2. `AnchorManifest.intents` field + `from_json_path` reconstruction (additive,
   per-entry fail-soft). Tests in `test_anchors.py`.
3. Floor gating (chosen variant) in `coverage.py` + wiring at the post-scenario-gen
   gate; unsatisfiable orphans → `render_floor_remediation`. Audience-declared
   commit.
4. `/df-write-spec/SKILL.md` nudge.
5. Regression: the canonical spec-5 spec must NOT REVISE at authoring time (no
   validator block); at the floor, FR-2 #3 must be demanded; FR-1's RLS orphans
   must route to the remediation menu (author adds `covers`), not abort
   unfixably.
6. Full unit + shell suite green; `gitnexus_impact` on touched symbols;
   `gitnexus_detect_changes` pre-commit; Linus on the diff.

---

## 6. The open fork for Neo: floor-gating granularity (A vs B)

Validator-block (original 3.2) is **rejected**. Design Y (full `SI-N` namespace +
per-intent floor for *every* intent) is **rejected** (floor multiplication on heavy
specs; re-implements three AC-keyed escape hatches at the intent grain). The live
fork is **how precise the floor demand is for the security/error-orphan slice**:

- **(A) Coarse count** — least machinery (parser + floor only); demand is
  per-FR-count, so gameable / off-target.
- **(B) Per-intent handle `FR-N#M`** — pins the behavior; costs a credit-regex
  addition + a TE-prompt line (touches the TE tagging convention we have been
  stabilizing on heavy specs).

**Questions for a real Neo ruling:**
1. **A or B** — is per-intent precision worth the credit-regex + TE-prompt change,
   given that neither variant forces a *discriminating* test (so the precision gain
   is "a whitespace-shaped test exists" vs "one-more-FR-2-test exists")?
2. Is **layer (`security`/`error`)** the right selector for what the floor gates,
   or should it key off the **Non-Negotiable/Blocking** set — noting SI-3 has no
   AC, so it is not *in* §8a (circular)? What is the tested layer→severity
   allowlist + default for unknown layers?
3. Is routing unsatisfiable orphans through the **existing** `_FLOOR_VEHICLE_MENU`
   (author declares vehicle in §8) the right reuse — and does it adequately handle
   the **FR-1 RLS** case without a per-stack rule?
4. Is the **§4 scope-boundary** framing honest, or does it over/under-claim — and
   given §4, is promotion even the right *sprint* vs going straight at the
   discrimination check (treating addressability as a sub-task of that)?

---

## 7. Neo ruling (2nd pass, code-grounded) — RESOLVED → **B-reduced**

**Verdict: REVISE (narrowly), then proceed with variant B (reduced scope).**

### Fork resolved: B, not A — and A is non-viable, not merely coarser
Neo overturned the framing that A is "less machinery." Grounded reasons:
- **A's machinery savings are illusory.** `compute_coverage` keys
  `per_anchor_counts` by declared anchor IDs only (`coverage.py:306`); an orphan
  has none. A would need a *new bespoke per-parent deficit counter* that
  `weakly_covered` cannot express — AND still needs the §3.1 parser. B instead
  reuses the live `uncovered_anchors → render_floor_remediation` spine
  (`orchestrator.py:5148, 5479`).
- **A games trivially.** "FR-2 needs one more scenario" is satisfiable by the
  existing AC-1 expired-JWT test → deficit closes with **zero** new behavioral
  coverage. The real delta vs B is "off-target test that games the gate" (A) vs
  "on-target test that may be inert" (B) — wider than the plan claimed.
- **A cannot route FR-1 to the remediation menu** (no key to render) — see below.
- **A is sequencing-incompatible** with the discrimination oracle, which needs the
  `FR-N#M` handle to enumerate what to mutate-test. Addressability is genuinely a
  sub-task of discrimination; B produces the handle, A would be redone.

**Scope B to the orphan slice only:** mint `FR-N#M` ONLY for orphan
security/error/adversarial intents that land in the deficit. Mapped intents ride
their AC's existing obligation. This is NOT Design Y (every intent).

### REVISE item 1 — validator ADVISORY (not blocking) at authoring
Floor-gating is right, but gen-time-only means the author learns of the obligation
only after a mid-pipeline abort (`orchestrator.py:5492`), having already paid for
architect+substrate+TE. Add an **advisory** orphan-security-intent notice to the
existing `check_anchor_intent_coverage` advisory path (`precheck.py:845`) — routed
to advisory output, **never** to `passed`/`fatal`. Nearly free; closes the
learn-too-late hole without re-introducing the 7-blocker REVISE.

### REVISE item 2 (highest-leverage) — propagate `FR-N#M` across THREE regexes + prove FR-1 end-to-end
The mechanism will be **wired-but-inert** unless the handle is first-class in every
regex that encodes the anchor grammar (each silently drops a `#M` suffix today):
- `_CREDIT_ANCHOR_RE` (`coverage.py:357`) — used by `architect_credited_anchors`
  (substrate `covers` credit) and `check_substrate_deferral_consistency`
  (`:561`). Until extended, `covers = ["FR-1#1"]` fails the match and is silently
  not credited → **FR-1's RLS orphan can never be substrate-covered → unfixable
  abort.** This is the chicken-and-egg the plan hand-waved.
- `_ANCHOR_RE` (`:86`) / `_COVERS_TAG_RE` (`:81`) — scenario-credit path; must
  credit `@covers FR-2#3` without false-crediting bare `FR-2`.
- `_natural_key` (`:868`) + the `uncovered`/`actionable` render — must sort/display
  `FR-2#3` without crashing.

**Build the regression test FIRST** (before the gate): run the real floor path on
the canonical spec-5 and assert (a) FR-2#3 is demanded, (b) FR-1#1..#N route to the
substrate-`covers` remediation menu, (c) declaring `covers=["FR-1#1", ...]` on
`[[tenant_isolation]]` actually credits them and the spec does NOT abort. This is
the "verify the mechanism ACTIVATES, not just exists" discipline from MEMORY.md.

### Other rulings
- **Layer slice = `{security, error, adversarial}`** (add `adversarial` —
  Cross-Cutting #2 header-injection is exactly this class). Define as data
  (`_BLOCKING_ORPHAN_LAYERS = frozenset({...})`); **unknown/missing layer →
  NON-blocking** (fail-safe). Test per layer + unknown + missing.
- **§4 honesty:** strongest part of the doc; keep. Build now as the precondition —
  correct ordering, conditional on B.
- Minor: verify the exact FR-1 orphan set against the spec during impl (Neo
  wavered 4-vs-5; confirm from the table).

---

## 8. Validator gating reversal (Neo 3rd-pass; user-directed)

After the advisory version merged (dffb4e1), `dark-factory validate` on spec-5
returned **PASS 100/100 with all 7 orphans unresolved** — so the user's
"validate→/df-write-spec loop until validator passes" terminated immediately
with FR-2#3 (the mis-ship intent) still orphan. Per the user's rule ("if the
validator doesn't gate AND surface the feedback to fix the issue, change the
validator"), the orphan check must GATE.

Neo's 3rd-pass ruling (PARTIAL REVERSE): gate, but on **addressability**, not
coverage.

- **Predicate = addressability, not coverage.** At validate time there are no
  scenarios and no architect substrate manifest, so coverage cannot be judged. A
  coverage gate would FALSE-BLOCK substrate-vehicle orphans (FR-1 RLS), whose
  real vehicle — the architect's `covers` list — does not exist yet. The one fix
  an author can make from spec text is **fill `ac_id` (map the intent to an AC)**,
  and EVERY orphan can receive an AC, so an addressability gate false-blocks
  nothing. FR-1 RLS resolves by mapping to a (new) AC the architect then `covers`
  — same path as today, no new machinery.
- **Scope = ALL parents.** Gate every `{security,error,adversarial}` orphan,
  FR-parented or not. A non-FR (Cross-Cutting) orphan gets NO floor handle, so
  the validator is its ONLY gate — leaving it advisory made it the worst hole.
  The FR-parent restriction is a floor-handle limitation; it does not scope this
  gate (which only checks `ac_id` presence). Non-blocking layers
  (`contract`/`edge`) and unknown/missing layers stay non-gating (fail-safe).
- **REVISE, not fatal.** Feeds `passed=False` at `precheck.py` (the `passed`
  conjunction), never `fatal`.
- **Map-to-existing-AC cop-out** (mapping a behavioral orphan onto an AC that
  already carries another blocking intent re-dilutes coverage at AC grain) is
  **NOT policed at the validator** (semantic, non-deterministic). It is policed
  at the FLOOR via a "dilution handle" — mint `FR-N#M` not only for
  `mapped_anchor is None` but also for a blocking intent mapping to an AC that
  already carries ≥1 other mapped blocking intent. This is **P1 (deferred)** —
  ship the addressability gate first; add the dilution handle when a real spec
  demonstrates the cop-out shipping a bug. The skill nudge meanwhile says: prefer
  a dedicated AC per blocking behavior; sharing keeps a floor obligation.

Net: the validator now FORCES orphan resolution in the authoring loop (the
reported pain), the fix is drivable from validator feedback alone (map to an AC),
and nothing is false-blocked. The `FR-N#M` floor handle (§3-7) stays as the
gen-time precision cut for the deferred dilution case. Implemented on branch
`feat/orphan-intent-validator-gate`.

---

## 9. Place in the run20 defense architecture (2026-06-25, pm)

This work (addressability — every behavioral-rejection §9 intent maps to an AC) is
**H3** in the four-hole frame from the run20 post-mortem (see
`2026-06-25-discrimination-gate-design.md` §R1): a **precondition, sufficient for
no defense alone** — exactly the honest claim in §4 here. Its leverage is that it
*enables* the two independently-sufficient defenses:

- **H1 (kill ambiguity pre-lock):** the run20 validation trace graded
  `divergence=LOW` — the implementation-divergence sketch **missed** the
  trim-vs-reject fork (a *recall* failure, not a policy one). A plausible co-cause
  is that SI-3 was an **orphan** (no named AC for the sketch to diverge *against*),
  buried in §9 prose. Promoting it to a named AC-13 plausibly **lifts the sketch's
  recall** — but this is **MEASUREMENT-GATED**: see the divergence-recall ablation
  in the B-doc §R12, alongside the `temp=0` modal-collapse and soft-wording
  co-causes (three entangled, N=1). **No change to the validator / `temp=0` until
  that ablation is run and read.**
- **H4 (a discriminating test):** the addressability handle is what lets a
  discrimination check (the cheap positive-control requirement, or mutation)
  *target* the right behavior. See B-doc §R9 (positive-control) and §R13 (ranking).

Net: ship + validate this gate end-to-end (the paused spec-5 re-run), then let the
measurements in B-doc §R11 (Phase -1) decide the H1/H4 mechanisms — nothing
upstream of those measurements gets changed.
