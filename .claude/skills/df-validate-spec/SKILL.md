---
name: df-validate-spec
description: Validate a Dark Factory specification for completeness before entering the pipeline
argument-hint: "[spec-name]"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. For full information barrier compliance, use `./scripts/dark-factory.sh`.

# Validate Specification

1. Read the agent definition at `agents/spec-validator.md`
2. Read the specification file the user wants validated from `.dark-factory/specs/`
3. Evaluate the spec against every criterion in the Spec Validator agent definition
4. Perform the Precision Analysis (Implementation Divergence Test): sketch 3 maximally-different implementations, rate divergence LOW/MEDIUM/HIGH
5. Produce the validation report in the exact format specified, including the Precision Analysis section
6. Write the report to `.dark-factory/reviews/[spec-name]-validation.md`
7. Write the `.status` file with all 5 keys (spec, date, score, verdict, divergence)
8. If the spec fails (score < 100 or divergence = HIGH), provide specific suggestions for each gap

## Pre-flight Checks
Before proceeding, verify ALL of the following. STOP with a clear error if any fails.
1. **Spec file exists:** `.dark-factory/specs/[spec-name].md` is present and non-empty
2. **Agent definition accessible:** `agents/spec-validator.md` can be read
3. **Reviews dir exists:** `.dark-factory/reviews/` exists (create if needed)
4. **Config present (warn only):** `.dark-factory/config.sh` exists

## Prerequisites
- A spec file must exist in `.dark-factory/specs/`

## Output
A validation report with a completeness score, PASS/REVISE/REJECT verdict, and a Precision Analysis section with divergence rating (LOW/MEDIUM/HIGH). HIGH divergence blocks the pipeline even at 100/100.
