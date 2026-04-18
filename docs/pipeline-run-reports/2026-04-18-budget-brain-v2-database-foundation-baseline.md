# Pipeline Run Report — database-foundation (Phase 6 baseline)

**Target:** `budget-brain-v2`
**Spec:** `2026-03-28-database-foundation`
**Run ID:** `3c56fc7f6e6f4bdf`
**Date:** 2026-04-18 (pipeline-side: `2026-04-18T06:09:23Z` gate timestamp)
**Outcome:** **SHIPPED** (via `SHIP_WITH_GAPS`, 7 stable failures)
**Iterations:** 2 of 3
**Wall time:** ~53 min (3161s to `SPEC_SHIPPED`)

## Context

First run after the **artifact preservation scheme** was added to `/df-pipeline-run`
(commit `51d11b9`). The prior escalated run `d6967cc1b09d4355` was archived to
`$TARGET/.dark-factory/archived-runs/d6967cc1b09d4355/` before reset — confirming the
preservation loop works end-to-end. This run produces the anchor source for
the upcoming Phase 6 V-dimension sweep (V4 mandatory first per triage, then V1/V2/V3).

## Iteration trace

| Iter | Implementer attempts | Smoke | Gate | Pass rate | Oracle |
|------|----------------------|-------|------|-----------|--------|
| 1 | 3 (414s + 271s + 61s) | PASS | FAIL (exit=1) | 83% | NO-SHIP |
| 2 | 1 (206s) | PASS | FAIL (exit=1) | 83% | SHIP (ship-with-gaps, 7 stable failures) |

Iter 2 pass_rate was **identical** to iter 1 and the implementer produced no net movement.
The ship decision fired on the `SHIP_WITH_GAPS` path: the 7 failing tests were classified
as stable failures and accepted.

## Phase 6 instrumentation — all wired and emitting

**I1 `ARCHITECT_MANIFEST_HASH`**
```
manifest_hash=742637dd0b496f3e... cache_hit=True
```
Cache hit: the prior run's architect manifest at
`.dark-factory/architecture/<spec>-manifest.yaml` was preserved by Step 1's wipe list
and re-used. **Implication for V-runs:** a "free" V1 run on this project will *also* hit
the architect cache unless architecture/ is explicitly evicted — currently only V2
(`--anchor-manifest`) deliberately exercises this path. To measure true architect
variance, either pass `--anchor-manifest` with a different source or extend Step 1 to
also move architecture/ aside.

**I4 `ITERATION_TEMPORAL_SIGNATURE`** — the signal of this run
| Iter | moved→pass | moved→fail | unchanged | prior_hash | curr_hash |
|------|-----------|-----------|-----------|------------|-----------|
| 1 | 0 | 7 | 0 | `4f53cda1` | `c9c3d244` |
| 2 | 0 | 0 | 7 | `c9c3d244` | `c9c3d244` |

Iter 2 is a **textbook zero-gain iteration**: identical failure-set hashes and zero
test movement. The same 7 tests failed verbatim in both iterations despite an
implementer attempt. This is exactly the signature the improvement plan predicted for
stable-failure scenarios.

**B4 `CLASSIFIER_IO_HASH`** — determinism check
```
iter 1: input_hash=492134a24abf… output_hash=ca54a17d3ff2…
iter 2: input_hash=492134a24abf… output_hash=ca54a17d3ff2…
```
Input hash identical (same failing-test set → same classifier input) → output hash
identical. **Classifier is deterministic** on this run. No evidence of stochastic
drift in the defect-classifier agent.

**I2 `SCENARIO_FINGERPRINT`**
```
phase=generate: 6 events
phase=coverage_regen: 22 events  (11 scenarios × 2 regen attempts)
```
Coverage regen ran twice (first attempt failed the floor, second passed) and both
emitted fingerprints for the full 11-file set. `variance-diff.py` will pick the last
phase per scenario_path, so comparison-time behavior is unaffected.

## Quality gate summary

```
| Gate | Status |
|------|--------|
| Install | PASS |
| Setup | PASS |
| Build | PASS |
| Lint | PASS |
| Type Check | PASS |
| Self-Tests | PASS |
| Blind Scenarios (executable) | FAIL |
| Mutation Testing | SKIP |

Result: FAIL  (→ overridden by SHIP_WITH_GAPS at oracle)
```

Build/lint/types/self-tests all clean. The FAIL is entirely in the blind scenarios
at 83% pass rate.

## Substrate verification — infra carryover

```json
{
  "status": "fail",
  "findings": [{
    "severity": "error",
    "code": "substrate-psycopg-missing",
    "message": "psycopg is required to run verify_substrate but is not installed in the gate environment"
  }]
}
```

Same psycopg-missing finding carried over from the `d6967cc1` run. This is a **gate
container provisioning gap**, not an agent failure — worth surfacing to the V4
(environment-anchored) analysis because it's exactly the kind of environment-side
noise an env anchor should factor out.

## Notable events

- `SPEC_SHIPPED` at 3161s after `SHIP_WITH_GAPS` (pass_rate=83, 7 stable failures accepted)
- `ORACLE_METRIC_DELTA` both iterations: `claimed=83, actual=83, delta=0` — no oracle hallucination
- `FEEDBACK_WORD_COUNT=323` for iter 2 feedback — under the ≤400-word guardrail
- **0 `BARRIER_PATH_ECHO_DETECTED`** events in 115 events.jsonl lines — no barrier violations
- `SMOKE_COMPLETED success=True` both iterations on first attempt
- `TRAJECTORY_CLASSIFIED` fired at iter 2 (stable failure pattern detected)
- Coverage regen required two attempts (first missed the floor; second satisfied it)

## Artifact paths for V-run anchoring

This run's artifacts are now the stable anchor source for V-dimension analysis:

| V-run | Flag | Anchor path |
|-------|------|-------------|
| V2 | `--anchor-manifest` | `$TARGET/.dark-factory/archived-runs/3c56fc7f6e6f4bdf/architecture/2026-03-28-database-foundation-manifest.yaml` |
| V3 | `--anchor-scenarios` (+ V2 flag) | `$TARGET/.dark-factory/archived-runs/3c56fc7f6e6f4bdf/scenarios/2026-03-28-database-foundation/` |
| V4 | `--anchor-environment` | `$TARGET/.dark-factory/archived-runs/3c56fc7f6e6f4bdf/architecture/environment.toml` |

## Follow-ups

1. **`init --update` bug** — mid-pre-flight, `dark-factory init --update` rewrote
   `project-hints.toml` despite the skill claiming it preserves that file. It replaced
   a customized 58-line hints file (with consistency_rules policy) with a fresh
   42-line minimal template. Restored from HEAD before proceeding. **Action:** audit
   `dark-factory init --update` behavior for `project-hints.toml` — it should only
   refresh `[detected]` and preserve `[policy.*]`.

2. **Architect cache-hit behavior on V1** — for a true baseline-vs-V1 comparison we
   need architecture/ to be evicted in V1. Either extend `/df-pipeline-run` Step 1b
   to also wipe architecture/, or add a separate `/df-pipeline-run-v1` variant that
   does the deep wipe. V2/V3/V4 don't have this issue since they all write to
   architecture/ via `apply_anchors`.

3. **Zero-gain iter 2 with ship-with-gaps override** — the orchestrator correctly
   detected the stable-failure pattern and shipped, but this is the first recorded
   case of `SHIP_WITH_GAPS` firing on a 2-iteration run. The semantics of "stable
   failure" vs "not enough iterations to tell" deserve a closer look — this ran
   in iter 2, ship-with-gaps was already eligible per the log excerpt.
