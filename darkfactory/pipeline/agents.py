"""Agent invocation — run agents in Docker containers with retry.

Replaces invoke_agent / run_agent from docker.sh.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from darkfactory.audit.checker import check_output_for_barriers
from darkfactory.docker.args import build_agent_args, build_claude_args, build_proxy_env_args
from darkfactory.docker.barriers import BarrierConfig
from darkfactory.docker.containers import check_language_image, execute_container
from darkfactory.docker.mounts import check_hardlink_safety, check_symlink_safety
from darkfactory.docker.networks import network_name, proxy_name
from darkfactory.errors import TransientFailure
from darkfactory.events.types import emit_agent_completed, emit_agent_failed, emit_agent_start
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT

log = logging.getLogger(__name__)

# Minimum output length — below this, synthetic failure.
_MIN_OUTPUT_LEN = 20

# Failure dump truncation limits.
_DUMP_STDOUT_HEAD = 25_000
_DUMP_STDOUT_TAIL = 25_000
_DUMP_STDERR_LIMIT = 10_000

# Roles that need execution network (not analysis).
_EXECUTION_ROLES = {AgentRole.IMPLEMENTER}

# Patterns that indicate transient infrastructure failures.
_TRANSIENT_PATTERNS = [
    re.compile(r"(?i)out of memory|oom|cannot allocate memory"),
    re.compile(r"(?i)connection refused|connection reset|connection timed out"),
    re.compile(r"(?i)network (?:is )?unreachable|name resolution failed|dns"),
    re.compile(r"(?i)docker daemon|daemon not running|cannot connect to.*docker"),
    re.compile(r"(?i)temporary failure|service unavailable|502 bad gateway|503|504"),
    re.compile(r"(?i)rate limit|too many requests|429"),
    re.compile(r"(?i)(?:connection |request |context |rpc )?(?:timed out|deadline exceeded)"),
]

# Exit codes that signal OOM kill.
_OOM_EXIT_CODES = {137}


def _is_transient(exit_code: int, stderr: str) -> bool:
    """Classify whether a failure is transient (infrastructure) vs implementation."""
    if exit_code in _OOM_EXIT_CODES:
        return True
    for pattern in _TRANSIENT_PATTERNS:
        if pattern.search(stderr):
            return True
    return False



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
    model_override: str = "",
    max_turns_override: int = 0,
    phase: str = "",
    attempt_offset: int = 0,
) -> AgentResult:
    """Invoke an agent with retry logic.

    Runs the agent in a Docker container, retries on failure up to
    config.agent_retries times. Transient infrastructure failures get
    separate retry with exponential backoff. Emits events and checks
    for anomalies.

    Returns AgentResult with exit code, output, and metadata.
    """
    # Pre-flight image check — before filesystem safety work to catch missing
    # language images (including per-spec overrides) with a clear error message.
    check_language_image(config.language, spec_name=spec_name)

    # Phase 4.5.3: rebuild barrier from live config so source_dirs
    # amendments by the architect mid-run cannot leave the symlink
    # safety check or audit-log scan operating on stale paths.
    from darkfactory.docker.barriers import live_barrier_for
    barrier = live_barrier_for(role, config)
    image = f"dark-factory-{config.language}"
    max_attempts = config.agent_retries + 1

    # Resolve model: explicit override > per-role > global.
    resolved_model = model_override or config.model_ids.get(role.value, config.model_id)

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

    # Proxy env vars — route Claude Code API calls through tinyproxy on internal networks.
    if config.egress_filtering:
        net_type = "execution" if role in _EXECUTION_ROLES else "analysis"
        proxy_url = f"http://{proxy_name(run_id, net_type)}:8080"
        docker_args.extend(build_proxy_env_args(proxy_url, config))

    claude_args = build_claude_args(role, config, barriers, max_turns_override=max_turns_override)

    # Apply model override to claude_args if needed.
    if model_override:
        claude_args = [
            f"--model={model_override}" if a.startswith("--model=") else a
            for a in claude_args
        ]

    last_result = AgentResult(role=role, exit_code=1)
    attempt = 0
    transient_retries = 0
    stdout = ""
    stderr = ""

    while attempt < max_attempts:
        attempt += 1

        if events_file:
            # Phase 1.2: attempt_offset reflects outer retry loops (e.g.,
            # pre-gate / smoke retries that re-launch the implementer). Without
            # the offset, every fresh invoke_agent call logs attempt:1 even on
            # the second/third implementer run for the same iteration.
            emit_agent_start(
                events_file, spec=spec_name, run_id=run_id,
                iteration=iteration, role=role.value,
                attempt=attempt + attempt_offset,
                phase=phase,
            )

        start = time.monotonic()

        # Execute container — prompt is piped via stdin.
        container_result = execute_container(
            docker_args, image, claude_args,
            timeout=config.agent_timeout,
            dry_run=dry_run,
            audit_path=str(audit_path) if audit_path else "",
            input_text=prompt,
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
                    model_id=resolved_model,
                    est_input_tokens=config.agent_max_turns * config.cost_est_input_tokens_per_turn,
                    est_output_tokens=config.agent_max_turns * config.cost_est_output_tokens_per_turn,
                    wall_seconds=wall_s,
                    phase=phase,
                )

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
            # Check if this is a transient infrastructure failure.
            if _is_transient(exit_code, stderr) and transient_retries < config.transient_retry_max:
                from darkfactory.utils.backoff import transient_backoff
                delay = transient_backoff(transient_retries, config.transient_retry_base_s)
                transient_retries += 1
                log.warning(
                    "agent %s transient failure (attempt %d, transient retry %d/%d), "
                    "backing off %.1fs",
                    role.value, attempt, transient_retries,
                    config.transient_retry_max, delay,
                )
                time.sleep(delay)
                # Transient retries do NOT consume agent_retries budget.
                attempt -= 1
                continue

            if events_file:
                reason = "timeout" if timed_out else f"exit {exit_code}"
                emit_agent_failed(
                    events_file, spec=spec_name, run_id=run_id,
                    iteration=iteration, role=role.value,
                    exit_code=exit_code, reason=reason,
                    model_id=resolved_model,
                    est_input_tokens=config.agent_max_turns * config.cost_est_input_tokens_per_turn,
                    est_output_tokens=config.agent_max_turns * config.cost_est_output_tokens_per_turn,
                    wall_seconds=wall_s,
                    phase=phase,
                )

            # Raise TransientFailure if all transient retries exhausted on a
            # transient error — allows plan-level retry.
            if _is_transient(exit_code, stderr) and transient_retries >= config.transient_retry_max:
                last_result = result
                if not dry_run:
                    _write_failure_dump(
                        project_dir, role, spec_name, run_id, iteration,
                        exit_code, timed_out, wall_s, attempt,
                        claude_args, stdout, stderr,
                    )
                # Raise here — the post-loop dump check is never reached on this path.
                raise TransientFailure(
                    f"agent {role.value} transient failure after "
                    f"{transient_retries} retries (exit {exit_code})"
                )

            if attempt < max_attempts:
                log.warning("agent %s failed (attempt %d/%d), retrying", role.value, attempt, max_attempts)
            else:
                log.warning("agent %s failed after %d attempts", role.value, max_attempts)

            last_result = result

    if not dry_run and last_result.exit_code != 0:
        _write_failure_dump(
            project_dir, role, spec_name, run_id, iteration,
            last_result.exit_code, last_result.timed_out,
            last_result.wall_seconds, attempt,
            claude_args, stdout, stderr,
        )

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


def _write_failure_dump(
    project_dir: Path,
    role: AgentRole,
    spec_name: str,
    run_id: str,
    iteration: int,
    exit_code: int,
    timed_out: bool,
    wall_seconds: float,
    attempts: int,
    claude_args: list[str],
    stdout: str,
    stderr: str,
) -> None:
    """Write a structured failure dump for post-mortem debugging.

    Writes to .dark-factory/failures/. Silently skips on any error
    (dump failure must never mask the original failure).
    """
    try:
        failures_dir = project_dir / ".dark-factory" / "failures"
        failures_dir.mkdir(parents=True, exist_ok=True)

        now = datetime.now(timezone.utc)
        ts = now.strftime("%Y%m%dT%H%M%SZ")
        filename = f"failure_{role.value}_{spec_name}_{run_id[:8]}_{ts}.json"

        truncated = len(stdout) > _DUMP_STDOUT_HEAD + _DUMP_STDOUT_TAIL
        if truncated:
            stdout_field = (
                stdout[:_DUMP_STDOUT_HEAD]
                + f"\n\n[... {len(stdout) - _DUMP_STDOUT_HEAD - _DUMP_STDOUT_TAIL} chars omitted ...]\n\n"
                + stdout[-_DUMP_STDOUT_TAIL:]
            )
        else:
            stdout_field = stdout

        record = {
            "agent_role": role.value,
            "spec_name": spec_name,
            "run_id": run_id,
            "iteration": iteration,
            "exit_code": exit_code,
            "timed_out": timed_out,
            "wall_seconds": round(wall_seconds, 2),
            "attempts": attempts,
            "timestamp": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "claude_args": claude_args,
            "stdout_truncated": truncated,
            "stdout": stdout_field,
            "stderr": stderr[-_DUMP_STDERR_LIMIT:] if len(stderr) > _DUMP_STDERR_LIMIT else stderr,
        }

        dump_path = failures_dir / filename
        dump_path.write_text(json.dumps(record, indent=2))
        log.debug("failure dump written: %s", dump_path)

    except Exception as exc:
        log.debug("failed to write failure dump: %s", exc)


def extract_agent_text(raw_output: str) -> str:
    """Extract human-readable text from Claude CLI ``--output-format=json`` output.

    The JSON wrapper has the form ``{"result": "<text>", "session_id": "...", ...}``.
    Returns the ``result`` field when present, otherwise falls back to raw output.
    """
    if not raw_output or not raw_output.lstrip().startswith("{"):
        return raw_output
    try:
        data = json.loads(raw_output)
    except (json.JSONDecodeError, TypeError):
        return raw_output
    if isinstance(data, dict) and "result" in data and isinstance(data["result"], str):
        return data["result"]
    return raw_output
