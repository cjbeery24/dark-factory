# Plan: Resolve Skills / Information Barrier Contradiction

**Status:** COMPLETE (2026-03-27)
**Priority:** 3 (Medium — straightforward, high-integrity fix)
**Scope:** 2 skill files updated, documentation updates
**Estimated effort:** 30-60 minutes
**Reviewed by:** Liotta, Neo

## Implementation Summary

Completed in commit `b3a0092`. Deprecated `/df-implement` and `/df-generate-scenarios` with clear notices redirecting to `dark-factory run`. Updated `/df-cycle` and CLAUDE.md.

## Problem Statement

The Dark Factory's core principle: **agents that write code never see the tests.** This barrier is enforced by Docker container filesystem isolation when running `dark-factory run`.

The skills `/df-implement` and `/df-generate-scenarios` run in the user's local Claude Code session with **no filesystem isolation**. Prompt-level barriers ("MUST NOT read this directory") are unreliable — as the intent document notes: *"'Don't look at the tests' instructions are useless; agents routinely violate them."*

The most convenient developer workflow (skills) bypasses the most important architectural constraint (barriers). From the HackerNoon Dark Factory Pattern article: *"The wall is the whole game."*

## Architecture Decision

**Deprecate barrier-sensitive skills. Redirect to CLI.**

- `/df-implement` and `/df-generate-scenarios` → deprecation notice pointing to `dark-factory run`
- All non-barrier skills remain unchanged
- No new CLI aliases or flags needed (review feedback: `dark-factory run` is the correct entry point, don't add complexity)

## Implementation Steps

### Step 1: Deprecate `/df-implement`

**File:** `.claude/skills/df-implement/SKILL.md`

Replace the entire skill content with:

```markdown
---
name: df-implement
description: "[DEPRECATED] Use 'dark-factory run <spec>' for barrier-enforced implementation"
argument-hint: "[spec-name]"
---

# DEPRECATED: Use `dark-factory run` instead

This skill has been deprecated because it runs without Docker filesystem isolation,
which means the information barrier between implementation and test scenarios is
**NOT enforced**.

## What to do instead

Run the full pipeline with barrier enforcement:

    dark-factory run <spec-name>

The pipeline handles implementation (Step 3) inside a Docker container where
`.dark-factory/scenarios/` is masked with an empty tmpfs overlay — the files
literally don't exist. Prompt-level barriers are unreliable; filesystem-level
barriers are reliable.

## If you don't have Docker

Docker is a prerequisite for Dark Factory's quality guarantees. Without it,
the information barrier cannot be enforced and blind scenarios lose their value.

Install Docker Desktop: https://www.docker.com/products/docker-desktop/

## Non-barrier alternatives

These skills still work without Docker:
- `/df-write-spec` — Write a new specification
- `/df-validate-spec` — Validate spec completeness
- `/df-write-feedback` — Translate quality gate report to feedback
- `/df-run-quality-gate` — Run quality gate checks (read-only, no barrier needed)
```

### Step 2: Deprecate `/df-generate-scenarios`

**File:** `.claude/skills/df-generate-scenarios/SKILL.md`

Replace with equivalent deprecation notice:

```markdown
---
name: df-generate-scenarios
description: "[DEPRECATED] Use 'dark-factory run <spec>' for barrier-enforced scenario generation"
argument-hint: "[spec-name]"
---

# DEPRECATED: Use `dark-factory run` instead

This skill has been deprecated because it runs without Docker filesystem isolation,
which means the information barrier between scenario generation and source code is
**NOT enforced**.

## What to do instead

Run the full pipeline (scenarios are generated at Step 2):

    dark-factory run <spec-name>

Or run just validation + scenarios:

    dark-factory validate <spec-name>
    dark-factory scenarios <spec-name>

The `scenarios` command runs the Scenario Architect inside a Docker container where
source directories are masked — the code literally doesn't exist in the container's
filesystem.

## Non-barrier alternatives

These skills still work without Docker:
- `/df-write-spec` — Write a new specification
- `/df-validate-spec` — Validate spec completeness
- `/df-write-feedback` — Translate quality gate report to feedback
```

### Step 3: Update CLAUDE.md skills note

**File:** `CLAUDE.md`

Replace the current skills caveat:

```markdown
- Skills-based workflows (`/df-write-spec`, `/df-validate-spec`, `/df-write-plan`,
  `/df-write-feedback`, `/df-run-quality-gate`) run in the user's current Claude Code
  session for convenience on non-barrier-sensitive operations. Barrier-sensitive steps
  (implementation, scenario generation) MUST use `dark-factory run` or the individual
  CLI commands (`dark-factory scenarios`, `dark-factory gate`) for Docker-enforced isolation.
  `/df-implement` and `/df-generate-scenarios` have been deprecated — they showed
  deprecation notices redirecting to the CLI.
```

### Step 4: Update `/df-cycle` guidance

**File:** `.claude/skills/df-cycle/SKILL.md`

Update the workflow steps to direct users to CLI for barrier-sensitive work:

Where the cycle skill currently references `/df-implement` or `/df-generate-scenarios`, replace with:

```markdown
## Barrier-Sensitive Steps (require Docker)

For implementation and scenario generation, use the CLI — these steps require
Docker container isolation for information barrier enforcement:

    dark-factory run <spec-name>

Do NOT use `/df-implement` or `/df-generate-scenarios` — these skills have been
deprecated because they run without filesystem isolation.
```

### Step 5: Update workflow documentation

**File:** `docs/WORKFLOW.md`

Update any references to `/df-implement` or `/df-generate-scenarios` to point to CLI commands instead. Add a note in the "Running Individual Steps" section:

```markdown
> **Note:** Implementation and scenario generation require Docker for information
> barrier enforcement. Use the CLI commands below, not the deprecated skills.
```

## Verification Criteria

1. `/df-implement` shows deprecation notice with CLI redirect
2. `/df-generate-scenarios` shows deprecation notice with CLI redirect
3. Non-barrier skills unchanged: `/df-write-spec`, `/df-validate-spec`, `/df-write-feedback`, `/df-run-quality-gate`, `/df-write-plan`
4. CLAUDE.md accurately reflects which skills are active vs. deprecated
5. `/df-cycle` directs users to CLI for barrier-sensitive steps
6. `docs/WORKFLOW.md` references CLI commands for barrier-sensitive steps

## Risks

- **User friction** — skills were convenient. Mitigated by clear deprecation messages and CLI alternatives that already exist.
- **Docker becomes a hard requirement for implementation** — this was always the intent. Docker is listed as a prerequisite in CLAUDE.md and QUICKSTART.md.
- **Existing workflows break** — users who invoke `/df-implement` will see a notice instead of running. This is intentional — they were running without barriers.

## Non-Goals

- Do NOT delete skill files (keep with deprecation for discoverability)
- Do NOT add new CLI aliases like `dark-factory implement` (review feedback: `dark-factory run` is sufficient)
- Do NOT add `--no-barriers` escape hatch (undermines the core principle)
- Do NOT modify `/df-run-quality-gate` (read-only, no barrier needed)
