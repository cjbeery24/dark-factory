# Sprint 3b — Validator DETECTION determinism

> Follows the Sprint 3 measurement (N=4): the validator detects the real §5/§10
> error-message contradiction only **2/4 runs**. Verdict tracks detection
> exactly; status tracks the ledger (no transcription gap). So deriving the gate
> from the ledger is a no-op — **the fix must make DETECTION reliable.**

## Root cause (from the captured ledgers)

The consistency procedure (`agents/spec-validator.md:101-409`) is an elaborate
prompt-based 3-pass markdown "commitment device" (extract claims → pairwise walk
→ YAML ledger). It still flips because **extracting and comparing the SPECIFIC
conflicting claims is left to LLM judgment**:
- Pass-1 extraction is FR-centric (steps 1-5 read FR Processing/Side-effects/
  Output + ACs + ECs). The §5/§10 failure is a CROSS-SECTION literal-string
  conflict: `TOO_MANY_DECIMALS` → `"Amount must have at most 2 decimal places"`
  (§5 normative table, line 140) vs `"At most 2 decimal places are allowed"`
  (§10 example, line 351).
- Run 4's ledger never even extracted those two strings (0 mentions) → couldn't
  compare → missed. Runs 2/3 extracted both → caught (typed T1 once, T4 once).
- So the variance is in whether the model extracts+compares the right literal
  pairs — inherently non-deterministic when left to "extract load-bearing claims."

## The fork

**Option A — mechanical consistency lint (deterministic by construction, PROPOSED).**
Add a code-based check (like the M1 command-scoping gate
`manifest.py:validate_command_scoping` — a deterministic gate that runs ALONGSIDE
the LLM) for the mechanically-detectable, high-value contradiction classes.
Start with the observed one: **same error code mapped to ≥2 distinct message
strings across the spec.** Parse the spec, collect every `CODE → "message"`
mapping, flag any code with >1 distinct message. 100% deterministic, 0 LLM
variance, catches the §5/§10 class N/N. The LLM consistency check stays for the
semantic/fuzzy classes (T2 lifecycle ordering, etc.).
- **Feasibility risk:** parsing `code → message` out of a §5 markdown table +
  §10 free-form examples is itself fuzzy unless the spec exposes the mapping
  parseably. May need the spec TEMPLATE to carry a structured error-contract
  block (one source of truth the examples must echo), OR a tolerant extractor
  (regex for `CODE` near a quoted string) that errs toward flagging.

**Option B — structured/unskippable LLM extraction (Neo's Session 1.5).**
Force cross-section literal-claim extraction via a required mechanical step
("build a table of every error-code→message pair from §5 AND §10; flag
mismatches") and/or a schema-constrained sub-call so the model can't skip it.
Tighter than today, but still LLM detection → residual variance.

**Option C — hybrid.** A (deterministic) for mechanically-detectable classes
that the spec exposes structurally; B (tightened LLM) for the rest.

**Proposal: A**, scoped to the error-code→message class first. It literally makes
detection deterministic for the observed failure, fits the existing
deterministic-gate pattern (M1), and matches the reset's mechanical-validators
theme. B alone can't reach N/N determinism (it's still LLM).

## Measurement / verification
- Fast loop: the existing reproducibility harness (`tests/reproducibility/`,
  isolated consistency surface) — caveat: it measures the surface in isolation,
  not full-pipeline.
- Ground truth: full-pipeline N≥5 captures (the Sprint 3 N=4 harness in
  `/tmp/sprint3-capture` is the baseline at 2/4). Exit criterion: the targeted
  contradiction class is detected **N/N** (deterministic).

## Questions for Neo (RETRY — the tool crashed on the Sprint-3 derivation design)
- **Q1:** A vs B vs C? Is a deterministic code-based consistency lint the right
  move, or does it duplicate/undermine the LLM consistency check?
- **Q2:** Feasibility of mechanical `code→message` extraction from current spec
  format — does Option A REQUIRE a spec-template change (structured error
  contract), or is a tolerant regex extractor sound enough? If template change:
  is that in scope or a separate spec-gate sprint?
- **Q3:** Where does the lint live — inside the validator agent (prompt), or as
  a pre/post-validator CODE gate in the orchestrator (like M1)? The latter is
  deterministic and LLM-independent; the former isn't.
- **Q4:** Scope — start with ONLY the error-code→message class (the observed
  failure), or enumerate the mechanically-detectable classes up front? Avoid
  over-building.
- **Q5:** False-positive risk of a mechanical lint (e.g., intentionally different
  messages for the same code in different contexts) — acceptable fail-closed, or
  does it need an escape hatch?
- **Q6:** Does making ONE class deterministic while the rest stay LLM-flaky
  actually move the reliability needle, or is it a drop in the bucket? (The N=4
  data says error-message contradictions are what's actually blocking real specs,
  so it may be high-value despite being narrow.)
