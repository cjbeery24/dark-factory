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


# Orchestrator-only field. The agent does NOT emit ``stage_exit`` —
# giving it that channel would reintroduce the verification-claim
# ambiguity that ``claim: "none"`` exists to prevent (an agent that
# can't say "verified" via ``claim`` would say "abandoned" via
# ``stage_exit``). The orchestrator computes this from validator
# outcome and retry exhaustion.
_VALID_STAGE_EXITS = ("clean", "with_rejections", "abandoned")
_REQUIRED_CLAIM_VALUE = "none"
_SCHEMA_VERSION = 2

# v1 top-level keys that v2 explicitly removed or renamed. Their
# presence on a v2-tagged emission is a hybrid-payload signal — the
# agent's prompt is stale or its output is internally inconsistent.
# Surface as a parse error so the retry loop names the offending key
# rather than silently dropping the agent's data.
_V1_FORBIDDEN_KEYS = ("commands", "evidence", "no_op", "stage_exit")


@dataclass
class ParsedAgentOutput:
    """Structured return from ``_parse_agent_output``.

    Replaces the earlier 5-tuple return — past the readability cliff
    once ``files_seeded`` was added (Linus belated review). Adding the
    next field is one line here, not a signature change at every call
    site. Throwaway underscores in tests collapse to one ignored field
    on the dataclass instance.
    """
    commands: list[dict] = field(default_factory=list)
    files_seeded: list[str] = field(default_factory=list)
    considered: list["ConsideredPrecondition"] = field(default_factory=list)
    parse_errors: list[str] = field(default_factory=list)


@dataclass
class SubstratePrepResult:
    """Outcome of a substrate-prep invocation.

    Schema-v2 result shape (see ``agents/substrate-owner.md`` Output
    Contract). The deliberate omissions are load-bearing:

    * **No ``no_op`` field.** v1 used ``no_op: True`` to mean "no gaps
      detected — substrate is verified clean." That was a verification
      claim by the agent, encoded in the schema. Empty
      ``commands`` here means "agent emitted nothing this attempt" —
      a description of agent action, not a health claim. The substrate's
      post-condition is verified independently by ``verify_substrate()``
      downstream; nothing in this dataclass should be read as a substrate-
      health proxy. (Closes Gap 1 of the persisted-but-not-consumed audit:
      the schema-level non-claim survives a model refresh; a prompt-level
      "this agent does not verify" does not.)
    * **No ``verified`` / ``healthy`` field.** Same rationale.
    """
    commands: list[str] = field(default_factory=list)
    """Validated commands ready to forward to the gate as ``--pre-setup-cmd``.
    Empty list is the normal value when the agent observed no gaps to
    seed — it is NOT a signal that the substrate is healthy. Only
    ``verify_substrate()`` answers that question."""
    files_seeded: list[str] = field(default_factory=list)
    """Forward-looking declarative list: paths the validated commands
    will create or update. Recorded for archive grep ("what files did
    substrate-prep touch?") without re-parsing each shell command."""
    stage_exit: str = "clean"
    """How the emission attempt finished. One of: ``"clean"`` (commands
    emitted without unresolved errors — the success state, including
    zero-commands-emitted), ``"with_rejections"`` (best-effort forward
    after retries exhausted), ``"abandoned"`` (no valid emission
    possible). Description of agent action; not a substrate-health
    claim."""
    rejections: list[CommandRejection] = field(default_factory=list)
    """Per-attempt validator rejections. Empty list on first-pass success."""
    considered: list[ConsideredPrecondition] = field(default_factory=list)
    """Precondition CLASSES the agent evaluated. Empty list is itself a
    signal — the agent did not reason about preconditions at all,
    which is rarely correct."""
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


def _parse_agent_output(out_path: Path) -> ParsedAgentOutput:
    """Parse a substrate-prep.json file (schema v2).

    Schema-v2 parser. The contract enforces a load-bearing non-claim:
    the ``claim`` field is required and MUST equal ``"none"``. Any other
    value triggers a parse error so the agent is re-prompted with the
    rejection reason. Schema v1 (which carried the verification-claim
    ``no_op: true``) is rejected outright by schema_version mismatch —
    no migration shim.

    The agent does NOT emit ``stage_exit`` — that field is orchestrator-
    only (computed from validator outcome and retry exhaustion). v1
    field names (``commands``, ``evidence``, ``no_op``, ``stage_exit``)
    on a v2-tagged emission are flagged as hybrid-payload errors so the
    agent's data isn't silently dropped.

    Closes the substrate-prep half of Gap 1.
    """
    if not out_path.is_file():
        return ParsedAgentOutput(
            parse_errors=[f"agent did not write output file at {out_path}"],
        )
    try:
        raw = out_path.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return ParsedAgentOutput(
            parse_errors=[f"failed to read/parse {out_path}: {exc}"],
        )
    if not isinstance(doc, dict):
        return ParsedAgentOutput(parse_errors=[
            f"substrate-prep.json must be a JSON object, got "
            f"{type(doc).__name__}",
        ])

    errors: list[str] = []

    # Schema version gate — v1 carried a verification claim
    # (``no_op: true``) that v2 deliberately removes. Reject v1 outright;
    # the retry loop will re-prompt the agent which reads v2 agent.md.
    schema_version = doc.get("schema_version")
    if schema_version != _SCHEMA_VERSION:
        errors.append(
            f"schema_version must be {_SCHEMA_VERSION}, got {schema_version!r} "
            f"(v1's no_op verification claim is no longer accepted; "
            f"see agents/substrate-owner.md Output Contract)"
        )

    # The load-bearing non-claim. Required field; only legal value is
    # ``"none"``. Encoded in the schema (not just the prompt) so a
    # future model refresh cannot quietly start asserting verification.
    # JSON ``null`` (Python ``None``) is treated as a non-"none" value
    # and rejected; missing-key path takes the explicit "missing"
    # message instead.
    if "claim" not in doc:
        errors.append(
            f"missing required `claim` field (must be {_REQUIRED_CLAIM_VALUE!r}; "
            f"the agent makes no health claim — substrate verification is "
            f"performed downstream by verify_substrate())"
        )
    elif doc.get("claim") != _REQUIRED_CLAIM_VALUE:
        errors.append(
            f"`claim` must be {_REQUIRED_CLAIM_VALUE!r}, got "
            f"{doc.get('claim')!r} — the substrate-owner agent makes no "
            f"health claim; only verify_substrate() is authoritative. "
            f"Set claim to {_REQUIRED_CLAIM_VALUE!r}."
        )

    # Hybrid-payload detection: a v2-tagged emission carrying v1 field
    # names is the agent's prompt or training going stale on schema
    # transitions. Flag explicitly so the agent re-emits with the right
    # field names rather than having its data silently dropped.
    for v1_key in _V1_FORBIDDEN_KEYS:
        if v1_key in doc:
            errors.append(
                f"unexpected v1-only field {v1_key!r} on schema_version=2 "
                f"emission (use the v2 field name from "
                f"agents/substrate-owner.md Output Contract; v1 fields "
                f"are not silently accepted as zero-data fallback)"
            )

    cmds = doc.get("commands_emitted")
    files_seeded_raw = doc.get("files_seeded")
    considered_raw = doc.get("considered")

    # Reject ``null`` and missing-key for required collections. An
    # ``or []`` coercion would silently turn a JSON ``null`` into "zero
    # commands, success" — the same silent-acceptance pattern the
    # hybrid-payload check guards against.
    if cmds is None:
        errors.append(
            "missing or null `commands_emitted` (use [] for zero commands)"
        )
        cmds = []
    if files_seeded_raw is None:
        errors.append(
            "missing or null `files_seeded` (use [] for zero seeded files)"
        )
        files_seeded_raw = []
    if considered_raw is None:
        # Empty considered is itself a signal (per agent definition);
        # missing/null is a separate error so the agent re-emits with
        # the field present.
        errors.append("missing or null `considered` (use [] if appropriate)")
        considered_raw = []

    if not isinstance(cmds, list):
        errors.append(
            "substrate-prep.json `commands_emitted` must be a list, got "
            f"{type(cmds).__name__}"
        )
        return ParsedAgentOutput(parse_errors=errors)
    if not isinstance(files_seeded_raw, list):
        errors.append(
            "substrate-prep.json `files_seeded` must be a list, got "
            f"{type(files_seeded_raw).__name__}"
        )
        return ParsedAgentOutput(parse_errors=errors)
    if not isinstance(considered_raw, list):
        errors.append(
            "substrate-prep.json `considered` must be a list, got "
            f"{type(considered_raw).__name__}"
        )
        return ParsedAgentOutput(parse_errors=errors)

    out: list[dict] = []
    for i, entry in enumerate(cmds):
        if not isinstance(entry, dict):
            errors.append(
                f"commands_emitted[{i}]: expected mapping, "
                f"got {type(entry).__name__}"
            )
            continue
        step = entry.get("step", "")
        command = entry.get("command", "")
        if not isinstance(command, str) or not command.strip():
            errors.append(
                f"commands_emitted[{i}]: missing or empty `command` field"
            )
            continue
        out.append({
            "step": str(step) if step else f"step-{i}",
            "command": command.strip(),
            "rationale": str(entry.get("rationale", "") or ""),
            "fires_when": str(entry.get("fires_when", "") or ""),
        })

    files_seeded: list[str] = []
    for i, entry in enumerate(files_seeded_raw):
        if not isinstance(entry, str) or not entry.strip():
            errors.append(
                f"files_seeded[{i}]: expected non-empty string, "
                f"got {entry!r}"
            )
            continue
        files_seeded.append(entry.strip())

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
    return ParsedAgentOutput(
        commands=out,
        files_seeded=files_seeded,
        considered=considered,
        parse_errors=errors,
    )


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
        f"definition's Output Contract (schema_version 2). Write a "
        f"human-readable report to /project/{report_rel}.\n\n"
        f"If you observe no gaps, emit `commands_emitted: []` and "
        f"`files_seeded: []` with `stage_exit: \"clean\"` and "
        f"`claim: \"none\"`. Zero commands describes what you did; it "
        f"is NOT a claim that the substrate is healthy. The Setup "
        f"post-condition is observed downstream by verify_substrate()."
    )


def _build_retry_prompt(
    spec_run: "SpecRun",
    project_dir: Path,
    rejections: list[CommandRejection],
) -> str:
    """Retry prompt: name the rule that fired + the offending entry.

    Both schema-validation rejections (e.g., wrong ``claim`` value,
    missing required field) and command-shell rejections (e.g., inline
    ``;``, ``$(...)``) flow through the same rejection list — the
    rejection ``rule`` text names the specific failure so the agent
    can fix the right thing.
    """
    spec_name = spec_run.spec_name
    out_rel = f"{SUBSTRATE_PREP_DIR}/{spec_name}.json"
    rejection_lines = "\n".join(
        f"- step {r.step!r}: command {r.command!r} → REJECTED: {r.rule}"
        for r in rejections
    )
    return (
        f"Your previous substrate-prep emission was REJECTED. Each "
        f"rejection cites the rule that fired and the offending entry "
        f"verbatim. Schema-level failures (wrong claim value, missing "
        f"required field, schema_version mismatch) and command-shell "
        f"failures (inline `;`, `$(...)`, etc.) both flow through this "
        f"list. Read each rejection, fix the named issue, and re-emit.\n\n"
        f"Rejections:\n{rejection_lines}\n\n"
        f"Re-emit the full substrate-prep.json at /project/{out_rel} "
        f"per the schema-v2 Output Contract in your agent definition. "
        f"Reminders: `claim` MUST be `\"none\"` (you make no health "
        f"claim); `stage_exit` MUST be one of `\"clean\"` / "
        f"`\"with_rejections\"` / `\"abandoned\"`; commands go under "
        f"`commands_emitted` (NOT `commands`). The shell-safety rules "
        f"are in \"Critical Rules\" → \"Every command MUST be shell-safe.\""
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

        parsed = _parse_agent_output(out_path)
        # Always carry the latest `considered` and `files_seeded` logs
        # forward so diagnostic fields reflect the agent's most recent
        # reasoning, not the first attempt's. Empty `considered` is itself
        # a signal — the agent did not reason about preconditions.
        result.considered = parsed.considered
        result.files_seeded = parsed.files_seeded
        if parsed.parse_errors:
            for err in parsed.parse_errors:
                log.warning("substrate-prep parse: %s", err)
            # Treat parse failure as full-rejection — re-prompt next attempt
            # with a synthetic rejection entry that names the parse error.
            # All schema-v2 violations (wrong claim, missing claim,
            # schema_version mismatch, hybrid v1/v2 fields) land here so
            # the agent gets the rejection rule verbatim. ``stage_exit``
            # is set explicitly to "abandoned" rather than left at the
            # dataclass default — orchestrator-owned field, the agent's
            # output was unparseable so the attempt did not reach a
            # validator-clean state.
            result.stage_exit = "abandoned"
            rejections_so_far = [CommandRejection(
                attempt=attempt,
                step="(schema)",
                command="(see rule)",
                rule=f"schema/parse: {parsed.parse_errors[0]}",
            )]
            continue

        if not parsed.commands:
            # Zero commands is a description of agent action, NOT a
            # health claim. The orchestrator forwards no extra commands
            # and proceeds; the gate's Setup phase + verify_substrate()
            # determine substrate health independently. stage_exit is
            # "clean" here (the orchestrator-default) — the agent emitted
            # a valid v2 payload, just with nothing to seed.
            log.info(
                "substrate-prep attempt %d: agent emitted zero commands "
                "(stage_exit=clean, claim=none)", attempt,
            )
            return result

        passing, rejections = _validate_commands(parsed.commands, attempt)
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
    # Schema field describes WHAT the agent emitted, not whether the
    # substrate is healthy. "with_rejections" tells downstream readers
    # the forwarded commands are best-effort, not validator-clean.
    result.stage_exit = "with_rejections"
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
