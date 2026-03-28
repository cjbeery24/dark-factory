# Implementation Plan: Free Variables Pipeline + Quality Gate Python Port

**Status:** PRIORITY 1 COMPLETE, PRIORITY 2 DEFERRED (v3 — implemented + reviewed by Linus + Neo)
**Date:** 2026-03-28
**Scope:** Priority 1 (spec quality via Free Variables), Priority 2 (quality gate Python port — DEFERRED pending measurement)
**Parent:** [Gap Analysis](gap-analysis.md)
**Reviewed by:** Liotta, Neo

---

## Review Summary (v1 → v2)

### Liotta Review
- **CUT Steps 1.2 and 1.6** — Validator already detects free variables (lines 93-123 of `agents/spec-validator.md`); adding scoring/precheck adds noise, not signal. The Validator's existing Precision Analysis catches under-specification through the 3-sketch divergence test.
- **DEFER Priority 2 entirely** — Bash bugs are fixed. The 0% ship rate is caused by scenario quality, not gate bugs. Porting bash to Python doesn't ship a single spec. Ship Free Variables first, measure, then decide.
- **ADD measurement gate** between Priority 1 and Priority 2. Run 3 specs. If ship rate > 0%, proceed. If 0%, the problem is elsewhere — stop and reassess.
- **ADD implicit free variables default** for config/manifest specs — defensive fallback when spec authors write poor Free Variables sections.
- **ADD vague scope handling** — SA needs instruction: "If a Free Variable is vaguely scoped, treat the entire domain as free."

### Neo Review
- **P0: `darkfactory` package not installable inside Docker** — gate_runner.py must be zero-import from `darkfactory.*`, or pip + `tomli-w` need adding to Dockerfile.base. `compute_satisfaction()` imports `PipelineConfig` which imports `tomli-w` — not available in Alpine containers.
- **P0: `GateRunResult` incompatible with `GateResult`** — iteration loop, Oracle, and reviewer all consume `GateResult`. Need explicit adapter layer.
- **P1: Smoke test mode not addressed** in Python runner. `run_smoke_test()` in `quality_gate.py` calls bash with `--smoke`.
- **P1: `runner.py` already has scenario command building** — extend it, don't reimplement in gate_runner.py.
- **P1: Free Variables regex "None" check too narrow** — table headers will be present even when body says "None." Use positive check for `FV-` identifiers instead.
- **P2: No parity test harness** — run both gates on same input, diff reports.
- **P2: Build failure behavior unspecified** — bash runs all gates even after build failure; Python must match.
- **P3: Regex only matches `##`, not `###`** — use `#{2,3}` for permissiveness.

### Changes Made
- Removed Steps 1.2 (Validator scoring) and 1.6 (precheck warning)
- Added implicit free variables default for config/manifest specs to SA prompt (Step 1.2, renumbered)
- Added measurement gate (Phase 1.5) between Priority 1 and Priority 2
- Marked Priority 2 as DEFERRED pending measurement
- Updated regex to use positive `FV-` check and `#{2,3}` heading match
- Preserved Priority 2 plan for future execution with Neo's architectural feedback incorporated

---

## Priority 1: Free Variables Pipeline (Spec Quality)

### Problem

The Scenario Architect generates bad scenarios because specs don't contain enough information. 18 of 19 persistent failures are scenario-side. The SA doesn't know which values are unconstrained because the spec doesn't say.

### Current State

The Spec Validator already has a **Free Variable Detection** section (lines 93-123 of `agents/spec-validator.md`) that outputs a Free Variables table in the validation report. However:
- Spec templates do not have a `## Free Variables` section
- The SA prompt does not reference Free Variables
- The SA execution context does not pipe Free Variables from the validation report
- The Spec Validator treats Free Variables as purely informational

### Step 1.1: Add `## Free Variables` section to all spec templates + SA prompt

**Files:**
- All templates in `templates/` (feature, api, bug-fix, refactor, integration, db-migration, performance, security-fix)
- `agents/scenario-architect.md`

**Template change:** Add after the last section in each template:

```markdown
## Free Variables (implementation choices -- DO NOT assert in scenarios)

List implementation decisions this spec intentionally leaves unconstrained.
The Scenario Architect will treat these as DO-NOT-ASSERT directives.

| ID | Variable | What Is Constrained | What Is Free |
|----|----------|-------------------|--------------|
| FV-1 | [e.g., Config schema structure] | [e.g., Must contain build and start commands] | [e.g., JSON key nesting (flat vs nested)] |

If no implementation choices are free, write "None -- all observable behaviors are fully constrained."
```

**SA prompt change:** Add after Critical Rule 2 in `agents/scenario-architect.md`:

```markdown
   **Free Variables:** If the spec includes a `## Free Variables` section or the execution
   context lists Free Variables, treat each listed item as a DO-NOT-ASSERT directive. These
   are implementation choices the spec intentionally leaves unconstrained. Before writing any
   assertion, check whether the asserted property falls within a declared free variable's scope.
   If it does, either: (a) delete the assertion, or (b) rewrite it to test the constrained
   aspect only (e.g., verify the file exists and is valid JSON, but not its key structure).

   If a free variable is vaguely scoped ("config format"), treat the entire domain as free.
```

**Verification:** Each template has the section. SA prompt has the directive.
**Scope:** ~15 lines x 8 templates + ~10 lines SA prompt. Small.

### Step 1.2: Add implicit free variables default for config/manifest specs

**File:** `agents/scenario-architect.md`

Add to the SA prompt (in the Free Variables section or as a separate rule):

```markdown
   **Implicit Free Variables (when spec declares none):** When a spec creates config files,
   JSON schemas, YAML manifests, or deployment configs and does NOT declare any free variables,
   treat the following as implicitly free:
   - Key ordering and nesting depth in JSON/YAML config files
   - Whitespace, quoting style, and comment formatting
   - Import syntax choices (require vs import, default vs named)
   - Package manager workspace config format (package.json workspaces vs pnpm-workspace.yaml)
   - Deployment platform config schemas (Railway, Vercel, Netlify) unless the spec defines the exact schema
   - Build/lint/test script exact command strings (any valid command is acceptable)
```

**Verification:** Manual review.
**Scope:** ~10 lines. Small.

### Step 1.3: Pipe Free Variables into SA execution context

**File:** `darkfactory/pipeline/orchestrator.py`

Add `_extract_free_variables(project_dir, spec_name)` that reads the spec file first (author-provided Free Variables take precedence), then falls back to the validation report. Append to the SA's execution context block.

```python
def _extract_free_variables(project_dir: Path, spec_name: str) -> str:
    """Extract Free Variables section from spec or validation report."""
    for source in [
        project_dir / ".dark-factory" / "specs" / f"{spec_name}.md",
        project_dir / ".dark-factory" / "reviews" / f"{spec_name}-validation.md",
    ]:
        if source.exists():
            content = source.read_text(encoding="utf-8", errors="replace")
            match = re.search(
                r'(?i)#{2,3}\s*free\s+variables.*?\n(.*?)(?=\n#{1,3}\s|\Z)',
                content, re.DOTALL,
            )
            if match:
                body = match.group(1).strip()
                # Positive check: only return if body contains substantive FV entries
                if body and re.search(r'\bFV-\d+\b', body):
                    return body
    return ""
```

Inject into SA context (in `_step_scenario_generation`, after building the context block):
```python
free_vars = _extract_free_variables(project_dir, spec_run.spec_name)
if free_vars:
    context_block += "\n## Free Variables (DO NOT ASSERT on these)\n\n"
    context_block += free_vars
    context_block += (
        "\n\nEach free variable above represents an implementation choice the spec "
        "intentionally leaves unconstrained. Do NOT write assertions that depend on "
        "how these choices are resolved.\n"
    )
```

**Key design decisions (from review):**
- Regex uses `#{2,3}` to match both `##` and `###` headings (Neo P3)
- Lookahead uses `\n#{1,3}\s` to stop at the next heading of any level
- Positive check for `FV-` identifiers instead of negative "None" check (Neo P1)
- Reads spec file first, validation report as fallback
- Uses `errors="replace"` for encoding safety

**Verification:** Unit test + audit log inspection.
**Scope:** ~40 lines. Medium.

### Step 1.4: Add unit tests for Free Variables extraction

**File:** `tests/unit/test_free_variables.py` (new)

Test cases:
- Spec with `## Free Variables` containing FV-1, FV-2 entries -> extracted
- Spec with `### Free Variables` (subsection) -> extracted (regex matches `###`)
- Spec without, validation report with Free Variables -> fallback extracted
- Neither has Free Variables -> empty string
- Free Variables section with only table headers and "None" row -> empty string (no `FV-` match)
- Free Variables as last section (no trailing `##`) -> extracted (regex `\Z` handles this)
- Spec with FV entries + extra prose -> full body extracted
- Empty spec file -> empty string

**Scope:** ~80 lines. Small.

---

## Phase 1.5: Measurement Gate

**After completing Steps 1.1-1.4, run 3 diverse specs through the pipeline:**

1. A scaffolding spec (like budget-brain's project-scaffolding) with Free Variables populated
2. An API feature spec with Free Variables
3. A library/refactor spec

**Track:**
- First-iteration scenario pass rate (target: > 85%, up from 80-82%)
- Failure classification distribution (target: < 30% SPEC_AMBIGUOUS/BRITTLE, down from ~80%)
- Ship rate (target: > 0%, up from 0%)

**Decision gate:**
- If ship rate > 0%: Priority 2 is worth pursuing as a reliability investment
- If ship rate = 0% but pass rate improved: iterate on spec/SA quality, not gate infrastructure
- If ship rate = 0% and pass rate unchanged: the problem is elsewhere — reassess entirely

---

## Priority 2: Quality Gate Python Port (DEFERRED)

**Status:** DEFERRED pending Phase 1.5 measurement results.

**Rationale (Liotta review):** The bash bugs are fixed. The 0% ship rate is caused by scenario quality, not gate bugs. Porting bash to Python is ~1,500 lines of new code with the highest-risk step in the plan (Docker entrypoint switch), and its success criteria ("identical reports to bash") means it's a refactor with zero feature value until the core pipeline actually ships specs.

**When to activate:** After Phase 1.5 shows improved ship rate AND new bash bugs emerge in subsequent pipeline runs. If the bash stays stable for 5+ runs, deprioritize further.

**Preserved design decisions (from Neo review, for future execution):**
- gate_runner.py must be zero-import from `darkfactory.*` (no `PipelineConfig`, no `tomli-w` dependency)
- `GateRunResult` needs explicit adapter to `GateResult` for downstream consumers
- Smoke test mode and pre-gate mode must be ported
- Extend `darkfactory/scenarios/runner.py` for runner resolution, don't reimplement
- Run all gates even after build failure (match bash behavior)
- Add parity test harness (bash vs Python output diff)
- `scripts/lib/` bash files remain needed for skills workflows during transition
- `DF_USE_BASH_GATE=1` fallback env var for rollback

Full step-by-step plan preserved in [priority-2-python-gate-deferred.md](priority-2-python-gate-deferred.md) for future activation.

---

## Execution Order

```
Phase 1 -- Free Variables (4 commits):
  Step 1.1 (templates + SA prompt)  ──── commit 1
  Step 1.2 (implicit defaults)      ──── commit 2
  Step 1.3 (pipe to SA context)     ──── commit 3
  Step 1.4 (unit tests)             ──── commit 4

Phase 1.5 -- Measurement Gate:
  Run 3 specs through pipeline
  Analyze results
  Decision: proceed to P2 or iterate on P1

Phase 2 -- Python Gate (DEFERRED):
  Activate only after Phase 1.5 + new bash bugs
```

---

## Risk Assessment

| Risk | Severity | Mitigation |
|------|----------|------------|
| SA ignores Free Variables | Medium | Execution context injection is additive; Rule 2 already covers this; implicit defaults cover common case |
| Spec authors write vague Free Variables | Low | SA instruction: "If vaguely scoped, treat entire domain as free" |
| Spec authors skip Free Variables entirely | Low | Implicit defaults cover config/manifest specs; Validator already detects free variables |
| Free Variables regex misses edge cases | Low | Positive `FV-` check + `#{2,3}` heading match + unit tests |

---

## Success Criteria

### Priority 1
- Every spec template has `## Free Variables` section
- SA prompt references Free Variables as DO-NOT-ASSERT directives
- SA prompt has implicit free variables default for config/manifest specs
- SA execution context includes Free Variables when present in spec or validation report
- Unit tests pass for extraction function
- Extraction handles `##` and `###` headings, "None" entries, missing sections

### Phase 1.5
- 3 pipeline runs completed with Free Variables populated
- Scenario pass rate measurably improved (target: > 85%)
- At least 1 spec ships (target: ship rate > 0%)
