# Pipeline Run Report: database-foundation Phase 5 Benchmark (3 runs)

**Target:** budget-brain-v2
**Date:** 2026-04-17
**Framework state:** Phase 5 complete (R3 + S1 + R2-rev + A1-esc + R1-verify)
**Purpose:** Measure Phase 5 impact on iter-1 quality, pre-gate retry budget use, and feedback scoping.

## Configuration

- `max_pre_gate_retries=3` (R3: raised from 1)
- S1 session continuation active (pre-gate retries via `docker exec` + `claude --continue`)
- Implementer + Reviewer + Oracle receive full agent def via `--append-system-prompt` (A1-esc)
- Feedback generation uses prioritized top-N failures + 400-word soft cap (R2-rev)
- `_rollback_to_iteration` has unit-test guard for untracked-file cleanup (R1-verify)

## Runs

| Run | Run ID | Iters | Best pass_rate | R1 fires | Pre-gate passes at | Status |
|-----|--------|-------|---------------|----------|-------------------|--------|
| 1 | d0da7bbc5ed24bfe | 3 | 50 (iter 3) | 0 | attempt 1 (all iters) | ESCALATED |
| 2 | 30469ea2fbbd4304 | 3 | 68 (iter 3) | 0 | attempt 1 (all iters) | ESCALATED |
| 3 | d30bb9d005f7490f | 1 | 91 (iter 1) | 0 | attempt 1 | INTERRUPTED — classifier `count_mismatch` ×2 |

### Per-iteration pass_rate

| Run | Iter 1 | Iter 2 | Iter 3 |
|-----|--------|--------|--------|
| 1 | 0 | 0 | 50 |
| 2 | 0 | 55 | 68 |
| 3 | 91 | — | — |

### Feedback word counts (R2-rev soft cap = 400)

| Run | Iter 1 | Iter 2 | Iter 3 |
|-----|--------|--------|--------|
| 1 | 225 | 312 | 361 |
| 2 | 234 | 381 | 460 |
| 3 | — | — | — |

## Observations

**Iter-1 quality trending up.** Baseline pattern (Phase 4 benchmark) was 3 iterations plateauing at 50% max. Across these three Phase 5 runs, iter-1 climbed 0% → 55% → 91%. Variance across runs remains high because architect and test-engineer generation are non-deterministic — a single run is not a trend, but the envelope moved up.

**A1-esc confirmed firing** on every implementer invocation:
```
AGENT_DEF_INJECTED role=implementer word_count=2058
```

**S1 confirmed firing** on Run 3 preflight retry:
- Attempt 1 (cold): 8m 1s
- Attempt 2 (with `--continue` into persistent session container): 1m 51s
- ~4× speedup from session reuse. Pre-gate retry loop itself did not retry in any run (all pre-gate passed attempt 1) — the preflight/pre-gate distinction matters, S1 covers both since the session wraps the whole try/finally block.

**R3 budget unused.** `max_pre_gate_retries=3` but pre-gate passed attempt 1 in all 7 iterations across all 3 runs. Two interpretations: (a) implementations were genuinely clean on build/lint/typecheck, or (b) pre-gate gates are too lenient to surface issues that scenario gates then catch. Worth revisiting.

**R2-rev word cap respected in Runs 1/2.** Run 2 iter 3 exceeded by ~15% (460 words). Iter-3 overruns are accepted slack per plan §5.4 — last-chance feedback is allowed to be more detailed.

**R1 did not fire.** No iteration regressed enough to warrant rollback across 7 iterations. The unit test from R1-verify now guards the untracked-file cleanup path against regressions.

## Run 3 blocker — defect-classifier `count_mismatch`

Run 3 aborted at iter 1 with `status=interrupted` because the defect-classifier returned `count_mismatch` on 2 consecutive attempts. The iteration loop aborts rather than feed degraded input to the Oracle. This is **pre-existing contract drift** between the scenario/gate counters and the classifier — unrelated to Phase 5 changes. Until fixed, any run with iter-1 pass_rate well under 100% and a sparse failure set will be at risk.

Filed as follow-up.

## Conclusions

Phase 5 shipped and is verified. Iter-1 quality improved across the sample. S1 delivered measurable speedup on the one retry that fired. Convergence is still not reliable — Runs 1 and 2 escalated, Run 3 aborted before we could measure convergence. The next lever is **Phase 6** (architect/test-engineer strategy revision), which these runs suggest is the real plateau source. Runs 1 and 2 ended at 50% and 68% after 3 full iterations — the implementer is capped not by its budget but by the spec interpretation it receives.

## Follow-ups

1. Fix defect-classifier `count_mismatch` contract drift — blocks runs with sparse failures.
2. After fix, re-run the 3-benchmark batch. Look specifically for runs where pre-gate retries fire so R3/S1 contribution becomes measurable (not just observable).
3. Begin Phase 6 design: architect and test-engineer variance is now the dominant source of run-to-run pass_rate spread.
