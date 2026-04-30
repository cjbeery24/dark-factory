# Feature Specification: Env Validation Failure Mode (T5 fixture)

<!-- dark-factory-metadata
feature-id: F-X5
spec_type: feature
-->

> Test fixture. T5 wire_contract_shape_mismatch. Two clauses describe the same observable surface (process termination on missing env) with disjoint shapes (exit vs throw).

## 2. Functional Requirements

### FR-1: Fail-fast on missing required environment variables

- **Input:** A node process starting without `DATABASE_URL` set in the environment.
- **Processing:** The startup env-validation step asserts presence of every required variable. On a missing required variable, the process MUST terminate by calling `process.exit(1)` after writing a single stderr line of the form `ERROR: <var> is required`. The exit-code-1 termination is the user-observable contract — the surrounding harness reads the exit code to know env-validation failed.
- **Output:** stderr contains the error line; the process is no longer running.
- **Side effects:** The process exits with code 1.

## 8. Acceptance Criteria

| ID | Acceptance Criterion | Blocking |
|----|----------------------|----------|
| AC-2 | GIVEN `DATABASE_URL` is missing WHEN the env validator runs THEN it throws `InvalidEnvError` with `code = 'ENV_MISSING'` and propagates up through the bootstrap caller, leaving the process running for the caller to handle | yes |
