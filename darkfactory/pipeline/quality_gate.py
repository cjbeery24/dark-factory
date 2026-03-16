"""Quality gate runner — build/lint/type/test gate execution.

Replaces quality-gate.sh. Runs the quality gate script inside a Docker
container (not via Claude — direct bash entrypoint).
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from darkfactory.docker.args import build_base_args
from darkfactory.errors import ConfigError
from darkfactory.docker.containers import execute_container
from darkfactory.docker.mounts import build_cache_volumes, build_project_mount
from darkfactory.docker.networks import network_name
from darkfactory.models.config import PipelineConfig

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
    # Pre-flight: if the spec has *.journey.md scenarios, the playwright image
    # must be present.  Fail early with a clear message rather than a cryptic
    # docker-run error inside the gate container.
    _check_playwright_image(spec_name, project_dir)

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
    gate_cmd.extend(["--server-startup-timeout", str(config.server_startup_timeout)])
    if config.server_health_path:
        gate_cmd.extend(["--server-health-path", config.server_health_path])
    if config.browser_base_url:
        gate_cmd.extend(["--browser-base-url", config.browser_base_url])
    if config.browser_server_cmd:
        gate_cmd.extend(["--browser-server-cmd", config.browser_server_cmd])
    gate_cmd.extend(["--browser-startup-timeout", str(config.browser_startup_timeout)])
    gate_cmd.extend(["--iteration", str(iteration)])

    result = execute_container(
        docker_args, image, gate_cmd,
        timeout=config.gate_timeout,
        dry_run=dry_run,
    )

    gate_result = GateResult(
        exit_code=result.exit_code,
        output=result.stdout + result.stderr,
    )

    # Parse scenario counts from the env file or output.
    _parse_scenario_counts(gate_result, project_dir, spec_name)

    # Guarantee the counts file exists so downstream readers (e.g. reviewer
    # prepass) don't warn.  When scenarios were skipped the container never
    # writes the file; we write zeros in that case.
    _ensure_counts_file(project_dir, spec_name, gate_result)

    # Mutation testing step (optional).
    if gate_result.exit_code == 0 and config.mutation_cmd:
        _run_mutation_gate(gate_result, config, run_id, project_dir, dry_run=dry_run)

    return gate_result


def _check_playwright_image(spec_name: str, project_dir: Path) -> None:
    """Fail early if journey scenarios are present but the playwright image is missing.

    A missing image inside the gate container produces a cryptic docker error.
    This pre-flight check surfaces a clear, actionable message instead.
    """
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
    if not scenarios_dir.is_dir():
        return
    if not list(scenarios_dir.rglob("*.journey.md")):
        return

    # Distinguish "Docker daemon not running" from "image not present".
    daemon_check = subprocess.run(
        ["docker", "info"],
        capture_output=True,
    )
    if daemon_check.returncode != 0:
        raise ConfigError(
            "Docker daemon is not running. Start Docker Desktop (or Docker Engine) "
            "and then retry."
        )

    result = subprocess.run(
        ["docker", "image", "inspect", "dark-factory-playwright"],
        capture_output=True,
    )
    if result.returncode != 0:
        raise ConfigError(
            f"Spec '{spec_name}' has *.journey.md scenarios but the "
            "'dark-factory-playwright' Docker image is not available. "
            "Build it first:\n"
            "  bash docker/build-images.sh python --with-playwright"
        )


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
    """Write a zero-value counts file if the container didn't produce one.

    quality-gate.sh only writes this file when blind scenarios are run.
    When scenarios are skipped the file is absent, causing downstream
    readers (e.g. review prepass) to warn unnecessarily.
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


def _run_mutation_gate(
    gate_result: GateResult,
    config: PipelineConfig,
    run_id: str,
    project_dir: Path,
    *,
    dry_run: bool = False,
) -> None:
    """Run mutation testing command and enforce minimum score."""
    import re

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
