# Plan: Scenario Format Compatibility

## Problem Statement

The Scenario Architect's format decision tree is one-dimensional: it asks "what does the spec describe?" but never "can this format execute in this quality gate context?" Executability is a function of both spec content AND deployment context — toolchain, server availability, configured commands.

This led to the budget-brain scaffolding run generating 4 format types, with 3 unexecutable:

| Format | Generated | Can run | Root incompatibility |
|--------|-----------|---------|----------------------|
| `shared/*.test.ts` (Format D) | 4 files | YES | — |
| `api/*.scenarios.md` (Format A) | 8 files | NO | No `TEST_SERVER_CMD`; server not running |
| `ui/*.journey.md` (Format B) | 8 files | NO | No `BROWSER_SERVER_CMD` |
| `workspace/test_*.py` (Format C) | 8 files | NO | Python not in node container |

The budget-brain spec was a scaffolding spec that creates a server — not a spec that adds endpoints to an already-running server. The decision tree cannot distinguish these two cases today, and it systematically routes all "HTTP API" specs to Format A regardless of whether the server will be running during the quality gate.

The current quality gate fix (FAIL on Python-in-node) is correct as a backstop but fires after an iteration is consumed. The root problem is the Scenario Architect's lack of execution context at generation time.

Note on over-generation: the 8 Format A files for a single `/health` endpoint were generated because the decision tree incorrectly routed a scaffolding spec to Format A. The decision tree fix in Layer 3 (the scaffolding fork) organically prevents this over-generation for the scaffolding case, as a side effect of routing the spec correctly.

---

## Root Cause Analysis

### 1. Decision tree has no toolchain axis

Format C is documented as Python subprocess tests. The agent applies this for any "non-HTTP, non-UI, non-library" behavior regardless of toolchain. For a node project, Python tests cannot run. The same structural test (does this file exist? does this package.json have these fields?) written as TypeScript vitest with `fs.readFileSync` would work. The fix is not "don't use Python for node projects" — it's that Format C is not inherently Python; it should be toolchain-conditional.

### 2. Scaffolding specs systematically trigger Format A/B incorrectly

A spec that creates a server and a spec that adds an endpoint to an existing server appear identical to the decision tree: both define HTTP endpoints. But a scaffolding spec has no running server during the quality gate — the scaffold IS the output. Format A requires a running server as a precondition.

The Format E (sentinel) criteria are insufficient. They ask "does the spec have testable runtime behavior?" — this scaffolding spec does (once the scaffold exists), so the sentinel was correctly rejected. What's missing is a branch in the decision tree: "testable behavior exists, but the runtime is the output of this spec, not a precondition of it."

### 3. No validation gate between generation and iteration

The pipeline goes from Scenario Generation (Step 2) directly to the Iteration Loop (Step 3). The first time incompatibility is detected is in the quality gate, after the implementer has run. This wastes an iteration.

### 4. Quality gate backstop is partial

The current quality gate FAIL for Python-in-node fires only when Python is the *only* executable format. When Python and TypeScript tests coexist in the same scenarios directory, the TypeScript tests run and the Python tests are silently skipped with a WARNING. This is weaker than a hard backstop. It is acceptable only because Layer 1 (the new pipeline validator) will catch the case before any iteration.

---

## Solution: Three Complementary Layers

The fix requires all three layers. No single layer is sufficient alone.

### Layer 1: Scenario Format Compatibility Validator (new pipeline step)

**What:** A new Python function `check_format_compatibility(scenarios_dir, config)` in `darkfactory/scenarios/compat.py`, called by the orchestrator after Step 2 (scenario generation) and before Step 3 (iteration loop). Deterministic. No LLM calls.

**How it works:**
1. Detects which formats are present using existing `detection.py` patterns
2. Reads config to determine toolchain and configured commands
3. Returns a `CompatResult` dataclass with `hard_errors: list[str]` and `warnings: list[str]`

**Compatibility check table (precise logic):**

| Format present | Hard error condition | Warning condition |
|----------------|---------------------|-------------------|
| `*.scenarios.md` (Format A) | `scenario_test_cmd` is empty AND `test_server_cmd` is empty AND `test_base_url` is empty AND `toolchain == "python"` (Python projects with HTTP scenarios and no server are likely errors) | `scenario_test_cmd` is empty AND `test_server_cmd` is empty AND `test_base_url` is empty AND `toolchain != "python"` (node/go/rust: Format A may be intentionally deferred) |
| `*.journey.md` (Format B) | Same hard-error condition as Format A but for browser | Same warning condition as Format A |
| `test_*.py` / `*_test.py` (Python) | `toolchain in ("node", "go", "rust")` AND `scenario_test_cmd` is empty | — |

Key points on this table:
- Python-in-node/go/rust without `scenario_test_cmd` is always a **hard error**. pytest is definitively absent from these containers.
- Format A/B without server config is a **hard error only for Python toolchain** projects (a Python API project with HTTP scenarios but no server configured is almost certainly a mistake). For node/go/rust, it is a **warning** — intentionally-deferred scenarios for a future pipeline run are a valid use case.
- `scenario_test_cmd` bypasses ALL format-incompatibility checks — if the operator has configured a custom runner, they have accepted responsibility for execution.
- `toolchain == "unknown"` (undetected): emit warnings for all potentially-incompatible formats, no hard errors.

**Handling "0 HTTP scenarios run" — Oracle and Reviewer:**

When Format A/B warnings fire (not errors), the pipeline proceeds and the quality gate silently SKIPs those scenarios. This is the intended behavior for intentionally-deferred scenarios. The Oracle and Reviewer must interpret SKIP correctly.

The Oracle and Reviewer agent definitions must be updated (see Layer 3) to add the following rule: "If the quality gate report shows SKIP for blind scenarios (HTTP or Journey), and `test_server_cmd`/`browser_server_cmd` is not configured, this is an acceptable outcome — the scenarios are deferred, not failed. Do not treat absent HTTP/Journey results as a quality gap unless the spec requires a running service."

**Pipeline integration — state-safe design:**

The compat check must not advance `completed_step` in the state file. It runs between steps 2 and 3 but is not itself a checkpoint. The check must run both on first run and on regen:

```python
# orchestrator.py — after _step_scenario_generation, before run_iteration_loop

# Step 2.5: Scenario format compatibility check.
# This check runs every time scenarios are present and the iteration loop has not started.
# It does NOT advance completed_step — it is a guard, not a pipeline stage.
# On resume, if completed_step >= 3, the check is skipped (iteration loop already started).
if spec_run.completed_step < 3:
    from darkfactory.scenarios.compat import check_format_compatibility
    compat = check_format_compatibility(scenarios_dir, config)
    for warning in compat.warnings:
        log.warning("scenario compat: %s", warning)
    if compat.hard_errors:
        # Do NOT advance completed_step — leave state at 2 so regen can fix the problem.
        raise DarkFactoryError(
            "Scenario format incompatibility detected — fix before implementation:\n"
            + "\n".join(f"  • {e}" for e in compat.hard_errors)
            + "\n\nOptions: (1) Regenerate scenarios with correct format, "
            + "(2) Set scenario_test_cmd in config to provide a custom runner, "
            + "(3) Add SCENARIOS-NOT-APPLICABLE.md to opt out."
        )
```

**On resume after hard error:** The state file shows `completed_step=2`. A subsequent `--resume` or fresh invocation re-enters the pipeline at step 2.5 (since `completed_step < 3` is still True). The user fixes the scenarios (via regen or manual edit) and runs again; the check re-runs against the updated scenarios directory. This is the correct recovery loop.

**On `--regen-scenarios`:** Regen deletes and rewrites the scenarios directory, then re-runs the compat check (since `completed_step` stays at 2). The check runs against the new scenarios. This is correct.

**Files:** `darkfactory/scenarios/compat.py` (new), `darkfactory/pipeline/orchestrator.py` (add step 2.5)

---

### Layer 2: Execution Context Injected into Scenario Architect

**What:** The pipeline injects a structured execution context block into the Scenario Architect's stdin prompt. The agent uses this to disqualify ineligible formats before writing any files.

**Mechanism:** Read `agents.py` to confirm: scenario generation invokes `invoke_agent`, which passes a `prompt` string via stdin (constructed in `_step_scenario_generation`). The execution context block is appended to the stdin prompt before passing it to `invoke_agent` — no changes to `build_claude_args` or `BarrierConfig` are required.

```python
# orchestrator.py — in _step_scenario_generation, before calling invoke_agent

# Build execution context block to inject into the Scenario Architect's prompt.
# Pass boolean flags only — not the actual command strings, which may contain
# environment-specific paths. This does not weaken the information barrier:
# toolchain and server configuration are not implementation details.
context_block = "\n\n## Execution Context\n\n"
context_block += f"TOOLCHAIN: {config.language or 'unknown'}\n"
context_block += f"HAS_TEST_SERVER_CMD: {'true' if config.test_server_cmd else 'false'}\n"
context_block += f"HAS_BROWSER_SERVER_CMD: {'true' if config.browser_server_cmd else 'false'}\n"
context_block += f"HAS_SCENARIO_TEST_CMD: {'true' if config.scenario_test_cmd else 'false'}\n"
context_block += "\n"
context_block += "Apply the Execution Context Constraints from your agent definition before choosing any scenario format.\n"

prompt = existing_prompt + context_block
```

This uses the resolved config (already passed to `_step_scenario_generation`), so per-spec profile overrides are correctly reflected. Config values at `_step_scenario_generation` invocation time are the fully-resolved values from `resolve_profile`.

**Information barrier:** The toolchain and presence/absence of server commands are not implementation details. They describe the execution environment, not the code being implemented. This is safe to inject.

**This is probabilistic, not deterministic.** The agent may still make mistakes. Layer 1 is the deterministic backstop. Layer 2 reduces the probability of reaching Layer 1 with an error.

**Files:** `darkfactory/pipeline/orchestrator.py` (`_step_scenario_generation` function), `agents/scenario-architect.md` (add Execution Context Constraints section)

---

### Layer 3: Agent Definition and Downstream Agent Updates

#### 3a. Scenario Architect — decision tree restructure

**Change 1: Scaffolding fork before Format A/B**

The current top-level question "Is it an HTTP API / REST service?" must be replaced with a two-part check:

```
Is it an HTTP API / REST service?
  └─ YES → Is the server ALREADY RUNNING during the quality gate?
           (i.e., the spec adds endpoints/features to a service that exists BEFORE
           this spec runs — not a spec that creates/scaffolds the server itself)
       └─ YES (adding to existing service) → Format A
       └─ NO (spec creates the server; the server is the output, not the precondition)
            → The server does not exist during the quality gate.
              Use Format D TypeScript tests to verify scaffold artifacts:
              file existence, package.json fields, tsconfig configuration, env files.
              Use Node.js `node:fs` and `node:path` builtins — no running server needed.
              Do NOT generate Format A for this spec type.
```

Diagnostic question for the architect: "If I ran `dark-factory gate` right now against a fresh clone of the repo, would the server be running?" If the answer is "no, this spec creates the server," Format A is ineligible.

**Change 2: Format C is toolchain-conditional**

Replace the current Format C description ("For CLI tools. Write as pytest tests using `subprocess.run`") with:

> **Format C — Subprocess / Filesystem Integration Scenarios**
>
> For CLI tools and filesystem/workspace structure verification. Write in the project's native test language based on `TOOLCHAIN` from the Execution Context:
>
> - **TOOLCHAIN=node or TypeScript:** Write as `*.test.ts` using Vitest with Node.js `node:fs`, `node:path`, and `node:child_process`. Name files `*.test.ts`.
> - **TOOLCHAIN=python:** Write as `test_*.py` using pytest and `subprocess.run`.
> - **TOOLCHAIN=go or rust:** Format C is not natively supported. Write `SCENARIOS-NOT-APPLICABLE.md` and note that `scenario_test_cmd` must be configured for executable scenarios.
>
> The Python pytest example below applies to TOOLCHAIN=python only.

Add a TypeScript subprocess/filesystem example:

```typescript
// workspace_structure.test.ts
// Format C for TOOLCHAIN=node — filesystem verification without a running server
// WARNING: Vitest may not set CWD to the project root. Use process.env.PROJECT_ROOT
// (injected by the quality gate container) or resolve from an absolute anchor.
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// PROJECT_ROOT is set to /project in the quality gate Docker container.
// Fall back to process.cwd() only in local development.
const ROOT = process.env.PROJECT_ROOT ?? process.cwd();

describe('Workspace structure', () => {
  it('should have required package directories', () => {
    expect(existsSync(resolve(ROOT, 'apps/web'))).toBe(true);
    expect(existsSync(resolve(ROOT, 'apps/api'))).toBe(true);
    expect(existsSync(resolve(ROOT, 'packages/shared'))).toBe(true);
  });

  it('should have pnpm workspace configuration', () => {
    const yaml = readFileSync(resolve(ROOT, 'pnpm-workspace.yaml'), 'utf-8');
    expect(yaml).toContain('apps/*');
    expect(yaml).toContain('packages/*');
  });

  it('should have required root package.json scripts', () => {
    const pkg = JSON.parse(readFileSync(resolve(ROOT, 'package.json'), 'utf-8'));
    expect(pkg.scripts).toHaveProperty('build');
    expect(pkg.scripts).toHaveProperty('test');
    expect(pkg.scripts).toHaveProperty('lint');
  });
});
```

**Note on `PROJECT_ROOT`:** The quality gate container must inject `PROJECT_ROOT=/project` as an environment variable when running vitest. Confirm this is already set or add it to the quality gate container invocation. If not already present, add to `scripts/quality-gate.sh` wherever vitest is invoked for scenario execution.

**Change 3: Add Execution Context Constraints section**

Add a new section immediately after "Step 1: Choose the Right Scenario Format" that reads the injected execution context:

```markdown
## Execution Context Constraints

Before choosing any format, read the EXECUTION CONTEXT block at the end of this prompt.
Apply these constraints:

- **TOOLCHAIN=node:** Format C scenarios MUST be TypeScript (`*.test.ts`). Never generate
  Python (`test_*.py`) — Python is not in the node container.
- **TOOLCHAIN=go or TOOLCHAIN=rust:** Format C is not supported. Use Format E sentinel or
  note that `scenario_test_cmd` must be configured.
- **HAS_TEST_SERVER_CMD=false:** Format A (`.scenarios.md`) will be SILENTLY SKIPPED by
  the quality gate unless the operator configures `test_server_cmd`. Only generate Format A
  if the spec adds endpoints to an already-running service (not a scaffolding spec).
- **HAS_BROWSER_SERVER_CMD=false:** Format B (`.journey.md`) will be SILENTLY SKIPPED
  unless `browser_server_cmd` is configured. Same constraint as Format A.
- **HAS_SCENARIO_TEST_CMD=true:** All format restrictions are lifted — the operator has
  configured a custom runner and accepted responsibility for execution.
```

#### 3b. Oracle and Reviewer — SKIP interpretation

Add a rule to the Oracle and Reviewer agent definitions:

> **Interpreting SKIP results for HTTP and Journey scenarios:**
> If the quality gate report shows SKIP for "Blind Scenarios (HTTP)" or "Blind Scenarios (Journey)", and `HAS_TEST_SERVER_CMD` / `HAS_BROWSER_SERVER_CMD` is false in the context, this is acceptable — the scenarios are intentionally deferred. Do not treat absent HTTP/Journey results as a quality gap. Base the SHIP/NO-SHIP decision on the scenarios that did run (executable unit tests and any configured server scenarios).

**Files:** `agents/scenario-architect.md`, `agents/oracle.md`, `agents/reviewer.md`

---

## Skill Path Gap

The `/df-generate-scenarios` skill path runs outside the pipeline orchestrator. It has no `PipelineConfig` object, so:
- Layer 1 (compat validator) does not run
- Layer 2 (context injection) does not run

This is a known limitation. The skill path is explicitly scoped as a "developer convenience tool, not a compliant pipeline execution method" (per CLAUDE.md). Addressing the skill path is out of scope for this plan. Document this explicitly in the skill's help text: "Scenarios generated via this skill are not validated for execution context compatibility. Run `dark-factory run` to get compatibility validation."

---

## `PROJECT_ROOT` Environment Variable

The TypeScript Format C example requires `PROJECT_ROOT` to be available in the quality gate container to resolve filesystem paths correctly. Confirm whether `quality-gate.sh` already sets this, or add `export PROJECT_ROOT=/project` (or the actual project mount path) before invoking vitest for scenario execution. This is a prerequisite for the TypeScript Format C example to work correctly.

---

## Files to Change

| File | Change | Layer |
|------|--------|-------|
| `darkfactory/scenarios/compat.py` | New: format compatibility checker (`CompatResult`, `check_format_compatibility`) | 1 |
| `darkfactory/pipeline/orchestrator.py` | Add step 2.5 compat check; append execution context block to Scenario Architect prompt in `_step_scenario_generation` | 1, 2 |
| `agents/scenario-architect.md` | Scaffolding fork in decision tree; Format C toolchain-conditional; Execution Context Constraints section | 3 |
| `agents/oracle.md` | Add SKIP interpretation rule for Format A/B | 3 |
| `agents/reviewer.md` | Add SKIP interpretation rule for Format A/B | 3 |
| `scripts/quality-gate.sh` | Set `PROJECT_ROOT` before vitest invocation for scenario execution (if not already present) | prerequisite |

---

## Out of Scope

- Go and Rust native Format C support — `SCENARIO_TEST_CMD` is the documented workaround
- Cross-container execution (running pytest from inside a node container) — larger architecture change
- Over-generation for non-scaffolding HTTP specs (endpoint-addition case) — the scaffolding fork addresses the budget-brain incident; over-generation on "adds endpoint to existing server" specs is a separate quality concern
- Skill path compatibility validation — explicitly deferred; skills are not compliant pipeline paths

---

## Known Limitations

- Layer 2 (context injection) is probabilistic. A sufficiently confused agent could still generate Python tests in a node project. Layer 1 catches this before an iteration is consumed.
- The "scaffolding creates server" fork (Change 1) requires semantic judgment from the agent — it cannot be made fully rule-based from a prompt. Layer 1 does not attempt to detect this case (it would require reading the spec). The execution context constraints help (HAS_TEST_SERVER_CMD=false signals the operator hasn't configured a server), but a motivated agent could still generate Format A.
- Format A/B warnings (not errors) mean intentionally-deferred scenarios are indistinguishable from accidentally-generated ones in automated output. This is acceptable given the Oracle/Reviewer SKIP rule update.
- Quality gate backstop for Python-in-node is partial: it fires as a hard FAIL only when Python is the only executable format. Mixed Python+TypeScript scenarios produce a WARNING and run the TypeScript subset. Layer 1 catches pure-Python-in-node before any iteration; the mixed case is caught by the WARNING and should be treated as a defect in the scenarios.
- The skill path (`/df-generate-scenarios`) does not benefit from Layer 1 or Layer 2. This is accepted.
