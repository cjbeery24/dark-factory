# Sprint 8 — substrate-rule trigger false-positive (the recurring one, now at the gate)

> Issue #1 from the N=3 @90min battery. run3 was FUNCTIONALLY COMPLETE (44/44
> tests, 100% scenarios / satisfaction / 46 scenarios on all 3 iterations) yet
> ESCALATED — the quality gate's **Substrate Verification FAILed**:
> `substrate-manifest-missing: spec triggers substrate rules but architect
> produced no substrate manifest`. (Note: the escalation report's "type export
> contract" narrative was an LLM hallucination — the real gate failures are
> Substrate Verification + Scenario Lint, read from the gate report.)

## Root cause (trace-confirmed, file:line)

- The spec (pure function `parseAmount`) §denies substrate at line 269:
  "...no cross-cutting, **tenant-isolation**, security, or persistence invariant."
- `triggered_rules(spec_text)` returns `[tenant_isolation]` (verified live) — the
  rule's regex `tenant.*isolat` (postgres/substrate_rules.py) matches the
  substring with **no negation/context awareness**.
- `verify_substrate` (postgres/adapter.py:436-448): when `manifest_path` is
  absent AND `triggered_rules(spec_text)` fires → returns a
  `substrate-manifest-missing` error Finding → gate Substrate Verification FAIL →
  `exit=1` → never ships, despite 100% scenarios. The architect CORRECTLY
  produced no manifest (it's a pure function); the dumb regex overrides the smart
  architect.

## Why this is the highest-value issue
SAME false-positive root cause as Sprint 6 (TE contract-helper staging). Neo's
Sprint-6 round-1 analysis IDENTIFIED this regex FP and chose to fix the TE path
via a manifest-gate (A3) while DEFERRING the regex fix (A1) as "fragile." The FP
has now surfaced at a THIRD consumer (`verify_substrate`) where the manifest-gate
doesn't apply — `triggered_rules` IS the gate's signal. It's the
"wire-language FP on foot-gun docs" family
([[project_database_foundation_deferred_bugs]]). Fixing the trigger at the source
fixes all consumers (verify_substrate, TE staging activation, adapter activation)
and stops the recurrence. It blocked a *functionally perfect* impl — the single
highest-leverage reliability fix surfaced by the battery.

## The trigger's legitimate purpose (must preserve)
`triggered_rules` is a FOOT-GUN CATCHER: "the spec ASSERTS tenant isolation but
didn't declare the tenant-scoped tables in a `[[tenant_isolation]]` block inside
`## Substrate Primitives`." A real RLS spec that affirmatively requires isolation
but forgets the declaration SHOULD still fire. So the fix must kill the
DENIAL/mention false-positive WITHOUT killing the affirmative catch.

## Fix options
- **A — negation/context-aware matching.** Don't fire when the match sits in a
  negated/denial/scope-exclusion clause ("no … tenant-isolation", "not", "without",
  "out of scope", "excludes"). Neo called this fragile in Sprint 6. Risk:
  open-vocabulary negation; could re-introduce a miss.
- **B — affirmative-requirement matching.** Require the match co-occur with
  affirmative obligation language (MUST/enforce/require + isolation) rather than a
  bare substring. Tighter, still regex; risk: a real spec phrased unusually slips.
- **C — `## Substrate Primitives` section is authoritative.** The trigger's whole
  point is "asserted but not declared." If the spec HAS no `## Substrate
  Primitives` block AND the only matches are in prose/denials, treat as no-trigger.
  But a real RLS spec that FORGOT the block is exactly the case the trigger
  catches — so "no block ⇒ no trigger" guts the catcher. Unless: fire only when
  match is OUTSIDE a scope/exclusion/justification section.
- **D — architect-authoritative.** If the architect deliberately produced no
  substrate manifest (judged none needed despite the trigger), record that decision
  (an explicit "no-substrate" marker) so `verify_substrate` returns None (skip)
  instead of substrate-manifest-missing. Distinguishes "architect correctly
  skipped" from "architect buggily forgot." Bigger change (architect + gate), but
  trusts the SMART signal over the dumb regex — and generalizes beyond tenancy.

## Questions for Neo
- **Q1:** Which seam — fix the TRIGGER (A/B/C — stop the FP at `triggered_rules`,
  fixes all consumers) or the GATE/architect contract (D — trust the architect's
  no-manifest decision)? Given the FP now hits ≥3 consumers, is the durable fix at
  the trigger, or is D (architect-authoritative) the principled one?
- **Q2:** If trigger-side: negation-aware (A) vs affirmative-requirement (B) vs
  section-scoped (C)? You deferred A in Sprint 6 as fragile — does the
  now-recurring cost change that, and what's the least-fragile precise rule that
  (a) does NOT fire on the §269 denial, (b) STILL fires on an affirmative RLS spec
  that forgot the `## Substrate Primitives` block?
- **Q3:** False-NEGATIVE risk: the canonical RLS spec (database-foundation) MUST
  still trigger + require its manifest. How do we verify the affirmative case keeps
  firing? (database-foundation has a real `## Substrate Primitives` block + a TOML.)
- **Q4:** Is D worth the extra surface, or over-building for a regex-precision
  problem? Scope discipline.
- **Q5:** Authorization: `triggered_rules` / verify_substrate feed the gate report
  (Reviewer/Oracle-visible) — is tightening the trigger authorization-sensitive
  (docs/AUTHORIZATION-MODEL.md)? It rejects FEWER (removes a false gate failure) —
  confirm direction.
- **Q6:** The §269 text is in a scope/exclusion justification ("Non-Negotiable
  Acceptance Criteria" / what the spec is NOT). Is "don't match substrate triggers
  inside spec scope-exclusion prose" a clean general rule, or too spec-format-specific?

## ITERATION LOG — the trigger-regex approach is a swamp (3 demonstrated failure modes)

Implemented + gated; both reviewers BLOCKING; each lexical variant trades one
error class for another:
1. **Bare `tenant.*isolat`** (original) → FP on denials ("...no...tenant-isolation
   ...invariant"). The bug.
2. **Affirmative co-occurrence** (verb near "tenant"; Neo's option B, corrected to
   exclude `isolat`/`scope`) → still FP on NEGATED requirements: Codex +
   Linus caught "requires no tenant", "does not require tenant isolation", "no
   tenant filter required" — and Linus found a REAL-corpus instance,
   echo-endpoint:28 "requires no tenant context". Negation-blind in a different
   spot.
3. **`multi[- ]tenant` only** (OPT1) → kills all found FPs (denials don't say
   "multi-tenant") BUT false-NEGATIVES the affirmative foot-gun catch: the
   pinned `test_tenant_isolation_pattern_activates_adapter` ("enforces tenant
   isolation via RLS" → activate postgres) now fails. That catch is the trigger's
   whole purpose (spec needs RLS but forgot to declare primitives).

Lexical negation detection is positionally fragile (negator before/between/after
the verb; "must not read" is an AFFIRMATIVE idiom; "never sees" is affirmative
isolation) — every guard has a counter-example. This is the "let the data force
the move" signal: the trigger may not be the right SOLE seam.

**Re-consulting Neo (round 2)** on the seam: (a) accept OPT1's weakened catch +
update the test, (b) a sentence-scoped negation guard in code (own edge cases),
or (c) option D — architect-authoritative: the LLM architect read the whole spec
and produced no manifest; trust that at `verify_substrate` instead of overriding
it with a dumb regex. D was rejected in round 1 as over-build, but the recurring
intractability is new evidence. Working tree holds OPT1 (staged, NOT committed,
1 test red) pending the ruling.

## Neo round-2 ruling — option (c) architect-authoritative, GATE-SCOPED (0.93) — IMPLEMENTED

The 3 lexical variants are ONE category error: a prose regex reproducing a
judgment the LLM architect already made. Stop fighting the regex. This FINISHES
the Sprint-6 A3 migration (TE-staging already made manifest-authoritative; the
gate was the consumer left behind).

- **Seam = the GATE only** (`verify_substrate` is the sole ship-blocking
  consumer; activation/cache keep the cheap regex — a false trigger there just
  stages unused tooling, never blocks a ship). **Don't touch the trigger** →
  REVERTED the regex/test churn.
- **Seam LOCATION (Neo's own diff was unimplementable):** the verifier runs in a
  container with NO project mount, so it can't see the YAML manifest. The
  architect-authority decision is a HOST-SIDE fact → implemented in
  `substrate_phase.py::run_substrate_verification` (has `project_dir`), before the
  per-adapter loop: `substrate TOML absent AND <spec>-manifest.yaml present →
  SKIP` (architect ran + modeled no substrate). YAML ALSO absent → fall through →
  verify_substrate's regex branch flags the real missing-architecture failure
  (distinguishability preserved).
- **Adversarial residual:** spec needs RLS, has NO SQL tokens (pure prose), AND
  the architect erroneously omits substrate — the SAME residual Sprint 6 A3
  already accepted + shipped. (Correction per Linus: it is NOT additionally
  bounded by rls_policy/custom_guc at THIS seam — at the no-manifest gate path
  verify_substrate runs only the `triggered_rules` regex; the SQL-token rules
  fire only on a PARSED manifest. So the short-circuit bypasses exactly the FP
  regex and nothing of value. The residual was never bounded at this seam — this
  change adds no new risk on that axis.)
- **The RLS-forgot-the-`## Substrate Primitives`-block foot-gun** moves to the
  spec-validator addendum (pre-architect, whole-spec view) as a SEPARATE
  follow-up — not a post-hoc prose regex at the gate.
- Why not whack-a-mole: this CHANGES THE LAYER (structural file-presence signal,
  no prose surface to attack); the only residual is an LLM-judgment error, a
  different already-accepted risk class.
- Authorization: removes a gate-failure path (fewer rejections — safe direction);
  host-side dispatch + a comment; no Reviewer→Implementer promotion.

Implemented: `substrate_phase.py` ~13-line short-circuit; 2 tests
(`test_architect_authority_skips_when_yaml_present_no_substrate_toml`,
`test_no_yaml_manifest_falls_through_to_verifier`). Regex + its tests reverted
to HEAD. Full unit 3928; integration 406/71 = baseline; ruff clean.

## Out of scope (this issue)
run3 ALSO failed Scenario Lint ("shape issue, Implementer-masked") — a SEPARATE
issue (#next). Fixing the substrate-trigger FP alone won't clean-ship run3; it
clears ONE of the two gate failures. One issue at a time per the workflow; re-run
after this fix validates the substrate-verification clears, then tackle the lint.
