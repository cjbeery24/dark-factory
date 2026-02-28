---
name: df-validate-spec
description: Validate a Dark Factory specification for completeness before entering the pipeline
argument-hint: "[spec-name]"
---

# Validate Specification

1. Read the agent definition at `agents/spec-validator.md`
2. Read the specification file the user wants validated from `.dark-factory/specs/`
3. Evaluate the spec against every criterion in the Spec Validator agent definition
4. Produce the validation report in the exact format specified
5. Write the report to `.dark-factory/reviews/[spec-name]-validation.md`
6. If the spec fails, provide specific suggestions for each gap

## Pre-flight Checks
Before proceeding, verify ALL of the following. STOP with a clear error if any fails.
1. **Spec file exists:** `.dark-factory/specs/[spec-name].md` is present and non-empty
2. **Agent definition accessible:** `agents/spec-validator.md` can be read
3. **Reviews dir exists:** `.dark-factory/reviews/` exists (create if needed)
4. **Config present (warn only):** `.dark-factory/config.sh` exists

## Prerequisites
- A spec file must exist in `.dark-factory/specs/`

## Output
A validation report with a completeness score and PASS/REVISE/REJECT verdict.
