# Pre-Gate Implementation Plan

## Problem

The implementer agent has a `Phase 5: Verify — MANDATORY GATE` in its prompt, but Claude
does not reliably self-enforce this in long agentic sessions. When the implementer submits
code that fails lint or doesn't compile, the pipeline burns a full iteration (implement →
gate → review → oracle) to learn something that could have been caught in seconds. Each
full iteration is expensive.

## Solution

Add a **pre-gate** between the implementer and the full quality gate. The pre-gate runs
only Build + Lint + Typecheck (no self-tests, no blind scenarios). If it fails, the output
goes back to the implementer for a fix without consuming an iteration slot. Disabled by
default (`max_pre_gate_retries = 0`); users opt in via `[gates] pre_gate_retries = 1`
or `= 2` in their config.

Also fix Python `build_cmd` detection so mypy has packages installed.

## Architectural Notes

The pre-gate is **not** a new abstraction. It is the quality gate in a restricted mode:
same Docker image, same container arguments, same egress filtering, same command
resolution — just with an early exit after Gate 3. This ensures zero divergence between
what the pre-gate and the full quality gate evaluate.

`hard_gate_build` remains correct and intentional after the pre-gate is added. Pre-gate
retries absorb build failures early; if they exhaust and the full gate still reports
exit code 2 with `config.hard_gate_build = True`, the existing skip-review-and-oracle
path fires as before. No change to that logic.

Pre-gate sub-attempts are NOT persisted to the `state/` file. A crash mid-pre-gate loop
resumes at the start of the full iteration (fresh implementation from spec). This is an
accepted known limitation — document it in a comment in iteration.py.

---

## Part 1: `scripts/quality-gate.sh`

**Arg parsing order is critical:** `--pre-gate` must be parsed in the existing
`while [[ $# -gt 0 ]]` argument loop at the top of the script, before any writes to
`$REPORT_FILE` or `$SUMMARY_FILE`. The existing loop already runs before those writes,
so the ordering is achievable — but the implementation must not move the flag parse after
the header writes.

**Changes:**

1. Add `--pre-gate` as a boolean flag in the arg-parsing loop (no value, just `PRE_GATE=true`).

2. Immediately after arg parsing and before any writes, if `PRE_GATE=true`, override:
   ```bash
   REPORT_FILE="${DF_HOME}/reports/${SPEC_NAME}-pre-gate.md"
   SUMMARY_FILE="${DF_HOME}/reports/${SPEC_NAME}-pre-gate-summary.md"
   ```
   This prevents any partial writes to the full-gate report paths (`{spec}-quality-gate.md`,
   `{spec}-quality-gate-summary.md`).

3. After Gate 3 (Typecheck) runs, if `PRE_GATE=true`:
   - Write `## Result: PASS` or `## Result: FAIL` to `$REPORT_FILE`
   - Write `PRE_GATE_PASSED=true` or `PRE_GATE_PASSED=false` to `$SUMMARY_FILE`
   - Exit with the same codes as the full gate: `0` = all pass, `1` = non-build failure,
     `2` = build failure
   - Be careful: do not reference `$CLI_TEST_CMD`, `$SELF_TEST_CMD`, or other Gate 4+
     variables before they are initialized — with `set -euo pipefail`, referencing an unset
     variable is a fatal error. The early exit must happen before any Gate 4 variable use.

4. Gates 4-8 are never reached in pre-gate mode.

---

## Part 2: `darkfactory/pipeline/quality_gate.py`

### 2.1 — New `PreGateResult` dataclass

```python
@dataclass
class PreGateResult:
    exit_code: int     # 0=pass, 1=non-build fail, 2=build fail
    output: str = ""
```

### 2.2 — Extract `_launch_gate_container()` private helper

The Docker arg-construction block in `run_quality_gate()` (base args + project mount +
cache volumes + labels + entrypoint + egress filtering) is ~50 lines that `run_pre_gate()`
would otherwise duplicate. Extract it into a private helper:

```python
def _launch_gate_container(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    gate_cmd: list[str],
    *,
    timeout: int,
    dry_run: bool = False,
) -> RunResult:
    """Shared Docker launch for run_quality_gate() and run_pre_gate()."""
    image = f"dark-factory-{config.language}"
    docker_args = build_base_args(config, run_id)
    docker_args.extend(build_project_mount(str(project_dir), rw=True))
    docker_args.extend(build_cache_volumes("quality-gate"))
    docker_args.extend([
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=quality-gate",
        "--entrypoint", "bash",
        "--workdir", "/project",
        "-e", "DF_PYTHON_HANDLES_AB=1",
    ])
    if config.egress_filtering:
        proxy_url = f"http://{proxy_name(run_id, 'execution')}:8080"
        docker_args.extend([
            "--network", network_name(run_id, "execution"),
            "-e", f"HTTP_PROXY={proxy_url}",
            "-e", f"HTTPS_PROXY={proxy_url}",
            "-e", f"http_proxy={proxy_url}",
            "-e", f"https_proxy={proxy_url}",
            "-e", "NO_PROXY=localhost,127.0.0.1",
            "-e", "no_proxy=localhost,127.0.0.1",
        ])
    return execute_container(docker_args, image, gate_cmd, timeout=timeout, dry_run=dry_run)
```

Refactor `run_quality_gate()` to call `_launch_gate_container()`. No behavior change to
the full gate.

### 2.3 — New `run_pre_gate()` function

```python
def run_pre_gate(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    *,
    iteration: int = 1,
    dry_run: bool = False,
) -> PreGateResult:
    gate_cmd = ["/opt/dark-factory/scripts/quality-gate.sh", spec_name, "--pre-gate"]
    if config.language:
        gate_cmd.extend(["--language", config.language])
    if config.build_cmd:
        gate_cmd.extend(["--build-cmd", config.build_cmd])
    if config.lint_cmd:
        gate_cmd.extend(["--lint-cmd", config.lint_cmd])
    if config.typecheck_cmd:
        gate_cmd.extend(["--typecheck-cmd", config.typecheck_cmd])
    if config.source_dirs:
        gate_cmd.extend(["--source-dir", ":".join(config.source_dirs)])
    gate_cmd.extend(["--iteration", str(iteration)])  # for shell script consistency

    timeout = config.gate_timeout * 3  # 3 gates max
    result = _launch_gate_container(
        spec_name, config, run_id, project_dir, gate_cmd,
        timeout=timeout, dry_run=dry_run,
    )
    # RunResult exposes .stdout and .stderr (not .output) — combine them.
    output = (result.stdout or "") + (result.stderr or "")
    return PreGateResult(exit_code=result.exit_code, output=output)
```

**Important:** `run_pre_gate()` does NOT call `_ensure_gate_summary()` and does NOT start
any scenario server containers. The pre-gate report (`{spec}-pre-gate.md`) is consumed
only by the re-implementation prompt — never by the reviewer or oracle.

---

## Part 3: `darkfactory/pipeline/iteration.py`

### 3.1 — Parameterize `_step_implementation()`

Add `pre_gate_result: PreGateResult | None = None` parameter. This avoids a separate
function and ensures `spec_run.total_turns`, `iter_state.files_changed`,
`iter_state.barrier_violations`, and all other metric tracking apply equally to pre-gate
fix invocations.

Prompt construction gains a new branch (add it before the existing `iteration == 1`
branch, since pre-gate fixes can happen at any iteration):

```python
if pre_gate_result is not None:
    # Pre-gate fix: tight prompt focused on the mechanical failures.
    prompt = (
        f"Your code failed the pre-gate checks. "
        f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md "
        f"and your source files to understand the context, then fix the following errors:\n\n"
        f"{pre_gate_result.output}\n\n"
        f"Run lint, typecheck, and build until they pass. "
        f"Do not change behavior — only fix the reported errors."
    )
```

The explicit "Read the spec at..." instruction mirrors the pattern used in the normal
iteration prompts. The implementer needs the spec path to understand intent when fixing
a type error or lint violation that requires a design judgment.

### 3.2 — Pre-gate loop in `run_iteration_loop()`

Insert after Step 3 (implementation) and before Step 4 (quality gate):

```python
# Step 3.5: Pre-gate (skipped when max_pre_gate_retries == 0)
# Pre-gate failures loop back to the implementer without incrementing spec_run.iteration.
# NOTE: crashes during pre-gate retries resume at the start of this full iteration
# (fresh implementation from spec). This is an accepted known limitation.
if config.max_pre_gate_retries > 0:
    for pre_gate_attempt in range(config.max_pre_gate_retries):
        _progress(f"  Running pre-gate (attempt {pre_gate_attempt + 1}/{config.max_pre_gate_retries})...")
        pre_gate_result = run_pre_gate(
            spec_run.spec_name, config, spec_run.run_id, project_dir,
            iteration=spec_run.iteration, dry_run=dry_run,
        )
        if pre_gate_result.exit_code == 0:
            _progress("  Pre-gate passed")
            break
        _progress(f"  Pre-gate failed (attempt {pre_gate_attempt + 1})")
        if pre_gate_attempt < config.max_pre_gate_retries - 1:
            # Re-invoke implementer with pre-gate feedback.
            # spec_run.iteration does NOT increment — this is not a full iteration.
            # total_turns IS incremented inside _step_implementation (same as normal call).
            _step_implementation(
                spec_run, iter_state, config, barriers, project_dir,
                pre_gate_result=pre_gate_result,
                events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            )
```

When retries are exhausted and the pre-gate still fails, fall through to the full quality
gate. The full gate will catch it and produce structured feedback for the reviewer/oracle.

### 3.3 — Import `run_pre_gate` and `PreGateResult`

Add to the imports at the top of `iteration.py`:
```python
from darkfactory.pipeline.quality_gate import GateResult, PreGateResult, run_pre_gate, run_quality_gate
```

---

## Part 4: `darkfactory/models/config.py`

Add field in the Gates section of `PipelineConfig`:

```python
max_pre_gate_retries: int = 0  # 0 = disabled; set to 1 or 2 to enable the pre-gate
```

Add to `__post_init__` validation (follow the pattern of existing bounded-integer checks):

```python
if self.max_pre_gate_retries < 0:
    raise ConfigError("max_pre_gate_retries must be >= 0")
```

---

## Part 5: `darkfactory/config/defaults.py`

Add to `GATES_FIELDS`:

```python
"pre_gate_retries": "max_pre_gate_retries",
```

Users configure this as:
```toml
[gates]
pre_gate_retries = 2
```

---

## Part 6: `darkfactory/setup/detect.py`

**Modified `_detect_python_commands()`** — replace `result.build_cmd = ""` with:

```python
# Build — install project dependencies so mypy and other type-checkers
# can resolve imports. Prefer editable install when a package is present;
# editable install makes the project itself importable, not just its deps.
# requirements-dev.txt and other split-file patterns are not auto-detected;
# users should set build_cmd manually for those.
if (
    (project_dir / "pyproject.toml").exists()
    or (project_dir / "setup.py").exists()
    or (project_dir / "setup.cfg").exists()
):
    result.build_cmd = "pip install -e . -q"
elif (project_dir / "requirements.txt").exists():
    result.build_cmd = "pip install -r requirements.txt -q"
elif (project_dir / "requirements" / "base.txt").exists():
    result.build_cmd = "pip install -r requirements/base.txt -q"
# else: leave build_cmd = "" (no installable package detected)
```

Note: a project with both `pyproject.toml` and `requirements.txt` correctly gets
`pip install -e .` (not requirements.txt) because the check order prefers the editable
install.

---

## Files Touched

| File | Change |
|------|--------|
| `scripts/quality-gate.sh` | `--pre-gate` flag; override report paths before any writes; early exit after Gate 3 |
| `darkfactory/pipeline/quality_gate.py` | Extract `_launch_gate_container()`; add `PreGateResult`; add `run_pre_gate()` |
| `darkfactory/pipeline/iteration.py` | Pre-gate loop between steps 3 and 4; `pre_gate_result` param on `_step_implementation` |
| `darkfactory/models/config.py` | `max_pre_gate_retries: int = 0`; validation |
| `darkfactory/config/defaults.py` | `"pre_gate_retries"` in `GATES_FIELDS` |
| `darkfactory/setup/detect.py` | Python `build_cmd` detection replaces no-op |

---

## Testing the Implementation

1. **Unit tests** — add tests for `run_pre_gate()` alongside existing `run_quality_gate` tests.
2. **Integration** — set `pre_gate_retries = 1` in a test project config and verify:
   - A lint-broken implementation triggers the pre-gate → re-implementation loop
   - The full quality gate runs after pre-gate passes
   - `spec_run.iteration` does not increment during pre-gate retries
   - Pre-gate reports go to `{spec}-pre-gate.md`, not `{spec}-quality-gate.md`
3. **Default-off** — verify that `pre_gate_retries = 0` (default) produces identical
   pipeline behavior to the current codebase.
