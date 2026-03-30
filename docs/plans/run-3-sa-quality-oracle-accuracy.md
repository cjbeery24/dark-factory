# Plan: Run 3 — Mechanical BRITTLE Detection + SA Quality (v2)

## Problem Statement

Run 2 ESCALATED with 80% pass rate (14 failures). 8 of 14 failures (57%) are SCENARIO_BUGs. The Oracle had full guidance on BRITTLE classification (stable failure analysis, decision taxonomy, SCENARIO-DOMINATED PLATEAU warning) and still classified everything as IMPL_BUG.

**Lesson from Run 2:** Prompt engineering for classification doesn't work — the Oracle already had every instruction it needed. Mechanical enforcement is needed.

## Reviewer Feedback (Liotta + Neo)

- **Liotta:** "Four prompt patches that don't compound." SA already has 259 lines including rules against every observed pattern. Oracle already gets stable failure analysis at lines 836-863. Build mechanical enforcement that compounds forever.
- **Neo:** Change 4 is redundant (already exists). Verify per-test JSON is generated correctly. BRITTLE escalation threshold interaction is desirable.
- **Data finding:** Per-test `error_type` is hardcoded to `"assertion"` in quality-gate.sh:503. Crash detection only works in aggregate, not per-test. TypeErrors from vitest assertion chains aren't caught because ANSI codes interfere with the crash regex.

## Changes (ordered by impact)

### Change 1: Fix per-test error_type classification in quality gate
**File:** `scripts/quality-gate.sh` (~line 503)

Currently hardcoded: `"error_type": "assertion"`. Fix: check if the test's failure output contains a crash-class error pattern.

The crash regex already exists (line 429): `^\s*(?:TypeError|ReferenceError|SyntaxError|Cannot find module|...):`

Apply the same regex to each test's failure block to classify per-test. This needs to match against the test's specific error output, not just the aggregate.

**Impact:** Enables mechanical pre-classification. TypeErrors from undefined property access (like `railwayBuildCmd.toMatch()`) get classified as crashes, not assertion failures.

### Change 2: Mechanical pre-classification of crashes in Oracle prompt
**File:** `darkfactory/pipeline/iteration.py`

When building the Oracle prompt, if a failing test has `error_type: "crash"` in the per-test results, pre-classify it as SCENARIO_CRASH in the CLASSIFICATIONS block that gets injected into the Oracle's context. Tell the Oracle: "These tests crashed (not assertion failures) — classify as SCENARIO_CRASH and exclude from implementation feedback."

```python
# In _run_oracle(), after parsing per-test results:
crash_tests = [t for t in per_test_results if t.get("error_type") == "crash"]
if crash_tests:
    prompt += (
        f"\n\n## Pre-Classified Crashes\n"
        f"The following {len(crash_tests)} test(s) crashed with runtime errors "
        f"(TypeError/ReferenceError/SyntaxError). These are scenario code bugs, "
        f"NOT implementation failures. Classify them as SCENARIO_CRASH in your "
        f"CLASSIFICATIONS block and do NOT include them in implementation feedback:\n"
    )
    for t in crash_tests:
        prompt += f"- {t['name']}\n"
```

### Change 3: Add Free Variables to budget-brain spec
**File:** `~/sites/budget-brain/.dark-factory/specs/2026-03-24-project-scaffolding.md`

Add FV-5 through FV-7:
```markdown
- **FV-5: React import style** — Whether components use `import React from 'react'` (legacy) or named imports via JSX transform (React 19+). Do NOT assert on import syntax.
- **FV-6: TypeScript composite mode** — Whether tsconfig files include `composite: true` explicitly vs inheriting it from a base config or not using it at all.
- **FV-7: Vite resolve configuration** — Whether cross-package imports use `resolve.alias` in vite.config.ts or `paths` in tsconfig.json. Both are valid approaches.
```

### Change 4: Add 2 critical anti-patterns to SA Common Pitfalls
**File:** `agents/scenario-architect.md`

Add only to the existing "Common Pitfalls" section (not a new section):
```markdown
- **Regex quality on file content:** When using `toMatch()` on multi-line file content, always use the multiline flag (`/m`) if your pattern uses `^` or `$` anchors. Without `/m`, `$` means end-of-string, not end-of-line.
- **Modern framework patterns:** Do NOT assert `import React from 'react'` — React 19's JSX transform eliminates this requirement. Do NOT assert specific config key nesting in JSON files (e.g., `build.command` vs `buildCommand`) — test for the presence of required values, not exact key paths.
```

## NOT doing (per reviewer feedback)
- Oracle decision tree — Oracle already has full classification guidance and ignored it
- Change 4 from v1 (stable failure context) — already exists at iteration.py:837-880
- SA re-generation or programmatic scenario validation
- Deterministic scenario seeding
- Source-file-regex BRITTLE detection — good idea but higher complexity; try simpler changes first

## Implementation Order
1. Change 1 (quality-gate.sh — per-test error_type fix)
2. Change 2 (iteration.py — mechanical pre-classification)
3. Change 4 (SA prompt — 2 anti-patterns, ~6 lines)
4. Change 3 (budget-brain spec — Free Variables, no code change)

## Verification
1. Unit test for per-test error classification
2. `python3 -m pytest tests/ -v` — full suite
3. Re-run pipeline on budget-brain
