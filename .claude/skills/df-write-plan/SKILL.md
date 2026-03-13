---
name: df-write-plan
description: Plan a new project — make architectural decisions and decompose into ordered specs for the Dark Factory pipeline
argument-hint: "[project-name]"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. For full information barrier compliance, use `dark-factory run <spec>`.

# Write Project Plan

## Pre-flight Checks
Before proceeding, verify ALL of the following. STOP with a clear error if any fails.
1. **Plans dir exists:** `.dark-factory/plans/` exists (create if needed)
2. **Specs dir exists:** `.dark-factory/specs/` exists (create if needed)
3. **Project plan template accessible:** `templates/project-plan.md` can be read
4. **Config present (warn only):** `.dark-factory/config.sh` exists

## Steps

1. Ask the user which mode they prefer:
   - **Quick-start** — User provides a brain-dump; extract what you can, propose defaults, ask only about gaps
   - **Guided** — Full section-by-section interview

2. Read the template at `templates/project-plan.md`.

3. Interview in dependency order: Project Overview → Tech Stack → Architecture → Data Model → Directory Structure → NFRs → Feature Decomposition → Shared Patterns → Out of Scope → Open Questions.
   - Push back on vague answers: "Can you quantify 'fast'? What latency target in milliseconds?"
   - At Feature Decomposition: suggest missed features, establish dependencies, derive build phases
   - At Data Model: call out entities implied by features that the user hasn't mentioned

4. Validate the completed plan before writing:
   - All features have unique IDs (F-1, F-2, ...)
   - No dependency cycles (topological sort must succeed)
   - NFRs are quantified (no weasel words)
   - Tech stack has a rationale for each choice
   - At least one feature is decomposed
   - If validation fails, report issues and help fix them

5. Write the plan to `.dark-factory/plans/[YYYY-MM-DD]-[project-name].md`

6. Generate stub specs for each feature in the Feature Decomposition table:
   - Use the correct template type per the Spec Type column:
     - feature → `templates/feature-spec.md`
     - api → `templates/api-spec.md`
     - bug-fix → `templates/bug-fix-spec.md`
     - refactor → `templates/refactor-spec.md`
     - db-migration → `templates/db-migration-spec.md`
     - performance → `templates/performance-spec.md`
     - security-fix → `templates/security-fix-spec.md`
     - integration → `templates/integration-spec.md`
   - Write stubs to `.dark-factory/specs/[YYYY-MM-DD]-[feature-name].md`
   - Set Status to STUB in the header
   - Pre-fill: Problem Statement, NFRs, Constraints, Dependencies, Out of Scope (from the plan)
   - Leave as placeholders: Functional Requirements, Interface Contract, Edge Cases, Error Handling, Acceptance Criteria, Examples
   - Add an HTML comment header with dark-factory-metadata linking back to the plan and feature ID:
     ```html
     <!-- dark-factory-metadata
     feature-id: F-X
     depends-on: [F-1, F-2]
     plan: YYYY-MM-DD-project-name
     -->
     ```

7. Print summary: plan path, stub paths, and suggested next steps (`/df-write-spec [feature-name]` for each stub in build-phase order).

## Output
A project plan file + stub spec files ready for `/df-write-spec` completion.
