# Dark Factory — AI Development Framework

## Overview

Dark Factory is a specification-driven AI development framework that enforces **information barriers** between agents to produce robust, well-tested software without human intervention after the spec phase.

The core principle: **agents that write code never see the tests, and agents that write tests never see the code.**

## Information Barrier Architecture

Barriers are enforced through **Docker container filesystem isolation** (primary) with prompt-level barriers and `--disallowedTools` as defense-in-depth layers:

- **Primary barrier:** Each agent runs inside a Docker container with restricted bind mounts. Restricted directories are masked with empty read-only tmpfs overlays — the files simply don't exist inside the container.
- **Defense-in-depth:** `--disallowedTools` flags and `--append-system-prompt` barriers are retained as secondary enforcement layers inside containers.
- **Symlink protection:** Pre-flight checks detect symlinks that cross barrier boundaries.

## Directory Structure

```
dark-factory/
├── agents/          → Agent role definitions (who does what)
├── docker/          → Docker infrastructure (Dockerfiles, build scripts)
├── skills/          → Claude Code skill files (how to invoke workflows)
├── templates/       → Specification templates (starting points for specs)
├── scripts/         → Orchestration scripts (pipeline automation)
├── docs/            → Documentation (guides, references, onboarding)
└── .dark-factory/   → Runtime artifacts (generated during pipeline runs)
    ├── plans/       → Project plans (decomposed into specs)
    ├── specs/       → Active specifications (input to pipeline)
    ├── scenarios/   → Blind test scenarios (RESTRICTED — implementation agents cannot read)
    ├── reviews/     → Review outputs
    ├── reports/     → Quality gate reports
    ├── audit/       → Audit logs (agent activity, barrier violation detection)
    └── state/       → Pipeline state files (for resume/recovery)
```

## Critical Information Barrier Rules

These rules are the foundation of the framework. Violating them defeats the purpose.

1. **Implementation agents MUST NOT read `.dark-factory/scenarios/`** — This directory is masked (empty tmpfs) in the Implementer's container. The files don't exist in their filesystem.
2. **Scenario generation agents MUST NOT read implementation source code** — Source directories (`SOURCE_DIRS`) are masked in the Scenario Architect's container.
3. **`.git/` is masked in barrier-sensitive containers** — Prevents `git log`/`git show` from leaking restricted content.
4. **All code changes MUST begin with a validated specification** — No specification, no implementation. Period.
5. **Quality gates are non-negotiable** — Code does not ship until it passes blind scenarios and review.

## Agent Roles (Summary)

| Agent | Project Mount | Masked (empty tmpfs) | Purpose |
|-------|:---:|---|---------|
| Spec Validator | `:ro` | — | Validate spec completeness |
| Scenario Architect | `:ro` | `SOURCE_DIRS[]`, `.git/` | Generate blind test scenarios |
| Implementer | `:rw` | `.dark-factory/scenarios/`, `.git/` | Write implementation code |
| Quality Gate | `:rw` | — | Run build/lint/test |
| Reviewer | `:ro` | — | Evaluate quality |
| Oracle | `:ro` | — | Final ship/no-ship decision |

## Prerequisites

- **Docker** (required) — Docker Desktop or Docker Engine
- Claude Code installed and configured
- Git repository initialized

## Using This Framework

### For a new project:
1. Copy this framework into your project or reference it
2. Build Docker images: `bash docker/build-images.sh <language>`
3. Plan the project: Use `/df-write-plan` to make architectural decisions and decompose into ordered specs
4. Complete each stub spec: Use `/df-write-spec` on each stub (in build-phase order)
5. Run the dark factory pipeline: `./scripts/dark-factory.sh`

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
- Mutation testing is available as an opt-in enhancement (configure `MUTATION_CMD` in config.sh)
