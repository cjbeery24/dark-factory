---
name: df-write-feedback
description: Translate a quality gate report into barrier-safe iteration feedback for the implementer
argument-hint: "[spec-name] [iteration-number]"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. For full information barrier compliance, use `dark-factory run <spec>`.

# Write Iteration Feedback

## Purpose

After `/df-run-quality-gate` produces a NO-SHIP report, this skill translates the quality gate results into barrier-safe feedback that can be given to the implementer. It acts as the Oracle's feedback-writing function — reading the full report (which contains scenario names, assertion details, and raw test output) and producing a filtered feedback file that references only spec sections and behavioral categories.

## CRITICAL: Information Barrier Enforcement

The output feedback file **MUST NOT** contain:
- Scenario filenames (e.g., `test_02_happy_path.py`, `01-contract.test.ts`)
- Test class or method names (e.g., `TestAnalyzeTestResultsHappyPath::test_gate_outcomes_parsed`)
- Assertion messages or expected/actual values from test output
- Raw test runner output or stack traces
- Layer references with scenario context (e.g., "layer 02", "scenario 03")
- Blind scenario counts or totals (e.g., "172/173 passed", "171+ scenarios pass") — the implementer must not know how many blind scenarios exist

The output feedback file **MUST** contain:
- Spec section references (FR-N, EC-N, AC-N, NFR-N) with behavioral categories
- Non-scenario gate failures (build, lint, type check errors) verbatim — these are NOT barrier-sensitive
- Priority-ordered fix list anchored to spec sections
- Pattern analysis using implementation-approach terms, not scenario content

## Pre-flight Checks

Before proceeding, verify ALL of the following. STOP with a clear error if any fails.

1. **Spec file exists:** `.dark-factory/specs/[spec-name].md` is present
2. **Quality gate report exists:** `.dark-factory/reports/[spec-name]-quality-gate.md` is present
3. **Report indicates NO-SHIP:** The report contains at least one FAIL gate
4. **Iteration number provided:** User must specify which iteration this feedback is for (1, 2, or 3). If iteration is 3, produce escalation output instead of implementer feedback.

## Steps

### Step 1: Read Inputs

1. Read the **spec** at `.dark-factory/specs/[spec-name].md` — build a map of all spec section IDs (FR-N, EC-N, AC-N, NFR-N) and their behavioral descriptions
2. Read the **quality gate report** at `.dark-factory/reports/[spec-name]-quality-gate.md`
3. If it exists, read the **scenario generation report** at `.dark-factory/reports/[spec-name]-scenario-generation.md` for the spec-section-to-scenario mapping (this helps you translate scenario failures back to spec sections without leaking scenario details)

### Step 2: Classify Failures

For each failure in the quality gate report:
1. **Non-scenario gate failures** (build, lint, type check, self-test): Keep verbatim — these are safe
2. **Blind scenario failures**: Map each failure back to its spec section(s) using:
   - The failure's "Category" annotation (e.g., `FR-1`, `EC-9`, `AC-12`) if present in the report
   - The behavioral description of what failed (strip scenario-specific details)
   - The scenario generation report's spec-section mapping if available
3. Group failures by spec section and determine severity:
   - **Critical**: Core functional requirements (FR-N) failing
   - **High**: Acceptance criteria (AC-N) or error conditions (EC-N) failing
   - **Medium**: Edge cases, security precision issues
   - **Low**: Adversarial/robustness scenarios

### Step 3: Identify Root Causes

Analyze grouped failures for patterns:
- Are multiple failures traceable to the same implementation gap?
- Is there a systematic issue (e.g., "gate summary parsing not implemented") vs. isolated bugs?
- Express root causes in terms of **implementation approach** (e.g., "the function defaults to True instead of conservatively defaulting to False"), NOT in terms of test expectations
- **Avoid specifying the exact fix when the spec contains enough detail for the implementer to derive it.** Describe the category of the problem and point to the relevant spec text. If the spec lacks the detail needed to derive the fix from a category-level hint alone, it is acceptable to be more specific — but never include content sourced from scenario output (assertion values, test input data, scenario structure).

### Step 3.5: Inject Manifest Contract Context (barrier-safe)

For each priority-ordered fix item, cross-reference the architect manifest at `.dark-factory/architecture/[spec-name]-manifest.yaml` and inject the relevant public-surface contract. This turns abstract "re-read FR-N" feedback into specific, actionable guidance without leaking test content.

1. If the manifest does not exist or has no `test_integration.entry_points`, skip this step.
2. Otherwise, for each fix item:
   - Build a keyword set from the cited spec-section text and the fix item's behavioral-category phrase (e.g., "boundary condition handling").
   - Scan each `entry_points[*]` in `test_integration`. Consider an entry point relevant if any of its `exports` names or its `note` field shares a non-trivial keyword with the fix-item keyword set (case-insensitive; ignore stopwords like "the", "and", "a"). A dedicated `spec_sections` field on the entry point, if present, takes precedence over keyword matching.
   - For each relevant entry point, append a single bullet to the fix item containing, verbatim from the manifest:
     - `module_system` and `import_extension` from `test_integration`
     - the entry point's `specifier`
     - the entry point's `exports` list (named exports, comma-separated)
3. Do **not** quote:
   - Implementation source code or file contents.
   - Scenario file names, test names, or assertion values.
   - The entry point's `path` (filesystem location — may leak internal layout choices). Only the `specifier`, which is the contract the Implementer is required to honor, is safe to cite.
4. The manifest is the pre-implementation contract both the Architect and Implementer already see; quoting its contract fields is zero-new-information but makes the feedback concrete.

Example injected bullet:
> - **Contract reminder (from architect manifest):** This behavior must be reachable through the named exports `parseRecord, validateRecord` on the specifier `../../../apps/api/src/parser.js` under `module_system=ESM`, `import_extension=.js`. Verify runtime resolution with a scratch import before submitting.

### Step 4: Write Feedback File

Write to `.dark-factory/reviews/[spec-name]-feedback-iter[N].md` using this exact format:

```markdown
# Iteration Feedback
**Spec:** [spec-name]  **Iteration:** [N] of 3

## Non-Scenario Gate Failures
[Build/lint/type errors verbatim, or "None" if all non-scenario gates passed]

## Spec Section Failures
| # | Spec Section(s) | Behavioral Category | Failure Count | Severity |
|---|---|---|---|---|
| 1 | FR-2, EC-3 | [e.g., "boundary condition handling"] | 3 | Critical |

## Priority-Ordered Fix List
1. **[Spec section]** — Re-read this section. [What category of behavior to re-examine. Point to the spec text that defines the expected behavior — prefer category-level hints over exact fixes.]

## Root Cause Analysis
[Patterns in terms of implementation approach, NOT scenario content]

## What Worked
[Passing areas by spec section — avoid regression]
```

### Step 5: Self-Validate

After writing the feedback file, validate it against the leakage patterns from `darkfactory/audit/checker.py`:
1. Check for scenario filenames matching `\b\d{2}-[\w-]+\.(?:test|spec|scenario)\.\w+` or `\btest_\d{2}_\w+\.py\b`
2. Check for assertion calls matching `\b(?:expect|assert|should)\s*[.(]`
3. Check for test DSL matching `\b(?:describe|it|test)\s*\(\s*["']`
4. Check for stack traces matching `at\s+\w+\s+\(.*:\d+:\d+\)`
5. Check for layer references matching `\b(?:layer|scenario)\s+\d{2}-`
6. **Unprefixed scenario-file paste check** (catches Step 3.5 misuse): grep for `[\w-]+\.(?:test|spec)\.(?:ts|tsx|js|jsx|py)` — manifest `specifier` fields end in runtime extensions (`.js`, `.ts`) and are safe; any match here ending in `.test.*` or `.spec.*` is a scenario filename leak and must be removed.

If ANY pattern matches, **fix the feedback file** before finishing. Remove or rephrase the offending content.

If the `validate_oracle_feedback` function is available, run it programmatically:
```bash
python -c "
from darkfactory.audit.checker import validate_oracle_feedback
from pathlib import Path
feedback = Path('.dark-factory/reviews/[spec-name]-feedback-iter[N].md').read_text()
result = validate_oracle_feedback(feedback, Path('.dark-factory/audit'))
if not result.is_compliant:
    print('LEAKAGE DETECTED:')
    for v in result.violations: print(f'  - {v}')
else:
    print('CLEAN — no leakage patterns found')
"
```

### Step 6 (Iteration 3 Only): Escalation

If this is iteration 3, append an escalation section after the standard feedback:

```markdown
## Escalation to Human
### Summary of Attempts
- Iteration 1: [what was tried, what failed — from prior feedback files if available]
- Iteration 2: [what was tried, what failed]
- Iteration 3: [what was tried, what failed]

### Persistent Issues
[Issues that weren't resolved across iterations]

### Recommendation
[Continue iterating / Redesign spec / Human intervention needed]
```

## Output

Barrier-safe feedback file at `.dark-factory/reviews/[spec-name]-feedback-iter[N].md`, validated against leakage patterns. Report the validation result to the user.
