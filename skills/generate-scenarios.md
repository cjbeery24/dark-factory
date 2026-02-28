# Skill: Generate Blind Test Scenarios

## Trigger
User wants to generate blind test scenarios for a validated specification.

## CRITICAL: Information Barrier
This skill MUST be run in a Claude Code session that has NOT seen any implementation code. If you are in a session where implementation has been discussed or viewed, STOP and instruct the user to open a new Claude Code session for scenario generation.

## Behavior

1. Read the agent definition at `agents/scenario-architect.md`
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

## Prerequisites
- Spec must be validated (PASS status)
- Must know the project's test framework
- MUST NOT have seen implementation code in this session

## Output
Executable test files in `.dark-factory/scenarios/` and a generation report.

## Key Phrases
- "generate scenarios", "create blind tests", "write test scenarios"
- "scenario generation for [spec]"
