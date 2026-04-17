# Agent Reliability & Measurement — Pipeline Improvement Plan

**Date**: 2026-04-16
**Status**: Reviewed by Neo — updated for findings
**Benchmark spec**: `database-foundation` (budget-brain-v2, 3 iterations, escalated)
**Target**: `~/sites/dark-factory`

---

## Motivation

The `database-foundation` spec failed to ship after 3 iterations and was
escalated. Root cause investigation revealed multi-agent coordination failures
across ALL four critical agents — not a spec quality issue (spec scored 90/100).

Parallel research into LLM instruction-following benchmarks (AgentIF NeurIPS
2025, CodeIF ACL 2025, Chroma Context Rot) revealed a quantitative explanation:
**every agent in the pipeline receives 6,680-9,192 words of input, exceeding the
~6,000-word threshold where instruction compliance drops to near zero for all
frontier models.**

This plan addresses the problem through three complementary strategies:
1. Mechanical validators that replace prompt-level rules with code
2. Prompt reduction to bring agent inputs below the compliance cliff
3. Pipeline guardrails that prevent failure cascading between agents

Every proposed change has a measurable outcome. The `database-foundation` spec
serves as the benchmark — it has 3+ failed runs as baseline data.

---

## Research Foundation

| Finding | Source | Implication |
|---------|--------|-------------|
| ISR drops to ~0% at >6,000 words | AgentIF (NeurIPS 2025) | All our agents are over this |
| Best model: 36.2% constraint satisfaction on hard code tasks | CodeIF (ACL 2025) | Prompt rules alone are insufficient |
| ~150-200 instruction capacity | HumanLayer analysis | Our prompts have 13-24 MUST constraints each |
| 30%+ accuracy drop in middle of context | Chroma Context Rot | Rules in the middle of long prompts are effectively invisible |
| Models self-grant rule exemptions | Yajin Zhou (2026-03) | Even prominent mandatory rules get ignored |
| Mechanical enforcement >> prompt rules | Multiple studies | The architectural bet Dark Factory already makes for barriers |

---

## Baseline Measurements

### Agent Input Sizes (database-foundation spec)

| Agent | Definition | + Context | Total | Over 6K? |
|-------|-----------|-----------|-------|:--------:|
| Architect | 2,580w | spec 4,196 + env 116 + addenda 1,201 | **~8,093w** | YES |
| Test Engineer | 3,083w | spec 4,196 + manifest 310 + env 116 + contracts 1,487 | **~9,192w** | YES |
| Implementer (iter 0) | 2,058w | spec 4,196 + manifest 310 + env 116 | **~6,680w** | YES |
| Implementer (iter 2) | 2,058w | above + feedback 828 | **~7,508w** | YES |
| Oracle | 2,639w | spec 4,196 + gate reports + review + prior feedback | **~8,000+w** | YES |

### Observed Failures (database-foundation benchmark)

| ID | Failure | Agent | Metric | Baseline Value |
|----|---------|-------|--------|---------------|
| F1 | `commands.test` scoped to `packages/shared` not `apps/api` | Architect | monorepo_scoping_violation | true |
| F2 | Tests use postgres.js syntax on DrizzleTx type | Test Engineer | scenario_import_violations | >0 |
| F3 | Claimed 57% pass rate (actual: 0%) | Oracle | oracle_metric_delta | 57 |
| F4 | Missing baseline migration .sql file | Implementer | missing_manifest_files | >=1 |
| F5 | Overall pass rate | Pipeline | pass_rate | 0% |
| F6 | Iterations to ship | Pipeline | iterations | 3 (escalated) |

---

## Phase 1 — Instrument & Gate

**Effort**: S | **Timeline**: 1-2 days | **Dependency**: None

### M5: Prompt Word Count Logging

**What**: Instrument `invoke_agent` to compute and log total word count of
assembled prompt (system prompt + task prompt) to `events.jsonl`.

**Why**: We cannot improve what we cannot measure. This is the prerequisite for
validating all prompt reduction work.

**Modularity**: Fully general — no language/ecosystem dependency.

**Implementation**: In `darkfactory/pipeline/agents.py`, after prompt assembly
and before `subprocess.run`, count words and emit event.

**Metric**: `prompt_word_count` per agent per invocation.
**Target**: 100% of invocations logged. Optionally gate at configurable
threshold (default 6,000 words) as a warning.

---

### M4: Post-Implementer File Existence Preflight

**What**: After Implementer exits and before quality gate, check that every
file in `manifest.file_map` with `action: create` exists on disk. If missing,
re-prompt Implementer with exact list.

**Why**: The Implementer missed a spec-described file. Research shows files at
the bottom of long input are easily lost.

**Modularity**: Fully general — file existence check, no language dependency.
Follows the pattern of existing `check_forbidden_patterns` preflight.

**Implementation**: In `iteration.py`, between Implementer exit and pre-gate
launch. Read manifest YAML, iterate `file_map`, check `os.path.exists()`.

**Metric**: `missing_manifest_files` count per implementation attempt.
**Target**: 0 missing files when quality gate begins.

**Neo review note**: Prioritized ahead of M3 — simpler to implement (stat
checks on disk), prevents a concrete failure that cascaded through
database-foundation, and produces a clear signal.

---

### M3: Oracle Metric Accuracy Enforcement

**What**: Require the Oracle to emit a structured `<!-- METRICS: {"pass_rate":
N, "scenario_pass": X, "scenario_total": Y} -->` HTML comment block in its
output (same pattern as the existing `<!-- CLASSIFICATIONS: {...} -->` block).
After Oracle writes output, parse this block and compare `pass_rate` against
`iter_state.pass_rate`. If delta > 1 percentage point, reject output and
re-prompt.

**Why**: The Oracle fabricated test metrics (claimed 57% when actual was 0%).
This corrupts the feedback loop — the Implementer wastes iterations chasing
fabricated failures.

**Modularity**: Fully general — compares two numbers, no language dependency.

**Implementation**: Extend the existing feedback validation at
`iteration.py:3256-3304`. Add `<!-- METRICS: -->` requirement to `oracle.md`.
Parse via regex + JSON (same pattern as CLASSIFICATIONS extraction at line 801
of `iteration.py`). Compare against `iter_state.pass_rate`.

**Neo review note**: Do NOT parse pass rate from the Oracle's prose — the
format is unreliable and the Oracle deviates from templates (the 57%
fabrication is evidence). The structured HTML comment with JSON payload matches
the existing CLASSIFICATIONS pattern and gives a reliable parse target.

**Metric**: `oracle_metric_delta` (absolute difference, claimed vs actual).
**Target**: Delta <= 1 on all runs.
**Secondary**: `oracle_reprompt_rate` — how often Oracle gets it wrong initially.

---

### G2: Pre-Gate Scenario Import & Export Validator

**What**: After Test Engineer writes scenarios and before quality gate, parse
import/require statements in test files and verify:
1. Each source string matches a declared `entry_points[*].specifier`
2. Each imported symbol name is a subset of that entry point's `exports` list

**Why**: The TE generated tests importing with incompatible API patterns. Import
mismatches are the #1 cause of scenario crashes (documented in TE agent def).
Checking specifiers alone (Gap 1 from Neo review) is insufficient — the TE may
import the right module but use wrong symbols or wrong API surface.

**Modularity**: **Language-specific.** Import parsing differs per ecosystem:
- TypeScript/JS: `import { X } from '<specifier>'` and `require('<specifier>')`
- Python: `from <module> import <name>` and `import <module>`
- Go: `import "<path>"`
- Rust: `use <crate>::<path>`

**Adapter integration**: Add a `parse_test_imports(file_path) ->
list[ImportInfo]` method to the adapter interface as a **concrete method with
default no-op** (avoids breaking change per ADAPTER-GOVERNANCE.md). Each
adapter implements import extraction for its language. `ImportInfo` contains
both the specifier and the imported symbol names.

**Implementation**: In `darkfactory/scenarios/validator.py`, extending the
existing post-TE validation. The adapter provides parsed imports; the validator
checks specifier set membership AND symbol subset against manifest entry_points.

**Neo review note**: Promoted from Phase 2 — the infrastructure is half-built
(`validator.py` already does file-pattern discovery and assertion checks).
Adding import-specifier cross-checking is incremental. This is the single
improvement most likely to prevent the TE's "wrong API surface" failure class.

---

## Phase 2 — Mechanical Barriers

**Effort**: M | **Timeline**: 2-3 days | **Dependency**: Phase 1 (for metrics logging)

### M1: Monorepo Command Scoping Validator

**What**: After Architect writes manifest, verify every `commands.*` field in
the `runtime` amendment targets a directory consistent with the spec's
`source_dirs`. Reject manifest on violation; retry Architect with error.

**Why**: The Architect ignored a prominent "mandatory" monorepo scoping rule.
This rule is perfectly mechanizable.

**Modularity**: Mostly general. The core check (does `commands.X` scope to a
source_dir?) is universal. However, **parsing what constitutes "scoping" is
language-specific** — `cd apps/api &&` (shell), `--filter ./apps/api` (pnpm),
`./apps/api/...` (Go), positional `apps/api/` (pytest). Detecting workspace
structure also differs by ecosystem.

**Adapter integration**: Add two concrete methods with default no-op
implementations (avoids breaking change per ADAPTER-GOVERNANCE.md):
- `detect_workspaces() -> list[str]` — workspace discovery
- `validate_command_scope(command, source_dirs) -> list[Finding]` — parse the
  command string for scoping, using knowledge of the package manager's CLI

**Where to run (Neo review: both stages)**:
1. **Pre-delta** (on `manifest.runtime.amendment_ops`): Primary gate. Catches
   the Architect's mistake before it becomes the contract. Run after
   `validate_manifest()` returns, before `amend._build_ops`. Enables Architect
   retry.
2. **Post-delta** (on materialized `environment.toml`): Defense-in-depth. Catches
   delta pipeline bugs and stale bootstrap values. Run as preflight before TE.

**Implementation**: In `darkfactory/spec/manifest.py` (pre-delta) and
`darkfactory/setup/workspace_check.py` (post-delta).

**Metric**: `monorepo_scoping_violation` (boolean per run).
**Target**: 0 violations reaching downstream agents.

---

### G1: Architect Manifest Retry Loop

**What**: Treat manifest validation errors (from `validate_manifest` + M1) as
retryable. If validation fails, re-prompt Architect with error list (up to 2
retries). Block TE phase until manifest validates clean.

**Why**: Currently validation errors are logged but not gated. Invalid manifests
cascade into TE and Implementer failures, wasting 2+ iterations.

**Modularity**: Fully general — orchestrator flow control.

**Implementation**: In `iteration.py`, wrap Architect invocation in retry loop
matching the existing TE retry pattern.

**Metric**: `manifest_validation_retries` per run.
**Target**: First-attempt pass rate >80%. Zero invalid manifests reaching TE.

---

## Phase 3 — Prompt Reduction

**Effort**: M-L | **Timeline**: 3-4 days | **Dependency**: M5 (word count logging)

### P1: Split Agent Definitions into Core + Mounted Reference

**What**: Restructure each agent .md into:
- **Core** (<=1,500 words): Role, access rules, hard rules, protocol steps.
  Injected via `--append-system-prompt`.
- **Reference** (remaining): Examples, edge-case instructions, explanatory
  prose. Mounted as a readable file the agent can consult via tool use.

**Why**: AgentIF shows compliance cliffs at >6,000 words. Removing 1,000+ words
from the system prompt may bring agents below threshold.

**Cut candidates per agent** (preliminary — validate with A/B testing):

| Agent | Current | Cut to reference | Keep in core | Est. core |
|-------|---------|-----------------|-------------|-----------|
| Architect | 2,580w | YAML template, forbidden-pattern docs, amend-mode docs | CWD rule, monorepo scoping, step protocol | ~1,400w |
| Test Engineer | 3,083w | Code examples (80+ lines), logger observation guide, per-language idioms | **Import rules, framework detection, file naming** (top-3 failure prevention — Neo review) | ~1,500w |
| Implementer | 2,058w | Output format template, test integration verification walkthrough | Phase protocol, access rules, iteration protocol | ~1,300w |
| Oracle | 2,639w | Ship-with-gaps criteria prose, low-confidence anchor explanation | Decision format, barrier rules, METRICS/CLASSIFICATIONS blocks | ~1,500w |

**Neo review note on TE**: The import rule, framework detection, and file
naming conventions are the TE's most critical constraints — they directly
prevent the failure modes this plan addresses. These MUST stay in the core
prompt (~800w budget). Only examples and templates go to reference. Moving
import rules to a reference file risks the TE not reading them, recreating
the exact failure mode we're fixing.

**Docker mount consideration**: Agent containers already mount the project
directory. Reference files placed at `.dark-factory/agents/reference/` are
accessible via the existing `:ro` or `:rw` project mount. No Docker config
changes needed. Verified: `--dangerously-skip-permissions` allows unrestricted
Read/Glob/Grep tool use inside the container.

**Caveat**: Reading a reference file consumes a tool-use turn against
`--max-turns`. Splitting into 3+ reference files costs 3+ turns of overhead.
Verify turn budget is sufficient. The bigger risk: the agent may choose not
to read the file. Mitigate with mandatory directive in core prompt, but
recognize this is itself a prompt-level constraint subject to compliance
degradation.

**Modularity**: Fully general — all agents benefit.

**Metric**: `prompt_word_count` (before vs after).
**Target**: Agent definition <1,500w AND total input at **iteration 0** <6,000w.
Accept that later iterations (with feedback, inline defects) will exceed the
threshold — the AgentIF research measures initial prompt compliance; subsequent
turns may have different dynamics since the model has already internalized
core constraints.

**Neo review note**: Reframed from "all agents <6,000w total" to "iteration 0
<6,000w." The spec alone is ~4,196w for database-foundation; with 1,500w agent
def + manifest + env contract, iter 0 lands at ~6,200w. Later iterations with
feedback will always exceed 6K regardless of prompt reduction.

---

### P2: Move Examples to Mounted Files

**What**: Extract code examples from TE and Architect definitions into
`agents/examples/{role}-examples.md`. Add single instruction: "Read
/project/.dark-factory/agents/reference/{role}-examples.md for syntax reference
before writing output."

**Why**: Quick win — the TE has 80+ lines of vitest/pytest/Go/Rust examples.
These are consulted once and shouldn't occupy the instruction stream permanently.

**Modularity**: Fully general.

**Metric**: TE definition word count (before: 3,083w, target: ~1,800w).
**Regression check**: Import violation rate must not increase.

---

### P4: Condense Oracle Context to Structured Data

**What**: Replace verbose prose crash context, stable-failure analysis, and
ship-with-gaps explanation in the Oracle prompt with structured YAML/JSON data
blocks. Replace 200+ words of explanation per section with 20-30 words of data
plus a reference pointer.

**Why**: The Oracle fabricated metrics. Research shows hallucination worsens
with more "noisy" prose in context.

**Modularity**: Fully general.

**Metric**: Oracle `prompt_word_count` (target: <6,000w).
**Accuracy**: `oracle_metric_delta` (should decrease as noise decreases).

---

## Phase 4 — Re-run & Measure

**Effort**: S | **Timeline**: 1 day | **Dependency**: Phases 1-3

1. Reset `database-foundation` pipeline state
2. Re-run full pipeline
3. Compare ALL metrics against baseline table above
4. Document results in the Results section below

### A/B Testing Protocol (for prompt reduction)

For the P1+P2 bundle: run **5x** with current prompts, **5x** with reduced
prompts, across **database-foundation AND at least 2 additional specs** to
test generalization. Compare:
- Constraint compliance rate (measured by validators M1-M5)
- Pass rate per iteration
- Total iterations to SHIP/escalate

Cost: ~$1.50/run. Total: ~$22.50 for 15 runs (5 per variant x 3 specs).

**Pre-registered success criteria** (define before running):
> P1+P2 is successful if: median iterations-to-SHIP decreases by at least 1,
> OR mean pass_rate at iteration 1 increases by at least 20 percentage points,
> across both database-foundation AND at least one other spec.

**Neo review note**: 3 runs per group (original plan) is not statistically
meaningful. With n=3, even a large effect (Cohen's d > 1.0) won't reach
p < 0.05 on a two-sample t-test. With n=5 and a binary outcome (SHIP/NO-SHIP),
Fisher's exact test gives p = 0.008 for a perfect 5/5 vs 0/5 split.

**Biggest risk in prompt reduction** (Neo review): Improvements that overfit to
database-foundation won't transfer. The A/B protocol must include specs that
exercise different failure modes. Before cutting any constraint from a core
prompt, tag it with the failure mode it prevents. If that failure mode appeared
in ANY past pipeline run (not just database-foundation), keep it in the core.
Only move constraints that are redundant with mechanical validators (once G2
validates imports mechanically, the TE's import rules can be shortened).

---

## Phase 5 — Iterate (Ongoing)

### P3: Conditional Adapter Addenda Injection
Filter adapter addenda to only inject sentences relevant to current spec's
dependencies. Target: <400 addendum words per agent.

### G3: Constraint Compliance Test Suite
Known-input/known-output fixtures for each validator. Run in CI to detect
regressions when agent definitions change. Start with M1/M3/M4.

### G4: Iteration Waste Category Tracking
Classify each NO-SHIP iteration by root cause: `MANIFEST_INVALID`,
`SCENARIO_CRASH`, `IMPL_MISSING_FILE`, `ORACLE_METRIC_ERROR`,
`GATE_FAIL_LEGITIMATE`, `UNKNOWN`. Track trends across runs.

---

## Measurement Summary

| ID | Metric | Baseline | Target | Logged In |
|----|--------|----------|--------|-----------|
| M5 | prompt_word_count | 6,680-9,192 | <6,000 iter 0 | events.jsonl |
| M4 | missing_manifest_files | >=1 | 0 | events.jsonl |
| M3 | oracle_metric_delta | 57 | <=1 | events.jsonl |
| G2 | scenario_import_violations | >0 | 0 | events.jsonl |
| G2 | scenario_export_violations | >0 | 0 | events.jsonl |
| M1 | monorepo_scoping_violation | true | false | events.jsonl |
| G1 | manifest_validation_retries | N/A | <2 avg | events.jsonl |
| P1-P4 | agent def word count | 2,058-3,083 | <1,500 | events.jsonl |
| F5 | database-foundation pass rate | 0% | >80% | state file |
| F6 | iterations to SHIP | 3 (escalated) | <=2 | state file |

---

## Adapter Integration Summary

Validators that need per-ecosystem behavior plug into the existing adapter
system at `darkfactory/adapters/`. All new adapter methods are **concrete
with default no-op implementations** (not abstract) to avoid a breaking
change under ADAPTER-GOVERNANCE.md:

| Validator | General Interface | Adapter Method | Default |
|-----------|------------------|---------------|---------|
| M1 | Check commands.* target source_dirs | `detect_workspaces() -> list[str]` | `[]` (skip check) |
| M1 | Parse command scoping syntax | `validate_command_scope(cmd, dirs) -> list[Finding]` | `[]` (skip check) |
| G2 | Parse imports + symbols from test files | `parse_test_imports(path) -> list[ImportInfo]` | `[]` (skip check) |

All other validators (M3, M4, M5, G1) are fully general — no adapter needed.

---

## Known Gaps (from Neo review)

1. **Semantic manifest correctness**: G1 retries on validation *errors*, but a
   syntactically valid manifest with semantically wrong values (e.g., valid
   YAML pointing at the wrong workspace) won't trigger retry. M1 catches the
   monorepo-scoping case specifically; no general semantic validator exists.

2. **Spec-derived vs manifest-derived file requirements**: M4 checks files
   the Architect declared in `file_map`. If the Architect *forgot* to declare
   a file that the spec requires, M4 won't catch it. This gap is hard to
   close without parsing natural language.

3. **Prompt reduction regression on non-benchmark specs**: Removing rules from
   agent prompts may cause new failures on spec types database-foundation
   doesn't exercise. Mitigated by the multi-spec A/B protocol but not
   fully eliminated.

4. **Reference file read-skip risk**: After P1, agents may choose not to
   read mounted reference files. This is a prompt-level compliance risk —
   the same class of problem the plan is trying to solve.

---

## Results

_To be filled after each phase is implemented and measured._

### Phase 1 Results
| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| | | | |

### Phase 2 Results
| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| | | | |

### Phase 3 Results (A/B — 5 runs per variant, 3 specs)
| Spec | Metric | Control (5 runs) | Treatment (5 runs) | Signal |
|------|--------|-----------------|-------------------|--------|
| | | | | |

### Phase 4 Results (full re-run)
| Metric | Before | After | Delta |
|--------|--------|-------|-------|
| | | | |
