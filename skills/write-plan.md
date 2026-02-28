# Skill: Write Project Plan

## Trigger
User wants to plan a new project from scratch — making architectural decisions and decomposing it into ordered specs that feed the Dark Factory pipeline.

## Behavior

1. Ask the user which mode they prefer:
   - **Quick-start** — User provides a brain-dump (paragraph, bullets, or existing doc). Extract what you can, propose defaults, ask targeted questions only for gaps.
   - **Guided** — Full section-by-section interview from foundational to granular.

2. Read the template at `templates/project-plan.md`.

3. Walk through the 10 sections in dependency order:
   1. **Project Overview** — what, who, why, success metrics
   2. **Tech Stack** — constrains everything downstream; require a rationale for each choice
   3. **Architecture** — component diagram, data flow, integrations, auth
   4. **Data Model** — entities, fields, relationships, invariants (often reveals missed features)
   5. **Directory Structure** — propose based on stack/arch, user adjusts
   6. **Non-Functional Requirements** — push for quantified targets, reject weasel words
   7. **Feature Decomposition** — the main event; suggest missed features, establish dependency graph, derive build phases
   8. **Shared Patterns** — derive from tech stack, confirm with user
   9. **Out of Scope** — what's explicitly excluded
   10. **Open Questions** — unresolved decisions that need answers before specific features

4. Validate the completed plan:
   - All features have unique IDs (F-1, F-2, ...)
   - No dependency cycles (topological sort must succeed)
   - NFRs are quantified (no weasel words like "fast", "scalable", "secure" without numbers)
   - Tech stack has a rationale for each choice
   - At least one feature is decomposed
   - If validation fails, report issues and help fix them

5. Write the plan to `.dark-factory/plans/[YYYY-MM-DD]-[project-name].md`

6. Generate stub specs for each feature in Section 7:
   - Write stubs to `.dark-factory/specs/[YYYY-MM-DD]-[feature-name].md`
   - Use the correct template type (feature/api/bug-fix/refactor) per the Spec Type column
   - Pre-fill (~30-40%): Header (Status: STUB), Problem Statement, NFRs, Constraints, Dependencies, Out of Scope
   - Leave as placeholders: Functional Requirements, Interface Contract, Edge Cases, Error Handling, Acceptance Criteria, Examples
   - Include an HTML comment header linking back to the plan

7. Print a summary: plan location, stub locations, suggested next steps (`/df-write-spec [feature-name]` for each stub, in build-phase order).

## Output
- A complete project plan in `.dark-factory/plans/`
- Stub spec files in `.dark-factory/specs/` (one per feature)
- Summary with next steps

## Key Phrases
- "plan a project", "plan a new app", "project plan"
- "I want to build a new...", "start a new project"
- "help me plan...", "architect a..."
- "decompose this project into specs"
