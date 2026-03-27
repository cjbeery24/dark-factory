"""Main pipeline orchestrator — the 7-step pipeline.

Replaces scripts/dark-factory.sh. Coordinates spec validation,
scenario generation, the iteration loop, and post-pipeline actions
(registry, regression, git, notifications).
"""

from __future__ import annotations

import logging
import shutil
from collections.abc import Callable
from pathlib import Path

from darkfactory.config.schema import validate_spec_name
from darkfactory.docker.barriers import BarrierConfig, configure_barriers
from darkfactory.docker.client import require_docker
from darkfactory.docker.networks import setup_networks, setup_proxies
from darkfactory.errors import BudgetExceeded, DarkFactoryError, HardAbort, HumanHoldRequired
from darkfactory.events.types import (
    emit_cost_summary,
    emit_deploy_complete,
    emit_spec_escalated,
    emit_spec_failed,
    emit_spec_shipped,
    emit_spec_start,
    emit_trust_scored,
)
from darkfactory.git import ops as git_ops
from darkfactory.intelligence.cost import estimate_cost
from darkfactory.intelligence.metrics import record_end, record_start
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import PipelineStatus, SpecRun
from darkfactory.notify.dispatcher import (
    notify_canary_failed,
    notify_canary_promoted,
    notify_scenarios_generated,
    notify_spec_start,
    notify_escalated,
    notify_failed,
    notify_flag_killed,
    notify_rollback,
    notify_rollback_failed,
    notify_ship_review,
    notify_shipped,
)
from darkfactory.pipeline.agents import invoke_agent
from darkfactory.pipeline.cleanup import CleanupManager
from darkfactory.pipeline.iteration import run_iteration_loop
from darkfactory.pipeline.quality_gate import SmokeTestResult, run_smoke_test
from darkfactory.scenarios.compat import check_format_compatibility
from darkfactory.scenarios.validator import validate_scenarios
from darkfactory.setup.workspace_check import check_workspace_scripts
from darkfactory.scenarios.detection import (
    get_sentinel_path,
    has_scenario_files,
    has_sentinel_file,
    validate_sentinel,
    validate_spec_result,
)
from darkfactory.state.registry import register as registry_register
from darkfactory.state.store import read_state, write_state, write_state_or_warn
from darkfactory.utils.hash import sha256_file

log = logging.getLogger(__name__)


def run_pipeline(
    spec_name: str,
    config: PipelineConfig,
    project_dir: Path,
    *,
    resume: bool = False,
    fresh: bool = False,
    regen_scenarios: bool = False,
    run_regression_on_ship: bool = False,
    events_file: Path | None = None,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
    stop_after_step: int | None = None,
) -> int:
    """Run the full 7-step pipeline for a spec.

    Returns exit code: 0=shipped, 1=failed, 2=escalated, 3=aborted.

    stop_after_step: if set, halt after completing this step number and
    return 0. State is NOT advanced, so subsequent calls resume normally.
    """
    def _progress(msg: str) -> None:
        if progress is not None:
            progress(msg)

    validate_spec_name(spec_name)

    # Resolve paths.
    spec_file = project_dir / ".dark-factory" / "specs" / f"{spec_name}.md"
    if not spec_file.exists():
        raise DarkFactoryError(f"spec file not found: {spec_file}")

    # Resolve per-spec profile and field overrides.
    from darkfactory.config.profiles import resolve_profile
    config = resolve_profile(spec_file, config)

    state_dir = project_dir / ".dark-factory" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    audit_dir = project_dir / ".dark-factory" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / ".dark-factory" / "reviews").mkdir(parents=True, exist_ok=True)
    (project_dir / ".dark-factory" / "reports").mkdir(parents=True, exist_ok=True)

    # Create spec run.
    spec_run = SpecRun(spec_name=spec_name)
    state_file = state_dir / f"{spec_name}.state"

    # Spec hash for change detection.
    spec_run.spec_hash = sha256_file(spec_file)

    if dry_run:
        _progress(f"[dry-run] Simulating pipeline for {spec_name}")

    # Auto-resume detection: if state file exists and status is interrupted/running,
    # resume unless --fresh was passed.
    if not fresh and state_file.exists():
        resume = _try_auto_resume(spec_run, state_file, resume, progress)

    audit_path = audit_dir / f"{spec_name}-{spec_run.run_id}.log"

    # When running validation-only, always re-run step 1 regardless of state.
    if stop_after_step == 1:
        spec_run.completed_step = 0

    # Configure barriers.
    barriers = configure_barriers(config.source_dirs)

    # Docker pre-flight.
    if not dry_run:
        require_docker()

    # Cleanup manager.
    cleanup = CleanupManager(
        run_id=spec_run.run_id,
        dry_run=dry_run,
        deploy_teardown_cmd=config.deploy_teardown_cmd,
        on_interrupt=lambda: _write_interrupted_state(state_file, spec_run),
    )
    cleanup.register()

    # Setup Docker networks and proxies.
    if not dry_run:
        setup_networks(spec_run.run_id)
        setup_proxies(spec_run.run_id, dry_run=dry_run)

    # PR preflight.
    if config.auto_pr and not dry_run:
        git_ops.preflight(config, project_dir)

    # Write initial state.
    if not resume:
        write_state(state_file, "spec", spec_name)
        write_state(state_file, "run_id", spec_run.run_id)
        write_state(state_file, "spec_hash", spec_run.spec_hash)
    write_state(state_file, "status", "running")
    spec_run.status = PipelineStatus.RUNNING

    if events_file:
        emit_spec_start(events_file, spec=spec_name, run_id=spec_run.run_id)
    notify_spec_start(config, spec_name, spec_run.run_id, spec_run.iteration)

    # Dynamic autonomy — stored on SpecRun, not mutated on config.
    spec_run.effective_autonomy = config.autonomy_level
    if config.dynamic_autonomy and events_file and config.enterprise_enabled:
        from darkfactory.intelligence.trust import calculate_trust, trust_to_autonomy
        trust = calculate_trust(events_file, config)
        autonomy = trust_to_autonomy(trust.total)
        spec_run.trust_score = trust.total
        if autonomy < config.autonomy_level:
            log.info("dynamic autonomy: lowering from %d to %d (trust=%d)",
                     config.autonomy_level, autonomy, trust.total)
            spec_run.effective_autonomy = autonomy

        emit_trust_scored(
            events_file, spec=spec_name, run_id=spec_run.run_id,
            trust=trust.total, autonomy=spec_run.effective_autonomy,
        )

    # Dynamic iterations — predict from history.
    if config.dynamic_iterations and events_file and config.enterprise_enabled:
        from darkfactory.intelligence.trust import predict_iterations
        predicted = predict_iterations(events_file, config)
        spec_run.effective_max_iterations = min(predicted + 1, config.max_iterations)

    # Metrics.
    metrics_file = project_dir / ".dark-factory" / "metrics.jsonl"
    record_start(metrics_file, spec_name, spec_run.run_id)

    try:
        # Step 0.5: Spec sanitization (pre-validation).
        _progress("Checking spec...")
        if config.spec_sanitization_enabled and spec_run.completed_step < 1:
            from darkfactory.audit.sanitizer import sanitize_spec
            spec_text = spec_file.read_text()
            san_result = sanitize_spec(spec_text)
            if not san_result.clean:
                findings_summary = "; ".join(
                    f"{f.pattern_name} (line {f.line_number})" for f in san_result.findings
                    if f.severity.value == "high"
                )
                raise DarkFactoryError(
                    f"spec sanitization failed — high-severity injection detected: {findings_summary}"
                )
            elif san_result.findings:
                log.warning("spec sanitization: %d low/medium findings (proceeding)",
                            len(san_result.findings))

        # Step 0.75: Deterministic spec pre-check.
        if spec_run.completed_step < 1:
            from darkfactory.spec.precheck import run_precheck
            precheck_result = run_precheck(spec_file)
            if not precheck_result.passed:
                raise DarkFactoryError(
                    f"spec pre-check failed:\n{precheck_result.summary}"
                )
            elif precheck_result.ambiguities:
                log.warning("spec pre-check: %d ambiguity warnings (proceeding)",
                            len(precheck_result.ambiguities))

        # Step 1: Spec validation.
        if spec_run.completed_step < 1:
            _progress("Validating spec...")
            _step_spec_validation(
                spec_run, config, barriers, project_dir,
                events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            )
            write_state_or_warn(state_file, "completed_step", "1")
            spec_run.completed_step = 1
            _progress("Spec validation passed")

        if stop_after_step == 1:
            return 0

        # Step 2: Scenario generation.
        scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_run.spec_name
        if spec_run.completed_step < 2:
            _progress("Generating blind test scenarios...")
            _step_scenario_generation(
                spec_run, config, barriers, project_dir,
                regen=regen_scenarios,
                events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            )
            write_state_or_warn(state_file, "completed_step", "2")
            spec_run.completed_step = 2
            _progress("Scenarios generated")
            notify_scenarios_generated(config, spec_name, spec_run.run_id)

        # Step 2.5: Scenario format compatibility check.
        # This check runs every time scenarios are present and the iteration loop has not started.
        # It does NOT advance completed_step — it is a guard, not a pipeline stage.
        # On resume, if completed_step >= 3, the check is skipped (iteration loop already started).
        if spec_run.completed_step < 3:
            compat = check_format_compatibility(scenarios_dir, config)
            for warning in compat.warnings:
                log.warning("scenario compat: %s", warning)
            if compat.hard_errors:
                # Do NOT advance completed_step — leave state at 2 so regen can fix the problem.
                raise DarkFactoryError(
                    "Scenario format incompatibility detected — fix before implementation:\n"
                    + "\n".join(f"  \u2022 {e}" for e in compat.hard_errors)
                    + "\n\nOptions: (1) Regenerate scenarios with correct format, "
                    + "(2) Set scenario_test_cmd in config to provide a custom runner, "
                    + "(3) Add SCENARIOS-NOT-APPLICABLE.md to opt out."
                )

        # Step 2.6: Workspace script consistency pre-flight.
        # Runs before the iteration loop to catch missing required scripts before
        # consuming any iteration budget. No container required.
        # Uses completed_step < 3 (same boundary as the compat check) so this
        # re-runs on resume from completed_step == 2.
        if spec_run.completed_step < 3:
            ws_result = check_workspace_scripts(project_dir)
            for warning in ws_result.warnings:
                log.warning("workspace check: %s", warning)
            if not ws_result.passed:
                lines = [
                    f"  {pkg}: missing {', '.join(repr(s) for s in scripts)}"
                    for pkg, scripts in ws_result.errors
                ]
                raise DarkFactoryError(
                    "Workspace consistency check failed — fix before running the pipeline:\n"
                    + "\n".join(lines)
                    + "\n\nAdd the missing scripts to each package.json listed above."
                )

        # Scenario smoke test guard (P3): run scenarios pre-implementation
        # to catch crash-type scenario bugs before the iteration loop.
        # Does NOT advance completed_step (it's a guard, not a pipeline stage).
        if (
            spec_run.completed_step < 3
            and config.scenario_smoke_test
            and config.validate_scenarios != "off"
        ):
            if has_scenario_files(scenarios_dir) and not has_sentinel_file(scenarios_dir):
                _progress("Running scenario smoke test...")
                smoke_result = _step_smoke_test(
                    spec_run, config, project_dir, dry_run=dry_run,
                )
                if smoke_result.crashes > 0 or smoke_result.tautological_passes > 0:
                    _progress(
                        f"  Smoke test: {smoke_result.crashes} crash(es), "
                        f"{smoke_result.tautological_passes} tautological — triggering SA repair"
                    )
                    _step_scenario_repair(
                        spec_run, config, barriers, project_dir,
                        smoke_result=smoke_result,
                        events_file=events_file, audit_path=audit_path,
                        dry_run=dry_run,
                    )
                else:
                    _progress("  Smoke test: all scenarios fail cleanly (healthy)")

                # Write smoke test summary for the Oracle to read.
                smoke_report_path = (
                    project_dir / ".dark-factory" / "reports"
                    / f"{spec_run.spec_name}-smoke-test.md"
                )
                if not smoke_report_path.exists():
                    smoke_report_path.parent.mkdir(parents=True, exist_ok=True)
                    smoke_report_path.write_text(
                        f"# Smoke Test Summary\n\n"
                        f"Crashes: {smoke_result.crashes}\n"
                        f"Assertion failures: {smoke_result.assertion_failures}\n"
                        f"Tautological passes: {smoke_result.tautological_passes}\n"
                    )

        # Resume from hold: oracle already said SHIP, human confirmed.
        # Skip the iteration loop and go straight to ship.
        if spec_run.completed_step >= 6:
            # Human confirmed the hold — elevate autonomy so hold
            # does not re-trigger if _handle_ship somehow re-enters.
            spec_run.effective_autonomy = max(spec_run.effective_autonomy, 2)
            _progress(f"Completing ship for {spec_name}...")
            return _handle_ship(
                spec_run, config, project_dir,
                state_file=state_file, events_file=events_file,
                metrics_file=metrics_file,
                run_regression_on_ship=run_regression_on_ship,
                dry_run=dry_run,
                cleanup_manager=cleanup,
                progress=progress,
            )

        # Steps 3-6: Iteration loop.
        max_iters = spec_run.effective_max_iterations or config.max_iterations
        _progress(f"Starting iteration loop (max {max_iters} iterations)")
        shipped = run_iteration_loop(
            spec_run, config, barriers, project_dir,
            events_file=events_file, audit_path=audit_path,
            state_file=state_file, dry_run=dry_run,
            progress=progress,
        )

        if shipped:
            return _handle_ship(
                spec_run, config, project_dir,
                state_file=state_file, events_file=events_file,
                metrics_file=metrics_file,
                run_regression_on_ship=run_regression_on_ship,
                dry_run=dry_run,
                cleanup_manager=cleanup,
                progress=progress,
            )
        else:
            return _handle_escalation(
                spec_run, config, barriers, project_dir,
                state_file=state_file, events_file=events_file,
                audit_path=audit_path, metrics_file=metrics_file,
                dry_run=dry_run,
                progress=progress,
            )

    except HumanHoldRequired:
        spec_run.status = PipelineStatus.HOLD
        write_state(state_file, "status", "hold")
        write_state_or_warn(state_file, "completed_step", "6")
        _progress(f"HOLD: {spec_name} — awaiting human confirmation")
        return 4

    except (HardAbort, BudgetExceeded) as e:
        return _handle_abort(
            spec_run, config, project_dir, str(e),
            state_file=state_file, events_file=events_file,
            metrics_file=metrics_file,
            progress=progress,
        )

    finally:
        cleanup.cleanup()


# --- Pipeline steps ---

def _step_spec_validation(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 1: Validate the spec."""
    prompt = (
        f"Validate the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md. "
        f"Write the full validation report to /project/.dark-factory/reviews/{spec_run.spec_name}-validation.md. "
        f"Write the machine-readable status file to /project/.dark-factory/reviews/{spec_run.spec_name}-validation.status "
        f"using EXACTLY this format (lowercase keys, one per line, no comments, no extra fields):\n"
        f"verdict=PASS\n"
        f"score=<integer 0-100>\n"
        f"divergence=<LOW|MEDIUM|HIGH>\n"
        f"blockers=<integer count of Critical Issues that must be fixed before implementation>"
    )

    result = invoke_agent(
        AgentRole.SPEC_VALIDATOR, prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path, dry_run=dry_run,
    )

    if result.exit_code != 0:
        raise DarkFactoryError(f"spec validation failed (exit {result.exit_code})")

    # Verify validation result.
    reviews_dir = project_dir / ".dark-factory" / "reviews"
    passed, reason = validate_spec_result(reviews_dir, spec_run.spec_name)
    if not passed:
        raise DarkFactoryError(f"spec validation failed: {reason}")



def _step_scenario_generation(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    regen: bool = False,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 2: Generate blind test scenarios."""
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_run.spec_name

    if regen and scenarios_dir.exists():
        if has_sentinel_file(scenarios_dir):
            log.warning(
                "regen=True will destroy the SCENARIOS-NOT-APPLICABLE.md sentinel in %s — "
                "the Scenario Architect must re-justify non-applicability",
                scenarios_dir,
            )
        shutil.rmtree(scenarios_dir)
        log.info("regenerating scenarios")

    # Sentinel check comes first — a sentinel-only directory has no recognized test
    # file patterns but is a valid and complete output from the Scenario Architect.
    if has_sentinel_file(scenarios_dir):
        valid, objections = validate_sentinel(get_sentinel_path(scenarios_dir))
        if not valid:
            raise DarkFactoryError(
                "Scenario sentinel rejected — fix the justification or write real "
                f"scenario files:\n{objections}"
            )
        log.info("scenarios sentinel validated, skipping scenario generation")
        return

    if has_scenario_files(scenarios_dir):
        log.info("scenarios already exist, skipping generation")
        return

    scenarios_dir.mkdir(parents=True, exist_ok=True)

    prompt = (
        f"Read your agent definition at /project/.dark-factory/agents/scenario-architect.md. "
        f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md. "
        f"Generate blind test scenarios following the decision tree in your agent definition. "
        f"Write output to /project/.dark-factory/scenarios/{spec_run.spec_name}/. "
        f"Write a scenario report to /project/.dark-factory/reports/{spec_run.spec_name}-scenarios.md"
    )

    # Build execution context block to inject into the Scenario Architect's prompt.
    context_block = "\n\n## Execution Context\n\n"
    context_block += f"TOOLCHAIN: {config.language or 'unknown'}\n"
    context_block += f"HAS_TEST_SERVER_CMD: {'true' if config.test_server_cmd else 'false'}\n"
    context_block += f"HAS_BROWSER_SERVER_CMD: {'true' if config.browser_server_cmd else 'false'}\n"
    context_block += f"HAS_SCENARIO_TEST_CMD: {'true' if config.scenario_test_cmd else 'false'}\n"
    context_block += "\n"
    context_block += "Apply the Execution Context Constraints from your agent definition before choosing any scenario format.\n"
    prompt = prompt + context_block

    result = invoke_agent(
        AgentRole.SCENARIO_ARCHITECT, prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path, dry_run=dry_run,
    )

    if result.exit_code != 0:
        raise DarkFactoryError(f"scenario generation failed (exit {result.exit_code})")

    # Validate the output: the Architect may write real scenario files or a sentinel.
    if has_sentinel_file(scenarios_dir):
        valid, objections = validate_sentinel(get_sentinel_path(scenarios_dir))
        if not valid:
            raise DarkFactoryError(
                "Scenario Architect wrote a sentinel but it was rejected — "
                f"fix the justification or write real scenario files:\n{objections}"
            )
        log.info("scenarios sentinel written and validated")
    elif not has_scenario_files(scenarios_dir):
        raise DarkFactoryError("scenario generation succeeded but no scenario files were written")


# --- Scenario smoke test (P3) ---


def _step_smoke_test(
    spec_run: SpecRun,
    config: PipelineConfig,
    project_dir: Path,
    *,
    dry_run: bool = False,
) -> SmokeTestResult:
    """Run scenarios pre-implementation to catch scenario bugs early.

    Returns SmokeTestResult. Infrastructure failures are soft — return
    an empty result rather than raising.
    """
    try:
        return run_smoke_test(
            spec_run.spec_name, config, spec_run.run_id, project_dir,
            dry_run=dry_run,
        )
    except Exception:
        log.warning("smoke test failed (infrastructure error), skipping", exc_info=True)
        return SmokeTestResult()


def _step_scenario_repair(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    smoke_result: SmokeTestResult,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Re-invoke the Scenario Architect to repair crashing/tautological scenarios.

    Max 1 repair attempt. The SA sees its own scenario files + sanitized crash
    output but CANNOT see source code (barrier preserved via tmpfs masking).

    Post-repair mechanical validation: snapshot before repair, verify after
    repair that only crash-failing files were modified, assertion counts did
    not decrease, and no test functions were deleted. Revert on failure.
    """
    import shutil

    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_run.spec_name
    snapshot_dir = project_dir / ".dark-factory" / "scenarios" / f"{spec_run.spec_name}-pre-repair"

    # Snapshot scenario files before repair.
    if snapshot_dir.exists():
        shutil.rmtree(snapshot_dir)
    shutil.copytree(scenarios_dir, snapshot_dir)

    # Build repair prompt with sanitized crash output (allowlist only).
    crash_info = smoke_result.sanitized_crash_output or "(no crash details)"
    tautological_list = "\n".join(smoke_result.tautological_files) if smoke_result.tautological_files else "(none)"

    prompt = (
        f"Read your agent definition at /project/.dark-factory/agents/scenario-architect.md. "
        f"You are in REPAIR MODE. Your previously generated scenarios have bugs.\n\n"
        f"## Crash Output (sanitized)\n```\n{crash_info[:6000]}\n```\n\n"
        f"## Tautological Tests (passed before implementation exists)\n"
        f"{tautological_list}\n\n"
        f"Fix the crashing scenarios in /project/.dark-factory/scenarios/{spec_run.spec_name}/. "
        f"Follow the Self-Repair Mode instructions in your agent definition."
    )

    try:
        invoke_agent(
            AgentRole.SCENARIO_ARCHITECT, prompt,
            config=config, barriers=barriers,
            run_id=spec_run.run_id, project_dir=project_dir,
            spec_name=spec_run.spec_name, iteration=0,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        )
    except Exception:
        log.warning("scenario repair failed, reverting to original scenarios", exc_info=True)
        shutil.rmtree(scenarios_dir)
        shutil.move(str(snapshot_dir), str(scenarios_dir))
        return

    # Post-repair mechanical validation.
    if not _validate_repair(scenarios_dir, snapshot_dir, config.language):
        log.warning("scenario repair failed validation, reverting to original scenarios")
        shutil.rmtree(scenarios_dir)
        shutil.move(str(snapshot_dir), str(scenarios_dir))
        return

    # Repair passed validation — clean up snapshot.
    shutil.rmtree(snapshot_dir, ignore_errors=True)
    log.info("scenario repair completed and validated")


def _validate_repair(
    repaired_dir: Path,
    snapshot_dir: Path,
    language: str,
    crash_files: set[str] | None = None,
) -> bool:
    """Mechanical post-repair validation (anti-cheat).

    Returns True if repair is valid, False if it should be reverted.
    Checks:
    1. Only files that had CRASH-type failures were modified (if crash_files provided)
    2. Total assertion count per file did not decrease
    3. No test functions were deleted
    4. No new files were created during repair
    """
    import re

    # Count assertions and test functions in a file.
    if language in ("node", ""):
        assertion_re = re.compile(r"expect\(")
        test_fn_re = re.compile(r"""(?:it|test)\s*\(""")
    elif language == "python":
        assertion_re = re.compile(r"(?:assert |self\.assert|pytest\.raises)")
        test_fn_re = re.compile(r"def test_")
    elif language == "go":
        assertion_re = re.compile(r"(?:t\.Error|require\.|assert\.)")
        test_fn_re = re.compile(r"func Test")
    elif language == "rust":
        assertion_re = re.compile(r"(?:assert!|assert_eq!)")
        test_fn_re = re.compile(r"fn test_")
    else:
        assertion_re = re.compile(r"expect\(")
        test_fn_re = re.compile(r"""(?:it|test)\s*\(""")

    snapshot_files = {f.name for f in snapshot_dir.iterdir() if f.is_file()}
    repaired_files = {f.name for f in repaired_dir.iterdir() if f.is_file()}

    # Check 4: Reject new files created during repair.
    new_files = repaired_files - snapshot_files
    if new_files:
        log.warning("repair created new files %s — reverting (repair must only fix existing files)", new_files)
        return False

    for snapshot_file in snapshot_dir.iterdir():
        if not snapshot_file.is_file():
            continue
        repaired_file = repaired_dir / snapshot_file.name
        if not repaired_file.exists():
            log.warning("repair deleted file %s — reverting", snapshot_file.name)
            return False

        old_content = snapshot_file.read_text()
        new_content = repaired_file.read_text()

        # Check 1: Only crash-failing files should be modified.
        if old_content != new_content and crash_files and snapshot_file.name not in crash_files:
            log.warning(
                "repair modified non-crashing file %s — reverting",
                snapshot_file.name,
            )
            return False

        # Check 2: Assertion count must not decrease.
        old_assertions = len(assertion_re.findall(old_content))
        new_assertions = len(assertion_re.findall(new_content))
        if new_assertions < old_assertions:
            log.warning(
                "repair reduced assertions in %s (%d → %d) — reverting",
                snapshot_file.name, old_assertions, new_assertions,
            )
            return False

        # Check 3: Test function count must not decrease.
        old_tests = len(test_fn_re.findall(old_content))
        new_tests = len(test_fn_re.findall(new_content))
        if new_tests < old_tests:
            log.warning(
                "repair deleted test functions in %s (%d → %d) — reverting",
                snapshot_file.name, old_tests, new_tests,
            )
            return False

    return True


# --- Post-pipeline handlers ---

def _handle_ship(
    spec_run: SpecRun,
    config: PipelineConfig,
    project_dir: Path,
    *,
    state_file: Path,
    events_file: Path | None = None,
    metrics_file: Path | None = None,
    run_regression_on_ship: bool = False,
    dry_run: bool = False,
    cleanup_manager: CleanupManager | None = None,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Handle SHIP decision — registry, regression, git, deployment, notification.

    Flow when canary_enabled:
      1. Registry + regression
      2. Create branch + PR (NOT merged yet)
      3. Run post-ship pipeline (provision → migration check → flag → canary)
      4. On canary success: merge
      5. On canary failure: close PR, mark ROLLED_BACK
    """
    def _progress(msg: str) -> None:
        if progress is not None:
            progress(msg)

    spec_run.status = PipelineStatus.SHIPPED

    # Register scenarios.
    registry_register(project_dir, spec_run.spec_name)

    # Regression testing — mandatory when trust-derived autonomy < 3.
    trust_requires_regression = spec_run.effective_autonomy < 3
    if (run_regression_on_ship or config.regression_on_ship or trust_requires_regression) and config.enterprise_enabled:
        from darkfactory.scenarios.regression import run_regression
        _progress("Running regression tests...")
        passed, failed, total = run_regression(
            config, project_dir,
            exclude=[spec_run.spec_name],
            events_file=events_file,
            run_id=spec_run.run_id,
        )
        if failed > 0 and config.regression_policy == "block":
            log.warning("regression failed (%d/%d), blocking ship", failed, total)
            write_state(state_file, "status", "regression-failed")
            return 2

    # Git operations: create branch + PR.
    if config.auto_pr and not dry_run:
        _progress("Creating branch and PR...")
        branch = git_ops.create_branch(
            spec_run.spec_name, spec_run.run_id, config, project_dir,
        )
        git_ops.commit_changes(
            spec_run.spec_name, config.source_dirs, config.test_dir,
            project_dir,
        )
        git_ops.push_branch(branch, config, project_dir)

        last_iter = spec_run.current_iteration
        git_ops.create_pr(
            spec_run.spec_name, config, project_dir,
            run_id=spec_run.run_id,
            satisfaction_score=last_iter.satisfaction_score if last_iter else 0,
            trust_score=spec_run.trust_score,
        )

        # Post-ship deployment pipeline (pre-merge canary).
        if config.canary_enabled and not dry_run:
            _progress("Starting post-ship pipeline...")
            deploy_ok = post_ship_pipeline(
                spec_run, config, project_dir,
                state_file=state_file,
                events_file=events_file,
                cleanup_manager=cleanup_manager,
            )
            if not deploy_ok:
                # Canary failed — close PR, do not merge.
                git_ops.close_pr(
                    project_dir,
                    comment=f"Canary failed: {spec_run.deployment_result.rollback_reason if spec_run.deployment_result else 'unknown'}",
                    branch=branch,
                )
                spec_run.status = PipelineStatus.ROLLED_BACK
                write_state(state_file, "status", "rolled_back")
                if events_file:
                    emit_deploy_complete(
                        events_file, spec=spec_run.spec_name,
                        run_id=spec_run.run_id, status="rolled_back",
                    )
                notify_canary_failed(
                    config, spec_run.spec_name, spec_run.run_id,
                    spec_run.deployment_result.rollback_reason if spec_run.deployment_result else "canary failed",
                )
                _progress(f"ROLLED BACK: {spec_run.spec_name}")
                return 1

        # Canary passed (or not enabled) — proceed to merge.
        if config.merge_queue:
            git_ops.merge_queue_add(project_dir)
        if config.auto_merge_high_trust:
            git_ops.auto_merge_high_trust(project_dir, spec_run.trust_score)

    # Post-merge rollback monitor.
    if config.rollback_monitor_cmd and events_file and not dry_run and config.enterprise_enabled:
        from darkfactory.deployment.monitor import monitor_rollback_window
        monitor_ok = monitor_rollback_window(
            config, spec_run.spec_name, spec_run.run_id,
            events_file, str(project_dir),
        )
        if not monitor_ok:
            spec_run.status = PipelineStatus.ROLLED_BACK
            write_state(state_file, "status", "rolled_back")
            notify_rollback(config, spec_run.spec_name, spec_run.run_id,
                            "monitor", "post-merge monitor breach")
            _progress(f"ROLLED BACK: {spec_run.spec_name}")
            return 1

    # State and events.
    final_status = "deployed" if config.canary_enabled else "shipped"
    if config.canary_enabled:
        spec_run.status = PipelineStatus.DEPLOYED
    write_state(state_file, "status", final_status)
    write_state_or_warn(state_file, "completed_step", "99")

    if events_file:
        last_iter = spec_run.current_iteration
        emit_spec_shipped(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            iterations=len(spec_run.iterations),
            satisfaction=last_iter.satisfaction_score if last_iter else 0,
        )
        emit_cost_summary(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            total_turns=spec_run.total_turns,
            estimated_usd=estimate_cost(spec_run.total_turns, config).estimated_usd,
        )
        if config.canary_enabled:
            emit_deploy_complete(
                events_file, spec=spec_run.spec_name,
                run_id=spec_run.run_id, status="promoted",
            )

    if metrics_file:
        record_end(
            metrics_file, spec_run.spec_name, spec_run.run_id, final_status,
            spec_run.iteration - 1, spec_run.total_turns, spec_run.wall_elapsed,
        )

    notify_shipped(config, spec_run.spec_name, spec_run.run_id,
                   len(spec_run.iterations), spec_run.trust_score)
    if config.canary_enabled and spec_run.deployment_result:
        notify_canary_promoted(
            config, spec_run.spec_name, spec_run.run_id,
            spec_run.deployment_result.stages_completed,
        )
    # Autonomy 1 only reaches here after human confirmation (elevated to 2).
    # Autonomy 2 reaches here directly. Either way, flag for review.
    if spec_run.effective_autonomy <= 2:
        notify_ship_review(config, spec_run.spec_name, spec_run.run_id,
                           spec_run.trust_score)
    _progress(f"SHIPPED: {spec_run.spec_name}")
    return 0


def post_ship_pipeline(
    spec_run: SpecRun,
    config: PipelineConfig,
    project_dir: Path,
    *,
    state_file: Path,
    events_file: Path | None = None,
    cleanup_manager: CleanupManager | None = None,
) -> bool:
    """Run the post-ship deployment pipeline (pre-merge canary).

    Steps:
      100. Ephemeral environment provisioning
      101. Migration safety check
      102. Feature flag registration
      103. Canary deployment (staged traffic shifting + metric gates)
      104. Ephemeral environment teardown

    Returns True if deployment succeeded, False if it failed/rolled back.
    """
    if not config.enterprise_enabled:
        log.info("deployment skipped — enterprise features disabled")
        return True

    from darkfactory.deployment.environment import health_check, provision, teardown  # noqa: F811
    from darkfactory.deployment.feature_flags import create_flag
    from darkfactory.deployment.migration_check import check_migrations
    from darkfactory.deployment.models import DeploymentResult, DeploymentStatus, RollbackLayer
    from darkfactory.deployment.progressive import run_canary
    from darkfactory.deployment.rollback import rollback

    spec_run.status = PipelineStatus.DEPLOYING
    write_state(state_file, "status", "deploying")
    project_dir_str = str(project_dir)

    # Restore compose_project from state on resume, or initialize empty.
    compose_project = read_state(state_file, "compose_project") or ""

    try:
        # Step 100: Provision ephemeral environment.
        if spec_run.deployment_step < 100:
            env_info = provision(config, project_dir_str, spec_run.spec_name, spec_run.run_id)
            if env_info:
                compose_project = env_info.compose_project or ""
                if compose_project:
                    write_state_or_warn(state_file, "compose_project", compose_project)
                elif not config.deploy_teardown_cmd:
                    log.warning("legacy deploy with no teardown cmd — environment may leak on failure")
                # Health check.
                base_url = env_info.base_url or config.deploy_base_url
                if base_url:
                    healthy = health_check(
                        base_url,
                        endpoint=config.deploy_health_endpoint,
                        timeout=config.deploy_health_timeout,
                    )
                    if not healthy:
                        log.warning("health check failed — aborting deployment")
                        spec_run.deployment_result = DeploymentResult(
                            status=DeploymentStatus.FAILED,
                            stages_completed=0, stages_total=0,
                            error="health check failed",
                        )
                        return False

            spec_run.deployment_step = 100
            write_state_or_warn(state_file, "deployment_step", "100")

        # Step 101: Migration safety check.
        if spec_run.deployment_step < 101:
            if config.migration_check_enabled:
                mig_result = check_migrations(
                    project_dir_str,
                    config.migration_dirs,
                    base_branch=config.git_base_branch,
                    extra_patterns=config.migration_extra_patterns or None,
                )
                if not mig_result.safe and config.migration_check_policy == "block":
                    log.warning("migration safety check failed — blocking deployment")
                    spec_run.deployment_result = DeploymentResult(
                        status=DeploymentStatus.FAILED,
                        stages_completed=0, stages_total=0,
                        error="mixed migration detected (expand-contract violation)",
                    )
                    return False
                elif not mig_result.safe:
                    log.warning("migration safety check: mixed migration detected (warn mode)")

            spec_run.deployment_step = 101
            write_state_or_warn(state_file, "deployment_step", "101")

        # Step 102: Feature flag registration.
        if spec_run.deployment_step < 102:
            if config.flag_create_cmd:
                flag_ok = create_flag(config, spec_run.spec_name, project_dir_str)
                if not flag_ok:
                    log.warning("flag creation failed — continuing (non-blocking)")

            spec_run.deployment_step = 102
            write_state_or_warn(state_file, "deployment_step", "102")

        # Step 103: Canary deployment.
        if spec_run.deployment_step < 103:
            if not events_file:
                log.warning("no events_file — skipping canary (events required)")
                spec_run.deployment_result = DeploymentResult(
                    status=DeploymentStatus.FAILED,
                    stages_completed=0, stages_total=0,
                    error="events_file required for canary",
                )
                return False

            canary_result = run_canary(
                config,
                spec_run.spec_name,
                spec_run.run_id,
                config.canary_stages or None,
                config.canary_metrics or [],
                events_file,
                project_dir_str,
                cleanup_manager=cleanup_manager,
            )
            spec_run.deployment_result = canary_result

            if canary_result.status == DeploymentStatus.PROMOTED:
                log.info("canary promoted — deployment successful")
            else:
                reason = canary_result.rollback_reason or canary_result.error
                log.warning("canary failed: %s", reason)
                # Run rollback cascade (pre-merge, so no L4 git revert).
                if events_file:
                    rb_layer = rollback(
                        config, spec_run.spec_name, spec_run.run_id,
                        reason=reason,
                        start_layer=RollbackLayer.FLAG_KILL,
                        project_dir=project_dir_str,
                        events_file=events_file,
                        post_merge=False,
                    )
                    if rb_layer:
                        notify_rollback(config, spec_run.spec_name, spec_run.run_id,
                                        rb_layer.value, reason)
                        if rb_layer == RollbackLayer.FLAG_KILL:
                            notify_flag_killed(config, spec_run.spec_name, spec_run.run_id)
                    else:
                        notify_rollback_failed(config, spec_run.spec_name, spec_run.run_id, reason)
                return False

            spec_run.deployment_step = 103
            write_state_or_warn(state_file, "deployment_step", "103")

        return True

    finally:
        # Step 104: Always teardown ephemeral environment.
        if compose_project or config.deploy_teardown_cmd:
            try:
                teardown(
                    config, project_dir_str,
                    compose_project=compose_project or None,
                    run_id=spec_run.run_id,
                )
            except Exception as e:
                log.warning("deployment teardown failed: %s", e)


def _handle_escalation(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    state_file: Path,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    metrics_file: Path | None = None,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Handle escalation — max iterations reached without SHIP."""
    def _progress(msg: str) -> None:
        if progress is not None:
            progress(msg)

    spec_run.status = PipelineStatus.ESCALATED
    _progress("Running escalation agent...")

    # Run escalation agent (project is :ro — orchestrator writes the file).
    prompt = (
        f"Write an escalation report for {spec_run.spec_name}. "
        f"The pipeline reached {spec_run.iteration - 1} iterations without shipping. "
        f"Output the full report to stdout."
    )
    esc_result = invoke_agent(
        AgentRole.ESCALATION, prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=spec_run.iteration,
        events_file=events_file, audit_path=audit_path,
        min_output=0, dry_run=dry_run,
    )
    if esc_result.output and not dry_run:
        esc_file = project_dir / ".dark-factory" / "reviews" / f"{spec_run.spec_name}-escalation.md"
        esc_file.parent.mkdir(parents=True, exist_ok=True)
        esc_file.write_text(esc_result.output)

    write_state(state_file, "status", "escalated")

    if events_file:
        emit_spec_escalated(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            iterations=len(spec_run.iterations),
        )

    if metrics_file:
        record_end(
            metrics_file, spec_run.spec_name, spec_run.run_id, "escalated",
            spec_run.iteration - 1, spec_run.total_turns, spec_run.wall_elapsed,
        )

    notify_escalated(config, spec_run.spec_name, spec_run.run_id,
                     len(spec_run.iterations))
    _progress(f"ESCALATED: {spec_run.spec_name} (after {len(spec_run.iterations)} iterations)")
    return 2


def _handle_abort(
    spec_run: SpecRun,
    config: PipelineConfig,
    project_dir: Path,
    reason: str,
    *,
    state_file: Path,
    events_file: Path | None = None,
    metrics_file: Path | None = None,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Handle abort — budget or hard abort."""
    spec_run.status = PipelineStatus.ABORTED
    spec_run.abort_reason = reason

    write_state(state_file, "status", "failed")
    write_state_or_warn(state_file, "abort_reason", reason)

    if events_file:
        emit_spec_failed(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            reason=reason,
        )

    if metrics_file:
        record_end(
            metrics_file, spec_run.spec_name, spec_run.run_id, "aborted",
            spec_run.iteration - 1, spec_run.total_turns, spec_run.wall_elapsed,
        )

    notify_failed(config, spec_run.spec_name, spec_run.run_id, reason)
    if progress is not None:
        progress(f"ABORTED: {spec_run.spec_name} — {reason}")
    return 3


# --- Helpers ---

def _try_auto_resume(
    spec_run: SpecRun,
    state_file: Path,
    explicit_resume: bool,
    progress: Callable[[str], None] | None = None,
) -> bool:
    """Auto-resume from interrupted state if present, or if --resume was explicit.

    Returns True if state was loaded (resume should proceed), False otherwise.
    """
    def _progress(msg: str) -> None:
        if progress is not None:
            progress(msg)

    _RESUMABLE = ("interrupted", "running", "hold", "deploying")
    _TERMINAL   = ("shipped", "deployed", "rolled_back", "regression-failed", "escalated", "failed")

    status = read_state(state_file, "status") or ""

    # Terminal states cannot be resumed — they are already done.
    if status in _TERMINAL:
        if explicit_resume:
            raise DarkFactoryError(
                f"cannot resume: previous run already finished with status '{status}'. "
                "Use --fresh to start a new run."
            )
        return False

    if status not in _RESUMABLE and not explicit_resume:
        return False

    if status == "running" and not explicit_resume:
        _progress(
            "WARNING: previous run status is 'running' — may have crashed without "
            "interrupt handler. Resuming from last known step. Use --fresh to start over."
        )
    _handle_resume(spec_run, state_file, spec_run.spec_hash)
    _progress(
        f"Resuming from step {spec_run.completed_step}, iteration {spec_run.iteration} "
        f"(use --fresh to start over)"
    )
    return True


def _handle_resume(
    spec_run: SpecRun,
    state_file: Path,
    current_spec_hash: str,
) -> None:
    """Restore state from previous run for resume."""
    stored_hash = read_state(state_file, "spec_hash")
    if stored_hash and stored_hash != current_spec_hash:
        raise DarkFactoryError(
            "spec has changed since last run — use --fresh to start over"
        )

    completed = read_state(state_file, "completed_step")
    if completed:
        spec_run.completed_step = int(completed)

    iteration = read_state(state_file, "iteration")
    if iteration:
        spec_run.iteration = int(iteration)

    deployment_step = read_state(state_file, "deployment_step")
    if deployment_step:
        spec_run.deployment_step = int(deployment_step)

    stored_run_id = read_state(state_file, "run_id")
    if stored_run_id:
        spec_run.run_id = stored_run_id



def _write_interrupted_state(state_file: Path, spec_run: SpecRun) -> None:
    """Write interrupted state for crash recovery."""
    write_state_or_warn(state_file, "status", "interrupted")
    write_state_or_warn(state_file, "iteration", str(spec_run.iteration))
    write_state_or_warn(state_file, "completed_step", str(spec_run.completed_step))
