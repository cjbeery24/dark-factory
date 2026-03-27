"""Docker CLI argument builder — role-declarative configuration.

Replaces the massive if/elif chain in docker.sh's run_agent.
Each AgentRole gets a declarative RoleDockerConfig. The arg builder
reads the config and calls mounts.py functions.
"""

from __future__ import annotations

import logging

from darkfactory.docker.barriers import BarrierConfig
from darkfactory.docker.mounts import (
    build_cache_volumes,
    build_platform_isolation_overlays,
    build_project_mount,
    build_rw_overlays,
    build_tmpfs_masks,
)
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)


def build_base_args(
    config: PipelineConfig,
    run_id: str,
    *,
    pids_limit: int = 256,
) -> list[str]:
    """Build common Docker container arguments.

    Returns args for security, resource limits, tmpfs /tmp and /home/agent.

    Args:
        pids_limit: Maximum PIDs allowed in the container. Default 256 is
            appropriate for most containers. Server containers should use 512
            to accommodate multi-worker app servers.
    """
    args = [
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        f"--pids-limit={pids_limit}",
        f"--memory={config.agent_memory}",
        f"--cpus={config.agent_cpus}",
        "--user=1000:1000",
        "--tmpfs", "/tmp:rw,exec,nosuid,size=512m",
        "--tmpfs", "/home/agent:rw,exec,nosuid,size=256m",
        # pnpm store location. The named volume df-pnpm-store is mounted at /pnpm-store
        # (pre-initialized with correct ownership in Dockerfile.base). npm_config_store_dir
        # is the env var pnpm reads (matches the store-dir .npmrc key); PNPM_STORE_DIR is
        # not a recognized pnpm variable and causes pnpm to fall back to a project-local
        # .pnpm-store directory.
        "-e", "npm_config_store_dir=/pnpm-store",
        # Disable watch/interactive mode in vitest, jest, and other tools that check CI.
        "-e", "CI=true",
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
    if not project_rw and barrier.write_dirs:
        args.extend(build_rw_overlays(project_dir, barrier.write_dirs))
    args.extend(build_tmpfs_masks(project_dir, barrier.masked_dirs))
    args.extend(build_cache_volumes(role.value))
    args.extend(build_platform_isolation_overlays(config.language, project_dir, project_rw=project_rw))

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

    resolved_model = config.model_ids.get(role.value, config.model_id)
    if resolved_model:
        args.append(f"--model={resolved_model}")

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
