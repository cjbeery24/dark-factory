"""Docker CLI argument builder — role-declarative configuration.

Replaces the massive if/elif chain in docker.sh's run_agent.
Each AgentRole gets a declarative RoleDockerConfig. The arg builder
reads the config and calls mounts.py functions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from darkfactory.docker.barriers import BarrierConfig
from darkfactory.docker.mounts import (
    build_cache_volumes,
    build_project_mount,
    build_tmpfs_masks,
)
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)


def build_base_args(
    config: PipelineConfig,
    run_id: str,
) -> list[str]:
    """Build common Docker container arguments.

    Returns args for security, resource limits, tmpfs /tmp and /home/agent.
    """
    args = [
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=256",
        f"--memory={config.agent_memory}",
        f"--cpus={config.agent_cpus}",
        "--user=1000:1000",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=512m",
        "--tmpfs", "/home/agent:rw,exec,nosuid,size=256m",
    ]

    # Optional I/O limits (Linux-only).
    if config.agent_io_device:
        if config.agent_io_read_bps:
            args.append(
                f"--device-read-bps={config.agent_io_device}:{config.agent_io_read_bps}"
            )
        if config.agent_io_write_bps:
            args.append(
                f"--device-write-bps={config.agent_io_device}:{config.agent_io_write_bps}"
            )

    return args


def build_agent_args(
    role: AgentRole,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    run_id: str,
    project_dir: str,
    *,
    network: str = "",
) -> list[str]:
    """Build complete Docker run arguments for an agent.

    Combines base args, project mount, tmpfs masks, cache volumes,
    network, and labels.
    """
    barrier = barriers[role]

    # Determine if project mount is rw.
    project_rw = role in (AgentRole.IMPLEMENTER,)

    args = build_base_args(config, run_id)
    args.extend(build_project_mount(project_dir, rw=project_rw))
    args.extend(build_tmpfs_masks(project_dir, barrier.masked_dirs))
    args.extend(build_cache_volumes(role.value))

    if network:
        args.extend(["--network", network])

    # Labels for cleanup identification.
    args.extend([
        "--label", f"df-run-id={run_id}",
        "--label", f"df-role={role.value}",
    ])

    return args


def build_claude_args(
    role: AgentRole,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    output_file: str = "",
) -> list[str]:
    """Build Claude CLI arguments for an agent invocation.

    The prompt is piped to stdin by the caller (not included in args).

    Returns args like: claude -p --dangerously-skip-permissions --max-turns N
    --disallowedTools ... --append-system-prompt ... --output-format json
    """
    barrier = barriers[role]
    args = [
        "claude", "-p",
        "--dangerously-skip-permissions",
        f"--max-turns={config.agent_max_turns}",
        "--output-format=json",
    ]

    if config.model_id:
        args.append(f"--model={config.model_id}")

    # Denied tools (defense-in-depth).
    if barrier.denied_tools:
        for tool in barrier.denied_tools:
            args.extend(["--disallowedTools", tool])

    # Barrier prompt (defense-in-depth).
    if barrier.barrier_prompt:
        args.extend(["--append-system-prompt", barrier.barrier_prompt])

    if output_file:
        args.extend(["--output", output_file])

    return args
