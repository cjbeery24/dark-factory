"""Quality gate runner — build/lint/type/test gate execution.

Replaces quality-gate.sh. Runs the quality gate script inside a Docker
container (not via Claude — direct bash entrypoint).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from darkfactory.docker.args import build_base_args
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

    # Gate command.
    gate_cmd = ["/project/.dark-factory/scripts/quality-gate.sh", spec_name]
    if config.test_cmd:
        gate_cmd.extend(["--test-cmd", config.test_cmd])
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

    # Mutation testing step (optional).
    if gate_result.exit_code == 0 and config.mutation_cmd:
        _run_mutation_gate(gate_result, config, run_id, project_dir, dry_run=dry_run)

    return gate_result


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
