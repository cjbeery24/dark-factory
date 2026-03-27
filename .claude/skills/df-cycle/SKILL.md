---
name: df-cycle
description: Run the complete Dark Factory pipeline for a specification (guides multi-session workflow)
argument-hint: "[spec-name]"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. For full information barrier compliance, use `dark-factory run <spec>`.

# Dark Factory Full Cycle

## IMPORTANT: Session Isolation
The full cycle CANNOT run in a single Claude Code session because information barriers require separate sessions for the Scenario Architect and Implementer. This skill guides the user through the multi-session workflow.

## Pre-flight Checks
Before proceeding, verify ALL of the following. STOP with a clear error if any fails.
1. **Spec file exists:** `.dark-factory/specs/[spec-name].md` is present
2. **Find framework dir:** Run `python3 -c "import darkfactory, pathlib; print(pathlib.Path(darkfactory.__file__).parent.parent)"` and save as `FRAMEWORK_DIR`. STOP with "dark-factory package not found — run: pip install -e /path/to/dark-factory" if it fails.
3. **Framework structure exists:** `.dark-factory/agents/` directory and `.dark-factory/` subdirs are present. If missing, tell the user to run `dark-factory init --update`.
4. **Config present (warn only):** `.dark-factory/config.toml` exists

## Step 1: Verify Spec
1. Check that the spec exists in `.dark-factory/specs/`
2. Check that the spec has been validated (look for `.dark-factory/reviews/[spec-name]-validation.status` with `verdict=PASS`, `score=100`, and `divergence` not `HIGH`). If the `divergence` key is missing, re-run `/df-validate-spec`.
3. If not validated, run `/df-validate-spec` first

## Step 2: Run the Pipeline

The recommended approach is to use the CLI, which handles all steps with Docker-enforced information barriers:

```bash
dark-factory run [SPEC_NAME]
```

This runs: validation → scenario generation → implementation → quality gate → review → oracle decision, with automatic iteration on NO-SHIP (up to 3 iterations).

## Barrier-Sensitive Steps (require Docker)

Implementation and scenario generation MUST use the CLI — these steps require
Docker container isolation for information barrier enforcement.

Do NOT use `/df-implement` or `/df-generate-scenarios` — these skills have been
deprecated because they run without filesystem isolation.

## Step 3: Handle Result
- **SHIP:** Congratulate, suggest committing and opening a PR
- **NO-SHIP (iteration < 3):** The pipeline automatically iterates
- **NO-SHIP (iteration 3):** Escalate to human review

## Output
Guided walkthrough of the complete pipeline with copy-paste commands for each session.
