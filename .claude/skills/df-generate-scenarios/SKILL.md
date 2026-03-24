---
name: df-generate-scenarios
description: Generate blind test scenarios for a validated Dark Factory specification
argument-hint: "[spec-name]"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. For full information barrier compliance, use `dark-factory run <spec>`.

# Generate Blind Test Scenarios

## CRITICAL: Information Barrier
This skill MUST be run in a Claude Code session that has NOT seen any implementation code. If you are in a session where implementation has been discussed or viewed, STOP and instruct the user to open a new Claude Code session for scenario generation.

## Steps

1. Read the agent definition at `.dark-factory/agents/scenario-architect.md`
2. Read the validated specification from `.dark-factory/specs/`
3. Identify the project's test framework and patterns
4. Generate comprehensive test scenarios across all 8 layers:
   - Layer 1: Contract Verification
   - Layer 2: Happy Path Coverage
   - Layer 3: Edge Cases & Boundaries
   - Layer 4: Error Conditions
   - Layer 5: State & Sequence
   - Layer 6: Security
   - Layer 7: Property-Based
   - Layer 8: Adversarial
5. Write executable test files to `.dark-factory/scenarios/[spec-name]/`
6. Write the scenario generation report

## Pre-flight Checks
Before proceeding, verify ALL of the following. STOP with a clear error if any fails.
1. **Spec file exists:** `.dark-factory/specs/[spec-name].md` is present
2. **Spec is validated:** `.dark-factory/reviews/[spec-name]-validation.status` has `verdict=PASS`, `score=100`, and `divergence` is not `HIGH`. If the `divergence` key is missing, STOP and tell the user to re-run `/df-validate-spec` (the spec was validated before the Precision Analysis was added). If missing entirely, check the markdown report for the score. If neither shows 100%, STOP and tell the user to run `/df-validate-spec`.
3. **Session is clean:** You have NOT read any implementation source code in this session
4. **Test framework identifiable:** Can determine the project's test framework from config or project files
5. **Agent definition accessible:** `.dark-factory/agents/scenario-architect.md` exists. If missing, tell the user to run `dark-factory init --update`.
6. **Config present (warn only):** `.dark-factory/config.toml` exists

## Prerequisites
- Spec must be validated (PASS status)
- Must know the project's test framework
- MUST NOT have seen implementation code in this session

## Output
Executable test files in `.dark-factory/scenarios/` and a generation report.
