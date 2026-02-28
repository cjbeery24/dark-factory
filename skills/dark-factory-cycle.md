# Skill: Dark Factory Full Cycle

## Trigger
User wants to run the complete Dark Factory pipeline for a specification.

## IMPORTANT: Session Isolation
The full cycle CANNOT run in a single Claude Code session because information barriers require separate sessions for the Scenario Architect and Implementer. This skill guides the user through the multi-session workflow.

## Behavior

### Step 1: Verify Spec
1. Check that the spec exists in `.dark-factory/specs/`
2. Check that the spec has been validated (look for validation report)
3. If not validated, run the validate-spec skill first

### Step 2: Guide Parallel Session Launch
Instruct the user to open TWO separate terminal windows/tabs:

**Terminal A — Scenario Generation:**
```bash
claude "You are the Scenario Architect. Read agents/scenario-architect.md
for your role definition. Generate blind test scenarios for the spec at
.dark-factory/specs/[SPEC_NAME].md. Write executable tests to
.dark-factory/scenarios/[SPEC_NAME]/. Use the project's test framework. Do NOT read
any implementation source code."
```

**Terminal B — Implementation:**
```bash
claude "You are the Implementer. Read agents/implementer.md for your
role definition. Implement the spec at .dark-factory/specs/[SPEC_NAME].md.
Follow existing codebase conventions. Write self-tests. Do NOT read
anything in .dark-factory/scenarios/."
```

### Step 3: Wait for Both to Complete
Tell the user to wait for both sessions to finish.

### Step 4: Run Quality Gates
In the current session (or a new one):
- Run build, lint, type check
- Run self-tests
- Run blind scenarios
- Produce quality gate report

### Step 5: Review
```bash
claude "You are the Reviewer. Read agents/reviewer.md for your role
definition. Review the implementation for spec at
.dark-factory/specs/[SPEC_NAME].md. Test results are at
.dark-factory/reports/[SPEC_NAME]-quality-gate.md. Write your review
to .dark-factory/reviews/[SPEC_NAME]-review.md."
```

### Step 6: Oracle Decision
```bash
claude "You are the Oracle. Read agents/oracle.md for your role
definition. Make a SHIP/NO-SHIP decision for spec
.dark-factory/specs/[SPEC_NAME].md. Review report is at
.dark-factory/reviews/[SPEC_NAME]-review.md. This is iteration [N]."
```

### Step 7: Handle Result
- **SHIP:** Congratulate, suggest committing and opening a PR
- **NO-SHIP (iteration < 3):** Extract feedback, guide user to re-run Terminal B (Implementation) with feedback
- **NO-SHIP (iteration 3):** Escalate to human review

## Output
Guided walkthrough of the complete pipeline with copy-paste commands for each session.

## Key Phrases
- "run dark factory", "full cycle", "run the pipeline"
- "dark factory for [spec]"
