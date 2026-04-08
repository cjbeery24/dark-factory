"""Main pipeline orchestrator — the 7-step pipeline.

Replaces scripts/dark-factory.sh. Coordinates spec validation,
scenario generation, the iteration loop, and post-pipeline actions
(registry, regression, git, notifications).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from darkfactory.spec.manifest import RuntimeDecl
import re
import shutil
from collections.abc import Callable
from urllib.parse import urlparse
from pathlib import Path

from darkfactory.config.profiles import parse_spec_type
from darkfactory.config.schema import validate_spec_name
from darkfactory.docker.barriers import BarrierConfig, configure_barriers
from darkfactory.docker.client import require_docker
from darkfactory.docker.networks import setup_networks, setup_proxies
from darkfactory.architecture.runtime import step_begin
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
from darkfactory.models.config import PipelineConfig, ServiceDef
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
from darkfactory.pipeline.agents import extract_agent_text, invoke_agent
from darkfactory.pipeline.cleanup import CleanupManager
from darkfactory.pipeline.iteration import run_iteration_loop
from darkfactory.pipeline.quality_gate import SmokeTestResult, run_smoke_test
from darkfactory.scenarios.compat import check_format_compatibility
from darkfactory.scenarios.tool_docs import TOOL_DOCS
from darkfactory.scenarios.validator import validate_scenarios
from darkfactory.setup.workspace_check import check_workspace_scripts, derive_required_scripts
from darkfactory.scenarios.detection import (
    get_sentinel_path,
    has_scenario_files,
    has_sentinel_file,
    validate_sentinel,
    validate_spec_result,
)
from darkfactory.pipeline.post_ship_archive import (
    capture_env_hash,
    record_shipped_spec,
    tag_archived_scenarios,
)
from darkfactory.state.registry import register as registry_register
from darkfactory.state.store import read_state, write_state, write_state_or_warn
from darkfactory.utils.hash import sha256_file

log = logging.getLogger(__name__)


def _count_advisory_findings(precheck_result) -> int:
    """Count all non-fatal precheck findings for the advisory log line.

    Includes the four pre-Phase-0 blocking categories (placeholders, criteria,
    shape claims, anchor intent) PLUS ambiguities. This is the user-visible
    count, so it must match the union of categories the Architect will see
    in `.dark-factory/precheck/<spec>.json`.
    """
    return (
        len(precheck_result.placeholders)
        + len(precheck_result.criteria_issues)
        + len(precheck_result.shape_claims)
        + len(precheck_result.anchor_intent_issues)
        + len(precheck_result.ambiguities)
    )


def _preflight_config_validation(
    config: PipelineConfig,
) -> None:
    """Pre-run config consistency checks. Warns on misconfigurations.

    Runs BEFORE any pipeline step and therefore before any ``step_begin``
    freshens Config from ``environment.toml``. That's intentional: these
    checks inspect the bootstrap ``config.toml`` values directly, before
    the Architect has had a chance to amend anything. Do not try to
    "migrate" these reads to `load_environment()` — at t=0 there is no
    contract to overlay, and catching bootstrap misconfigurations
    requires seeing config.toml as authored.
    """
    # PORT conflict: server_env.PORT vs test_base_url port
    if config.server_env.get("PORT") and config.test_base_url:
        parsed = urlparse(config.test_base_url)
        # Default to 443 for https, 80 for everything else (http, ws, etc.)
        url_port = str(parsed.port or (443 if parsed.scheme == "https" else 80))
        env_port = config.server_env["PORT"].strip()
        if env_port != url_port:
            log.warning(
                "PORT conflict: [test_server] env.PORT=%s but base_url port=%s. "
                "The server will use PORT=%s (from env), but Docker maps port %s. "
                "The health check will fail. Fix: make env.PORT match base_url, "
                "or remove env.PORT to use auto-injection.",
                env_port, url_port, env_port, url_port,
            )

    # Missing base_url warning
    if config.test_server_cmd and not config.test_base_url:
        log.warning(
            "test_server_cmd is set but test_base_url is empty; "
            "server will default to port 8000"
        )


def _clear_stale_scenario_counts(
    spec_name: str,
    project_dir: Path,
) -> None:
    """Remove scenario count files from prior runs to prevent stale data."""
    reports = project_dir / ".dark-factory" / "reports"
    if not reports.is_dir():
        return
    canonical = reports / f"{spec_name}-scenario-counts.env"
    if canonical.exists():
        try:
            canonical.unlink()
            log.debug("removed stale scenario counts: %s", canonical.name)
        except OSError:
            log.warning("could not remove stale scenario counts: %s", canonical)
    for frag in reports.glob(f"{spec_name}-scenario-counts-*.env"):
        try:
            frag.unlink()
            log.debug("removed stale scenario count fragment: %s", frag.name)
        except OSError:
            log.warning("could not remove stale fragment: %s", frag)


def run_pipeline(
    spec_name: str,
    config: PipelineConfig,
    project_dir: Path,
    *,
    resume: bool = False,
    fresh: bool = False,
    allow_resume: bool = True,
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

    # Phase 4.1: spec_type is required. Check before any FS side-effects so
    # a missing field doesn't leave behind half-built state/audit dirs or
    # bootstrap an environment.toml for a spec that won't run.
    parsed_spec_type = parse_spec_type(spec_file)
    if parsed_spec_type is None:
        raise DarkFactoryError(
            f"spec {spec_name!r} is missing required `spec_type` in its "
            f"dark-factory-metadata block. Allowed values: bootstrap, "
            f"feature, bugfix, refactor. Run `dark-factory specs "
            f"migrate-frontmatter --apply` to backfill existing specs."
        )
    # Phase 4.6: spec-type-specific structural gates (e.g. bugfix
    # Reproduction section). Same source of truth used by
    # `dark-factory validate`; running it here ensures the bugfix
    # Reproduction requirement is enforced on direct `dark-factory run`
    # invocations too, not just validate.
    from darkfactory.spec.frontmatter_gate import check_spec_frontmatter_gate
    _gate_ok, _gate_msg = check_spec_frontmatter_gate(spec_file)
    if not _gate_ok:
        raise DarkFactoryError(
            f"spec {spec_name!r}: {_gate_msg}"
        )

    # Resolve per-spec profile and field overrides.
    from darkfactory.config.profiles import resolve_profile
    config = resolve_profile(spec_file, config)

    # Phase 2.4: one-shot config.toml -> environment.toml migration. No-op
    # if environment.toml already exists. After this, step_begin's
    # load_environment will see a real contract instead of defaults.
    from darkfactory.architecture.backcompat import (
        BackcompatError,
        ensure_environment_bootstrap,
    )
    try:
        ensure_environment_bootstrap(config, project_dir)
    except BackcompatError as exc:
        raise RuntimeError(
            f"environment.toml bootstrap failed: {exc}. Refusing to run "
            f"on a half-migrated project — fix the underlying issue and retry."
        ) from exc

    # After backcompat synthesis, refresh config.source_dirs (and other
    # runtime-overlay fields) from the freshly-materialized environment.toml.
    # Without this, the Phase 4.5.1 preflight below sees the stale dataclass
    # default `["src"]` instead of the source_dirs the synthesizer just
    # detected and wrote into the bootstrap delta — every monorepo would
    # trip the preflight even though the env contract is correct.
    from darkfactory.architecture.runtime import (
        load_environment,
        refresh_config_from_environment,
    )
    try:
        env_for_overlay = load_environment(project_dir)
    except Exception:  # noqa: BLE001 — preflight will surface a clearer error
        env_for_overlay = None
    if env_for_overlay is not None:
        refresh_config_from_environment(config, env_for_overlay)

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

    # Phase 4.1: spec_type was validated above (before FS side-effects).
    spec_run.spec_type = parsed_spec_type

    if dry_run:
        _progress(f"[dry-run] Simulating pipeline for {spec_name}")

    # Auto-resume detection: if state file exists and status is interrupted/running,
    # resume unless --fresh was passed. Callers that cannot mutate anything
    # (e.g. `dark-factory validate`, which runs step 1 only and is read-only)
    # pass allow_resume=False so auto-resume is skipped entirely. Routing
    # read-only callers through resume detection used to trip the spec-hash-
    # mismatch guard and hard-fail on any spec edit after a prior `run`, which
    # is why `dark-factory validate` had to pass a `fresh=True` bandaid
    # (0e0a0be). Phase 2.6 removes both the bandaid and the flawed routing.
    if allow_resume and not fresh and state_file.exists():
        resume = _try_auto_resume(spec_run, state_file, resume, progress)

    audit_path = audit_dir / f"{spec_name}-{spec_run.run_id}.log"

    # When running validation-only, always re-run step 1 regardless of state.
    if stop_after_step == 1:
        spec_run.completed_step = 0

    # Phase 4.5.1: refuse to launch if source_dirs is suspect. Runs
    # BEFORE configure_barriers so a leak can never reach a container.
    from darkfactory.docker.source_dirs_preflight import (
        check_source_dirs_preflight,
    )
    check_source_dirs_preflight(project_dir, config.source_dirs)

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
        state_dir=state_dir,
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
    _preflight_config_validation(config)
    _clear_stale_scenario_counts(spec_name, project_dir)

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
        # Phase 0 of resilience rewrite: precheck is ADVISORY by default. Only
        # structural failures (parser error, oversize, empty) are fatal. All
        # content findings (placeholders, criteria, shape claims, anchor
        # coverage) are written to .dark-factory/precheck/<spec>.json and
        # mounted into the Architect container as input context.
        if spec_run.completed_step < 1:
            from darkfactory.spec.advisory import write_precheck_advisory
            from darkfactory.spec.precheck import run_precheck
            precheck_result = run_precheck(spec_file)
            # Always persist the advisory, regardless of fatal/advisory split.
            try:
                write_precheck_advisory(project_dir, spec_run.spec_name, precheck_result)
            except (OSError, TypeError) as exc:
                log.warning("failed to persist precheck advisory: %s", exc)
            if precheck_result.fatal:
                raise DarkFactoryError(
                    f"spec pre-check failed (structural):\n{precheck_result.summary}"
                )
            if not precheck_result.passed:
                log.warning(
                    "spec pre-check has %d advisory finding(s) (proceeding); "
                    "see .dark-factory/precheck/%s.json",
                    _count_advisory_findings(precheck_result),
                    spec_run.spec_name,
                )

            # Persist the anchor manifest so the post-scenario-gen coverage
            # gate can read it without re-parsing the spec. precheck stays
            # file-IO-free; the orchestrator owns the write.
            from darkfactory.spec.anchors import AnchorManifest, manifest_path
            if isinstance(precheck_result.anchor_manifest, AnchorManifest):
                try:
                    precheck_result.anchor_manifest.to_json_path(
                        manifest_path(project_dir, spec_run.spec_name)
                    )
                except OSError as exc:
                    log.warning("failed to persist anchor manifest: %s", exc)

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

        # Step 1.5: Architecture Lock (manifest generation).
        if config.architecture_lock and spec_run.completed_step < 2:
            _step_architecture_lock(
                spec_run, config, barriers, project_dir,
                fresh=fresh, regen=regen_scenarios,
                events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            )

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
        #
        # Bootstrap specs are exempt by design: the whole purpose of a bootstrap
        # spec is to *create* the tooling (lint/test scripts, package.json entries,
        # tsconfig, etc.) that this check validates. Running it on a bootstrap spec
        # is a chicken-and-egg failure mode — the check rejects the project for
        # lacking the exact scripts the implementer is about to add.
        if spec_run.completed_step < 3 and spec_run.spec_type != "bootstrap":
            ws_required = derive_required_scripts(
                lint_cmd=config.lint_cmd or "",
                typecheck_cmd=config.typecheck_cmd or "",
                test_cmd=config.test_cmd or "",
            )
            ws_result = check_workspace_scripts(project_dir, ws_required)
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

def _format_structural_field(entry: dict, field: str) -> str:
    """Return the entry field value, or an explicit-missing sentinel.

    Specifically for the structural ``surface`` / ``section`` fields.
    The ``sketches_diverged_on`` field is handled by
    ``_format_diverged_field`` (which uses ``repr()`` to distinguish a
    literal ``"?"`` string from the missing-field sentinel).

    Replaces the old ``entry.get(field, "?")`` default which made "field
    missing" indistinguishable from "value was literally ?".
    """
    raw = entry.get(field)
    if raw is None:
        return f"<no {field} field emitted by validator>"
    if not isinstance(raw, str) or not raw.strip():
        return f"<empty {field}: {raw!r}>"
    return raw


def _format_diverged_field(entry: dict) -> str:
    """Render ``sketches_diverged_on`` uniformly for error output.

    Uses ``repr()`` on valid non-empty string values so that the literal
    string ``"?"`` is visibly distinct from the "field missing" sentinel
    and from empty/whitespace values.
    """
    raw = entry.get("sketches_diverged_on")
    if raw is None:
        return "<no sketches_diverged_on field emitted by validator>"
    if not isinstance(raw, str) or not raw.strip():
        return f"<empty sketches_diverged_on: {raw!r}>"
    return repr(raw)


def _build_vacuous_reason_retry_prompt(
    spec_name: str, vacuous_entries: list[dict],
) -> str:
    """Build the re-prompt instruction for vacuous-reason retry."""
    bullet_lines = []
    for e in vacuous_entries:
        surface = e.get("surface", "<unknown surface>")
        section = e.get("section", "<unknown section>")
        bullet_lines.append(f"  - surface: {surface!r}, section: {section!r}")
    bullets = "\n".join(bullet_lines)
    return (
        f"Your previous validation report at "
        f"/project/.dark-factory/reviews/{spec_name}-validation.md "
        f"flagged the following contracts as unresolved but did not provide "
        f"a meaningful `sketches_diverged_on` reason:\n"
        f"{bullets}\n\n"
        f"For each entry, either:\n"
        f"  (a) describe in one concrete sentence what differs between your "
        f"Precision Analysis sketches — naming the field, type, or shape "
        f"that disagrees (minimum 4 words, do not echo the surface or "
        f"section field, do not use placeholders like '?', 'TODO', 'N/A', "
        f"or 'unclear'); OR\n"
        f"  (b) drop the entry from `unresolved_contracts` if your sketches "
        f"actually agree.\n\n"
        f"Rewrite the fenced ```yaml `unresolved_contracts:` block at the "
        f"END of the same validation file. Do not rewrite the rest of the "
        f"report."
    )


def _format_vacuous_reason_failure(
    *,
    first_entries: list[dict],
    second_entries: list[dict],
    first_raw: str,
    second_raw: str,
) -> str:
    """Format a fail-loud error for the second-strike vacuous-reason case.

    Includes the vacuous entries from both attempts and a bounded dump of
    the raw validator output from the last attempt so the user sees what
    the validator actually said, not the orchestrator's placeholder.
    """
    def _append_vacuous_entries(
        out: list[str], entries: list[dict], header: str,
    ) -> None:
        out.append(header)
        for e in entries:
            surface = _format_structural_field(e, "surface")
            section = _format_structural_field(e, "section")
            diverged_display = _format_diverged_field(e)
            out.append(f"  - {surface} ({section}): {diverged_display}")

    lines = [
        "spec validation failed: validator emitted unresolved_contracts "
        "with vacuous `sketches_diverged_on` on both attempts "
        "(initial + one re-prompt):",
        "",
    ]
    _append_vacuous_entries(lines, first_entries, "First attempt vacuous entries:")
    lines.append("")
    _append_vacuous_entries(lines, second_entries, "Second attempt vacuous entries:")
    lines.append("")
    lines.append("Raw validator output (last attempt, bounded to ~40 lines):")
    raw_lines = (second_raw or "").splitlines()
    max_lines = 40
    if len(raw_lines) > max_lines:
        shown = raw_lines[:max_lines]
        shown.append(f"... [{len(raw_lines) - max_lines} more lines elided]")
    else:
        shown = raw_lines
    lines.extend(shown)
    lines.append("")
    lines.append(
        "Resolve each by adding a literal example to the spec's Wire "
        "Contracts section, or fix the validator prompt if this recurs."
    )
    return "\n".join(lines)


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
    step_begin(spec_run.spec_name, "spec_validator", config, project_dir)
    from darkfactory.spec.validator_output import (
        audit_free_variables,
        is_vacuous_divergence_reason,
        parse_unresolved_contracts,
    )

    prompt = (
        f"Validate the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md. "
        f"Write the full validation report to /project/.dark-factory/reviews/{spec_run.spec_name}-validation.md. "
        f"Write the machine-readable status file to /project/.dark-factory/reviews/{spec_run.spec_name}-validation.status "
        f"using EXACTLY this format (lowercase keys, one per line, no comments, no extra fields):\n"
        f"verdict=PASS\n"
        f"score=<integer 0-100>\n"
        f"divergence=<LOW|MEDIUM|HIGH>\n"
        f"blockers=<integer count of Critical Issues that must be fixed before implementation>\n\n"
        f"At the END of your validation report markdown file, you MUST emit a fenced "
        f"```yaml block containing an `unresolved_contracts:` key (empty list `[]` if "
        f"none diverge across your Precision Analysis sketches; otherwise list each "
        f"surface, section, and divergence). This block is required on every run."
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

    # Enforce unresolved_contracts block. Re-prompt once if missing.
    if dry_run:
        return

    report_path = reviews_dir / f"{spec_run.spec_name}-validation.md"
    if not report_path.exists():
        # validate_spec_result passed without the .md file present (test
        # harness with mocked agent, or .status-only run). Nothing to parse;
        # skip the contracts check.
        return
    report_text = report_path.read_text(encoding="utf-8")
    contracts = parse_unresolved_contracts(report_text)

    if contracts is None:
        log.warning("validator omitted unresolved_contracts block; re-prompting once")
        retry_prompt = (
            f"Your previous validation report at "
            f"/project/.dark-factory/reviews/{spec_run.spec_name}-validation.md "
            f"did not include the required `unresolved_contracts:` YAML block. "
            f"Append it now to the END of the same file: emit `unresolved_contracts: []` "
            f"if no contracts diverge across your Precision Analysis sketches, otherwise "
            f"list each entry with `surface`, `section`, and `sketches_diverged_on` fields. "
            f"Do not rewrite the rest of the report."
        )
        retry_result = invoke_agent(
            AgentRole.SPEC_VALIDATOR, retry_prompt,
            config=config, barriers=barriers,
            run_id=spec_run.run_id, project_dir=project_dir,
            spec_name=spec_run.spec_name, iteration=0,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        )
        if retry_result.exit_code != 0:
            raise DarkFactoryError(
                f"spec validation re-prompt failed (exit {retry_result.exit_code})"
            )
        report_text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
        contracts = parse_unresolved_contracts(report_text)
        if contracts is None:
            raise DarkFactoryError(
                "spec validation failed: validator did not emit the required "
                "`unresolved_contracts:` YAML block after one re-prompt"
            )

    # Vacuous-reason retry: if any entry has a content-free
    # sketches_diverged_on, re-prompt the validator once with a sharper
    # instruction before failing.
    if contracts and any(is_vacuous_divergence_reason(e) for e in contracts):
        log.warning(
            "validator emitted unresolved_contracts with vacuous "
            "sketches_diverged_on; re-prompting once"
        )
        vacuous_entries_first = [
            e for e in contracts if is_vacuous_divergence_reason(e)
        ]
        retry_prompt = _build_vacuous_reason_retry_prompt(
            spec_run.spec_name, vacuous_entries_first,
        )
        retry_result = invoke_agent(
            AgentRole.SPEC_VALIDATOR, retry_prompt,
            config=config, barriers=barriers,
            run_id=spec_run.run_id, project_dir=project_dir,
            spec_name=spec_run.spec_name, iteration=0,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        )
        if retry_result.exit_code != 0:
            raise DarkFactoryError(
                f"spec validation vacuous-reason re-prompt failed "
                f"(exit {retry_result.exit_code})"
            )
        if not report_path.exists():
            raise DarkFactoryError(
                "spec validation failed: validator report file disappeared "
                "after vacuous-reason retry"
            )
        report_text_after_retry = report_path.read_text(encoding="utf-8")
        contracts_after_retry = parse_unresolved_contracts(report_text_after_retry)
        if contracts_after_retry is None:
            raise DarkFactoryError(
                "spec validation failed: validator dropped the "
                "`unresolved_contracts:` block on the vacuous-reason retry"
            )
        if any(is_vacuous_divergence_reason(e) for e in contracts_after_retry):
            vacuous_entries_second = [
                e for e in contracts_after_retry
                if is_vacuous_divergence_reason(e)
            ]
            raise DarkFactoryError(_format_vacuous_reason_failure(
                first_entries=vacuous_entries_first,
                second_entries=vacuous_entries_second,
                first_raw=report_text,
                second_raw=report_text_after_retry,
            ))
        contracts = contracts_after_retry
        report_text = report_text_after_retry

    if contracts:
        # Reached when validator emitted real non-vacuous contracts; prints and fails.
        # The plan's "unreachable in normal flow" note was wrong — this is the primary
        # path for legitimate unresolved contracts.
        lines = ["spec validation failed: validator flagged unresolved contracts:"]
        for entry in contracts:
            surface = _format_structural_field(entry, "surface")
            section = _format_structural_field(entry, "section")
            diverged = _format_diverged_field(entry)
            lines.append(f"  - {surface} ({section}): {diverged}")
        lines.append(
            "Resolve each by adding a literal example to the spec's Wire Contracts section."
        )
        raise DarkFactoryError("\n".join(lines))

    fv_offenders = audit_free_variables(report_text)
    if fv_offenders:
        lines = [
            "spec validation failed: Free Variables table contains entries that "
            "describe serialized cross-boundary surfaces (these are unresolved "
            "contracts, not free variables):",
        ]
        for row in fv_offenders:
            lines.append(f"  {row}")
        lines.append(
            "Move each offending row out of Free Variables and resolve it as a "
            "wire contract in the spec."
        )
        raise DarkFactoryError("\n".join(lines))


def bridge_unmodeled_runtime_fields(
    config: PipelineConfig, rt: RuntimeDecl,
) -> None:
    """Bridge: fill PipelineConfig fields that are NOT yet in EnvironmentContract.

    Historically this function filled every runtime field from the architect's
    manifest as a fallback. Phase 2.3 moved the common fields (server_setup_cmd,
    test_server_cmd, server_health_path, server_startup_timeout, build/lint/
    typecheck/test cmds, language, source_dirs) into ``EnvironmentContract``,
    and ``refresh_config_from_environment`` is now called at every step
    boundary via ``step_begin``, so those fields no longer need a manifest
    fallback — they come from ``environment.toml``.

    What REMAINS here are the fields that EnvironmentContract does not yet
    model: ``services`` and ``test_base_url``. Phase 4 extends the schema to
    cover them, at which point this entire function can be deleted and the
    call site at _step_architecture_lock removed. Until then, this is the
    only path that populates services/test_base_url into PipelineConfig.
    """
    if rt.test_base_url and not config.test_base_url:
        config.test_base_url = rt.test_base_url
        log.info("manifest runtime bridge: test_base_url=%s", rt.test_base_url)
    if rt.services and not config.services:
        config.services = [
            ServiceDef(
                name=s.name,
                image=s.image,
                env=dict(s.env),
                health_cmd=s.health_cmd,
                health_timeout=s.health_timeout,
                inject_env=dict(s.inject_env),
            )
            for s in rt.services
        ]
        svc_names = [s.name for s in config.services]
        log.info("manifest runtime bridge: services=%s", svc_names)


def _step_architecture_lock(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    fresh: bool = False,
    regen: bool = False,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """Step 1.5: Generate architecture manifest.

    Uses file-existence + spec_hash as its own resume gate — does NOT
    advance completed_step, avoiding renumbering existing steps.
    """
    step_begin(spec_run.spec_name, "architect", config, project_dir)
    from darkfactory.spec.manifest import (
        parse_manifest,
        validate_environment,
        validate_manifest,
        validate_manifest_scope,
    )

    arch_dir = project_dir / ".dark-factory" / "architecture"
    arch_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = arch_dir / f"{spec_run.spec_name}-manifest.yaml"
    spec_file = project_dir / ".dark-factory" / "specs" / f"{spec_run.spec_name}.md"

    # --fresh or --regen-scenarios: delete existing manifest to force regeneration.
    if (fresh or regen) and manifest_path.exists():
        manifest_path.unlink()
        log.info("architecture manifest deleted (--%s)", "fresh" if fresh else "regen-scenarios")

    # Resume gate: skip ARCHITECT INVOCATION if manifest exists and spec_hash
    # matches — but still re-apply the cached manifest to the environment
    # contract. Without the re-apply, env.toml/delta-log drift (e.g. after a
    # reset procedure that wiped deltas but kept the manifest) silently
    # leaves the legacy bootstrap as the effective contract, discarding the
    # architect's scoped commands. amend is idempotent via parent_hash and
    # skips the write when the ops already match the folded view.
    if manifest_path.exists():
        import yaml
        try:
            existing = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict) and existing.get("spec_hash") == spec_run.spec_hash:
                log.info(
                    "architecture manifest is current (spec_hash matches); "
                    "re-applying cached manifest to environment contract"
                )
                try:
                    _apply_manifest_to_environment(
                        manifest_path=manifest_path,
                        spec_file=spec_file,
                        spec_run=spec_run,
                        config=config,
                        project_dir=project_dir,
                    )
                    return
                except DarkFactoryError as exc:
                    log.warning(
                        "cached manifest no longer validates against current "
                        "tree (%s); regenerating", exc,
                    )
                    manifest_path.unlink()
            else:
                log.info("architecture manifest is stale (spec_hash mismatch), regenerating")
                manifest_path.unlink()
        except Exception:
            log.warning("failed to read existing manifest, regenerating")
            manifest_path.unlink()

    # Phase 3.4: detect lockfile-based environment hints and write them
    # to .dark-factory/architecture/hints.json. The Architect reads this
    # file to avoid emitting npm commands against a pnpm workspace, etc.
    from darkfactory.architecture.hints import detect_and_write
    detect_and_write(project_dir)

    # Build prompt for the Architect agent.
    tier1_context, _ = _compile_tier1_contracts(project_dir, spec_run.spec_name, config)
    prompt = (
        f"Read your agent definition at /project/.dark-factory/agents/architect.md. "
        f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md. "
        f"Analyze the existing codebase in {config.source_dirs}. "
        f"Write the architecture manifest to /project/.dark-factory/architecture/"
        f"{spec_run.spec_name}-manifest.yaml. "
        f"Write a generation report to /project/.dark-factory/reports/"
        f"{spec_run.spec_name}-architecture.md"
    )
    if tier1_context:
        prompt += f"\n\n{tier1_context}"

    # Phase 3.1: inject the materialized environment contract by reference,
    # not by value. The Architect reads /project/.dark-factory/architecture/
    # environment.toml directly and treats its `runtime:` block in the
    # manifest as a proposed amendment (delta payload). The orchestrator
    # transforms that block into a delta host-side after the agent returns.
    env_path = arch_dir / "environment.toml"
    precheck_path = (
        project_dir / ".dark-factory" / "precheck" / f"{spec_run.spec_name}.json"
    )
    # Phase 4.1: spec_type is guaranteed present — orchestrator entry
    # point hard-fails if absent, so no fallback needed here.
    effective_spec_type = spec_run.spec_type

    env_section = (
        f"\n\nENVIRONMENT CONTRACT:\n"
        f"- Current contract: /project/.dark-factory/architecture/environment.toml"
        f"{' (exists)' if env_path.exists() else ' (does not exist yet — bootstrap)'}\n"
        f"- Delta log: /project/.dark-factory/architecture/environment.deltas/\n"
        f"- spec_type: {effective_spec_type}\n"
        f"\nRead environment.toml before producing your manifest. Your `runtime:` block "
        f"in the manifest is the proposed amendment (delta payload) — set ONLY the "
        f"fields you intend to change. For most {effective_spec_type} specs the runtime "
        f"block should be empty. "
    )
    if effective_spec_type != "bootstrap":
        env_section += (
            "Because spec_type is not 'bootstrap', you MUST NOT set "
            "`runtime.language` or `layout.source_dirs` — those are bootstrap-only. "
        )
    env_section += (
        "Do NOT write to environment.toml or environment.deltas/ yourself — those "
        "are managed host-side by the orchestrator.\n"
        "\nIMPORTANT (CWD rule): if `server_setup_cmd` or `test_server_cmd` in the "
        "contract uses `cd <dir>`, that directory is the effective CWD for the server. "
        "Configuration files (drizzle.config.ts, .env, etc.) must be placed relative to "
        "that CWD, not the project root."
    )
    prompt += env_section

    if precheck_path.exists():
        prompt += (
            f"\n\nPRECHECK ADVISORY: /project/.dark-factory/precheck/"
            f"{spec_run.spec_name}.json — read this for shape claims and "
            f"unresolved acceptance-criteria findings."
        )

    prompt += (
        "\n\nENVIRONMENT HINTS: /project/.dark-factory/architecture/hints.json — "
        "best-guess project tooling (package manager, language, lockfiles, "
        "workspace layout) detected from project files. Use these to avoid "
        "proposing commands that contradict the project's actual tooling "
        "(e.g., do not emit npm commands against a pnpm workspace)."
    )

    result = invoke_agent(
        AgentRole.ARCHITECT, prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        max_turns_override=config.architect_max_turns,
    )

    if result.exit_code != 0:
        raise DarkFactoryError(f"architecture lock failed (exit {result.exit_code})")

    if not manifest_path.exists():
        raise DarkFactoryError("architect agent succeeded but no manifest was written")

    _apply_manifest_to_environment(
        manifest_path=manifest_path,
        spec_file=spec_file,
        spec_run=spec_run,
        config=config,
        project_dir=project_dir,
    )

    log.info("architecture manifest generated and validated: %s", manifest_path)


def _apply_manifest_to_environment(
    *,
    manifest_path: Path,
    spec_file: Path,
    spec_run: SpecRun,
    config: PipelineConfig,
    project_dir: Path,
) -> None:
    """Parse + validate a manifest, apply its runtime block as an environment
    delta, and bridge unmodeled runtime fields into the in-memory config.

    Safe to call on both freshly-written and cached manifests. The amend
    function is idempotent via parent_hash and skips writing a new delta
    when the proposed ops are already reflected in the folded view —
    so replays on an already-consistent env contract are no-ops.
    """
    from darkfactory.spec.manifest import (
        parse_manifest,
        validate_environment,
        validate_manifest,
        validate_manifest_scope,
    )
    from darkfactory.architecture.amend import (
        AmendmentRejected,
        amend_environment_from_manifest,
    )
    from darkfactory.architecture.deltas import EnvironmentConflict

    manifest_text = manifest_path.read_text(encoding="utf-8")
    parse_result = parse_manifest(manifest_text)
    if parse_result.errors:
        raise DarkFactoryError(
            "Architecture manifest parse errors:\n"
            + "\n".join(f"  - {e}" for e in parse_result.errors)
        )

    spec_text = spec_file.read_text(encoding="utf-8")
    validation = validate_manifest(parse_result.manifest, spec_text, project_dir)
    if validation.errors:
        raise DarkFactoryError(
            "Architecture manifest validation errors:\n"
            + "\n".join(f"  - {e}" for e in validation.errors)
        )
    for w in validation.warnings:
        log.warning("manifest validation: %s", w)

    scope_warnings = validate_manifest_scope(parse_result.manifest, spec_text)
    for w in scope_warnings:
        log.warning("manifest scope: %s", w)

    env_warnings = validate_environment(parse_result.manifest, config.language)
    for w in env_warnings:
        log.warning("manifest environment: %s", w)

    effective_spec_type = spec_run.spec_type

    try:
        delta_path = amend_environment_from_manifest(
            parse_result.manifest.runtime,
            spec_id=spec_run.spec_name,
            spec_type=effective_spec_type,
            project_dir=project_dir,
        )
    except AmendmentRejected as exc:
        raise DarkFactoryError(
            f"Architecture manifest violates environment contract allowlist: {exc}"
        ) from exc
    except EnvironmentConflict as exc:
        raise DarkFactoryError(
            f"Environment contract fold failed (parent_hash mismatch): {exc}. "
            f"This indicates a delta-log corruption — manual escalation required."
        ) from exc

    if delta_path is not None:
        log.info(
            "ENVIRONMENT_AMENDED: spec=%s delta=%s",
            spec_run.spec_name, delta_path.name,
        )

    # Legacy fallback gate for fields not yet in the env contract schema
    # (services, test_base_url). Mirrors the allowlist logic above.
    rt = parse_result.manifest.runtime
    if effective_spec_type != "bootstrap":
        legacy_offenders: list[str] = []
        if rt.services:
            legacy_offenders.append("services")
        if rt.test_base_url:
            legacy_offenders.append("test_base_url")
        if legacy_offenders:
            raise DarkFactoryError(
                f"Architecture manifest violates environment contract allowlist: "
                f"spec_type={effective_spec_type!r} may not declare {legacy_offenders}. "
                f"Only spec_type='bootstrap' may set service or test_base_url. "
                f"(These fields bypass the env contract schema in Phase 3 and are "
                f"gated here pending Phase 4 schema extension.)"
            )
    bridge_unmodeled_runtime_fields(config, rt)


def _load_manifest_context_for_sa(
    project_dir: Path, spec_name: str,
) -> tuple[str, bool]:
    """Load architecture manifest and format it for the Scenario Architect.

    Returns (context_block, has_api_contracts).  context_block is empty when
    no manifest exists or parsing fails.  has_api_contracts is True when the
    manifest contains api_contracts AND a test server is expected to be
    available (caller must combine with config.test_server_cmd).
    """
    manifest_path = (
        project_dir / ".dark-factory" / "architecture" / f"{spec_name}-manifest.yaml"
    )
    if not manifest_path.exists():
        return "", False

    from darkfactory.spec.manifest import format_manifest_for_sa, parse_manifest

    manifest_text = manifest_path.read_text(encoding="utf-8")
    parse_result = parse_manifest(manifest_text)
    if parse_result.errors or parse_result.manifest is None:
        log.warning("failed to parse architecture manifest for SA injection, skipping")
        return "", False

    has_api = bool(parse_result.manifest.api_contracts)
    return "\n\n" + format_manifest_for_sa(parse_result.manifest), has_api


def _extract_free_variables(project_dir: Path, spec_name: str) -> str:
    """Extract Free Variables section from spec or validation report.

    Reads the spec file first (author-provided Free Variables take
    precedence), then falls back to the validation report.  Returns
    the body text of the Free Variables section, or empty string if
    none found or no substantive entries (must contain FV-N identifiers).
    """
    sources = [
        project_dir / ".dark-factory" / "specs" / f"{spec_name}.md",
        project_dir / ".dark-factory" / "reviews" / f"{spec_name}-validation.md",
    ]
    for source in sources:
        if not source.exists():
            continue
        content = source.read_text(encoding="utf-8", errors="replace")
        match = re.search(
            r"(?i)#{2,3}\s+(?:\d+\.\s*)?free\s+variables.*?\n(.*?)(?=\n#{1,3}\s|\Z)",
            content,
            re.DOTALL,
        )
        if match:
            body = match.group(1).strip()
            # Accept FV-N (spec-authored) or FR-N (validator-detected) entries
            if body and re.search(r"\b(?:FV|FR)-\d+\b", body):
                return body
    return ""


def _detect_tools_in_spec(spec_text: str) -> list[str]:
    """Detect tool/platform names mentioned in spec text.

    Uses custom trigger patterns for generic names (e.g. yaml_assertions)
    and word-boundary matching for specific tool names (biome, eslint, etc.).
    """
    from darkfactory.scenarios.tool_docs import _TOOL_TRIGGERS

    detected = []
    for tool in TOOL_DOCS:
        trigger = _TOOL_TRIGGERS.get(tool)
        if trigger is not None:
            if trigger.search(spec_text):
                detected.append(tool)
        else:
            if re.search(rf"\b{tool}\b", spec_text, re.IGNORECASE):
                detected.append(tool)
    return detected


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
    """Step 2: Generate blind test scenarios.

    Phase 4.6b.1.v: the former single-agent Scenario Architect has been
    split into two roles with independent context windows:

    - **Test Engineer** writes AC-aligned *visible* tests under
      ``.dark-factory/scenarios/<spec>/`` that the Implementer can read.
    - **QA Holdout** writes adversarial *blind* tests under
      ``.dark-factory/holdouts/<spec>/`` that the Implementer cannot read
      (the directory is masked in the Implementer's container).

    All SA validation, coverage-floor enforcement, and repair retries
    target the Test Engineer's output (the authoritative anchor-aligned
    suite). The QA Holdout runs once as an independent invocation with
    no retry loop — adversarial coverage is best-effort and does not
    gate the pipeline.
    """
    step_begin(spec_run.spec_name, "test_engineer", config, project_dir)
    scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_run.spec_name
    holdouts_dir = project_dir / ".dark-factory" / "holdouts" / spec_run.spec_name

    if regen and scenarios_dir.exists():
        if has_sentinel_file(scenarios_dir):
            log.warning(
                "regen=True will destroy the SCENARIOS-NOT-APPLICABLE.md sentinel in %s — "
                "the Test Engineer must re-justify non-applicability",
                scenarios_dir,
            )
        shutil.rmtree(scenarios_dir)
        log.info("regenerating scenarios")
    if regen and holdouts_dir.exists():
        shutil.rmtree(holdouts_dir)
        log.info("regenerating holdouts")

    # Phase 4.6b.1.v: TE and QA Holdout are gated independently so a
    # resume after a crash between TE success and QA Holdout start still
    # runs the QA Holdout. The old early-returns (sentinel / files exist)
    # are converted to boolean flags; QA Holdout invocation at the bottom
    # of the function is always reached except for sentinel specs.
    te_sentinel = False
    te_files_existed = False

    # Sentinel check comes first — a sentinel-only directory has no recognized test
    # file patterns but is a valid and complete output from the Test Engineer.
    if has_sentinel_file(scenarios_dir):
        valid, objections = validate_sentinel(get_sentinel_path(scenarios_dir))
        if not valid:
            raise DarkFactoryError(
                "Scenario sentinel rejected — fix the justification or write real "
                f"scenario files:\n{objections}"
            )
        log.info("scenarios sentinel validated, skipping TE generation")
        te_sentinel = True
    elif has_scenario_files(scenarios_dir):
        log.info("scenarios already exist, skipping TE generation")
        te_files_existed = True

    # Sentinel specs short-circuit entirely: no visible suite, no
    # adversarial holdouts to write against a "not applicable" justification.
    if te_sentinel:
        return

    # Phase 4.6b.1.v: QA Holdout runs here, BEFORE the TE generation block
    # below. This placement is deliberate: it guarantees that a resume
    # after TE success (te_files_existed=True) still reaches the QA
    # Holdout invocation, and that a fresh run reaches it too. The QA
    # Holdout is independent of TE output (its barrier masks
    # .dark-factory/scenarios/), so ordering relative to the coverage-floor
    # retry loop does not matter. _invoke_qa_holdout is idempotent on
    # holdouts_dir, so this is safe on any resume.
    _invoke_qa_holdout(
        spec_run, config, barriers, project_dir,
        holdouts_dir=holdouts_dir,
        events_file=events_file,
        audit_path=audit_path,
        dry_run=dry_run,
    )

    # If TE output already existed when the step entered, the validation
    # retry loop and coverage floor have already run in a prior
    # invocation and must not re-run (they mutate scenarios_dir and
    # could rewrite files the Implementer has already consumed).
    if te_files_existed:
        return

    scenarios_dir.mkdir(parents=True, exist_ok=True)

    # Parse and compile Tier 1 contracts (deterministic).  Files are written
    # AFTER the SA finishes so they don't poison the has_scenario_files() guard.
    tier1_context, tier1_files = _compile_tier1_contracts(
        project_dir, spec_run.spec_name, config,
    )

    prompt = (
        f"Read your agent definition at /project/.dark-factory/agents/test-engineer.md. "
        f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md. "
        f"Read the anchor manifest at /project/.dark-factory/state/{spec_run.spec_name}/anchor_manifest.json "
        f"(if present) for the authoritative list of AC/FR/EC IDs you must cover. "
        f"Generate AC-aligned visible tests following the Hard Rules in your agent definition. "
        f"Every test name MUST carry exactly one in-manifest tag: [AC-N], [FR-N], or [EC-N]. "
        f"Write output to /project/.dark-factory/scenarios/{spec_run.spec_name}/. "
        f"Write a scenario report to /project/.dark-factory/reports/{spec_run.spec_name}-scenarios.md"
    )

    # Build execution context block to inject into the Test Engineer's prompt.
    context_block = "\n\n## Execution Context\n\n"
    # Phase 3.6: point the SA at the materialized environment contract.
    # The file already exists in the SA's RO mount; this just tells it
    # to read it so its scenarios target the project's actual commands.
    env_path = project_dir / ".dark-factory" / "architecture" / "environment.toml"
    if env_path.exists():
        context_block += (
            "ENVIRONMENT CONTRACT: /project/.dark-factory/architecture/"
            "environment.toml — read this for the project's canonical "
            "build/test/server commands and ports. Scenarios that assume "
            "different commands will fail at gate time.\n"
        )
    context_block += f"TOOLCHAIN: {config.language or 'unknown'}\n"
    context_block += f"HAS_TEST_SERVER_CMD: {'true' if config.test_server_cmd else 'false'}\n"
    context_block += f"HAS_BROWSER_SERVER_CMD: {'true' if config.browser_server_cmd else 'false'}\n"
    context_block += f"HAS_SCENARIO_TEST_CMD: {'true' if config.scenario_test_cmd else 'false'}\n"
    if config.services:
        svc_names = ", ".join(s.name for s in config.services)
        context_block += f"HAS_SERVICES: true ({svc_names})\n"
    context_block += "\n"
    context_block += "Apply the Execution Context Constraints from your agent definition before choosing any scenario format.\n"

    # Inject Free Variables so the SA knows what NOT to assert on.
    free_vars = _extract_free_variables(project_dir, spec_run.spec_name)
    if free_vars:
        context_block += (
            "\n## Free Variables (DO NOT ASSERT on these)\n\n"
            + free_vars
            + "\n\nEach free variable above represents an implementation choice the spec "
            "intentionally leaves unconstrained. Do NOT write assertions that depend on "
            "how these choices are resolved.\n"
        )

    # Inject Tier 1 coverage list so the SA knows what NOT to re-test.
    if tier1_context:
        context_block += tier1_context

    # Inject Architecture Manifest context so SA aligns with locked structure.
    manifest_context, _manifest_has_api = _load_manifest_context_for_sa(
        project_dir, spec_run.spec_name,
    )
    if manifest_context:
        context_block += manifest_context

    # Inject tool documentation for detected tools.
    spec_file = project_dir / ".dark-factory" / "specs" / f"{spec_run.spec_name}.md"
    spec_text_for_tools = spec_file.read_text(encoding="utf-8")
    detected_tools = _detect_tools_in_spec(spec_text_for_tools)
    if detected_tools:
        context_block += "\n## Tool Reference Documentation\n\n"
        context_block += (
            "The following tools are referenced in the spec. "
            "Use this documentation when writing assertions.\n"
        )
        for tool in detected_tools:
            context_block += f"\n### {tool.title()}\n{TOOL_DOCS[tool]}\n"

    prompt = prompt + context_block

    result = invoke_agent(
        AgentRole.TEST_ENGINEER, prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        phase="generate",
    )

    if result.exit_code != 0:
        raise DarkFactoryError(f"test engineer scenario generation failed (exit {result.exit_code})")

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

    # SA validation + retry loop (does NOT consume iteration budget).
    if not has_sentinel_file(scenarios_dir) and config.sa_max_retries > 0:
        for sa_attempt in range(config.sa_max_retries + 1):
            # SA may write a sentinel during retry — skip validation if so.
            if has_sentinel_file(scenarios_dir):
                break
            validation = validate_scenarios(
                spec_run.spec_name,
                config.language,
                project_dir,
                max_assertions=config.scenario_max_assertions_per_file,
                spec_path=spec_file,
                tier1_prefix="00-tier1-",
            )
            if validation.passed:
                break
            if sa_attempt < config.sa_max_retries:
                log.info(
                    "SA validation failed (attempt %d/%d), regenerating Tier 2: %s",
                    sa_attempt + 1, config.sa_max_retries, validation.report[:500],
                )
                _clear_tier2_scenarios(scenarios_dir, tier1_prefix="00-tier1-")
                suffix = "Fix the issues above in your regenerated scenarios."

                retry_prompt = (
                    prompt
                    + f"\n\n## Validation Report (attempt {sa_attempt + 1} failed)\n\n"
                    + validation.report
                    + "\n\n" + suffix
                )
                retry_result = invoke_agent(
                    AgentRole.TEST_ENGINEER, retry_prompt,
                    config=config, barriers=barriers,
                    run_id=spec_run.run_id, project_dir=project_dir,
                    spec_name=spec_run.spec_name, iteration=0,
                    events_file=events_file, audit_path=audit_path, dry_run=dry_run,
                    phase="validate",
                )
                if retry_result.exit_code != 0:
                    log.warning("TE retry %d failed (exit %d)", sa_attempt + 1, retry_result.exit_code)
            else:
                log.warning(
                    "TE validation failed after %d attempt(s)",
                    config.sa_max_retries + 1,
                )
                raise DarkFactoryError(
                    f"TE scenarios failed validation after {config.sa_max_retries + 1} attempts:\n"
                    + validation.report[:2000]
                )

    # Write Tier 1 files now that the SA has finished and the guard checks are done.
    for filename, content in tier1_files:
        (scenarios_dir / filename).write_text(content, encoding="utf-8")
    if tier1_files:
        log.info("wrote %d Tier 1 contract file(s) to %s", len(tier1_files), scenarios_dir)

    # Anchor coverage floor — bounded regen if any declared anchor has zero
    # scenarios crediting it. Skipped for sentinel-only outputs.
    if (
        config.coverage_floor_enabled
        and not has_sentinel_file(scenarios_dir)
    ):
        _enforce_coverage_floor(
            spec_run, config, barriers, project_dir,
            scenarios_dir=scenarios_dir,
            base_prompt=prompt,
            events_file=events_file,
            audit_path=audit_path,
            dry_run=dry_run,
        )



def _invoke_qa_holdout(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    holdouts_dir: Path,
    events_file: Path | None,
    audit_path: Path | None,
    dry_run: bool,
) -> None:
    """Invoke the QA Holdout agent (Phase 4.6b.1.v).

    Writes adversarial blind tests to ``.dark-factory/holdouts/<spec>/``.
    No validation or retry loop — the holdouts are invisible to every
    downstream agent except the gate runner, and any failure there is
    classified by the defect classifier, not patched at generation time.
    Infrastructure failures (non-zero exit) are warned but do not abort
    the pipeline: the visible AC suite is the gate, holdouts are
    supplementary adversarial coverage.
    """
    step_begin(spec_run.spec_name, "qa_holdout", config, project_dir)
    # Idempotency: treat the holdouts report file as the completion
    # marker. Checking for any file in holdouts_dir (as an earlier draft
    # did) would treat a half-written dir from a crashed previous run as
    # "done" and wedge resume permanently. The report is the QA Holdout's
    # last write in its agent definition, so its presence is a reliable
    # "finished" signal.
    holdouts_report = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_run.spec_name}-holdouts.md"
    )
    if holdouts_report.exists() and holdouts_dir.exists() and any(
        holdouts_dir.iterdir()
    ):
        log.info(
            "holdouts report and files already exist at %s, skipping QA Holdout",
            holdouts_dir,
        )
        return
    # Partial state from a prior crashed QA Holdout — wipe and re-run to
    # avoid mixing files from two different invocations.
    if holdouts_dir.exists():
        shutil.rmtree(holdouts_dir)
    holdouts_dir.mkdir(parents=True, exist_ok=True)

    prompt = (
        f"Read your agent definition at /project/.dark-factory/agents/qa-holdout.md. "
        f"Read the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md. "
        f"Read the anchor manifest at /project/.dark-factory/state/{spec_run.spec_name}/anchor_manifest.json "
        f"(if present) for context on what IS in scope so you can explore the boundary. "
        f"Generate adversarial blind tests following the What-to-Explore and Hard Rules "
        f"sections in your agent definition. Every test name MUST be tagged [adversarial] "
        f"or [holdout] — no [AC-N]/[FR-N]/[EC-N] tags. "
        f"Write output to /project/.dark-factory/holdouts/{spec_run.spec_name}/. "
        f"Write a holdouts report to /project/.dark-factory/reports/{spec_run.spec_name}-holdouts.md"
    )

    # QA Holdout shares the same toolchain signalling as TE — it runs in the
    # same test runner, so it needs to know what HAS_TEST_SERVER_CMD etc. are.
    context_block = "\n\n## Execution Context\n\n"
    env_path = project_dir / ".dark-factory" / "architecture" / "environment.toml"
    if env_path.exists():
        context_block += (
            "ENVIRONMENT CONTRACT: /project/.dark-factory/architecture/"
            "environment.toml — read this for the project's canonical "
            "build/test/server commands and ports.\n"
        )
    context_block += f"TOOLCHAIN: {config.language or 'unknown'}\n"
    context_block += f"HAS_TEST_SERVER_CMD: {'true' if config.test_server_cmd else 'false'}\n"
    context_block += f"HAS_BROWSER_SERVER_CMD: {'true' if config.browser_server_cmd else 'false'}\n"
    context_block += f"HAS_SCENARIO_TEST_CMD: {'true' if config.scenario_test_cmd else 'false'}\n"
    prompt = prompt + context_block

    result = invoke_agent(
        AgentRole.QA_HOLDOUT, prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        phase="generate",
    )
    if result.exit_code != 0:
        log.warning(
            "QA Holdout exited with code %d — continuing without "
            "adversarial coverage (visible AC suite is still the gate)",
            result.exit_code,
        )
        return

    wrote_files = any(
        p.is_file() for p in holdouts_dir.rglob("*")
    ) if holdouts_dir.exists() else False
    if not wrote_files:
        log.warning(
            "QA Holdout produced no holdout files at %s — continuing "
            "(visible AC suite is still the gate)",
            holdouts_dir,
        )


def _load_anchor_manifest(project_dir: Path, spec_name: str):
    """Load the persisted AnchorManifest from state, returning None on miss."""
    from darkfactory.spec.anchors import AnchorManifest, manifest_path

    return AnchorManifest.from_json_path(manifest_path(project_dir, spec_name))


def _enforce_coverage_floor(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    scenarios_dir: Path,
    base_prompt: str,
    events_file: Path | None,
    audit_path: Path | None,
    dry_run: bool,
) -> None:
    """Post-scenario-gen anchor coverage gate with bounded regen.

    Phase 4.6b.1.v: invokes the Test Engineer agent — coverage floor is
    anchor-aligned, and anchor-aligned coverage is the Test Engineer's
    responsibility. The QA Holdout does not participate in this loop
    (its output is [adversarial]-tagged, never anchor-tagged, and
    therefore cannot close anchor gaps).

    Up to ``config.coverage_max_regen_attempts`` extra TE invocations to
    cover any zero-coverage anchors. Retry-1 lists the gaps. Retry-2
    includes anchor text and the @untestable escape hatch. Persistent
    gaps escalate via DarkFactoryError.
    """
    step_begin(spec_run.spec_name, "test_engineer", config, project_dir)
    from darkfactory.events.types import (
        emit_coverage_duplicate_tags,
        emit_coverage_floor_violation,
        emit_coverage_gap,
        emit_coverage_pass,
        emit_coverage_regen,
        emit_coverage_untestable_waived,
    )
    from darkfactory.scenarios.coverage import (
        _extract_untestable_markers,
        check_spec_type_floor,
        compute_coverage,
    )

    manifest = _load_anchor_manifest(project_dir, spec_run.spec_name)
    if manifest is None:
        log.debug("no anchor manifest found, skipping coverage floor")
        return
    declared_total = (
        len(manifest.fr_ids) + len(manifest.ac_ids) + len(manifest.ec_ids)
    )
    if declared_total == 0:
        return

    spec_file = (
        project_dir / ".dark-factory" / "specs" / f"{spec_run.spec_name}.md"
    )

    def _persist_gaps(report) -> None:
        import json as _json

        from darkfactory.spec.anchors import coverage_gaps_path

        path = coverage_gaps_path(project_dir, spec_run.spec_name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                _json.dumps(
                    {
                        "uncovered_anchors": list(report.uncovered_anchors),
                        "weakly_covered": list(report.weakly_covered),
                        "duplicate_tags_by_anchor": dict(
                            report.duplicate_tags_by_anchor
                        ),
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
        except OSError as exc:
            log.warning("failed to persist coverage gaps: %s", exc)

    def _collect_untestable() -> dict[str, str]:
        markers: dict[str, str] = {}
        if not scenarios_dir.exists():
            return markers
        for p in scenarios_dir.rglob("*"):
            if not p.is_file():
                continue
            try:
                t = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            markers.update(_extract_untestable_markers(t))
        return markers

    def _apply_waivers(report, attempt: int) -> dict[str, str]:
        """Subtract @untestable waivers from uncovered. Only after retry-1."""
        if attempt < 1:
            return {}
        markers = _collect_untestable()
        if not markers:
            return {}
        waived = {a: r for a, r in markers.items() if a in report.uncovered_anchors}
        if not waived:
            return {}
        report.uncovered_anchors = [
            a for a in report.uncovered_anchors if a not in waived
        ]
        if events_file:
            emit_coverage_untestable_waived(
                events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                waived=waived, attempt=attempt,
            )
        return waived

    report = compute_coverage(scenarios_dir, manifest)
    _persist_gaps(report)

    # Phase 4.3: apply the spec-type-specific floor on top of the
    # per-anchor baseline. A bugfix spec with 100% anchor coverage but
    # no regression scenario still fails this check.
    floor_pass, floor_reason = check_spec_type_floor(
        report, spec_run.spec_type,
    )

    if not report.uncovered_anchors and floor_pass:
        if events_file:
            emit_coverage_pass(
                events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                anchor_count=declared_total,
            )
        if report.duplicate_tags_by_anchor and events_file:
            emit_coverage_duplicate_tags(
                events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                duplicates=report.duplicate_tags_by_anchor,
            )
        return

    if not report.uncovered_anchors and not floor_pass:
        log.warning(
            "coverage floor failure (%s): %s",
            spec_run.spec_type, floor_reason,
        )

    # Gap or floor failure detected — bounded regen.
    for attempt in range(1, config.coverage_max_regen_attempts + 1):
        uncovered = list(report.uncovered_anchors)
        floor_only = not uncovered and not floor_pass
        # Surface floor-only retries in the audit log alongside ordinary
        # gap retries. We use a synthetic FLOOR:<spec_type> sentinel in the
        # uncovered payload so downstream consumers can distinguish a
        # floor-driven regen from a real anchor gap without a new event
        # type.
        gap_payload = uncovered or (
            [f"FLOOR:{spec_run.spec_type}"] if floor_only else []
        )
        if events_file:
            emit_coverage_gap(
                events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                uncovered=gap_payload, attempt=attempt,
            )
            emit_coverage_regen(
                events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                attempt=attempt, uncovered=gap_payload,
            )

        existing_files = sorted(
            p.name for p in scenarios_dir.iterdir() if p.is_file()
        ) if scenarios_dir.exists() else []

        if floor_only:
            suffix = (
                f"\n\n## Coverage Floor Failure (regen attempt {attempt})\n"
                f"Every declared anchor is covered, but the spec-type floor "
                f"for `{spec_run.spec_type}` failed: {floor_reason}\n\n"
                f"Generate ONE additional scenario that satisfies the floor. "
                f"Do not modify or duplicate existing scenarios. "
                f"The following files already exist and you must not "
                f"overwrite them: {existing_files}."
            )
        elif attempt == 1:
            suffix = (
                "\n\n## Coverage Gap (regen attempt 1)\n"
                f"Your previous run did not produce scenarios covering anchors: "
                f"{', '.join(uncovered)}. Generate scenarios for ONLY these "
                f"anchors. Do not modify or duplicate existing scenarios. "
                f"The following files already exist and you must not "
                f"overwrite them: {existing_files}. "
                f"Tag each new scenario with `@covers <anchor-id>`."
            )
        else:
            # Retry 2+: include anchor text + @untestable escape hatch.
            anchor_excerpts: list[str] = []
            try:
                spec_text = spec_file.read_text(encoding="utf-8")
                for anchor in uncovered:
                    excerpt = _extract_anchor_text(spec_text, anchor)
                    if excerpt:
                        anchor_excerpts.append(f"### {anchor}\n{excerpt}")
            except OSError:
                pass
            excerpt_block = "\n\n".join(anchor_excerpts)
            suffix = (
                "\n\n## Coverage Gap (regen attempt 2 — final)\n"
                f"Anchors still uncovered after retry 1: {', '.join(uncovered)}.\n\n"
                f"Anchor text from the spec:\n\n{excerpt_block}\n\n"
                f"Generate scenarios for ONLY these anchors. Tag each with "
                f"`@covers <anchor-id>`. Do not modify existing files: "
                f"{existing_files}.\n\n"
                f"### What is testable (DEFAULT: testable)\n"
                f"Any anchor whose satisfaction is observable via one or more "
                f"of the following IS testable and MUST NOT be waived:\n"
                f"- **Process exit code** — spawn the target process and "
                f"assert on its exit code (e.g., \"exits 1 on invalid env\" → "
                f"spawn with bad env, assert `proc.status === 1`).\n"
                f"- **Process stdout/stderr** — spawn and assert on captured "
                f"output (e.g., \"logs structured JSON on startup\" → spawn, "
                f"read stdout lines, parse as JSON, assert fields).\n"
                f"- **HTTP request/response** — spawn the server and assert "
                f"on status code, headers, or body (e.g., \"GET /health "
                f"returns 200\" → start server, fetch, assert).\n"
                f"- **Filesystem state** — assert on files/directories "
                f"created, modified, or their contents after a command runs.\n"
                f"- **Environment variable handling** — spawn with crafted "
                f"env, assert exit code + stderr message (e.g., \"fails fast "
                f"on missing VAR\" → spawn without VAR, assert non-zero exit "
                f"and stderr contains the variable name).\n"
                f"- **Config file parsing** — write a crafted config, spawn, "
                f"assert exit code + stderr (e.g., \"rejects invalid schema\" "
                f"→ write bad config, spawn, assert failure).\n\n"
                f"### Escape hatch (NARROW)\n"
                f"`@untestable` is reserved for anchors that genuinely "
                f"**cannot** be verified by any automated mechanism in the "
                f"test runtime. The runtime CAN spawn processes, read "
                f"stdout/stderr, check exit codes, read/write files, and "
                f"make HTTP requests. The runtime CANNOT: take interactive "
                f"keyboard input, inspect rendered UI pixels, reach a real "
                f"external service that isn't mockable, or enforce "
                f"microsecond wall-clock guarantees.\n\n"
                f"**Before emitting `@untestable`, ask:** can I write a "
                f"vitest test that (a) spawns a process with specific env "
                f"or inputs, and (b) asserts on exit code, stdout, stderr, "
                f"or filesystem state? If yes, the anchor IS testable — "
                f"write the scenario. Waive only if the honest answer is "
                f"no.\n\n"
                f"If you do waive, emit "
                f"`@untestable <anchor-id>: <reason>` in a stub file. The "
                f"reason must name a specific runtime limitation (\"requires "
                f"interactive TTY\", \"requires GPU device\", not \"complex\" "
                f"or \"hard to test\")."
            )

        retry_prompt = base_prompt + suffix
        retry_result = invoke_agent(
            AgentRole.TEST_ENGINEER, retry_prompt,
            config=config, barriers=barriers,
            run_id=spec_run.run_id, project_dir=project_dir,
            spec_name=spec_run.spec_name, iteration=0,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            phase="coverage_regen",
        )
        if retry_result.exit_code != 0:
            raise DarkFactoryError(
                f"coverage regen attempt {attempt} failed: Test Engineer "
                f"exited with code {retry_result.exit_code} — aborting rather "
                f"than recomputing coverage on unchanged files"
            )

        report = compute_coverage(scenarios_dir, manifest)
        _apply_waivers(report, attempt)
        _persist_gaps(report)
        floor_pass, floor_reason = check_spec_type_floor(
            report, spec_run.spec_type,
        )
        if not report.uncovered_anchors and floor_pass:
            if events_file:
                emit_coverage_pass(
                    events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                    anchor_count=declared_total,
                )
            return

    # Persistent gap or floor failure — collect @untestable and escalate.
    untestable = _collect_untestable()

    if events_file:
        emit_coverage_floor_violation(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            uncovered=list(report.uncovered_anchors),
            untestable=untestable or None,
        )

    parts: list[str] = []
    if report.uncovered_anchors:
        parts.append(
            "anchors with zero scenarios after "
            f"{config.coverage_max_regen_attempts} regen attempts: "
            f"{', '.join(report.uncovered_anchors)}"
        )
    if not floor_pass:
        parts.append(f"spec-type floor ({spec_run.spec_type}): {floor_reason}")
    if untestable:
        parts.append(f"@untestable markers: {untestable}")
    raise DarkFactoryError(
        "scenario coverage floor violation: " + " | ".join(parts)
    )


def _extract_anchor_text(spec_text: str, anchor: str) -> str:
    """Best-effort extraction of the spec text describing a given anchor.

    Returns up to ~10 lines following the first line containing the anchor
    ID. Used to give the SA enough context on retry 2 without sending the
    whole spec back.
    """
    lines = spec_text.split("\n")
    # Prefer a markdown heading line (e.g. "### FR-5" or "## FR-5 Title") —
    # this targets the anchor's body section rather than incidental mentions
    # in Scenario Intent tables or summary rows.
    heading_re = re.compile(
        rf"^\s*#{{1,6}}\s+{re.escape(anchor)}\b", re.IGNORECASE,
    )
    for i, line in enumerate(lines):
        if heading_re.match(line):
            return "\n".join(lines[i:i + 10])
    # Fallback: first line mentioning the anchor at all.
    for i, line in enumerate(lines):
        if anchor in line:
            return "\n".join(lines[i:i + 10])
    return ""


def _clear_tier2_scenarios(scenarios_dir: Path, *, tier1_prefix: str = "00-tier1-") -> None:
    """Delete Tier 2 scenario files and subdirectories, preserving Tier 1 files."""
    for entry in scenarios_dir.iterdir():
        if entry.name.startswith(tier1_prefix):
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        elif entry.is_file():
            entry.unlink()


def _compile_tier1_contracts(
    project_dir: Path,
    spec_name: str,
    config: PipelineConfig,
) -> tuple[str, list[tuple[str, str]]]:
    """Parse and compile Tier 1 contracts from spec.

    Returns ``(context_block, files)`` where *context_block* is a string to
    inject into the SA prompt (empty when there are no active contracts) and
    *files* is a list of ``(filename, content)`` pairs.  The caller is
    responsible for writing the files to disk at the appropriate time.
    """
    from darkfactory.spec.contracts import compile_contracts, parse_contracts

    spec_file = project_dir / ".dark-factory" / "specs" / f"{spec_name}.md"
    if not spec_file.exists():
        return "", []

    spec_text = spec_file.read_text(encoding="utf-8")
    result = parse_contracts(spec_text)

    if result.errors:
        for err in result.errors:
            log.warning("Tier 1 contract parse error: %s", err)

    if not result.contracts:
        return "", []

    active = [c for c in result.contracts if not c.invariant]
    if not active:
        log.info(
            "all %d contracts are invariant (verified by gates), skipping Tier 1 compilation",
            len(result.contracts),
        )
        return "", []

    try:
        files = compile_contracts(active, config.language or "node")
    except (ValueError, TypeError) as exc:
        log.warning("Tier 1 contract compilation failed: %s", exc)
        return "", []

    log.info(
        "compiled %d Tier 1 contract file(s) (%d active, %d invariant)",
        len(files), len(active), len(result.contracts) - len(active),
    )

    # Build context block for the SA.
    lines = [
        "\n## Tier 1 Contracts (Already Compiled — Static/Filesystem Only)\n",
        f"{len(active)} **static** conditions below are covered by Tier 1 contract tests.",
        "These are file-existence and file-content checks ONLY.",
        "Do NOT generate scenarios that re-test these static conditions.",
        "HTTP behavioral testing of API endpoints is NOT covered by Tier 1 "
        "and MUST be provided by your scenarios.\n",
    ]
    for c in active:
        lines.append(f"- {c.id} ({c.fr}): {c.given} — type={c.type}")
    lines.append("")

    return "\n".join(lines), files


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
    """Re-invoke the Test Engineer to repair crashing/tautological scenarios.

    Phase 4.6b.1.v: repair targets the Test Engineer's visible suite only.
    The QA Holdout's adversarial files are not smoke-tested today (the
    smoke test only runs files under ``.dark-factory/scenarios/<spec>/``),
    so there is nothing for a holdout-side repair step to act on yet.

    Max 1 repair attempt. The TE sees its own scenario files + sanitized crash
    output but CANNOT see source code (barrier preserved via tmpfs masking).

    Post-repair mechanical validation: snapshot before repair, verify after
    repair that only crash-failing files were modified, assertion counts did
    not decrease, and no test functions were deleted. Revert on failure.
    """
    step_begin(spec_run.spec_name, "test_engineer", config, project_dir)
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
        f"Read your agent definition at /project/.dark-factory/agents/test-engineer.md. "
        f"You are in REPAIR MODE. Your previously generated visible tests have bugs.\n\n"
        f"## Crash Output (sanitized)\n```\n{crash_info[:6000]}\n```\n\n"
        f"## Tautological Tests (passed before implementation exists)\n"
        f"{tautological_list}\n\n"
        f"Fix ONLY the crashing tests in /project/.dark-factory/scenarios/{spec_run.spec_name}/. "
        f"Do not delete assertions or test functions. Do not create new files. "
        f"Do not weaken an AC-aligned test to make it pass — if the test encodes "
        f"the spec predicate correctly, the crash indicates either a test bug "
        f"(fix it) or a genuine pre-implementation failure (leave it)."
    )

    try:
        invoke_agent(
            AgentRole.TEST_ENGINEER, prompt,
            config=config, barriers=barriers,
            run_id=spec_run.run_id, project_dir=project_dir,
            spec_name=spec_run.spec_name, iteration=0,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            phase="repair",
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


def _write_defect_registry(spec_run: SpecRun, project_dir: Path) -> None:
    """Write defect registry for shipped-with-gaps runs."""
    last_iter = spec_run.current_iteration
    lines = [
        f"# Defect Registry: {spec_run.spec_name}\n",
        f"**Run ID:** {spec_run.run_id}",
        f"**Ship decision:** ORACLE_WITH_GAPS",
        f"**Pass rate:** {last_iter.pass_rate if last_iter else 0}%",
        f"**Stable failures:** {len(spec_run.stable_failure_names)}\n",
        "## Documented Gaps\n",
        "| # | Test Name | Classification | Iterations Stable |",
        "|---|-----------|---------------|-------------------|",
    ]
    for i, name in enumerate(sorted(spec_run.stable_failure_names), 1):
        lines.append(f"| {i} | {name} | BRITTLE | {spec_run.iteration} |")

    lines.append("")
    lines.append("## Gate Results\n")
    if last_iter:
        lines.append(f"- Hard Gates (Build/Lint/Type): {'PASS' if last_iter.hard_gates_pass else 'FAIL'}")
        lines.append(
            f"- Blind Scenarios: {last_iter.scenario_pass_count}/"
            f"{last_iter.scenario_total} ({last_iter.pass_rate}%)"
        )

    report_path = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_run.spec_name}-defect-registry.md"
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n")
    log.info("wrote defect registry: %s", report_path)


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

    # Phase 5.1: snapshot the env contract hash *before* any post-ship
    # side effect runs. This is the "what the Oracle actually saw" anchor
    # for Phase 5.2 regression drift detection and Phase 6 plan replay.
    # Capturing after registry_register / regression / branch creation
    # would record whatever state drifted during archival, not the
    # decision-time contract.
    env_hash_at_ship = capture_env_hash(project_dir)

    # Defect registry for shipped-with-gaps runs.
    if spec_run.ship_with_gaps:
        _write_defect_registry(spec_run, project_dir)

    # Register scenarios.
    registry_register(project_dir, spec_run.spec_name)

    # Phase 5.1: tag the newly archived scenarios with origin/type headers
    # and append a ShippedSpec row to .dark-factory/plan/shipped.jsonl so
    # Phase 5.2's regression runner and Phase 6's plan runner have a
    # durable "what was decided when" record keyed by env_hash_at_ship.
    # Best-effort: failures here log and continue — the spec already shipped.
    # spec_type is required: silently defaulting to "feature" would mislabel
    # bootstrap/refactor specs in the durable shipped.jsonl record. If it's
    # missing here, that's an upstream bug — skip the archive and log loudly.
    if not spec_run.spec_type:
        log.error(
            "post-ship archive: spec_run.spec_type missing for %s; skipping archive",
            spec_run.spec_name,
        )
    else:
        try:
            archive_dir = (
                project_dir / ".dark-factory" / "shipped-scenarios" / spec_run.spec_name
            )
            tag_archived_scenarios(
                archive_dir, spec_run.spec_name, spec_run.spec_type,
            )
            record_shipped_spec(
                project_dir,
                spec_id=spec_run.spec_name,
                spec_type=spec_run.spec_type,
                spec_origin=spec_run.spec_name,
                env_hash=env_hash_at_ship,
            )
        except Exception:  # noqa: BLE001 — never unship on archive failure
            log.exception(
                "post-ship archive failed for %s", spec_run.spec_name,
            )

    # Regression testing — mandatory when trust-derived autonomy < 3.
    trust_requires_regression = spec_run.effective_autonomy < 3
    if (run_regression_on_ship or config.regression_on_ship or trust_requires_regression) and config.enterprise_enabled:
        from darkfactory.scenarios.regression import run_regression
        _progress("Running regression tests...")
        passed, failed, total, deferred = run_regression(
            config, project_dir,
            exclude=[spec_run.spec_name],
            events_file=events_file,
            run_id=spec_run.run_id,
        )
        if deferred:
            log.info(
                "regression: %d spec(s) deferred under budget: %s",
                len(deferred), ",".join(deferred),
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
    """Handle escalation — max iterations reached without SHIP.

    Phase 2.3: invokes the escalation agent — must freshen the config
    from environment.toml at the boundary like every other agent step.
    """
    step_begin(spec_run.spec_name, "escalation", config, project_dir)
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
        esc_file.write_text(extract_agent_text(esc_result.output))

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
