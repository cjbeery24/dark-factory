# Design: Cross-Spec Consistency — prose→structured→impl-gate (corrected after dual review)

**Status:** DESIGN — not implemented. Neo (design) ✓ + Codex ✓ reviewed; Linus gates the CODE.
**Predecessor:** `docs/dev/2026-06-23-cross-spec-consistency-gap.md` (analysis, dual-reviewed).
**Goal:** lights-out — the spec set is a consistent contract enforced with no human in the loop.

## What the review killed (and why this version differs)
The prior draft proposed (Stage 2) an auto-populated invariant registry that extracts MUST-NOTs
from each spec's **structured §4a block**, plus a deterministic cross-spec gate. Dual review,
code-verified, found this fatally mis-founded:
- **Empirically empty source.** The motivating F-1c MUST-NOT ("startup MUST NOT invoke
  `migrate(...)`") lives in **prose** (FR-1:36, :124), the AC table (AC-9), and ECs — NOT in
  F-1c's structured `forbidden_commands` (which lists only `drizzle-kit push`/`db:push`). spec-5
  has **no §4a block**. **0 of 24 bb specs** have an organically-authored structured forbidden
  block. So registry-extraction-from-§4a reads nothing and misses the very contradiction it targets.
- **Unbuildable deterministic core.** Pairing "MUST NOT `migrate()`" with "run via
  `drizzle-orm/migrator` on startup" requires the semantic judgment (different surface tokens;
  shared concept = migration-on-startup). The "narrow aliasing LLM" is the holistic contradiction
  question in a smaller hat → over/under-generates → reintroduces the validator flip-flop.
- **No dependency loading.** `plan/depends_on.py` treats feature-IDs as unresolvable in standalone
  runs (CLI warns + ignores). Cross-spec content loading doesn't exist.

Conclusion: **the fix is not a cross-spec LLM gate or a §4a-sourced registry. It is closing the
prose→structured→deterministic-impl-gate path.** The harm is concrete and local: the implementer
writing the forbidden construct into source. A deterministic `forbidden_patterns` rule catches
exactly that, at the gate, against the impl — no LLM, no flip-flop, no touching code-blindness.

## Stage 0 — now, zero framework change (the actual N=1 defect)
- Reconcile bb's F-1c/spec-5 contradiction via `/df-write-spec`: remove/rework spec-5 FR-1's
  "migration on startup" to honor F-1c CLI-only (the `db:migrate`-in-deployment-sequence already
  satisfies "DB ready before first request", per the validator's own suggested resolution).
- **N>1 audit, calibrated on the KNOWN pair first:** the contradiction is `migrate(` vs
  `drizzle-orm/migrator` + "on startup" vs "MUST NOT on startup" — a naive opposite-polarity grep
  MISSES it (different tokens). If the audit can't re-find the one contradiction we already have,
  it can't validate N>1. Build the audit to catch THIS shape, then scan the bb set.

## Stage 1 — THE PRODUCT: prose MUST-NOT → architect forbidden_patterns → deterministic gate
The deliverable is the missing wiring, in three deterministic pieces:
1. **`/df-write-spec` promotes prose/AC MUST-NOTs.** Detect clauses that name a symbol/command
   and assert prohibition ("MUST NOT invoke `migrate(...)`", "MUST NOT import X", "never on
   startup") in FR prose / AC / EC tables — *where they actually live*, not the empty §4a block —
   and surface them for promotion into the spec's structured negative-space block.
2. **Wire spec MUST-NOT → architect-manifest `forbidden_patterns`.** This propagation does NOT
   exist today (verified: nothing in `darkfactory/architecture/*.py` carries spec forbidden rules
   into the architect manifest's `forbidden_patterns`, the surface scanned against implementer
   source diffs by `pipeline/forbidden_patterns.py`). Specify it: a promoted MUST-NOT on a
   symbol/import/call becomes an architect `forbidden_patterns` entry (e.g.
   `re:from ['"]drizzle-orm/.*migrator`). The EXISTING gate then deterministically blocks any
   implementer diff that violates it. (NB: spec §4a `forbidden_commands` is the WRONG surface —
   it's scanned against *runtime commands*, not source; a `migrate()` import in `index.ts` would
   slip past it.)
3. **Authoring-time cross-spec surfacing.** When authoring spec X, load X's `depends-on` specs'
   **prose+AC MUST-NOTs** (not §4a) and flag any X clause that names a prohibited symbol — surface
   to the author before validation (the genuinely novel, cheap cross-spec win).
- **Harden the gate's fail-open:** `forbidden_patterns` currently fails open on missing/malformed
  manifest + git/check exceptions. A promoted hard MUST-NOT should fail *closed* (or at least warn
  loudly) so a manifest glitch doesn't silently drop the constraint.

Stage 1 is deterministic end-to-end, reuses the existing impl gate, never invokes an LLM
cross-spec walk, and catches the motivating defect at the only place it does harm (the impl).

## Stage 2 — CUT as founded; re-found only on evidence
Do NOT build the registry + cross-spec LLM gate. Stage 1 already closes the harm for any
contradiction whose resolution reduces to "forbidden behavior must not appear in impl." **Only if
the Stage 0 audit finds a SECOND contradiction whose resolution is NOT expressible as a forbidden
impl-pattern** is Stage 2 justified — and then it must extract from **prose+ACs** (where MUST-NOTs
live), not §4a, with that specific contradiction shape as its spec.

## Corrections logged (claims the review falsified)
- `/df-write-spec` VALIDATION-FAILED mode *consumes* feedback but applies it via a focused
  interview (LLM/operator behavior), NOT a deterministic auto-apply. "Consumed automatically" is
  only half-true; application is not guaranteed deterministic.
- `forbidden_patterns` lives at `darkfactory/pipeline/forbidden_patterns.py` (not `lint/`), runs
  pre-gate from `iteration.py`, and **fails open** — deterministic only when configured + healthy.
- Two distinct forbidden surfaces (spec `forbidden_commands` vs architect `forbidden_patterns`)
  were conflated; only the latter catches implementer source.

## Gates
Neo + Codex (design) ✓. Linus (code) before implementing Stage 1's promotion + propagation wiring.
Stage 0 is no-code. Stage 2 is cut pending N>1 evidence of a non-impl-pattern contradiction.
