# Sprint 16 — Deterministic Security Authority (Reviewer→Oracle FP fix)

## Problem (from a real escalated run, not theorized)
`notes-crud-ts` (TS Tier-B) is 100% functionally correct — gate Result PASS,
44/44 blind scenarios pass every iteration — yet ESCALATED (3 iters, no ship).
Trace root cause (run_id `10291d57`):

The LLM **Reviewer** returned `REQUEST CHANGES` (74/100) citing a **"Critical
prototype pollution vulnerability"** at `apps/api/src/common/health.ts:15` —
`(module.exports as ...).__healthChecks = healthChecks;`, a benign **static-key**
assignment in **pre-existing, already-shipped scaffolding** (api-bootstrap
`645dec1`), outside the current spec's scope. The **Oracle** then auto-NO-SHIP'd
on `oracle.md`'s "Any critical security issue identified" trigger (+ score < 80).

Two compounding facts:
- The reviewer was **instructed** (iteration.py:4252-4257) to use the
  deterministic pre-pass Security score (which found **0 HIGH/MEDIUM → 15/15**)
  but **disobeyed**, overriding Security to 5/15 with the fabricated finding.
- The classifier-missing NO-SHIP was RULED OUT as a hard blocker (defects.json is
  always absent on a 100%-pass gate by design; `oracle.md:288` conditions that
  rule on "any failing scenarios"; python notes-crud clean-shipped with no
  defects.json). The reviewer FP is the sole ship-blocker.

## Design (Neo APPROVE — ruling in session trace)
Make the **deterministic security pre-pass the sole authority for security
ship-gating**, on both channels the Oracle consumes — score AND prose. The LLM
reviewer's free-prose security claims become advisory. This is the Sprint-8 move
(make ship-gating deterministic/authoritative, don't rely on LLM prompt
compliance) applied to security, and it is purely **de-escalating**
(BLOCKING→ADVISORY) — the safe direction per `docs/AUTHORIZATION-MODEL.md`.

Implemented host-side (no `review.md` rewrite → no `reviews/`-mask barrier
concern; `reviews/` is Implementer-readable and unchanged by this fix):

1. **Thread the verdict** — `IterationState.security_high_count` (-1 = unknown),
   set in `_step_review` from `prepass_result.security.findings` (count of HIGH).
2. **Authoritative Oracle-prompt block** — `_step_oracle` injects
   `## Deterministic Security Verdict (AUTHORITATIVE)`:
   - `== 0` → SECURITY-CLEAN; MUST NOT NO-SHIP on security; review-prose security
     language is advisory; disregard Security-dimension score deductions.
   - `> 0`  → N HIGH findings ARE valid ship-blockers.
   - `== -1` → block omitted (fail-safe: Oracle keeps its own judgment).
3. **Re-prompt backstop** — `_reconsider_oracle_security`, modeled on
   `_validate_oracle_metrics` (re-prompt-then-accept, NOT a hard ship-override):
   when the Oracle NO-SHIPs while EVERY deterministic signal is green
   (hard gates pass, scenarios pass, security_high_count == 0, not
   composite-blocked), re-prompt ONCE to neutralize security/score reasoning. A
   concrete non-security spec-compliance/robustness blocker survives (Oracle keeps
   NO-SHIP and restates it).
4. **`oracle.md`** updated so the agent definition is consistent: security is
   adjudicated by the deterministic verdict block, not review prose.

## Authorization model
Neither trigger fires. All findings move BLOCKING→ADVISORY (de-escalation). Files
touched: `models/pipeline.py`, `pipeline/iteration.py` (oracle/review steps),
`agents/oracle.md`, tests. NOT `barriers.py`, NOT quality-gate.sh report rows,
NOT `scenario_lint.py`. No string is promoted to an Implementer-readable gating
input (the opposite — a Reviewer-only prose string becomes LESS authoritative).

## Deferred (named residuals)
- **Changed-set scoping of the security gate** (Neo's "narrow C"): block only on
  deterministic HIGH findings in files changed-vs-the-run-start-baseline. NOT
  load-bearing for this FP (scanner found 0 HIGH), and carries a baseline-SHA
  correctness trap (must diff vs run-start baseline, not HEAD-vs-last-iter, or a
  file the implementer legitimately changed in iter-1 is misclassified in iter-3).
- **Broad diff-scoping of ALL dimensions** (Robustness/SpecCompliance prose can
  still carry fabricated blockers): a larger, authorization-sensitive design.
- **Scanner blind-spot now load-bearing**: a real HIGH the regex scanner misses
  ships silently. Mitigation: keep LLM security observations visible as advisory;
  maintain `SECURITY_PATTERNS` coverage.

## Validation
Full unit suite green (3959 passed); 5 new tests
(`TestOracleDeterministicSecurity`). End-to-end: re-run `notes-crud-ts` after the
Linus+Codex dual gate.
