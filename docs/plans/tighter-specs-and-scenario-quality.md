# Plan: Tighter Specs and Scenario Quality

**Date:** 2026-03-28
**Status:** COMPLETE (Phases 1-3) — Phase 4 moved to [Path to First Ship](path-to-first-ship.md)
**Parent:** [Gap Analysis](spec-scenario-improvements/gap-analysis.md)
**Supersedes:** [Free Variables Pipeline](spec-scenario-improvements/implementation-plan.md) (Priority 1 complete; this plan addresses the next layer)
**Reviewed by:** Liotta, Neo (pre-plan review of problem statement)

---

## Problem Statement

Five pipeline runs on a single spec. Zero ships. Best result: 97% (Run 3). The SA generates wildly different scenarios each run (37 tests → 72 tests) because specs give it too much surface area and interpretive freedom. 15+ commits of SA prompt engineering have not solved this — the problem is structural.

### Root Causes (ordered by impact)

1. **Specs are too broad.** F-1 has 9 FRs spanning pnpm monorepo, TypeScript, 3 packages, CI, Railway, Vercel, env vars. The SA's decision space is enormous.
2. **The SA does two jobs with the same interpretive freedom.** Contract verification (did the code do what the spec says?) and adversarial exploration (can I break it?) are conflated. Contract verification should be deterministic.
3. **No post-generation quality gate on SA output.** Syntactically valid but semantically brittle scenarios enter the pipeline unchecked.
4. **Scenario count and intent are undeclared.** The spec doesn't say how many scenarios to expect or what they should cover. The SA invents this from scratch each run.

### Key Insights from Liotta + Neo Review

- **Liotta:** "The SA is not broken; it is being asked to solve an under-constrained problem. Separate deterministic verification from generative testing."
- **Neo:** "Spec precision eliminates semantic non-determinism (what to assert) but not stylistic non-determinism (how to assert). For the latter, you need SA output validation or assertion templates."
- **Both:** The 10x move is separating Tier 1 (machine-readable contracts, compiled deterministically) from Tier 2 (SA-generated adversarial scenarios where creativity adds value).

---

## Phase 1: Plan Decomposition for Tighter Specs ✓

**Status:** COMPLETE — committed as `abfaff8` (1a) and `1eb2f31` (1b-1f)
**Goal:** Enforce smaller specs by capping FRs at 3-5 per spec and adding pipeline suitability signals.

### Changes

#### 1a. `/df-write-plan` skill — FR cap and auto-decomposition
**File:** `.claude/skills/df-write-plan/SKILL.md`
**Section:** Feature Decomposition (Step 3) and Validation (Step 4)

Add:
- Rule: "Each feature must have at most 5 FRs. If a feature grows beyond 5 FRs during the interview, decompose it into sub-features (F-1a, F-1b, F-1c) with dependency chains. Each sub-feature becomes its own spec."
- Pipeline Suitability estimate: "For each feature, estimate scenario count (5-15 per FR × FR count). Flag features that would produce >50 scenarios."
- Validation: "Warn if any feature has >5 estimated FRs. Suggest decomposition."

**Complexity:** S

#### 1b. `/df-write-spec` skill — atomic ACs and FR count guidance
**File:** `.claude/skills/df-write-spec/SKILL.md`
**Section:** Interview Process, Completeness Checklist

Add:
- "Push for atomic acceptance criteria. Each AC must test exactly one observable behavior. If an AC tests multiple conditions, split it."
- "Target: GIVEN [one precondition] WHEN [one action] THEN [one observable output with exact expected value]."
- "FR count check: if >5, suggest splitting the spec."

**Complexity:** S

#### 1c. `/df-validate-spec` skill — FR count as Critical Issue
**File:** `.claude/skills/df-validate-spec/SKILL.md`
**Section:** Evaluation criteria

Add: "Check FR count. If >5, add a Critical Issue: 'Spec has N FRs (max recommended: 5). Decompose into sub-specs to reduce scenario explosion.'"

**Complexity:** S

#### 1d. Spec Validator agent — scope check
**File:** `agents/spec-validator.md`
**Section:** Completeness Checklist, Validation Rules

Add:
- New validation rule: "Scope Check — Count FR-N headings under Functional Requirements. If count >5, this is a Critical Issue (forces REVISE). Count 6-7 with strong justification can pass with a Warning."
- Rationale text for the agent: "Large specs produce excessive scenarios (>100), increasing SA non-determinism and iteration cost."

**Complexity:** S

#### 1e. Deterministic precheck — FR count function
**File:** `darkfactory/spec/precheck.py`

Add:
- New function `check_fr_count(text: str) -> tuple[int, list[int]]` — parse `### FR-N:` headings under Functional Requirements section, return count and line numbers.
- Add FR count to `PreCheckResult` (new field `fr_count: int | None = None`).
- In `run_precheck()`, call `check_fr_count()`. If count > 5, add to findings. Do NOT hard-fail precheck (the agent validator handles the REVISE decision), but include in summary.
- Respect code fences (existing pattern in precheck.py).

**Complexity:** M

#### 1f. Templates — FR count guidance
**Files:** `templates/feature-spec.md`, `templates/api-spec.md`, `templates/project-plan.md`, other templates as appropriate

Add guidance comment in Functional Requirements section: "Target 3-5 FRs per spec. If you need more, split into multiple specs with dependency chains."

Add "Est. FRs" column to project-plan.md feature decomposition table.

**Complexity:** S

### Phase 1 Tests
- Unit test for `check_fr_count()`: 1 FR, 5 FRs, 6 FRs, 9 FRs, no FR section, FRs inside code fences (should be excluded).
- Smoke: `dark-factory precheck` on budget-brain `2026-03-24-project-scaffolding.md` should report "9 FRs detected."

### Phase 1 Verification
Run `/df-write-plan` on a test project. Verify: features are capped at 5 FRs, pipeline suitability estimates appear, features with >5 FRs are auto-decomposed.

---

## Phase 2: Pin Scenario Count + Intent in Spec Template ✓

**Status:** COMPLETE — committed as `8b8130e`
**Goal:** Specs declare what scenarios should test. The SA compiles intents to test code rather than inventing from scratch. This removes the biggest variance source (37 vs 72 scenarios from the same spec).

### Changes

#### 2a. All spec templates — Scenario Intent section
**Files:** All 8 templates in `templates/`

Add new section (after Acceptance Criteria, before Examples):

```markdown
## N. Scenario Intent

Declare what each scenario should verify. The Scenario Architect compiles these
intents into executable test code — it does not invent scenarios beyond what is
declared here plus a small cross-cutting set.

### FR-1: [Name]
| # | Intent | Layer |
|---|--------|-------|
| 1 | [e.g., "Valid input returns 200 with expected body shape"] | happy_path |
| 2 | [e.g., "Missing required field returns 400 with field name in error"] | error |
| 3 | [e.g., "Empty string name is rejected"] | edge_case |
| 4 | [e.g., "SQL injection in name field is safely handled"] | security |
| 5 | [e.g., "Concurrent creates don't produce duplicate records"] | state |

### Cross-Cutting (optional, 0-10 intents)
| # | Intent | Layer |
|---|--------|-------|
| 1 | [e.g., "Auth token expiration returns 401 on all endpoints"] | security |
```

Layer values: `contract`, `happy_path`, `edge_case`, `error`, `state`, `security`, `property`, `adversarial`

**Complexity:** S per template × 8 = M total

#### 2b. `/df-write-spec` skill — generate intents during interview
**File:** `.claude/skills/df-write-spec/SKILL.md`

Add to interview process: "After writing Acceptance Criteria, generate Scenario Intents. For each FR, propose 5-15 intents covering happy path, error cases, edge cases, and security. Present to user for review. Ask: 'What else would you check to be sure FR-N works?'"

Add to completeness checklist: "Scenario Intent section present? Each FR has 5-15 intents?"

**Complexity:** S

#### 2c. `/df-validate-spec` skill — verify intents
**File:** `.claude/skills/df-validate-spec/SKILL.md`

Add: "Verify Scenario Intent section exists. Each FR must have ≥5 intents. Intents must be one-line, testable descriptions. Flag missing/empty as Critical Issue."

**Complexity:** S

#### 2d. Spec Validator agent — intent validation
**File:** `agents/spec-validator.md`

Add Scenario Intent to completeness checklist as section 11. Adjust scoring: redistribute to keep /100 total (reduce Examples from 10→5, Dependencies/Out of Scope from 10→5, add Scenario Intent at 10).

Add validation rule: "Each FR must have 5-15 scenario intents. Intents must be one-line testable behaviors. Intents must not assert on free variables."

**Complexity:** M

#### 2e. Deterministic precheck — intent check
**File:** `darkfactory/spec/precheck.py`

Add function `check_scenario_intents(text: str) -> dict[str, int]` — parse Scenario Intent section, return per-FR intent counts. Add to `PreCheckResult`. Missing section = finding (not hard-fail during transition; controlled by config `require_scenario_intents: bool`).

Add `"scenario intent"` to `_SECTION_ALIASES`.

**Complexity:** M

#### 2f. SA agent — compile intents, don't invent
**File:** `agents/scenario-architect.md`

Add preamble to Step 2 (Generate Scenarios): "If the spec contains a Scenario Intent section, use it as your primary guide. For each declared intent, compile it into test code. Do NOT invent scenarios beyond the declared intents unless the spec has a Cross-Cutting section. Your creativity focuses on HOW to test each intent (assertion strategy, test data), not WHAT to test."

Add Critical Rule: "Total scenario count must be within 1.5x of total declared intent count. If the spec declares 30 intents, generate 30-45 tests, not 100."

**Complexity:** S

### Phase 2 Tests
- Unit test for `check_scenario_intents()`: valid section, missing section, FR with 3 intents (too few), FR with 20 intents.
- Integration: spec with intents passes `dark-factory validate`; spec without intents gets flagged.

### Phase 2 Verification
Write a small test spec with scenario intents. Run `dark-factory run` twice. Compare SA output: scenario count should be within 1.5x of intent count both times (not 37 one run and 72 the next).

---

## Phase 3: Tier 1 / Tier 2 Split ✓

**Status:** COMPLETE — committed as `70169e9` (3a), `271910a` (3b), `67bb9bd` (3c), `06400f2`+`b646b8c` (3d), `edebe54` (3e), `220b04d` (3f)
**Goal:** Separate deterministic contract verification (Tier 1) from creative adversarial testing (Tier 2). Tier 1 produces identical tests every run.

### Design Decisions

**Contract format:** YAML blocks inside the spec's Acceptance Criteria section. Each contract specifies: id, FR reference, precondition (given), action (when), expected result (then) as structured data.

**Invariant marker:** ACs verified by build/lint/typecheck gates are marked `invariant: true`. The SA skips these entirely.

**Compilation:** A new module (`darkfactory/spec/contracts.py`) parses contracts and compiles them to test code. For TypeScript: vitest `describe`/`it` blocks. For Python: pytest functions. The output is deterministic — same input always produces identical test files.

**Tier 2 scope:** SA generates scenarios for layers 3-8 only (edge cases, error conditions, state/sequence, security, property-based, adversarial). Layers 1-2 (contract, happy path) are handled by Tier 1.

### Changes

#### 3a. Spec templates — contract blocks
**Files:** All 8 templates in `templates/`

Restructure Acceptance Criteria section:

```markdown
## 8. Acceptance Criteria

### Tier 1: Test Contracts (compiled deterministically)

These contracts are compiled to test code mechanically. They produce identical
tests every pipeline run.

```yaml
contracts:
  - id: TC-1
    fr: FR-1
    type: http          # http | file_exists | file_contains | command | import
    given: "valid auth token"
    when:
      method: POST
      path: /api/v1/resource
      body: { "name": "Test" }
    then:
      status: 201
      body:
        id: { type: string }
        name: { equals: "Test" }
    invariant: false

  - id: TC-2
    fr: FR-1
    type: file_exists
    given: "after build"
    when:
      check: "dist/index.js"
    then:
      exists: true
    invariant: true    # already verified by build gate — SA skips
```

### Tier 2: Adversarial Criteria (SA-generated)

These criteria guide the SA's creative adversarial testing (layers 3-8).

| ID | Criterion |
|----|-----------|
| AC-1 | GIVEN [edge condition] WHEN [action] THEN [expected] |
```

**Contract types:**
- `http` — HTTP request/response (needs test server)
- `file_exists` — file path exists after implementation
- `file_contains` — file content matches (JSON property, YAML key, regex)
- `command` — shell command exit code and/or output
- `import` — module exports expected symbols

**Complexity:** M per template

#### 3b. Contract parser and compiler — new module
**File:** `darkfactory/spec/contracts.py` (NEW)

```python
@dataclass
class Contract:
    id: str
    fr: str
    type: str               # http, file_exists, file_contains, command, import
    given: str
    when: dict
    then: dict
    invariant: bool = False

def parse_contracts(spec_text: str) -> list[Contract]:
    """Extract YAML contract blocks from spec markdown."""

def compile_contracts(
    contracts: list[Contract],
    language: str,            # typescript, python, go, rust
    test_framework: str,      # vitest, jest, pytest, go_test, cargo_test
) -> list[tuple[str, str]]:  # [(filename, content), ...]
    """Compile contracts to deterministic test files.

    Invariant contracts are skipped (they're verified by gates).
    Non-invariant contracts are compiled to test code.
    Output is deterministic: same input → identical output.
    """
```

Compilation targets:
- TypeScript/vitest: `describe('Tier 1 Contracts', () => { it('TC-1: ...', ...) })`
- Python/pytest: `class TestTier1Contracts: def test_tc_1(self): ...`

File naming: `00-tier1-contracts.test.ts` (prefix `00-` sorts before SA files)

**Complexity:** L — this is the core new logic. Start with `file_exists`, `file_contains`, and `command` types (most common for scaffolding specs). Add `http` and `import` types as needed.

#### 3c. Precheck — contract validation
**File:** `darkfactory/spec/precheck.py`

Add function `check_contracts(text: str) -> list[str]` — validate YAML contract blocks parse correctly. Return list of issues (malformed YAML, missing required fields, unknown contract type). Add to `PreCheckResult`.

**Complexity:** M

#### 3d. SA agent — Tier 2 only
**File:** `agents/scenario-architect.md`

Update:
- Step 1: "If the spec contains Tier 1 contracts, those are compiled separately by the pipeline. Your job is Tier 2 only — focus on layers 3-8."
- Critical Rules: "Do NOT generate scenarios for ACs marked `invariant: true`. Do NOT re-test what Tier 1 contracts cover."
- Layer guidance: "Layers 1-2 are handled by Tier 1. Generate scenarios for edge cases, error conditions, state/sequence, security, property-based, and adversarial layers only."

**Complexity:** S

#### 3e. Orchestrator — compile Tier 1 before SA invocation
**File:** `darkfactory/pipeline/orchestrator.py`
**Function:** `_step_scenario_generation()`

Before invoking the SA:
1. Parse contracts from spec
2. Filter out invariant contracts
3. Compile remaining contracts to test files
4. Write to scenarios directory with `00-tier1-` prefix
5. Pass Tier 1 file list to SA context so it knows what's covered

**Complexity:** M

#### 3f. Satisfaction scoring — tier-weighted
**File:** `darkfactory/scenarios/satisfaction.py`

Update `compute_satisfaction()`:
- Detect Tier 1 files by `00-tier1-` prefix
- Compute separate Tier 1 and Tier 2 scores
- Weighted total: `total = tier1_weight * tier1_score + tier2_weight * tier2_score` (configurable, default 60/40)
- Add `tier1_score` and `tier2_score` to `SatisfactionScore`

**File:** `darkfactory/models/config.py`
Add: `tier1_weight: int = 60`, `tier2_weight: int = 40`

**Complexity:** M

### Phase 3 Tests
- Unit tests for `parse_contracts()`: valid YAML, malformed YAML, missing fields, invariant markers.
- Unit tests for `compile_contracts()`: generate vitest file for file_exists, file_contains, command contracts. Verify output is deterministic (run twice, diff = empty).
- Unit tests for tier-weighted satisfaction scoring.
- Integration: run pipeline twice on same spec with contracts, verify Tier 1 test files are byte-identical.

### Phase 3 Verification
Write a spec with 5 Tier 1 contracts and 15 Tier 2 intents. Run pipeline. Verify:
1. `00-tier1-contracts.test.ts` is identical across runs
2. SA output only covers layers 3-8
3. Satisfaction score shows Tier 1/Tier 2 breakdown

---

## Phase 4: SA Output Validation (Tier 2)

**Status:** MOVED → [Path to First Ship](path-to-first-ship.md) Phase 2
**Moved on:** 2026-03-29
**Reason:** Phase 4 is the highest-leverage remaining change and is being absorbed into a broader plan that addresses the full set of blockers preventing spec shipment (Oracle decision logic, SA validation, probabilistic thresholds, tool knowledge injection). See the new plan for the expanded scope.

**Original goal:** Mechanical quality checks on SA-generated scenarios with regeneration on failure, before the implementer sees anything.

### Changes

#### 4a. Scenario validator — new checks
**File:** `darkfactory/scenarios/validator.py`

Add checks 10-14 (apply to Tier 2 files only, skip `00-tier1-*` prefix):

| # | Check | Type | Description |
|---|-------|------|-------------|
| 10 | Assertion count upper bound | Hard fail | Max 15 assertions per file (configurable). Files with >15 are overly granular and brittle. |
| 11 | No source file reads | Hard fail | `readFileSync` / `open()` on `.ts`, `.tsx`, `.js`, `.jsx`, `.py`, `.go`, `.rs` files. Config files (`.json`, `.yaml`, `.toml`) are allowed. |
| 12 | No indexOf ordering | Hard fail | `indexOf(X) < indexOf(Y)` or `indexOf(X) > indexOf(Y)` patterns. Tests ordering that spec doesn't constrain. |
| 13 | Intent count alignment | Hard fail | Total Tier 2 test count within 2x of declared scenario intents. Requires spec path (skip if no intent section). |
| 14 | Regex multiline flag | Warning | `toMatch(/pattern/)` on multi-line content without `/m` or `/s` flag. |

Update `validate_scenarios()` signature: add `spec_path: Path | None = None`, `tier1_prefix: str = "00-tier1-"`.

**Complexity:** M

#### 4b. Config — new fields
**File:** `darkfactory/models/config.py`

Add:
- `sa_max_retries: int = 2` — max regeneration attempts on validation failure
- `scenario_max_assertions_per_file: int = 15`

**Complexity:** S

#### 4c. Orchestrator — SA retry loop
**File:** `darkfactory/pipeline/orchestrator.py`
**Location:** After `_step_scenario_generation()`, before format compatibility check

Add retry loop:
```
for attempt in range(config.sa_max_retries):
    validation = validate_scenarios(..., spec_path=spec_file)
    if validation.passed:
        break
    log("SA validation failed (attempt {attempt+1}), regenerating...")
    # Clear Tier 2 files only (preserve Tier 1)
    # Re-invoke SA with validation report prepended
    _step_scenario_generation(..., validation_report=validation.summary)
```

This is a pre-implementation retry loop, NOT an iteration. It does not consume iteration budget. The implementer never sees failed SA output.

**Complexity:** M

### Phase 4 Tests
- Unit test for each new check: construct files that trigger each, verify detection.
- Check 10: file with 1 assertion (pass), file with 20 (fail).
- Check 11: `readFileSync('src/server.ts')` (fail), `readFileSync('package.json')` (pass).
- Check 12: `content.indexOf('a') < content.indexOf('b')` (fail).
- Check 13: 20 intents in spec, 50 tests (pass), 100 tests (fail).
- Integration: inject bad scenario file, run pipeline, verify regeneration occurs (check logs).

### Phase 4 Verification
Run pipeline on a spec. Check logs for SA validation pass/fail. If regeneration occurs, verify the second attempt produces cleaner scenarios.

---

## Cross-Phase Dependencies

```
Phase 1 ──→ Phase 2 ──→ Phase 3 ──→ Phase 4
  │            │            │            │
  │            │            │            └─ Check 13 needs Phase 2 (intents)
  │            │            │               Skip Tier 1 files (Phase 3 prefix)
  │            │            │
  │            │            └─ Contracts formalize Phase 2 intents
  │            │               SA told "Tier 2 only" builds on Phase 2 guidance
  │            │
  │            └─ Intents per FR; FR count capped by Phase 1
  │               5 FRs × 15 intents = 75 max (manageable)
  │
  └─ Smaller specs make all downstream phases more effective
```

## Risk Areas

| Risk | Mitigation |
|------|------------|
| FR cap too strict for legitimate complex features | Allow override via spec metadata (`<!-- max-frs: 8 -->`) but default to 5 |
| Scenario Intent adds authoring burden | `/df-write-spec` auto-generates intents from ACs; user reviews, not writes from scratch |
| YAML contract blocks fragile to parse | Validate in precheck.py before any agent sees the spec |
| Tier 1 compilation produces tests that don't compile | Run `tsc --noEmit` on compiled contracts (reuse existing check 9) |
| SA retry loop burns API budget | Cap at 2 retries. Log cost. Does NOT count against iteration budget |
| Backward compatibility with existing specs | New checks warn (not fail) when sections are missing. Config flag `require_scenario_intents: bool = False` for transition period |

## Success Metrics

| Metric | Current Baseline | Target | How to Measure |
|--------|-----------------|--------|----------------|
| Specs shipped | 0 / 5 runs | ≥1 / 3 runs | Pipeline telemetry |
| SA scenario count variance | σ ≈ 17 (37-72) | σ < 5 | Run same spec 3x |
| Tier 1 test determinism | N/A | 100% identical across runs | `diff` compiled test files |
| SA validation pass rate (first attempt) | Unknown | >70% | Pipeline logs |
| Runs to first SHIP | >5 (never shipped) | ≤2 | Pipeline telemetry |

## Implementation Order

1. **Phase 1** (1-2 days): Skill/template/precheck changes. Ship, then re-decompose budget-brain plan to validate.
2. **Phase 2** (1-2 days): Template + skill + precheck + SA prompt. Ship, then write a small test spec with intents and run pipeline.
3. **Phase 3** (3-5 days): `contracts.py` + orchestrator + satisfaction scoring. Ship, then run pipeline and verify Tier 1 determinism.
4. **Phase 4** (1-2 days): Validator checks + retry loop. Ship, then run pipeline and check SA validation logs.

**Total estimated effort:** 6-11 days
**Measurement gate:** After Phase 2, run 3 pipeline attempts on a small, well-scoped spec. If any ships, continue to Phase 3. If none ship, reassess — the problem may be elsewhere.

### Progress Log

| Date | Phase | Commits | Notes |
|------|-------|---------|-------|
| 2026-03-28 | Phase 1 | `abfaff8`, `1eb2f31` | FR cap in plan/spec skills, precheck, templates, parser regex fix for sub-feature IDs |
| 2026-03-28 | Phase 2 | `8b8130e` | Scenario Intent in all 8 templates, precheck validation, SA intent-driven workflow. Reviewed by Linus + Neo (bugs: duplicate issue emission, regex missed 6/8 templates — both fixed) |
| 2026-03-28 | Phase 3a | `70169e9` | Tier 1/Tier 2 AC split in all 8 spec templates with contract schema reference |
| 2026-03-28 | Phase 3b | `271910a` | Contract parser + compiler (`contracts.py`): YAML→vitest/pytest, injection-safe, deterministic. Reviewed by Linus + Neo (critical: injection vulns in JS escape, regex, HTTP; missing async; when/then validation) |
| 2026-03-28 | Phase 3c | `67bb9bd` | Contract validation in precheck — warning-only, delegates to `parse_contracts()` |
| 2026-03-28 | Phase 3d | `06400f2`, `b646b8c` | SA agent: replaced blanket YAML regex exception, added Rule 8 + Step 2.0 for Tier 1 awareness |
| 2026-03-28 | Phase 3e | `edebe54` | Orchestrator: compile Tier 1 before SA, write files after SA (resume-safety). Reviewed by Linus + Neo (critical: has_scenario_files poisoning on resume) |
| 2026-03-28 | Phase 3f | `220b04d` | Tier-weighted satisfaction scoring (60/40 default), config loader, 13 new tests. Reviewed by Linus + Neo (bug: division by zero, config loader gap) |
| — | **GATE** | — | Run 3 pipeline attempts on small spec with contracts + intents. If any ships, proceed to Phase 4 |
