# Skill: Validate Specification

## Trigger
User wants to validate a specification before entering the pipeline.

## Behavior

1. Read the agent definition at `agents/spec-validator.md`
2. Read the specification file the user wants validated
3. Evaluate the spec against every criterion in the Spec Validator agent definition
4. Perform the Precision Analysis (Implementation Divergence Test): sketch 3 maximally-different implementations, rate divergence LOW/MEDIUM/HIGH
5. Produce the validation report in the exact format specified, including the Precision Analysis section
6. Write the report to `.dark-factory/reviews/[spec-name]-validation.md`
7. Write the `.status` file with all 5 keys (spec, date, score, verdict, divergence)
8. If the spec fails (score < 100 or divergence = HIGH), provide specific suggestions for each gap

## Prerequisites
- A spec file must exist in `.dark-factory/specs/`

## Output
A validation report with a completeness score and PASS/REVISE/REJECT verdict.

## Key Phrases
- "validate spec", "check spec", "is this spec complete?"
- "review my specification", "spec validation"
