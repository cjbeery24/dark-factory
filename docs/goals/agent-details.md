The original source (Haytham Yassine / Yassine Dwkte’s LinkedIn article from early March 2026) describes the 11-agent architecture at a high level and gives concrete examples of what each agent produces, but it does not include, embed, or link to any raw system-prompt .md files, AGENTS.md, or individual agent definition files. No GitHub repository or prompt artifacts are referenced in the post. The descriptions are narrative + example outputs only.
Below is a clear, structured breakdown of each of the 11 agents, compiled directly from the post. I’ve included:

Role and primary phase(s)
Responsibilities
Inputs / Outputs
Information barriers (where explicitly enforced)
Real examples from the F-002 feature run

This matches the pipeline you’ve been comparing against.
1. Product Owner Agent

Phase: 1 – Specification
Responsibilities: Turns a high-level human trigger into locked, machine-readable specifications.
Inputs: Human command (e.g., /run-pipeline "Implement feature F-002...").
Outputs: Full Gherkin feature files + user stories with measurable acceptance criteria (e.g., one feature produced 30+ scenarios and 55 explicit criteria).
Barriers: None (starts the pipeline).
Example: For F-002 it specified exact variant content rules like “given a user with preferred_tool 'Claude Code', the lesson contains at least one terminal command example.”

2. UX Designer Agent

Phase: 2 – Design
Responsibilities: Creates component-level design specs.
Inputs: Completed specifications from Product Owner.
Outputs: Wireframes, exact color/design tokens, interaction states, accessibility rules, responsive breakpoints (e.g., 4 component specs with per-tool colors).
Barriers: Cannot see source code or holdout tests.
Example: Outputs functional but generic UIs unless explicitly constrained by design tokens and elevation hierarchies.

3. Architect Agent

Phase: 3 – Architecture
Responsibilities: Locks down all technical contracts before any code is written.
Inputs: Specs + design outputs.
Outputs: Exact data models (column types), API request/response schemas, canonical identifiers, and Architecture Decision Records (ADRs).
Barriers: Cannot access holdouts or modify source code.
Example: Decided on a “variants table” structure and enforced verbatim use of shared IDs like “mod-intro”.

4. Test Engineer Agent

Phase: 4 – Test-First (parallel)
Responsibilities: Writes the visible test suite that the Developer can see.
Inputs: Locked specs + architecture + design.
Outputs: Visible tests (e.g., 44 tests for F-002).
Barriers: Cannot see holdout tests.
Example: Produced 44 core functionality tests that the Developer had to pass.

5. QA-Holdout Agent

Phase: 4 – Test-First (parallel)
Responsibilities: Generates adversarial, unseen tests.
Inputs: Same locked specs + architecture + design (never sees code).
Outputs: Holdout tests written to isolated .holdout/ directory (e.g., 100 tests covering SQL injection, duplicate handling, oversized payloads, missing auth headers).
Barriers: Strict — cannot read src/ or any implementation; enforced by holdout-guard.sh git hook.
Example: Created security and edge-case tests the Developer never saw.

6. Developer Agent

Phase: 5 – Implementation
Responsibilities: Writes the actual production code.
Inputs: Specs, architecture, design, visible tests only.
Outputs: Source code changes (new models, endpoints, frontend components).
Barriers: Strict — cannot read .holdout/ directory; enforced by git hooks. Can read src/ and visible tests.
Example: Implemented ToolBadge + Settings page for F-002 and passed all 44 visible tests on the first try.

7. Validator Agent (Holdout Verification)

Phase: 6 – Holdout Verification
Responsibilities: Runs the blind holdouts and judges the implementation.
Inputs: Built code + holdout suite.
Outputs: Sanitized behavioral report only (e.g., pass/fail percentages + one-line expected-vs-actual descriptions).
Barriers: Can see everything internally but must output only sanitized feedback — no test source, no file paths, no assertion logic.
Example: Reported “Expected: Speed Learner badge awarded when module completed in ≤24 hours. Actual: Badge only awarded for strictly <24 hours.” (96/100 holdouts passed for F-002).

8. Factory-Lead Agent (Orchestrator)

Phases: Runs across all (especially gates and iteration)
Responsibilities: Spawns agents, enforces phase gates, manages up to 3 iteration retries, routes failures, decides escalation.
Inputs: Outputs from every prior phase.
Outputs: Pipeline progression decisions, iteration commands, or “ship with gaps” + defect registry entry.
Barriers: None (full visibility for orchestration).
Example: On complex features allowed exactly 3 retries before documenting remaining gaps.

9. Code Review Agent

Phase: 7 – Code Review + Final Validation
Responsibilities: Automated final checks.
Inputs: Implementation + all test results.
Outputs: OWASP Top 10 compliance report + full regression results (combined visible + holdouts).
Barriers: None.
Example: Confirmed 97.7 % pass rate across 1,367 total tests for F-002 and verified OWASP compliance.

10. Monitoring Dashboard Agent

Phase: 9 – Monitoring & Observability (post-pipeline)
Responsibilities: Provides visibility into every run.
Inputs: JSON artifacts from the pipeline.
Outputs: Dashboards showing timelines, holdout pass-rate gauges, defect breakdowns.
Barriers: None.
Example: Visualized defect classifications and pipeline metrics for each feature.

11. Defect Classifier Agent

Phase: 8 – Iteration / Defect Management (works with Factory-Lead)
Responsibilities: Categorizes failures for feedback and registry.
Inputs: Validator and Code Review outputs.
Outputs: Tagged defects (implementation bug, fixture mismatch, spec ambiguity, etc.) fed into the defect registry.
Barriers: None (operates on reports).
Example: Across 7 features it classified 18 defects (10 implementation, 5 fixture, 2 spec, 1 accessibility).

These agents are not generic “do whatever” prompts — each has a tightly scoped role, and the two information barriers (Developer ↔ QA-Holdout and Validator’s sanitized output) are the mechanical enforcement that prevents cheating.