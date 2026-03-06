"""Agent invocation — run agents in Docker containers with retry.

Replaces invoke_agent / run_agent from docker.sh.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path

from darkfactory.audit.checker import check_output_for_barriers
from darkfactory.docker.args import build_agent_args, build_claude_args
from darkfactory.docker.barriers import BarrierConfig
from darkfactory.docker.containers import execute_container
from darkfactory.docker.mounts import check_hardlink_safety, check_symlink_safety
from darkfactory.docker.networks import network_name
from darkfactory.errors import AgentTimeout
from darkfactory.events.types import emit_agent_completed, emit_agent_failed, emit_agent_start
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT

log = logging.getLogger(__name__)

# Minimum output length — below this, synthetic failure.
_MIN_OUTPUT_LEN = 20

# Roles that need execution network (not analysis).
_EXECUTION_ROLES = {AgentRole.IMPLEMENTER}


def invoke_agent(
    role: AgentRole,
    prompt: str,
    *,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    run_id: str,
    project_dir: Path,
    spec_name: str = "",
    iteration: int = 0,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    min_output: int = _MIN_OUTPUT_LEN,
    dry_run: bool = False,
) -> AgentResult:
    """Invoke an agent with retry logic.

    Runs the agent in a Docker container, retries on failure up to
    config.agent_retries times. Emits events and checks for anomalies.

    Returns AgentResult with exit code, output, and metadata.
    """
    barrier = barriers[role]
    image = f"dark-factory-{config.language}"
    max_attempts = config.agent_retries + 1

    # Network selection.
    if role in _EXECUTION_ROLES:
        network = network_name(run_id, "execution")
    else:
        network = network_name(run_id, "analysis")

    # Pre-flight safety checks.
    _run_safety_checks(role, barrier, project_dir, config)

    # Build Docker and Claude args.
    docker_args = build_agent_args(
        role, config, barriers, run_id, str(project_dir),
        network=network,
    )

    # Auth env vars.
    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if token:
        docker_args.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={token}"])
    elif api_key:
        docker_args.extend(["-e", f"ANTHROPIC_API_KEY={api_key}"])

    claude_args = build_claude_args(role, config, barriers)

    last_result = AgentResult(role=role, exit_code=1)

    for attempt in range(1, max_attempts + 1):
        if events_file:
            emit_agent_start(
                events_file, spec=spec_name, run_id=run_id,
                iteration=iteration, role=role.value, attempt=attempt,
            )

        start = time.monotonic()

        # Execute container — prompt is piped via stdin (echo | docker run).
        container_result = execute_container(
            docker_args, image, claude_args,
            timeout=config.agent_timeout,
            dry_run=dry_run,
            audit_path=str(audit_path) if audit_path else "",
        )

        wall_s = time.monotonic() - start
        stdout = container_result.stdout
        stderr = container_result.stderr
        output = stdout + ("\n" if stdout and not stdout.endswith("\n") and stderr else "") + stderr
        exit_code = container_result.exit_code
        timed_out = exit_code == EXIT_TIMEOUT

        # Minimum output check.
        if exit_code == 0 and len(output.strip()) < min_output:
            log.warning("agent %s output too short (%d bytes)", role.value, len(output.strip()))
            exit_code = 99

        result = AgentResult(
            role=role,
            exit_code=exit_code,
            output=output,
            timed_out=timed_out,
            wall_seconds=wall_s,
            retries=attempt - 1,
        )

        if exit_code == 0:
            if events_file:
                emit_agent_completed(
                    events_file, spec=spec_name, run_id=run_id,
                    iteration=iteration, role=role.value,
                    turns=config.agent_max_turns, exit_code=0,
                    model_id=config.model_id,
                    est_input_tokens=config.agent_max_turns * config.cost_est_input_tokens_per_turn,
                    est_output_tokens=config.agent_max_turns * config.cost_est_output_tokens_per_turn,
                )
            # Per-agent anomaly detection requires actual turn counts
            # from Claude Code output parsing (Phase 7).

            # Barrier audit.
            if audit_path and barrier.masked_dirs and events_file:
                violations = check_output_for_barriers(
                    output, barrier.masked_dirs, role.value,
                    audit_path, events_file, spec_name, run_id,
                )
                if violations:
                    result.barrier_violations = violations

            last_result = result
            break
        else:
            if events_file:
                reason = "timeout" if timed_out else f"exit {exit_code}"
                emit_agent_failed(
                    events_file, spec=spec_name, run_id=run_id,
                    iteration=iteration, role=role.value,
                    exit_code=exit_code, reason=reason,
                    model_id=config.model_id,
                    est_input_tokens=config.agent_max_turns * config.cost_est_input_tokens_per_turn,
                    est_output_tokens=config.agent_max_turns * config.cost_est_output_tokens_per_turn,
                )

            if attempt < max_attempts:
                log.warning("agent %s failed (attempt %d/%d), retrying", role.value, attempt, max_attempts)
            else:
                log.warning("agent %s failed after %d attempts", role.value, max_attempts)

            last_result = result

    return last_result


def _run_safety_checks(
    role: AgentRole,
    barrier: BarrierConfig,
    project_dir: Path,
    config: PipelineConfig,
) -> None:
    """Pre-flight symlink and hardlink safety checks."""
    if barrier.masked_dirs:
        check_symlink_safety(project_dir, barrier.masked_dirs)

    # Hardlink checks for roles that have both visible and masked dirs.
    if role == AgentRole.IMPLEMENTER:
        check_hardlink_safety(
            project_dir, config.source_dirs, barrier.masked_dirs,
        )
    elif role == AgentRole.SCENARIO_ARCHITECT:
        check_hardlink_safety(
            project_dir,
            [".dark-factory/scenarios"],
            barrier.masked_dirs,
        )
