# Pipeline Run Report: budget-brain-v2 / monorepo-tooling

**Date:** 2026-03-29 13:23
**Repo:** budget-brain-v2
**Spec:** 2026-03-28-monorepo-tooling
**Context:** First pipeline run after Phases 1, 2, 4 completion (path-to-first-ship.md)

## Changes Since Last Run

Phases 1, 2, 4 from `path-to-first-ship.md` are now complete:
- **Phase 1:** Mechanical stable-failure detection + Oracle SHIP override when hard gates pass
- **Phase 2:** SA output validation + retry loop with 16 structural checks
- **Phase 4:** Tool documentation injection for SA (Biome, ESLint, Railway, pnpm, Vitest, YAML)

Previous best result: 94% pass rate (run 4 on 2026-03-29-08-40 report), still ESCALATED.

## Baseline Metrics (from prior runs)

| Metric | Prior Best | Target |
|--------|-----------|--------|
| Pass rate | 94% | 100% (or SHIP via stable-failure override) |
| Hard gates (build/lint/typecheck) | 100% all runs | 100% |
| SA first-attempt validation | Unknown | >70% |
| Stable failures | ~6-16% | <20% |

---

## Run 1

**Start time:** 13:24 (2026-03-29)
**Duration:** ~17 min (SA only — pipeline did not reach implementation)
**Outcome:** INTERRUPTED — SA validation failed after 3 attempts

### Pipeline Events
- 13:24 — Spec validation passed (1 ambiguity warning: "fast" on line 213)
- 13:26 — SA attempt 1 started
- 13:31 — SA attempt 1 validation: 4 FAIL checks (typescript_compilation, assertion_upper_bound×2, no_source_reads×1, no_project_writes×1)
- 13:31 — SA attempt 2 started (retry)
- 13:35 — SA attempt 2 validation: 4 FAIL checks (typescript_compilation, serialization_brittleness×2, assertion_upper_bound×2, no_source_reads×2)
- 13:35 — SA attempt 3 started (retry)
- 13:41 — SA attempt 3 validation: 3 FAIL checks (typescript_compilation, serialization_brittleness×1, assertion_upper_bound×3)
- 13:41 — Pipeline interrupted: SA validation failed after max retries

### Hard Gate Results
- N/A — Pipeline never reached implementation/gate phase

### Scenario Results
- N/A — Pipeline never reached scenario execution

### SA Validation Progression

| Check | Attempt 1 | Attempt 2 | Attempt 3 | Trend |
|-------|-----------|-----------|-----------|-------|
| typescript_compilation | FAIL | FAIL | FAIL | **Unfixable** — tsc not installed on host |
| assertion_upper_bound | 2 files | 2 files | 3 files | Worsening |
| no_source_reads | 1 file | 2 files | 0 files | Fixed on attempt 3 |
| no_project_writes | 1 file | 0 files | 0 files | Fixed on attempt 2 |
| serialization_brittleness | 0 files | 2 files | 1 file | Regressed then improved |

### Observations
- Phase 2 validation retry loop is working correctly — caught real issues and retried
- SA progressively fixed `no_source_reads` (barrier violation) and `no_project_writes` (state pollution)
- `typescript_compilation` FAIL is **not the SA's fault** — `npx tsc` on host prints "This is not the tsc command you are looking for" because TypeScript isn't a host dependency. This consumes one of the SA's 3 retry attempts every time.
- `assertion_upper_bound` (>15/file) getting worse across retries — SA struggles to stay under limit

### Key Findings
1. **BUG FOUND:** `typescript_compilation` validation runs `npx tsc` on host but TypeScript isn't installed. The exit code is non-zero but `FileNotFoundError` isn't raised (npx exists), so it counts as a real compilation failure instead of graceful skip.
2. **FIX APPLIED:** Added detection for "This is not the tsc command" message → gracefully skip.
3. Without the tsc false failure, SA attempt 3 would have had only 2 failures (serialization_brittleness, assertion_upper_bound) — potentially passable.
4. Phase 2 validation catches real SA issues (barrier violations, state pollution) — working as designed.

### tsc Validation Analysis

The `typescript_compilation` check was a false blocker in Run 1. Analysis of the design trade-offs:

**Architecture:** The scenario validation runs on the **host** (orchestrator process via `subprocess.run`), not inside the SA container. The SA container has TypeScript installed (via `docker/lockfiles/node-tools/package.json`), but validation doesn't use it.

**Fix applied (for Run 2):** Detect the "This is not the tsc command" npx error message and treat as graceful skip, consistent with the existing `FileNotFoundError` and `TimeoutExpired` graceful degradation.

**Trade-offs:**
| Approach | Pro | Con |
|----------|-----|-----|
| **Skip tsc (current fix)** | Zero host dependencies; matches existing degradation pattern; unblocks pipeline immediately | Loses compile-time scenario validation; real TypeScript errors caught later at QG |
| **Install tsc on host** | Real validation; catches syntax/import errors early | Adds host dependency; doc/onboarding friction |
| **Run validation in container** | Best of both worlds; uses already-installed tsc | Larger refactor; validator.py is host-only today |

**Recommendation:** Current fix is correct for now. Ideal long-term fix is running scenario validation inside a container (tsc + vitest/node already available there).

---

## Run 2

**Start time:** ~13:44 (2026-03-29)
**Duration:** ~21 min (SA only — pipeline did not reach implementation)
**Outcome:** INTERRUPTED — SA validation failed after 3 attempts
**tsc fix:** Confirmed working (PASS: "tsc not available (skipped)")

### SA Validation Progression

| Check | Attempt 1 | Attempt 2 | Attempt 3 | Trend |
|-------|-----------|-----------|-----------|-------|
| typescript_compilation | PASS (skipped) | PASS (skipped) | PASS (skipped) | **Fixed by tsc patch** |
| assertion_upper_bound | 5/5 files | 5/5 files | 4/5 files | **Persistent blocker** |
| serialization_brittleness | 2 files | 0 files | 0 files | Fixed on attempt 2 |
| no_source_reads | 2 files | 0 files | 1 file | Mixed |
| no_project_writes | 2 files | 1 file | 1 file | Improving |

### Observations
- tsc false failure eliminated — tsc fix confirmed working
- **assertion_upper_bound is the sole systematic blocker** — SA cannot stay under 15 assertions/file
- SA fixes serialization_brittleness, source_reads, project_writes across retries but assertion count is persistent
- SA creates tests with 2-3 `expect()` calls per `it()` block; 10 tests × 2.5 avg = 25 assertions, well over 15

### Key Findings
1. `assertion_upper_bound` with limit=15 is too restrictive for this spec's complexity (39 contracts, 26 scenario intents)
2. The SA prompt says "5-15 tests" but validator counts `expect()` calls, not `it()` blocks — terminology mismatch
3. **FIXES APPLIED for Run 3:**
   - Increased `_MAX_ASSERTIONS_PER_FILE` from 15 → 20
   - Added actual assertion counts to validation feedback (e.g., "file.test.ts (22 assertions)")
   - Updated SA agent prompt: clarified "20 expect/assert calls per file" and "each expect() counts as one"

---

## Run 3

**Start time:** ~14:12 (2026-03-29)
**Duration:** ~35 min (full pipeline — 3 iterations)
**Outcome:** ESCALATED after 3 iterations
**SA validation:** PASSED on attempt 3 (first time ever past SA validation!)

### SA Validation Progression

| Check | Attempt 1 | Attempt 2 | Attempt 3 | Trend |
|-------|-----------|-----------|-----------|-------|
| typescript_compilation | PASS (skipped) | PASS (skipped) | PASS (skipped) | Fixed |
| assertion_upper_bound | 1 file (17) | 1 file (33!) | 0 files | **PASSED** (note: used old 15 limit due to .pyc cache) |
| serialization_brittleness | 1 file | 0 files | 0 files | Fixed |
| no_source_reads | 1 file | 0 files | 0 files | Fixed |
| no_project_writes | 0 files | 0 files | 0 files | Clean |
| no_hardcoded_paths | 0 files | 3 files | 0 files | Regressed then fixed |

### Iteration Results

| Iteration | Scenario Pass Rate | Key Failures |
|-----------|-------------------|--------------|
| 1 | 88% (47/53) | Biome config unknown keys (`typescript`, `trailingComma`); kebab-case regex; FR-1.2 workspace listing |
| 2 | 88% (47/53) | Identical failures — Implementer did not fix Biome config |
| 3 | 90% (48/53) | Biome config fixed (unknown keys removed), but format/lint now correctly finds real issues in code |

### Hard Gate Results (all iterations)
- Build: PASS (all 3 iterations)
- Lint: PASS (all 3 iterations)
- TypeCheck: PASS (all 3 iterations)
- Self-Tests: FAIL (Biome formatting issues in implementation code)

### Failure Breakdown

**5 remaining scenario failures (iter 3):**
1. **FR-1.2** (workspace listing): SA expects 3 workspaces in `pnpm ls` output — implementation issue
2. **FR-3.1** (lint exits 0): Biome lint now works but finds real issues in implementation
3. **FR-3.3** (format:check exits 0): Biome format finds unformatted code
4. **FR-3.4** (format:check exits non-zero on unformatted): Test logic issue
5. **Cross.2** (kebab-case naming): SA regex `^[a-z0-9-]+\.(json|js|yaml)$` rejects `tsconfig.base.json` — **SA bug** (regex doesn't allow dots in basename)

**Root cause analysis:**
- Biome config was wrong (unknown keys) in iters 1-2, fixed in iter 3 but format/lint then found real code issues
- FR-3.x failures are cascading from implementation not being formatted/linted clean
- Cross.2 is an SA regex bug (too strict)
- FR-1.2 may be an implementation or SA issue

### Key Findings
1. **MILESTONE:** First time ever past SA validation — tsc fix and assertion count feedback (showing actual counts) were instrumental
2. **.pyc cache issue:** Run 3 still used old 15 assertion limit despite source change to 20 — stale bytecode. Must clear `__pycache__` between runs.
3. **Iteration regression:** Iter 3 worsened self-tests (14/40 vs 13/25) because fixing Biome config exposed real formatting issues
4. **Implementer blind spot:** Biome config error persisted for 2 iterations — the Oracle/feedback mechanism didn't surface this specific error clearly enough
5. **SA assertion budgeting improved:** SA reduced to 3 Tier 2 files with ≤15 assertions each (though one attempt had 33 in a single file)

---

## Run 4

**Start time:** ~14:47 (2026-03-29)
**Duration:** ~18 min (SA only — pipeline did not reach implementation)
**Outcome:** INTERRUPTED — SA validation failed after 3 attempts
**Assertion limit:** Still 15 (config model default not updated until mid-run)

### SA Validation Progression

| Check | Attempt 1 | Attempt 2 | Attempt 3 | Trend |
|-------|-----------|-----------|-----------|-------|
| assertion_upper_bound | 1 file (16) | 0 files | 1 file (23) | SA nearly passed on attempt 2 |
| serialization_brittleness | 0 files | 1 file | 1 file | Regressed on attempt 2 |
| no_source_reads | 1 file | 0 files | 0 files | Fixed on attempt 2 |
| no_project_writes | 3 files | 0 files | 3 files | Yo-yo pattern |
| no_hardcoded_paths | 0 files | 0 files | 0 files | Clean |

### Observations
- SA attempt 2 was ONE check away from passing (only `serialization_brittleness` on 1 file)
- But attempt 3 regressed badly — SA can't maintain all constraints simultaneously
- With the 20 assertion limit, attempt 1 (16 assertions) and attempt 2 would have both passed assertion_upper_bound
- **BUG FOUND:** Assertion limit default was in TWO places: `validator.py` (changed to 20) and `config.py` (still 15). The orchestrator uses `config.py`, making the validator change ineffective.
- **FIX APPLIED:** Updated `darkfactory/models/config.py:43` to default to 20

---

## Cross-Run Analysis

### Run Summary

| Run | Duration | SA Passed? | Implementation? | Pass Rate | Outcome |
|-----|----------|-----------|-----------------|-----------|---------|
| 1 | ~17 min | No (tsc false fail) | No | N/A | INTERRUPTED |
| 2 | ~21 min | No (assertion limit) | No | N/A | INTERRUPTED |
| 3 | ~35 min | Yes (attempt 3) | Yes (3 iters) | 88→88→90% | ESCALATED |
| 4 | ~18 min | No (assertion + project writes) | No | N/A | INTERRUPTED |

### SA Validation: Persistent Blockers

The SA validation retry loop (Phase 2) caught real issues and enabled retries. But the SA consistently struggles with:

1. **assertion_upper_bound**: SA writes 16-33 assertions per file when limit is 15. Root cause: each `it()` block has 2-3 `expect()` calls, so 8 tests = 24 assertions. Fixed by raising limit to 20 + clarifying prompt.
2. **serialization_brittleness**: SA reads JSON/YAML files and uses string matching (toContain/toMatch) instead of JSON.parse. Feedback helps but doesn't fully eliminate.
3. **no_project_writes**: SA writes to project paths (should use temp dirs). Improves within a session but regresses across retries.
4. **Constraint interference**: When SA fixes one check, it often regresses on another. The SA can't reliably satisfy all 16 checks in 3 attempts.

### Implementation Quality (Run 3 only)

When the pipeline reaches implementation:
- **Hard gates pass 100%** — Build, Lint, TypeCheck clean every iteration
- **90% scenario pass rate** (best) — only 5 scenario failures after 3 iterations
- **Biome config was the main impl bug** — `typescript` and `trailingComma` as unknown keys; fixed by iteration 3
- **SA-side failures** (kebab-case regex) inflate the failure count

### Fixes Applied During This Session

| Fix | File | Impact |
|-----|------|--------|
| tsc graceful skip | `validator.py` | Eliminated false tsc failure (Run 1 → Run 2) |
| Assertion counts in feedback | `validator.py` | SA sees exact counts, not just "too many" |
| Assertion limit 15→20 (validator) | `validator.py` | Would allow 16-20 assertion files to pass |
| Assertion limit 15→20 (config) | `config.py` | **Critical** — orchestrator uses this default |
| SA prompt clarification | `scenario-architect.md` | "20 expect/assert calls per file" with explanation |
| .pyc cache awareness | N/A | Must clear `__pycache__` before each pipeline run |

### Key Takeaways

1. **Phase 2 validation works but SA can't reliably satisfy all constraints in 3 attempts.** The retry budget is tight.
2. **The assertion limit of 15 was the #1 blocker** across all runs. Raising to 20 + fixing the config model default should help significantly.
3. **When the pipeline reaches implementation, it performs well** — 90% scenario pass rate with hard gates clean.
4. **SA validation catches real issues** (source reads, project writes, hardcoded paths) — the checks are valuable.
5. **.pyc bytecode caching caused fixes to not take effect mid-session** — pipeline-relevant changes need __pycache__ cleared before the next `dark-factory run`.

## Improvement Metrics vs Prior Reports

| Metric | Pre-Phase 1/2/4 Best | Post-Phase 1/2/4 | Delta |
|--------|---------------------|-------------------|-------|
| Pass rate (when reaching impl) | 94% | 90% | -4% (fewer scenarios, different SA output) |
| Hard gates | 100% | 100% | Unchanged |
| SA first-attempt validation | N/A (no validation before) | 0/4 passed first attempt | New capability |
| SA passed validation (any attempt) | N/A | 1/4 runs (25%) | New capability — needs improvement |
| Outcome | ESCALATED (all prior runs) | ESCALATED (all 4 runs) | Unchanged |
| Pipeline reached implementation | 100% (no SA gate before) | 25% (1/4 runs) | SA gate is filtering more aggressively |

### Recommendations for Next Session

1. **Verify assertion limit fix works:** Clear __pycache__, confirm `scenario_max_assertions_per_file=20` in running process
2. **Consider raising SA retry budget from 3→4** if assertion limit fix isn't sufficient
3. **Phase 3 (probabilistic threshold):** Would help with the 90% pass rate — a 2/3 pass threshold for individual scenarios could convert 90% → SHIP
4. **Investigate `serialization_brittleness` persistence:** SA needs better guidance on parsing JSON/YAML before asserting
5. **Investigate `no_project_writes` oscillation:** SA fixes it then regresses — likely a fresh-session problem
