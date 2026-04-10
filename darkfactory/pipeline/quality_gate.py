"""Quality gate runner — build/lint/type/test gate execution.

Replaces quality-gate.sh. Runs the quality gate script inside a Docker
container (not via Claude — direct bash entrypoint).
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse, urlunparse

from darkfactory.docker.args import build_base_args, build_proxy_env_args
from darkfactory.scenarios.detection import has_sentinel_file
from darkfactory.errors import ConfigError
from darkfactory.docker.containers import (
    check_docker_image,
    check_language_image,
    container_logs,
    execute_container,
    remove_container,
    start_container,
    stop_container,
)
from darkfactory.docker.mounts import build_cache_volumes, build_platform_isolation_overlays, build_project_mount, build_scripts_mount
from darkfactory.docker.networks import network_name, proxy_name
from darkfactory.models.config import PipelineConfig, ServiceDef
from darkfactory.utils.subprocess_runner import RunResult, run as _run

log = logging.getLogger(__name__)

# Extra seconds added to server_startup_timeout when an install command
# (pnpm install / pip install) is prepended to server_cmd.  The install
# resolves from the shared pnpm-store cache but still takes time to link
# packages, especially in large monorepos.
_INSTALL_TIMEOUT_PADDING = 30


def _dep_install_prefix(language: str) -> str:
    """Return a shell prefix that installs project dependencies.

    Each quality-gate container gets a fresh tmpfs over node_modules,
    so dependencies must be reinstalled from the shared pnpm-store cache.
    """
    return {
        "node": "pnpm install --frozen-lockfile 2>&1 && ",
        "python": "[ -f requirements.txt ] && pip install -r requirements.txt 2>&1; ",
    }.get(language, "")


def _persist_container_logs(
    container_name: str,
    project_dir: Path,
    spec_name: str,
    *,
    tail: int = 200,
    dry_run: bool = False,
) -> str:
    """Fetch and persist container logs to disk. Returns log text."""
    logs = container_logs(container_name, tail=tail, dry_run=dry_run)
    if logs and not dry_run:
        log_dir = project_dir / ".dark-factory" / "reports" / spec_name
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{container_name}.log"
        try:
            log_path.write_text(logs)
            log.debug("persisted container logs: %s", log_path)
        except OSError as exc:
            log.warning("could not persist container logs to %s: %s", log_path, exc)
    return logs


def _persist_smoke_logs(
    project_dir: Path,
    spec_name: str,
    run_id: str,
    iteration: int,
    *,
    server_container: str | None,
    server_logs: str,
    resolved_setup_cmd: str,
    resolved_server_cmd: str,
    error: str = "",
    dry_run: bool = False,
) -> None:
    """Persist smoke server container stdout/stderr on every invocation.

    Phase 1.1: smoke logs are captured regardless of outcome (success or
    failure) so the operator can always inspect what the server printed.
    Includes the resolved setup/server commands at the top so the file is
    self-documenting. Symlinks ``latest-smoke.log`` to the most recent.

    Layout::

        .dark-factory/reports/<spec>/<run_id>/smoke-iter<N>.log   (stdout+stderr)
        .dark-factory/reports/<spec>/<run_id>/smoke-iter<N>.err   (orchestrator error msg, if any)
        .dark-factory/reports/<spec>/<run_id>/latest-smoke.log    (symlink)

    Failures here are warnings — never raise. Diagnostics must not break
    the gate.
    """
    if dry_run:
        return
    log_dir = project_dir / ".dark-factory" / "reports" / spec_name / run_id
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.warning("smoke log dir %s not creatable: %s", log_dir, exc)
        return

    log_path = log_dir / f"smoke-iter{iteration}.log"
    err_path = log_dir / f"smoke-iter{iteration}.err"

    header = (
        f"# smoke-iter{iteration} for {spec_name} (run {run_id})\n"
        f"# server_setup_cmd: {resolved_setup_cmd or '(none)'}\n"
        f"# test_server_cmd:  {resolved_server_cmd or '(none)'}\n"
        f"# server_container: {server_container or '(not started)'}\n"
        "# ---\n"
    )

    try:
        log_path.write_text(header + (server_logs or ""))
    except OSError as exc:
        log.warning("could not write smoke log %s: %s", log_path, exc)
        return

    if error:
        try:
            err_path.write_text(error)
        except OSError as exc:
            log.warning("could not write smoke err %s: %s", err_path, exc)

    # Refresh latest-smoke.log symlink. Best effort: not all filesystems
    # support symlinks, so a failure here is logged and ignored.
    latest = log_dir / "latest-smoke.log"
    try:
        latest.unlink(missing_ok=True)
        latest.symlink_to(log_path.name)
    except OSError as exc:
        log.debug("could not refresh latest-smoke.log symlink: %s", exc)


def _persist_runner_output(
    project_dir: Path,
    spec_name: str,
    runner_name: str,
    output: str,
) -> None:
    """Persist scenario runner stdout/stderr to disk for post-mortem analysis."""
    if not output:
        return
    log_dir = project_dir / ".dark-factory" / "reports" / spec_name
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{runner_name}.log"
    try:
        log_path.write_text(output)
        log.debug("persisted runner output: %s", log_path)
    except OSError as exc:
        log.warning("could not persist runner output to %s: %s", log_path, exc)


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
    sentinel_present: bool = False  # True when SCENARIOS-NOT-APPLICABLE.md exists
    # Crash-vs-assert breakdown of failures (P4: scenario quality reporting).
    scenario_crashed: int = 0       # TypeError/ReferenceError/SyntaxError/import errors
    scenario_assert_failed: int = 0  # Assertion failures (implementation-side signals)
    scenario_crash_adjusted: bool = False  # True when pass_rate excludes crashes
    # Phase 7: skip observability — vitest/jest tests that silently skip due to
    # beforeAll failures (e.g., database unreachable). SCENARIO_TOTAL stays as
    # passed+failed; SCENARIO_REGISTERED includes skipped.
    scenario_skipped: int = 0
    scenario_registered: int = 0  # passed + failed + skipped
    skip_failure: bool = False    # True when >50% of registered tests skipped
    # Effective counts after crash adjustment — raw counts above stay unchanged.
    scenario_effective_total: int = 0
    scenario_effective_failed: int = 0
    # Hard gate results — True when Build/Lint/TypeCheck all pass (scenarios may fail).
    hard_gates_pass: bool = False
    # Server container logs on startup failure — threaded to Oracle prompt so
    # the implementer gets actionable feedback instead of a generic "0% pass rate".
    server_error: str = ""


@dataclass
class PreGateResult:
    """Result from running the pre-gate (Build + Lint + Typecheck only)."""
    exit_code: int     # 0=pass, 1=non-build fail, 2=build fail
    output: str = ""


@dataclass
class ServerSmokeResult:
    """Result from server smoke check (services + server startup)."""
    success: bool = False
    server_error: str = ""           # Error output on failure (for Implementer feedback)
    server_name: str = ""            # Running server container name (for reuse)
    service_names: list[str] = field(default_factory=list)  # Running service containers
    inject_env: dict[str, str] = field(default_factory=dict)  # Service inject_env for gate


@dataclass
class SmokeTestResult:
    """Result from the scenario smoke test (P3: pre-implementation guard)."""
    exit_code: int = 0
    crashes: int = 0              # TypeError/ReferenceError/SyntaxError (scenario bugs)
    assertion_failures: int = 0    # Expected — scenarios should fail before implementation
    suspicious_passes: int = 0     # PASS in extension specs (warning only)
    tautological_passes: int = 0   # PASS in greenfield specs (repair trigger)
    sanitized_crash_output: str = ""
    tautological_files: list[str] = field(default_factory=list)


@dataclass
class _FormatFlags:
    """Which scenario formats exist in a spec's scenarios directory."""
    has_http: bool = False      # *.scenarios.md — Format A (HTTP)
    has_journey: bool = False   # *.journey.md   — Format B (browser journey)
    has_executable: bool = False  # *.test.*, test_*.py, etc. — Format C/D


def _launch_gate_container(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    gate_cmd: list[str],
    *,
    timeout: int,
    dry_run: bool = False,
    extra_env: dict[str, str] | None = None,
) -> RunResult:
    """Shared Docker launch for run_quality_gate() and run_pre_gate()."""
    image = f"dark-factory-{config.language}"
    docker_args = build_base_args(config, run_id)
    docker_args.extend(build_project_mount(str(project_dir), rw=True))
    docker_args.extend(build_cache_volumes("quality-gate"))
    docker_args.extend(build_platform_isolation_overlays(config.language, project_dir))
    docker_args.extend(build_scripts_mount())
    docker_args.extend([
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=quality-gate",
        "--entrypoint", "bash",
        "--workdir", "/project",
        "-e", "DF_PYTHON_ORCHESTRATED=1",
    ])
    for key, val in (extra_env or {}).items():
        docker_args.extend(["-e", f"{key}={val}"])
    if config.egress_filtering or config.services:
        docker_args.extend([
            "--network", network_name(run_id, "execution"),
        ])
    if config.egress_filtering:
        proxy_url = f"http://{proxy_name(run_id, 'execution')}:8080"
        docker_args.extend(build_proxy_env_args(proxy_url, config))
    return execute_container(docker_args, image, gate_cmd, timeout=timeout, dry_run=dry_run)


def run_quality_gate(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    *,
    iteration: int = 1,
    dry_run: bool = False,
    existing_server: str | None = None,
    existing_services: list[str] | None = None,
    existing_inject_env: dict[str, str] | None = None,
) -> GateResult:
    """Run the quality gate in a Docker container.

    Uses bash entrypoint (not Claude) to run quality-gate.sh.
    Returns GateResult with exit code and parsed scenario counts.
    """
    # Gate command — forward all PipelineConfig values so quality-gate.sh
    # doesn't depend on config.sh inside the container.
    gate_cmd = ["/opt/dark-factory/scripts/quality-gate.sh", spec_name]
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
    # Bug #6 fix: forward architect-supplied install command (commands.install)
    # so the gate container actually installs deps before running build/lint/test.
    if config.server_setup_cmd:
        gate_cmd.extend(["--install-cmd", config.server_setup_cmd])
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
    gate_cmd.extend(["--scenario-timeout", str(config.scenario_timeout)])
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

    # Forward multi-run config so satisfaction.sh uses N-of-K execution.
    env: dict[str, str] = {}
    if config.multi_run_n > 1:
        env["SCENARIO_MULTI_RUN_N"] = str(config.multi_run_n)
        env["SCENARIO_MULTI_RUN_K"] = str(config.multi_run_k)

    # --- Phase 5: Start services BEFORE the gate container ---
    # Services must be running before the gate so inject_env values
    # (e.g., DATABASE_URL) reach the gate container and service hostnames
    # resolve on the execution network.
    service_names: list[str] = []
    _svc_inject_env: dict[str, str] = {}
    server_name: str | None = None
    browser_server_name: str | None = None
    gate_result = GateResult(exit_code=0, output="")

    try:
        if config.services and not existing_services:
            try:
                _start_service_containers(config, run_id, service_names, dry_run=dry_run)
            except TimeoutError as exc:
                gate_result.server_error = f"Service startup failed: {exc}"
                gate_result.exit_code = 1
                _append_gate_report_row(project_dir, spec_name, "Service Startup", 1)
                _append_gate_summary_row(project_dir, spec_name, "Service Startup", 1)
                _rewrite_summary_result(project_dir, spec_name, "FAIL")
                return gate_result
            _svc_inject_env = _collect_inject_env(config)
        elif existing_services:
            service_names.extend(existing_services)
            _svc_inject_env = existing_inject_env if existing_inject_env is not None else (
                _collect_inject_env(config) if config.services else {}
            )

        # Merge inject_env so the gate container can reach sidecars.
        env.update(_svc_inject_env)

        # Container must outlive all sequential gates. Build/lint/type/test gates each
        # use gate_timeout; the scenario gate uses scenario_timeout (or gate_timeout if 0).
        effective_scenario_timeout = config.scenario_timeout if config.scenario_timeout > 0 else config.gate_timeout
        container_timeout = config.gate_timeout * 9 + effective_scenario_timeout
        result = _launch_gate_container(
            spec_name, config, run_id, project_dir, gate_cmd,
            timeout=container_timeout, dry_run=dry_run,
            extra_env=env,
        )

        gate_result = GateResult(
            exit_code=result.exit_code,
            output=result.stdout + result.stderr,
        )

        _ensure_gate_summary(project_dir, spec_name, result.exit_code, dry_run=dry_run)

        # Detect which scenario formats exist so we know what to orchestrate.
        formats = _detect_formats(project_dir, spec_name)

        # --- Format A (HTTP) and Format B (Journey) orchestration ---
        # Services are already running. Now start HTTP/browser servers if needed.
        _cmds_same = bool(
            config.test_server_cmd and config.browser_server_cmd
            and config.test_server_cmd == config.browser_server_cmd
        )
        original_base_url = config.test_base_url or "http://localhost:8000"
        _has_install = bool(_dep_install_prefix(config.language))

        _needs_http_server = formats.has_http and config.test_server_cmd
        _needs_shared_server = formats.has_journey and _cmds_same

        # Reuse existing server from a prior smoke check, if provided.
        if existing_server and (_needs_http_server or _needs_shared_server):
            server_name = existing_server
            _parsed = urlparse(original_base_url)
            _port = _parsed.port or 80
            try:
                _wait_for_server(
                    existing_server,
                    _port,
                    config.server_health_path or "/health",
                    10,
                    dry_run=dry_run,
                )
            except TimeoutError:
                log.warning("existing server container died, starting fresh")
                _stop_server_container(existing_server, dry_run=dry_run)
                server_name = None
                existing_server = None

        # Fresh server startup — services already running, skip in _start_server_stack.
        if not server_name and (_needs_http_server or _needs_shared_server):
            stack = _start_server_stack(
                spec_name, config, run_id, project_dir,
                server_cmd=config.test_server_cmd or config.browser_server_cmd,
                base_url=original_base_url,
                needs_services=False,
                dry_run=dry_run,
            )
            if stack.error:
                gate_result.server_error = stack.error
                gate_result.exit_code = max(gate_result.exit_code, 1)
                _stage = stack.failed_stage or "Server Startup"
                _append_gate_report_row(project_dir, spec_name, _stage, 1)
                _append_gate_summary_row(project_dir, spec_name, _stage, 1)
                _rewrite_summary_result(project_dir, spec_name, "FAIL")
                return gate_result
            server_name = stack.server_name

        rewritten_base_url = (
            _rewrite_base_url(original_base_url, server_name)
            if server_name else original_base_url
        )

        if formats.has_http:
            check_docker_image("dark-factory-python")
            a_result = _run_format_a_runner(
                spec_name, config, run_id, project_dir,
                rewritten_base_url, iteration=iteration, dry_run=dry_run,
            )
            if a_result.exit_code != 0:
                _output = (a_result.stderr or a_result.stdout or "").strip()
                log.warning(
                    "HTTP scenario runner exited %d:\n%s",
                    a_result.exit_code, _output[:4000] if _output else "(no output)",
                )
                _persist_runner_output(
                    project_dir, spec_name, "http-scenario-runner", _output,
                )
                gate_result.exit_code = max(gate_result.exit_code, 1)
            _append_gate_report_row(
                project_dir, spec_name, "Blind Scenarios (HTTP)", a_result.exit_code
            )

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
                _browser_timeout = config.browser_startup_timeout + (
                    _INSTALL_TIMEOUT_PADDING if _has_install else 0
                )
                _browser_parsed = urlparse(original_browser_url)
                _browser_port = _browser_parsed.port or 80
                try:
                    _wait_for_server(
                        browser_server_name,
                        _browser_port,
                        config.server_health_path or "/health",
                        _browser_timeout,
                        dry_run=dry_run,
                    )
                except TimeoutError as exc:
                    log.warning("browser server startup timed out: %s", exc)
                    srv_logs = ""
                    if browser_server_name:
                        srv_logs = _persist_container_logs(browser_server_name, project_dir, spec_name, dry_run=dry_run)
                        if srv_logs:
                            log.error("browser server container logs:\n%s", srv_logs)
                    gate_result.server_error = srv_logs or str(exc)
                    _append_gate_report_row(project_dir, spec_name, "Server Startup", 1)
                    _append_gate_summary_row(project_dir, spec_name, "Server Startup", 1)
                    _rewrite_summary_result(project_dir, spec_name, "FAIL")
                    gate_result.exit_code = max(gate_result.exit_code, 1)
                    return gate_result
                rewritten_browser_url = _rewrite_base_url(original_browser_url, browser_server_name)
            elif _cmds_same and server_name:
                # Same command as HTTP server — reuse its container via Docker-internal DNS.
                rewritten_browser_url = _rewrite_base_url(original_browser_url, server_name)
            else:
                rewritten_browser_url = original_browser_url

            b_result = _run_format_b_runner(
                spec_name, config, run_id, project_dir,
                rewritten_browser_url, iteration=iteration, dry_run=dry_run,
            )
            if b_result.exit_code != 0:
                _output = (b_result.stderr or b_result.stdout or "").strip()
                log.warning(
                    "journey scenario runner exited %d:\n%s",
                    b_result.exit_code, _output[:4000] if _output else "(no output)",
                )
                _persist_runner_output(
                    project_dir, spec_name, "journey-runner", _output,
                )
                gate_result.exit_code = max(gate_result.exit_code, 1)
            _append_gate_report_row(
                project_dir, spec_name, "Blind Scenarios (Journey)", b_result.exit_code
            )

    finally:
        # Deduplicate: if future refactors ever assign the same name to both vars,
        # we must not call stop+remove twice on the same container.
        _seen: set[str] = set()
        for cname in [server_name, browser_server_name]:
            if cname and cname not in _seen:
                _seen.add(cname)
                _stop_server_container(cname, dry_run=dry_run)
        # Stop service sidecars after the server (reverse dependency order).
        if service_names:
            _stop_service_containers(service_names, dry_run=dry_run)

        # Merge A/B runner count fragments into the canonical counts file.
        # Runs in finally so early returns (server startup failures) still
        # produce a canonical file — otherwise prepass reads stale data and
        # the Oracle sees 0% despite executable tests passing.
        _accumulate_ab_scenario_counts(project_dir, spec_name, gate_result)
        _accumulate_category_breakdowns(project_dir, spec_name)
        _ensure_counts_file(project_dir, spec_name, gate_result)

        # Record sentinel status so iteration.py doesn't re-stat the filesystem.
        gate_result.sentinel_present = has_sentinel_file(
            project_dir / ".dark-factory" / "scenarios" / spec_name
        )

        # Crash-adjusted pass rate: exclude crashed scenarios from the denominator.
        _apply_crash_adjustment(gate_result)

        # Parse hard gate status from the summary file.
        _parse_hard_gate_status(gate_result, project_dir, spec_name)

    # Mutation testing step (optional).
    if gate_result.exit_code == 0 and config.mutation_cmd:
        _run_mutation_gate(gate_result, config, run_id, project_dir, dry_run=dry_run)

    return gate_result


def run_pre_gate(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    *,
    iteration: int = 1,
    dry_run: bool = False,
) -> PreGateResult:
    """Run the pre-gate (Build + Lint + Typecheck only) in a Docker container.

    Uses the same image, Docker args, and entrypoint as run_quality_gate(), but
    passes --pre-gate to quality-gate.sh so it exits after Gate 3. Does NOT call
    _ensure_gate_summary() and does NOT start scenario server containers.
    The pre-gate report ({spec}-pre-gate.md) is consumed only by the
    re-implementation prompt — never by the reviewer or oracle.
    """
    gate_cmd = ["/opt/dark-factory/scripts/quality-gate.sh", spec_name, "--pre-gate"]
    if config.language:
        gate_cmd.extend(["--language", config.language])
    if config.build_cmd:
        gate_cmd.extend(["--build-cmd", config.build_cmd])
    if config.lint_cmd:
        gate_cmd.extend(["--lint-cmd", config.lint_cmd])
    if config.typecheck_cmd:
        gate_cmd.extend(["--typecheck-cmd", config.typecheck_cmd])
    # Bug #6 fix: forward architect-supplied install command (commands.install)
    # so the gate container actually installs deps before running build/lint/test.
    if config.server_setup_cmd:
        gate_cmd.extend(["--install-cmd", config.server_setup_cmd])
    if config.source_dirs:
        gate_cmd.extend(["--source-dir", ":".join(config.source_dirs)])
    gate_cmd.extend(["--iteration", str(iteration)])  # for shell script consistency

    check_language_image(config.language, spec_name=spec_name)

    timeout = config.gate_timeout * 3  # 3 gates × 1 per gate (build, lint, typecheck)
    result = _launch_gate_container(
        spec_name, config, run_id, project_dir, gate_cmd,
        timeout=timeout, dry_run=dry_run,
    )
    # RunResult exposes .stdout and .stderr (not .output) — combine them.
    output = (result.stdout or "") + (result.stderr or "")
    return PreGateResult(exit_code=result.exit_code, output=output)


# ==============================================================================
# Server smoke check (pre-gate server startup validation)
# ==============================================================================

def run_server_smoke(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    *,
    iteration: int = 1,
    dry_run: bool = False,
) -> ServerSmokeResult:
    """Start services + server, check health endpoint. Returns running containers on success.

    On success: caller owns the containers and must clean them up (or pass to
    run_quality_gate via the existing_server/existing_services parameters).
    On failure: containers are cleaned up by _start_server_stack before returning.
    """
    result = ServerSmokeResult()
    formats = _detect_formats(project_dir, spec_name)

    if not (formats.has_http or formats.has_journey):
        result.success = True
        return result

    if not config.test_server_cmd:
        result.success = True
        return result

    original_base_url = config.test_base_url or "http://localhost:8000"
    # Phase 4: services start for ANY scenario format.
    needs_services = bool(config.services)

    stack = _start_server_stack(
        spec_name, config, run_id, project_dir,
        server_cmd=config.test_server_cmd,
        base_url=original_base_url,
        needs_services=needs_services,
        dry_run=dry_run,
    )

    # Phase 1.1: persist smoke logs unconditionally — success AND failure.
    # _start_server_stack captures container logs before its teardown on
    # failure; on success the container is still running so we fetch here.
    server_logs = stack.server_logs
    if not stack.error and stack.server_name and not dry_run:
        try:
            server_logs = container_logs(stack.server_name, tail=500, dry_run=False) or ""
        except subprocess.CalledProcessError as exc:
            server_logs = f"(could not fetch container logs: {exc})"
    _persist_smoke_logs(
        project_dir, spec_name, run_id, iteration,
        server_container=stack.server_name,
        server_logs=server_logs,
        resolved_setup_cmd=config.server_setup_cmd,
        resolved_server_cmd=config.test_server_cmd,
        error=stack.error,
        dry_run=dry_run,
    )

    if stack.error:
        result.server_error = stack.error
        return result

    result.success = True
    result.server_name = stack.server_name
    result.service_names = stack.service_names
    result.inject_env = stack.inject_env
    return result


# ==============================================================================
# Scenario smoke test (P3)
# ==============================================================================

# Error classes that indicate scenario crashes (not assertion failures).
_CRASH_ERROR_TYPES = frozenset({
    "TypeError", "ReferenceError", "SyntaxError",
    "Cannot find module", "ModuleNotFoundError",
    "ImportError", "NameError",
})

# Error types where the message describes a scenario bug, not implementation
# details.  "ReferenceError: spawn is not defined" = scenario forgot an import.
# "NameError: name 'foo' is not defined" = same pattern in Python.
# Messages are kept but paths are still redacted.
_SAFE_MESSAGE_TYPES = frozenset({"ReferenceError", "TypeError", "SyntaxError", "NameError"})

# Types where the message may leak implementation details — strip to type only.
_UNSAFE_MESSAGE_TYPES = _CRASH_ERROR_TYPES - _SAFE_MESSAGE_TYPES

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

# Pattern matching path-like tokens (e.g., /project/src/foo.ts, ../src/config).
_PATH_TOKEN_RE = re.compile(r"(?:\.\./|/)[^\s:,)]+")


def sanitize_crash_output(raw_output: str, scenarios_dir_name: str) -> str:
    """Allowlist-based crash output sanitization.

    Keeps only error class names + scenario file references, with non-scenario
    paths redacted.  Prevents the Scenario Architect from gaining
    implementation knowledge during repair mode.

    Sanitization rules:
    - Lines referencing scenario files: kept, but non-scenario paths redacted
    - Lines with safe error types (ReferenceError, TypeError, SyntaxError,
      NameError): kept with message, paths redacted — these describe scenario
      bugs, not implementation details
    - Lines with unsafe error types (ModuleNotFoundError, ImportError,
      "Cannot find module"): stripped to type name only
    - A line matching both safe AND unsafe types is treated as unsafe
    - All other lines: dropped
    """
    clean = _ANSI_RE.sub("", raw_output)
    lines = clean.splitlines()
    sanitized: list[str] = []

    for line in lines:
        # Lines referencing scenario files: keep but redact non-scenario paths.
        if scenarios_dir_name in line:
            # Replace path-like tokens that are NOT scenario paths.
            def _redact_non_scenario(match: re.Match[str]) -> str:
                path = match.group(0)
                if scenarios_dir_name in path:
                    return path
                return "[REDACTED]"
            sanitized.append(_PATH_TOKEN_RE.sub(_redact_non_scenario, line))
            continue

        # Error class lines: check unsafe types FIRST — a line matching both
        # safe and unsafe must be treated as unsafe (e.g., "TypeError: Cannot
        # find module ..." matches both TypeError and "Cannot find module").
        unsafe_match = next(
            (t for t in sorted(_UNSAFE_MESSAGE_TYPES) if t in line), None,
        )
        if unsafe_match:
            sanitized.append(unsafe_match)
            continue
        safe_match = next(
            (t for t in sorted(_SAFE_MESSAGE_TYPES) if t in line), None,
        )
        if safe_match:
            # Message describes a scenario bug — keep it, but redact any
            # paths that could leak implementation details.
            redacted = _PATH_TOKEN_RE.sub(
                lambda m: m.group(0) if scenarios_dir_name in m.group(0) else "[REDACTED]",
                line.strip(),
            )
            sanitized.append(redacted)
            continue
        # Lines that don't match scenario files or error types are dropped.

    return "\n".join(sanitized) if sanitized else "(no crash details extracted)"


def run_smoke_test(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    *,
    dry_run: bool = False,
) -> SmokeTestResult:
    """Run scenarios against the project BEFORE implementation (P3 guard).

    Classifies each scenario result as:
    - ASSERTION_FAIL: healthy (scenarios should fail before implementation)
    - CRASH: scenario bug (TypeError, ReferenceError, etc.)
    - PASS: suspicious (may be tautological)

    The smoke test uses --smoke flag to skip Gates 1-4 and only run scenarios.
    """
    gate_cmd = ["/opt/dark-factory/scripts/quality-gate.sh", spec_name, "--smoke"]
    if config.language:
        gate_cmd.extend(["--language", config.language])
    if config.source_dirs:
        gate_cmd.extend(["--source-dir", ":".join(config.source_dirs)])
    if config.scenario_test_cmd:
        gate_cmd.extend(["--scenario-test-cmd", config.scenario_test_cmd])
    if config.server_setup_cmd:
        gate_cmd.extend(["--install-cmd", config.server_setup_cmd])
    gate_cmd.extend(["--gate-timeout", str(config.gate_timeout)])
    gate_cmd.extend(["--scenario-timeout", str(config.scenario_timeout)])
    gate_cmd.extend(["--iteration", "0"])  # smoke test is pre-iteration

    try:
        check_language_image(config.language, spec_name=spec_name)
    except Exception:
        log.warning("smoke test skipped: Docker image not available")
        return SmokeTestResult()

    effective_timeout = config.scenario_timeout if config.scenario_timeout > 0 else config.gate_timeout
    try:
        result = _launch_gate_container(
            spec_name, config, run_id, project_dir, gate_cmd,
            timeout=effective_timeout, dry_run=dry_run,
        )
    except Exception:
        log.warning("smoke test skipped: container launch failed", exc_info=True)
        return SmokeTestResult()

    output = (result.stdout or "") + (result.stderr or "")
    clean = _ANSI_RE.sub("", output)
    smoke = SmokeTestResult(exit_code=result.exit_code)

    # Parse the counts file written by quality-gate.sh --smoke.
    counts_file = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-smoke-test.env"
    )
    if counts_file.exists():
        data: dict[str, str] = {}
        for line in counts_file.read_text().splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                data[key] = value
        smoke.crashes = _safe_int(data.get("SCENARIO_CRASHED", "0"))
        smoke.assertion_failures = _safe_int(data.get("SCENARIO_ASSERT_FAILED", "0"))
        total_passes = _safe_int(data.get("SCENARIO_PASSED", "0"))
        # All passes in a smoke test are suspicious or tautological.
        smoke.suspicious_passes = total_passes
        smoke.tautological_passes = total_passes

    # Sanitize crash output for SA repair (allowlist only).
    if smoke.crashes > 0:
        smoke.sanitized_crash_output = sanitize_crash_output(
            output, f".dark-factory/scenarios/{spec_name}"
        )

    return smoke


# ==============================================================================
# Format detection
# ==============================================================================

def _detect_formats(project_dir: Path, spec_name: str) -> _FormatFlags:
    """Detect which scenario formats exist in the spec's scenarios directory."""
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
    if not scenarios_dir.is_dir():
        return _FormatFlags()

    flags = _FormatFlags()
    # Format A (*.scenarios.md) retired in Phase 4.6b.1a — never set has_http.
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
    extra_env: dict[str, str] | None = None,
    dry_run: bool = False,
) -> str:
    """Start a detached server container. Returns the container name.

    Health checks use ``docker exec`` inside the container, so no port
    publishing is needed.  This allows the container to stay on
    ``--internal`` Docker networks used by egress filtering.

    ``extra_env`` injects additional environment variables (e.g., DATABASE_URL
    from service sidecar ``inject_env`` fields).
    """
    if name is None:
        name = _server_container_name(run_id)

    # Server containers need more PIDs than the default 256 for multi-worker
    # app servers (gunicorn, uvicorn, etc.).
    docker_args = build_base_args(config, run_id, pids_limit=512)
    docker_args.extend(build_project_mount(str(project_dir), rw=False))
    docker_args.extend(build_cache_volumes("test-server"))
    # project_rw=True gives the server container tmpfs overlays over
    # node_modules (and similar per-language dirs).  The underlying bind
    # mount is still :ro — the tmpfs sits on top, so writes go to the
    # ephemeral overlay, not the host filesystem.  Without this, the
    # server has no writable node_modules and pnpm install cannot
    # populate dependencies.
    docker_args.extend(build_platform_isolation_overlays(config.language, project_dir, project_rw=True))

    # Parse port from base_url for HOST/PORT env injection.
    # No port publishing — health checks use docker exec (not host polling),
    # so the server works on --internal networks used by egress filtering.
    parsed = urlparse(base_url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    docker_args.extend([
        "--network", network_name(run_id, "execution"),
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=test-server",
        "--entrypoint", "bash",
        "--workdir", "/project",
        # Force the server to bind to all interfaces and use the port
        # derived from test_base_url.  HOST=0.0.0.0 ensures the server
        # is reachable from other containers on the Docker network.
        # Both can be overridden via [test_server] env in config.toml.
        "-e", "HOST=0.0.0.0",
        "-e", f"PORT={port}",
    ])
    # Inject user-configured server env vars (e.g., FLASK_RUN_HOST=0.0.0.0).
    # These come after the default HOST=0.0.0.0 so they can override it.
    for k, v in config.server_env.items():
        docker_args.extend(["-e", f"{k}={v}"])
    for k, v in (extra_env or {}).items():
        docker_args.extend(["-e", f"{k}={v}"])

    # The server container starts with empty node_modules (tmpfs overlay).
    # Prepend a dependency install so packages resolve from the shared
    # pnpm-store cache before the server command runs.
    effective_cmd = _dep_install_prefix(config.language) + server_cmd

    start_container(
        docker_args,
        f"dark-factory-{config.language}",
        ["-c", effective_cmd],
        name=name,
        dry_run=dry_run,
    )
    return name


def _wait_for_server(
    container_name: str,
    port: int,
    health_path: str,
    timeout: int,
    *,
    dry_run: bool = False,
) -> None:
    """Poll server health via ``docker exec`` until 200 or timeout.

    Uses ``wget`` inside the server container to check the health endpoint.
    This avoids Docker port publishing, which does not work on ``--internal``
    networks used by egress filtering.
    """
    if dry_run:
        return

    # Use 127.0.0.1 (not localhost) — BusyBox wget resolves localhost to ::1
    # (IPv6) on Alpine, but servers bind to 0.0.0.0 (IPv4 only via HOST env).
    url = f"http://127.0.0.1:{port}{health_path}"
    deadline = time.monotonic() + timeout
    start = time.monotonic()
    attempt = 0
    last_error = ""
    while time.monotonic() < deadline:
        attempt += 1
        result = _run(
            ["docker", "exec", container_name, "wget", "-q", "-O", "/dev/null", url],
            timeout=5,
        )
        if result.exit_code == 0:
            log.info("server healthy after %.1fs (%d attempts)", time.monotonic() - start, attempt)
            return
        last_error = (result.stderr or "").strip()
        log.debug("health poll #%d at %.1fs: exit %d %s", attempt, time.monotonic() - start, result.exit_code, last_error)
        time.sleep(1)
    elapsed = time.monotonic() - start
    err_detail = f" (last error: {last_error})" if last_error else ""
    raise TimeoutError(
        f"Server {container_name} did not become healthy at {url} within {timeout}s "
        f"({attempt} attempts over {elapsed:.1f}s){err_detail}"
    )


def _stop_server_container(name: str, *, dry_run: bool = False) -> None:
    """Stop and remove a server container started by _start_server_container."""
    stop_container(name, dry_run=dry_run)
    remove_container(name, dry_run=dry_run)


# ==============================================================================
# Service sidecar containers (Postgres, Redis, etc.)
# ==============================================================================

def _service_container_name(run_id: str, service_name: str) -> str:
    return f"df-{run_id}-svc-{service_name}"


def _topo_sort_services(services: list[ServiceDef]) -> list[ServiceDef]:
    """Topological sort of ServiceDefs by depends_on. Raises on cycles.

    Returns a new list ordered so that every service appears after all of
    its dependencies. Services with no dependencies retain their original
    relative order (stable sort via Kahn's algorithm with a deterministic
    seed queue).
    """
    by_name: dict[str, ServiceDef] = {}
    for s in services:
        if s.name in by_name:
            raise ConfigError(f"duplicate service name: '{s.name}'")
        by_name[s.name] = s

    # in_degree[name] = number of unresolved deps
    in_degree: dict[str, int] = {s.name: 0 for s in services}
    dependents: dict[str, list[str]] = {s.name: [] for s in services}
    for svc in services:
        for dep in svc.depends_on:
            if dep == svc.name:
                raise ConfigError(f"service '{svc.name}' depends on itself")
            if dep not in by_name:
                raise ConfigError(
                    f"service '{svc.name}' depends_on '{dep}' which is not defined"
                )
            in_degree[svc.name] += 1
            dependents[dep].append(svc.name)

    # Seed: services with no deps, in original order for stability.
    queue = [s.name for s in services if in_degree[s.name] == 0]
    result: list[ServiceDef] = []
    while queue:
        name = queue.pop(0)
        result.append(by_name[name])
        for dep_name in sorted(dependents[name]):
            in_degree[dep_name] -= 1
            if in_degree[dep_name] == 0:
                queue.append(dep_name)

    if len(result) != len(services):
        remaining = sorted(set(by_name) - {s.name for s in result})
        raise ConfigError(
            f"circular depends_on among services: {remaining}"
        )
    return result


def _start_service_containers(
    config: PipelineConfig,
    run_id: str,
    started: list[str],
    *,
    dry_run: bool = False,
) -> None:
    """Start service sidecar containers and wait for each to be healthy.

    Services are started in topological order (by depends_on) — each must
    pass its health check before the next starts.  Container names are
    appended to ``started`` as they launch so the caller can clean them up
    even if a later health check times out.
    """
    exec_net = network_name(run_id, "execution")

    for svc in _topo_sort_services(config.services):
        name = _service_container_name(run_id, svc.name)
        docker_args = [
            "--network", exec_net,
            "--network-alias", svc.name,
            "--label", f"df-run-id={run_id}",
            "--label", "df-role=service",
            "--name", name,
        ]
        for k, v in svc.env.items():
            docker_args.extend(["-e", f"{k}={v}"])

        start_container(docker_args, svc.image, [], name=name, dry_run=dry_run)
        started.append(name)

        if svc.health_cmd:
            _wait_for_service(name, svc.health_cmd, svc.health_timeout, dry_run=dry_run)


def _wait_for_service(
    container_name: str,
    health_cmd: str,
    timeout: int,
    *,
    dry_run: bool = False,
) -> None:
    """Poll ``docker exec`` health command until exit 0 or timeout."""
    if dry_run:
        return

    last_stderr = ""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = _run(
            ["docker", "exec", container_name, "sh", "-c", health_cmd],
        )
        if result.exit_code == 0:
            log.debug("service %s healthy", container_name)
            return
        last_stderr = (result.stderr or "").strip()
        time.sleep(1)
    detail = f" last error: {last_stderr}" if last_stderr else ""
    raise TimeoutError(
        f"Service {container_name} did not become healthy within {timeout}s "
        f"(health_cmd: {health_cmd}){detail}"
    )


def _stop_service_containers(names: list[str], *, dry_run: bool = False) -> None:
    """Stop and remove service sidecar containers."""
    for name in names:
        try:
            stop_container(name, dry_run=dry_run)
            remove_container(name, dry_run=dry_run)
        except Exception:
            log.warning("failed to clean up service container %s", name, exc_info=True)


def _collect_inject_env(config: PipelineConfig) -> dict[str, str]:
    """Merge inject_env from all service definitions.

    Raises ConfigError if two services define the same key.
    """
    merged: dict[str, str] = {}
    for svc in config.services:
        for k, v in svc.inject_env.items():
            if k in merged:
                raise ConfigError(
                    f"inject_env key '{k}' defined by service '{svc.name}' "
                    f"conflicts with an earlier service"
                )
            merged[k] = v
    return merged


# ==============================================================================
# Shared server stack startup
# ==============================================================================

@dataclass
class _ServerStack:
    """Running server stack (services + server). Returned by _start_server_stack."""
    server_name: str = ""
    service_names: list[str] = field(default_factory=list)
    inject_env: dict[str, str] = field(default_factory=dict)
    error: str = ""
    failed_stage: str = ""  # "Service Startup", "Server Setup", or "Server Startup"
    # Phase 1.1: container logs captured BEFORE the failure-path teardown so
    # _persist_smoke_logs has something useful to write on failure.
    server_logs: str = ""


def _start_server_stack(
    spec_name: str,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    *,
    server_cmd: str,
    base_url: str,
    needs_services: bool = False,
    run_setup: bool = True,
    dry_run: bool = False,
) -> _ServerStack:
    """Start services + setup + server + health check. Returns running stack.

    On success: caller owns the containers and must clean them up.
    On failure: containers are cleaned up before returning; ``error`` is populated.
    """
    stack = _ServerStack()
    service_names: list[str] = []
    server_name: str | None = None

    try:
        # 1. Start service sidecars
        if needs_services and config.services:
            try:
                _start_service_containers(config, run_id, service_names, dry_run=dry_run)
            except TimeoutError as exc:
                stack.failed_stage = "Service Startup"
                stack.error = f"Service startup failed: {exc}"
                if service_names:
                    svc_logs = _persist_container_logs(service_names[-1], project_dir, spec_name, dry_run=dry_run)
                    if svc_logs:
                        stack.error = svc_logs
                return stack

        # 2. Collect inject_env
        svc_inject_env = _collect_inject_env(config) if config.services else {}

        # 3. Run setup command (migrations etc.)
        if run_setup and config.server_setup_cmd:
            effective_setup_cmd = _dep_install_prefix(config.language) + config.server_setup_cmd
            log.info("running server setup command: %s", effective_setup_cmd)
            setup_result = _launch_gate_container(
                spec_name, config, run_id, project_dir,
                gate_cmd=["-c", effective_setup_cmd],
                timeout=config.gate_timeout + _INSTALL_TIMEOUT_PADDING,
                extra_env=svc_inject_env,
                dry_run=dry_run,
            )
            if setup_result.exit_code != 0:
                _output = (setup_result.stderr or setup_result.stdout or "(no output)")
                stack.failed_stage = "Server Setup"
                stack.error = (
                    f"server_setup_cmd failed (exit {setup_result.exit_code}):\n"
                    + _output[-2000:]
                )
                return stack

        # 4. Start server
        server_name = _start_server_container(
            config, run_id, project_dir,
            server_cmd,
            base_url=base_url,
            extra_env=svc_inject_env,
            dry_run=dry_run,
        )

        # 5. Wait for health (via docker exec, not host port publishing)
        _has_install = bool(_dep_install_prefix(config.language))
        _timeout = config.server_startup_timeout + (
            _INSTALL_TIMEOUT_PADDING if _has_install else 0
        )
        _parsed_url = urlparse(base_url)
        _port = _parsed_url.port or 80
        _wait_for_server(
            server_name,
            _port,
            config.server_health_path or "/health",
            _timeout,
            dry_run=dry_run,
        )

        # Success — transfer ownership to caller
        stack.server_name = server_name
        stack.service_names = list(service_names)
        stack.inject_env = svc_inject_env
        return stack

    except TimeoutError as exc:
        stack.failed_stage = "Server Startup"
        srv_logs = ""
        if server_name:
            srv_logs = _persist_container_logs(server_name, project_dir, spec_name, dry_run=dry_run)
            if srv_logs:
                log.error("server container logs:\n%s", srv_logs)
        stack.error = srv_logs or str(exc)
        return stack

    finally:
        if stack.error:
            # Phase 1.1: capture container logs BEFORE teardown so the smoke
            # log file on the failure path is actually useful.
            if server_name and not dry_run:
                try:
                    stack.server_logs = container_logs(server_name, tail=500, dry_run=False) or ""
                except subprocess.CalledProcessError as exc:
                    stack.server_logs = f"(could not fetch container logs: {exc})"
            if server_name:
                _stop_server_container(server_name, dry_run=dry_run)
            if service_names:
                _stop_service_containers(service_names, dry_run=dry_run)


def cleanup_smoke_containers(smoke_result: ServerSmokeResult, *, dry_run: bool = False) -> None:
    """Clean up containers owned by a successful smoke result. Idempotent."""
    if smoke_result.server_name:
        try:
            _stop_server_container(smoke_result.server_name, dry_run=dry_run)
        except Exception:
            log.warning("failed to clean up smoke server %s", smoke_result.server_name, exc_info=True)
    if smoke_result.service_names:
        try:
            _stop_service_containers(smoke_result.service_names, dry_run=dry_run)
        except Exception:
            log.warning("failed to clean up smoke services", exc_info=True)


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
    iteration: int = 0,
    dry_run: bool = False,
) -> RunResult:
    """Run http-scenario-runner.py in dark-factory-python."""
    docker_args = build_base_args(config, run_id)
    docker_args.extend(build_project_mount(str(project_dir), rw=True))
    docker_args.extend(build_cache_volumes("http-runner"))
    # Format A always runs in dark-factory-python regardless of project toolchain.
    docker_args.extend(build_platform_isolation_overlays("python", project_dir))
    docker_args.extend(build_scripts_mount())
    docker_args.extend([
        "--entrypoint", "",
        "--network", network_name(run_id, "execution"),
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=http-runner",
        "--workdir", "/project",
    ])
    cmd = [
        "python3", "/opt/dark-factory/scripts/lib/http-scenario-runner.py",
        f"/project/.dark-factory/scenarios/{spec_name}",
        "--base-url", base_url,
        "--reports-dir", "/project/.dark-factory/reports",
        "--spec-name", spec_name,
        "--iteration", str(iteration),
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
    iteration: int = 0,
    dry_run: bool = False,
) -> RunResult:
    """Run journey-runner.py in dark-factory-playwright."""
    docker_args = build_base_args(config, run_id)
    docker_args.extend(build_project_mount(str(project_dir), rw=True))
    docker_args.extend(build_cache_volumes("journey-runner"))
    # Format B always runs in dark-factory-playwright (Python-based) regardless of project toolchain.
    docker_args.extend(build_platform_isolation_overlays("python", project_dir))
    docker_args.extend(build_scripts_mount())
    docker_args.extend([
        "--entrypoint", "",
        "--network", network_name(run_id, "execution"),
        "--label", f"df-run-id={run_id}",
        "--label", "df-role=journey-runner",
        "--workdir", "/project",
    ])
    cmd = [
        "python3", "/opt/dark-factory/scripts/lib/journey-runner.py",
        f"/project/.dark-factory/scenarios/{spec_name}",
        "--base-url", base_url,
        "--reports-dir", "/project/.dark-factory/reports",
        "--spec-name", spec_name,
        "--iteration", str(iteration),
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


def _append_gate_summary_row(
    project_dir: Path,
    spec_name: str,
    gate_name: str,
    exit_code: int,
) -> None:
    """Append a result row to the quality gate summary markdown.

    Inserts the row before the ``## Result:`` line so it appears in the
    table, not after the verdict.  Skips gracefully if the summary file
    doesn't exist (``_ensure_gate_summary`` will create a fallback later).
    """
    summary_path = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-quality-gate-summary.md"
    )
    if not summary_path.exists():
        return
    status = "PASS" if exit_code == 0 else "FAIL"
    row = f"| {gate_name} | {status} |\n"
    content = summary_path.read_text()
    m = re.search(r'^\s*## Result:', content, re.MULTILINE)
    if m:
        content = content[:m.start()] + row + content[m.start():]
        summary_path.write_text(content)
    else:
        with summary_path.open("a") as f:
            f.write(row)


def _rewrite_summary_result(
    project_dir: Path,
    spec_name: str,
    result_label: str,
) -> None:
    """Overwrite the ``## Result:`` line in the gate summary."""
    summary_path = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-quality-gate-summary.md"
    )
    if not summary_path.exists():
        return
    content = summary_path.read_text()
    new_content = re.sub(
        r'^(## Result:)\s*\S+',
        rf'\1 {result_label}',
        content,
        flags=re.MULTILINE,
    )
    if new_content != content:
        summary_path.write_text(new_content)


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

# Keys whose values are counts and should be summed across fragment files.
_SUMMABLE_KEYS = frozenset({
    "SCENARIO_PASSED", "SCENARIO_FAILED", "SCENARIO_TOTAL",
    "SCENARIO_CRASHED", "SCENARIO_ASSERT_FAILED",
    "SCENARIO_SKIPPED", "SCENARIO_REGISTERED",
})

# Keys whose values are metadata or percentages — last-writer-wins, not summed.
_DIRECT_SET_KEYS = frozenset({
    "SATISFACTION_SCORE", "MULTI_RUN_N", "MULTI_RUN_K", "SCENARIO_RATE",
})

# Category breakdown: suffixes that determine sum vs last-writer-wins.
_CAT_SUMMABLE_SUFFIXES = ("_PASSED", "_TOTAL")
_CAT_NONSUMMABLE_SUFFIXES = ("_WEIGHT",)


def _accumulate_ab_scenario_counts(
    project_dir: Path,
    spec_name: str,
    gate_result: GateResult,
) -> None:
    """Merge all scenario count fragment files into the canonical counts file.

    Each producer (executable, http, journey, satisfaction) writes its own
    fragment file (``{spec}-scenario-counts-{format}.env``).  This function
    is the **sole writer** of the canonical ``{spec}-scenario-counts.env``.

    When no fragments exist (backwards-compat: bash wrote directly to
    canonical), the canonical is left untouched.
    """
    reports_dir = project_dir / ".dark-factory" / "reports"
    canonical = reports_dir / f"{spec_name}-scenario-counts.env"
    # The glob pattern includes the trailing hyphen after spec_name, so
    # "auth-scenario-counts-*.env" cannot match "auth-admin-scenario-counts-http.env".
    fragments = list(reports_dir.glob(f"{spec_name}-scenario-counts-*.env"))

    if not fragments:
        if not canonical.exists():
            log.warning(
                "no scenario count fragments found for spec %s; "
                "this likely means scenarios did not execute — investigate "
                "container logs",
                spec_name,
            )
            reports_dir.mkdir(parents=True, exist_ok=True)
            canonical.write_text(
                "SCENARIO_PASSED=0\nSCENARIO_FAILED=0\n"
                "SCENARIO_TOTAL=0\nSCENARIO_RATE=0\n"
            )
        _parse_scenario_counts(gate_result, project_dir, spec_name)
        return

    merged: dict[str, int] = {}
    for frag in fragments:
        log.debug("reading scenario count fragment: %s", frag.name)
        for line in frag.read_text().splitlines():
            key, _, val = line.partition("=")
            if not key:
                continue
            if key in _SUMMABLE_KEYS:
                merged[key] = merged.get(key, 0) + _safe_int(val)
            elif key in _DIRECT_SET_KEYS:
                merged[key] = _safe_int(val)
            else:
                log.warning("unknown key in fragment %s: %s", frag.name, key)

    # Recompute pass rate from accumulated counts.
    total = merged.get("SCENARIO_TOTAL", 0)
    rate = (merged.get("SCENARIO_PASSED", 0) * 100 // total) if total > 0 else 0
    merged["SCENARIO_RATE"] = rate

    # When no producer wrote SATISFACTION_SCORE (single-run mode with only
    # HTTP/journey scenarios), default to pass_rate so the metric isn't
    # stuck at 0% for HTTP-only specs.
    if "SATISFACTION_SCORE" not in merged:
        merged["SATISFACTION_SCORE"] = rate

    reports_dir.mkdir(parents=True, exist_ok=True)
    canonical.write_text(
        "\n".join(f"{k}={v}" for k, v in sorted(merged.items())) + "\n"
    )
    for frag in fragments:
        frag.unlink(missing_ok=True)
    _parse_scenario_counts(gate_result, project_dir, spec_name)


def _accumulate_category_breakdowns(
    project_dir: Path,
    spec_name: str,
) -> None:
    """Merge category breakdown fragment files into the canonical breakdown file.

    Same single-writer pattern as scenario counts: each producer writes its
    own fragment, this function merges them into the canonical file.
    """
    reports_dir = project_dir / ".dark-factory" / "reports"
    fragments = list(reports_dir.glob(f"{spec_name}-category-breakdown-*.env"))
    if not fragments:
        return
    canonical = reports_dir / f"{spec_name}-category-breakdown.env"
    merged: dict[str, int] = {}
    for frag in fragments:
        for line in frag.read_text().splitlines():
            key, _, val = line.partition("=")
            if not key:
                continue
            if any(key.endswith(s) for s in _CAT_SUMMABLE_SUFFIXES):
                merged[key] = merged.get(key, 0) + _safe_int(val)
            elif any(key.endswith(s) for s in _CAT_NONSUMMABLE_SUFFIXES):
                merged[key] = _safe_int(val)
            else:
                log.warning("unknown key in breakdown fragment %s: %s", frag.name, key)
    canonical.write_text("\n".join(f"{k}={v}" for k, v in sorted(merged.items())) + "\n")
    for frag in fragments:
        frag.unlink(missing_ok=True)


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
# Hard gate status parsing
# ==============================================================================

# Gates whose PASS status is required for the stable-failure SHIP override.
_HARD_GATE_NAMES = frozenset({"Build", "Lint", "Type Check"})


def _parse_hard_gate_status(
    gate_result: GateResult,
    project_dir: Path,
    spec_name: str,
) -> None:
    """Parse Build/Lint/TypeCheck status from the quality gate summary file.

    Sets ``gate_result.hard_gates_pass`` to True when all hard gates either
    passed or were skipped (no command configured).  If the summary file is
    missing or unparseable, ``hard_gates_pass`` stays False (safe default).
    """
    summary_path = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-quality-gate-summary.md"
    )
    if not summary_path.exists():
        return

    # Parse markdown table rows: ``| Gate Name | STATUS |`` or
    # ``| Gate Name | STATUS | details |``.
    gate_statuses: dict[str, str] = {}
    for line in summary_path.read_text().splitlines():
        parts = [p.strip() for p in line.split("|")]
        # A valid row looks like ['', 'Build', 'PASS', ''] (4+ parts with
        # empty leading/trailing from the pipe delimiters).
        if len(parts) < 3:
            continue
        name = parts[1]
        status = parts[2]
        if name in _HARD_GATE_NAMES:
            gate_statuses[name] = status

    # All configured hard gates must be PASS or SKIP.  A gate that didn't
    # appear in the summary (e.g. no lint command) is implicitly skipped.
    all_pass = all(
        gate_statuses.get(g, "SKIP") in ("PASS", "SKIP")
        for g in _HARD_GATE_NAMES
    )
    gate_result.hard_gates_pass = all_pass
    if all_pass and gate_result.exit_code != 0:
        log.debug(
            "hard gates all pass but overall gate failed (exit_code=%d) — "
            "scenario-only failure eligible for stable-failure override",
            gate_result.exit_code,
        )


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
    gate_result.scenario_crashed = _safe_int(data.get("SCENARIO_CRASHED", "0"))
    gate_result.scenario_assert_failed = _safe_int(data.get("SCENARIO_ASSERT_FAILED", "0"))
    # Phase 7: skip observability.
    gate_result.scenario_skipped = _safe_int(data.get("SCENARIO_SKIPPED", "0"))
    gate_result.scenario_registered = _safe_int(data.get("SCENARIO_REGISTERED", "0"))
    gate_result.skip_failure = data.get("skip_failure", "").strip().lower() == "true"


def _apply_crash_adjustment(gate_result: GateResult) -> None:
    """Exclude crashed scenarios from pass rate denominator.

    Crashed scenarios (TypeError, SyntaxError, import errors) are scenario
    bugs, not implementation bugs.  They should not penalize the pass rate
    used by the Oracle for SHIP/NO-SHIP decisions.

    When crashes are present, ``pass_rate`` is recomputed over the effective
    total (total minus crashed).  The raw scenario_passed / scenario_failed /
    scenario_total counts are left unchanged; effective counts are written to
    ``scenario_effective_total`` and ``scenario_effective_failed``.

    Note: ``satisfaction_score`` is NOT adjusted here because it requires
    per-file crash data (``crashed_per_file``) that the shell gate does not
    yet expose.  The ``compute_satisfaction(crashed_per_file=...)`` API is
    ready — wiring it requires the shell to write per-file crash counts.
    """
    if gate_result.scenario_crashed <= 0:
        return

    raw_rate = gate_result.pass_rate  # value from shell before adjustment
    effective_total = gate_result.scenario_total - gate_result.scenario_crashed
    effective_failed = max(0, gate_result.scenario_failed - gate_result.scenario_crashed)

    gate_result.scenario_effective_total = effective_total
    gate_result.scenario_effective_failed = effective_failed

    if effective_total > 0:
        gate_result.pass_rate = min(
            100, (gate_result.scenario_passed * 100) // effective_total,
        )
    elif gate_result.scenario_total > 0:
        # All scenarios crashed — zero evidence of implementation failure.
        gate_result.pass_rate = 100
    # else: scenario_total == 0 — pass_rate stays 0 (no scenarios at all)

    gate_result.scenario_crash_adjusted = True
    log.info(
        "crash-adjusted pass rate: %d%% -> %d%% "
        "(%d crashed scenarios excluded from denominator)",
        raw_rate, gate_result.pass_rate, gate_result.scenario_crashed,
    )


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
    docker_args.extend(build_platform_isolation_overlays(config.language, project_dir))
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
