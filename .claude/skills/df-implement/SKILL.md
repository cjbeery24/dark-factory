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

## Prerequisites
- Spec must be validated (PASS status)
- MUST NOT have seen scenario files in this session

## Iteration Mode
If the user provides feedback from a failed quality gate:
1. Read the feedback (test results and reviewer comments)
2. DO NOT attempt to find or read test scenario source files
3. Fix the root cause based on the error messages
4. Rerun self-tests
5. Produce updated implementation notes

## Output
Implementation code, self-tests, and implementation notes.
