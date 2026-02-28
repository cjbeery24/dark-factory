# Dark Factory Workflow Guide

## Overview

This document describes the complete Dark Factory workflow — from idea to shipped code — using Claude Code as your AI development tool.

## Prerequisites

- Claude Code installed and configured
- Git repository initialized for your project
- Test framework installed for your language (pytest, vitest, jest, go test, etc.)
- This dark-factory framework accessible (in your project or as a reference)

---

## The Complete Pipeline

### Step 0: Decide What to Build

Before entering the pipeline, you (the human) decide what needs to be built or changed. This is the only step that always requires human judgment.

**Planning a whole project?** Use `/df-write-plan` to make architectural decisions (tech stack, data model, directory structure) and decompose the project into ordered feature specs. The plan generates stub specs in `.dark-factory/specs/` that you complete with `/df-write-spec` before entering the pipeline. See `skills/write-plan.md` for details.

### Step 1: Write the Specification

**Who:** You (human), optionally assisted by Claude Code
**Time:** 10-30 minutes
**Output:** A spec file in `.dark-factory/specs/`

```bash
# Start from a template
cp templates/feature-spec.md .dark-factory/specs/my-feature.md

# Edit the spec (manually or with Claude Code assistance)
# Claude Code can help you think through edge cases and refine requirements
claude "Help me fill out this spec template for [feature description].
Read the template at .dark-factory/specs/my-feature.md and help me
complete each section. Ask me questions to fill gaps."
```

**Key principle:** Spend the most time here. A perfect spec produces perfect code in one iteration. A vague spec wastes cycles.

### Step 2: Validate the Specification

**Who:** Spec Validator agent
**Time:** 1-2 minutes
**Output:** Validation report

```bash
claude "You are the Spec Validator. Read the agent definition at
agents/spec-validator.md, then validate the specification at
.dark-factory/specs/my-feature.md. Produce the validation report
exactly as specified in your agent definition."
```

**If the spec fails validation:** Fix the issues and re-validate. Do NOT proceed to implementation with an incomplete spec.

### Step 3: Generate Blind Test Scenarios

**Who:** Scenario Architect agent (in a SEPARATE Claude Code session)
**Time:** 3-10 minutes
**Output:** Test files in `.dark-factory/scenarios/my-feature/`

```bash
# IMPORTANT: Use a fresh Claude Code session
# This agent must NOT have seen any implementation code

claude "You are the Scenario Architect. Read the agent definition at
agents/scenario-architect.md, then read the validated specification at
.dark-factory/specs/my-feature.md. Generate comprehensive blind test
scenarios. Write executable test files to .dark-factory/scenarios/my-feature/.
You MUST NOT read any implementation source code. Use [test framework]
for the test files."
```

### Step 4: Implement from Spec

**Who:** Implementer agent (in a SEPARATE Claude Code session)
**Time:** 5-30 minutes
**Output:** Implementation code + self-tests

```bash
# IMPORTANT: Use a fresh Claude Code session
# This agent must NOT have access to scenarios

claude "You are the Implementer. Read the agent definition at
agents/implementer.md, then implement the specification at
.dark-factory/specs/my-feature.md. You MUST NOT read anything in
.dark-factory/scenarios/. Follow existing codebase conventions.
Write your own unit tests based on the spec."
```

### Step 5: Run Quality Gates

**Who:** Automated scripts
**Time:** 1-5 minutes
**Output:** Quality gate report

```bash
# Run the quality gate script
./scripts/quality-gate.sh my-feature

# Or manually run each gate:
# 1. Build
# 2. Lint
# 3. Type check
# 4. Implementer's self-tests
# 5. Blind scenarios
```

### Step 6: Review

**Who:** Reviewer agent
**Time:** 3-10 minutes
**Output:** Review report in `.dark-factory/reviews/`

```bash
claude "You are the Reviewer. Read the agent definition at
agents/reviewer.md. Review the implementation for spec
.dark-factory/specs/my-feature.md. The implementation is in
[paths]. Test results are in [path]. Produce the review report
exactly as specified in your agent definition. Write it to
.dark-factory/reviews/my-feature-review.md."
```

### Step 7: Oracle Decision

**Who:** Oracle agent
**Time:** 1-2 minutes
**Output:** SHIP or NO-SHIP decision

```bash
claude "You are the Oracle. Read the agent definition at
agents/oracle.md. Make a SHIP/NO-SHIP decision for spec
.dark-factory/specs/my-feature.md. The review report is at
.dark-factory/reviews/my-feature-review.md. Test results are
in [path]. This is iteration [N] of 3."
```

### Step 8: Iterate or Ship

**If SHIP:** Commit, push, open PR.

**If NO-SHIP (iteration < 3):**
1. Take the Oracle's feedback
2. Start a new Implementer session (Step 4) with the feedback
3. The Implementer gets: test RESULTS + reviewer feedback (never test source code)
4. Rerun from Step 5

**If NO-SHIP (iteration = 3):** Human reviews the escalation report and decides next steps.

---

## Quick Reference: Session Isolation

This is the most important operational detail. Each agent must run in an isolated Claude Code session.

| Step | Agent | New Session? | What to Tell It |
|------|-------|-------------|-----------------|
| 2 | Spec Validator | Yes | Read spec only |
| 3 | Scenario Architect | **Yes (critical)** | Read spec, write scenarios, don't read code |
| 4 | Implementer | **Yes (critical)** | Read spec, don't read scenarios |
| 5 | (automated) | N/A | Scripts run tests |
| 6 | Reviewer | Yes | Read everything |
| 7 | Oracle | Yes | Read everything |

**Why separate sessions?** Claude Code sessions accumulate context. An Implementer session that previously saw scenario files (even accidentally) breaks the information barrier. Fresh sessions guarantee isolation.

---

## Workflow Variants

### Quick Fix (Bug Fix)
For small, well-understood bugs:
1. Write a bug-fix spec (use `templates/bug-fix-spec.md`)
2. Skip formal validation (but still check completeness mentally)
3. Run Steps 3-7 as normal

### Refactoring
For refactoring existing code:
1. Write a refactor spec (use `templates/refactor-spec.md`)
2. Scenarios focus on behavioral preservation (outputs should not change)
3. Run Steps 2-7 as normal

### API Development
For new API endpoints:
1. Write an API spec (use `templates/api-spec.md`)
2. Scenarios include HTTP-level tests (status codes, headers, body)
3. Run Steps 2-7 as normal

---

## Automation

### Full Pipeline Script

```bash
# Run the complete pipeline
./scripts/dark-factory.sh my-feature

# With options
./scripts/dark-factory.sh my-feature --max-iterations 3 --test-framework vitest
```

### CI/CD Integration

Add to your CI pipeline to run blind scenarios on every PR:

```yaml
# .github/workflows/dark-factory.yml
name: Dark Factory Quality Gate
on: pull_request
jobs:
  quality-gate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Run blind scenarios
        run: ./scripts/quality-gate.sh
```

---

## Metrics to Track

Over time, track these metrics to improve your specs and pipeline:

| Metric | What It Tells You |
|--------|-------------------|
| **First-pass rate** | % of specs that SHIP on iteration 1 (target: >60%) |
| **Avg iterations** | Mean iterations to SHIP (target: <2) |
| **Spec rejection rate** | % of specs that fail validation (target: decreasing over time) |
| **Scenario catch rate** | % of bugs caught by blind scenarios vs. found later |
| **Time to ship** | End-to-end pipeline time per feature |
| **Escalation rate** | % that hit iteration 3 and need human intervention |

---

## Troubleshooting

### "The implementer keeps failing the same scenarios"
- Check if the spec is ambiguous about that behavior
- The feedback loop may not have enough detail — enrich the Oracle's error messages
- Consider whether the scenarios are testing something not in the spec

### "Scenarios are too easy / not catching real bugs"
- Review the Scenario Architect's output for coverage gaps
- Add domain-specific adversarial patterns to the scenario architect agent definition
- Consider adding property-based testing requirements

### "Spec validation takes too long"
- Start with the template — it guides you to completeness faster
- Use Claude Code to help fill out sections interactively
- Common patterns (CRUD, auth, etc.) should have pre-filled templates

### "The whole pipeline is too slow"
- Steps 3 and 4 can run in parallel (scenario generation and implementation)
- Consider running the Implementer and Scenario Architect simultaneously in separate terminals
- Cache scenario generation for iteration cycles (don't regenerate scenarios)
