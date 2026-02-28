---
name: df-write-spec
description: Write a new Dark Factory specification for a feature, bug fix, API, or refactoring
argument-hint: "[feature-name]"
---

# Write Specification

## Pre-flight Checks
Before proceeding, verify ALL of the following. STOP with a clear error if any fails.
1. **Specs dir exists:** `.dark-factory/specs/` exists (create if needed)
2. **At least one template accessible:** At least one template in `templates/` can be read
3. **Config present (warn only):** `.dark-factory/config.sh` exists

## Steps

1. Ask the user what type of spec they need:
   - Feature → use `templates/feature-spec.md`
   - Bug fix → use `templates/bug-fix-spec.md`
   - API endpoint → use `templates/api-spec.md`
   - Refactoring → use `templates/refactor-spec.md`
   - Database migration → use `templates/db-migration-spec.md`
   - Performance optimization → use `templates/performance-spec.md`
   - Security fix → use `templates/security-fix-spec.md`
   - Service integration → use `templates/integration-spec.md`

2. Read the selected template.

3. Interview the user to fill in each section. For each section:
   - Explain what information is needed
   - Ask targeted questions to extract the details
   - Push back on vague answers: "Can you be more specific about what 'fast' means? What latency target in milliseconds?"
   - Suggest edge cases they may not have considered
   - Suggest error conditions they may have missed

4. Write the completed spec to `.dark-factory/specs/[YYYY-MM-DD]-[name].md`

5. Run the spec through mental validation against the completeness checklist:
   - Every section filled in?
   - No weasel words?
   - All types defined?
   - All edge cases enumerated?
   - All errors handled?
   - At least 3 examples (happy path, error, edge case)?

6. If gaps remain, point them out and help the user fill them.

## Output
A complete specification file ready for formal validation.
