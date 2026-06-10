# Sprint 7 — substrate-owner churn on substrate-less specs

> The first clean ship (shared-parse-amount) took ~33 min and 2/3 N=3 runs TIMED
> OUT at 40 min. The trace shows the substrate-owner ran **4× back-to-back
> (~9 min)** on a PURE-FUNCTION spec with no substrate. That churn is the main
> time sink and the timeout cause. Fix it → faster runs → likely 3/3.

## Root cause (file:line-verified)

1. **Unconditional invocation.** `run_substrate_prep` (substrate_prep.py:460) is
   called every pipeline iteration at `iteration.py:3064-3071` with NO gate —
   unlike the Sprint 6 TE-path gates (`orchestrator.py:2936`, `:3071`) which
   short-circuit on `substrate_manifest.is_file()`.
2. **4× = strict-parser re-prompt loop.** `substrate_prep.py:495`
   `for attempt in range(1, max_retries + 2)` with `DEFAULT_MAX_RETRIES = 3`
   (`:63`) → 4 attempts. The "0 commands → return" fast path (`:555-566`) is
   gated behind a CLEAN parse (`:534` checks `parse_errors` first). On a no-gap
   spec the agent emits an empty payload that trips the strict schema-v2 parser
   (`:222-305`: `schema_version==2`, `claim`, `considered`, `files_seeded`,
   `commands_emitted`, no v1 keys) → `parse_errors` non-empty → `continue` →
   re-prompt. Each attempt RE-READS the whole project (the expensive part:
   278s cold + 95/74/110s retries). max_retries is a param (`:469`) but the call
   site doesn't pass it → effectively hardcoded.
3. **Output use:** `substrate_result.commands` → gate `--pre-setup-cmd`
   (`iteration.py:3100`). On shared-parse-amount: 0 commands, gate setup handled
   deps itself, shipped. So substrate-prep was genuinely UNNECESSARY here.

## The tension (the key design question)

The substrate-owner's job (prompt, `substrate_prep.py:409-411`) is to "identify
any tool-prerequisite gaps that would cause the gate's Setup phase to fail or
hang" — **general environment/tool prep on EVERY spec**. This scope is BROADER
than the substrate manifest (the architect's DB/adapter `## Substrate Primitives`
modeling, written at `orchestrator.py:1672`).

So gating substrate-prep on `substrate_manifest.is_file()` (option A, the
Sprint-6 mirror) risks a **FALSE NEGATIVE**: a spec with a real non-DB
tool-prereq gap but no substrate TOML would have its prep silently skipped. The
bug we actually observed is the **4× churn on an empty result**, NOT that the
agent runs. Fixing the churn is safe; gating the invocation may under-trigger.

## Fix options

- **A — gate on `substrate_manifest.is_file()`** (mirror Sprint 6). Highest raw
  leverage (eliminates all 4 runs + ~9 min on substrate-less specs). RISK:
  substrate-owner scope ⊋ substrate-manifest scope → skips legit non-DB tool-prep
  → false negative. Only safe IF tool-prereq gaps NEVER occur without a substrate
  manifest (unverified; likely false in general).
- **C — make an empty/no-gap result converge in 1 attempt** (fix the churn, not
  the invocation). The agent legitimately runs on every spec; the bug is that
  "I found no gaps" costs 4 expensive project re-reads. Sub-options:
  - C1: when the emission has NO commands AND no v1 keys, accept it as
    zero-commands success even if an OPTIONAL field is missing — i.e. don't
    re-prompt 3× over a structurally-empty payload. Touches the parser →
    AUTHORIZATION-SENSITIVE (the `claim:"none"` non-claim contract; must NOT
    weaken the "zero commands ≠ healthy claim" invariant — only relax OPTIONAL
    field strictness on an empty payload).
  - C2: harden the FIRST-attempt prompt to emit the exact empty schema reliably
    (reduce first-pass parse misses). Prompt-only, low risk, but LLM-variance —
    may not fully converge.
  - C3: lower `DEFAULT_MAX_RETRIES` (caps churn at 2 not 4). Partial; weakens
    convergence on REAL substrate specs (the drizzle-journal case the module
    exists for). Low value alone.
- **A+C combo** — gate on the manifest for the common substrate-less case AND
  fix convergence for when it does run. Belt-and-suspenders; more change.

## Recommendation (for Neo to rule)
Lean **C1** (fix the churn safely) over A (avoids the false-negative), possibly
+ C2. But the leverage of A is real and the false-negative may be theoretical
for the current battery. **Neo to rule the seam.**

## Questions for Neo
- **Q1:** A (gate invocation) vs C (fix convergence)? Is the substrate-owner's
  "tool-prereq gaps on every spec" scope real enough that gating on the DB
  substrate manifest is an unsafe false-negative — or is "no substrate manifest
  ⇒ no prep needed" true in practice (making A safe + highest-leverage)?
- **Q2:** If C1, is relaxing OPTIONAL-field strictness on a STRUCTURALLY-EMPTY
  (zero-command, no-v1-key) payload authorization-safe — i.e. does it preserve
  the `claim:"none"` "zero commands is not a health claim" invariant
  (docs/AUTHORIZATION-MODEL.md + the module docstring)? Where EXACTLY is the line
  between "accept empty" and "weaken the contract"?
- **Q3:** Root of the churn — WHY does the agent miss the empty schema on attempt
  1? Is the schema too strict for "nothing to do" (5 required fields for an empty
  result), and is the right fix to make the EMPTY case a trivial one-field
  emission?
- **Q4:** Is A+C the right answer (gate the common case, harden the rare one), or
  is that over-building? Scope discipline.
- **Q5:** Verify: can a real spec produce substrate-prep commands WITHOUT a
  substrate-manifests/<spec>.toml? (If yes → A is unsafe alone. If no → A is
  safe + simplest.) This is the load-bearing empirical question.
- **Q6:** authorization — A just SKIPS an agent (removes strings from a restricted
  container → safe). C touches the parser (the gate) → needs the trigger-#2
  declaration. Confirm.
