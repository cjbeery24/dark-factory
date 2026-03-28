# Dark Factory: Gap Analysis Against Original Intent

**Date:** 2026-03-28
**Purpose:** Honest comparison of our implementation vs. the original Dark Factory architecture (Yassine Dwkte's 11-agent / 9-phase pipeline), with concrete next steps.

---

## Executive Summary

The project has strong foundations — Docker barriers, format taxonomy, failure classification, enterprise gating — but is stuck at **0% ship rate across 4 pipeline runs** because of scenario quality. The scenario quality problem is a **spec quality problem** wearing a prompt engineering mask. The path forward is: make specs more precise, eliminate the bash quality gate as a reliability sink, and run more specs to generate data for deferred decisions.

---

## What We Do Well

### 1. Information barriers are genuinely superior

The original uses `holdout-guard.sh` git hooks and directory permissions. We use **Docker tmpfs overlays** that make restricted files literally invisible inside the container. This is architecturally stronger — git hooks can be bypassed, tmpfs cannot. The layered defense (Docker primary, `--disallowedTools` secondary, audit tertiary) is more robust than anything described in the original or found in open-source implementations like FSPEC or Fabro.

### 2. Failure classification and routing

The original's Defect Classifier is a separate agent. We folded its logic into the Oracle with structured `<!-- CLASSIFICATIONS: {...} -->` JSON output, a parser, and routing logic (IMPL_BUG -> iterate, SPEC_AMBIGUOUS -> escalate, ENV_ISSUE -> retry gate). This is a pragmatic consolidation — one less agent invocation, same capability. The reclassification rule (persistent IMPL_BUG -> SPEC_AMBIGUOUS after 2 iterations) is a genuine improvement over the original.

### 3. Enterprise feature gating

Cleanly separating core from enterprise with `enterprise_enabled = false` as default prevents the over-engineering trap. The original achieved 12x velocity with none of those features. Getting this right keeps focus on what matters.

### 4. Scenario format taxonomy

Five scenario formats (HTTP behavioral, browser journey, executable unit, subprocess, sentinel) with format-specific runners is more sophisticated than the original's single holdout approach. The Format E sentinel for scaffolding specs is a particularly good design — recognizing that not all specs produce testable runtime behavior.

### 5. Battle-tested through real usage

The budget-brain pipeline runs produced genuine empirical data. The iterative bug-fixing cycle (SIGPIPE, dual-writer, grep edge cases, smoke test dependency install) is exactly how production systems harden. The project is past the "it compiles" phase and into the "it works on real projects" phase.

---

## Where We Fall Short

### 1. No specification generation phase (the biggest gap)

The original has three phases before any code is written:

- **Phase 1: Product Owner Agent** — generates full Gherkin feature files with 55 measurable criteria from a one-line trigger
- **Phase 2: UX Designer Agent** — component wireframes, design tokens, interaction states
- **Phase 3: Architect Agent** — locks data models, API contracts, canonical identifiers, ADRs

We have **none of these**. Humans write specs (with `/df-write-spec` assistance), and the Implementer makes all architectural decisions on the fly.

**Why this matters:** The original's scenario quality works because the specs are absurdly detailed — "given a user with preferred_tool 'Claude Code', the lesson contains at least one terminal command example." Our specs leave too many things unconstrained, and the Scenario Architect has to guess. Every guess is a potential false failure.

**The scenario quality problem is fundamentally a spec quality problem.** The SA generates bad scenarios because the spec doesn't contain enough information to generate good ones.

### 2. No visible Test Engineer (deferred, correctly)

The original runs two test generation agents in parallel:

- **Test Engineer** — constructive visible tests the Developer can see
- **QA-Holdout** — adversarial blind tests the Developer cannot see

We have only the blind side (Scenario Architect). The Implementer writes their own visible self-tests, which creates a self-reinforcing bias — the Implementer tests what they implemented, not what the spec says.

Phase 1 (prompt enhancement requiring spec-derived self-tests) was applied. Phase 2 (measurement after 10 runs) hasn't been reached — we've only done ~4 pipeline runs. The deferral was correct, but the measurement gate should be actively pursued.

### 3. No probabilistic validation

The original runs each holdout 3x and requires 2/3 success. We run once. The multi-run infrastructure exists in `satisfaction.sh` but defaults to N=1, K=1, and the env var forwarding to Docker containers was implemented but left at default.

For deterministic failures (27/31 in budget-brain), this doesn't help. But for the flaky edge — which will grow as specs get more complex — it's a gap.

### 4. Scenario quality remains the #1 blocker

Across 4 pipeline runs: **0% ship rate**. Pass rate plateaus at 80-82%. Of 15 persistent failures in the latest run:

| Category | Count | Root Cause |
|----------|-------|------------|
| Railway schema assumptions | 6 | SA guesses wrong about config nesting |
| Environment/build validation | 3 | SA asserts on patterns the spec doesn't constrain |
| Web scaffolding details | 3 | SA checks for specific Vite/Tailwind config patterns |
| Security/type assertions | 2 | SA invents restrictions not in the spec |
| Root package.json | 1 | SA assumes `workspaces` field (pnpm uses `pnpm-workspace.yaml`) |

**18 of 19 failures are scenario-side, not implementation-side.** The implementation is good — build, lint, typecheck, and self-tests all pass. The scenarios are the problem.

Multiple prompt iterations (627 -> 202 lines, added critical rules, common pitfalls, assertion reference) have only moved the needle from ~80% to ~82%. The SA prompt is approaching diminishing returns.

### 5. No monitoring or observability agent

The original has a dedicated Monitoring Dashboard Agent that maintains JSON artifact dashboards showing timelines, pass-rate gauges, and defect breakdowns. We have cost tracking and event files, but no agent that synthesizes them into a human-readable view. For a solo developer this is tolerable; for a team it's a gap.

---

## Where We've Gone in the Wrong Direction

### 1. Prompt engineering as a substitute for structural solutions

The commit history shows 4+ iterations of SA prompt changes trying to fix scenario quality. Each one addresses specific failure patterns with more rules, more pitfalls, more guidance. But the core problem is structural:

**The SA doesn't have enough information to write good tests.** The information barrier correctly prevents it from seeing the implementation. But it also prevents it from knowing things like "Railway uses nested JSON schemas" or "pnpm uses `pnpm-workspace.yaml` instead of `workspaces` in package.json." These aren't implementation details — they're domain facts. And no amount of prompt rules will teach the SA every platform's config schema.

The original solved this by having absurdly detailed specs. We're trying to solve it by having absurdly detailed prompts. **The specs are the right lever.**

### 2. Quality gate complexity (bash/Python hybrid)

The quality gate bash script has been the single most-changed file across 30 commits, with at least 8 commits fixing bash-specific bugs: SIGPIPE from `grep|head`, dual-writer race conditions, `grep -c` producing `"0\n0"`, pipefail interactions. This is accidental complexity from the bash/Python boundary.

The original's Validator Agent is described as a single agent that "runs the blind holdout suite in an ephemeral environment." Our quality gate is a bash script that shells out to Python for parsing, writes fragment files for coordination, and has a complex state machine for 5 gates. The complexity budget is being spent on plumbing, not on pipeline intelligence.

### 3. Accumulating plan documents without execution velocity

The `docs/plans/` directory contains 14 plan files. Of those, 3 are COMPLETE, 1 is DEFERRED, and the scenario quality plans are partially implemented. Meanwhile, the scenario quality problem — identified as "Priority 0 (Existential)" — persists across all runs.

The plans are well-written and well-reviewed. But the ratio of planning to shipping is too high for the current phase. The project needs rapid empirical iteration (run pipeline -> analyze failures -> fix -> run again), not more architectural plans.

---

## Concrete Suggestions

### Priority 1: Fix scenario quality through spec quality, not prompt quality

The spec is the only legitimate information source for the SA. If the spec doesn't constrain something, the SA should not assert on it. The current approach (teach the SA about every platform via prompt rules) doesn't scale.

**Action: Add a Spec Enrichment step before scenario generation.** This doesn't need to be a full Product Owner agent — it can be a validation enhancement:

1. When the spec mentions deployment platforms (Railway, Vercel, etc.), require the spec to include the exact config schema or mark those fields as unconstrained
2. When the spec references package managers (pnpm, npm, yarn), require the spec to specify workspace configuration format
3. Add a `## Free Variables` section to every spec (the Spec Validator already has Precision Analysis — extend it to enumerate free variables that the SA must NOT assert on)

The SA already has Rule 2 ("do not assert on unconstrained values"). The problem is that the SA doesn't know which values are unconstrained because the spec doesn't say. Making unconstrained values explicit in the spec closes this gap without any prompt changes.

### Priority 2: Rewrite quality gate in Python

The bash/Python hybrid is the biggest source of accidental complexity. Every SIGPIPE, pipefail, and dual-writer bug was a bash problem. The Python code has been comparatively stable.

**Action: Port `quality-gate.sh` to Python.** The script already shells out to Python for parsing, satisfaction scoring, and report generation. The bash layer is just sequencing subprocess calls and writing fragment files — Python does this natively. Benefits:

- Eliminates SIGPIPE/pipefail class of bugs entirely
- Single language for the entire quality gate
- Structured data throughout (no fragment file coordination)
- Easier to test (Python unit tests vs shell test scripts)

This is the biggest single refactor and should be done deliberately, not rushed.

### Priority 3: Run 10 specs through the pipeline

The Test Engineer deferral, the multi-run default, the Oracle threshold — all of these decisions depend on empirical data from real pipeline runs. We have 4 runs. We need 10.

**Action: Pick 5-6 diverse specs across different project types** (API, frontend component, library, refactor, bug fix) and run them. Track:

- First-iteration pass rate
- Failure classification distribution
- Self-test coverage of FR-N/EC-N sections
- Ship rate
- Iterations to convergence

This data drives every other decision. Without it, we're optimizing in the dark.

### Priority 4: Make Free Variables explicit in specs and pipe them to the SA

Extend the spec template and validator to require a `## Free Variables` section that lists implementation choices the spec intentionally leaves unconstrained:

```markdown
## Free Variables (implementation choices — DO NOT test)
- FV-1: Railway config schema structure (nested vs flat)
- FV-2: Package manager workspace config format
- FV-3: Exact build script commands (any valid build command is acceptable)
- FV-4: CSS framework choice (Tailwind, vanilla, etc.)
```

Pipe this section into the SA's execution context. The SA already has the spec — this just makes the boundaries explicit.

### Priority 5: Evaluate whether the Architect Agent adds value

The original locks data models, API contracts, and ADRs before implementation. Our Implementer makes all these decisions. For simple specs this is fine. For complex specs (multi-service, shared types, database schemas), this means the Implementer is doing too many jobs at once.

**Action:** After the 10-run measurement pass (Priority 3), assess whether implementation failures correlate with architectural complexity. If they do, add an Architect step that locks contracts before implementation. If they don't, defer.

---

## Status of Existing Plans

| Plan | Status | Remaining Work |
|------|--------|----------------|
| add-failure-classification | COMPLETE | None |
| defer-enterprise-features | COMPLETE | None |
| resolve-skills-barrier-contradiction | COMPLETE | None |
| add-test-engineer-agent | DEFERRED | Need 10 runs for measurement gate |
| scenario-quality-improvements | Applied (commit `d39524a`) | Partially effective; Railway schema issue persists |
| scenario-quality-fix | Mostly implemented | Steps 1-2 (crash classification, crash-adjusted rates), 4 (tsc --noEmit), 5 (Oracle crash context), 6 (prompt condensation) done. Step 3 (multi-run forwarding) wired but defaulting to N=1. |

---

## Comparison: Original 11 Agents vs. Our 7 Agents

| Original Agent | Our Equivalent | Gap |
|----------------|---------------|-----|
| Product Owner | None (human + `/df-write-spec`) | No AI spec generation from triggers |
| UX Designer | None | No design phase at all |
| Architect | None | No locked contracts before implementation |
| Test Engineer | None (Implementer self-tests) | Deferred; prompt enhancement applied |
| QA-Holdout | Scenario Architect | Present and functional |
| Developer | Implementer | Present and functional |
| Validator | Quality Gate (bash script) | Present but fragile (bash/Python hybrid) |
| Factory-Lead | Python orchestrator | Present (procedural code, not AI agent) |
| Code Reviewer | Reviewer agent | Present and functional |
| Monitor/Dashboard | Event files + cost tracking | No synthesis agent |
| Defect Classifier | Oracle (integrated) | Present as Oracle capability |

**Key insight:** The three missing pre-implementation agents (Product Owner, UX Designer, Architect) are exactly the ones that produce the detailed specs the original relies on for high holdout pass rates. Their absence is the root cause of our scenario quality problem.

---

## Community Context

The Dark Factory pattern is actively evolving. Key reference points:

- **StrongDM** (3-person team, 32k lines, zero human code/review since July 2025) — the production benchmark
- **FSPEC** (GitHub, 58 stars) — spec-driven multi-agent factory with Gherkin scenarios
- **Fabro** (GitHub, 574 stars) — deterministic workflow graphs, human-in-the-loop gates
- **HackerNoon article** (Saumya Tyagi, Feb 2026) — "The wall is the whole game"
- **Stanford Law CodeX** — governance concerns about autonomous code generation

Common pitfalls across the community:
1. Specification quality is the bottleneck (matches our experience exactly)
2. Agent reward hacking (our barriers address this well)
3. Shared AI blind spots (SA and Implementer using same model)
4. Brownfield resistance (not yet tested — our runs are greenfield)

---

## Bottom Line

The original achieved 96% holdout pass rate on first try because the spec had 55 explicit criteria for a single feature. Our specs have far fewer constraints, and no amount of SA prompt tuning will compensate for that gap.

**Priority order:**
1. ~~Fix specs (Free Variables, enrichment, stricter validation)~~ **IMPLEMENTED** (2026-03-28)
2. ~~Fix quality gate (port bash to Python)~~ **DEFERRED** pending measurement (Liotta + Neo review: bash bugs are fixed, ship rate is the real problem)
3. Generate data (10 pipeline runs) — **NEXT**: run 3 specs with Free Variables populated, measure ship rate improvement
4. Evaluate deferred decisions with data (Test Engineer, Architect, multi-run)

---

## Implementation Log (2026-03-28)

### Priority 1: Free Variables Pipeline — COMPLETE

Reviewed by Liotta, Neo, Linus across 4 implementation steps. Each step reviewed and iterated before commit.

| Commit | Step | Description |
|--------|------|-------------|
| `726fec7` | 1.1 | Added `## Free Variables` section to all 8 spec templates + DO-NOT-ASSERT directive to SA prompt |
| `bfc0d16` | 1.2 | Added implicit free variables default for config/manifest specs (pkg manager format, deployment schemas, build scripts) |
| `b14f7ff` | 1.3 | Added `_extract_free_variables()` to orchestrator.py, piping FVs into SA execution context |
| `aa9dcbf` | 1.4 | Added 17 unit tests for extraction function (regex, fallback, boundary, edge cases) |

**Key review findings incorporated:**
- Fixed regex to handle numbered headings (`## 12. Free Variables`) — Linus caught that `\s*free` won't match `12.`
- Broadened identifier check to accept both `FV-N` (spec-authored) and `FR-N` (validator-detected) — Neo caught that validation reports use FR-N
- Changed implicit FV trigger from "when spec declares none" to "regardless of other free variables" for config specs — Linus review
- Added "unless spec constrains" escape clauses to build script and deployment schema bullets — both reviewers
- Removed items redundant with existing Structured File Assertions rules — Linus review
- Renamed misleading test names (`test_none_sentinel` → `test_body_without_identifiers_returns_empty`) — both reviewers
- Added 5 missing test cases (FR-N in spec, prose-only body, empty file, fallback-through-sentinel, missing spec + validation) — both reviewers

### Priority 2: Quality Gate Python Port — DEFERRED

**Rationale:** Bash bugs are fixed. The 0% ship rate is caused by scenario quality, not gate bugs. Porting ~1,500 lines of bash to Python is a refactor with zero feature value until the core pipeline ships specs. Decision preserved in `implementation-plan.md` with Neo's architectural feedback for future activation.

**Activation criteria:** Phase 1.5 measurement shows improved ship rate AND new bash bugs emerge in subsequent pipeline runs.
