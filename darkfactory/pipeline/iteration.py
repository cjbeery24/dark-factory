"""Steps 3–6 iteration loop — implement, gate, review, oracle.

Replaces the main iteration loop in dark-factory.sh (steps 3-6).
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from darkfactory.audit.checker import validate_oracle_feedback
from darkfactory.setup.detect import refresh_gate_commands
from darkfactory.docker.barriers import BarrierConfig
from darkfactory.errors import HumanHoldRequired
from darkfactory.events.types import (
    emit_files_changed,
    emit_gate_completed,
    emit_iter_cost,
    emit_trajectory_classified,
)
from darkfactory.git.ops import count_files_changed
from darkfactory.intelligence.cost import check_budget, estimate_cost
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.models.satisfaction import TrajectoryType
from darkfactory.pipeline.agents import invoke_agent
from darkfactory.pipeline.quality_gate import GateResult, PreGateResult, run_pre_gate, run_quality_gate
from darkfactory.notify.dispatcher import (
    notify_anomaly,
    notify_gate_complete,
    notify_hold,
    notify_no_ship,
)
from darkfactory.scenarios.trajectory import (
    StrategyActionType,
    classify_trajectory,
    detect_stable_failures,
    strategy_action,
    strategy_recommendation,
)
from darkfactory.state.store import write_state_or_warn

log = logging.getLogger(__name__)

_INLINE_MAX_CHARS = 8_000  # per file — prevent runaway context
_MAX_FEEDBACK_RETRIES = 2  # re-prompt attempts for leakage/compliance violations


def should_ship_with_gaps(
    config: PipelineConfig,
    spec_run: "SpecRun",
    hard_gates_pass: bool,
    pass_rate: int,
) -> bool:
    """Check whether the mechanical SHIP override conditions are met.

    Separated into a named function so it can be tested directly.

    Uses ``hard_gates_pass`` (Build/Lint/TypeCheck all pass) instead of
    ``gate_exit_code == 0`` — the overall gate exit code is non-zero whenever
    blind scenarios fail, which is exactly the case this override targets.
    """
    return (
        config.stable_failure_ship_override
        and spec_run.iteration >= config.stable_failure_min_iterations
        and hard_gates_pass
        and spec_run.stable_failure_ratio >= config.stable_failure_min_ratio
        and pass_rate >= config.min_ship_pass_rate
    )


def _read_for_inline(path: Path) -> str:
    """Read a file for prompt inlining. Returns empty string if missing/unreadable."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
        if len(text) > _INLINE_MAX_CHARS:
            text = text[:_INLINE_MAX_CHARS] + "\n\n[... truncated — content exceeds limit ...]"
        return text
    except OSError as exc:
        log.debug("could not read %s for inlining: %s", path, exc)
        return ""


def _parse_failing_test_names(
    project_dir: Path,
    spec_name: str,
    iteration: int,
) -> list[str]:
    """Read per-test results JSON and return names of failing tests.

    Returns an empty list if the file is missing or malformed (soft failure).
    """
    results_file = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-test-results-iter{iteration}.json"
    )
    if not results_file.exists():
        return []
    try:
        data = json.loads(results_file.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [
            entry["name"]
            for entry in data
            if isinstance(entry, dict)
            and entry.get("status") == "failed"
            and isinstance(entry.get("name"), str)
        ]
    except (json.JSONDecodeError, OSError) as exc:
        log.debug("could not parse per-test results %s: %s", results_file, exc)
        return []


def _parse_crashed_test_names(
    project_dir: Path,
    spec_name: str,
    iteration: int,
) -> list[str]:
    """Read per-test results JSON and return names of tests that crashed.

    Crashed tests have ``error_type == "crash"`` (TypeError, ReferenceError,
    etc.) as classified by the quality gate's per-test crash detection.
    Returns an empty list if the file is missing or no crashes detected.
    """
    results_file = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_name}-test-results-iter{iteration}.json"
    )
    if not results_file.exists():
        return []
    try:
        data = json.loads(results_file.read_text(encoding="utf-8"))
        if not isinstance(data, list):
            return []
        return [
            entry["name"]
            for entry in data
            if isinstance(entry, dict)
            and entry.get("error_type") == "crash"
            and isinstance(entry.get("name"), str)
        ]
    except (json.JSONDecodeError, OSError) as exc:
        log.debug("could not parse per-test results %s: %s", results_file, exc)
        return []


# ---------------------------------------------------------------------------
# Failure classification parsing
# ---------------------------------------------------------------------------

# Map old taxonomy names → new names for backward compatibility.
_CLASSIFICATION_ALIASES: dict[str, str] = {
    "FIXABLE": "IMPL_BUG",
    "AMBIGUOUS": "SPEC_AMBIGUOUS",
}

_VALID_CLASSIFICATIONS = frozenset({
    "IMPL_BUG", "SPEC_AMBIGUOUS", "SCENARIO_CRASH", "ENV_ISSUE", "BRITTLE",
})


def parse_feedback_classifications(feedback_text: str) -> dict[str, str]:
    """Parse Oracle feedback and extract failure classifications.

    Looks for the first ``<!-- CLASSIFICATIONS: {...} -->`` HTML comment
    block containing a JSON dict that maps spec sections to classifications.
    If multiple blocks exist (e.g. after a re-prompt), only the first is used.

    Returns a dict mapping spec section → classification.
    Returns empty dict if parsing fails (soft failure — preserves
    current all-IMPL_BUG behavior).
    """
    # Greedy match up to the closing --> to handle nested JSON objects.
    match = re.search(
        r'<!--\s*CLASSIFICATIONS:\s*(\{.*\})\s*-->',
        feedback_text,
        re.DOTALL,
    )
    if not match:
        return {}

    try:
        raw = json.loads(match.group(1))
    except (json.JSONDecodeError, TypeError) as exc:
        log.warning("failed to parse CLASSIFICATIONS JSON: %s", exc)
        return {}

    if not isinstance(raw, dict):
        log.warning("CLASSIFICATIONS block is not a JSON object")
        return {}

    result: dict[str, str] = {}
    for section, cls in raw.items():
        if not isinstance(cls, str):
            continue
        cls = _CLASSIFICATION_ALIASES.get(cls.upper(), cls.upper())
        if cls in _VALID_CLASSIFICATIONS:
            result[str(section).strip()] = cls
        else:
            log.warning(
                "unknown classification %r for %s, defaulting to IMPL_BUG",
                cls, section,
            )
            result[str(section).strip()] = "IMPL_BUG"
    return result


def apply_classification_routing(
    spec_run: SpecRun,
    iter_state: IterationState,
    feedback_text: str,
) -> None:
    """Evaluate Oracle feedback classifications and set escalation flags.

    Resets escalation state, parses classifications from feedback, applies
    mechanical reclassification, checks for ENV_ISSUE dominance, and tracks
    server error persistence.  Mutates *spec_run* and *iter_state* in place.
    """
    # Reset per-iteration flags so a changed distribution doesn't carry over.
    spec_run.escalation_recommended = False
    spec_run.escalation_category = ""
    spec_run.escalation_reason = ""

    classifications = parse_feedback_classifications(feedback_text)
    if classifications:
        # Store raw Oracle output (copy) for observability.
        spec_run.classification_history.append(dict(classifications))

        # --- Forced mechanical reclassification ---
        # Only applies when the server was healthy in both iterations.
        # If server_error was set in either, the Implementer couldn't
        # verify HTTP-level fixes, so repeated IMPL_BUG is expected.
        _prev_server_error = (
            spec_run.iterations[-2].server_error
            if len(spec_run.iterations) >= 2 else ""
        )
        if (
            len(spec_run.classification_history) >= 2
            and spec_run.stable_failure_ratio > 0.5
            and not iter_state.server_error
            and not _prev_server_error
        ):
            prev = spec_run.classification_history[-2]
            reclassified = 0
            for section in list(classifications):
                if (
                    classifications[section] == "IMPL_BUG"
                    and prev.get(section) == "IMPL_BUG"
                ):
                    classifications[section] = "SPEC_AMBIGUOUS"
                    reclassified += 1
            if reclassified:
                log.info(
                    "mechanical reclassification: %d IMPL_BUG → "
                    "SPEC_AMBIGUOUS (stable_failure_ratio=%.2f)",
                    reclassified, spec_run.stable_failure_ratio,
                )

        counts = Counter(classifications.values())
        iter_state.classification_counts = dict(counts)

        total = len(classifications)
        ambiguous = counts.get("SPEC_AMBIGUOUS", 0)
        brittle = counts.get("BRITTLE", 0)
        env = counts.get("ENV_ISSUE", 0)
        non_impl = ambiguous + brittle + env

        # Majority non-implementation failures → recommend escalation.
        if (ambiguous + brittle) > 0 and (ambiguous + brittle) / total > 0.5:
            spec_run.escalation_recommended = True
            spec_run.escalation_category = "SPEC_AMBIGUOUS"
            spec_run.escalation_reason = (
                f"{ambiguous} SPEC_AMBIGUOUS + {brittle} BRITTLE "
                f"out of {total} failures"
            )
            log.warning("escalation recommended: %s", spec_run.escalation_reason)

        # ENV_ISSUE escalation: the Implementer cannot fix
        # environment/config issues outside its writable scope.
        # Iter 1: escalate only when ALL failures are ENV_ISSUE.
        # Iter 2+: escalate when majority are ENV_ISSUE.
        env_dominant = (
            (env == total)
            or (spec_run.iteration >= 2 and env / total > 0.5)
        )
        if env_dominant:
            spec_run.escalation_recommended = True
            spec_run.escalation_category = "ENV_ISSUE"
            spec_run.escalation_reason = (
                f"{env} ENV_ISSUE out of {total} failures — "
                "environment/config issue outside Implementer scope"
            )
            log.warning(
                "escalation recommended (ENV_ISSUE): %s",
                spec_run.escalation_reason,
            )

        # Combined non-implementation majority at iter 2+: if no single
        # category dominates but together they do, escalate.
        if (
            not spec_run.escalation_recommended
            and spec_run.iteration >= 2
            and non_impl / total > 0.5
        ):
            spec_run.escalation_recommended = True
            spec_run.escalation_category = "SPEC_AMBIGUOUS"
            spec_run.escalation_reason = (
                f"{non_impl} non-implementation failures "
                f"({ambiguous} SPEC_AMBIGUOUS + {brittle} BRITTLE + "
                f"{env} ENV_ISSUE) out of {total}"
            )
            log.warning("escalation recommended (combined): %s", spec_run.escalation_reason)

    # Persistent server errors: if server_setup_cmd fails on consecutive
    # iterations, the Implementer can't fix it (config issue outside its
    # writable scope).
    if iter_state.server_error:
        spec_run.consecutive_server_errors += 1
    else:
        spec_run.consecutive_server_errors = 0

    if spec_run.consecutive_server_errors >= 2:
        spec_run.escalation_recommended = True
        spec_run.escalation_category = "ENV_ISSUE"
        spec_run.escalation_reason = (
            f"server_setup_cmd failed {spec_run.consecutive_server_errors} "
            "consecutive iterations — likely config/infrastructure issue"
        )
        log.warning("server error persistence: %s", spec_run.escalation_reason)


# Trajectory classes that trigger early escalation at iteration >= 3.
_ESCALATION_TRAJECTORIES = {
    TrajectoryType.DEGRADING,
    TrajectoryType.OSCILLATING,
    TrajectoryType.PLATEAU,
}


def _refresh_gate_commands_hook(
    config: PipelineConfig,
    project_dir: Path,
    progress: "Callable[[str], None]",
) -> None:
    config_path = project_dir / ".dark-factory" / "config.toml"
    if not config_path.exists():
        return
    refreshed = refresh_gate_commands(project_dir, config, config_path)
    if refreshed:
        progress("  Auto-detected gate commands from updated project structure:")
        for field_name, value in refreshed:
            progress(f"    {field_name} = {value!r}")
        progress("  Updated config.toml (hand-authored comments in [gates] may have been removed).")


def run_iteration_loop(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    state_file: Path | None = None,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """Run the steps 3-6 loop until SHIP or max iterations.

    Returns True if shipped, False if escalation needed.
    Raises HardAbort or BudgetExceeded on abort conditions.
    """
    def _progress(msg: str) -> None:
        if progress is not None:
            progress(msg)

    score_history: list[int] = []
    budget_alerted: set[int] = set()

    max_iters = spec_run.effective_max_iterations or config.max_iterations
    while spec_run.iteration <= max_iters:
        iter_state = IterationState(number=spec_run.iteration)
        spec_run.iterations.append(iter_state)
        turns_at_iter_start = spec_run.total_turns

        if state_file:
            write_state_or_warn(state_file, "iteration", str(spec_run.iteration))

        _progress(f"Iteration {spec_run.iteration}/{max_iters}:")

        # Step 3: Implementation.
        _progress("  Implementing...")
        _step_implementation(
            spec_run, iter_state, config, barriers, project_dir,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        )
        _progress("  Implementation complete")

        # Step 3.1: Refresh absent gate commands from updated project structure.
        _refresh_gate_commands_hook(config, project_dir, _progress)

        # Step 3.5: Pre-gate (skipped when max_pre_gate_retries == 0)
        # Pre-gate failures loop back to the implementer without incrementing spec_run.iteration.
        # NOTE: crashes during pre-gate retries resume at the start of this full iteration
        # (fresh implementation from spec). This is an accepted known limitation.
        if config.max_pre_gate_retries > 0:
            for pre_gate_attempt in range(config.max_pre_gate_retries):
                _progress(f"  Running pre-gate (attempt {pre_gate_attempt + 1}/{config.max_pre_gate_retries})...")
                pre_gate_result = run_pre_gate(
                    spec_run.spec_name, config, spec_run.run_id, project_dir,
                    iteration=spec_run.iteration, dry_run=dry_run,
                )
                if pre_gate_result.exit_code == 0:
                    _progress("  Pre-gate passed")
                    break
                _progress(f"  Pre-gate failed (attempt {pre_gate_attempt + 1})")
                if pre_gate_attempt < config.max_pre_gate_retries - 1:
                    # Re-invoke implementer with pre-gate feedback.
                    # spec_run.iteration does NOT increment — this is not a full iteration.
                    # total_turns IS incremented inside _step_implementation (same as normal call).
                    _step_implementation(
                        spec_run, iter_state, config, barriers, project_dir,
                        pre_gate_result=pre_gate_result,
                        events_file=events_file, audit_path=audit_path, dry_run=dry_run,
                    )
                else:
                    # Retries exhausted — fall through to the full quality gate.
                    # Log the last failure so it's visible in the pipeline log even
                    # though the full gate will re-evaluate the same code.
                    log.warning(
                        "pre-gate exhausted after %d attempts; last failure output:\n%s",
                        config.max_pre_gate_retries,
                        pre_gate_result.output[:2000] if pre_gate_result.output else "(empty)",
                    )

        # Step 4: Quality gate.
        _progress("  Running quality gate...")
        gate_result = _step_quality_gate(
            spec_run, iter_state, config, project_dir,
            events_file=events_file, dry_run=dry_run,
        )
        _progress(f"  Quality gate: {gate_result.pass_rate}% pass rate")
        notify_gate_complete(
            config, spec_run.spec_name, spec_run.run_id,
            spec_run.iteration, gate_result.pass_rate,
            passed=gate_result.exit_code == 0,
        )

        # Cost budget check.
        if events_file:
            check_budget(
                spec_run.total_turns, config, events_file,
                spec_run.spec_name, spec_run.run_id,
                alerted=budget_alerted,
            )

        # Anomaly checks (enterprise only).
        if events_file and config.enterprise_enabled:
            from darkfactory.intelligence.anomaly import check_baseline, check_spec_limits
            check_spec_limits(
                spec_run.total_turns, spec_run.wall_elapsed, config,
                events_file, spec_run.spec_name, spec_run.run_id,
            )
            check_baseline(
                spec_run.total_turns, events_file, config,
                spec_run.spec_name, spec_run.run_id,
            )

        # Per-iteration cost event.
        if events_file:
            iter_turns = spec_run.total_turns - turns_at_iter_start
            iter_cost = estimate_cost(iter_turns, config)
            emit_iter_cost(
                events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                iteration=spec_run.iteration, turns=iter_turns,
                estimated_usd=iter_cost.estimated_usd,
            )

        # Track scores for trajectory.
        score_history.append(iter_state.satisfaction_score)

        # Trajectory analysis (iteration >= 2).
        if len(score_history) >= 2:
            traj = classify_trajectory(score_history)
            spec_run.trajectory_class = traj
            spec_run.trajectory_strategy = strategy_recommendation(traj)

            # Stable failure set detection (P4).
            failing_sets = [it.failing_test_names for it in spec_run.iterations]
            stable_ratio, invariant_set = detect_stable_failures(
                failing_sets,
                threshold=config.trajectory_stable_failure_threshold,
            )
            if invariant_set:
                spec_run.stable_failure_ratio = stable_ratio
                spec_run.stable_failure_names = invariant_set
                log.info(
                    "stable failure set: %.0f%% invariant (%d test(s)), "
                    "suggesting scenario-dominated plateau",
                    stable_ratio * 100, len(invariant_set),
                )

            if events_file:
                emit_trajectory_classified(
                    events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                    iteration=spec_run.iteration, trajectory=traj.value,
                    strategy=spec_run.trajectory_strategy,
                )

            # Mechanical SHIP override: when hard gates pass, failures are stable
            # across iterations, and pass rate meets floor, ship with documented gaps.
            # Must be checked BEFORE early escalation — PLATEAU trajectory would
            # otherwise escalate before the override can fire.
            if should_ship_with_gaps(
                config, spec_run, iter_state.hard_gates_pass, iter_state.pass_rate,
            ):
                spec_run.ship_with_gaps = True
                log.info(
                    "mechanical SHIP override: gate passed, stable_failure_ratio=%.2f "
                    "(>= %.2f), pass_rate=%d%% (>= %d%%)",
                    spec_run.stable_failure_ratio,
                    config.stable_failure_min_ratio,
                    iter_state.pass_rate,
                    config.min_ship_pass_rate,
                )
                _progress(
                    f"  Mechanical SHIP override: {len(spec_run.stable_failure_names)} "
                    f"stable failure(s), pass rate {iter_state.pass_rate}%"
                )
                return True

            # Early escalation on bad trajectories at iteration >= 3.
            if spec_run.iteration >= 3 and traj in _ESCALATION_TRAJECTORIES:
                # Strategy switching: try alternative strategies before escalating.
                if config.strategy_switch_enabled and spec_run.strategy_switches < config.strategy_max_switches:
                    action = strategy_action(traj)
                    applied = _apply_strategy(spec_run, config, action.action, score_history)
                    if applied:
                        log.info(
                            "strategy switch %d/%d: %s at iteration %d (trajectory=%s)",
                            spec_run.strategy_switches, config.strategy_max_switches,
                            action.action.value, spec_run.iteration, traj.value,
                        )
                        spec_run.iteration += 1
                        continue
                log.warning("early escalation: trajectory=%s at iteration %d", traj, spec_run.iteration)
                spec_run.iteration += 1
                return False

        # Hard gate build failure.
        if gate_result.exit_code == 2 and config.hard_gate_build:
            _progress("  Quality gate: build failed, skipping review")
            spec_run.iteration += 1
            continue

        # Step 4.5: Evaluator (optional).
        if config.deploy_cmd:
            _progress("  Evaluating deployment...")
            _step_evaluator(
                spec_run, config, barriers, project_dir,
                events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            )
            _progress("  Evaluation complete")

        # Step 5: Review.
        _progress("  Reviewing...")
        _step_review(
            spec_run, iter_state, config, barriers, project_dir,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        )

        _progress("  Review complete")

        # Step 6: Oracle decision.
        _progress("  Oracle deciding...")
        shipped = _step_oracle(
            spec_run, iter_state, config, barriers, project_dir,
            events_file=events_file, audit_path=audit_path,
            state_file=state_file, dry_run=dry_run,
        )

        if shipped:
            _progress("  Oracle: SHIP")
            return True

        _progress("  Oracle: NO-SHIP")
        notify_no_ship(
            config, spec_run.spec_name, spec_run.run_id,
            spec_run.iteration, iter_state.pass_rate,
        )

        # --- Failure classification routing ---
        feedback_path = (
            project_dir / ".dark-factory" / "reviews"
            / f"{spec_run.spec_name}-feedback-iter{spec_run.iteration}.md"
        )
        feedback_text = ""
        if feedback_path.exists():
            feedback_text = feedback_path.read_text(encoding="utf-8", errors="replace")

        apply_classification_routing(spec_run, iter_state, feedback_text)

        # Short-circuit: escalation recommended.
        # ENV_ISSUE escalations fire after 1 iteration (retrying won't help).
        # Other escalations (SPEC_AMBIGUOUS/BRITTLE) wait for iteration >= 2.
        escalation_threshold = 1 if spec_run.escalation_category == "ENV_ISSUE" else 2
        if spec_run.escalation_recommended and spec_run.iteration >= escalation_threshold:
            _progress(f"  Escalation recommended: {spec_run.escalation_reason}")
            spec_run.iteration += 1
            return False

        spec_run.iteration += 1

    return False


def _apply_strategy(
    spec_run: SpecRun,
    config: PipelineConfig,
    action: StrategyActionType,
    score_history: list[int] | None = None,
) -> bool:
    """Apply a strategy switch. Returns True if a strategy was applied.

    For FRESH_START, clears score_history (if provided), spec_run.iterations,
    spec_run.trajectory_class, and resets the iteration counter.
    """
    if action == StrategyActionType.SWITCH_MODEL:
        # Pick a fallback model: use the first model_ids value that differs
        # from the current effective model.
        current = spec_run.model_override or config.model_id
        fallback = ""
        for role_model in config.model_ids.values():
            if role_model != current:
                fallback = role_model
                break
        if not fallback or fallback == current:
            # No alternative model available — can't switch.
            return False
        spec_run.model_override = fallback
        spec_run.strategy_switches += 1
        log.info("strategy: switching model from %s to %s", current, fallback)
        return True

    if action == StrategyActionType.FRESH_START:
        # Reset iteration to 1 with clean state.
        spec_run.iteration = 0  # Will be incremented to 1 by the loop.
        spec_run.iterations.clear()
        spec_run.trajectory_class = None
        spec_run.trajectory_strategy = ""
        if score_history is not None:
            score_history.clear()
        spec_run.strategy_switches += 1
        log.info("strategy: fresh start (resetting to iteration 1)")
        return True

    # CONTINUE and ESCALATE don't apply a strategy switch.
    return False


def _step_implementation(
    spec_run: SpecRun,
    iter_state: IterationState,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    pre_gate_result: PreGateResult | None = None,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 3: Invoke the implementer agent."""
    if pre_gate_result is not None:
        # Pre-gate fix: tight prompt focused on the mechanical failures.
        prompt = (
            f"Your code failed the pre-gate checks. "
            f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md "
            f"and your source files to understand the context, then fix the following errors:\n\n"
            f"{pre_gate_result.output}\n\n"
            f"Run lint, typecheck, and build until they pass. "
            f"Do not change behavior — only fix the reported errors."
        )
    elif spec_run.iteration == 1:
        prompt = (
            f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md and implement it. "
            f"Write code in the source directories: {config.source_dirs}."
        )
        # Inject architecture manifest context on iteration 1.
        manifest_path = (
            project_dir / ".dark-factory" / "architecture"
            / f"{spec_run.spec_name}-manifest.yaml"
        )
        if manifest_path.exists():
            prompt += (
                " Then read the architecture manifest at /project/.dark-factory/architecture/"
                f"{spec_run.spec_name}-manifest.yaml for structural decisions — exact file "
                "paths, schemas, API shapes, and dependencies. The blind test scenarios were "
                "generated against this manifest — deviating from its file_map or api_contracts "
                "will cause test failures."
            )
    else:
        feedback_host = (
            project_dir / ".dark-factory" / "reviews"
            / f"{spec_run.spec_name}-feedback-iter{spec_run.iteration - 1}.md"
        )
        if feedback_host.exists():
            max_iters = spec_run.effective_max_iterations or config.max_iterations
            if spec_run.iteration >= max_iters:
                # Final iteration: absolute prohibition — no recovery path exists,
                # so the instruction is harder than the non-final tier ("explicitly",
                # "as failing", "Do not make any other changes" vs "Fix only").
                scope_instruction = (
                    f" This is the final iteration ({spec_run.iteration} of {max_iters}). "
                    "Address only what the feedback explicitly identifies as failing. "
                    "Do not make any other changes."
                )
            else:
                # Non-final iteration: firm but not absolute — the Implementer can
                # make necessary structural changes to fix a root cause, but must
                # not speculatively refactor adjacent code.
                scope_instruction = (
                    f" Iteration {spec_run.iteration} of {max_iters}. "
                    "Fix only what the feedback identifies. Do not refactor, restructure, "
                    "or improve anything not directly causing a failure."
                )
            prompt = (
                f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md. "
                f"Read the feedback at /project/.dark-factory/reviews/"
                f"{spec_run.spec_name}-feedback-iter{spec_run.iteration - 1}.md "
                f"and fix the issues.{scope_instruction}"
            )
        else:
            log.warning("feedback file missing for iteration %d, implementing from spec only", spec_run.iteration)
            prompt = (
                f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md and implement it. "
                f"This is iteration {spec_run.iteration} — no feedback available from the previous iteration."
            )

    result = invoke_agent(
        AgentRole.IMPLEMENTER, prompt,
        config=config, barriers=barriers, run_id=spec_run.run_id,
        project_dir=project_dir, spec_name=spec_run.spec_name,
        iteration=spec_run.iteration, events_file=events_file,
        audit_path=audit_path, dry_run=dry_run,
        model_override=spec_run.model_override,
    )
    # Track turns as upper-bound estimate (max_turns per invocation).
    # Actual turn count is not available from the container output.
    spec_run.total_turns += config.agent_max_turns
    iter_state.wall_seconds += result.wall_seconds

    # Track files changed and barrier violations.
    if not dry_run:
        iter_state.files_changed = count_files_changed(project_dir, config.source_dirs)
    if events_file and iter_state.files_changed:
        emit_files_changed(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            iteration=spec_run.iteration, count=iter_state.files_changed,
        )
    if result.barrier_violations:
        iter_state.barrier_violations = len(result.barrier_violations)

    if result.exit_code != 0:
        log.warning("implementer failed at iteration %d (exit %d)", spec_run.iteration, result.exit_code)


def _step_quality_gate(
    spec_run: SpecRun,
    iter_state: IterationState,
    config: PipelineConfig,
    project_dir: Path,
    *,
    events_file: Path | None = None,
    dry_run: bool = False,
) -> GateResult:
    """Step 4: Run the quality gate."""
    gate_result = run_quality_gate(
        spec_run.spec_name, config, spec_run.run_id, project_dir,
        iteration=spec_run.iteration, dry_run=dry_run,
    )

    iter_state.gate_exit_code = gate_result.exit_code
    iter_state.hard_gates_pass = gate_result.hard_gates_pass
    iter_state.scenario_pass_count = gate_result.scenario_passed
    iter_state.scenario_fail_count = gate_result.scenario_failed
    iter_state.scenario_total = gate_result.scenario_total
    iter_state.pass_rate = gate_result.pass_rate
    iter_state.satisfaction_score = gate_result.satisfaction_score
    iter_state.sentinel_present = gate_result.sentinel_present
    iter_state.scenario_crashed = gate_result.scenario_crashed
    iter_state.scenario_assert_failed = gate_result.scenario_assert_failed
    iter_state.server_error = gate_result.server_error

    # Populate failing test names from per-test results JSON (P4).
    iter_state.failing_test_names = _parse_failing_test_names(
        project_dir, spec_run.spec_name, spec_run.iteration,
    )

    # Floor enforcement.
    if config.min_satisfaction_score > 0 and gate_result.satisfaction_score < config.min_satisfaction_score:
        if gate_result.exit_code == 0:
            gate_result.exit_code = 1
            log.info("satisfaction %d below floor %d, forcing gate failure",
                     gate_result.satisfaction_score, config.min_satisfaction_score)

    # Enforce min_ship_pass_rate with sentinel awareness.
    # A sentinel (SCENARIOS-NOT-APPLICABLE.md) is the explicit, validated opt-out
    # from blind scenarios — do not penalize it. 0 scenarios without a sentinel
    # is an error state (scenario generation should have caught it).
    #
    # Interaction with validate_scenarios:
    #   validate_scenarios=True  → zero scenarios + no sentinel is an explicit failure
    #   validate_scenarios=False → scenario execution is disabled entirely; a zero
    #     scenario count is expected and should not trigger the floor check. In that
    #     case skip this block entirely (min_ship_pass_rate has no useful meaning).
    if config.min_ship_pass_rate > 0 and not gate_result.sentinel_present and config.validate_scenarios:
        if gate_result.scenario_total == 0:
            # No scenarios, no sentinel, and scenarios ARE expected.
            if gate_result.exit_code == 0:
                gate_result.exit_code = 1
                log.warning(
                    "no blind scenarios and no sentinel present — forcing gate failure"
                )
        elif gate_result.pass_rate < config.min_ship_pass_rate:
            if gate_result.exit_code == 0:
                gate_result.exit_code = 1
                log.info("pass rate %d below floor %d, forcing gate failure",
                         gate_result.pass_rate, config.min_ship_pass_rate)

    if events_file:
        emit_gate_completed(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            iteration=spec_run.iteration, exit_code=gate_result.exit_code,
            pass_rate=gate_result.pass_rate, satisfaction=gate_result.satisfaction_score,
        )

    return gate_result


def _step_evaluator(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 4.5: Deploy, evaluate, teardown."""
    if not config.enterprise_enabled:
        return

    from darkfactory.pipeline.evaluator import deploy, health_check, teardown

    try:
        if not deploy(config):
            return

        if not health_check(config):
            return

        # Ensure reports dir exists before bind-mounting it rw into the container.
        # Docker creates missing dirs as root-owned, making them unwritable by the agent user.
        (project_dir / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)

        # Run evaluator agent if agent definition exists.
        evaluator_md = project_dir / "agents" / "evaluator.md"
        if evaluator_md.exists():
            prompt = (
                f"Exercise the deployed application at {config.deploy_base_url}. "
                f"Write evaluation report to .dark-factory/reports/"
                f"{spec_run.spec_name}-evaluation-iter{spec_run.iteration}.md"
            )
            invoke_agent(
                AgentRole.EVALUATOR, prompt,
                config=config, barriers=barriers, run_id=spec_run.run_id,
                project_dir=project_dir, spec_name=spec_run.spec_name,
                iteration=spec_run.iteration, events_file=events_file,
                audit_path=audit_path, dry_run=dry_run,
            )
    finally:
        teardown(config)


def _step_review(
    spec_run: SpecRun,
    iter_state: IterationState,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 5: Run deterministic pre-pass then invoke the reviewer agent."""
    # Ensure reviews dir exists before bind-mounting it rw into the container.
    (project_dir / ".dark-factory" / "reviews").mkdir(parents=True, exist_ok=True)

    report_file = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_run.spec_name}-quality-gate-summary.md"
    )

    # FR-5: Run deterministic pre-pass before the reviewer (EC-11: catch all errors).
    prepass_result = None
    try:
        from darkfactory.review.prepass import run_review_prepass
        prepass_result = run_review_prepass(
            project_dir, spec_run.spec_name, config,
            server_error=iter_state.server_error,
        )
    except Exception as exc:
        log.error("review pre-pass failed, proceeding without: %s", exc)

    # Read on host before container launch — Reviewer's container has reports/ masked.
    gate_text = _read_for_inline(report_file)
    prepass_text = ""
    if prepass_result is not None:
        prepass_md = project_dir / ".dark-factory" / "reports" / f"{spec_run.spec_name}-review-prepass.md"
        prepass_text = _read_for_inline(prepass_md)

    prompt = f"Review the implementation for spec /project/.dark-factory/specs/{spec_run.spec_name}.md.\n\n"

    if gate_text:
        prompt += "```quality-gate-summary\n" + gate_text + "\n```\n\n"

    if prepass_text:
        prompt += "```review-prepass\n" + prepass_text + "\n```\n\n"

    prompt += f"Write review to /project/.dark-factory/reviews/{spec_run.spec_name}-review.md"

    # Gate the scoring instruction on prepass_text being non-empty — if the file was
    # written but unreadable, sending the instruction without the data is misleading.
    if prepass_text:
        prompt += (
            " Use pre-pass scores for Test Results (25%), Code Quality (20%),"
            " and Security (15%). Focus your analysis on Spec Compliance (30%)"
            " and Robustness (10%)."
        )

    result = invoke_agent(
        AgentRole.REVIEWER, prompt,
        config=config, barriers=barriers, run_id=spec_run.run_id,
        project_dir=project_dir, spec_name=spec_run.spec_name,
        iteration=spec_run.iteration, events_file=events_file,
        audit_path=audit_path, dry_run=dry_run,
    )

    if result.exit_code != 0:
        # FR-6 / EC-8: Fallback to deterministic scores on reviewer failure.
        log.warning("reviewer failed, using deterministic pre-pass scores as fallback")
        review_path = project_dir / ".dark-factory" / "reviews" / f"{spec_run.spec_name}-review.md"
        review_path.parent.mkdir(parents=True, exist_ok=True)

        if prepass_result is not None:
            pp = prepass_result
            review_path.write_text(
                f"# Review: PARTIAL (Deterministic Fallback)\n\n"
                f"Review agent failed (exit {result.exit_code}). "
                f"Deterministic pre-pass scores used as fallback.\n\n"
                f"| Dimension | Score | Source |\n"
                f"|-----------|-------|--------|\n"
                f"| Test Results | {pp.test_analysis.score}/25 | Deterministic pre-pass |\n"
                f"| Code Quality | {pp.code_quality.score}/20 | Deterministic pre-pass |\n"
                f"| Security | {pp.security.score}/15 | Deterministic pre-pass |\n"
                f"| Spec Compliance | 0/30 | Reviewer failed — no LLM assessment |\n"
                f"| Robustness | 0/10 | Reviewer failed — no LLM assessment |\n"
                f"| Total | {pp.deterministic_total}/100 |\n\n"
                f"VERDICT: REQUEST CHANGES\n"
            )
        else:
            review_path.write_text(
                f"# Review: FAILED\n\nReview agent failed (exit {result.exit_code}).\n\nVERDICT: REVIEW_FAILED\n"
            )


def _step_oracle(
    spec_run: SpecRun,
    iter_state: IterationState,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    state_file: Path | None = None,
    dry_run: bool = False,
) -> bool:
    """Step 6: Oracle decision. Returns True if SHIP."""
    prompt = (
        f"Make a SHIP or NO-SHIP decision for {spec_run.spec_name}. "
        f"Spec: /project/.dark-factory/specs/{spec_run.spec_name}.md. "
        f"Review: /project/.dark-factory/reviews/{spec_run.spec_name}-review.md. "
        f"Report: /project/.dark-factory/reports/{spec_run.spec_name}-quality-gate-summary.md. "
        f"Iteration: {spec_run.iteration}/{config.max_iterations}. "
        f"Satisfaction: {iter_state.satisfaction_score}. Pass rate: {iter_state.pass_rate}%. "
        f"Your first line MUST be either SHIP or NO-SHIP. "
        f"If NO-SHIP, write structured iteration feedback to "
        f"/project/.dark-factory/reviews/{spec_run.spec_name}-feedback-iter{spec_run.iteration}.md "
        f"following the format in your agent definition. "
        f"IMPORTANT: If NO-SHIP, your feedback MUST end with "
        f"<!-- CLASSIFICATIONS: {{...}} --> mapping each failing spec "
        f"section to IMPL_BUG, SPEC_AMBIGUOUS, SCENARIO_CRASH, ENV_ISSUE, or BRITTLE."
    )

    # Crash context: tell the Oracle when crashes were excluded from pass rate.
    if iter_state.scenario_crashed > 0:
        raw_total = iter_state.scenario_total
        raw_rate = (
            (iter_state.scenario_pass_count * 100) // raw_total
            if raw_total > 0 else 0
        )
        prompt += (
            f"\n\n**Crash-adjusted pass rate:** {iter_state.scenario_crashed} scenario(s) "
            f"crashed (TypeError/SyntaxError/import error) and were excluded from the "
            f"pass rate. These are scenario bugs, not implementation bugs. "
            f"Raw pass rate (including crashes): {raw_rate}%. "
            f"Adjusted pass rate (excluding crashes): {iter_state.pass_rate}%."
        )

    # Mechanical pre-classification: identify tests that crashed (TypeError,
    # ReferenceError, etc.) and tell the Oracle to classify them as
    # SCENARIO_CRASH.  This removes them from the Oracle's classification
    # burden and prevents misclassification as IMPL_BUG.
    crashed_names = _parse_crashed_test_names(
        project_dir, spec_run.spec_name, spec_run.iteration,
    )
    if crashed_names:
        prompt += (
            f"\n\n## Pre-Classified Crashes\n"
            f"The following {len(crashed_names)} test(s) crashed with runtime "
            f"errors (TypeError/ReferenceError/SyntaxError). These are scenario "
            f"code bugs, NOT implementation failures. Classify them as "
            f"SCENARIO_CRASH in your CLASSIFICATIONS block and do NOT include "
            f"them in implementation feedback:\n"
        )
        for cn in crashed_names:
            prompt += f"- {cn}\n"

    # Server startup failure context: include container logs so the Oracle
    # can write actionable feedback for the Implementer.
    if iter_state.server_error:
        # Strip ANSI escape codes (Docker output may contain color codes).
        clean_error = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', iter_state.server_error)
        error_lines = clean_error.strip().splitlines()[-30:]
        trimmed = "\n".join(error_lines)
        prompt += (
            f"\n\n## Server Setup/Startup Failure\n"
            f"The test server failed during setup or startup. No HTTP/journey "
            f"scenarios could run. The 0% HTTP pass rate is caused by this "
            f"failure, not by scenario or implementation logic bugs.\n\n"
            f"**Server logs (last {len(error_lines)} lines):**\n"
            f"```\n{trimmed}\n```\n\n"
            f"Include the root cause from these logs in your feedback so the "
            f"Implementer can fix the startup error."
        )

    # Inject stable failure analysis when plateau detected.
    if spec_run.stable_failure_ratio > 0 and spec_run.stable_failure_names:
        stable_count = len(spec_run.stable_failure_names)
        iters_with_data = len([
            it for it in spec_run.iterations if it.failing_test_names
        ])
        prompt += (
            f"\n\n## Stable Failure Analysis\n"
            f"The pipeline has detected that {spec_run.stable_failure_ratio:.0%} "
            f"of failing tests are invariant across {iters_with_data} iteration(s) "
            f"({stable_count} test(s) fail every iteration with identical results). "
            f"This suggests a SCENARIO-DOMINATED PLATEAU — the failures may be caused "
            f"by brittle or over-specified test scenarios rather than implementation "
            f"deficiency.\n\n"
            f"When writing feedback, classify each failing spec area using the "
            f"taxonomy in your agent definition. Include the Classification "
            f"column in the Spec Section Failures table and the structured "
            f"<!-- CLASSIFICATIONS: {{...}} --> block.\n\n"
            f"Quick reference:\n"
            f"- IMPL_BUG: Spec says X, implementation does Y — cite the spec line.\n"
            f"- SPEC_AMBIGUOUS: Two reasonable implementations could differ — "
            f"recommend ESCALATION.\n"
            f"- BRITTLE: Test asserts on format/style the spec doesn't constrain — "
            f"recommend ESCALATION.\n"
            f"- SCENARIO_CRASH: Test itself crashes (TypeError, import error) — "
            f"omit from fix list.\n"
            f"- ENV_ISSUE: Timeout, OOM, infra failure — omit from fix list."
        )

        # Mechanical reclassification: failures targeted in feedback for 2+
        # iterations should be reclassified as SPEC_AMBIGUOUS per the
        # reclassification rule in the Oracle agent definition.
        suspected_ambiguous = [
            name for name in spec_run.stable_failure_names
            if spec_run.failure_feedback_iterations.get(name, 0) >= 2
        ]
        if suspected_ambiguous:
            prompt += (
                f"\n\n## Mechanical Reclassification Override\n"
                f"The following {len(suspected_ambiguous)} failure(s) have persisted "
                f"for 2+ iterations AFTER being included in feedback. Per the "
                f"reclassification rule, classify these as **SPEC_AMBIGUOUS** unless "
                f"you can cite the exact spec line that uniquely determines the "
                f"expected behavior. Recommend ESCALATION for these failures."
            )

    # Composite anomaly blocking (enterprise only).
    composite_blocked = False
    if config.enterprise_enabled:
        from darkfactory.intelligence.anomaly import (
            COMPOSITE_BLOCK, COMPOSITE_ESCALATE, composite_score,
        )
        anomaly = composite_score(
            turns_anomalous=spec_run.total_turns > config.anomaly_max_spec_turns,
            wall_anomalous=spec_run.wall_elapsed > config.anomaly_max_spec_wall_s,
            trajectory_bad=spec_run.trajectory_class in _ESCALATION_TRAJECTORIES,
            files_anomalous=iter_state.files_changed > config.anomaly_max_files_written,
            barrier_violation=any(i.barrier_violations > 0 for i in spec_run.iterations),
        )
        if anomaly >= COMPOSITE_BLOCK:
            prompt += "\n\nCRITICAL: Anomaly score is high. You MUST NOT SHIP."
            composite_blocked = True
            notify_anomaly(config, spec_run.spec_name, "composite_block", anomaly)
        elif anomaly >= COMPOSITE_ESCALATE:
            notify_anomaly(config, spec_run.spec_name, "composite_escalate", anomaly)

    result = invoke_agent(
        AgentRole.ORACLE, prompt,
        config=config, barriers=barriers, run_id=spec_run.run_id,
        project_dir=project_dir, spec_name=spec_run.spec_name,
        iteration=spec_run.iteration, events_file=events_file,
        audit_path=audit_path, dry_run=dry_run,
    )

    # Parse decision from first line.
    decision = _parse_oracle_decision(result.output)

    if decision == "SHIP":
        # Override checks.
        if composite_blocked:
            log.warning("oracle said SHIP but composite blocked — forcing NO-SHIP")
            decision = "NO-SHIP"
        if config.min_satisfaction_score > 0 and iter_state.satisfaction_score < config.min_satisfaction_score:
            log.warning("oracle said SHIP but satisfaction below floor — forcing NO-SHIP")
            decision = "NO-SHIP"
        if (config.min_ship_pass_rate > 0
                and config.validate_scenarios
                and not iter_state.sentinel_present
                and iter_state.pass_rate < config.min_ship_pass_rate):
            log.warning("oracle said SHIP but pass rate below floor — forcing NO-SHIP")
            decision = "NO-SHIP"

    # Record verdict after all overrides are applied.
    iter_state.oracle_verdict = decision

    if decision == "SHIP":
        # Autonomy level 1: require human confirmation before ship.
        if spec_run.effective_autonomy == 1:
            hold_file = (
                project_dir / ".dark-factory" / "state"
                / f"{spec_run.spec_name}.hold"
            )
            hold_file.parent.mkdir(parents=True, exist_ok=True)
            hold_file.write_text(
                f"spec={spec_run.spec_name}\n"
                f"run_id={spec_run.run_id}\n"
                f"trust={spec_run.trust_score}\n"
                f"iteration={spec_run.iteration}\n"
                f"satisfaction={iter_state.satisfaction_score}\n"
            )
            notify_hold(config, spec_run.spec_name, spec_run.run_id, spec_run.trust_score)
            raise HumanHoldRequired(
                f"{spec_run.spec_name}: SHIP pending human confirmation "
                f"(trust={spec_run.trust_score}, autonomy=1)"
            )
        return True

    # NO-SHIP path: validate feedback for leakage with re-prompt on violation.
    feedback_file = (
        project_dir / ".dark-factory" / "reviews"
        / f"{spec_run.spec_name}-feedback-iter{spec_run.iteration}.md"
    )
    if feedback_file.exists() and audit_path:
        for attempt in range(_MAX_FEEDBACK_RETRIES + 1):
            feedback_text = feedback_file.read_text()
            validation = validate_oracle_feedback(feedback_text, audit_path)
            if validation.is_compliant:
                break
            log.warning(
                "oracle feedback failed validation (attempt %d/%d): %s",
                attempt + 1, _MAX_FEEDBACK_RETRIES + 1,
                validation.violation_summary,
            )
            if attempt < _MAX_FEEDBACK_RETRIES:
                # Re-prompt the Oracle to fix its feedback.
                reprompt = (
                    f"Your feedback file at /project/.dark-factory/reviews/"
                    f"{spec_run.spec_name}-feedback-iter{spec_run.iteration}.md "
                    f"violates validation rules: "
                    f"{validation.violation_summary}. "
                    f"Rewrite the feedback file. Do NOT include scenario names, "
                    f"assertion messages, test counts, percentages, or N/M scores. "
                    f"Reference spec sections (FR-N, EC-N) only. "
                    f"You MUST include the <!-- CLASSIFICATIONS: {{...}} --> block "
                    f"at the end of your feedback mapping each failing spec section "
                    f"to its classification (IMPL_BUG, SPEC_AMBIGUOUS, SCENARIO_CRASH, "
                    f"ENV_ISSUE, BRITTLE)."
                )
                invoke_agent(
                    AgentRole.ORACLE, reprompt,
                    config=config, barriers=barriers,
                    run_id=spec_run.run_id,
                    project_dir=project_dir,
                    spec_name=spec_run.spec_name,
                    iteration=spec_run.iteration,
                    events_file=events_file,
                    audit_path=audit_path, dry_run=dry_run,
                )
            else:
                log.warning(
                    "oracle feedback still non-compliant after re-prompt; "
                    "proceeding with non-compliant feedback"
                )

    # Mechanical reclassification: track how many iterations each stable failure
    # has been targeted in feedback. After 2+ iterations, the Oracle prompt will
    # annotate these as SPEC_AMBIGUOUS per the reclassification rule.
    for name in spec_run.stable_failure_names:
        spec_run.failure_feedback_iterations[name] = (
            spec_run.failure_feedback_iterations.get(name, 0) + 1
        )

    if state_file:
        write_state_or_warn(state_file, "last_decision", decision)

    return False


def _parse_oracle_decision(output: str) -> str:
    """Parse SHIP or NO-SHIP from oracle output's first line.

    The oracle must start its response with exactly SHIP or NO-SHIP.
    """
    for line in output.splitlines():
        stripped = line.strip().upper()
        if not stripped:
            continue
        if stripped == "SHIP" or stripped.startswith("SHIP ") or stripped.startswith("SHIP:"):
            return "SHIP"
        return "NO-SHIP"
    return "NO-SHIP"
