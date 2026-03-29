# Plan: Path to First Ship

**Date:** 2026-03-29
**Status:** IN PROGRESS (Phases 1, 2, 4 complete; Phases 3, 5 pending)
**Depends on:** [Tighter Specs and Scenario Quality](tighter-specs-and-scenario-quality.md) Phases 1-3 (COMPLETE)
**Absorbs:** Phase 4 of Tighter Specs plan (SA Output Validation), moved here as Phase 2

---

## Problem Statement

13+ pipeline runs across 2 projects (budget-brain, budget-brain-v2). Zero specs shipped. The implementation is consistently correct — Build/Lint/TypeCheck pass in virtually every run. The pipeline is blocked by three interacting failures:

1. **SA generates buggy tests** (wrong config paths, fragile regex, state pollution, incorrect API usage). Quality ranges from 2% to 97% pass rate run-to-run on the same spec.
2. **Oracle cannot distinguish SA bugs from impl bugs.** It classifies SCENARIO_BUGs as IMPL_BUG in every run, directing the implementer to "fix" correct code.
3. **The iteration loop wastes all 3 attempts** on unfixable scenario bugs. Stable plateau across iterations is the norm.

### Evidence (from pipeline run reports)

| Run Set | Best Pass Rate | Impl Bugs | Scenario Bugs | Shipped? |
|---------|---------------|-----------|---------------|----------|
| budget-brain (5 runs) | 97% (Run 3) | 0-4 | 1-8 | No |
| budget-brain-v2 pre-Phase 3 (4 runs) | 93% (Run 2) | 0-1 | 2-6 | No |
| budget-brain-v2 post-Phase 3 (4 runs) | 94% (Run 4) | 0 | 4-16 | No |

In every run where build/lint/type pass, the implementation is ship-worthy. The blocker is scenario quality and Oracle decision-making.

### Design Principles

1. **Fix the decision first, then fix the input.** If the Oracle can't ship a 94% pass rate with 0 impl bugs, fix the Oracle before spending more effort on SA quality. (Phases 1-2)
2. **Mechanical rules over prompt engineering.** The Oracle has been re-prompted 15+ times across runs. Prompt changes don't stick. Mechanical pre-processing of data before the Oracle sees it is more reliable. (Phase 1)
3. **Validate SA output before burning iteration budget.** Bad scenarios should be caught and regenerated before the implementer ever starts. (Phase 2)
4. **Reduce assertion brittleness structurally, not per-pattern.** Free Variables help for known patterns but new brittle patterns appear each run. Behavioral evaluation eliminates the entire class of "wrong regex" bugs. (Phase 3)

---

## Phase 1: Mechanical Stable-Failure Detection + Oracle SHIP Override

**Status:** COMPLETE
**Goal:** When build/lint/type all pass, remaining failures are stable across 2+ iterations, and pass rate >= threshold, ship with documented gaps. This directly enables shipping on implementations that are already correct.
**Estimated effort:** 1-2 days

### Why This Is First

This is the lowest-effort, highest-impact change. Budget-brain-v2 Run 4 hit 94% with 0 impl bugs. If the pipeline had this logic, it would have shipped. We don't need to fix SA quality to ship — we need the Oracle to recognize that stable failures on passing implementations are scenario bugs, not blockers.

### Changes

#### 1a. Stable-failure classifier — mechanical pre-processing

**File:** `darkfactory/pipeline/iteration.py`
**Location:** Before `_step_oracle()`, after quality gate results are available

When iteration >= 2 and the same tests fail with identical names across consecutive iterations:

```python
def classify_stable_failures(
    current_results: list[dict],     # per-test results from quality gate
    previous_results: list[dict],    # per-test results from previous iteration
    gate_passed: bool,               # build/lint/type all passed
) -> StableFailureAnalysis:
    """
    Compare test results across iterations. Tests that fail identically
    in both iterations are 'stable failures' — the implementer cannot fix them.

    Returns analysis with:
    - stable_failure_names: set[str] — tests that failed in both iterations
    - stable_failure_ratio: float — stable / total_failed
    - recommended_classifications: dict[str, str] — test_name → "BRITTLE"
    - gate_health: bool — True if build/lint/type passed
    """
```

The analysis is injected into the Oracle prompt as structured context:

```markdown
## Stable Failure Analysis

Build/Lint/TypeCheck: ALL PASS
Iteration 1 failures: 4 tests
Iteration 2 failures: 4 tests (identical set)
Stable failure ratio: 100% (4/4 failures are identical across iterations)

Mechanical recommendation: All 4 failures are stable across iterations with all
hard gates passing. These are likely BRITTLE or SCENARIO_CRASH, not IMPL_BUG.
The implementer has attempted to fix them and they did not change.
```

**Complexity:** M — logic is straightforward, integration into Oracle prompt requires care

#### 1b. Oracle SHIP override for stable-failure + passing-gates

**File:** `darkfactory/pipeline/iteration.py`
**Function:** `_step_oracle()` and `run_iteration_loop()`

Add a mechanical SHIP path that bypasses Oracle judgment when conditions are met:

```python
# After quality gate, before Oracle invocation (iteration >= 2):
if (
    iter_state.gate_exit_code == 0          # Build/lint/type passed
    and stable_analysis.gate_health         # Redundant safety check
    and stable_analysis.stable_failure_ratio >= 0.8  # >=80% of failures are stable
    and iter_state.pass_rate >= config.min_ship_pass_rate  # Meets configured floor
    and all_hard_gates_passed                # No build/lint/type failures
):
    # SHIP with documented gaps
    ship_with_gaps = True
    # Still invoke Oracle for the record, but override NO-SHIP if Oracle disagrees
```

**Important:** This is NOT "lower the bar." This is "the bar is met, the Oracle is wrong." The pass rate threshold (`min_ship_pass_rate`, default 80) is configurable. The mechanical override only fires when:
- Hard gates pass (implementation is correct by build/lint/type standards)
- Failures are demonstrably unfixable (stable across iterations)
- Pass rate meets the configured floor

The Oracle is still invoked and its feedback recorded. But if it says NO-SHIP and the mechanical conditions are met, we override to SHIP and document the gaps.

**Complexity:** M

#### 1c. Defect registry for shipped-with-gaps

**File:** `darkfactory/pipeline/orchestrator.py`
**Function:** `_handle_ship()`

When `ship_with_gaps` is True, write a defect registry entry:

**File created:** `.dark-factory/reports/{spec}-defect-registry.md`

```markdown
# Defect Registry: {spec-name}

**Run ID:** {run_id}
**Ship decision:** MECHANICAL_OVERRIDE (Oracle said NO-SHIP, stable-failure override applied)
**Pass rate:** {pass_rate}%
**Stable failures:** {count}

## Documented Gaps

| # | Test Name | Classification | Iterations Stable |
|---|-----------|---------------|-------------------|
| 1 | {test} | BRITTLE | 2 |
| 2 | {test} | BRITTLE | 2 |

## Gate Results
- Build: PASS
- Lint: PASS
- TypeCheck: PASS
- Self-Tests: {status}
- Blind Scenarios: {pass}/{total} ({rate}%)
```

This matches Yassine's original design: "If it still fails after 3 attempts, it escalates or ships with documented gaps in the defect registry."

**Complexity:** S

#### 1d. Config — new fields

**File:** `darkfactory/models/config.py`

Add:
- `stable_failure_ship_override: bool = True` — enable/disable the mechanical SHIP override
- `stable_failure_min_ratio: float = 0.8` — minimum ratio of stable failures to trigger override
- `stable_failure_min_iterations: int = 2` — minimum iterations with identical failures

**Complexity:** S

### Phase 1 Tests

- Unit test: `classify_stable_failures()` with matching failures → high ratio, mismatching → low ratio, no previous results → skip
- Unit test: SHIP override fires when conditions met, does NOT fire when pass rate below floor, does NOT fire when hard gates fail
- Unit test: Defect registry markdown generation
- Integration: Mock pipeline with 2 iterations of identical failures + passing hard gates → verify SHIP decision

### Phase 1 Verification

Re-run budget-brain-v2 monorepo-tooling spec. If pass rate is ≥80% (as in Runs 1,2,4: 87-94%) with stable failures, the pipeline should SHIP on iteration 2 instead of escalating after 3.

---

## Phase 2: SA Output Validation + Retry Loop

**Status:** COMPLETE (moved from Tighter Specs plan Phase 4)
**Goal:** Catch bad SA scenarios before the implementer starts. Regenerate on failure.
**Estimated effort:** 2-3 days
**Depends on:** None (independent of Phase 1, can be implemented in parallel)

### Why This Is Second

Phase 1 lets us ship despite bad scenarios. Phase 2 prevents bad scenarios from reaching the pipeline. Together they close the gap from both ends.

### Changes

#### 2a. Scenario validator — new structural checks

**File:** `darkfactory/scenarios/validator.py`

Add checks 10-16 (apply to Tier 2 files only, skip `00-tier1-*` prefix files):

| # | Check | Type | Catches | Evidence from Runs |
|---|-------|------|---------|-------------------|
| 10 | Assertion count upper bound | Hard fail | Overly granular files. Max 15 assertions per file (configurable via `scenario_max_assertions_per_file`). | Run 3 (bb-v2): 45 tests in 6 files, one catastrophic |
| 11 | No source file reads | Hard fail | `readFileSync`/`open()` on `.ts`, `.tsx`, `.js`, `.jsx`, `.py`, `.go`, `.rs` files. Config files (`.json`, `.yaml`, `.toml`) are allowed. | SA reads `packages/shared/src/index.ts` in adversarial tests |
| 12 | No indexOf ordering | Hard fail | `indexOf(X) < indexOf(Y)` or `indexOf(X) > indexOf(Y)` patterns that test unconstrained ordering. | budget-brain Runs 2-4: CI step ordering assertions |
| 13 | Intent count alignment | Hard fail | Total Tier 2 test count within 2x of declared scenario intents. Requires spec path (skip if no intent section). | SA generates 72 tests from 30 intents (budget-brain Run 4) |
| 14 | Regex multiline flag | Warning | `toMatch(/pattern/)` on content from `readFileSync` without `/m` or `/s` flag. Multi-line file content needs multiline regex. | CI YAML regex failures in ALL runs |
| 15 | No project file modification | Hard fail | `writeFileSync` / `fs.writeFile` targeting project paths (not `/tmp/` or `mkdtemp`). Adversarial tests that modify project files corrupt state. | bb-v2 Runs 1-2: state pollution from adversarial tests |
| 16 | No hardcoded container paths | Hard fail | `process.chdir('/workspace')` or `process.chdir('/project')` — scenarios should use relative paths or `process.cwd()`. | bb-v2 Run 3: 2% pass rate from wrong CWD |

Update `validate_scenarios()` signature:
```python
def validate_scenarios(
    spec_name: str,
    language: str,
    project_dir: Path,
    *,
    min_files: int = _MIN_FILES,
    min_assertions: int = _MIN_ASSERTIONS_PER_FILE,
    max_assertions: int = 15,               # NEW
    spec_path: Path | None = None,          # NEW — for intent count alignment
    tier1_prefix: str = "00-tier1-",        # NEW — skip Tier 1 files
) -> ValidationResult:
```

**Complexity:** M

#### 2b. Config — new fields

**File:** `darkfactory/models/config.py`

Add:
- `sa_max_retries: int = 2` — max SA regeneration attempts on validation failure
- `scenario_max_assertions_per_file: int = 15` — upper bound for check 10

**Complexity:** S

#### 2c. Orchestrator — SA retry loop

**File:** `darkfactory/pipeline/orchestrator.py`
**Location:** After `_step_scenario_generation()`, before format compatibility check (Step 2.5)

```python
# SA validation + retry loop (does NOT consume iteration budget)
for sa_attempt in range(config.sa_max_retries + 1):
    validation = validate_scenarios(
        spec_name=spec_run.spec_name,
        language=config.language,
        project_dir=project_dir,
        max_assertions=config.scenario_max_assertions_per_file,
        spec_path=spec_file,
        tier1_prefix="00-tier1-",
    )

    if validation.passed:
        break

    if sa_attempt < config.sa_max_retries:
        log(f"SA validation failed (attempt {sa_attempt + 1}), regenerating Tier 2...")
        # Delete only Tier 2 files (preserve 00-tier1-* files)
        _clear_tier2_scenarios(scenario_dir, tier1_prefix="00-tier1-")
        # Re-invoke SA with validation report prepended to prompt
        _step_scenario_generation(
            ...,
            validation_report=validation.report,
        )
    else:
        log(f"SA validation failed after {config.sa_max_retries + 1} attempts")
        # Proceed with best-effort scenarios (validation warnings only)
        # Hard failures block pipeline — cannot proceed with structurally broken scenarios
        if not validation.passed:
            raise HardAbort(f"SA scenarios failed validation after {config.sa_max_retries + 1} attempts")
```

**Key design decisions:**
- Tier 1 files are NEVER deleted during retry — they're deterministic and correct
- The validation report is prepended to the SA prompt on retry, giving the SA specific feedback about what failed
- Hard validation failures (checks 10-13, 15-16) block the pipeline. Warnings (check 14) are logged but don't block.
- This is a PRE-implementation retry loop. The implementer never sees failed SA output. It does NOT consume iteration budget.

**Complexity:** M

#### 2d. SA prompt — validation awareness

**File:** `agents/scenario-architect.md`

Add to Critical Rules section:

```markdown
9. **Post-generation validation will check your output mechanically.** The pipeline runs
   structural checks on your scenarios before the implementer starts. If checks fail,
   you will be re-invoked with a validation report. Checks include:
   - Max 15 assertions per file
   - No readFileSync/open() on source code files (.ts, .tsx, .js, .py, etc.)
   - No indexOf ordering comparisons
   - No writeFileSync to project files (use temp directories)
   - No hardcoded container paths (use relative paths)
   - Regex on file content must use /m or /s flag for multiline matching
   - Total test count within 2x of declared scenario intents
```

**Complexity:** S

### Phase 2 Tests

- Unit test per check (10-16): construct files that trigger each, verify detection
- Check 10: file with 1 assertion (pass), file with 20 (fail)
- Check 11: `readFileSync('src/server.ts')` (fail), `readFileSync('package.json')` (pass)
- Check 12: `content.indexOf('a') < content.indexOf('b')` (fail)
- Check 13: 20 intents in spec, 50 tests (pass), 100 tests (fail)
- Check 14: `toMatch(/pattern/)` on `readFileSync` result without `/m` (warning)
- Check 15: `writeFileSync('packages/shared/src/index.ts', ...)` (fail), `writeFileSync(tmpFile, ...)` (pass)
- Check 16: `process.chdir('/workspace')` (fail), `process.chdir(process.cwd())` (pass)
- Integration: inject bad scenario file, run validation, verify failure detected
- Integration: mock SA retry, verify Tier 1 files preserved, Tier 2 regenerated

### Phase 2 Verification

Run pipeline on budget-brain-v2 monorepo-tooling spec. Check logs for:
1. SA validation pass/fail on first attempt
2. If validation fails, verify retry preserves Tier 1 and regenerates Tier 2
3. Second-attempt scenarios should not contain the flagged patterns
4. Overall: fewer scenario bugs reaching the iteration loop

---

## Phase 3: Per-Scenario Probabilistic Threshold (2/3)

**Status:** NOT STARTED
**Goal:** Run each scenario 3 times, require 2/3 passes. Absorbs flakiness and non-determinism.
**Estimated effort:** 2-3 days
**Depends on:** None (independent, but verify Phase 1+2 first)

### Why This Is Third

The original Yassine pipeline ran each holdout 3x with 2/3 threshold. We don't do this. Adding it absorbs test flakiness (timing, environment state, ordering) without lowering quality standards. It also matches the original design intent.

### Changes

#### 3a. Quality gate — multi-exec per scenario

**File:** `scripts/quality-gate.sh`
**Location:** Blind Scenarios gate (Gate 5)

For each scenario runner invocation, run 3 times and accumulate per-test results:

```bash
_df_run_scenario_probabilistic() {
    local runner="$1"
    local scenario_dir="$2"
    local runs="${SCENARIO_RUNS:-3}"
    local threshold="${SCENARIO_THRESHOLD:-2}"

    for run in $(seq 1 "$runs"); do
        # Run scenarios, capture per-test JSON
        "$runner" "$scenario_dir" > "results-run-${run}.json"
    done

    # Merge: test passes if it passed in >= threshold runs
    # Output: merged per-test JSON with pass/fail based on 2/3 threshold
}
```

**Important:** This is opt-in via config. Default `scenario_runs=1` preserves current behavior. Set `scenario_runs=3` and `scenario_threshold=2` for probabilistic mode.

**Complexity:** M — the runner invocations are straightforward, the merge logic needs care

#### 3b. Per-test results — add run-level detail

**File:** `scripts/quality-gate.sh`, function `_write_per_test_results`

Extend per-test results JSON:

```json
{
    "name": "test name",
    "status": "passed",
    "runs": [
        {"run": 1, "status": "passed"},
        {"run": 2, "status": "failed", "error": "..."},
        {"run": 3, "status": "passed"}
    ],
    "pass_count": 2,
    "run_count": 3,
    "probabilistic": true
}
```

**Complexity:** S

#### 3c. Config — new fields

**File:** `darkfactory/models/config.py`

Add:
- `scenario_runs: int = 1` — number of times to run each scenario (1 = current behavior, 3 = probabilistic)
- `scenario_threshold: int = 1` — minimum passes required (for `scenario_runs=3`, set to 2)

Validation: `scenario_threshold <= scenario_runs`

**Complexity:** S

#### 3d. Satisfaction scoring — probabilistic awareness

**File:** `darkfactory/scenarios/satisfaction.py`

Update `compute_satisfaction()` to handle probabilistic results:
- A test that passes 2/3 is scored as "passed" (not partial credit)
- A test that passes 1/3 is scored as "failed"
- Crash detection: if a test crashes in any run, classify as crash regardless of other runs

**Complexity:** S

### Phase 3 Tests

- Unit test: 3 runs, test passes 2/3 → final status "passed"
- Unit test: 3 runs, test passes 1/3 → final status "failed"
- Unit test: 3 runs, test crashes in run 2 but passes runs 1+3 → classified as crash (conservative)
- Unit test: `scenario_runs=1` → no change in behavior
- Integration: run quality gate with `scenario_runs=3` on a known-flaky scenario set

### Phase 3 Verification

Run pipeline with `scenario_runs=3, scenario_threshold=2` on budget-brain-v2 spec. Compare pass rate to single-run mode. Expect: slight improvement in pass rate as flaky tests are absorbed, no change in pass rate for deterministic failures.

---

## Phase 4: Tool Documentation Injection for SA

**Status:** COMPLETE
**Goal:** Give the SA concrete knowledge about tools/platforms referenced in the spec, eliminating the most common domain-knowledge failures.
**Estimated effort:** 1-2 days
**Depends on:** None (independent)

### Why This Is Fourth

This addresses the root cause of SA domain-knowledge bugs (Biome config paths, Railway JSON schema, ESLint --print-config usage) without violating the information barrier. It's implementation knowledge of the tools, not the project.

### Changes

#### 4a. Tool documentation registry

**File:** `darkfactory/scenarios/tool_docs.py` (NEW)

```python
# Registry of brief, assertion-relevant documentation for common tools
TOOL_DOCS: dict[str, str] = {
    "biome": """
Biome Configuration (biome.json):
- Formatting options are NESTED under language-specific keys:
  - `javascript.formatter.quoteStyle` (not `formatter.quoteStyle`)
  - `javascript.formatter.trailingCommas` (not `formatter.trailingCommas`)
  - `javascript.formatter.indentStyle` and `javascript.formatter.indentWidth`
- Top-level `formatter` only has: `enabled`, `indentStyle`, `indentWidth`, `lineWidth`
- CLI: `biome check` runs both linter and formatter
- CLI: `biome format --write` auto-fixes formatting
""",
    "eslint": """
ESLint (Flat Config - eslint.config.js):
- `--print-config` requires a FILE path, not a directory (e.g., `--print-config src/App.tsx`)
- Flat config exports an array of config objects, not a single object
- Plugin names in flat config don't use `eslint-plugin-` prefix
""",
    "railway": """
Railway Configuration (railway.json):
- Uses NESTED structure: `build.buildCommand`, `deploy.startCommand`
- NOT flat: `buildCommand` at top level is invalid
- Alternative: railway.toml with `[build]` and `[deploy]` sections
""",
    "pnpm": """
pnpm Workspace:
- Workspace config is in `pnpm-workspace.yaml`, NOT `package.json` `workspaces` field
- `pnpm ls` output format varies by version; parse as structured data, not regex
- `pnpm --filter` accepts both package names (@scope/pkg) and directory paths (./apps/web)
- `pnpm exec` PATH is non-deterministic in containers; prefer direct binary invocation
""",
    "vitest": """
Vitest in Monorepos:
- Dynamic `import()` of workspace packages may not resolve in test context
- Prefer `execSync('node -e "require(...)"')` for testing module resolution
- `process.cwd()` in test files is the project root, not the scenario directory
""",
    "yaml": """
YAML Assertions:
- YAML has multiple valid representations for the same data:
  - `branches: [main]` and `branches:\\n  - main` are identical
  - Quoted and unquoted strings are identical: `name: "foo"` = `name: foo`
- Parse YAML before asserting on structure. Do NOT use regex on YAML files.
- If you must use regex, use `/m` flag (multiline) and match key-value pairs, not structure.
""",
}
```

**Complexity:** S

#### 4b. Orchestrator — inject tool docs into SA context

**File:** `darkfactory/pipeline/orchestrator.py`
**Function:** `_step_scenario_generation()`

After reading the spec, detect tool/platform references:

```python
def _detect_tools_in_spec(spec_text: str) -> list[str]:
    """Detect tool/platform names mentioned in spec text."""
    known_tools = list(TOOL_DOCS.keys())
    detected = []
    for tool in known_tools:
        if re.search(rf'\b{tool}\b', spec_text, re.IGNORECASE):
            detected.append(tool)
    return detected

# In _step_scenario_generation():
detected_tools = _detect_tools_in_spec(spec_text)
tool_context = "\n\n".join(
    f"### {tool.title()} Reference\n{TOOL_DOCS[tool]}"
    for tool in detected_tools
)
# Append tool_context to SA execution context
```

This doesn't violate the information barrier — it's tool documentation, not implementation code. The SA already receives execution context (language, test framework, server commands). Tool docs extend this with assertion-relevant knowledge.

**Complexity:** S

### Phase 4 Tests

- Unit test: `_detect_tools_in_spec()` finds "biome" in spec mentioning Biome, doesn't find "railway" in spec without Railway mention
- Unit test: Tool docs are injected into SA prompt when detected
- Verify: TOOL_DOCS entries are factually accurate (manual review)

### Phase 4 Verification

Run pipeline on budget-brain-v2 spec (mentions Biome, ESLint, pnpm). Verify:
1. SA receives Biome, ESLint, pnpm, YAML tool docs
2. SA uses `javascript.formatter.quoteStyle` (not `formatter.quoteStyle`)
3. SA parses YAML instead of regex-matching CI workflow files

---

## Phase 5: Behavioral Evaluation for Tier 2 Scenarios

**Status:** NOT STARTED — DESIGN PHASE
**Goal:** Replace hard vitest assertions in adversarial scenarios with behavioral descriptions + LLM-as-judge evaluation. Eliminates the entire class of "SA wrote wrong regex/config path" failures.
**Estimated effort:** 5-7 days
**Depends on:** Phases 1-2 (ship first, then rethink evaluation)

### Why This Is Last

This is the correct long-term architecture, but it's also the biggest change. Phases 1-4 should get us shipping specs. Phase 5 makes the pipeline structurally robust against SA assertion brittleness. We should only pursue this after validating that the simpler fixes work (or don't work enough).

### Design Concept

Currently, Tier 2 (SA-generated) scenarios look like:

```typescript
// SA generates executable test code with hard assertions
it('CI workflow triggers on PR to main', () => {
    const content = readFileSync('.github/workflows/ci.yml', 'utf-8');
    expect(content).toMatch(/pull_request.*main/s);  // Breaks on valid YAML variations
});
```

The behavioral model replaces this with:

```markdown
## Scenario: CI workflow triggers on PR to main
### Check
Read `.github/workflows/ci.yml`. Parse as YAML. Verify that the `pull_request`
trigger includes `main` in its `branches` list.
### Expected
The CI workflow is configured to run on pull requests targeting the main branch.
```

An evaluator agent (we already have one defined at `agents/evaluator.md`) reads the scenario description, inspects the built project, and uses LLM judgment to determine pass/fail. This is how StrongDM and OctopusGarden work.

### Advantages

- **Eliminates regex brittleness:** The evaluator parses files and understands YAML semantics, not string patterns
- **Eliminates config path errors:** The evaluator reads actual Biome config and checks if quote style is configured, not whether a specific JSON path exists
- **Tolerates valid implementation variations:** `branches: [main]` vs `branches:\n  - main` both satisfy "triggers on PR to main"
- **SA focuses on WHAT to test, not HOW to assert:** The SA describes behaviors, which is within its competence. The evaluator handles the assertion mechanics.

### Risks

- **LLM-as-judge adds cost:** Each scenario evaluation is an LLM call. For 50 scenarios × 3 iterations, that's 150 additional calls per run.
- **LLM-as-judge is non-deterministic:** The evaluator might disagree with itself across runs. Mitigate with structured evaluation rubrics and temperature=0.
- **The evaluator has access to implementation code:** This is intentional and matches the original Yassine "Validator" design — the validator sees everything but outputs only sanitized results.
- **Mixed evaluation model:** Tier 1 stays as deterministic vitest tests, Tier 2 shifts to behavioral evaluation. The quality gate needs to handle both.

### Implementation Sketch (details TBD after Phases 1-4 validated)

1. **SA generates `.eval.md` files** (behavioral descriptions) instead of `.test.ts` for Tier 2
2. **Quality gate detects `.eval.md`** format and invokes Evaluator agent instead of vitest
3. **Evaluator agent reads project files** and produces per-scenario pass/fail with rationale
4. **Results feed into satisfaction scoring** same as vitest results
5. **Tier 1 contracts remain as deterministic vitest** — no change to compiled contract tests

### Phase 5 Decision Gate

Before implementing Phase 5, run 3 pipeline attempts with Phases 1-4 in place. Measure:
- Ship rate: if ≥1/3 ships, Phase 5 is optimization, not necessity
- SA validation pass rate: if first-attempt validation passes >70%, SA quality is sufficient for vitest
- Stable failure ratio: if <20% of failures are stable (most are fixable), the iteration loop works

If Phases 1-4 achieve consistent shipping, Phase 5 becomes a nice-to-have for resilience. If we're still stuck, Phase 5 is the structural fix.

---

## Cross-Phase Dependencies

```
Phase 1 (SHIP override)     Phase 2 (SA validation)     Phase 4 (tool docs)
     │                            │                           │
     │ (independent)              │ (independent)             │ (independent)
     │                            │                           │
     └──── Phases 1+2+4 can be implemented in parallel ──────┘
                                  │
                           Phase 3 (probabilistic)
                                  │
                         ─── MEASUREMENT GATE ───
                                  │
                    Phase 5 (behavioral evaluation)
                         (only if needed)
```

Phases 1, 2, and 4 are fully independent and can be developed in parallel. Phase 3 should wait for Phase 1+2 results to see if probabilistic testing is still needed. Phase 5 is contingent on measurement results after Phases 1-4.

---

## Risk Areas

| Risk | Mitigation |
|------|------------|
| Mechanical SHIP override ships bad code | Override requires ALL hard gates pass + configurable pass rate floor (default 80%). Can be disabled via `stable_failure_ship_override: false`. |
| SA retry loop burns API budget | Cap at 2 retries. Log cost. Does NOT count against iteration budget. |
| Tool docs become stale | Keep docs minimal (assertion-relevant only, not full API docs). Review annually. Mark with version numbers. |
| Probabilistic mode masks real flakiness | Log all 3 run results. Tests that fail 1/3 are flagged as "flaky" in report (not hidden). |
| Behavioral evaluation adds latency | Tier 2 eval is parallelizable per scenario. Tier 1 stays fast (vitest). Measure added time before committing. |
| Phase 5 requires significant architecture change | Decision gate after Phases 1-4. Only implement if simpler fixes are insufficient. |

## Success Metrics

| Metric | Current Baseline | Phase 1+2 Target | Phase 1-4 Target | How to Measure |
|--------|-----------------|------------------|------------------|----------------|
| Specs shipped | 0 / 13+ runs | ≥1 / 3 runs | ≥2 / 3 runs | Pipeline telemetry |
| SA validation first-attempt pass | Unknown | >50% | >70% | Pipeline logs |
| Stable failure ratio at escalation | 80-100% | <30% (most shipped) | <20% | Pipeline reports |
| Runs to first SHIP | >13 (never shipped) | ≤3 | ≤2 | Pipeline telemetry |
| Oracle BRITTLE classification accuracy | ~0% (all IMPL_BUG) | >60% (mechanical) | >80% | Classification audit |
| Pass rate variance (same spec, 3 runs) | σ ≈ 17 (37-72 tests) | σ < 10 | σ < 5 | Run same spec 3x |

## Implementation Order

1. **Phase 1 + Phase 2 + Phase 4** (in parallel, 2-3 days): Ship override + SA validation + tool docs. Run 3 pipeline attempts.
2. **Phase 3** (2-3 days): Probabilistic threshold. Only if flakiness is still a problem after Phase 1+2.
3. **Measurement gate:** 3 pipeline runs with all changes. Evaluate success metrics.
4. **Phase 5** (5-7 days): Behavioral evaluation. Only if measurement gate shows vitest assertions are still the bottleneck.

### Progress Log

| Date | Phase | Commits | Notes |
|------|-------|---------|-------|
| — | — | — | Plan created |
| 2026-03-29 | 1, 2, 4 | a7789f9 | Implemented in parallel. Phase 1: `should_ship_with_gaps()` with defect registry. Phase 2: validator checks 10-16 + SA retry loop. Phase 4: TOOL_DOCS registry + detection. Linus review caught critical ordering bug (SHIP override placed after early escalation — PLATEAU would preempt it). Fixed: override now runs before escalation. "yaml" trigger narrowed to assertion-context patterns. 41 new tests, 278 related passing. |
