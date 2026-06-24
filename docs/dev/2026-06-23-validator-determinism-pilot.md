# Validator non-determinism — confirming pilot (N=14) findings + the fix

**Pilot:** 14 serial, blind, isolated `validate` runs on spec-5 (auth-schemas-middleware),
10 adapter-off + 4 adapter-on, sonnet-4-6, full streams preserved
(`~/sites/.validator-confirm/`). Behavioral metrics only (intrinsic, not
latency — so cache/budget confounds don't apply). Reviewed: Neo + Codex (design + analysis).

## Headline (mechanism resolved verbatim; rates pilot-grade with wide CIs)
| arm | n | REVISE | org_id **detected** in thinking | org_id **elevated** to blocker |
|-----|---|--------|---------------------------------|--------------------------------|
| OFF | 10 | 5/10 [0.24–0.76] | **8/10 [0.49–0.94]** | **3/10 [0.11–0.60]** |
| ON | 4 | 0/4 [0–0.49] | 1/4 [0.05–0.70] | 0/4 [0–0.49] |

1. **OFF arm = ELEVATION variance (the dominant non-determinism).** The validator *detects*
   org_id 8/10 but *elevates* it 3/10 — it reliably SEES the defect and coin-flips on whether
   to block. Verbatim-confirmed: run-05/06/08/09 all show the **"argue both sides of severity
   then land somewhere"** oscillation; run-09 argued *toward* demoting then blocked, run-08
   argued the same way then passed. The deliberation is symmetric; only the landing differs.
   run-06 (PASS) oscillated T5-fires → "ambiguity not contradiction" → MEDIUM → LOW → warning →
   PASS in one run. It is NOT detection variance and NOT sketch-construction variance.
2. **ON arm = DETECTION suppression.** Adapter-on detected org_id 1/4 vs OFF 8/10 (run-06 the
   lone exception; run-02/10/14 never engaged it). Consistent with the postgres addendum
   displacing the core Precision Analysis. (N=4 — directionally strong, not tight; CIs overlap.)
3. **Cross-spec F-1c contradiction:** read by 2/14, elevated by 1/14 — doubly-gated and rare.
4. The validator non-deterministically elevates ONE of several genuinely-real defects
   (F-1c-migration, org_id, AC-7, and run-13's distinct MEDIUM blocker).

## Why prompting can't fix it (the load-bearing point)
The validator **derives the defect correctly and then coin-flips on elevation** — it argues
*both* severity sides within one run. A prompt rubric ("HIGH unreachability → critical, do not
downgrade") fails the same way the testability-flag rule did: the model rationalizes around it
("ambiguity rather than contradiction," "not wire-level observable" — verbatim from run-06). This
is the task-#30 lesson at the elevation stage: **you cannot rubric-away a model that argues both
ways.** The fix must remove the elevation decision from the LLM's discretion.

## The fix (deterministic gate, NOT prompt-tuning, NOT ensemble)
Take elevation out of the LLM's hands for the structurally-detectable class:
- **org_id class (intra-spec unreachability):** a host-side deterministic check over the claim
  ledger the validator already emits — "an FR Processing step extracts an input that no later
  step or schema column consumes, AND an EC/AC asserts an error outcome that would require
  consuming it" → force a blocker. Rides existing data; deterministic → no coin-flip.
- **F-1c class (cross-spec contradiction):** the cross-spec design's Stage 1 — promote prose/AC
  MUST-NOTs to the architect manifest's `forbidden_patterns` (deterministic impl gate).
  See `docs/dev/2026-06-23-cross-spec-consistency-design.md`.
- **NOT** an LLM cross-spec/ensemble pass (re-imports the flip-flop, per the cross-spec review).

## IMPLEMENTED (2026-06-23) — Candidate B increment 1, Neo-designed + Linus-gated
Neo's design verdict (full brief in-session) ruled **Candidate A (a per-defect org_id
unreachability detector) REJECTED**: the ledger carries claim tuples but NOT the §5 schema /
FK-reachability graph, so a deterministic unreachability proof needs a NEW per-stack extraction
pass = the bespoke trap. The org_id *class* (detect-but-coin-flip elevation) routes to the
**L2 finding-level validator ensemble (task #30)** as its general, non-bespoke cure — NOT a
point-fix. **Candidate B was built** as the deterministic-gate increment:

- **`darkfactory/pipeline/ledger_forbidden_patterns.py`** — derives `forbidden_patterns` from the
  validator's OWN `predicate=forbids` ledger claims (the validator extracts MUST-NOTs reliably;
  this rides that structured extraction, no new parsing). A `forbids` claim naming a **code symbol**
  (empty `()` / ellipsis `(...)` call form — Linus C1: real-arg calls like `cache(key)`/`fetch(url)`
  are illustrative prose, rejected) becomes `re:\b<ident>\s*\(`. Verified against REAL preserved
  ledger bytes: F-1c → `{FR-1: [re:\bmigrate\s*\(]}`; spec-5 (all behavioral forbids) → `{}`.
- Merged (union, never replaces) into the existing `forbidden_patterns.py` pre-gate at
  `iteration.py` `_run_deterministic_preflight`, behind **`forbidden_patterns_from_ledger` (default
  OFF)**. 18 unit tests + full suite (4602) green.
- **Linus verdict: SHIP-WITH-FIXES (no NACK)** — C1 (narrow feedstock to empty/ellipsis call form +
  prefer `object` over `raw`), docstring precision, alias-safe deep-copy: all applied.
- **GATE BEFORE FLIPPING DEFAULT ON:** validate on the spec corpus first (the empty/ellipsis rule
  has a known small residual — an empty-arg call-token in incidental prose, e.g. `init()`, still
  emits). Follow-ups: cross-spec variant (a dependency's forbids → the dependent's gate, for the
  F-1c contradiction's actual impl-harm) needs depends-on→ledger resolution; forbidden_patterns
  fail-open→closed hardening deferred (semantics tradeoff, Linus's call).

## CORPUS VALIDATION (the pre-flip gate) — PASSED, zero false positives
Ran the extractor across **all 119 real consistency-ledgers** on disk (bb specs + archives +
the 14 preserved pilot runs: database-foundation, auth-schemas-middleware, api-bootstrap,
notes-crud[-pg], parse-amount-py, T1-real-fr3, …). Result:
- **Exactly ONE unique pattern emitted across the entire corpus: `re:\bmigrate\s*\(`** on
  database-foundation FR-1 (the genuine code-symbol MUST-NOT). 31 ledger files emitted it (all
  database-foundation runs); every other spec's `forbids` claims are behavioral/prose and
  correctly emitted **nothing**. The empty/ellipsis + object-first discriminators held perfectly
  on real data — **no over-broad patterns, no prose-incidental emissions, no false positives.**
- ⇒ The "validate on the corpus before flipping the default" gate is **satisfied** on available
  data. Safe to flip `forbidden_patterns_from_ledger` ON (the empty-arg-in-prose residual did not
  materialize on any real ledger). Recommend flipping after a user OK (it's a global default).
- **Pre-existing bug surfaced (NOT mine, worth a backlog note):** the parse-amount-py ledgers are
  **unparseable YAML** — the validator emits invalid double-quoted scalars for regex-bearing claim
  objects (`object: "match ^-?\d+(\.\d+)?$"` → `unknown escape character 'd'`). `parse_consistency_
  ledger` returns None → the gate degrades to `{}` (graceful, no false negative on our side), but
  it means the ledger gate is **blind to forbids claims in any spec whose ledger carries a regex
  object** (e.g. parse-amount's FR-3 "no rounding" — the original `Math.round` motivator). The
  validator should emit `'single-quoted'` or block scalars for regex objects; tracked as a
  framework bug independent of this change.

## Design-sensitivity caveat (per the no-bespoke discipline)
The intra-spec unreachability detector is the design-sensitive piece — "extracted-but-unconsumed
input feeding an error EC" is partly semantic. It MUST be scoped to ride the existing claim
ledger (not parse raw spec prose ad hoc) and Neo+Linus-reviewed before coding, or it becomes the
over-fit-to-org_id bespoke trap. Implement as a narrow, unit-tested precheck; do not generalize
past the ledger-structural pattern.

## Raw data
All 14 runs preserved (streams, reports, prompts, ledgers) under
`~/sites/.validator-confirm/` — delete nothing; available for deeper analysis.
The pilot resolves the MECHANISM; a ±0.15 rate measurement needs the ~40–80/arm run.
