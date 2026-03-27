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
