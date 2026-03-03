# Dark Factory — AI Development Framework

## Overview

Dark Factory is a specification-driven AI development framework that enforces **information barriers** between agents to produce robust, well-tested software without human intervention after the spec phase.

The core principle: **agents that write code never see the tests, and agents that write tests never see the code.**

## Information Barrier Architecture

Barriers are enforced through **Docker container filesystem isolation** (primary) with prompt-level barriers and `--disallowedTools` as defense-in-depth layers:

- **Primary barrier:** Each agent runs inside a Docker container with restricted bind mounts. Restricted directories are masked with empty read-only tmpfs overlays — the files simply don't exist inside the container.
- **Defense-in-depth:** `--disallowedTools` flags and `--append-system-prompt` barriers are retained as secondary enforcement layers inside containers.
- **Symlink protection:** Pre-flight checks detect symlinks that cross barrier boundaries.

## Security Model

### `--dangerously-skip-permissions` — accepted trade-off

Every agent container runs with `claude -p --dangerously-skip-permissions`. This flag
**disables Claude Code's entire permission system** — no Bash command, file write, or
network request requires human approval inside the container. It is architecturally
required: the pipeline runs with no human in the loop, so there is no one to approve
tool calls.

**What this means:** inside any agent container, Claude can:
- Execute arbitrary shell commands via the Bash tool
- Write to any path that is bind-mounted into the container
- Make network requests (subject to egress filtering — see `docs/INTEGRATION.md`)

**The container is therefore the real security boundary, not Claude Code's permission
system.** `--disallowedTools` flags and `--append-system-prompt` barriers operate
*inside* an already-isolated container. They provide defense-in-depth against behavioral
failures (e.g., an agent that tries to read a restricted path), but do not substitute
for filesystem isolation.

### What the container boundary enforces

| Control | Mechanism | Protects against |
|---------|-----------|-----------------|
| Filesystem isolation | tmpfs overlays mask restricted dirs | Cross-barrier reads (Implementer→scenarios+reports, Architect→source) |
| Read-only project mount | `:ro` bind mounts on analysis agents | Analysis agents writing to the project |
| `.git/` masking | empty tmpfs over `.git/` | `git log`/`git show` leaking restricted history |
| Egress filtering | `--internal` Docker networks + tinyproxy allowlist | Data exfiltration to arbitrary hosts |
| Version pinning | digest-pinned base images, exact package versions | Compromised supply chain injecting malicious agent code |

### Known limitations (accepted)

- `--disallowedTools` covers only one `SOURCE_DIR` as defense-in-depth (Docker masks all
  `SOURCE_DIRS` — the primary barrier is complete)
- The audit log detects naive/accidental barrier violations via path-string matching; it does
  not detect natural-language or semantic leakage
- npm transitive dependencies of `@anthropic-ai/claude-code` are resolved via semver at build
  time; full lockfile pinning would close this gap (future improvement)
- A compromised `claude-code` binary inside a pinned container could still behave arbitrarily;
  version pinning reduces the attack window but does not eliminate it
- Oracle iteration feedback now uses spec-section-anchored references (FR-N, EC-N + behavioral
  category) instead of scenario names or assertion messages; `.dark-factory/reports/` is masked
  in the Implementer container. Some inherent leakage remains — the Implementer can infer which
  spec sections have test coverage from the referenced sections, and the barrier weakens with
  each NO-SHIP cycle
- Skills-based workflows (`/df-implement`, `/df-generate-scenarios`) run in the user's
  current Claude Code session with no filesystem isolation; information barriers are technically
  enforced only when running `dark-factory.sh` — skills are developer convenience tools, not a
  compliant pipeline execution method

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

1. **Implementation agents MUST NOT read `.dark-factory/scenarios/` or `.dark-factory/reports/`** — These directories are masked (empty tmpfs) in the Implementer's container. The files don't exist in their filesystem.
2. **Scenario generation agents MUST NOT read implementation source code** — Source directories (`SOURCE_DIRS`) are masked in the Scenario Architect's container.
3. **`.git/` is masked in barrier-sensitive containers** — Prevents `git log`/`git show` from leaking restricted content.
4. **All code changes MUST begin with a validated specification** — No specification, no implementation. Period.
5. **Quality gates are non-negotiable** — Code does not ship until it passes blind scenarios and review.

## Agent Roles (Summary)

| Agent | Project Mount | Masked (empty tmpfs) | Purpose |
|-------|:---:|---|---------|
| Spec Validator | `:ro` | — | Validate spec completeness |
| Scenario Architect | `:ro` | `SOURCE_DIRS[]`, `.git/` | Generate blind test scenarios |
| Implementer | `:rw` | `.dark-factory/scenarios/`, `.dark-factory/reports/`, `.git/` | Write implementation code |
| Quality Gate | `:rw` | — | Run build/lint/test |
| Reviewer | `:ro` | `.dark-factory/scenarios/`, `.git/` | Evaluate quality |
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
- Specs must also pass the Precision Analysis (implementation divergence rated LOW or MEDIUM, not HIGH)
- Implementation must pass all blind scenarios
- Code must pass static analysis, type checking, and linting
- Reviewer must approve before Oracle makes final determination
- Mutation testing is available as an opt-in enhancement (configure `MUTATION_CMD` in config.sh)
