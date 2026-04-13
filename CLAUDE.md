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
- npm and pip transitive dependencies are pinned via lockfiles generated inside Alpine containers
  (`docker/lockfiles/`); `npm ci` and `pip install --no-deps` ensure reproducible installs
- A compromised `claude-code` binary inside a pinned container could still behave arbitrarily;
  version pinning reduces the attack window but does not eliminate it
- Oracle iteration feedback now uses spec-section-anchored references (FR-N, EC-N + behavioral
  category) instead of scenario names or assertion messages; `.dark-factory/reports/` is masked
  in the Implementer container. Some inherent leakage remains — the Implementer can infer which
  spec sections have test coverage from the referenced sections, and the barrier weakens with
  each NO-SHIP cycle
- Skills-based workflows (`/df-write-spec`, `/df-validate-spec`, `/df-write-plan`,
  `/df-write-feedback`, `/df-run-quality-gate`) run in the user's current Claude Code
  session for convenience on non-barrier-sensitive operations. Barrier-sensitive steps
  (implementation, scenario generation) MUST use `dark-factory run` or the individual
  CLI commands (`dark-factory scenarios`, `dark-factory gate`) for Docker-enforced isolation.
  `/df-implement` and `/df-generate-scenarios` have been deprecated — they show
  deprecation notices redirecting to the CLI

## Directory Structure

```
dark-factory/
├── agents/          → Agent role definitions (who does what)
├── docker/          → Docker infrastructure (Dockerfiles, build scripts)
├── skills/          → Claude Code skill files (how to invoke workflows)
├── templates/       → Specification templates (starting points for specs)
├── scripts/         → Quality gate bash entrypoint (runs inside Docker containers)
├── docs/            → Documentation (guides, references, onboarding)
└── .dark-factory/   → Runtime artifacts (generated during pipeline runs)
    ├── plans/       → Project plans (decomposed into specs)
    ├── specs/       → Active specifications (input to pipeline)
    ├── scenarios/   → Test Engineer's AC-aligned visible tests (readable by Implementer as TDD targets)
    ├── reviews/     → Review outputs
    ├── reports/     → Quality gate reports
    ├── audit/       → Audit logs (agent activity, barrier violation detection)
    └── state/       → Pipeline state files (for resume/recovery)
```

## Critical Information Barrier Rules

These rules are the foundation of the framework. Violating them defeats the purpose.

1. **Implementation agents MUST NOT read `.dark-factory/holdouts/` or `.dark-factory/reports/`** — These directories are masked (empty tmpfs) in the Implementer's container. The Implementer MAY read `.dark-factory/scenarios/` (Test Engineer's visible AC-aligned tests) as TDD targets.
2. **Scenario generation agents MUST NOT read implementation source code** — Source directories (`SOURCE_DIRS`) are masked in the Scenario Architect's container.
3. **`.git/` is masked in barrier-sensitive containers** — Prevents `git log`/`git show` from leaking restricted content.
4. **All code changes MUST begin with a validated specification** — No specification, no implementation. Period.
5. **Quality gates are non-negotiable** — Code does not ship until it passes blind scenarios and review.

## Agent Roles (Summary)

| Agent | Project Mount | Masked (empty tmpfs) | Purpose |
|-------|:---:|---|---------|
| Spec Validator | `:ro` | — | Validate spec completeness |
| Architect | `:ro` | `.dark-factory/scenarios/`, `.dark-factory/audit/`, `.dark-factory/state/`, `.dark-factory/failures/`, `.git/` | Produce architecture manifest |
| Scenario Architect | `:ro` | `SOURCE_DIRS[]`, `.git/` | Generate blind test scenarios |
| Implementer | `:rw` | `.dark-factory/holdouts/`, `.dark-factory/reports/`, `.dark-factory/audit/`, `.dark-factory/state/`, `.dark-factory/failures/`, `.git/` | Write implementation code |
| Quality Gate | `:rw` | — | Run build/lint/test |
| Reviewer | `:ro` | `.dark-factory/scenarios/`, `.dark-factory/holdouts/`, `.dark-factory/reports/`, `.git/` | Evaluate quality |
| Evaluator | `:ro` | `SOURCE_DIRS[]`, `.git/` | Evaluate deployed app behavior (optional) |
| Oracle | `:ro` | — | Final ship/no-ship decision |

## Feature Tiers

Features are organized into tiers. Enterprise features are off by default (`enterprise_enabled = false` in config) and must be explicitly opted into.

| Tier | Features | Gating |
|------|----------|--------|
| **Core** (always active) | Spec validation, scenario generation, implementation, quality gate, review, oracle, iteration loop, barrier enforcement | Always on |
| **Standard** (always on, lightweight) | Cost tracking, satisfaction scoring, trajectory classification, stable failure detection, failure classification | Always on |
| **Enterprise** (opt-in) | Progressive deployment (canary, rollback), feature flags, trust scoring, dynamic autonomy, anomaly detection, OTEL export, notifications (Slack/webhook), regression testing, cost calibration | `enterprise_enabled = true` |

Enterprise CLI commands (`deploy`, `export-otel`, `regression`, `calibrate-cost`) show a clear message when enterprise is disabled.

## Autonomy Levels

The pipeline supports graduated human oversight via `AUTONOMY_LEVEL` in config:

| Level | Name | Behavior |
|-------|------|----------|
| 1 | Assisted | Pauses after Oracle SHIP for human confirmation |
| 2 | Supervised | Auto-proceed, log decision, track metrics |
| 3 | Autonomous | Full dark factory — no human in the loop (default) |

## Prerequisites

- **Python 3.11+** (required)
- **Docker** (required) — Docker Desktop or Docker Engine
- Claude Code installed and configured
- Git repository initialized
- **Authentication:** `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` set in your environment (see `docs/INTEGRATION.md#authentication`)

### Installation

```bash
pip install /path/to/dark-factory       # installs the `dark-factory` CLI
# or for development:
pip install -e /path/to/dark-factory    # editable install
```

Verify: `dark-factory --help`

## Using This Framework

**First time? Start here:** `docs/QUICKSTART.md` — goes from zero to running pipeline in 10-15 minutes.

### For a new project:
1. Run `dark-factory init` to auto-detect your project and generate config
2. Build Docker images: `bash docker/build-images.sh <language>`
3. Plan the project: Use `/df-write-plan` to make architectural decisions and decompose into ordered specs
4. Complete each stub spec: Use `/df-write-spec` on each stub (in build-phase order)
5. Run the pipeline: `dark-factory run <spec-name>`

### For a single feature/bug/refactor:
1. Generate a spec: `dark-factory new-spec "Add user authentication"` (or copy from `templates/`)
2. Edit the generated spec to add project-specific details
3. Run the pipeline: `dark-factory run <spec-name>`

### After a successful SHIP:
Commit implementation code + pipeline artifacts to the target project. See `docs/POST-SHIP-COMMIT.md` for the complete checklist of what to commit and what to skip.

### For individual steps:
- Validate a spec: `dark-factory validate <spec>`
- Run quality gate: `dark-factory gate <spec>`
- Run scenarios: `dark-factory scenarios <spec>`
- Review code: Use the reviewer agent

See `docs/WORKFLOW.md` for the complete reference and `docs/TROUBLESHOOTING.md` for common errors.

## Quality Standards

- All specs must score 100% on the completeness checklist before entering the pipeline
- Specs must also pass the Precision Analysis (implementation divergence rated LOW or MEDIUM, not HIGH)
- Implementation must pass all blind scenarios
- Code must pass static analysis, type checking, and linting
- Reviewer must approve before Oracle makes final determination
- Mutation testing is available as an opt-in enhancement (configure `MUTATION_CMD` in config.sh)

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **dark-factory** (4082 symbols, 12246 relationships, 300 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## When Debugging

1. `gitnexus_query({query: "<error or symptom>"})` — find execution flows related to the issue
2. `gitnexus_context({name: "<suspect function>"})` — see all callers, callees, and process participation
3. `READ gitnexus://repo/dark-factory/process/{processName}` — trace the full execution flow step by step
4. For regressions: `gitnexus_detect_changes({scope: "compare", base_ref: "main"})` — see what your branch changed

## When Refactoring

- **Renaming**: MUST use `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` first. Review the preview — graph edits are safe, text_search edits need manual review. Then run with `dry_run: false`.
- **Extracting/Splitting**: MUST run `gitnexus_context({name: "target"})` to see all incoming/outgoing refs, then `gitnexus_impact({target: "target", direction: "upstream"})` to find all external callers before moving code.
- After any refactor: run `gitnexus_detect_changes({scope: "all"})` to verify only expected files changed.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Tools Quick Reference

| Tool | When to use | Command |
|------|-------------|---------|
| `query` | Find code by concept | `gitnexus_query({query: "auth validation"})` |
| `context` | 360-degree view of one symbol | `gitnexus_context({name: "validateUser"})` |
| `impact` | Blast radius before editing | `gitnexus_impact({target: "X", direction: "upstream"})` |
| `detect_changes` | Pre-commit scope check | `gitnexus_detect_changes({scope: "staged"})` |
| `rename` | Safe multi-file rename | `gitnexus_rename({symbol_name: "old", new_name: "new", dry_run: true})` |
| `cypher` | Custom graph queries | `gitnexus_cypher({query: "MATCH ..."})` |

## Impact Risk Levels

| Depth | Meaning | Action |
|-------|---------|--------|
| d=1 | WILL BREAK — direct callers/importers | MUST update these |
| d=2 | LIKELY AFFECTED — indirect deps | Should test |
| d=3 | MAY NEED TESTING — transitive | Test if critical path |

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/dark-factory/context` | Codebase overview, check index freshness |
| `gitnexus://repo/dark-factory/clusters` | All functional areas |
| `gitnexus://repo/dark-factory/processes` | All execution flows |
| `gitnexus://repo/dark-factory/process/{name}` | Step-by-step execution trace |

## Self-Check Before Finishing

Before completing any code modification task, verify:
1. `gitnexus_impact` was run for all modified symbols
2. No HIGH/CRITICAL risk warnings were ignored
3. `gitnexus_detect_changes()` confirms changes match expected scope
4. All d=1 (WILL BREAK) dependents were updated

## CLI

- Re-index: `npx gitnexus analyze`
- Check freshness: `npx gitnexus status`
- Generate docs: `npx gitnexus wiki`

<!-- gitnexus:end -->
