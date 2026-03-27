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
