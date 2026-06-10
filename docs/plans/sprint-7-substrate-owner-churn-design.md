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

## Neo ruling — REWORK (the predicate is the crux) + my follow-up verification

Neo REWORKed: gating on bare `substrate_manifest.is_file()` (the Sprint 6
Postgres-only predicate) would false-negative the substrate-owner's FOUNDING
case (drizzle `meta/_journal.json`). Don't ship C (the parser has no optional
field to relax — all 5 fields hard-required; C is mis-aimed and invites a
contract-weakening review for no benefit). Ship A, but with the CORRECT predicate
(not bare manifest): "manifest exists OR an active adapter detects substrate-prep
tooling in the project."

**My follow-up verification (refines Neo's premise — partly contradicts it):**
- The substrate-owner's gap detection keys on (agents/substrate-owner.md):
  the architect's `commands.setup` referencing a tool (:42), the spec's §4a
  `required_lifecycle_sequences` (:40), and lockfile evidence (:38) — NOT the
  substrate manifest TOML.
- The FOUNDING drizzle case IS `database-foundation` (substrate_prep.py:5-12),
  which DOES have a substrate-manifests TOML. So Neo's "drizzle case → no TOML"
  is empirically wrong for the founding case — a drizzle-migration spec creates
  tables → has `## Substrate Primitives` → architect writes the TOML.
- BUT Neo's underlying worry survives: a spec COULD have a `commands.setup`
  migration tool WITHOUT a `## Substrate Primitives` TOML (gap-detection inputs
  ≠ the TOML). So bare-manifest gating is still theoretically unsafe.
- Monorepo coarseness: "migration tooling in project" (Neo's predicate b) is
  PROJECT-level, not per-spec. budget-brain-v2 currently has NO drizzle in any
  package.json, so for THIS battery both predicates skip shared-parse-amount
  correctly. But a project WITH drizzle installed would make (b) true for ALL its
  specs (incl. pure functions) → re-churn. So (b) is coarse.

**The real per-spec signal** is likely "the architect's manifest `commands.setup`
references a tool that has a known prep-gap class" — per-spec, not project-level,
not TOML-only. That needs a deterministic check (parse the architect manifest
yaml's commands.setup). More than a one-line gate.

## GROUND TRUTH (from the captured trace) — BOTH prior hypotheses INVALIDATED

> Process miss owned: I designed (and ran TWO Neo passes) BEFORE pulling the
> actual substrate-prep rejection trace. The exact "measure before designing"
> lesson from Sprint 3. The trace (run-3 archive iter-1/architecture/substrate-
> prep/<spec>.json + final/events.jsonl) shows the real cause:

- **substrate-prep is genuinely NEEDED for shared-parse-amount** — NOT empty
  churn. The substrate-prep report says: "addresses infrastructure from the
  database foundation spec required by the GLOBAL environment.toml setup command,
  even though the current spec doesn't use database features. The Setup phase runs
  project-level commands regardless of individual spec requirements." So the
  global `environment.toml` setup runs `drizzle-kit migrate` for EVERY spec →
  even the pure function needs the drizzle `_journal.json` + migration dirs seeded
  or the gate's Setup hangs. **⇒ GATING THE INVOCATION (Option A / P1-P5) IS WRONG**
  — it would reintroduce the Setup hang. Invalidated.
- **The churn is ONE rejected command, not the empty-stage_exit contradiction.**
  `SUBSTRATE_PREP_VALIDATOR_REJECT` fires on `step: create_drizzle_config` — the
  agent emits a command that WRITES `apps/api/drizzle.config.ts` (configuration
  authoring), which the validator rejects because agent.md:46 forbids the
  substrate-owner from authoring config/schema/source. The agent RE-EMITS the
  rejected config command across all 4 attempts (`unresolved_rejections: 1`)
  instead of dropping it and converging on its 2 VALID seeding commands (mkdir
  migration dirs, seed `_journal.json`). **⇒ Neo round-1's stage_exit hypothesis
  is NOT the cause here** (the final payload had no stage_exit; the rejection is
  the config-authoring command). The convergence failure is: retry feedback
  doesn't get the agent to DROP a rejected out-of-scope command.

**The REAL fix space (re-design needed, on this data):**
- (R1) Make the retry loop CONVERGE: when a command is rejected, the feedback must
  make the agent DROP it and re-emit only the surviving valid commands — not
  re-justify the rejected one. (The 2 valid commands already seed what Setup
  needs; the run shipped anyway via best-effort forwarding.)
- (R2) Reinforce in the prompt/agent-def that seeding a CONFIG file
  (drizzle.config.ts) is out of scope (agent.md:46 says so, but the LLM does it —
  prompt may need an explicit "do NOT emit commands that write *.config.* /
  schema / source" guard, matching the validator's actual reject rule).
- (R3) Deeper (budget-brain-v2 config, not framework): the global environment.toml
  setup runs `drizzle-kit migrate` for ALL specs incl. non-DB ones. Arguably the
  Setup shouldn't force DB migration for a spec that doesn't touch the DB — but
  that's a target-project config question, and changing it edits the test oracle.

## STATUS: PAUSED for a careful predicate (post-first-clean-ship checkpoint)
The first CLEAN SHIP (the headline) is done. Sprint 7 is a RATE optimization
(1/3 → hopefully 3/3) whose gate predicate is subtle and regression-risky (wrong
predicate breaks the drizzle founding case). Rather than rush it at the end of a
long session, PAUSE here. NEXT (on resume): re-engage Neo with the verification
above to settle the predicate (bare-manifest vs commands.setup-tool-reference vs
manifest-OR-tooling), then implement → Linus+Codex → re-run N=3 for the rate.
Interim cheap win available if desired: raise run_timeout_s so the 2 slow runs
finish (measures the true rate without touching the churn).

## Questions for Neo (round 2, on resume — armed with the verification above)
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
