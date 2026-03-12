---
name: df-implement
description: Implement code from a validated Dark Factory specification
argument-hint: "[spec-name]"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. For full information barrier compliance, use `./scripts/dark-factory.sh`.

# Implement from Specification

## CRITICAL: Information Barrier
This skill MUST be run in a Claude Code session that has NOT seen any test scenarios from `.dark-factory/scenarios/`. If you are in a session where scenarios have been discussed, viewed, or generated, STOP and instruct the user to open a new Claude Code session for implementation.

You MUST NOT read any file in `.dark-factory/scenarios/`. This is a hard rule.

## Steps

1. Read the agent definition at `agents/implementer.md`
2. Read the validated specification from `.dark-factory/specs/`
3. Survey the existing codebase for conventions:
   - Naming patterns
   - File structure
   - Error handling patterns
   - Test patterns
4. Follow the Implementation Protocol from the agent definition:
   - Phase 1: Comprehension
   - Phase 2: Design
   - Phase 3: Implementation
   - Phase 4: Self-Testing
   - Phase 5: Polish
5. Write implementation code following existing conventions
6. Write self-tests based on your reading of the spec
7. Run lint, type check, and self-tests
8. Produce the implementation notes document

## Pre-flight Checks
Before proceeding, verify ALL of the following. STOP with a clear error if any fails.
1. **Spec file exists:** `.dark-factory/specs/[spec-name].md` is present
2. **Spec is validated:** `.dark-factory/reviews/[spec-name]-validation.status` has `verdict=PASS`, `score=100`, and `divergence` is not `HIGH`. If the `divergence` key is missing, STOP and tell the user to re-run `/df-validate-spec` (the spec was validated before the Precision Analysis was added). If missing entirely, check the markdown report for the score. If neither shows 100%, STOP and tell the user to run `/df-validate-spec`.
3. **Session is clean:** You have NOT read any file in `.dark-factory/scenarios/` in this session
4. **Config present (warn only):** `.dark-factory/config.sh` exists
5. **Detect iteration state:** Check for feedback files matching `.dark-factory/reviews/[spec-name]-feedback-iter*.md`. If any exist, this is an iteration run — see [Iteration Mode](#iteration-mode) below.

## Prerequisites
- Spec must be validated (PASS status)
- MUST NOT have seen scenario files in this session

## Iteration Mode

At pre-flight, scan for feedback files at `.dark-factory/reviews/[spec-name]-feedback-iter*.md`. If found:

1. **Determine iteration number:** Find the highest `N` in `feedback-iter{N}.md`. You are now implementing iteration `N + 1`.
2. **Report to user:** "Feedback file found for iteration N. Entering iteration N+1 mode."
3. **If N >= 3:** STOP. Tell the user: "Three iterations completed without shipping. This requires human review. See the escalation section in the iteration 3 feedback file."

### Iteration Steps

1. Read the **feedback file** at `.dark-factory/reviews/[spec-name]-feedback-iter[N].md` (the latest one)
2. Read the **spec** — re-read every spec section referenced in the feedback's "Spec Section Failures" table
3. Read the **existing implementation** to understand current state
4. **Fix non-scenario gate failures first** — build, lint, and type errors from the "Non-Scenario Gate Failures" section are safe to fix directly
5. **Fix spec-section failures** in the priority order given by the feedback's "Priority-Ordered Fix List" — re-derive the correct behavior from the spec, NOT from the feedback's behavioral category descriptions
6. **Consult the "Root Cause Analysis"** for patterns, but implement fixes based on what the spec says, not on what you infer the tests expect
7. **Avoid regressions** — the feedback's "What Worked" section lists passing areas. Do not modify code in those areas unless required by a fix
8. Rerun self-tests
9. Produce updated implementation notes mentioning what changed in this iteration

### Information Barrier (Iteration Mode)

The feedback file has been filtered by `/df-write-feedback` to remove scenario details. Even so:
- **DO NOT** attempt to infer test structure from the feedback
- **DO NOT** read `.dark-factory/scenarios/`, `.dark-factory/reports/`, or `.dark-factory/audit/`
- **DO** fix behavior based on the spec text, not on guesses about what tests check

## Output
Implementation code, self-tests, and implementation notes.
