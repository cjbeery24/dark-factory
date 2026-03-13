---
name: df-run-quality-gate
description: Run Dark Factory quality gate checks on an implementation
argument-hint: "[spec-name]"
---

> **No Docker isolation.** Skills run in your local Claude Code session without container-enforced filesystem barriers. For full information barrier compliance, use `dark-factory run <spec>`.

# Run Quality Gate

## Critical Constraint: Read-Only Evaluation

**You MUST NOT modify any implementation code, test code, or project source files during this skill.**

This skill is a read-only evaluation step. You may only:
- **Read** files (source code, specs, configs) to understand the project
- **Run** commands via Bash (build, lint, type check, test runners)
- **Write** the quality gate report to `.dark-factory/reports/`

You MUST NOT use the Edit or Write tools on any file other than the report. If any gate fails, report the failure with details and stop. Do not attempt to fix code — that is the Implementer's responsibility in a subsequent pipeline cycle.

**Why:** The quality gate has visibility into blind scenarios. Any code fixes made here would be informed by test content that the Implementer should never see, violating the information barrier.

## Pre-flight Checks
Before proceeding, verify ALL of the following. STOP with a clear error if any fails.
1. **Spec file exists:** `.dark-factory/specs/[spec-name].md` is present
2. **Implementation files exist:** Source code files for this spec have been created
3. **Scenarios dir has test files:** `.dark-factory/scenarios/[spec-name]/` contains executable test files
4. **Config present (warn only):** `.dark-factory/config.sh` exists

## Steps

1. Identify the project's build/test toolchain
2. Run each quality gate in order:

### Gate 1: Build
```bash
# Language-specific build command
# npm run build / cargo build / go build / python -m py_compile
```
**Pass criteria:** Exit code 0, no errors

### Gate 2: Lint
```bash
# Language-specific lint command
# npm run lint / ruff check / golangci-lint run / cargo clippy
```
**Pass criteria:** Zero errors (warnings acceptable)

### Gate 3: Type Check
```bash
# Language-specific type check
# npx tsc --noEmit / mypy / (Go and Rust: covered by build)
```
**Pass criteria:** Zero type errors

### Gate 4: Self-Tests (Implementer's Own Tests)
```bash
# Run only the implementation's tests, not blind scenarios
# npm test / pytest tests/ / go test ./... / cargo test
```
**Pass criteria:** All tests pass

### Gate 5: Blind Scenarios
```bash
# Run the blind scenarios against the implementation
# npm test -- .dark-factory/scenarios/[spec-name]/
# pytest .dark-factory/scenarios/[spec-name]/
```
**Pass criteria:** All scenarios pass

3. Produce a quality gate report and write it to `.dark-factory/reports/[spec-name]-quality-gate.md`

## Output
Quality gate report with pass/fail for each gate and details of any failures.
