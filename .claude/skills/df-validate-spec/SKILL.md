---
name: df-validate-spec
description: Validate a Dark Factory specification for completeness before entering the pipeline
argument-hint: "[spec-name]"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. For full information barrier compliance, use `dark-factory run <spec>`.

# Validate Specification

1. Read the agent definition at `.dark-factory/agents/spec-validator.md`
2. Read the specification file the user wants validated from `.dark-factory/specs/`
3. Evaluate the spec against every criterion in the Spec Validator agent definition
4. **Scope check:** Count Functional Requirements (FR-N headings). If >5, add a Critical Issue: "Spec has N FRs (max recommended: 5). Broad specs cause scenario explosion and reduce pipeline success. Decompose into sub-specs."
5. **Scenario Intent check:** Verify Scenario Intent section exists. Each FR must have ≥5 intents. Intents must be one-line, testable descriptions. Flag missing or empty Scenario Intent as a Critical Issue.
6. Perform the Precision Analysis (Implementation Divergence Test): sketch 3 maximally-different implementations, rate divergence LOW/MEDIUM/HIGH
7. Produce the validation report in the exact format specified, including the Precision Analysis section
8. Write the report to `.dark-factory/reviews/[spec-name]-validation.md`
9. Write the `.status` file with all 5 keys (spec, date, score, verdict, divergence)
10. If the spec fails (score < 100 or divergence = HIGH), provide specific suggestions for each gap

## Pre-flight Checks
Before proceeding, verify ALL of the following. STOP with a clear error if any fails.
1. **Spec file exists:** `.dark-factory/specs/[spec-name].md` is present and non-empty
2. **Agent definition accessible:** `.dark-factory/agents/spec-validator.md` can be read. If missing, tell the user to run `dark-factory init --update` to restore framework files.
4. **Reviews dir exists:** `.dark-factory/reviews/` exists (create if needed)
5. **Config present (warn only):** `.dark-factory/config.toml` exists

## Prerequisites
- A spec file must exist in `.dark-factory/specs/`

## Output
A validation report with a completeness score, PASS/REVISE/REJECT verdict, and a Precision Analysis section with divergence rating (LOW/MEDIUM/HIGH). HIGH divergence blocks the pipeline even at 100/100.
