"""Docker CLI argument builder — role-declarative configuration.

Replaces the massive if/elif chain in docker.sh's run_agent.
Each AgentRole gets a declarative RoleDockerConfig. The arg builder
reads the config and calls mounts.py functions.
"""

from __future__ import annotations

import logging

from darkfactory.docker.barriers import BarrierConfig, live_barrier_for
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


def build_proxy_env_args(proxy_url: str, config: PipelineConfig) -> list[str]:
    """Build ``-e`` args for HTTP(S)_PROXY and NO_PROXY.

    Exempts ``localhost``, ``127.0.0.1``, and all configured service
    container hostnames (e.g., ``postgres``, ``redis``) so non-HTTP
    protocols aren't routed through tinyproxy.
    """
    no_proxy = "localhost,127.0.0.1"
    if config.services:
        no_proxy += "," + ",".join(svc.name for svc in config.services)
    return [
        "-e", f"HTTP_PROXY={proxy_url}",
        "-e", f"HTTPS_PROXY={proxy_url}",
        "-e", f"http_proxy={proxy_url}",
        "-e", f"https_proxy={proxy_url}",
        "-e", f"NO_PROXY={no_proxy}",
        "-e", f"no_proxy={no_proxy}",
    ]


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
        # uid/gid=1000 so the python user-site install dir (PYTHONUSERBASE=/home/agent/.local
        # — Sprint 23) is owned by the gate user, matching the other tmpfs overlays in
        # mounts.py (which all pin uid=1000,gid=1000). NB: this only covers tmpfs-resident
        # paths; it does NOT make the root-owned named cache volumes (.cargo/.cache/pip/go in
        # mounts.py) uid-1000-writable — that needs a df-pnpm-store-style pre-own (deferred).
        "--tmpfs", "/home/agent:rw,exec,nosuid,size=256m,uid=1000,gid=1000",
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
    # Phase 4.5.3: rebuild barrier from live config — see live_barrier_for
    # docstring. The cached `barriers` parameter is intentionally ignored
    # for masked_dirs; it goes stale on architect source_dirs amendments.
    del barriers  # signal: deliberately unused, do not re-introduce.
    barrier = live_barrier_for(role, config)

    # Determine if project mount is rw.
    project_rw = role in (AgentRole.IMPLEMENTER,)

    args = build_base_args(config, run_id)

    # The Implementer is the only agent that adds dependencies — it must be
    # able to run `pnpm install` / `npm install` / `yarn install` to update
    # the lockfile after editing package.json.  CI=true (set in base args)
    # causes pnpm to enforce --frozen-lockfile automatically, npm to behave
    # like `npm ci`, and yarn to error on lockfile mismatch.  Override these
    # for the Implementer so it can resolve new deps; the quality gate still
    # enforces frozen-lockfile as the correctness check.
    if role == AgentRole.IMPLEMENTER:
        args.extend([
            "-e", "npm_config_frozen_lockfile=false",  # pnpm
            "-e", "npm_config_install_strategy=hoisted",  # npm: don't use `ci` semantics
            "-e", "YARN_ENABLE_IMMUTABLE_INSTALLS=false",  # yarn berry (v2+)
        ])

    # Set working directory to project root so Claude Code operates in the
    # project context (the Dockerfile WORKDIR is /workspace, not /project).
    args.extend(["--workdir", "/project"])
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
    max_turns_override: int = 0,
    agent_def_text: str = "",
    settings_json: str = "",
) -> list[str]:
    """Build Claude CLI arguments for an agent invocation.

    The prompt is piped to stdin by the caller (not included in args).

    Returns args like: claude -p --dangerously-skip-permissions --max-turns N
    --disallowedTools ... --append-system-prompt ... --output-format json

    ``agent_def_text`` (A1-esc): agent definition content to prepend to the
    barrier prompt via --append-system-prompt. Used for roles whose
    behavioral rules were previously left to a prompt-level "read this file"
    instruction (implementer, reviewer, oracle).

    ``settings_json`` (Phase 5.6 B4): pass-through JSON string for Claude
    Code's ``--settings`` flag. Used to set ``temperature=0`` on the
    defect-classifier so the classification is as deterministic as the
    infrastructure permits. Unknown keys are silently ignored by the CLI,
    so this is best-effort — the CLASSIFIER_IO_HASH event is what measures
    whether determinism was achieved.
    """
    barrier = barriers[role]
    max_turns = max_turns_override if max_turns_override > 0 else config.agent_max_turns
    args = [
        "claude", "-p",
        "--dangerously-skip-permissions",
        f"--max-turns={max_turns}",
        "--output-format=json",
    ]

    resolved_model = config.model_ids.get(role.value, config.model_id)
    if resolved_model:
        args.append(f"--model={resolved_model}")

    # Denied tools (defense-in-depth).
    if barrier.denied_tools:
        for tool in barrier.denied_tools:
            args.extend(["--disallowedTools", tool])

    # A1-esc: agent definition goes first so behavioral rules sit at the
    # top of the system-prompt append (highest-salience position per
    # Chroma Context Rot research). Barrier prompt follows as
    # defense-in-depth.
    system_prompt_parts: list[str] = []
    if agent_def_text:
        system_prompt_parts.append(agent_def_text)
    if barrier.barrier_prompt:
        system_prompt_parts.append(barrier.barrier_prompt)
    if system_prompt_parts:
        args.extend(["--append-system-prompt", "\n\n".join(system_prompt_parts)])

    if output_file:
        args.extend(["--output", output_file])

    if settings_json:
        args.extend(["--settings", settings_json])

    return args
