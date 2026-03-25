"""Quality gate runner — build/lint/type/test gate execution.

Replaces quality-gate.sh. Runs the quality gate script inside a Docker
container (not via Claude — direct bash entrypoint).
"""

from __future__ import annotations

import logging
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from darkfactory.docker.args import build_base_args
from darkfactory.errors import ConfigError
from darkfactory.docker.containers import (
    check_docker_image,
    check_language_image,
    execute_container,
    remove_container,
    start_container,
    stop_container,
)
from darkfactory.docker.mounts import build_cache_volumes, build_project_mount
from darkfactory.docker.networks import network_name
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import RunResult

log = logging.getLogger(__name__)


@dataclass
class GateResult:
    """Result from running the quality gate."""
    exit_code: int  # 0=pass, 1=non-build fail, 2=build fail
    output: str = ""
    scenario_passed: int = 0
    scenario_failed: int = 0
    scenario_total: int = 0
    pass_rate: int = 0
    satisfaction_score: int = 0
    mutation_score: int = 0


@dataclass
class _FormatFlags:
    """Which scenario formats exist in a spec's scenarios directory."""
    has_http: bool = False      # *.scenarios.md — Format A (HTTP)
    has_journey: bool = False   # *.journey.md   — Format B (browser journey)
    has_executable: bool = False  # *.test.*, test_*.py, etc. — Format C/D


def run_quality_gate(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    *,
    iteration: int = 1,
    dry_run: bool = False,
) -> GateResult:
    """Run the quality gate in a Docker container.

    Uses bash entrypoint (not Claude) to run quality-gate.sh.
    Returns GateResult with exit code and parsed scenario counts.
    """
    image = f"dark-factory-{config.language}"

    # Build Docker args — quality gate gets rw project mount.
    docker_args = build_base_args(config, run_id)
    docker_args.extend(build_project_mount(str(project_dir), rw=True))
    docker_args.extend(build_cache_volumes("quality-gate"))
    docker_args.extend([
        "--network", network_name(run_id, "execution"),
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=quality-gate",
        "--entrypoint", "bash",
        "--workdir", "/project",
        # Signal to quality-gate.sh that Python orchestrates Format A/B runners
        # in separate containers after this container exits.
        "-e", "DF_PYTHON_HANDLES_AB=1",
    ])

    # Gate command — forward all PipelineConfig values so quality-gate.sh
    # doesn't depend on config.sh inside the container.
    gate_cmd = ["/project/scripts/quality-gate.sh", spec_name]
    if config.language:
        gate_cmd.extend(["--language", config.language])
    if config.test_cmd:
        gate_cmd.extend(["--test-cmd", config.test_cmd])
    if config.build_cmd:
        gate_cmd.extend(["--build-cmd", config.build_cmd])
    if config.lint_cmd:
        gate_cmd.extend(["--lint-cmd", config.lint_cmd])
    if config.typecheck_cmd:
        gate_cmd.extend(["--typecheck-cmd", config.typecheck_cmd])
    if config.source_dirs:
        gate_cmd.extend(["--source-dir", ":".join(config.source_dirs)])
    if config.test_dir:
        gate_cmd.extend(["--test-dir", config.test_dir])
    if config.scenario_test_cmd:
        gate_cmd.extend(["--scenario-test-cmd", config.scenario_test_cmd])
    if config.validate_scenarios:
        gate_cmd.extend(["--scenario-validation", "true"])
    if config.test_base_url:
        gate_cmd.extend(["--test-base-url", config.test_base_url])
    if config.test_server_cmd:
        gate_cmd.extend(["--test-server-cmd", config.test_server_cmd])
    # Timeout flags are always forwarded so the shell script always has explicit
    # values rather than relying on its own defaults.  This matches the --iteration
    # convention and keeps the command line deterministic.
    # Gate timeout: per-gate bash-level timeout (prevents individual gates from
    # hanging until the Python-level container timeout kills the whole container).
    gate_cmd.extend(["--gate-timeout", str(config.gate_timeout)])
    gate_cmd.extend(["--server-startup-timeout", str(config.server_startup_timeout)])
    if config.server_health_path:
        gate_cmd.extend(["--server-health-path", config.server_health_path])
    if config.browser_base_url:
        gate_cmd.extend(["--browser-base-url", config.browser_base_url])
    if config.browser_server_cmd:
        gate_cmd.extend(["--browser-server-cmd", config.browser_server_cmd])
    gate_cmd.extend(["--browser-startup-timeout", str(config.browser_startup_timeout)])
    gate_cmd.extend(["--iteration", str(iteration)])

    check_language_image(config.language, spec_name=spec_name)

    result = execute_container(
        docker_args, image, gate_cmd,
        timeout=config.gate_timeout,
        dry_run=dry_run,
    )

    gate_result = GateResult(
        exit_code=result.exit_code,
        output=result.stdout + result.stderr,
    )

    # If the quality gate container was killed (timeout/crash) before the bash script
    # finished writing the summary file, write a minimal fallback from Python.
    # This ensures prepass.py always finds the summary file and can determine
    # at minimum whether the gate passed or failed overall.
    _ensure_gate_summary(project_dir, spec_name, result.exit_code, dry_run=dry_run)

    # Detect which scenario formats exist so we know what to orchestrate.
    formats = _detect_formats(project_dir, spec_name)

    # --- Format A (HTTP) and Format B (Journey) orchestration ---
    # The language container ran Gates 1–5 (build/lint/type/self-tests + C/D scenarios).
    # Now Python starts the appropriate server and runner containers for A/B formats.
    server_name: str | None = None
    browser_server_name: str | None = None

    # True when both server commands are identical — one container serves both formats.
    _cmds_same = bool(
        config.test_server_cmd and config.browser_server_cmd
        and config.test_server_cmd == config.browser_server_cmd
    )
    original_base_url = config.test_base_url or "http://localhost:8000"

    try:
        # Start the HTTP/shared server when Format A needs it OR when Format B
        # shares the same server command (cmds_same) but Format A is absent.
        # Without this, the elif branch below would find server_name=None and
        # pass the raw localhost URL to the runner container.
        _needs_http_server = formats.has_http and config.test_server_cmd
        _needs_shared_server = formats.has_journey and _cmds_same
        if _needs_http_server or _needs_shared_server:
            server_name = _start_server_container(
                config, run_id, project_dir,
                config.test_server_cmd or config.browser_server_cmd,
                base_url=original_base_url, dry_run=dry_run,
            )
            # Host polls original localhost URL (port published via -p 127.0.0.1:{port}:{port})
            _wait_for_server(
                original_base_url,
                config.server_health_path or "/health",
                config.server_startup_timeout,
                dry_run=dry_run,
            )

        rewritten_base_url = (
            _rewrite_base_url(original_base_url, server_name)
            if server_name else original_base_url
        )

        if formats.has_http:
            a_result = _run_format_a_runner(
                spec_name, config, run_id, project_dir,
                rewritten_base_url, dry_run=dry_run,
            )
            _append_gate_report_row(
                project_dir, spec_name, "Blind Scenarios (HTTP)", a_result.exit_code
            )
            if a_result.exit_code != 0:
                gate_result.exit_code = max(gate_result.exit_code, 1)

        # Start browser server if Format B needs a different server, or reuse shared.
        if formats.has_journey:
            _check_playwright_image(spec_name, project_dir)
            original_browser_url = config.browser_base_url or "http://localhost:3000"
            if config.browser_server_cmd and not _cmds_same:
                # Different command from HTTP server — start a dedicated container.
                browser_server_name = _start_server_container(
                    config, run_id, project_dir, config.browser_server_cmd,
                    base_url=original_browser_url,
                    name=_browser_server_container_name(run_id),
                    dry_run=dry_run,
                )
                _wait_for_server(
                    original_browser_url,
                    config.server_health_path or "/health",
                    config.browser_startup_timeout,
                    dry_run=dry_run,
                )
                rewritten_browser_url = _rewrite_base_url(original_browser_url, browser_server_name)
            elif _cmds_same and server_name:
                # Same command as HTTP server — reuse its container via Docker-internal DNS.
                rewritten_browser_url = _rewrite_base_url(original_browser_url, server_name)
            else:
                rewritten_browser_url = original_browser_url

            b_result = _run_format_b_runner(
                spec_name, config, run_id, project_dir,
                rewritten_browser_url, dry_run=dry_run,
            )
            _append_gate_report_row(
                project_dir, spec_name, "Blind Scenarios (Journey)", b_result.exit_code
            )
            if b_result.exit_code != 0:
                gate_result.exit_code = max(gate_result.exit_code, 1)

    finally:
        # Deduplicate: if future refactors ever assign the same name to both vars,
        # we must not call stop+remove twice on the same container.
        _seen: set[str] = set()
        for cname in [server_name, browser_server_name]:
            if cname and cname not in _seen:
                _seen.add(cname)
                _stop_server_container(cname, dry_run=dry_run)

    # Merge A/B runner count fragments into the canonical counts file.
    # _ensure_counts_file MUST come after accumulation (Step 9) so it sees
    # the merged totals and only writes zeros when truly no scenarios ran.
    _accumulate_ab_scenario_counts(project_dir, spec_name, gate_result)
    _ensure_counts_file(project_dir, spec_name, gate_result)

    # Mutation testing step (optional).
    if gate_result.exit_code == 0 and config.mutation_cmd:
        _run_mutation_gate(gate_result, config, run_id, project_dir, dry_run=dry_run)

    return gate_result


# ==============================================================================
# Format detection
# ==============================================================================

def _detect_formats(project_dir: Path, spec_name: str) -> _FormatFlags:
    """Detect which scenario formats exist in the spec's scenarios directory."""
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
    if not scenarios_dir.is_dir():
        return _FormatFlags()

    flags = _FormatFlags()
    if any(scenarios_dir.rglob("*.scenarios.md")):
        flags.has_http = True
    if any(scenarios_dir.rglob("*.journey.md")):
        flags.has_journey = True
    for pattern in ("*.test.*", "*.spec.*", "test_*.py", "*_test.py", "*_test.go"):
        if any(scenarios_dir.rglob(pattern)):
            flags.has_executable = True
            break
    return flags


# ==============================================================================
# Server container lifecycle
# ==============================================================================

def _server_container_name(run_id: str) -> str:
    return f"df-{run_id}-server"


def _browser_server_container_name(run_id: str) -> str:
    return f"df-{run_id}-browser-server"


def _start_server_container(
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    server_cmd: str,
    *,
    base_url: str,
    name: str | None = None,
    dry_run: bool = False,
) -> str:
    """Start a detached server container. Returns the container name.

    Publishes the server port to 127.0.0.1 on the host so the Python
    health poll can reach it (Docker internal DNS only works inside containers).
    """
    if name is None:
        name = _server_container_name(run_id)

    # Server containers need more PIDs than the default 256 for multi-worker
    # app servers (gunicorn, uvicorn, etc.).
    docker_args = build_base_args(config, run_id, pids_limit=512)
    docker_args.extend(build_project_mount(str(project_dir), rw=False))

    # Publish port to loopback for host health poll
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    docker_args.extend([
        "-p", f"127.0.0.1:{port}:{port}",
        "--network", network_name(run_id, "execution"),
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=test-server",
        "--entrypoint", "bash",
        "--workdir", "/project",
    ])
    start_container(
        docker_args,
        f"dark-factory-{config.language}",
        ["-c", server_cmd],
        name=name,
        dry_run=dry_run,
    )
    return name


def _wait_for_server(
    base_url: str,
    health_path: str,
    timeout: int,
    *,
    dry_run: bool = False,
) -> None:
    """Poll health endpoint until 200 or timeout. No-op in dry_run mode.

    IMPORTANT: base_url must be the HOST-reachable URL (localhost:{port}).
    Docker internal DNS only resolves inside containers on the same network.
    This function runs in the Python host process.
    """
    if dry_run:
        return
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


def _stop_server_container(name: str, *, dry_run: bool = False) -> None:
    """Stop and remove a server container started by _start_server_container."""
    stop_container(name, dry_run=dry_run)
    remove_container(name, dry_run=dry_run)


# ==============================================================================
# Format A/B runner containers
# ==============================================================================

def _run_format_a_runner(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    base_url: str,  # already rewritten (localhost → container name)
    *,
    dry_run: bool = False,
) -> RunResult:
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


def _run_format_b_runner(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    base_url: str,  # already rewritten
    *,
    dry_run: bool = False,
) -> RunResult:
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


# ==============================================================================
# Gate report rows
# ==============================================================================

def _append_gate_report_row(
    project_dir: Path,
    spec_name: str,
    gate_name: str,
    exit_code: int,
) -> None:
    """Append a result row to the quality gate markdown report.

    quality-gate.sh writes rows for Gates 1–5. A/B results are produced
    after the shell container exits, so Python must append them.
    """
    report_path = (
        project_dir / ".dark-factory" / "reports" / f"{spec_name}-quality-gate.md"
    )
    if not report_path.exists():
        return
    status = "PASS" if exit_code == 0 else "FAIL"
    with report_path.open("a") as f:
        f.write(f"| {gate_name} | {status} |\n")


def _ensure_gate_summary(
    project_dir: Path,
    spec_name: str,
    exit_code: int,
    *,
    dry_run: bool = False,
) -> None:
    """Write a minimal quality gate summary if the bash script didn't produce one.

    The quality gate bash script writes the summary at the START (before running
    any gates). If the container was killed mid-execution (Python timeout) before
    the first write reached disk — which can happen on macOS with Docker Desktop's
    VirtioFS sync — the prepass will see a missing file and assume all gates failed.

    This fallback guarantees the summary always exists after run_quality_gate returns.
    It only writes when the file is absent; if the bash script wrote it, this is a no-op.
    """
    if dry_run:
        return
    summary_path = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-quality-gate-summary.md"
    )
    if summary_path.exists():
        return

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    result_label = "PASS" if exit_code == 0 else "FAIL"
    now = datetime.now(timezone.utc).isoformat()
    summary_path.write_text(
        f"# Quality Gate Summary: {spec_name}\n"
        f"Date: {now}\n\n"
        f"| Gate | Status |\n"
        f"|------|--------|\n"
        f"\n"
        f"## Result: {result_label}\n"
    )
    log.warning(
        "quality gate summary not found after container exit — wrote fallback summary "
        "(exit_code=%d). The container may have been killed before the bash script "
        "could flush writes to the bind mount. Consider increasing gate_timeout.",
        exit_code,
    )


# ==============================================================================
# Scenario count accumulation (Step 10)
# ==============================================================================

def _accumulate_ab_scenario_counts(
    project_dir: Path,
    spec_name: str,
    gate_result: GateResult,
) -> None:
    """Merge A/B runner count fragments into the canonical scenario counts file.

    The language container already wrote C/D counts (or nothing).
    The A/B runner containers write their own fragment files.
    This function merges all fragments into the canonical counts file,
    then calls _parse_scenario_counts to update gate_result.

    NOT IDEMPOTENT — call exactly once. Calling twice would double-count A/B
    fragments because the first call writes them into the canonical file and
    the second call would read both the canonical file and the fragments again.
    """
    reports_dir = project_dir / ".dark-factory" / "reports"
    fragments = list(reports_dir.glob(f"{spec_name}-scenario-counts-*.env"))
    canonical = reports_dir / f"{spec_name}-scenario-counts.env"

    # Read existing canonical file (C/D counts from shell) if present.
    # Preserve all fields; only the three count fields are accumulated.
    merged: dict[str, int] = {
        "SCENARIO_PASSED": 0,
        "SCENARIO_FAILED": 0,
        "SCENARIO_TOTAL": 0,
    }
    if canonical.exists():
        for line in canonical.read_text().splitlines():
            key, _, val = line.partition("=")
            if key:
                merged[key] = _safe_int(val)

    # Merge each A/B fragment
    for frag in fragments:
        for line in frag.read_text().splitlines():
            key, _, val = line.partition("=")
            if key in ("SCENARIO_PASSED", "SCENARIO_FAILED", "SCENARIO_TOTAL"):
                merged[key] = merged.get(key, 0) + _safe_int(val)

    # Recompute pass rate
    total = merged["SCENARIO_TOTAL"]
    rate = (merged["SCENARIO_PASSED"] * 100 // total) if total > 0 else 0
    merged["SCENARIO_RATE"] = rate

    reports_dir.mkdir(parents=True, exist_ok=True)
    canonical.write_text(
        "\n".join(f"{k}={v}" for k, v in merged.items()) + "\n"
    )
    # Delete fragment files after merging to prevent double-counting on retry.
    for frag in fragments:
        frag.unlink(missing_ok=True)
    _parse_scenario_counts(gate_result, project_dir, spec_name)


# ==============================================================================
# URL rewriting
# ==============================================================================

def _rewrite_base_url(url: str, server_name: str) -> str:
    """Replace localhost/127.0.0.1 with the server container name for Docker networking.

    Runner containers reach the server via Docker-internal DNS (container name),
    not via the loopback address the host process uses.

    Args:
        url: The base URL configured by the user (e.g. ``http://localhost:8000``).
        server_name: The Docker container name to substitute in.

    Returns:
        URL with ``localhost`` or ``127.0.0.1`` replaced by ``server_name``.
        URLs with custom hostnames pass through unchanged.
    """
    parsed = urlparse(url)
    if parsed.hostname in ("localhost", "127.0.0.1"):
        # Reconstruct netloc explicitly rather than using str.replace, which is
        # fragile when netloc includes credentials or the host appears elsewhere.
        new_netloc = f"{server_name}:{parsed.port}" if parsed.port else server_name
        return urlunparse(parsed._replace(netloc=new_netloc))
    return url  # custom hostname: pass through unchanged


# ==============================================================================
# Pre-flight checks
# ==============================================================================

def _check_playwright_image(spec_name: str, project_dir: Path) -> None:
    """Fail early if journey scenarios are present but the playwright image is missing."""
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
    if not scenarios_dir.is_dir():
        return
    if not list(scenarios_dir.rglob("*.journey.md")):
        return
    check_docker_image("dark-factory-playwright")  # delegates to containers.py


# ==============================================================================
# Scenario count parsing
# ==============================================================================

def _parse_scenario_counts(
    gate_result: GateResult,
    project_dir: Path,
    spec_name: str,
) -> None:
    """Parse scenario pass/fail counts from the counts env file."""
    counts_file = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-scenario-counts.env"
    )
    if not counts_file.exists():
        return

    data: dict[str, str] = {}
    with counts_file.open() as f:
        for line in f:
            stripped = line.strip()
            if "=" in stripped:
                key, _, value = stripped.partition("=")
                data[key] = value

    gate_result.scenario_passed = _safe_int(data.get("SCENARIO_PASSED", "0"))
    gate_result.scenario_failed = _safe_int(data.get("SCENARIO_FAILED", "0"))
    gate_result.scenario_total = _safe_int(data.get("SCENARIO_TOTAL", "0"))
    gate_result.pass_rate = _safe_int(data.get("SCENARIO_RATE", "0"))
    gate_result.satisfaction_score = _safe_int(data.get("SATISFACTION_SCORE", "0"))


def _ensure_counts_file(
    project_dir: Path,
    spec_name: str,
    gate_result: GateResult,
) -> None:
    """Write a zero-value counts file if no counts file exists.

    Called AFTER _accumulate_ab_scenario_counts so it only writes zeros
    when truly no scenarios ran (not just before A/B runners finish).
    """
    counts_file = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-scenario-counts.env"
    )
    if counts_file.exists():
        return
    counts_file.parent.mkdir(parents=True, exist_ok=True)
    counts_file.write_text(
        f"SCENARIO_PASSED={gate_result.scenario_passed}\n"
        f"SCENARIO_FAILED={gate_result.scenario_failed}\n"
        f"SCENARIO_TOTAL={gate_result.scenario_total}\n"
        f"SCENARIO_RATE={gate_result.pass_rate}\n"
        f"SATISFACTION_SCORE={gate_result.satisfaction_score}\n"
    )
    log.debug("wrote zero scenario counts file (scenarios were skipped): %s", counts_file)


# ==============================================================================
# Mutation gate
# ==============================================================================

def _run_mutation_gate(
    gate_result: GateResult,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    *,
    dry_run: bool = False,
) -> None:
    """Run mutation testing command and enforce minimum score."""
    image = f"dark-factory-{config.language}"

    docker_args = build_base_args(config, run_id)
    docker_args.extend(build_project_mount(str(project_dir), rw=True))
    docker_args.extend(build_cache_volumes("mutation"))
    docker_args.extend([
        "--network", network_name(run_id, "execution"),
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=mutation",
        "--entrypoint", "bash",
        "--workdir", "/project",
    ])

    mutation_cmd = ["-c", config.mutation_cmd]

    result = execute_container(
        docker_args, image, mutation_cmd,
        timeout=config.gate_timeout,
        dry_run=dry_run,
    )

    output = result.stdout + result.stderr

    # Try to parse a score from output (e.g., "Mutation score: 85%").
    score = 0
    m = re.search(r"[Mm]utation\s+score[:\s]+(\d+)", output)
    if m:
        score = int(m.group(1))
    gate_result.mutation_score = score

    min_score = config.mutation_min_score
    if result.exit_code != 0 or (min_score > 0 and score < min_score):
        gate_result.exit_code = 1
        log.warning("mutation gate failed: score=%d (min=%d), exit=%d",
                     score, min_score, result.exit_code)


def _safe_int(value: str) -> int:
    """Parse an integer, returning 0 on failure."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0
