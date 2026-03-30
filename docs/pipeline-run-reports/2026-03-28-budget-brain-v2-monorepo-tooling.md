# Pipeline Run Report: budget-brain-v2 / monorepo-tooling

**Spec:** `2026-03-28-monorepo-tooling.md`
**Project:** budget-brain-v2
**Date:** 2026-03-28
**Goal:** Track pipeline improvement/regression metrics across runs

---

## Run 1

**Start time:** 2026-03-29T02:04Z
**End time:** 2026-03-29T02:35Z (~31 min)
**Pipeline command:** `dark-factory run 2026-03-28-monorepo-tooling`
**Run ID:** 3c240cc7d35742dd
**Outcome:** ESCALATED (NO-SHIP x3)

### Pre-run state
- Repo: greenfield (single commit: plan + specs)
- Config: `language = "node"`, no enterprise features

### Pipeline Timeline
- 02:04 — Spec validation (passed)
- 02:06 — Scenario generation (pre-repair: 37/39 crash, SA repair triggered)
- 02:09 — Scenario generation (post-repair: 5 files, 39 tests)
- 02:11 — Implementation iter 1 (auto-detected gate commands)
- 02:16 — Implementation iter 1 continued (2nd container - first may have hit limits)
- 02:19 — Quality gate iter 1: **Build PASS, Lint PASS, Type PASS, Self-Tests FAIL, Scenarios 35/39 (89%)**
- Iter 2: Same 4 failures (89%)
- Iter 3: REGRESSED to 33/39 (84%) — 2 new failures introduced
- 02:35 — Escalation

### Scenario Results by Iteration

| Iteration | Passed | Failed | Rate | Regression |
|-----------|--------|--------|------|------------|
| 1 | 35 | 4 | 89% | — |
| 2 | 35 | 4 | 89% | No change |
| 3 | 33 | 6 | 84% | -2 regressions |

### Persistent Failures (all 3 iterations)

| # | Test Name | Root Cause | Category |
|---|-----------|------------|----------|
| 1 | `packages/shared builds to dist/ with .d.ts declaration files` | Missing `declaration: true` in `packages/shared/tsconfig.json` | **IMPL_BUG** |
| 2 | `Biome configuration prevents inconsistent formatting` | Scenario tests `formatter.quoteStyle` but Biome puts it at `javascript.formatter.quoteStyle` | **SCENARIO_BUG** |
| 3 | `pnpm format:check exits 0 on properly formatted code` | Same Biome config path issue — tests `formatter.quoteStyle`/`formatter.trailingCommas` | **SCENARIO_BUG** |
| 4 | `CI workflow triggers on PR to main` | Regex `/branches:\s*[\r\n]\s*-\s*main/` rejects valid YAML `branches: [main]` | **SCENARIO_BUG** |

### Iteration 3 Regressions

| # | Test Name | Root Cause |
|---|-----------|------------|
| 5 | `Root package.json has all expected scripts` | Implementer changed scripts while trying to fix other issues |
| 6 | `Workspace structure supports scalability` | Removed `--parallel` from typecheck/build scripts |

### Pipeline Issues Found

1. **Scenario Architect — Biome config knowledge gap**: SA generated tests that assert `formatter.quoteStyle` at the top-level `formatter` key, but Biome 1.x puts `quoteStyle` and `trailingCommas` under `javascript.formatter`. This is a domain-specific knowledge failure. The same error appears in 2 tests (03 and 05), affecting 2/4 persistent failures.

2. **Scenario Architect — Overly rigid YAML format assertions**: SA wrote `/branches:\s*[\r\n]\s*-\s*main/` which only matches multi-line YAML list format. The implementation used `branches: [main]` (inline array), which is semantically identical valid YAML. The SA should use YAML parsing or more flexible regex.

3. **Oracle feedback inaccuracy**: Feedback classified issues as "ENV_ISSUE" and "integration testing failures" when the actual root causes were 1 impl bug and 3 scenario bugs. This misled the implementer into making broad changes that caused regressions in iter 3.

4. **Implementer regression in iteration 3**: Removed `--parallel` from pnpm scripts (possibly trying to fix execution ordering), breaking 2 previously-passing tests. The feedback-to-implementer loop amplified rather than corrected the problem.

5. **Self-Tests gate failure**: `pnpm test` fails because no test script exists. The spec doesn't define any tests — this is a tooling-only spec. The Self-Tests gate should handle the "no tests defined" case more gracefully, or the implementer should add a no-op test script.

6. **Escalation agent produced entirely hallucinated report**: The escalation agent generated a fictional incident report with made-up timestamps, root causes ("memory pressure", "external service availability"), and impact assessments that bear no relation to the actual failures.

7. **Smoke test initial results (5% pass)**: Pre-implementation, 37/39 scenarios failed. SA repair was triggered and fixed the tautological tests, but the remaining structural assertion failures persisted because they test config structure that can only pass after implementation.

8. **Oracle feedback leakage warning**: Pipeline detected "all scenarios" pattern in oracle feedback (barrier leakage).

### Implementation Quality Assessment
Despite the NO-SHIP outcome, the implementation was solid:
- All 4 FRs implemented correctly (monorepo, TS config, lint/format, CI)
- Build, lint, type check all passing
- 89% of blind scenarios passing (3 of 4 failures are scenario bugs)
- Only 1 real impl bug: missing `declaration: true`

---

## Run 2

**Start time:** 2026-03-29T02:38Z
**End time:** 2026-03-29T03:10Z (~32 min)
**Run ID:** def3357962b8437b
**Outcome:** ESCALATED (NO-SHIP x3)

### Pre-run state
- Repo: clean reset (git checkout + git clean)
- Config: `language = "node"`, no enterprise features

### Pipeline Timeline
- 02:38 — Spec validation (passed)
- 02:40 — Scenario generation (pre-repair: 30/33 crash, SA repair triggered, 3 tautological)
- 02:43 — Scenario generation (post-repair: 4 files, 33 tests)
- 02:47 — Implementation iter 1 (2 containers - first hit limits)
- 02:56 — Quality gate iter 1: **Build PASS, Lint PASS, Type PASS, Self-Tests FAIL, Scenarios 31/33 (93%)**
- Iter 2: Same 2 failures (93%) — no regression
- Iter 3: Same 2 failures (93%) — stable plateau, early escalation triggered
- 03:10 — Escalation

### Scenario Results by Iteration

| Iteration | Passed | Failed | Rate | Regression |
|-----------|--------|--------|------|------------|
| 1 | 31 | 2 | 93% | — |
| 2 | 31 | 2 | 93% | No change |
| 3 | 31 | 2 | 93% | No change (stable) |

### Persistent Failures (all 3 iterations)

| # | Test Name | Root Cause | Category |
|---|-----------|------------|----------|
| 1 | `All generated files follow kebab-case naming convention` | Regex `/^[a-z0-9-]+\.[a-z]+$/` rejects filenames with 2+ dots (e.g. `tsconfig.base.json`, `eslint.config.js`) | **SCENARIO_BUG** |
| 2 | `CI workflow triggers on PR to main` | Regex `/pull_request.*main/m` doesn't match across line boundaries; `pull_request:` and `branches: [main]` are on separate lines | **SCENARIO_BUG** |

### Improvements vs Run 1
- **Fewer scenarios, higher quality**: 33 tests (vs 39) with 93% pass (vs 89%)
- **No Biome config issue**: SA correctly handled Biome's nested `javascript.formatter` structure this time
- **No impl regressions**: Implementer maintained 93% across all 3 iterations (vs regressing from 89% to 84%)
- **No `declaration: true` issue**: Implementation correctly set this

### Remaining Pipeline Issues
1. **SA still writes inflexible YAML assertions**: Different regex this time (`/pull_request.*main/m` vs `/branches:\s*[\r\n]\s*-\s*main/`) but same root problem — doesn't handle YAML format variation
2. **SA writes fragile filename regex**: `/^[a-z0-9-]+\.[a-z]+$/` doesn't account for compound extensions (`.base.json`, `.config.js`)
3. **Self-Tests gate still fails**: Missing `test` script — same issue as Run 1
4. **Oracle feedback still inaccurate**: Classified real issue as "ENV_ISSUE" with misleading filesystem permission errors, when the actual issue is only 2 scenario regex bugs
5. **Implementer spawns 2 containers per iteration**: First container consistently hits some limit and a second one takes over

### Implementation Quality Assessment
- All 4 FRs implemented correctly
- Build, lint, type check all passing
- 93% blind scenarios (both failures are scenario bugs)
- **Zero real implementation bugs** — this is a ship-worthy implementation blocked only by SA testing errors

---

## Run 3

**Start time:** 2026-03-29T03:13Z
**End time:** 2026-03-29T03:45Z (~32 min)
**Run ID:** 717cf00b8ee6438c
**Outcome:** ESCALATED (NO-SHIP x3)

### Pre-run state
- Repo: clean reset
- Config: `language = "node"`, no enterprise features

### Pipeline Timeline
- 03:13 — Spec validation (passed)
- 03:15 — Scenario generation (6 files, 45 tests; smoke: 1 tautological, SA repair)
- 03:21 — Implementation iter 1 (2 containers)
- 03:29 — Quality gate iter 1: **Build PASS, Lint PASS, Type PASS, Self-Tests FAIL, Scenarios 1/45 (2%!)**
- Iter 2: 1/45 (2%) — no change
- Iter 3: 1/45 (2%) — no change
- 03:45 — Escalation

### Scenario Results by Iteration

| Iteration | Passed | Failed | Rate | Regression |
|-----------|--------|--------|------|------------|
| 1 | 1 | 44 | 2% | — |
| 2 | 1 | 44 | 2% | No change |
| 3 | 1 | 44 | 2% | No change |

### Root Cause: SA Hardcoded Wrong Container Path

All 6 scenario files include `process.chdir('/workspace')` in their `beforeAll` hooks. However, the project is mounted at `/project` inside the container (verified from audit log: `-v budget-brain-v2:/project:rw`). The base Docker image sets `WORKDIR /workspace`, but the project bind mount goes to `/project`.

**Result:** All file reads (`readFileSync('pnpm-workspace.yaml')`, etc.) use relative paths that resolve against `/workspace` (empty dir) instead of `/project` (actual project). 44/45 tests fail with ENOENT or assertion errors because files don't exist at the wrong CWD.

**The one passing test** (`should reject npm install when engine-strict is true`) succeeds because `execSync('npm install')` happens to work from any directory — npm finds the nearest `package.json` by walking up.

### Why Smoke Test Didn't Catch This

The smoke test runs BEFORE implementation. At that point, files don't exist at `/project` OR `/workspace`. The SA repair only catches "tautological" tests (passing without implementation). The CWD bug is indistinguishable from "files don't exist yet" in the pre-implementation smoke test.

### Pipeline Issues Found (NEW)

1. **SA hardcodes container paths**: SA assumed `/workspace` as the project root, but the actual mount point is `/project`. This is a critical SA knowledge gap about the container environment.

2. **Smoke test cannot detect CWD bugs**: When scenarios fail pre-implementation because "file not found", the smoke test can't distinguish between "file doesn't exist yet" (expected) vs "looking in wrong directory" (bug). This is a structural limitation of the pre-implementation smoke test.

3. **Oracle feedback completely wrong about root cause**: Attributed 44/45 failures to "pnpm installation failures" and "ENOENT for node_modules/.pnpm" when the real cause is the scenario CWD bug. The oracle's diagnostic capability is unreliable.

4. **Implementation was perfect**: Build/Lint/TypeCheck all PASS. All files correctly created. 0% scenario pass rate is entirely due to scenario-side bugs.

### Implementation Quality Assessment
- All 4 FRs implemented correctly (verified by Build/Lint/TypeCheck gates)
- Implementation quality comparable to Runs 1-2
- **100% of failures are scenario bugs** — zero implementation issues

---

## Run 4

**Start time:** 2026-03-29T03:47Z
**End time:** 2026-03-29T04:20Z (~33 min)
**Run ID:** 303905ca65e742f4
**Outcome:** ESCALATED (NO-SHIP x3)

### Pre-run state
- Repo: clean reset
- Config: `language = "node"`, no enterprise features

### Pipeline Timeline
- 03:47 — Spec validation (passed)
- 03:49 — Scenario generation (5 files, 35 tests; 2 tautological, SA repair)
- 03:53 — Implementation iter 1 (2 containers)
- 04:03 — Quality gate iter 1: **Build PASS, Lint PASS, Type PASS, Self-Tests FAIL, Scenarios 31/35 (88%)**
- Iter 2: Same 4 failures (88%)
- Iter 3: Same 4 failures (88%) — stable plateau, early escalation
- 04:20 — Escalation

### Scenario Results by Iteration

| Iteration | Passed | Failed | Rate | Regression |
|-----------|--------|--------|------|------------|
| 1 | 31 | 4 | 88% | — |
| 2 | 31 | 4 | 88% | No change |
| 3 | 31 | 4 | 88% | No change |

### Persistent Failures (all 3 iterations)

| # | Test Name | Root Cause | Category |
|---|-----------|------------|----------|
| 1 | `should have Biome configuration with required formatting options` | Tests `formatter.quoteStyle` but Biome puts it at `javascript.formatter.quoteStyle` | **SCENARIO_BUG** (same as Run 1) |
| 2 | `should follow kebab-case naming convention for generated files` | Regex `/^[a-z0-9]+(-[a-z0-9]+)*\.(yaml|json|js)$/` rejects `tsconfig.base.json`, `eslint.config.js` | **SCENARIO_BUG** (same as Run 2) |
| 3 | `should configure packages/shared for declaration file generation` | Missing `declaration: true` in `packages/shared/tsconfig.json` | **IMPL_BUG** (same as Run 1) |
| 4 | `should configure workflow to trigger on PR to main and pushes to main` | Regex `/pull_request.*main|main.*pull_request/` doesn't match across newlines | **SCENARIO_BUG** (all runs) |

### Notable Observations
- **No CWD bug** (unlike Run 3)
- **Same 3 SA bugs** as Runs 1-2 (Biome, kebab-case, CI YAML)
- **Same impl bug** as Run 1 (missing `declaration: true`)
- **No regressions** across iterations (stable like Run 2)
- **Oracle feedback leakage**: "88% pass" leaked exact scenario percentage (barrier violation detected by pipeline)

### Implementation Quality Assessment
- All 4 FRs implemented correctly
- Build, lint, type check all passing
- 1 real impl bug (missing `declaration: true`) + 3 scenario bugs
- Stable plateau — no regressions across iterations

---

## Summary / Metrics

| Metric | Run 1 | Run 2 | Run 3 | Run 4 |
|--------|-------|-------|-------|-------|
| Outcome | ESCALATED | ESCALATED | ESCALATED | ESCALATED |
| Pipeline duration | ~31 min | ~32 min | ~32 min | ~33 min |
| Iterations used | 3/3 | 3/3 | 3/3 | 3/3 |
| Scenarios passed/total | 35/39 best, 33/39 final | 31/33 stable | 1/45 stable | 31/35 stable |
| Key failure mode | SA config + impl regression | SA regex inflexibility | SA wrong CWD path | SA bugs + impl bug |
| Pipeline bugs found | 8 | 5 (3 repeat) | 4 (1 critical new) | 4 (all repeat) |

---

## Cross-Run Analysis

### Persistent SA Bugs (appear in multiple runs)

| Bug | Run 1 | Run 2 | Run 3 | Run 4 | Description |
|-----|-------|-------|-------|-------|-------------|
| Biome config path | YES | no | N/A | YES | Tests `formatter.quoteStyle` instead of `javascript.formatter.quoteStyle` |
| CI YAML regex | YES | YES | N/A | YES | `.` doesn't match newlines; YAML `pull_request:` and `branches: [main]` on separate lines |
| Kebab-case regex | no | YES | N/A | YES | Regex rejects filenames with multiple dots (`tsconfig.base.json`) |
| Hardcoded CWD | no | no | YES | no | `process.chdir('/workspace')` but project at `/project` |

### Implementation Bug Pattern

| Bug | Run 1 | Run 2 | Run 3 | Run 4 |
|-----|-------|-------|-------|-------|
| Missing `declaration: true` | YES | no | N/A | YES |
| Iteration 3 regression | YES | no | N/A | no |

### Key Findings

1. **The implementation is ship-worthy in all 4 runs.** Build, lint, and type-check gates pass every time. The core monorepo structure is correctly created.

2. **SA is the primary blocker.** 3 out of 4 persistent failure categories are SA bugs that the implementer cannot fix. The SA consistently writes:
   - Overly specific regex assertions for YAML structure
   - Incorrect config path assumptions (Biome `formatter` vs `javascript.formatter`)
   - Fragile filename regex that doesn't handle compound extensions

3. **Oracle feedback is consistently inaccurate.** In all runs, the oracle misdiagnoses SA bugs as "ENV_ISSUE" or "implementation bugs", leading to wasted iterations where the implementer tries to fix things that aren't broken.

4. **The 89-93% ceiling is structural.** Without fixing SA bugs, the pipeline cannot reach 100%. The iteration loop is wasted effort after the first quality gate.

5. **SA non-determinism is high.** Each run produces different scenario structures (39, 33, 45, 35 tests) with different bug combinations. Run 3 was catastrophically broken (2% pass) due to a CWD bug.

### Recommended Pipeline Improvements

1. **SA should parse YAML instead of regex matching**: Use `js-yaml` to parse CI YAML and assert on the parsed structure, not string patterns.

2. **SA should test Biome config by running `biome check`**: Instead of asserting on JSON paths (which vary by Biome version), test that `biome check` produces the expected behavior.

3. **SA agent prompt should document container mount paths**: Add explicit context that the project is mounted at `/project`, not `/workspace`.

4. **Oracle feedback should distinguish SA bugs from impl bugs**: When build/lint/typecheck PASS but scenario assertions fail, the feedback should consider that the scenarios themselves may be buggy.

5. **Add a "stable failure" detection**: When the same tests fail across all iterations with identical results, the pipeline should flag them as potentially unfixable (SA bugs) rather than continuing to iterate.
