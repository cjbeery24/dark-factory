# bb spec-4 (database-foundation) re-run 465c1e39 — EMPIRICAL POST-MORTEM

> Run with S0(a/b/c) observability live. First spec-4 run with COMPLETE timestamped
> agent traces (S0c). Author: Claude. Status: DRAFT for Neo + Codex critical review.
> Context: I (Claude) proposed THREE wrong single-cause diagnoses earlier this session
> (exploration-bound, stdout-buffering, simple rate-limiting) — each disproven by ground
> truth. This post-mortem is built ONLY on the captured numbers; please attack the
> conclusion the same way.

## 1. Outcome
- Run **terminated by signal 15** (external kill = the 5-hour usage limit was hit) at ~19:08
  during TE invocation #5. NOT a clean SHIP or SPEC_FAILED — it never reached a verdict.
- Pipeline reached: spec-validator ✓ → architect ✓ → qa-holdout ✓ → test-engineer (5 invocations,
  ALL timed out at 900s) → killed mid-6th.
- 7 scenario `.test.ts` files on disk at crash (salvage had reached 12 files; see §5).

## 2. The measured root cause (MULTI-CAUSAL — overturns all prior single-cause theories)

### Primary: per-turn EXTENDED-THINKING latency, and it is SYSTEMIC (every agent)
Every agent emits TINY outputs but spends minutes per turn in extended `thinking` blocks:

| agent | wall_s | model_s | longest_gap_s | turns | Σ output_tok (lowerbound) |
|---|---|---|---|---|---|
| spec-validator | 605 | 590 | **347** | 33 | 915 |
| architect | 481 | 305 | **181** | 62 | 881 |
| qa-holdout | 794 | 782 | **324** | 44 | 1685 |
| TE generate#1 | 900 (timeout) | 859 | **236** | 69 | 2138 |
| TE generate#2 | 900 (timeout) | 648 | 184 | 109 | 3359 |
| TE validate#1 | 900 (timeout) | 811 | **520** | 75 | 3044 |
| TE validate#2 | 900 (timeout) | 46 | 8 | 37 | **idle_tail=835s** |

- Direct evidence the gaps are thinking: every multi-minute gap on TE-gen#1 (236s/206s/144s/81s)
  arrives as an `assistant` message whose `content` is a `thinking` block with `output_tokens` of
  **24–34**. The model reasons for minutes, then emits ~30 tokens. So `time_in_model` is THINKING
  latency, NOT generation volume. NO assistant turn anywhere had >500 output tokens.
- `time_in_tools` is 1–10s everywhere → it is NOT exploration-bound (my retracted theory). Tool
  calls (Read/Glob/Bash) are fast; the wall is the thinking BETWEEN them.

### Why the TE specifically times out (and analysis agents don't): TURN COUNT
- Analysis agents finish in 33–62 turns → 33–62 thinking-turns fit in ≤900s (qa-holdout barely, 794s).
- The TE needs 69–109 turns to read context + generate ~12–15 scenario files. `turns ×
  minutes-of-thinking-per-turn` exceeds 900s. The TE is the first/worst victim of a SYSTEMIC
  per-turn cost because it has the most turns.

### Amplifier (dominant late in the run): 5-HOUR rate-budget exhaustion → latency EXPLODES
- HARD evidence this run (unlike the prior run's ambiguous `allowed_warning`): the 5-hour
  `rate_limit_event` utilization climbs **0.90 → 0.91 → … → 0.99 → 1.00**, and there is an actual
  **`status: "rejected"`** event. This is the "usage limit" that killed the run (signal 15).
- The per-turn stalls ESCALATE as the budget exhausts, tracking utilization upward:
  gen#1 longest_gap 236s → validate#1 520s → validate#2 a single **835s hang** (one request that
  never returned: last event at t=64s, killed at 900s). So near budget-exhaustion, a normally
  ~30–60s thinking-turn balloons to 200–835s. The rate limit doesn't (only) reject — it
  catastrophically inflates per-request latency.
- Corollary: on a FRESH 5-hour budget the same work might fit (qa-holdout's 794s succeeded EARLY
  in the run before the budget exhausted; the TE attempts got progressively slower as it filled).

## 3. What is NOT the cause (retractions, each now disproven by THIS run's data)
- NOT exploration-bound: tools = 1–10s per agent.
- NOT generation-volume: Σ output_tokens ≤ 3.4k per agent; no turn > 500 tok.
- NOT stdout block-buffering: S0c captured COMPLETE timestamped streams (119/188/142/68 events,
  including the late writes at t=848–895s that the old buffered dumps lost).

## 4. S0 observability/correctness validated IN PRODUCTION
- **S0(c) worked**: complete arrival-timestamped streams + `wall_attribution` on every invocation
  — this entire diagnosis is only possible because of it. The old buffered dumps would have shown
  a truncated exploration prefix (exactly the validate-att2 mystery) and led to another wrong guess.
- **S0(b) worked**: Check 9 `typescript_compilation` returned **FAIL "tsc found scenario code
  errors"** on the salvaged files (with `@types/node` resolved) — previously it would have
  silently SKIPPED (no-op). It caught real TS errors in salvaged scenarios. Concrete production win.
- **S0(a)**: tool-mix / first_write_turn / output_tokens_stream_lowerbound all populated and used above.

## 5. Salvage path (S41 + S0b interaction)
- TE generate#2 timed out but left files → `SCENARIO_TIMEOUT_SALVAGE` (file_count=10, later 12).
- SA-validate ran: file_count PASS (12), assertions/trivial/empty/imports all PASS, but
  **typescript_compilation FAIL** (real code errors). So the salvaged set was structurally present
  but not type-clean. Validate-regen (TE#3, TE#4) then timed out trying to fix → run killed.

## 6. Candidate levers (NOT yet decided — for Neo + Codex to critique)
- **L1 — cap thinking budget per agent** (`MAX_THINKING_TOKENS` env, sibling of the existing
  `CLAUDE_CODE_MAX_OUTPUT_TOKENS`). Directly attacks the dominant per-turn cost. RISK: the agents
  may be reasoning hard *because the spec is hard* — cutting thinking could cut quality/correctness.
  Unknown how much of the 200–835s is intrinsic thinking vs rate-inflation.
- **L2 — raise per-role TE timeout (~1800s)** (the original S1a). Fits more thinking-turns. RISK:
  blunt; on an exhausting rate budget it just burns more budget before walling; doesn't address the
  systemic per-turn cost.
- **L3 — S2 anchor-batched parallel TE**: N TE containers, disjoint anchor subsets → fewer
  turns/scenarios per invocation → each fits the wall. Durable ceiling-remover. RISK: complex;
  N concurrent agents burn the shared rate budget FASTER (may hit the 5-hour limit sooner).
- **L4 — reduce TE context size** (cache_read is 4–9M tokens/invocation): smaller per-turn input
  may reduce thinking/latency. Unmeasured whether context size drives the latency.
- **L5 — rate-budget-aware pacing**: detect 5-hour utilization climbing toward 1.0 and
  pause/back-off/resume rather than burning attempts into a wall + crashing. Addresses the
  amplifier + the crash, exogenous to the per-turn cost.

## 6b. POST-REVIEW CORRECTIONS (Neo + Codex independent review + Caleb steer)
- **VERDICT: REVISE.** Core mechanism (systemic INTRINSIC extended-thinking × high TE turn-count
  → 900s wall) is independently VERIFIED and sound. Two corrections:
- **RATE-LIMITING DROPPED (Caleb: one-off, not systemic; data agrees).** Neo's spot-check: the
  LARGEST thinking gaps (gen#1 236s/206s) occurred at FULL budget / zero utilization, and
  within-attempt gaps SHRANK as utilization rose. So rate-exhaustion did NOT inflate per-turn
  latency — it was only the TERMINAL crash (val#2 835s hang → signal-15). The §2 "amplifier
  tracks utilization 0.90→1.0" framing was BACKWARDS; cut it. L5 (rate-aware pacing) dropped.
- **Latency is INTRINSIC thinking, settled.** Early agents (architect 181s, qa-holdout 324s,
  spec-validator 347s max gaps) had huge thinking gaps at LOW utilization → thinking is
  intrinsically slow, not rate-inflated. Infrastructure (proxy/Docker/network) ruled out: every
  big gap terminates in a real `thinking` block and `time_in_tools` is 1–10s (Neo); the one
  exception is val#2's 835s idle-tail-after-tool-result (a genuine service/rate stall, the crash).
- **S0(c) OBSERVABILITY BUG (both reviewers): sidecar filename collision.** The sidecar
  `_stream_path` (`…_{phase}_attempt{N}.jsonl`) uses invoke_agent's INTERNAL retry counter, so a
  2nd TE invocation in the same phase (the SA-validate loop calls invoke_agent repeatedly)
  OVERWRITES the 1st's sidecar → val#1's dump wall_attribution (520s, session 744ed194) is not
  re-derivable from the on-disk sidecar (134s, session 652a9636). The dump number was correct at
  capture time but the sidecar got clobbered. FIX: make the sidecar filename unique per
  invoke_agent call (timestamp / global counter), matching the dump's `ts`+`it` disambiguation.

## 6c. FIX PLAN (post-review, rate-limiting dropped)
1. **S0(c) sidecar-collision fix** (cheap, correctness — my code; both reviewers flagged). Unique
   sidecar filename per invoke_agent call.
2. **L2 — per-role TE timeout ~1800s** (the cheap BRIDGE, now evidence-justified: turns ×
   intrinsic-thinking just overran 900s; multiple agents finished in 480–794s, the TE was close).
   This is the original S1(a) — but NOT the warm-regen part (Neo: warming is low-value, exploration
   is only 1–10s). Per-call `timeout_override`.
3. **L3 — parallel anchor-batched TE (= S2)**: the DURABLE structural fix. N TE containers, disjoint
   anchor subsets → fewer turns/invocation → fits the wall. Both rank it the top structural lever;
   Neo: the "burns rate faster" worry is OVERSTATED (thinking is wall-clock-dominated, ≤73 output
   tok/turn). Real cost = complexity.
4. **L1 — cap MAX_THINKING_TOKENS: DEFERRED, do NOT lead with it.** Data-supported (all big gaps are
   thinking) but a QUALITY FOOTGUN on a barrier-isolated correctness agent — needs a controlled
   quality comparison + a gate, not a blind cap. Revisit only if L2+L3 insufficient.
- **L4 (reduce context)**: unmeasured, deferred.

## 7. Open questions for the review (ANSWERED above — kept for provenance)
1. Is the 200–835s per-turn latency mostly INTRINSIC extended-thinking, or mostly RATE-INFLATION?
   (The escalation tracking utilization 0.90→1.0 suggests rate is a big multiplier — but gen#1's
   236s gap happened before the worst exhaustion.) This determines whether L1/L3 (cut the work) or
   L5/fresh-budget (fix the amplifier) is primary. How would we separate them with one more run?
2. Is cutting thinking (L1) safe for correctness on a barrier-isolated TE, or does it degrade the
   scenarios? Is there a middle setting?
3. Does L3 (parallel TE) help or HURT given the shared rate budget (more concurrent requests →
   faster exhaustion)?
4. Is "run on a fresh 5-hour budget" the honest first move (it's free and the data suggests early-run
   work fits), with structural levers as durable follow-ups — or is that ducking the real fix?
5. Anything in the data I'm still over-reading? (3 prior diagnoses collapsed — attack this one.)
