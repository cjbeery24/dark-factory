"""Substrate-prep step: invoke the substrate-owner agent, validate
emitted commands, retry on rejection with bounded loop.

The substrate-prep phase exists between the implementer and the
quality gate's Setup step. Phase 4 (2026-04-24 database-foundation
smoke 2) demonstrated that the architect emits semantically correct
``commands.setup`` (e.g., ``cd apps/api && pnpm db:migrate``) and the
implementer builds the migration files, but the gate's Setup hangs
indefinitely because no role owns the procedural prerequisite —
``drizzle-kit migrate`` requires ``meta/_journal.json`` to exist
before it runs, and neither architect nor implementer has that gap
in their critical path.

The substrate owner agent inspects:
- Installed dependencies (``apps/*/package.json``, lockfiles)
- Existing files in the project source tree
- The architect's manifest (which tools are planned)
- The spec's §4a ``required_lifecycle_sequences`` block

…and emits idempotent shell commands the gate runs as ``pre_setup``
(via the ``--pre-setup-cmd`` flags forwarded to ``quality-gate.sh``).

This module is the orchestrator-side wrapper:
1. Build a prompt for the agent
2. Invoke it via ``invoke_agent(AgentRole.SUBSTRATE_OWNER, ...)``
3. Parse the agent's emitted JSON output
4. Validate each command via ``validate_pre_setup_entry()``
5. On rejection: compose a retry prompt naming the rule + offending
   entry; loop up to ``max_retries`` times
6. Return the list of validated commands (and the rejection log,
   for events)

LLM-only design (Phase 5 plan v3): no deterministic Python dispatch
table per tool. The validator is the strong gate; LLM variance under
validator pressure IS the data we want to collect. If an emission
path fails consistently across retries, the rejection log says
exactly what to fix (validator feedback, few-shot, or a deterministic
layer for that specific tool family).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from darkfactory.architecture.environment import validate_pre_setup_entry
from darkfactory.models.agent import AgentRole

if TYPE_CHECKING:
    from darkfactory.docker.barriers import BarrierConfig
    from darkfactory.models.config import PipelineConfig
    from darkfactory.models.spec_run import SpecRun

log = logging.getLogger(__name__)


# Bounded retry count per substrate-prep invocation. Phase 5 plan v3
# baseline: N=3-4. Picked 3 to keep cost bounded; if this proves too
# tight in trials, the validator-rejection event log will say so and
# we bump.
DEFAULT_MAX_RETRIES = 3


@dataclass
class CommandRejection:
    """A single command-validation rejection."""
    attempt: int
    step: str
    command: str
    rule: str  # the validator's rejection reason


@dataclass
class ConsideredPrecondition:
    """A precondition CLASS the agent evaluated, with its decision +
    rationale. Distinguishes "agent considered the gap and judged"
    from "agent never recognized the gap class" — the silence-vs-
    rejection diagnostic neo flagged in Phase 5 Stage 3 review."""
    precondition: str
    decision: str  # "emit" | "skip"
    rationale: str


@dataclass
class SubstratePrepResult:
    """Outcome of a substrate-prep invocation."""
    commands: list[str] = field(default_factory=list)
    """Validated commands ready to forward to the gate as ``--pre-setup-cmd``."""
    rejections: list[CommandRejection] = field(default_factory=list)
    """Per-attempt validator rejections. Empty list on first-pass success."""
    considered: list[ConsideredPrecondition] = field(default_factory=list)
    """Precondition CLASSES the agent evaluated. Empty list is itself a
    signal — the agent did not reason about preconditions at all,
    which is rarely correct."""
    no_op: bool = False
    """``True`` when the agent emitted ``no_op: true`` (no gaps detected)."""
    retries_exhausted: bool = False
    """``True`` when the retry loop bottomed out with unresolved rejections.
    The caller may proceed with whatever passed, or skip pre_setup entirely."""
    attempts: int = 0
    """Number of agent invocations performed (1 + retry count)."""


# Path constants for agent output
SUBSTRATE_PREP_DIR = ".dark-factory/architecture/substrate-prep"


def output_path_for(project_dir: Path, spec_name: str) -> Path:
    """Path the substrate-owner agent writes its JSON output to."""
    return project_dir / SUBSTRATE_PREP_DIR / f"{spec_name}.json"


def report_path_for(project_dir: Path, spec_name: str) -> Path:
    """Path the substrate-owner agent writes its human report to."""
    return project_dir / ".dark-factory" / "reports" / f"{spec_name}-substrate-prep.md"


def _parse_agent_output(
    out_path: Path,
) -> tuple[list[dict], list[ConsideredPrecondition], bool, list[str]]:
    """Parse a substrate-prep.json file.

    Returns:
        (commands, considered, no_op, parse_errors)
        commands: list of {step, command, rationale, fires_when} dicts
        considered: parsed precondition-classes-evaluated list
        no_op: True when the file declares no gaps to seed
        parse_errors: human-readable error messages (empty on success)
    """
    if not out_path.is_file():
        return [], [], False, [f"agent did not write output file at {out_path}"]
    try:
        raw = out_path.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [], [], False, [f"failed to read/parse {out_path}: {exc}"]
    if not isinstance(doc, dict):
        return [], [], False, [
            f"substrate-prep.json must be a JSON object, got "
            f"{type(doc).__name__}",
        ]
    no_op = bool(doc.get("no_op", False))
    cmds = doc.get("commands", []) or []
    considered_raw = doc.get("considered", []) or []
    if not isinstance(cmds, list):
        return [], [], no_op, [
            "substrate-prep.json `commands` must be a list, got "
            f"{type(cmds).__name__}",
        ]
    if not isinstance(considered_raw, list):
        return [], [], no_op, [
            "substrate-prep.json `considered` must be a list, got "
            f"{type(considered_raw).__name__}",
        ]
    out: list[dict] = []
    errors: list[str] = []
    for i, entry in enumerate(cmds):
        if not isinstance(entry, dict):
            errors.append(f"commands[{i}]: expected mapping, got {type(entry).__name__}")
            continue
        step = entry.get("step", "")
        command = entry.get("command", "")
        if not isinstance(command, str) or not command.strip():
            errors.append(f"commands[{i}]: missing or empty `command` field")
            continue
        out.append({
            "step": str(step) if step else f"step-{i}",
            "command": command.strip(),
            "rationale": str(entry.get("rationale", "") or ""),
            "fires_when": str(entry.get("fires_when", "") or ""),
        })
    considered: list[ConsideredPrecondition] = []
    for i, entry in enumerate(considered_raw):
        if not isinstance(entry, dict):
            errors.append(
                f"considered[{i}]: expected mapping, got {type(entry).__name__}"
            )
            continue
        precondition = str(entry.get("precondition", "") or "")
        decision = str(entry.get("decision", "") or "")
        rationale = str(entry.get("rationale", "") or "")
        if not precondition:
            errors.append(f"considered[{i}]: missing `precondition` field")
            continue
        if decision not in ("emit", "skip"):
            errors.append(
                f"considered[{i}]: `decision` must be 'emit' or 'skip', "
                f"got {decision!r}"
            )
            continue
        considered.append(ConsideredPrecondition(
            precondition=precondition,
            decision=decision,
            rationale=rationale,
        ))
    return out, considered, no_op, errors


def _validate_commands(
    commands: list[dict], attempt: int,
) -> tuple[list[str], list[CommandRejection]]:
    """Run each emitted command through ``validate_pre_setup_entry()``.

    Returns:
        (passing_commands, rejections)
    """
    passing: list[str] = []
    rejections: list[CommandRejection] = []
    for cmd in commands:
        rule = validate_pre_setup_entry(cmd["command"])
        if rule is None:
            passing.append(cmd["command"])
        else:
            rejections.append(CommandRejection(
                attempt=attempt,
                step=cmd["step"],
                command=cmd["command"],
                rule=rule,
            ))
    return passing, rejections


def _build_initial_prompt(spec_run: "SpecRun", project_dir: Path) -> str:
    """First-attempt prompt: read spec + manifest + project state."""
    spec_name = spec_run.spec_name
    out_rel = f"{SUBSTRATE_PREP_DIR}/{spec_name}.json"
    report_rel = f".dark-factory/reports/{spec_name}-substrate-prep.md"
    return (
        f"Read your agent definition at /project/.dark-factory/agents/"
        f"substrate-owner.md.\n\n"
        f"Read the spec at /project/.dark-factory/specs/{spec_name}.md.\n"
        f"Read the architect's manifest at /project/.dark-factory/"
        f"architecture/{spec_name}-manifest.yaml.\n"
        f"Read the project source tree (apps/*/package.json, lockfiles, "
        f"existing files) to determine what tools the project has adopted "
        f"and what files already exist.\n\n"
        f"Identify any tool-prerequisite gaps that would cause the gate's "
        f"Setup phase to fail or hang. Emit idempotent shell commands "
        f"that seed those prerequisites.\n\n"
        f"Write your output to /project/{out_rel} per the agent "
        f"definition's Output Contract. Write a human-readable report "
        f"to /project/{report_rel}.\n\n"
        f"If no gaps exist, emit `no_op: true` with evidence describing "
        f"what you inspected. That is the success state."
    )


def _build_retry_prompt(
    spec_run: "SpecRun",
    project_dir: Path,
    rejections: list[CommandRejection],
) -> str:
    """Retry prompt: name the rule that fired + the offending entry."""
    spec_name = spec_run.spec_name
    out_rel = f"{SUBSTRATE_PREP_DIR}/{spec_name}.json"
    rejection_lines = "\n".join(
        f"- step {r.step!r}: command {r.command!r} → REJECTED: {r.rule}"
        for r in rejections
    )
    return (
        f"Your previous substrate-prep emission contained "
        f"{len(rejections)} command(s) that the orchestrator's "
        f"`validate_pre_setup_entry()` rejected. Each rejection cites "
        f"the rule that fired and the offending entry verbatim. Rewrite "
        f"each rejected command to satisfy the rule, leaving validated "
        f"commands unchanged.\n\n"
        f"Rejections:\n{rejection_lines}\n\n"
        f"Re-emit the full substrate-prep.json (validated + rewritten "
        f"commands) at /project/{out_rel}. The validator's rules are in "
        f"your agent definition under \"Critical Rules\" → "
        f"\"Every command MUST be shell-safe.\" Re-read them if needed."
    )


def run_substrate_prep(
    spec_run: "SpecRun",
    config: "PipelineConfig",
    barriers: "dict[AgentRole, BarrierConfig]",
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
    max_retries: int = DEFAULT_MAX_RETRIES,
) -> SubstratePrepResult:
    """Invoke the substrate-owner agent, validate its output, retry
    until clean or retries exhausted.

    Returns a ``SubstratePrepResult`` with the validated commands. On
    retries-exhausted the caller decides whether to forward whatever
    passed (best-effort) or skip pre_setup entirely (defensive).
    """
    from darkfactory.events.types import (
        emit_substrate_prep_emitted,
        emit_substrate_prep_retries_exhausted,
        emit_substrate_prep_validator_reject,
    )
    from darkfactory.pipeline.agents import invoke_agent

    out_path = output_path_for(project_dir, spec_run.spec_name)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Clean any stale output from a prior iteration so we never read
    # last attempt's emission as this attempt's success.
    if out_path.exists():
        out_path.unlink()

    result = SubstratePrepResult()
    rejections_so_far: list[CommandRejection] = []

    for attempt in range(1, max_retries + 2):
        result.attempts = attempt
        if attempt == 1:
            prompt = _build_initial_prompt(spec_run, project_dir)
        else:
            prompt = _build_retry_prompt(
                spec_run, project_dir, rejections_so_far,
            )
            # Clear stale output before retry so we never carry over
            # last attempt's file as this attempt's emission.
            if out_path.exists():
                out_path.unlink()
            rejections_so_far = []

        agent_result = invoke_agent(
            AgentRole.SUBSTRATE_OWNER, prompt,
            config=config, barriers=barriers,
            run_id=spec_run.run_id, project_dir=project_dir,
            spec_name=spec_run.spec_name, iteration=spec_run.iteration,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            phase="emit" if attempt == 1 else "retry",
            attempt_offset=attempt - 1,
        )

        if agent_result.exit_code != 0:
            log.warning(
                "substrate-owner attempt %d exited %d; treating as "
                "no-emission and proceeding to next attempt",
                attempt, agent_result.exit_code,
            )
            continue

        commands_dicts, considered, no_op, parse_errors = _parse_agent_output(out_path)
        # Always carry the latest `considered` log forward so the
        # diagnostic field reflects the agent's most recent reasoning,
        # not the first attempt's. Empty list itself is a signal.
        result.considered = considered
        if parse_errors:
            for err in parse_errors:
                log.warning("substrate-prep parse: %s", err)
            # Treat parse failure as full-rejection — re-prompt next attempt
            # with a synthetic rejection entry that names the parse error.
            rejections_so_far = [CommandRejection(
                attempt=attempt,
                step="(file)",
                command="(unparseable)",
                rule=f"output JSON parse: {parse_errors[0]}",
            )]
            continue

        if no_op and not commands_dicts:
            log.info(
                "substrate-prep attempt %d: agent reports no gaps "
                "(no_op=True)", attempt,
            )
            result.no_op = True
            return result

        passing, rejections = _validate_commands(commands_dicts, attempt)
        for r in rejections:
            log.info(
                "substrate-prep attempt %d: rejected step=%r rule=%r",
                attempt, r.step, r.rule,
            )
            if events_file is not None:
                emit_substrate_prep_validator_reject(
                    events_file,
                    spec=spec_run.spec_name,
                    run_id=spec_run.run_id,
                    attempt=attempt,
                    step=r.step,
                    command=r.command,
                    rule=r.rule,
                )

        if not rejections:
            result.commands = passing
            log.info(
                "substrate-prep attempt %d: %d command(s) validated",
                attempt, len(passing),
            )
            if events_file is not None:
                for cmd in passing:
                    emit_substrate_prep_emitted(
                        events_file,
                        spec=spec_run.spec_name,
                        run_id=spec_run.run_id,
                        command=cmd,
                    )
            result.rejections.extend(rejections_so_far)
            return result

        # Some rejections — record and loop
        rejections_so_far = rejections
        result.rejections.extend(rejections)

    # Exhausted retries
    log.warning(
        "substrate-prep retries exhausted after %d attempts; %d unresolved "
        "rejection(s). Forwarding %d command(s) that passed across "
        "attempts (best-effort)",
        result.attempts, len(rejections_so_far), len(result.commands),
    )
    result.retries_exhausted = True
    if events_file is not None:
        emit_substrate_prep_retries_exhausted(
            events_file,
            spec=spec_run.spec_name,
            run_id=spec_run.run_id,
            attempts=result.attempts,
            unresolved_rejections=len(rejections_so_far),
        )
    return result


__all__ = [
    "CommandRejection",
    "DEFAULT_MAX_RETRIES",
    "SUBSTRATE_PREP_DIR",
    "SubstratePrepResult",
    "output_path_for",
    "report_path_for",
    "run_substrate_prep",
]
