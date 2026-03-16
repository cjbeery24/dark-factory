# Plan: Multi-Container Scenario Runner Orchestrator

**Date:** 2026-03-16
**Reviewed by:** neo:neo (2 passes, approved)

See `2026-03-16-scenario-runners-findings.md` for full findings.

---

## Progress

| Step | Status | Notes |
|------|--------|-------|
| 1 — `start_container()` in `containers.py` | `[x]` | |
| 2 — `quality-gate.sh` A/B handoff | `[ ]` | includes `satisfaction.sh` multi-run path |
| 3 — `_rewrite_base_url()` | `[ ]` | |
| 4 — Server container lifecycle | `[ ]` | `_start_server_container`, `_wait_for_server`, port binding, `pids_limit` param |
| 5 — Format A runner container | `[ ]` | `_run_format_a_runner()` |
| 6 — Format B runner container | `[ ]` | `_run_format_b_runner()` |
| 7 — Orchestration in `run_quality_gate()` | `[ ]` | depends on 1–6 |
| 8 — Gate report rows for A/B | `[ ]` | `_append_gate_report_row()` |
| 9 — Fix `_ensure_counts_file()` ordering | `[ ]` | |
| 10 — `_accumulate_ab_scenario_counts()` | `[ ]` | depends on 9 |
| Tests | `[ ]` | orchestrator, URL rewriting, count accumulation |
| Docs | `[ ]` | `TOOLS.md`, `WORKFLOW.md` |

Status values: `[ ]` not started · `[~]` in progress · `[x]` done · `[!]` blocked

---

## Problem Statement

`quality-gate.sh` invokes `journey-runner.py` (Playwright) from inside `dark-factory-{language}`, which doesn't have Playwright. The `_check_playwright_image()` pre-flight checks the playwright image exists but never uses it. Format B (`*.journey.md`) scenarios are broken.

Root cause: all scenario runners run in a single container that can only satisfy one image's dependencies.

---

## Solution

Move Format A/B scenario runner invocation from `quality-gate.sh` into `quality_gate.py`. Python becomes the orchestrator: it spawns the correct container for each format and handles server lifecycle. `quality-gate.sh` shrinks to Gates 1–4 (build/lint/typecheck/self-tests) plus executable scenarios (C/D).

### Container topology after refactor

```
┌─────────────────────────────────────────────────┐
│  df-{run_id}-execution network                  │
│                                                 │
│  [lang container] ← dark-factory-{language}     │
│    Gate 1: build                                │
│    Gate 2: lint                                 │
│    Gate 3: typecheck                            │
│    Gate 4: self-tests                           │
│    Gate 5: executable scenarios (C/D)           │
│                                                 │
│  [server container] ← dark-factory-{language}   │
│    server_cmd / browser_server_cmd              │
│    (only started if configured)                 │
│                                                 │
│  [http-runner] ← dark-factory-python            │
│    http-scenario-runner.py                      │
│    (only started if *.scenarios.md exist)       │
│                                                 │
│  [journey-runner] ← dark-factory-playwright     │
│    journey-runner.py                            │
│    (only started if *.journey.md exist)         │
└─────────────────────────────────────────────────┘
```

All containers share the project bind mount (`/project:rw`) so runner outputs land on the host filesystem. All containers join `df-{run_id}-execution`.

---

## Implementation Steps

### Step 1 — Add `start_container()` to `darkfactory/docker/containers.py`

**Files:** `darkfactory/docker/containers.py`

```python
def start_container(
    docker_args: list[str],
    image: str,
    cmd: list[str],
    *,
    name: str | None = None,
    dry_run: bool = False,
) -> str:
    """Start a container in detached mode (-d). Returns container ID or '' in dry_run."""
```

`containers.py` already has `stop_container(name)` and `remove_container(name)` — no change needed there.

---

### Step 2 — Modify `quality-gate.sh`: hand off A/B to Python

**Files:** `scripts/quality-gate.sh`, `scripts/lib/satisfaction.sh`

Add `DF_PYTHON_HANDLES_AB` env var check at the start of Gate 5:

```bash
if [[ "${DF_PYTHON_HANDLES_AB:-0}" == "1" ]]; then
    echo "  Blind Scenarios (HTTP/Journey): delegated to Python orchestrator"
    # Fall through to executable scenarios only
fi
```

When `DF_PYTHON_HANDLES_AB=1`:
- Skip `_df_start_server` for HTTP and journey formats
- Skip `_run_format_runner` for `http-scenario-runner.py` and `journey-runner.py`
- Still run `_df_run_scenarios_satisfaction` for executable formats (C/D)
- Still run `_df_accumulate_format_counts` for C/D fragments only

When `DF_PYTHON_HANDLES_AB` is unset (standalone invocation for debugging): keep existing behavior. The standalone path is developer convenience only — not a production-complete execution path after this refactor.

Python sets `DF_PYTHON_HANDLES_AB=1` in the container environment via `-e DF_PYTHON_HANDLES_AB=1` in `docker_args`.

**Multi-run path:** `_df_run_scenarios_satisfaction` in `satisfaction.sh` internally finds and runs A/B format files via the same `find` pattern. This function must also respect `DF_PYTHON_HANDLES_AB` — filter out `*.scenarios.md` and `*.journey.md` before the multi-run loop when the flag is set.

---

### Step 3 — Base URL substitution: `localhost` → container name

**Files:** `darkfactory/pipeline/quality_gate.py`

```python
def _rewrite_base_url(url: str, server_name: str) -> str:
    """Replace localhost/127.0.0.1 with the server container name for Docker networking."""
    from urllib.parse import urlparse, urlunparse
    parsed = urlparse(url)
    if parsed.hostname in ("localhost", "127.0.0.1"):
        netloc = parsed.netloc.replace(parsed.hostname, server_name)
        return urlunparse(parsed._replace(netloc=netloc))
    return url  # custom hostname: pass through unchanged
```

Users who configure `base_url = "http://localhost:8000"` (the common case) get it rewritten automatically. Custom hostnames pass through unchanged.

---

### Step 4 — Server container lifecycle in `quality_gate.py`

**Files:** `darkfactory/pipeline/quality_gate.py`, `darkfactory/docker/args.py`

**4a — `pids_limit` parameter in `build_base_args`:**

`build_base_args` currently hard-codes `--pids-limit=256`. Add an optional `pids_limit: int = 256` parameter. The server container uses `512` (multi-worker app servers can exceed 256 PIDs). Backward-compatible.

**4b — `_start_server_container` and `_wait_for_server`:**

```python
def _server_container_name(run_id: str) -> str:
    return f"df-{run_id}-server"

def _start_server_container(
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    server_cmd: str,
    *,
    base_url: str,
    dry_run: bool = False,
) -> str:
    """Start a detached server container. Returns container name.

    Publishes the server port to 127.0.0.1 on the host so the Python
    health poll can reach it (Docker internal DNS only works inside containers).
    """
    name = _server_container_name(run_id)
    docker_args = build_base_args(config, run_id, pids_limit=512)
    docker_args.extend(build_project_mount(str(project_dir), rw=False))
    # Publish port to loopback for host health poll
    from urllib.parse import urlparse
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    docker_args.extend([
        "-p", f"127.0.0.1:{port}:{port}",
        "--network", network_name(run_id, "execution"),
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=test-server",
        "--name", name,
        "--workdir", "/project",
    ])
    start_container(docker_args, f"dark-factory-{config.language}", ["-c", server_cmd], name=name, dry_run=dry_run)
    return name

def _wait_for_server(base_url: str, health_path: str, timeout: int, *, dry_run: bool = False) -> None:
    """Poll health endpoint until 200 or timeout. No-op in dry_run mode.

    IMPORTANT: base_url must be the HOST-reachable URL (localhost:{port}).
    Docker internal DNS only resolves inside containers on the same network.
    This function runs in the Python host process.
    """
    if dry_run:
        return
    import time, urllib.request
    url = base_url.rstrip("/") + health_path
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if resp.status == 200:
                    return
        except Exception:
            pass
        time.sleep(1)
    raise TimeoutError(f"Server did not become healthy at {url} within {timeout}s")
```

**Server deduplication:** Compare `config.test_server_cmd` and `config.browser_server_cmd`. If they are the same string (or one is None), start one container and reuse it for both HTTP and browser runner base URLs. If different, start two containers (`df-{run_id}-server` and `df-{run_id}-browser-server`).

---

### Step 5 — Format A runner container in `quality_gate.py`

**Files:** `darkfactory/pipeline/quality_gate.py`

```python
def _run_format_a_runner(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    base_url: str,           # already rewritten (localhost → container name)
    *,
    dry_run: bool = False,
) -> ContainerResult:
    """Run http-scenario-runner.py in dark-factory-python."""
    docker_args = build_base_args(config, run_id)
    docker_args.extend(build_project_mount(str(project_dir), rw=True))
    docker_args.extend(build_cache_volumes("http-runner"))
    docker_args.extend([
        "--network", network_name(run_id, "execution"),
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=http-runner",
        "--workdir", "/project",
    ])
    cmd = [
        "python3", "/project/scripts/lib/http-scenario-runner.py",
        f"/project/.dark-factory/scenarios/{spec_name}",
        "--base-url", base_url,
        "--reports-dir", "/project/.dark-factory/reports",
        "--spec-name", spec_name,
    ]
    return execute_container(
        docker_args, "dark-factory-python", cmd,
        timeout=config.gate_timeout,
        dry_run=dry_run,
    )
```

---

### Step 6 — Format B runner container in `quality_gate.py`

**Files:** `darkfactory/pipeline/quality_gate.py`

```python
def _run_format_b_runner(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    base_url: str,           # already rewritten
    *,
    dry_run: bool = False,
) -> ContainerResult:
    """Run journey-runner.py in dark-factory-playwright."""
    docker_args = build_base_args(config, run_id)
    docker_args.extend(build_project_mount(str(project_dir), rw=True))
    docker_args.extend(build_cache_volumes("journey-runner"))
    docker_args.extend([
        "--network", network_name(run_id, "execution"),
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=journey-runner",
        "--workdir", "/project",
    ])
    cmd = [
        "python3", "/project/scripts/lib/journey-runner.py",
        f"/project/.dark-factory/scenarios/{spec_name}",
        "--base-url", base_url,
        "--reports-dir", "/project/.dark-factory/reports",
        "--spec-name", spec_name,
    ]
    return execute_container(
        docker_args, "dark-factory-playwright", cmd,
        timeout=config.gate_timeout,
        dry_run=dry_run,
    )
```

---

### Step 7 — Orchestration in `run_quality_gate()` after language container exits

**Files:** `darkfactory/pipeline/quality_gate.py`

```python
# After execute_container() for the language container (gates 1-4 + C/D):
formats = _detect_formats(project_dir, spec_name)  # uses darkfactory/scenarios/detection.py

server_name: str | None = None
browser_server_name: str | None = None

try:
    # Start HTTP server if Format A needs it
    if formats.has_http and config.test_server_cmd:
        original_base_url = config.test_base_url or "http://localhost:8000"
        server_name = _start_server_container(
            config, run_id, project_dir, config.test_server_cmd,
            base_url=original_base_url, dry_run=dry_run,
        )
        # Host polls original localhost URL (port published via -p 127.0.0.1:{port}:{port})
        _wait_for_server(original_base_url, config.server_health_path or "/health",
                         config.server_startup_timeout, dry_run=dry_run)
        # Runner containers use container name for Docker-internal routing
        rewritten_base_url = _rewrite_base_url(original_base_url, server_name)
    else:
        original_base_url = config.test_base_url or "http://localhost:8000"
        rewritten_base_url = original_base_url

    if formats.has_http:
        a_result = _run_format_a_runner(spec_name, config, run_id, project_dir,
                                        rewritten_base_url, dry_run=dry_run)
        _append_gate_report_row(project_dir, spec_name, "Blind Scenarios (HTTP)", a_result.exit_code)
        if a_result.exit_code != 0:
            gate_result.exit_code = 1

    # Start browser server if Format B needs it (deduplicate if same command)
    if formats.has_journey:
        _check_playwright_image(spec_name, project_dir)
        original_browser_url = config.browser_base_url or "http://localhost:3000"
        if config.browser_server_cmd and config.browser_server_cmd != config.test_server_cmd:
            browser_server_name = _start_server_container(
                config, run_id, project_dir, config.browser_server_cmd,
                base_url=original_browser_url, dry_run=dry_run,
            )
            # Host health poll uses original localhost URL
            _wait_for_server(original_browser_url, "/health",
                             config.browser_startup_timeout, dry_run=dry_run)
            rewritten_browser_url = _rewrite_base_url(original_browser_url, browser_server_name)
        elif config.browser_server_cmd and server_name:
            # Same command as HTTP server — reuse its container
            rewritten_browser_url = _rewrite_base_url(original_browser_url, server_name)
        else:
            rewritten_browser_url = original_browser_url

        b_result = _run_format_b_runner(spec_name, config, run_id, project_dir,
                                        rewritten_browser_url, dry_run=dry_run)
        _append_gate_report_row(project_dir, spec_name, "Blind Scenarios (Journey)", b_result.exit_code)
        if b_result.exit_code != 0:
            gate_result.exit_code = 1

finally:
    for cname in [server_name, browser_server_name]:
        if cname:
            _stop_server_container(cname, dry_run=dry_run)

# Accumulate A/B runner counts (AFTER runners finish) into canonical counts file
_accumulate_ab_scenario_counts(project_dir, spec_name, gate_result)
# _ensure_counts_file must come AFTER accumulation (see Step 9)
_ensure_counts_file(project_dir, spec_name, gate_result)
```

---

### Step 8 — Write gate report rows for A/B in Python

**Files:** `darkfactory/pipeline/quality_gate.py`

`quality-gate.sh` writes rows to `.dark-factory/reports/{spec}-quality-gate.md`. After the refactor, A/B results are produced after the shell container exits, so Python must append them:

```python
def _append_gate_report_row(project_dir: Path, spec_name: str, gate_name: str, exit_code: int) -> None:
    """Append a result row to the quality gate markdown report."""
    report_path = project_dir / ".dark-factory" / "reports" / f"{spec_name}-quality-gate.md"
    if not report_path.exists():
        return
    status = "PASS" if exit_code == 0 else "FAIL"
    with report_path.open("a") as f:
        f.write(f"| {gate_name} | {status} |\n")
```

---

### Step 9 — Fix `_ensure_counts_file()` ordering

**Files:** `darkfactory/pipeline/quality_gate.py`

`_ensure_counts_file()` is currently called immediately after `execute_container()` returns (before A/B runners run). It writes zeros before A/B runners have produced their counts.

Move the call to after `_accumulate_ab_scenario_counts()`. The function's contract is "write zeros if the file doesn't exist" — moving it later is safe because the file will either exist (written by a runner) or not (truly no scenarios ran).

---

### Step 10 — Accumulate scenario counts in Python

**Files:** `darkfactory/pipeline/quality_gate.py`

```python
def _accumulate_ab_scenario_counts(project_dir: Path, spec_name: str, gate_result: GateResult) -> None:
    """Merge A/B runner count fragments into the canonical scenario counts file.

    The language container already wrote C/D counts (or nothing).
    The A/B runner containers wrote their own fragments.
    Merge all fragments into the canonical {spec}-scenario-counts.env.
    Then call _parse_scenario_counts to update gate_result.

    NOT IDEMPOTENT — must be called exactly once. Calling twice would double-count
    A/B fragments because the first call writes them into the canonical file
    and the second call would read both the canonical file and the fragments again.
    """
    reports_dir = project_dir / ".dark-factory" / "reports"
    fragments = list(reports_dir.glob(f"{spec_name}-scenario-counts-*.env"))
    canonical = reports_dir / f"{spec_name}-scenario-counts.env"

    # Read existing canonical file (C/D counts from shell) if it exists
    merged: dict[str, int] = {"SCENARIO_PASSED": 0, "SCENARIO_FAILED": 0, "SCENARIO_TOTAL": 0}
    if canonical.exists():
        for line in canonical.read_text().splitlines():
            key, _, val = line.partition("=")
            if key in merged:
                merged[key] = int(val or 0)

    # Merge each A/B fragment
    for frag in fragments:
        for line in frag.read_text().splitlines():
            key, _, val = line.partition("=")
            if key in ("SCENARIO_PASSED", "SCENARIO_FAILED", "SCENARIO_TOTAL"):
                merged[key] = merged.get(key, 0) + int(val or 0)

    # Recompute rate
    total = merged["SCENARIO_TOTAL"]
    rate = (merged["SCENARIO_PASSED"] * 100 // total) if total > 0 else 0
    merged["SCENARIO_RATE"] = rate

    canonical.write_text(
        "\n".join(f"{k}={v}" for k, v in merged.items()) + "\n"
    )
    _parse_scenario_counts(gate_result, project_dir, spec_name)
```

---

## What Does NOT Change

- Information barriers: quality gate has no barriers today and continues to have none
- Gates 1–4 in `quality-gate.sh`: unchanged
- Executable scenarios (C/D) in `quality-gate.sh`: unchanged
- `GateResult` data structure: unchanged
- Docker network topology: unchanged
- `build_cache_volumes()`, `build_project_mount()`: unchanged

---

## Resolved Design Decisions

| Question | Decision |
|----------|---------|
| Share server container for HTTP + browser? | Yes, deduplicate by command string |
| `localhost` in base_url | Python-side substitution: `localhost`/`127.0.0.1` → container name (only for runner containers; health poll uses original localhost URL) |
| Backward compatibility for standalone `quality-gate.sh` | `DF_PYTHON_HANDLES_AB=1` env var; standalone path kept for developer debugging |
| Timeout for runner containers | Reuse `config.gate_timeout` |
| Server container `pids_limit` | Make `pids_limit` a parameter in `build_base_args` (default 256, server uses 512) |
| `dark-factory-python` required for Format A in non-Python projects | Add to `dark-factory init` output and non-Python build instructions in docs |

---

## Files Changed

| File | Change scope |
|------|-------------|
| `darkfactory/pipeline/quality_gate.py` | Major — orchestration, server lifecycle, URL rewriting, report rows, count accumulation |
| `darkfactory/docker/containers.py` | Minor — add `start_container()` for detached mode |
| `darkfactory/docker/args.py` | Minor — make `pids_limit` a parameter in `build_base_args` |
| `scripts/quality-gate.sh` | Moderate — skip A/B runners when `DF_PYTHON_HANDLES_AB=1` |
| `scripts/lib/satisfaction.sh` | Minor — filter A/B files when `DF_PYTHON_HANDLES_AB=1` in `_df_run_scenarios_satisfaction` |
| `darkfactory/scenarios/detection.py` | Read-only reference |
| `docs/TOOLS.md` | Minor — update server config and Format A/B docs |
| `docs/WORKFLOW.md` | Minor — update Gate 5 description |
| `tests/` | New tests for orchestrator, URL rewriting, count accumulation |
