# Dark Factory — AI Development Framework

## Overview

Dark Factory is a specification-driven AI development framework that enforces **information barriers** between agents to produce robust, well-tested software without human intervention after the spec phase.

The core principle: **agents that write code never see the tests, and agents that write tests never see the code.**

## Directory Structure

```
dark-factory/
├── agents/          → Agent role definitions (who does what)
├── skills/          → Claude Code skill files (how to invoke workflows)
├── templates/       → Specification templates (starting points for specs)
├── scripts/         → Orchestration scripts (pipeline automation)
├── docs/            → Documentation (guides, references, onboarding)
└── .dark-factory/   → Runtime artifacts (generated during pipeline runs)
    ├── plans/       → Project plans (decomposed into specs)
    ├── specs/       → Active specifications (input to pipeline)
    ├── scenarios/   → Blind test scenarios (RESTRICTED — implementation agents cannot read)
    ├── reviews/     → Review outputs
    └── reports/     → Quality gate reports
```

## Critical Information Barrier Rules

These rules are the foundation of the framework. Violating them defeats the purpose.

1. **Implementation agents MUST NOT read `.dark-factory/scenarios/`** — This directory contains blind test scenarios. If the implementer sees them, they'll optimize for the tests instead of writing genuinely robust code.
2. **Scenario generation agents MUST NOT read implementation source code** — Scenarios must be derived purely from the specification, not reverse-engineered from code.
3. **All code changes MUST begin with a validated specification** — No specification, no implementation. Period.
4. **Quality gates are non-negotiable** — Code does not ship until it passes blind scenarios and review.

## Agent Roles (Summary)

| Agent | Reads | Cannot Read | Purpose |
|-------|-------|-------------|---------|
| Spec Validator | Spec draft | — | Validate spec completeness |
| Implementer | Validated spec | `.dark-factory/scenarios/` | Write implementation code |
| Scenario Architect | Validated spec | Implementation source | Generate blind test scenarios |
| Reviewer | Spec, implementation, test results | — | Evaluate quality |
| Oracle | Everything | — | Final ship/no-ship decision |

## Using This Framework

### For a new project:
1. Copy this framework into your project or reference it
2. Plan the project: Use `/df-write-plan` to make architectural decisions and decompose into ordered specs
3. Complete each stub spec: Use `/df-write-spec` on each stub (in build-phase order)
4. Run the dark factory pipeline: `./scripts/dark-factory.sh`

### For a single feature/bug/refactor:
1. Write a spec using a template from `templates/`
2. Run the dark factory pipeline: `./scripts/dark-factory.sh`

### For individual steps:
- Validate a spec: Use the spec-validator agent
- Implement from spec: Use the implementer agent
- Generate scenarios: Use the scenario-architect agent
- Review code: Use the reviewer agent

See `docs/WORKFLOW.md` for the complete guide.

## Quality Standards

- All specs must score 100% on the completeness checklist before entering the pipeline
- Implementation must pass all blind scenarios
- Code must pass static analysis, type checking, and linting
- Reviewer must approve before Oracle makes final determination
- Mutation testing score must exceed 80% (when applicable)
