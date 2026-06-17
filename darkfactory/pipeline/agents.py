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
from darkfactory.docker.containers import (
    check_language_image,
    execute_container,
    execute_in_container,
    remove_container,
    start_container,
    stop_container,
)
from darkfactory.docker.mounts import check_hardlink_safety, check_symlink_safety
from darkfactory.docker.networks import network_name, proxy_name
from darkfactory.errors import TransientFailure
from darkfactory.events.types import (
    emit_agent_completed,
    emit_agent_def_injected,
    emit_agent_failed,
    emit_agent_start,
    emit_implementer_temp_zero,
    emit_prompt_word_count,
)
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT

log = logging.getLogger(__name__)

# Minimum output length — below this, synthetic failure.
_MIN_OUTPUT_LEN = 20

# Per-response output-token ceilings by model family. Claude Code defaults
# CLAUDE_CODE_MAX_OUTPUT_TOKENS to 32000 when unset; a single agent turn that
# needs more (the test-engineer emitting many scenario files at once) trips
# Claude Code's "exceeded the 32000 output token maximum" error, after which
# `claude -p` retries internally until the agent_timeout SIGKILL — a ~15-minute
# hang that surfaces as a bare exit-124 timeout (bb spec-3 api-bootstrap; the
# error text is preserved in the S33 stream dump). We raise the cap to each
# model's TRUE per-response max so the error never fires for in-headroom turns.
# Keyed by family substring because model ids carry version/date suffixes,
# mirroring intelligence.cost.MODEL_RATES. We never set the cap ABOVE a model's
# real max (derivation is per-family), so this does not rely on Claude Code
# clamping.
_MODEL_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "opus": 128000,
    "fable": 128000,
    "sonnet": 64000,
    "haiku": 64000,
}
# Conservative floor for an unrecognized model — still 2x the unset default.
_DEFAULT_MAX_OUTPUT_TOKENS = 64000

# Claude Code's terminal error when a single `-p` turn's output exceeds
# CLAUDE_CODE_MAX_OUTPUT_TOKENS. The observed (S33 dump) signal is the prose
# "API Error: Claude's response exceeded the <N> output token maximum ..." in
# the final assistant text; a future CLI may also surface an ``"error":
# "max_output_tokens"`` field, so we match either. The prose alternative is
# anchored to the ``API Error:`` envelope ON THE SAME LINE (not a free-floating
# substring) so a scenario file the TE writes that merely quotes the phrase
# cannot trip it. Detecting this lets us classify a cap-overflow as
# NON-RETRYABLE (re-running the same prompt only burns another full
# agent_timeout) and emit an actionable diagnostic instead of a mystery
# timeout. See invoke_agent's cap-overflow short-circuit.
_MAX_OUTPUT_TOKENS_ERROR_RE = re.compile(
    r'"error"\s*:\s*"max_output_tokens"'
    # ``[\d,]+`` tolerates a comma-grouped count ("32,000") if the CLI ever
    # reformats the number — a false negative here reopens the 900s hang, so we
    # fail toward matching (Codex A1-a).
    r'|API Error:[^\n]*exceeded the [\d,]+ output token maximum',
)


def resolve_max_output_tokens(model_id: str, override: int = 0) -> int:
    """Resolve the CLAUDE_CODE_MAX_OUTPUT_TOKENS value for an agent container.

    ``override`` (``config.max_output_tokens``) wins when positive; otherwise
    derive from the model family. An unrecognized model gets a conservative
    floor (still 2x Claude Code's unset default of 32000).
    """
    if override and override > 0:
        return override
    mid = (model_id or "").lower()
    for family, cap in _MODEL_MAX_OUTPUT_TOKENS.items():
        if family in mid:
            return cap
    return _DEFAULT_MAX_OUTPUT_TOKENS


def _is_max_output_tokens_overflow(raw_output: str) -> bool:
    """True when the agent stream ended on a CLAUDE_CODE_MAX_OUTPUT_TOKENS error."""
    return bool(raw_output) and bool(_MAX_OUTPUT_TOKENS_ERROR_RE.search(raw_output))

# Failure dump truncation limits.
_DUMP_STDOUT_HEAD = 25_000
_DUMP_STDOUT_TAIL = 25_000
_DUMP_STDERR_LIMIT = 10_000

# Roles that need execution network (not analysis).
_EXECUTION_ROLES = {AgentRole.IMPLEMENTER}

# A1-esc: Roles whose agent definition is mechanically injected via
# --append-system-prompt. Previously these agents were told to "read your
# agent definition at /project/agents/<role>.md" in their task prompt, but
# Phase 3+4 data showed compliance could not be verified. Mechanical
# injection guarantees the behavioral rules land in the system-prompt
# position. Architect and test-engineer are NOT in this set — they already
# get their definitions via a working prompt-level instruction AND their
# task prompts are structured differently; escalating would duplicate
# content. Re-evaluate in Phase 7 if M5 data shows compliance gaps.
_AGENT_DEF_INJECT_ROLES = {
    AgentRole.IMPLEMENTER,
    AgentRole.REVIEWER,
    AgentRole.ORACLE,
}

# Roles invoked with ``temperature=0`` (via Claude Code's ``--settings``
# flag). Two independent rationales gate membership:
#
#   (a) MEASUREMENT-INSTRUMENT INTEGRITY — the role's output is consumed
#       downstream as a stable signal, and run-to-run drift destroys
#       that signal. ``DEFECT_CLASSIFIER`` (Phase 5.6 B4) is the
#       canonical case: reclassifying the same 20 failing tests
#       differently across iterations destroys the Phase 6 variance
#       decomposition.
#
#   (b) SCORE-VARIANCE COMPRESSION — the role's output is a sum of
#       LLM judgments where per-item variance compounds, but the
#       verdict (the load-bearing output) is empirically stable.
#       ``SPEC_VALIDATOR`` (Phase 3, 2026-04-29): score swung
#       75/87/92/95/95/98 across six observed database-foundation
#       runs while verdict was PASS in 6/6. Pin compresses score
#       noise without (per current evidence) flipping the verdict.
#
#       **Watch-item / falsifier**: if a future run shows a Precision
#       Analysis divergence-rating flip on a previously-stable spec,
#       revisit this pin — temp=0 may be collapsing the three
#       implementation sketches toward a modal interpretation, which
#       would systematically under-rate divergence and let HIGH
#       specs slip through as MEDIUM. The falsifier is verdict
#       instability, not score change.
#
# Other roles remain at Claude Code's default (agentic tool use
# benefits from non-zero temperature). The CLI silently ignores
# unknown settings keys, so if the runtime does not honor
# ``temperature`` the role behaves as before. For the classifier,
# residual drift shows up in the CLASSIFIER_IO_HASH event — that
# safety net is asymmetric: the spec_validator pin has NO analogous
# instrumentation event, so a runtime that silently ignores
# ``temperature`` for that role would not surface in telemetry.
# The score-variance evidence is multi-run; one bad single-run drift
# would still wash out across observations, but if you're debugging
# why a Phase 5b discriminating run shows unexpected score swing,
# rule-out the silent-ignore case before reasoning about the pin.
_TEMPERATURE_ZERO_ROLES = {
    AgentRole.DEFECT_CLASSIFIER,  # rationale (a)
    AgentRole.SPEC_VALIDATOR,     # rationale (b)
}

# Priority 4 (2026-04-20 plan revision): Implementer uses default
# temperature on iter 1 (exploration of the solution space is useful)
# and ``temperature=0`` on iter >= 2 (exploitation against specific
# feedback). Reduces one orthogonal variance source in Priority 1's
# validation runs and directly addresses cases where iter-2 makes a
# silly new mistake that iter-1 did not.
_IMPLEMENTER_TEMP_ZERO_FROM_ITERATION = 2


def should_force_temperature_zero(role: AgentRole, iteration: int) -> bool:
    """Returns True when the invocation should run with temperature=0.

    Composes three independent gating rules:
      (1) measurement-instrument roles — defect_classifier
          (Phase 5.6 B4): reclassification drift across iterations
          destroys the Phase 6 variance decomposition.
      (2) score-variance-compression roles — spec_validator (Phase 3):
          per-rubric-item LLM judgment variance compounds in the
          summed score; pin compresses the band (75-98 across six
          observed runs collapses toward a stable point) while the
          verdict (PASS-stable in those same runs) is unaffected.
      (3) Priority 4 — implementer on iter >= 2 (exploitation against
          specific feedback).
    """
    if role in _TEMPERATURE_ZERO_ROLES:
        return True
    if role == AgentRole.IMPLEMENTER and iteration >= _IMPLEMENTER_TEMP_ZERO_FROM_ITERATION:
        return True
    return False

# Roles whose BarrierConfig.masked_dirs includes source_dirs — i.e. the
# only roles where a source-leak post-scan is meaningful. Keep in sync
# with darkfactory/docker/barriers.py (sa_masked, evaluator_masked).
_ANALYSIS_ROLES_WITH_SOURCE_BARRIER = {
    AgentRole.SCENARIO_ARCHITECT,
    AgentRole.TEST_ENGINEER,
    AgentRole.QA_HOLDOUT,
    AgentRole.EVALUATOR,
}

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


def _read_agent_definition(role: AgentRole) -> str:
    """A1-esc: Read the agent definition file for --append-system-prompt injection.

    Reads from the framework package's ``agents/`` dir (canonical source)
    rather than the mounted project's ``.dark-factory/agents/`` — the
    latter may be stale if the user forgot ``dark-factory init --update``
    after upgrading. Returns the file content or empty string if the file
    cannot be read (the caller treats empty as "no injection").
    """
    import darkfactory
    framework_dir = Path(darkfactory.__file__).parent.parent
    def_path = framework_dir / "agents" / f"{role.value}.md"
    try:
        return def_path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning(
            "A1-esc: failed to read agent definition %s: %s",
            def_path, exc,
        )
        return ""


def _collect_barrier_excluded_text(
    project_dir: Path,
    spec_name: str,
    role: AgentRole,
    prompt: str,
) -> str:
    """Assemble the text the agent was legitimately GIVEN, for the barrier audit.

    A masked source dir that appears in this text is not a barrier read — the dir
    is tmpfs-invisible in the container, so a path string the agent emits came
    from its own inputs. ``check_output_for_barriers`` subtracts these.

    Sources (best-effort; a missing file is skipped, never fatal):
    - the task ``prompt``;
    - the spec body and the architect manifest (the manifest's ``specifier`` is
      the path the Test Engineer is told to copy verbatim into an import — the
      load-bearing case, Neo Finding 3);
    - the agent definition. Read BOTH the project-mounted copy
      (``.dark-factory/agents/<role>.md`` — what mounted-read roles like the Test
      Engineer actually read, even if stale) AND the framework copy (injected via
      ``--append-system-prompt`` for implementer/reviewer/oracle).
    """
    parts: list[str] = [prompt or ""]
    df = project_dir / ".dark-factory"
    candidates = []
    if spec_name:
        candidates.append(df / "specs" / f"{spec_name}.md")
        candidates.append(df / "architecture" / f"{spec_name}-manifest.yaml")
    candidates.append(df / "agents" / f"{role.value}.md")
    for path in candidates:
        try:
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            pass
    parts.append(_read_agent_definition(role))  # framework copy (injected roles)
    return "\n".join(parts)


def _is_transient(exit_code: int, stderr: str) -> bool:
    """Classify whether a failure is transient (infrastructure) vs implementation."""
    if exit_code in _OOM_EXIT_CODES:
        return True
    for pattern in _TRANSIENT_PATTERNS:
        if pattern.search(stderr):
            return True
    return False


def _extract_result_envelope(raw_output: str) -> str:
    """Reconstruct the single `type:result` JSON envelope from a stream-json
    agent run (Sprint 33), so every downstream consumer + the barrier audit see
    the SAME shape as the legacy ``--output-format=json`` output.

    ``claude -p --output-format=stream-json --verbose`` emits one JSON object per
    line (system init, assistant turns, tool_use/tool_result, then a final
    ``{"type":"result", "result":..., "usage":...}`` line that is byte-identical
    in schema to what ``--output-format=json`` produced). This returns that final
    result line. Returns "" when there is no result line — i.e. the agent was
    SIGKILL'd mid-stream before completing (e.g. agent_timeout) — exactly the
    legacy "empty stdout on timeout" behavior, while the RAW stream is preserved
    separately for the failure dump.

    Robust to the legacy format: if ``raw_output`` is already a single JSON
    object (old ``--output-format=json``), it is returned as-is.
    """
    if not raw_output or not raw_output.strip():
        return ""
    # Primary: scan lines for the LAST {"type":"result", ...} envelope. This
    # handles stream-json AND a compact single-line legacy --output-format=json
    # output (which is itself a type:"result" object on one line).
    envelope = ""
    for line in raw_output.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict) and obj.get("type") == "result":
            envelope = line
    if envelope:
        return envelope
    # Fallback for a PRETTY-PRINTED legacy json envelope (multi-line, so the
    # line scan can't parse it). Accept ONLY a result-shaped object — never a
    # bare stream event (e.g. a lone ``{"type":"system","subtype":"init"}`` left
    # by a SIGKILL after one line, which is NOT a result and must yield "").
    stripped = raw_output.strip()
    try:
        obj = json.loads(stripped)
        if isinstance(obj, dict) and (
            obj.get("type") == "result" or "result" in obj or "usage" in obj
        ):
            return stripped
    except (json.JSONDecodeError, TypeError):
        pass
    return ""


def _extract_usage(stdout: str) -> dict:
    """Parse real token usage from `claude -p` result-envelope stdout.

    Returns a dict with input_tokens, output_tokens, cache_read_input_tokens,
    cache_creation_input_tokens. Missing keys default to 0. Returns all zeros
    on parse failure — telemetry is best-effort, never raises.
    """
    try:
        data = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {"input_tokens": 0, "output_tokens": 0,
                "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    usage = data.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    return {
        "input_tokens": int(usage.get("input_tokens", 0) or 0),
        "output_tokens": int(usage.get("output_tokens", 0) or 0),
        "cache_read_input_tokens": int(usage.get("cache_read_input_tokens", 0) or 0),
        "cache_creation_input_tokens": int(usage.get("cache_creation_input_tokens", 0) or 0),
    }


def _emit_telemetry(
    project_dir: Path,
    *,
    run_id: str,
    spec_name: str,
    role: str,
    iteration: int,
    phase: str,
    wall_clock_ms: int,
    usage: dict,
    exit_code: int,
    model_id: str,
) -> None:
    """Phase 4.6b.1c task v: append one telemetry line per agent invocation.

    Writes to ``.dark-factory/reports/run-telemetry.jsonl`` as append-only
    JSONL. Schema is pinned via ``schema_version`` so downstream analysis
    code cannot silently drift when fields are added. Best-effort — all
    write errors are swallowed to avoid masking the agent's own result.

    Schema v1 fields: schema_version, timestamp, run_id, spec_name, role,
    iteration_index, phase, exit_code, model_id, wall_clock_ms,
    input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens.
    """
    try:
        reports_dir = project_dir / ".dark-factory" / "reports"
        reports_dir.mkdir(parents=True, exist_ok=True)
        line = {
            "schema_version": 1,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "run_id": run_id,
            "spec_name": spec_name,
            "role": role,
            "iteration_index": iteration,
            "phase": phase,
            "exit_code": exit_code,
            "model_id": model_id,
            "wall_clock_ms": wall_clock_ms,
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "cache_read_tokens": usage["cache_read_input_tokens"],
            "cache_creation_tokens": usage["cache_creation_input_tokens"],
        }
        with (reports_dir / "run-telemetry.jsonl").open("a") as f:
            f.write(json.dumps(line) + "\n")
    except Exception as exc:  # noqa: BLE001 — telemetry is best-effort.
        log.debug("telemetry emit failed (non-fatal): %s", exc)



def _build_full_agent_docker_args(
    role: AgentRole,
    *,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    run_id: str,
    project_dir: Path,
    extra_docker_args: list[str] | None = None,
    model_override: str = "",
) -> list[str]:
    """Build the complete docker_args for a container running this agent role.

    Includes: mounts, barriers, network, auth env vars, the output-token cap,
    proxy env vars, and any caller-supplied extra args. Used by both
    ``invoke_agent`` (one-shot run) and ``start_agent_session`` (persistent
    container for S1 pre-gate retry session continuation) so the two paths stay
    in lockstep. ``model_override`` mirrors ``invoke_agent``'s per-call model
    override so the output-token cap is derived from the model that will
    actually run.
    """
    if role in _EXECUTION_ROLES:
        network = network_name(run_id, "execution")
    else:
        network = network_name(run_id, "analysis")

    docker_args = build_agent_args(
        role, config, barriers, run_id, str(project_dir),
        network=network,
    )

    token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if token:
        docker_args.extend(["-e", f"CLAUDE_CODE_OAUTH_TOKEN={token}"])
    elif api_key:
        docker_args.extend(["-e", f"ANTHROPIC_API_KEY={api_key}"])

    # Raise the per-response output-token cap above Claude Code's 32000 unset
    # default so a heavy single agent turn (e.g. the test-engineer) does not hit
    # the cap and hang-retry to the agent_timeout SIGKILL. Derived per-role from
    # the model that will actually run (same resolution as build_agent_args).
    resolved_model = model_override or config.model_ids.get(role.value, config.model_id)
    max_out = resolve_max_output_tokens(resolved_model, config.max_output_tokens)
    docker_args.extend(["-e", f"CLAUDE_CODE_MAX_OUTPUT_TOKENS={max_out}"])

    if config.egress_filtering:
        net_type = "execution" if role in _EXECUTION_ROLES else "analysis"
        proxy_url = f"http://{proxy_name(run_id, net_type)}:8080"
        docker_args.extend(build_proxy_env_args(proxy_url, config))

    if extra_docker_args:
        docker_args.extend(extra_docker_args)

    return docker_args


def start_agent_session(
    role: AgentRole,
    *,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    run_id: str,
    project_dir: Path,
    spec_name: str = "",
    audit_path: Path | None = None,
    dry_run: bool = False,
    extra_docker_args: list[str] | None = None,
    model_override: str = "",
) -> str:
    """S1: Start a persistent agent container for session continuation.

    The container runs ``sh -c 'tail -f /dev/null'`` (entrypoint overridden)
    so it stays alive. Subsequent invocations use ``docker exec`` to run
    ``claude --continue -p`` inside the same container, preserving Claude
    session state across pre-gate retries and eliminating the cold-start
    tax per retry.

    Returns the container ID. The caller MUST eventually call
    ``stop_agent_session`` to clean up.

    Used by the implementer's pre-gate retry loop and (F Move 2c) the
    test-engineer's scenario-gen empty/misnamed-retry loop — never
    cross-iteration. The builder is role-agnostic (only the network/proxy is
    role-keyed, via ``_EXECUTION_ROLES``), so a non-execution role like the TE
    correctly gets the analysis network + its ``:ro`` project mount with the
    ``scenarios/`` write-overlay; reusing the container opens no new barrier
    surface (masks/overlays are fixed at ``docker run`` time).

    ``model_override`` MUST match the override the caller will later pass to
    ``invoke_agent`` for this same session: the output-token cap env var is
    baked at ``docker run`` time and cannot be changed per ``docker exec``, so
    if a SWITCH_MODEL recovery switched the implementer to a different family,
    the session must be started with that override or the cap will track the
    configured model instead of the running one.
    """
    check_language_image(config.language, spec_name=spec_name)
    from darkfactory.docker.barriers import live_barrier_for
    barrier = live_barrier_for(role, config)
    _run_safety_checks(role, barrier, project_dir, config)

    docker_args = _build_full_agent_docker_args(
        role,
        config=config, barriers=barriers, run_id=run_id,
        project_dir=project_dir,
        extra_docker_args=extra_docker_args,
        model_override=model_override,
    )
    # Override image ENTRYPOINT (``claude -p --dangerously-skip-permissions``)
    # so we can run a keep-alive process instead. Subsequent ``docker exec``
    # calls bypass ENTRYPOINT anyway, so this only affects the keep-alive.
    docker_args.extend(["--entrypoint", ""])

    image = f"dark-factory-{config.language}"
    cmd = ["sh", "-c", "tail -f /dev/null"]
    container_id = start_container(
        docker_args, image, cmd,
        dry_run=dry_run,
        audit_path=str(audit_path) if audit_path else "",
    )
    log.debug("S1: started session container %s for %s", container_id[:12], role.value)
    return container_id


def stop_agent_session(container_id: str, *, dry_run: bool = False) -> None:
    """S1: Stop and remove a session container started by ``start_agent_session``."""
    if not container_id:
        return
    try:
        stop_container(container_id, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 — cleanup must never raise.
        log.warning("S1: stop_container failed for %s: %s", container_id[:12], exc)
    try:
        remove_container(container_id, dry_run=dry_run)
    except Exception as exc:  # noqa: BLE001 — cleanup must never raise.
        log.warning("S1: remove_container failed for %s: %s", container_id[:12], exc)


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
    extra_docker_args: list[str] | None = None,
    session_container_id: str = "",
    use_continue: bool = False,
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
    # S1: when reusing a session container, the image check and setup have
    # already happened at start_agent_session time; skip the per-invocation
    # check so we don't re-validate the same image.
    if not session_container_id:
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

    # Build Docker args only on the non-session path. When session_container_id
    # is set, mounts/env/network were already baked in at start_agent_session
    # time and we'll invoke claude via ``docker exec`` below.
    if session_container_id:
        docker_args: list[str] = []
    else:
        _run_safety_checks(role, barrier, project_dir, config)
        docker_args = _build_full_agent_docker_args(
            role,
            config=config, barriers=barriers, run_id=run_id,
            project_dir=project_dir,
            extra_docker_args=extra_docker_args,
            model_override=model_override,
        )

    # A1-esc: For implementer/reviewer/oracle, inject agent definition via
    # --append-system-prompt so behavioral rules sit at the highest-salience
    # system-prompt position. Was previously delegated to an unverifiable
    # "read your agent definition at /project/agents/<role>.md" instruction
    # in the task prompt.
    agent_def_text = ""
    if role in _AGENT_DEF_INJECT_ROLES:
        agent_def_text = _read_agent_definition(role)
        if agent_def_text and events_file:
            emit_agent_def_injected(
                events_file, spec=spec_name, run_id=run_id,
                iteration=iteration, role=role.value,
                word_count=len(agent_def_text.split()),
            )

    # Phase 5.6 B4: pin defect-classifier temperature to 0 via --settings.
    # Priority 4 (2026-04-20): implementer runs at temp=0 on iter >= 2.
    temp_zero = should_force_temperature_zero(role, iteration)
    settings_json = '{"temperature": 0}' if temp_zero else ""
    if temp_zero and events_file and role == AgentRole.IMPLEMENTER:
        emit_implementer_temp_zero(
            events_file, spec=spec_name, run_id=run_id,
            iteration=iteration,
        )

    claude_args = build_claude_args(
        role, config, barriers,
        max_turns_override=max_turns_override,
        agent_def_text=agent_def_text,
        settings_json=settings_json,
    )

    # Apply model override to claude_args if needed.
    if model_override:
        claude_args = [
            f"--model={model_override}" if a.startswith("--model=") else a
            for a in claude_args
        ]

    # S1: insert ``--continue`` after the ``claude`` arg so this invocation
    # resumes the most recent session inside the persistent container.
    # ``use_continue=True`` is only meaningful when session_container_id
    # points at a running container that already hosted a prior claude
    # invocation (the initial implementer call for this iteration).
    if use_continue:
        claude_args = [claude_args[0], "--continue", *claude_args[1:]]

    # M5: Log prompt word count before execution. The task prompt (piped
    # to stdin) carries the agent definition + spec + context. The system
    # prompt (--append-system-prompt) carries the barrier defense-in-depth
    # text plus (for A1-esc roles) the agent definition. Sum = total words
    # the model processes on first turn.
    if events_file:
        task_words = len(prompt.split())
        sys_words = len(barrier.barrier_prompt.split()) if barrier.barrier_prompt else 0
        sys_words += len(agent_def_text.split()) if agent_def_text else 0
        emit_prompt_word_count(
            events_file, spec=spec_name, run_id=run_id,
            iteration=iteration, role=role.value,
            prompt_word_count=task_words + sys_words,
            task_prompt_words=task_words,
            system_prompt_words=sys_words,
        )
        if task_words + sys_words > 6000:
            log.warning(
                "M5: agent %s prompt exceeds 6,000-word compliance threshold "
                "(%d words: %d task + %d system)",
                role.value, task_words + sys_words, task_words, sys_words,
            )

    last_result = AgentResult(role=role, exit_code=1)
    attempt = 0
    transient_retries = 0
    stdout = ""
    stderr = ""
    raw_output = ""  # Sprint 33: full stream-json transcript, for the failure dump

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

        # Execute container — prompt is piped via stdin. S1: when running
        # inside a pre-started session container, use ``docker exec``; the
        # claude session state persists between successive invocations.
        if session_container_id:
            container_result = execute_in_container(
                session_container_id, claude_args,
                timeout=config.agent_timeout,
                dry_run=dry_run,
                audit_path=str(audit_path) if audit_path else "",
                input_text=prompt,
            )
        else:
            container_result = execute_container(
                docker_args, image, claude_args,
                timeout=config.agent_timeout,
                dry_run=dry_run,
                audit_path=str(audit_path) if audit_path else "",
                input_text=prompt,
            )

        wall_s = time.monotonic() - start
        # Sprint 33: the agent runs stream-json; ``raw_output`` is the full
        # turn-by-turn stream (preserved for the failure dump = observability),
        # ``stdout`` is the reconstructed legacy result envelope so every
        # downstream consumer + the barrier audit are unchanged. On a SIGKILL
        # (timeout) there is no result line → stdout == "" (legacy behavior) but
        # raw_output still holds the partial transcript.
        raw_output = container_result.stdout
        stdout = _extract_result_envelope(raw_output)
        stderr = container_result.stderr
        output = stdout + ("\n" if stdout and not stdout.endswith("\n") and stderr else "") + stderr
        exit_code = container_result.exit_code
        timed_out = exit_code == EXIT_TIMEOUT

        # Minimum output check. Measure the RAW stdout (the stream), not the
        # reconstructed envelope — this is the faithful equivalent of the legacy
        # check (which measured the raw JSON stdout): it detects an exit-0 agent
        # that emitted nothing, while NOT false-flagging a productive run whose
        # final result line is terse or malformed (the envelope can be empty even
        # when the agent did substantial, captured work).
        if exit_code == 0 and len(raw_output.strip()) < min_output:
            log.warning("agent %s output too short (%d bytes)", role.value, len(raw_output.strip()))
            exit_code = 99
        # Sprint 33: a clean exit-0 with a substantial raw stream but NO
        # reconstructed result envelope means the stream lacked a terminal
        # {"type":"result"} line — a malformed / contract-violating run. Surface
        # it as a failure (exit 98) instead of silently handing "" to every
        # downstream consumer. Also the cheap backstop if the CLI's stream-json
        # schema ever changes.
        elif exit_code == 0 and not stdout.strip() and len(raw_output.strip()) >= min_output:
            log.warning(
                "agent %s exit 0 but no result envelope in %d-byte stream "
                "(malformed stream-json?)", role.value, len(raw_output.strip()))
            exit_code = 98

        result = AgentResult(
            role=role,
            exit_code=exit_code,
            output=output,
            timed_out=timed_out,
            wall_seconds=wall_s,
            retries=attempt - 1,
        )

        # Phase 4.6b.1c: parse real token usage from Claude JSON output and
        # emit per-agent telemetry line. Runs on every attempt (success and
        # failure) so cost/wall-clock attribution survives retries.
        _usage = _extract_usage(stdout)
        _emit_telemetry(
            project_dir,
            run_id=run_id, spec_name=spec_name, role=role.value,
            iteration=iteration, phase=phase,
            wall_clock_ms=int(wall_s * 1000),
            usage=_usage, exit_code=exit_code, model_id=resolved_model,
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

            # Barrier audit. Pass the agent's legitimately-given inputs as
            # excluded_text so an echo of a SOURCE path the agent was handed
            # (manifest specifier, spec path, agent-def example) is not flagged
            # as a read — the source dir is tmpfs-masked, so it cannot have been
            # read. Subtraction is scoped to source_dirs only (holdouts/.git
            # always fire). See checker.check_output_for_barriers.
            if audit_path and barrier.masked_dirs and events_file:
                excluded_text = _collect_barrier_excluded_text(
                    project_dir, spec_name, role, prompt,
                )
                violations = check_output_for_barriers(
                    output, barrier.masked_dirs, role.value,
                    audit_path, events_file, spec_name, run_id,
                    excluded_text=excluded_text,
                    subtractable_paths=config.source_dirs,
                )
                if violations:
                    result.barrier_violations = violations

            # Phase 4.5.4: defense-in-depth path-echo scan for
            # analysis-only roles (Scenario Architect, Evaluator).
            # Walks files under the LIVE source_dirs and grep-checks
            # the agent transcript for verbatim path matches, then
            # subtracts any path the agent already saw in the spec
            # text (its legitimate input).
            if events_file and role in _ANALYSIS_ROLES_WITH_SOURCE_BARRIER:
                from darkfactory.audit.source_leak_scan import (
                    scan_agent_output_for_source_leaks,
                )
                from darkfactory.events.types import (
                    emit_barrier_path_echo_detected,
                )
                spec_text = ""
                if spec_name:
                    spec_path = (
                        project_dir / ".dark-factory" / "specs"
                        / f"{spec_name}.md"
                    )
                    try:
                        spec_text = spec_path.read_text(
                            encoding="utf-8", errors="replace",
                        )
                    except OSError:
                        spec_text = ""
                # NOTE: this precise scanner intentionally subtracts spec-text
                # ONLY — do NOT widen it to the manifest/agent-def the way
                # check_output_for_barriers does. This scanner matches REAL
                # on-disk file paths and is the genuine safety net for a source
                # read; subtracting the manifest could let a manifest-quoted real
                # path mask an actual leak. The broader excluded_text belongs to
                # the blunt checker (which the precise scanner backstops), not here.
                leak = scan_agent_output_for_source_leaks(
                    output, project_dir, config.source_dirs,
                    excluded_text=spec_text,
                )
                if leak.matched_paths:
                    emit_barrier_path_echo_detected(
                        events_file, spec=spec_name, run_id=run_id,
                        role=role.value,
                        matched_paths=leak.matched_paths,
                    )
                    log.warning(
                        "BARRIER_PATH_ECHO_DETECTED: %s output names "
                        "%d source path(s) absent from the spec: %s",
                        role.value, len(leak.matched_paths),
                        leak.matched_paths[:5],
                    )

            last_result = result
            break
        else:
            # Cap-overflow short-circuit (NON-RETRYABLE). A single turn that
            # exceeded CLAUDE_CODE_MAX_OUTPUT_TOKENS makes `claude -p` hang-retry
            # to the agent_timeout SIGKILL (exit 124). Re-running the identical
            # prompt only burns another full agent_timeout — so stop now instead
            # of consuming the agent_retries budget, and surface an actionable
            # message. The cap is already raised to the model's true max
            # (resolve_max_output_tokens); hitting it means one turn genuinely
            # needs > the model max, which a retry cannot fix.
            if _is_max_output_tokens_overflow(raw_output):
                log.error(
                    "agent %s exceeded CLAUDE_CODE_MAX_OUTPUT_TOKENS in a single "
                    "turn (exit %d) — NOT retrying. The output cap is already at "
                    "the model's max; this turn needs to be split. Raise "
                    "[model] max_output_tokens in config.toml only if the model "
                    "supports a higher per-response limit.",
                    role.value, exit_code,
                )
                if events_file:
                    emit_agent_failed(
                        events_file, spec=spec_name, run_id=run_id,
                        iteration=iteration, role=role.value,
                        exit_code=exit_code, reason="max_output_tokens",
                        model_id=resolved_model,
                        est_input_tokens=config.agent_max_turns * config.cost_est_input_tokens_per_turn,
                        est_output_tokens=config.agent_max_turns * config.cost_est_output_tokens_per_turn,
                        wall_seconds=wall_s,
                        phase=phase,
                    )
                last_result = result
                break

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
                        claude_args, raw_output, stderr,
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
            claude_args, raw_output, stderr,  # Sprint 33: raw stream → partial transcript on timeout
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
