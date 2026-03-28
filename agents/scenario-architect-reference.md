# Scenario Architect — Reference Material

This file contains detailed examples, constraints, and instructions that supplement the core Scenario Architect prompt in `agents/scenario-architect.md`. The core prompt contains all rules and decision logic; this file provides extended examples and edge-case documentation.

---

## Format A — HTTP Behavioral Scenarios (detailed examples)

For REST APIs, HTTP services, and any spec that defines endpoints. These are language-agnostic, mechanically executable by any HTTP client.

```markdown
# Scenario: <descriptive name>
# Requirement: <FR-N or EC-N>

## Request
POST /api/users/register
Content-Type: application/json
{"email": "alice@example.com", "password": "correct-horse"}

## Expect
- Status: 201
- Body contains field "id" (non-empty string)
- Body field "email" equals "alice@example.com"

---

# Scenario: reject invalid email format
# Requirement: EC-1

## Request
POST /api/users/register
Content-Type: application/json
{"email": "not-an-email", "password": "correct-horse"}

## Expect
- Status: 422
- Body contains field "error"
- Body field "error" contains "email"
```

Multi-step sequences (e.g., create then fetch):

```markdown
# Scenario: created resource is retrievable
# Requirement: FR-2

## Step 1
POST /api/items
Content-Type: application/json
{"name": "widget"}

## Expect
- Status: 201
- Capture: item_id = body.id

## Step 2
GET /api/items/{item_id}

## Expect
- Status: 200
- Body field "name" equals "widget"
```

---

## Format B — Browser Journey Scenarios (detailed examples)

For frontend specs and UI behavior. Express user actions and visible outcomes — never React component imports or CSS selectors. Playwright resolves semantic descriptions to actual elements.

```markdown
# Scenario: inline validation on submit with empty required fields
# Requirement: FR-3

## Steps
1. navigate: /register
2. click: submit button
3. assert visible: "Email is required"
4. assert visible: "Password is required"
5. assert url: /register  (unchanged)

---

# Scenario: successful registration redirects to dashboard
# Requirement: FR-1

## Steps
1. navigate: /register
2. fill: email field with "alice@example.com"
3. fill: password field with "correct-horse"
4. click: submit button
5. assert url: /dashboard
6. assert visible: "Welcome, alice@example.com"
```

---

## Format C — Subprocess Integration Scenarios (detailed examples)

For CLI tools. Test via stdin/stdout/exit codes, not internal function calls.

**Python toolchain (`TOOLCHAIN=python`):** Write as pytest tests using `subprocess.run`.

```python
# test_01_contract.py
import subprocess

def test_help_flag_exits_zero():
    result = subprocess.run(["mytool", "--help"], capture_output=True)
    assert result.returncode == 0

def test_missing_required_arg_exits_nonzero():
    result = subprocess.run(["mytool", "process"], capture_output=True)
    assert result.returncode != 0
    assert b"required" in result.stderr.lower()
```

**Node toolchain (`TOOLCHAIN=node`):** Write as TypeScript tests using Vitest with `node:fs`, `node:path`, and `node:child_process`.

```typescript
// 01-contract.test.ts
import { execSync, spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { describe, it, expect } from "vitest";

const PROJECT_ROOT = process.env.PROJECT_ROOT ?? "/project";

describe("mytool contract", () => {
  it("help flag exits zero", () => {
    const result = spawnSync("mytool", ["--help"], { encoding: "utf8" });
    expect(result.status).toBe(0);
  });

  it("missing required arg exits nonzero", () => {
    const result = spawnSync("mytool", ["process"], { encoding: "utf8" });
    expect(result.status).not.toBe(0);
    expect(result.stderr).toMatch(/required/i);
  });

  it("expected output artifact exists", () => {
    expect(existsSync(join(PROJECT_ROOT, "dist", "mytool"))).toBe(true);
  });
});
```

**Go or Rust toolchain:** Format C is not natively supported — `go test` ignores dotted directories and `cargo test --test` runs `tests/*` only. Use Format E sentinel or note that `scenario_test_cmd` must be configured.

---

## Format D — Executable Unit Tests (detailed examples)

Use **only** when the spec explicitly defines the public module API — import paths, function names, and signatures stated in the spec itself. If you are inferring any import path, stop and use Format A or C instead.

```python
# test_01_contract.py — ONLY valid if spec states: "import from dark_factory.pipeline"
from dark_factory.pipeline import run_spec  # path taken directly from spec

def test_run_spec_returns_ship_on_passing_code():
    result = run_spec("fixtures/passing.yaml")
    assert result.decision == "SHIP"
```

**Import rule:** copy import paths verbatim from the spec. Never guess at module names, class names, or file structure.

---

## Format E — Sentinel (Non-Applicable Declaration, detailed template)

Use **only** when ALL of the following are true:
1. The spec defines no HTTP endpoints
2. The spec defines no CLI commands
3. The spec defines no importable module API
4. The spec defines no user-facing UI
5. All functional requirements are verifiable by build/lint/typecheck gates alone

**If ANY doubt exists, use Format C subprocess tests instead.**

Write `SCENARIOS-NOT-APPLICABLE.md` to `.dark-factory/scenarios/{spec-name}/`:

```markdown
# Scenarios Not Applicable

**Spec:** {spec-name}
**Date:** {date}

## Spec Classification
[Exactly one of: Project Scaffolding / Infrastructure Configuration / Build System Setup]
[Explanation of why this spec fits that classification — cite specific spec sections]

## Why Each Format Is Inapplicable

### Format A — HTTP Behavioral Scenarios
[Specific reason citing spec content]

### Format B — Browser Journey Scenarios
[Specific reason citing spec content]

### Format C — Subprocess/CLI Integration Scenarios
[Specific reason citing spec content — if the spec defines ANY command that could be
tested via subprocess, this reason will fail scrutiny. Be precise.]

### Format D — Executable Unit Tests
[Specific reason citing spec content]

## Quality Gate Coverage

| Gate | What it verifies |
|------|-----------------|
| Build | [Specific artifact] |
| Lint  | [Specific check] |
| Type Check | [If applicable] |

## Functional Requirement Coverage

| Requirement | Verification Method |
|-------------|-------------------|
| FR-1 | [Which gate verifies this and why — be specific] |
| FR-2 | [Which gate verifies this and why — be specific] |
```

**Validator will reject the sentinel if:**
- Any of the four format sections is missing
- Any section body is trivially short (just "N/A" or a single generic sentence)
- No functional requirement reference (FR-N, REQ-N, etc.) appears in the document

---

## Execution Context Constraints (detailed)

Before choosing any format, read the EXECUTION CONTEXT block at the end of the prompt delivered to you at runtime (if present — it is injected by the pipeline and will not appear when invoking this agent via a skill).

- **TOOLCHAIN=node:** Format C scenarios MUST be TypeScript (`*.test.ts`). Never generate Python (`test_*.py`) — Python is not in the node container.
- **TOOLCHAIN=go or TOOLCHAIN=rust:** Format C is not supported. Use Format E sentinel or note that `scenario_test_cmd` must be configured.
- **HAS_TEST_SERVER_CMD=false:** Format A (`.scenarios.md`) will be SILENTLY SKIPPED by the quality gate unless the operator configures `test_server_cmd`. Only generate Format A if the spec adds endpoints to an already-running service (not a scaffolding spec).
- **HAS_BROWSER_SERVER_CMD=false:** Format B (`.journey.md`) will be SILENTLY SKIPPED unless `browser_server_cmd` is configured. Same constraint as Format A.
- **HAS_SCENARIO_TEST_CMD=true:** All format restrictions are lifted — the operator has configured a custom runner and accepted responsibility for execution.

---

## Validation Enforcement (detailed)

Your generated scenarios will be automatically validated by a deterministic quality gate before the pipeline proceeds to implementation. Be aware of the following hard requirements:

1. **Minimum file count:** The pipeline enforces a minimum number of scenario files (default: 3). Produce enough files to cover the layers.
2. **Real assertions required (Format C/D):** Every executable test file must contain real assertion calls (`expect()`, `assert`, `t.Error`, `assert!`). Files with zero assertions will be rejected.
3. **Trivial assertions detected (Format C/D):** Assertions like `expect(true)`, `assert True`, `self.assertTrue(True)`, or `assert!(true)` are detected. A file where ALL assertions are trivial will be rejected.
4. **Empty test bodies detected (Format C/D):** Test functions with no logic (only whitespace, `pass`, or `...`) are detected and rejected.
5. **Go naming convention:** For Go projects, test files MUST use `*_test.go` naming.
6. **Python naming convention:** For Python projects, test files MUST use `test_NN_layer.py` naming. pytest only collects `test_*.py` or `*_test.py` by default.
7. **Go and Rust require custom scenario runners:** Go's `go test` ignores dotted directories. Rust's `cargo test --test` runs `tests/*`, not `.dark-factory/scenarios/`. A human must write a custom runner and set `SCENARIO_TEST_CMD` in `config.sh`.
8. **No test framework detected (Format C/D):** Do NOT generate non-executable placeholder files. Write a clear error to the scenario generation report and halt.
9. **TypeScript compilation (Check 9):** All `.ts` scenario files are compiled with `tsc --noEmit` before execution. Non-existent assertion methods (e.g., `toEndWith`), import failures, and syntax errors will be caught and the pipeline will fail.

---

## Self-Repair Mode

You may be re-invoked with crash output from a smoke test or quality gate run. When your prompt contains the phrase **"You are in REPAIR MODE"**, follow these rules:

### What you receive
- Your own previously generated scenario files (in `.dark-factory/scenarios/{spec-name}/`)
- Sanitized crash information: error class name + line number within the scenario file. Source paths are redacted.
- You do NOT receive implementation source code. Source directories remain masked.

### What to fix
- **CRASH errors only:** `TypeError`, `ReferenceError`, `SyntaxError`, `Cannot find module`, import resolution failures
- Common repair patterns:
  - Add null/undefined guards before accessing properties
  - Fix `stdout` vs `stderr` — TypeScript (`tsc`), ESLint, and Prettier write diagnostics to **stdout**, not stderr
  - Remove build-tool spawns and replace with filesystem assertions
  - Fix import paths that reference modules not defined in the spec

### What NOT to fix
- **Assertion failures:** These are legitimate test results. The implementation may be wrong — that is for the Implementer to fix. Do NOT change assertion expectations.
- **Do NOT weaken assertions:** Do not replace specific assertions with weaker ones. Do not delete test functions. Do not reduce assertion count per file.
- **Do NOT add new tests:** Repair mode is for fixing existing scenarios, not generating new ones.

### Post-repair validation
After your repair, the pipeline will mechanically verify:
1. Only files with CRASH-type failures were modified
2. Total assertion count per file did not decrease
3. No test functions were deleted
4. No trivial assertions were introduced

If any check fails, your repair is reverted and the original scenarios are used.
