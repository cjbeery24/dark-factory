---
name: df-run-quality-gate
description: Run Dark Factory quality gate checks on an implementation
argument-hint: "[spec-name]"
---

# Run Quality Gate

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
