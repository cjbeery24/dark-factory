"""Main pipeline orchestrator — the 7-step pipeline.

Replaces scripts/dark-factory.sh. Coordinates spec validation,
scenario generation, the iteration loop, and post-pipeline actions
(registry, regression, git, notifications).
"""

from __future__ import annotations

import json
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from darkfactory.adapters import EcosystemAdapter
    from darkfactory.architecture.consistency import ConsistencyError
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
    emit_scenario_authoring_lint,
    emit_spec_shipped,
    emit_spec_stable_failure,
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
from darkfactory.pipeline.adapter_staging import (
    db_reset_active,
    stage_adapter_contracts,
    stage_db_reset,
)
from darkfactory.pipeline.agents import extract_agent_text, invoke_agent
from darkfactory.pipeline.archive import (
    init_archive_root,
    snapshot_final,
    snapshot_iteration,
    snapshot_iteration_diff_for_completed,
)
from darkfactory.pipeline.cleanup import CleanupManager
from darkfactory.pipeline.iteration import run_iteration_loop
from darkfactory.pipeline.quality_gate import SmokeTestResult, run_smoke_test
from darkfactory.scenarios.compat import check_format_compatibility
from darkfactory.scenarios.coverage import CoverageGap
from darkfactory.scenarios.known_gaps import (
    SKIPPABLE_CATEGORIES,
    KnownGap,
    KnownGapsFile,
    extract_anchor,
)
from darkfactory.scenarios.tool_docs import TOOL_DOCS
from darkfactory.scenarios.validator import validate_scenarios
from darkfactory.scenarios.detection import (
    count_scenario_files,
    find_misnamed_scenario_files,
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


def _from_phase_gate_missing_artifacts(project_dir: Path, spec_name: str) -> list[str]:
    """Return human-readable names of prior-run artifacts required by
    ``--from-phase gate`` that are absent (empty list = all present).

    Re-entering at the gate reuses the architect manifest and the
    test-engineer's scenarios from disk; if either is missing there is nothing
    to gate, so the caller rejects with a clear error.
    """
    from darkfactory.scenarios.detection import has_scenario_files, has_sentinel_file
    arch_manifest = (
        project_dir / ".dark-factory" / "architecture"
        / f"{spec_name}-manifest.yaml"
    )
    scen_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
    missing: list[str] = []
    if not arch_manifest.is_file():
        missing.append(f"architect manifest ({arch_manifest.name})")
    # Require ACTUAL scenario files (or a not-applicable sentinel) — a dir with
    # only junk / _contracts / a stale partial would pass `any(iterdir())` but
    # produce a confusing late gate failure (Codex). has_scenario_files screens
    # for real test files; a sentinel is a valid "no scenarios needed" state.
    if not (scen_dir.is_dir()
            and (has_scenario_files(scen_dir) or has_sentinel_file(scen_dir))):
        missing.append(f"scenario files ({scen_dir})")
    return missing


def run_pipeline(
    spec_name: str,
    config: PipelineConfig,
    project_dir: Path,
    *,
    resume: bool = False,
    fresh: bool = False,
    allow_resume: bool = True,
    regen_scenarios: bool = False,
    skip_validation: bool = False,
    run_regression_on_ship: bool = False,
    events_file: Path | None = None,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
    stop_after_step: int | None = None,
    no_adapter: bool = False,
    from_phase: str | None = None,
) -> int:
    """Run the full 7-step pipeline for a spec.

    Returns exit code: 0=shipped, 1=failed, 2=escalated, 3=aborted, 4=hold,
    5=stable_failure (eligible run, Oracle NO-SHIP — honest terminal non-ship).

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
        refresh_config_from_environment(config, env_for_overlay, project_dir)

    # Self-heal missing agent definitions BEFORE any agent runs. A stripped
    # .dark-factory/agents/ (gitignored framework files removed by a from-scratch
    # reset) makes every mounted-read role (TE/qa-holdout/spec-validator/...) read
    # "File does not exist" and run BLIND of its role rules — a silent,
    # campaign-corrupting failure (bb spec-4). Auto-restore the canonical package
    # copies (non-destructive; staleness stays init --update's job) + warn LOUDLY.
    # Unconditional on resume (a resumed run's next agent still needs them);
    # skipped on dry_run (a rehearsal must leave the tree intact).
    if not dry_run:
        from darkfactory.setup.init import (
            detect_agent_definition_drift,
            ensure_agent_definitions,
        )
        _restored_defs = ensure_agent_definitions(project_dir)
        if _restored_defs:
            log.warning(
                "Restored %d MISSING agent definition(s) from the framework "
                "package: %s. The project's .dark-factory/agents/ was incomplete "
                "— affected agents would have run WITHOUT their role definitions "
                "(silent quality loss). Refresh all with `dark-factory init "
                "--update`.",
                len(_restored_defs), _restored_defs,
            )
        # Warn (never overwrite) on present-but-STALE defs: a mounted-read role
        # (TE/qa-holdout/...) reads its PROJECT copy, so a drifted copy runs
        # OUTDATED role rules. We don't clobber (a real local edit is the user's);
        # `init --update` is the refresh path. Excludes the just-restored ones.
        _drifted_defs = [
            d for d in detect_agent_definition_drift(project_dir)
            if d not in _restored_defs
        ]
        if _drifted_defs:
            log.warning(
                "%d agent definition(s) in .dark-factory/agents/ DIFFER from the "
                "framework canonical: %s. Mounted-read roles read the project copy "
                "at runtime, so a stale copy runs outdated role rules. Refresh with "
                "`dark-factory init --update` (or keep the edit if intentional).",
                len(_drifted_defs), _drifted_defs,
            )

    from darkfactory.config.diagnostics import resolved_config_dump
    log.debug(resolved_config_dump(config))

    # Phase 7: deterministic preflight — validate effective config
    # before any agent work. $0 cost, catches stale/broken configs.
    if config.preflight_enabled:
        from darkfactory.pipeline.preflight import run_preflight_check
        preflight_errors = run_preflight_check(config, project_dir)
        if preflight_errors:
            detail = "\n  - ".join(preflight_errors)
            raise RuntimeError(
                f"Preflight failed — pipeline configuration is incomplete:\n"
                f"  - {detail}\n"
                f"Fix: run `dark-factory init` or update the environment contract.\n"
                f"Escape hatch: set [gates] preflight_enabled = false in config.toml."
            )

    # A5: resolve active ecosystem adapters and fail fast before any
    # Docker/LLM cost. Empty or unregistered ``active_adapters`` abort
    # here — the registry error message tells the user how to fix it
    # (``dark-factory init`` to populate, or ``--no-adapter`` for a
    # degraded run). Runs after preflight (preflight is adapter-
    # independent and gives a better first-failure signal for
    # config-file problems); before Docker setup (no point paying
    # container-startup cost for a configuration that can't dispatch).
    from darkfactory.adapters import load_active_adapters
    from darkfactory.architecture.project_hints import load_project_hints

    hints = load_project_hints(project_dir)
    active_adapters = load_active_adapters(
        hints, project_dir, no_adapter=no_adapter,
    )
    # Per-spec adapter activation (`spec_triggers`) runs after SpecRun
    # is created — see the call to ``activate_spec_triggered_adapters``
    # below ``emit_spec_start``. The architect step doesn't run until
    # much later in this function, so deferring the union that far
    # costs nothing and keeps the SPEC_ADAPTER_ACTIVATED event payload
    # well-formed (``run_id`` populated).

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
    # --from-phase gate manages completed_step/iteration itself (below) and must
    # not trip the terminal-status guard in _try_auto_resume (the prior run is
    # often 'escalated'), so skip auto-resume entirely for it.
    if allow_resume and not fresh and state_file.exists() and from_phase != "gate":
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

    # 2026-04-22 iteration-steering-invariants Task 1: sweep stale
    # per-iter artifacts from a prior run of this spec before the first
    # agent container launches. Skipped on resume — the current run's
    # files ARE the state we want to preserve. Also skipped on dry_run
    # (a rehearsal must leave the tree intact for re-inspection).
    # Placed *after* ``emit_spec_start`` so the RUN_START_CLEANUP event
    # is temporally contained in the spec lifecycle for post-mortem
    # replay — the inverse ordering would orphan the event from its
    # parent spec start record.
    # Lossless by construction: the prior run's terminal handlers
    # called ``snapshot_iteration`` / ``snapshot_final``, so every file
    # we purge here is already under
    # ``archive/<prior-ts>-<spec>-<short-id>/``.
    if not resume and not dry_run:
        from darkfactory.pipeline.run_start_cleanup import run_start_cleanup
        run_start_cleanup(
            project_dir, spec_name,
            run_id=spec_run.run_id,
            events_file=events_file,
        )

    # Per-run artifact archive. Observational — the pipeline never reads
    # from this path; its purpose is post-mortem inspection of iteration
    # 1's artifacts before iteration 2 overwrites them.
    spec_run.archive_root = init_archive_root(
        project_dir, spec_name, spec_run.run_id,
    )

    # G1 backstop (Move 2 of 2026-04-16 remediation): project-level
    # ``active_adapters`` is inferred at ``dark-factory init`` time
    # from a repo-tree scan; a spec authored later can demand ecosystem
    # coverage the scan missed (2026-04-16 database-foundation shipped
    # a spec full of RLS/tenant-isolation language against a project
    # whose active list was ``["typescript-node"]``, so the entire
    # wire-contracts regime was silently dark). Union any adapter
    # whose ``spec_triggers`` fires so the architect + scenario-
    # architect see the full ecosystem set. Emitting post-SPEC_START
    # keeps ``SPEC_ADAPTER_ACTIVATED`` payloads well-formed (run_id
    # populated) and the event-order readable as "spec started, then
    # these adapters joined the active set". The architect step runs
    # much later in this function, so the union lands in plenty of
    # time to shape downstream dispatch.
    #
    # ``--no-adapter`` is the documented degraded-run escape hatch;
    # honoring ``spec_triggers`` under it would re-insert adapters the
    # user explicitly asked to skip. The warning from
    # ``load_active_adapters`` already surfaces the degraded state.
    if not no_adapter:
        from darkfactory.adapters import activate_spec_triggered_adapters
        active_adapters = activate_spec_triggered_adapters(
            active_adapters,
            spec_text=spec_file.read_text(encoding="utf-8"),
            project_dir=project_dir,
            events_file=events_file,
            spec_name=spec_name,
            run_id=spec_run.run_id,
        )
    log.info(
        "active adapters: %s",
        ", ".join(a.name for a in active_adapters) if active_adapters else "(none)",
    )

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

    # Capture a pre-run snapshot: (a) a ``git stash create`` sha
    # representing tracked staged + unstaged changes, (b) the set of
    # untracked paths. The two together let the deterministic preflight
    # distinguish implementer-authored work from operator-authored
    # pre-existing state. ``git stash create -u`` alone is unreliable
    # for untracked files on real git versions, so we capture them
    # separately. Empty ref means the tree was clean; empty untracked
    # set means nothing untracked at run start.
    if not spec_run.preflight_base_ref and not spec_run.preflight_untracked_baseline:
        (
            spec_run.preflight_base_ref,
            spec_run.preflight_untracked_baseline,
        ) = _capture_preflight_baseline(project_dir)

    # #11: --from-phase gate. Re-enter at the gate with the EXISTING on-disk
    # implementation — skipping validation / architecture / test-engineer (their
    # artifacts are reused from disk) and the first iteration's INITIAL
    # implementer — to verify a gate/framework fix against a known-correct impl,
    # and ship it, without re-running the expensive upstream agents. The
    # implementer skip is ONE-SHOT (skip_implementer_once): if pre-gate
    # (lint/typecheck/build) fails in iteration 1, the pre-gate retry loop can
    # still re-invoke the implementer. For the intended use (a known-correct
    # impl that passes pre-gate) the implementer never runs. A sticky skip
    # across pre-gate retries is a tracked follow-up (#11). Also note:
    # author-declared blocking-AC enforcement (ac_blocking) is populated only in
    # the skipped validation phase, so it is INERT under --from-phase gate —
    # only matters on the SHIP_WITH_GAPS path, not a clean full ship. Rejects if
    # prior-run artifacts are absent. active_adapters is already loaded above
    # (independent of completed_step), so substrate verification still runs.
    if from_phase == "gate":
        _missing = _from_phase_gate_missing_artifacts(project_dir, spec_name)
        if _missing:
            raise DarkFactoryError(
                "--from-phase gate requires prior-run artifacts on disk, but "
                + " and ".join(_missing) + " missing. Run the full pipeline "
                "through at least the test-engineer phase first."
            )
        # Coverage-floor verification (Codex BLOCK). Skipping the test-engineer
        # phase also skips coverage-floor ENFORCEMENT, so a spec whose prior run
        # aborted AT the floor (partial scenarios) could otherwise ship via
        # --from-phase gate. Re-run the floor in VERIFY-ONLY mode — regen and
        # architect-amend disabled, so NO agents are invoked — against the
        # on-disk scenarios. Fail-closed: _enforce_coverage_floor raises
        # COVERAGE_FLOOR_VIOLATION (with remediation) if anchors are uncovered
        # or the spec-type floor (e.g. bugfix @regression) is unmet.
        import dataclasses as _dataclasses
        _verify_floor_cfg = _dataclasses.replace(
            config, coverage_max_regen_attempts=0, coverage_architect_amend=False,
        )
        _enforce_coverage_floor(
            spec_run, _verify_floor_cfg, barriers, project_dir,
            scenarios_dir=(project_dir / ".dark-factory" / "scenarios" / spec_name),
            base_prompt="", events_file=events_file, audit_path=audit_path,
            dry_run=dry_run, active_adapters=active_adapters,
        )
        # completed_step 3 skips validation (<1) / architecture (<2) /
        # test-engineer + coverage-floor (<3); iteration 1 + the one-shot
        # implementer skip enter the loop straight at the gate.
        spec_run.completed_step = 3
        spec_run.iteration = 1
        spec_run.skip_implementer_once = True
        if state_file is not None:
            write_state_or_warn(state_file, "completed_step", "3")
            write_state_or_warn(state_file, "iteration", "1")
            write_state_or_warn(state_file, "status", "running")
        _progress(
            "--from-phase gate: re-entering at the gate with the existing "
            "on-disk implementation (validation/architecture/test-engineer and "
            "the first implementer are skipped)."
        )

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
            precheck_result = run_precheck(
                spec_file,
                project_dir=project_dir,
                p4_mechanism_gate_enabled=config.p4_mechanism_token_gate_enabled,
            )
            # Always persist the advisory, regardless of fatal/advisory split.
            try:
                write_precheck_advisory(project_dir, spec_run.spec_name, precheck_result)
            except (OSError, TypeError) as exc:
                log.warning("failed to persist precheck advisory: %s", exc)
            if precheck_result.fatal:
                # C1: structural precheck failure is a hard pre-implementer
                # gate. Emit a REMEDIATION_TARGET so C2/C3 can surface an
                # actionable next-step.
                _emit_remediation_if_enabled(
                    events_file,
                    spec=spec_run.spec_name,
                    run_id=spec_run.run_id,
                    failure_class="precheck_finding",
                    rule_or_check="precheck_structural",
                    remediation_type="edit_spec",
                    remediation_target_path=(
                        f".dark-factory/specs/{spec_run.spec_name}.md"
                    ),
                    auto_applicable=False,
                    rationale=precheck_result.summary,
                )
                raise DarkFactoryError(
                    f"spec pre-check failed (structural):\n{precheck_result.summary}",
                    run_id=spec_run.run_id,
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
                # Filter subprocess ACs from ac_ids before persisting.
                # Both the test-engineer and coverage floor read the on-disk
                # manifest; subprocess ACs must not be in ac_ids or the
                # coverage floor will expect tests that don't exist.
                manifest = precheck_result.anchor_manifest
                # subprocess anchors are covered by Tier-1 contracts; author-
                # declared @untestable anchors are explicitly waived. Both must
                # leave the scored id lists or the coverage floor will demand
                # Tier-2 scenarios that don't (and shouldn't) exist. Markers can
                # sit on AC *or* EC rows (Edge Cases / Error Handling), so strip
                # every scored list, not just ac_ids.
                # DELIBERATELY NOT included: manifest.runtime_boundary_ac_ids.
                # Those anchors stay in the scored lists — they keep their
                # scenario + blind-holdout demand; the runtime-boundary marker
                # only exempts the covering scenario from the behavioral-credit
                # IMPORT requirement (handled in the coverage floor). Excluding
                # them here would collapse the marker into @untestable and remove
                # the holdout backstop that makes it safe — do not add it.
                excluded = set(manifest.subprocess_ac_ids) | set(
                    manifest.untestable_ac_ids
                )
                if excluded:
                    manifest.ac_ids = [a for a in manifest.ac_ids if a not in excluded]
                    manifest.ec_ids = [a for a in manifest.ec_ids if a not in excluded]
                    manifest.fr_ids = [a for a in manifest.fr_ids if a not in excluded]
                try:
                    manifest.to_json_path(
                        manifest_path(project_dir, spec_run.spec_name)
                    )
                except OSError as exc:
                    log.warning("failed to persist anchor manifest: %s", exc)

            # Phase D: publish author-declared blocking ACs onto the
            # SpecRun so should_ship_with_gaps can veto promotion when
            # any failing test maps to a blocking ID. Empty dict is a
            # legal "author declared no blocking ACs" state (the
            # precheck rule required a justification paragraph in that
            # case).
            spec_run.ac_blocking = dict(precheck_result.blocking_acs)

        # Step 1: Spec validation.
        if spec_run.completed_step < 1:
            # --skip-validation: reuse a prior PASSing validation IFF the spec
            # bytes are byte-identical to what was validated (host-computed
            # hash match) AND a PASS .status artifact is still present. Both
            # are required and the decision FAILS CLOSED — any miss falls
            # through to a full LLM validation. Only the slow non-deterministic
            # validator AGENT is skipped; the deterministic post-validation
            # gates inside _step_spec_validation (error-message consistency,
            # free-variables, consistency-ledger) STILL run against the
            # cached, unchanged artifacts (Neo design Q3, 2026-06-16).
            reviews_dir = project_dir / ".dark-factory" / "reviews"
            reuse_validation = _should_reuse_validation(
                skip_validation, state_file, reviews_dir,
                spec_run.spec_name, spec_run.spec_hash,
            )
            if reuse_validation:
                _progress(
                    "Reusing prior spec validation (spec unchanged, PASS on "
                    "record) — skipping LLM validator agent"
                )
            else:
                _progress("Validating spec...")
            # Hash the spec bytes IMMEDIATELY before validation launches, so
            # the stamp reflects what the validator is about to read — not the
            # start-of-run snapshot (spec_run.spec_hash, line ~329) and not the
            # post-run disk state. Hashing post-return would widen the
            # change-window to the entire agent runtime (minutes); hashing here
            # narrows it to framework launch → the agent's first in-container
            # Read of the spec (sub-second). That residual gap is irreducible
            # host-side — closing it fully would require trusting an
            # agent-reported hash, which the trust model forbids (the host
            # computes the hash, never the agent). It is a non-adversarial
            # window: single-user tool, the spec is the user's own file, and a
            # mid-run edit already violates broader run assumptions.
            validated_hash = sha256_file(spec_file)
            _step_spec_validation(
                spec_run, config, barriers, project_dir,
                events_file=events_file, audit_path=audit_path, dry_run=dry_run,
                active_adapters=active_adapters,
                reuse_validation=reuse_validation,
            )
            # Stamp host-side ONLY after the step returns PASS + all
            # deterministic gates pass (the step raises otherwise, so this is
            # unreachable on failure). Never sourced from the agent-authored
            # .status. A re-stamp on a reused run writes the same value.
            write_state_or_warn(
                state_file, "validated_spec_hash", validated_hash
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
                active_adapters=active_adapters,
            )

        # Step 1.75: Proposal-test typecheck rehearsal (C2).
        # Runs after architect, before scenario generation. Validates
        # that AC/FR/EC anchors are actually unit-testable using the
        # pinned toolchain. On failure, raises DarkFactoryError which
        # aborts the pipeline before scenario gen burns a full iter.
        # No-ops on non-TS projects; uses completed_step < 2 so resume
        # re-runs it until scenarios are generated.
        if (
            config.proposal_rehearsal_enabled
            and spec_run.completed_step < 2
            and not dry_run
        ):
            _step_proposal_rehearsal(
                spec_run, config, barriers, project_dir,
                events_file=events_file, audit_path=audit_path,
            )

        # Step 2: Scenario generation.
        scenarios_dir = project_dir / ".dark-factory" / "scenarios" / spec_run.spec_name
        if spec_run.completed_step < 2:
            _progress("Generating blind test scenarios...")
            _step_scenario_generation(
                spec_run, config, barriers, project_dir,
                regen=regen_scenarios,
                events_file=events_file, audit_path=audit_path, dry_run=dry_run,
                active_adapters=active_adapters,
            )
            write_state_or_warn(state_file, "completed_step", "2")
            spec_run.completed_step = 2
            _progress("Scenarios generated")
            notify_scenarios_generated(config, spec_name, spec_run.run_id)

        # Step 2.4: TE pre-acceptance §4a check (ROADMAP.md Phase 2 B1).
        # Host-side lint pass on TE-emitted scenarios. Hits route back
        # to the TE for retry; budget exhaustion raises HardAbort with
        # ``scenario_author_retry_exhausted``. Feature-flagged off by
        # default per ROADMAP.md "Risk" mitigation — opt in via
        # ``[gates]\nte_pre_acceptance_check = true``. Does not advance
        # ``completed_step`` (the iteration loop's gate, already stable
        # at step 2, is the relevant resume boundary). On resume from
        # ``completed_step >= 3`` the iteration loop has already started
        # and pre-acceptance does not re-run — its job is one-shot
        # quality control on the emission, not per-iteration policing.
        #
        # Resume semantics on a crash mid-retry-loop (``completed_step
        # < 3``): the phase restarts from attempt 0 with a fresh budget.
        # Per-attempt audit files are overwritten by index, the
        # ``last_retry_exit`` accumulator resets, and the lint runs
        # against whatever scenarios survive on disk. This is an
        # accepted trade-off — idempotent partial-loop resume would
        # require persisting attempt counter + last_retry_exit to state
        # and replaying audit files on entry, which is not justified
        # while the phase is opt-in and crashes inside a single TE
        # invocation are rare.
        if (
            config.te_pre_acceptance_check
            and spec_run.completed_step < 3
            and not dry_run
        ):
            _progress("Running TE pre-acceptance check...")
            _step_te_pre_acceptance(
                spec_run, config, barriers, project_dir,
                events_file=events_file, audit_path=audit_path,
                dry_run=dry_run,
            )
            _progress("  Pre-acceptance: clean")

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
            # Ecosystem-blind dispatch: each active adapter checks its own
            # workspace-script consistency. The pnpm-specific grammar lives
            # in the TypeScript adapter; cargo/Go/etc. inherit the ABC's
            # default no-op (no per-member-script concept). The core stays
            # free of pnpm CLI grammar.
            ws_errors: list[tuple[str, list[str]]] = []
            for adapter in active_adapters:
                ws_result = adapter.check_workspace_consistency(
                    project_dir,
                    config.lint_cmd or "",
                    config.typecheck_cmd or "",
                    config.test_cmd or "",
                )
                for warning in ws_result.warnings:
                    log.warning("workspace check: %s", warning)
                ws_errors.extend(ws_result.errors)
            if ws_errors:
                lines = [
                    f"  {pkg}: missing {', '.join(repr(s) for s in scripts)}"
                    for pkg, scripts in ws_errors
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
            active_adapters=active_adapters,
        )

        # Sprint 1 invariant: a run is never both shipped and stable_failure.
        # If a future edit sets the flag AND returns True, `if shipped` would
        # win and ship a stable-failure run — assert against that regression.
        assert not (shipped and spec_run.stable_failure), (
            "invariant violation: run marked both shipped and stable_failure"
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
        elif spec_run.stable_failure:
            return _handle_stable_failure(
                spec_run, config, project_dir,
                state_file=state_file, events_file=events_file,
                metrics_file=metrics_file,
                dry_run=dry_run,
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

    except (DarkFactoryError, BudgetExceeded) as e:
        # Broadened from ``(HardAbort, BudgetExceeded)`` after run
        # d717e915's preflight-persisted raise bypassed the archive
        # handler. ``HardAbort`` subclasses ``DarkFactoryError``, so
        # any abort-path raise — whether the caller remembered to use
        # ``HardAbort`` or fell back to bare ``DarkFactoryError`` —
        # now routes through ``_handle_abort`` and writes
        # ``final/meta.json``. All current ``raise DarkFactoryError(...)``
        # sites reachable in this try scope (docker/git/spec/manifest
        # pre-flight failures, validator failures, etc.) are terminal
        # by intent, so the broadening is safe today. A future
        # ``TerminalAbort(DarkFactoryError)`` marker subclass would
        # narrow this catch to just abort-path raises without relying
        # on auditing every sibling raise — see backlog.
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


def _compose_adapter_addenda(
    active_adapters: "list[EcosystemAdapter]",
    getter: "Callable[[EcosystemAdapter], str]",
    *,
    log_label: str,
) -> str:
    """Concatenate adapter addendum fragments for prompt injection.

    Replaces three near-identical inline blocks (architect + validator
    + future scenario-architect). Single source for: ``OSError``-only
    catch (programmer errors propagate so a broken adapter fails loud
    instead of silently dropping its contribution), empty-string opt-
    out filtering (``""`` is the documented sentinel pinned by the
    contract harness), and adapter-declaration-order concatenation.

    Returns ``""`` when no active adapters or all addenda are empty;
    callers append a blank-line separator before non-empty results.
    """
    blocks: list[str] = []
    for adapter in active_adapters:
        try:
            block = getter(adapter)
        except OSError as exc:
            log.warning(
                "adapter %s %s I/O error %s; "
                "continuing without its prompt contribution",
                adapter.name, log_label, exc,
            )
            continue
        if block:
            blocks.append(block.rstrip())
    return "\n\n".join(blocks)


def _record_unresolved_contracts_advisory(
    contracts: list[dict], spec_run: "SpecRun", reviews_dir: Path,
) -> None:
    """F (2026-06-16): record advisory unresolved_contracts (hard gate off).

    The LLM ``unresolved_contracts`` check is non-deterministic and emits false
    positives that contradict the spec bytes, so by default it does NOT abort
    (``unresolved_contracts_hard_gate=false``). We still surface its entries —
    log a warning and write a discoverable advisory note beside the validation
    report — so the signal isn't lost, but the run proceeds. Never raises.
    """
    advisory_path = (
        reviews_dir / f"{spec_run.spec_name}-unresolved-contracts-advisory.md"
    )
    log.warning(
        "unresolved_contracts ADVISORY (hard gate disabled): validator flagged "
        "%d entr%s — NOT blocking; recorded to %s",
        len(contracts), "y" if len(contracts) == 1 else "ies",
        advisory_path.name,
    )
    try:
        advisory_path.write_text(
            "# Unresolved-contracts ADVISORY\n\n"
            "The validator emitted `unresolved_contracts` entries. The hard gate "
            "is disabled (`unresolved_contracts_hard_gate=false`), so this is "
            "advisory only and did NOT block the run.\n\n"
            + "\n".join(
                f"- {_format_structural_field(e, 'surface')} "
                f"({_format_structural_field(e, 'section')}): "
                f"{_format_diverged_field(e)}"
                for e in contracts
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        log.debug("could not write unresolved-contracts advisory: %s", exc)


def _enforce_unresolved_contracts_gate(
    *,
    contracts: list[dict] | None,
    report_text: str,
    report_path: Path,
    spec_run: "SpecRun",
    config: "PipelineConfig",
    barriers: "dict[AgentRole, BarrierConfig]",
    project_dir: Path,
    events_file: Path | None,
    audit_path: Path | None,
    dry_run: bool,
) -> None:
    """Legacy LLM ``unresolved_contracts`` HARD gate.

    Only invoked when ``config.unresolved_contracts_hard_gate`` is True (default
    False — see F, 2026-06-16: this gate is non-deterministic and hallucinated
    false divergences that aborted 3 consecutive runs). Re-prompts once if the
    block is missing or its ``sketches_diverged_on`` reasons are vacuous, then
    raises ``DarkFactoryError`` if any non-vacuous divergence survives.
    """
    from darkfactory.spec.validator_output import (
        is_vacuous_divergence_reason,
        parse_unresolved_contracts,
    )

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

    if contracts:
        # Reached when validator emitted real non-vacuous contracts; prints and fails.
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


def _should_reuse_validation(
    skip_validation: bool,
    state_file: Path,
    reviews_dir: Path,
    spec_name: str,
    current_spec_hash: str,
) -> bool:
    """Decide whether --skip-validation may reuse a prior LLM validation.

    Reuse is permitted ONLY when ALL hold (fail-closed — any miss → False →
    full validation):
      1. ``--skip-validation`` was requested.
      2. The host-stamped ``validated_spec_hash`` (state) equals the current
         spec hash — proving the spec bytes are byte-identical to what the
         validator agent last PASSed. The hash is host-computed
         (``sha256_file``), never read from the agent-authored .status file.
      3. BOTH the ``<spec>-validation.status`` AND ``<spec>-validation.md``
         artifacts are present, AND the status parses to a PASSing verdict.
         - Requiring the ``.status`` explicitly (not just any output
           ``validate_spec_result`` would accept) closes the markdown-fallback
           hole: ``validate_spec_result`` falls back to grepping the ``.md``
           report for ``verdict: pass`` when no ``.status`` exists
           (detection.py), which is a looser gate than the structured status.
           Reuse must rest on the structured ``.status``.
         - Requiring the ``.md`` report guarantees the deterministic
           post-validation gates downstream in ``_step_spec_validation``
           (unresolved-contracts parse, free-variables audit, consistency
           ledger) actually RUN on the reuse path — they early-return if the
           ``.md`` report is absent, which would silently skip them.
         Hash-match alone is insufficient: the spec can be reverted to a
         previously-validated hash while the on-disk ``.status`` reflects a
         LATER failed validation — the status gate catches that and refuses
         reuse. The hash, the status, and the report are sourced
         independently; the AND of all of them is what makes the decision
         fail-closed.
    """
    if not skip_validation:
        return False
    stored_hash = read_state(state_file, "validated_spec_hash")
    if not stored_hash or stored_hash != current_spec_hash:
        return False
    status_file = reviews_dir / f"{spec_name}-validation.status"
    report_file = reviews_dir / f"{spec_name}-validation.md"
    if not status_file.is_file() or not report_file.is_file():
        return False
    passed, _reason = validate_spec_result(reviews_dir, spec_name)
    return passed


def _step_spec_validation(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
    active_adapters: "list[EcosystemAdapter]",
    reuse_validation: bool = False,
) -> None:
    """Step 1: Validate the spec.

    When ``reuse_validation`` is True the caller has already proven (via
    ``_should_reuse_validation``) that the spec bytes are unchanged since a
    prior PASSing validation, so the slow non-deterministic LLM validator
    AGENT invocation is skipped. The deterministic post-validation gates below
    (error-message consistency, free-variables, consistency-ledger) STILL run
    against the cached, unchanged on-disk artifacts — "skip-validation" means
    precisely "skip the LLM agent," not "skip every gate."
    """
    step_begin(spec_run.spec_name, "spec_validator", config, project_dir)
    from darkfactory.spec.validator_output import (
        audit_free_variables,
        parse_unresolved_contracts,
    )

    prompt = (
        # Phase 3.5 Session 1.5: validator is NOT in
        # ``_AGENT_DEF_INJECT_ROLES`` (agents.py:64), so its definition is
        # NOT injected via --append-system-prompt. Architect / test-engineer
        # / qa-holdout get their definitions via this prompt-level "Read
        # your agent definition" instruction (orchestrator.py:1728, 2890,
        # 3494); the validator was the only consistency-procedure-heavy
        # role missing it. Without this line, production runs at temp=0
        # may operate without the typology SSOT in context — the harness
        # measurement (which embeds agent.md as system-append) was
        # unintentionally measuring a best case that did not exist in
        # production.
        f"Read your agent definition at /project/.dark-factory/agents/spec-validator.md before anything else. "
        f"Validate the spec at /project/.dark-factory/specs/{spec_run.spec_name}.md. "
        f"Write the full validation report to /project/.dark-factory/reviews/{spec_run.spec_name}-validation.md. "
        f"Write the machine-readable status file to /project/.dark-factory/reviews/{spec_run.spec_name}-validation.status "
        f"using EXACTLY this format (lowercase keys, one per line, no comments, no extra fields):\n"
        f"verdict=PASS\n"
        f"score=<integer 0-100>\n"
        f"divergence=<LOW|MEDIUM|HIGH>\n"
        f"blockers=<integer count of Critical Issues that must be fixed before implementation>\n"
        f"consistency=<PASS|FAIL>\n\n"
        f"At the END of your validation report markdown file, you MUST emit a fenced "
        f"```yaml block containing an `unresolved_contracts:` key (empty list `[]` if "
        f"none diverge across your Precision Analysis sketches; otherwise list each "
        f"surface, section, and divergence). This block is required on every run.\n\n"
        f"Run the Cross-Section Contradiction Procedure described in your agent "
        f"definition. Write the per-FR claim ledger and the contradictions list to "
        f"/project/.dark-factory/reviews/{spec_run.spec_name}-consistency-ledger.yaml. "
        f"Set `consistency=FAIL` whenever the ledger's `contradictions:` list is "
        f"non-empty; set `consistency=PASS` only when the extraction pass produced a "
        f"non-empty `fr_ledgers:` block AND the contradictions walk found no T1-T5 "
        f"entries. Each contradiction also contributes 1 to `blockers`."
    )

    addenda = _compose_adapter_addenda(
        active_adapters,
        lambda a: a.spec_validator_addendum(),
        log_label="spec_validator_addendum()",
    )
    if addenda:
        prompt += "\n\n" + addenda

    # Experiment instrumentation (env-gated; INERT unless DF_DUMP_VALIDATOR_PROMPT
    # is set). Captures the EXACT assembled validator task-prompt (including any
    # adapter addenda) so a confirming run can prove input-identity across isolated
    # copies and analyze the adapter-on vs adapter-off prompt diff. No behavior
    # change when the env var is unset.
    _df_prompt_dump = os.environ.get("DF_DUMP_VALIDATOR_PROMPT")
    if _df_prompt_dump:
        try:
            _dd = Path(_df_prompt_dump)
            _dd.mkdir(parents=True, exist_ok=True)
            (_dd / f"{spec_run.spec_name}-validator-prompt.txt").write_text(
                prompt, encoding="utf-8"
            )
        except OSError as _exc:
            log.warning("DF_DUMP_VALIDATOR_PROMPT dump failed (non-fatal): %s", _exc)

    if reuse_validation:
        # The slow non-deterministic LLM validator agent is skipped; the
        # caller proved the spec bytes are unchanged since a prior PASS. Fall
        # through to verify the cached .status + run the deterministic gates.
        log.info(
            "spec validation reused (spec unchanged since prior PASS) — "
            "LLM validator agent skipped"
        )
    else:
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
        # C1: validator-level failure → emit REMEDIATION_TARGET with
        # auto_applicable=false (humans must edit the spec). The rule_or_check
        # is "spec_validator" so C5 can compute per-gate remediation rates
        # without needing to parse the validator's free-form reason text.
        _emit_remediation_if_enabled(
            events_file,
            spec=spec_run.spec_name,
            run_id=spec_run.run_id,
            failure_class="spec_quality",
            rule_or_check="spec_validator",
            remediation_type="edit_spec",
            remediation_target_path=(
                f".dark-factory/specs/{spec_run.spec_name}.md"
            ),
            auto_applicable=False,
            rationale=f"spec validation failed: {reason}",
        )
        raise DarkFactoryError(
            f"spec validation failed: {reason}",
            run_id=spec_run.run_id,
        )

    # Enforce unresolved_contracts block. Re-prompt once if missing.
    if dry_run:
        return

    # Sprint 3b: deterministic error-message consistency lint. A true backstop —
    # it reads only the SPEC file, so it runs independent of the LLM's .status /
    # ledger / .md report (placed here, before the report-dependent early-return
    # below, so a missing .md doesn't skip it). It fires even when the LLM said
    # consistency=PASS, because the validator's FR-centric Pass-1 extraction
    # (spec-validator.md:167-209) structurally never walks the §5 error-message
    # table or §10 examples where the strings live (measured: the LLM catches
    # this class only ~2/4 runs). Catches "same error code → ≥2 distinct literal
    # messages" deterministically (N/N). Audience: pipeline-halt only — failure
    # is a SPEC_FAILED DarkFactoryError of spec text; writes no
    # .dark-factory/reports/ row and fires before implementation (no barrier
    # crossing).
    from darkfactory.spec.error_message_consistency import (
        format_error_message_failure,
        validate_error_message_consistency,
    )

    spec_md = project_dir / ".dark-factory" / "specs" / f"{spec_run.spec_name}.md"
    if spec_md.exists():
        msg_violations = validate_error_message_consistency(
            spec_md.read_text(encoding="utf-8")
        )
        if msg_violations:
            raise DarkFactoryError(format_error_message_failure(msg_violations))

    # ─── Substrate-ELICITATION GATE (PROVISIONAL, 2026-06-22) ───────────────
    # The ROOT of the deferral chain: make the framework ASK for verifiable=false
    # instead of relying on author pipeline-expertise. A substrate primitive that
    # DEFAULTS TO VERIFY (no explicit verifiable=false) will be gate-verified —
    # but if the spec's own Out-of-Scope/FRs make clear its enabling infra is
    # DEFERRED, that's a contradiction the gate would otherwise only hit 2-3h
    # late as "table missing". Deterministic candidate extraction + ONE narrow
    # LLM judgment (in-scope-per-FRs vs deferred-per-Out-of-Scope), reason-then-
    # verdict, measured 12/12 EXACT (opus 6/6 + sonnet 6/6, perfectly stable) on
    # bb spec-4. Runs BEFORE the deferral gate (elicit verifiable=false → then
    # that gate checks the ACs).
    # Same PROVISIONAL posture + same false-negative backstop (the late substrate
    # gate) as the deferral gate. See darkfactory/spec/deferral_check.py.
    if spec_md.exists():
        from darkfactory.spec.deferral_check import (
            build_elicitation_prompt,
            extract_elicitation_inputs,
            parse_elicitation_output,
            write_deferral_consistency_report,
        )
        _ei = extract_elicitation_inputs(spec_md.read_text(encoding="utf-8"))
        if _ei is not None:
            _eres = invoke_agent(
                AgentRole.SPEC_VALIDATOR,
                build_elicitation_prompt(_ei),
                config=config, barriers=barriers,
                run_id=spec_run.run_id, project_dir=project_dir,
                spec_name=spec_run.spec_name, iteration=0,
                events_file=events_file, audit_path=audit_path,
                dry_run=dry_run, phase="elicitation_check",
            )
            if _eres.exit_code != 0:
                log.warning(
                    "elicitation-gate: focused check agent failed (exit %d) — "
                    "skipping; late substrate gate remains the backstop",
                    _eres.exit_code,
                )
                _need_vf: set[str] = set()
            else:
                _need_vf = parse_elicitation_output(
                    extract_agent_text(_eres.output), _ei.candidate_primitives,
                    _ei.alias_to_type,
                )
            if _need_vf:
                try:
                    write_deferral_consistency_report(
                        reviews_dir, spec_run.spec_name, spec_run.run_id,
                        [
                            {
                                "kind": "MISSING_VERIFIABLE_FALSE",
                                "primitive": p, "anchor": None,
                                "message": (
                                    f"substrate primitive [[{p}]] defaults to "
                                    f"gate-verified, but the spec defers its "
                                    f"enabling infrastructure to a later phase "
                                    f"(Out-of-Scope) — mark it `verifiable=false` "
                                    f"or bring its infrastructure into scope."
                                ),
                            }
                            for p in sorted(_need_vf)
                        ],
                    )
                except OSError as exc:
                    log.warning("elicitation-gate report write failed: %s", exc)
                raise DarkFactoryError(
                    f"substrate elicitation gate: primitive(s) "
                    f"{sorted(_need_vf)} will be gate-verified but their "
                    f"enabling infrastructure is deferred to a later phase "
                    f"(per the spec's Out-of-Scope) — mark them "
                    f"`verifiable=false` (or bring the infra into scope) via "
                    f"/df-rescope-deferred. (Provisional gate — see "
                    f"darkfactory/spec/deferral_check.py.)",
                    run_id=spec_run.run_id,
                )

    # ─── Substrate-deferral GATE (PROVISIONAL, 2026-06-22) ──────────────────
    # Surface — at validation time, cheaply, pre-pipeline — a Blocking AC that
    # asserts the OUTCOME of a `verifiable=false` (deferred) substrate
    # capability (its enforcement does not exist this phase, so its scenario
    # cannot pass). DETERMINISTIC, STACK-AGNOSTIC precondition (a verifiable=false
    # primitive + a Blocking anchor — no per-adapter rule taxonomy) + ONE NARROW,
    # focused LLM judgment (outcome-vs-mechanism), reason-then-verdict, run K
    # times with a MAJORITY vote (per-run 10/12 EXACT, FP=0, recall wobble
    # recovered by the vote — see deferral_check). The narrow framing is why it
    # is tractable: the holistic validator's flip-flop comes from open-ended
    # defect-hunting, not bounded judgments — so this runs as a SEPARATE focused
    # call, NOT folded into the main validator prompt.
    #
    # ⚠ PROVISIONAL GATE — RE-EVALUATE if specs get stuck here ILLEGITIMATELY
    # (a false positive blocking a spec whose Blocking AC is genuinely an
    # in-scope mechanism). Reliability evidence is one realistic case; AMBIGUOUS
    # outcome-vs-mechanism anchors are unmeasured. If false positives appear,
    # demote to a REVISE-class advisory or add an author override marker. The
    # coverage-floor F/E2/E3 check (coverage.check_substrate_deferral_consistency)
    # is the DETERMINISTIC backstop, so demoting never loses the catch — it only
    # moves it later in the pipeline. See darkfactory/spec/deferral_check.py.
    #
    # Runs regardless of ``reuse_validation`` (it sits after that branch merges):
    # this is a SEPARATE focused check the cached prior PASS never ran, so
    # gating it behind ``not reuse_validation`` would let --skip-validation
    # BYPASS the gate. One extra focused LLM call on a reused validation is the
    # price of the gate not being bypassable.
    if spec_md.exists():
        from darkfactory.spec.deferral_check import (
            DEFERRAL_VOTE_K,
            build_deferral_check_prompt,
            extract_deferral_check_inputs,
            majority_vote,
            parse_deferral_check_output,
        )
        from darkfactory.spec.precheck import check_non_negotiable_section
        from darkfactory.spec.spec_model import parse_spec_model

        _spec_txt = spec_md.read_text(encoding="utf-8")
        _di = None
        try:
            _, _nn_map = check_non_negotiable_section(parse_spec_model(_spec_txt))
            _di = extract_deferral_check_inputs(_spec_txt, set(_nn_map))
        except Exception as exc:  # noqa: BLE001 — precondition parse is best-effort
            log.debug("deferral-gate: precondition extraction failed: %s", exc)
        if _di is not None:
            log.info(
                "deferral-gate: precondition met (%d deferred primitive(s), "
                "%d candidate anchor(s), %d blocking) — running focused check",
                len(_di.deferred_primitive_names), len(_di.candidate_anchors),
                len(_di.blocking_anchors),
            )
            # Run the focused check until we have DEFERRAL_VOTE_K SUCCESSFUL
            # votes, then take the MAJORITY. The per-run judgment has zero false
            # positives but a ~1-in-6 recall wobble on subtler anchors (measured,
            # reason-then-verdict); majority-of-K recovers a single-run miss while
            # suppressing a single-run spurious flag (see majority_vote).
            #
            # We REFILL on a failed vote (exit!=0) rather than deciding on a
            # shrunken panel (Neo): majority_vote keeps an item iff it is in
            # STRICTLY MORE THAN HALF the votes, so a panel of 2 demands
            # UNANIMITY — under which a single-run recall miss would DROP the item
            # (worse than single-call, the opposite of the ensemble's purpose).
            # Keeping the panel at the intended odd K preserves the recall
            # property. The refill is bounded (2*K attempts) so a systematically
            # broken agent cannot hang the gate; if the cap is hit with a
            # degraded/empty panel we decide on what we have (an empty panel →
            # fail OPEN to the coverage-floor F/E2/E3 backstop), logged loudly so
            # a broken check cannot masquerade as "every spec is clean".
            _votes: list[set[str]] = []
            _vote_failures = 0
            _attempts = 0
            _max_attempts = DEFERRAL_VOTE_K * 2
            while len(_votes) < DEFERRAL_VOTE_K and _attempts < _max_attempts:
                _attempts += 1
                _dres = invoke_agent(
                    AgentRole.SPEC_VALIDATOR,
                    build_deferral_check_prompt(_di),
                    config=config, barriers=barriers,
                    run_id=spec_run.run_id, project_dir=project_dir,
                    spec_name=spec_run.spec_name, iteration=0,
                    events_file=events_file, audit_path=audit_path,
                    dry_run=dry_run, phase="deferral_check",
                )
                if _dres.exit_code != 0:
                    _vote_failures += 1
                    continue
                _votes.append(
                    parse_deferral_check_output(extract_agent_text(_dres.output))
                )
            if not _votes:
                log.warning(
                    "deferral-gate: all %d focused-check attempts failed — "
                    "skipping gate this run; coverage-floor backstop remains",
                    _attempts,
                )
                _flagged: set[str] = set()
            else:
                if len(_votes) < DEFERRAL_VOTE_K:
                    log.warning(
                        "deferral-gate: only %d/%d successful votes after %d "
                        "attempts (%d failures); deciding on a DEGRADED panel "
                        "(stricter than majority-of-%d — recall leans on the "
                        "coverage-floor backstop)",
                        len(_votes), DEFERRAL_VOTE_K, _attempts, _vote_failures,
                        DEFERRAL_VOTE_K,
                    )
                _flagged = majority_vote(_votes)
            if _flagged:
                # Persist the SAME report shape the floor writes, so
                # /df-rescope-deferred can apply the re-scope from validate-time
                # feedback (no hand edits). EVERY flagged anchor is a would-be
                # live scenario target (→ @untestable:forward-reference, E3);
                # the subset that is ALSO Blocking (in §8a) additionally needs
                # dropping from Non-Negotiable (E2). Emit the kinds accordingly
                # so the skill applies the right action(s) per anchor.
                try:
                    from darkfactory.spec.deferral_check import (
                        write_deferral_consistency_report,
                    )
                    _prim = ", ".join(_di.deferred_primitive_names)
                    _blk = set(_di.blocking_anchors)
                    _findings_out: list[dict] = []
                    for _a in sorted(_flagged):
                        _findings_out.append({
                            "kind": "LIVE_SCENARIO_OVER_DEFERRED", "primitive": _prim,
                            "anchor": _a,
                            "message": (
                                f"{_a} asserts the OUTCOME of a verifiable=false "
                                f"(deferred) substrate capability ({_prim}) — mark "
                                f"@untestable:forward-reference so it is not "
                                f"scenario-tested this phase."
                            ),
                        })
                        if _a in _blk:
                            _findings_out.append({
                                "kind": "BLOCKING_OVER_DEFERRED", "primitive": _prim,
                                "anchor": _a,
                                "message": (
                                    f"{_a} is Blocking but asserts the OUTCOME of a "
                                    f"verifiable=false (deferred) substrate "
                                    f"capability ({_prim}) — also drop it from the "
                                    f"Non-Negotiable section."
                                ),
                            })
                    write_deferral_consistency_report(
                        reviews_dir, spec_run.spec_name, spec_run.run_id,
                        _findings_out,
                    )
                except OSError as exc:  # don't let a write failure mask the gate
                    log.warning("deferral-gate report write failed: %s", exc)
                _emit_remediation_if_enabled(
                    events_file,
                    spec=spec_run.spec_name, run_id=spec_run.run_id,
                    failure_class="spec_quality",
                    rule_or_check="substrate_deferral_gate",
                    remediation_type="edit_spec",
                    remediation_target_path=(
                        f".dark-factory/specs/{spec_run.spec_name}.md"
                    ),
                    auto_applicable=False,
                    rationale=(
                        f"Anchors assert a deferred capability's outcome: "
                        f"{sorted(_flagged)}"
                    ),
                )
                raise DarkFactoryError(
                    f"substrate deferral gate: anchor(s) {sorted(_flagged)} "
                    f"assert the OUTCOME of a verifiable=false (deferred) "
                    f"substrate capability — unsatisfiable at this build phase. "
                    f"Re-scope them (@untestable:forward-reference; the "
                    f"Non-Negotiable subset also dropped from §8a) via "
                    f"/df-rescope-deferred, or make the governing primitive "
                    f"verifiable=true. (Provisional gate — see "
                    f"darkfactory/spec/deferral_check.py.)",
                    run_id=spec_run.run_id,
                )

    report_path = reviews_dir / f"{spec_run.spec_name}-validation.md"
    if not report_path.exists():
        # validate_spec_result passed without the .md file present (test
        # harness with mocked agent, or .status-only run). Nothing to parse;
        # skip the contracts check.
        return
    report_text = report_path.read_text(encoding="utf-8")
    contracts = parse_unresolved_contracts(report_text)

    # F (2026-06-16): the LLM `unresolved_contracts` check is non-deterministic
    # and emits false positives that contradict the spec bytes (run 2f013c56
    # hallucinated TC-23/TC-32 divergences already resolved on disk; as a hard
    # gate it aborted 3 consecutive runs and blocked the campaign). Default
    # config demotes it to ADVISORY: parse + record + proceed, never abort or
    # re-prompt. The deterministic error-message-consistency check above and the
    # Free-Variables / consistency gates below STILL run as hard gates. Restore
    # the legacy LLM hard gate via unresolved_contracts_hard_gate=true.
    if config.unresolved_contracts_hard_gate:
        _enforce_unresolved_contracts_gate(
            contracts=contracts, report_text=report_text,
            report_path=report_path, spec_run=spec_run, config=config,
            barriers=barriers, project_dir=project_dir,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        )
    elif contracts:
        _record_unresolved_contracts_advisory(contracts, spec_run, reviews_dir)

    # Audit the AUTHOR's Free Variables (the spec), NOT the validator's
    # free-form report. A wire contract mis-tabled as a "free variable" is a
    # SPEC defect the author must fix — deterministic, keyed on the static spec.
    # Auditing the validator report made this gate non-deterministic: the LLM
    # validator sometimes invents a Free Variables section with wire-contract
    # rows the author never wrote, producing a hallucinated abort (python
    # notes-crud, 2026-06-11 — same spec passed on a prior run). The validator's
    # real detection channel is the structured unresolved_contracts: block above.
    spec_fv_text = (
        spec_md.read_text(encoding="utf-8", errors="replace")
        if spec_md.exists() else ""
    )
    fv_offenders = audit_free_variables(spec_fv_text)
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

    # Cross-section consistency gate (Phase 3.5b). Mirrors the existing
    # unresolved_contracts pattern: read the validator's structured side
    # artifact, raise a byte-addressable failure message on FAIL.
    # Back-compat: agent versions that pre-date this field omit
    # ``consistency=`` entirely → treat as PASS so existing pipelines
    # don't break on framework upgrade.
    from darkfactory.spec.consistency_status import (
        extract_contradictions,
        format_consistency_failure,
        parse_consistency_ledger,
        read_consistency_status,
    )

    status_file = reviews_dir / f"{spec_run.spec_name}-validation.status"
    consistency = read_consistency_status(status_file)
    ledger_path = (
        reviews_dir / f"{spec_run.spec_name}-consistency-ledger.yaml"
    )
    ledger = parse_consistency_ledger(ledger_path)
    contradictions = extract_contradictions(ledger)

    if consistency == "FAIL":
        if ledger is None:
            # Validator said FAIL but never wrote the ledger file. The
            # message must distinguish this from "ledger present, no
            # typed contradictions" so the operator knows which side
            # of the validator-output failure they're on.
            raise DarkFactoryError(
                f"spec validation failed: consistency=FAIL but the "
                f"consistency-ledger.yaml at {ledger_path.name} was "
                f"never written. Validator emitted FAIL without "
                f"evidence — re-run validation."
            )
        if not contradictions:
            raise DarkFactoryError(
                f"spec validation failed: consistency=FAIL but the "
                f"consistency-ledger.yaml at {ledger_path.name} contains no "
                f"typed (T1-T5) contradictions. Validator emitted FAIL "
                f"without evidence — re-run validation."
            )
        raise DarkFactoryError(format_consistency_failure(ledger, contradictions))

    if consistency == "PASS":
        # The validator's status disagrees with its own evidence: PASS
        # status but the ledger lists typed contradictions. Trust the
        # ledger; raise.
        if contradictions:
            raise DarkFactoryError(
                f"spec validation failed: consistency=PASS but the "
                f"consistency-ledger.yaml at {ledger_path.name} contains "
                f"{len(contradictions)} typed contradiction(s). Validator "
                f"status disagrees with its own evidence — re-run validation."
            )
        # Theater protection (Neo R5): PASS with no ledger file means
        # the extraction pass never ran. An empty fr_ledgers: [] inside
        # a present file is legitimate (FR-less specs like bug-fix /
        # refactor); only the absent file is a validator-side bug.
        if not ledger_path.exists():
            raise DarkFactoryError(
                f"spec validation failed: consistency=PASS but "
                f"{ledger_path.name} was never written. Validator skipped "
                f"the extraction pass — re-run validation."
            )


# Closed vocabulary for the architect cache-gate's reason tag. Emitted
# on ``ARCHITECT_CACHE_INVALIDATED`` and pinned by unit tests. Any new
# axis must appear here AND in the ``EventType.ARCHITECT_CACHE_INVALIDATED``
# docstring — keeping the two in sync is the contract with downstream
# audit tooling.
_CACHE_MISS_SPEC_HASH = "spec_hash_drift"
_CACHE_MISS_ADAPTER_SET = "adapter_set_drift"
_CACHE_MISS_SUBSTRATE = "substrate_manifest_missing"
_CACHE_MISS_UNREADABLE = "manifest_unreadable"


def _classify_architect_cache(
    *,
    manifest_path: Path,
    substrate_manifest_path: Path,
    spec_run: SpecRun,
    spec_text: str,
    active_adapters: "list[EcosystemAdapter]",
) -> tuple[str | None, list[str]]:
    """Return ``(miss_reason, prior_adapter_stamp)``.

    ``miss_reason`` is ``None`` on cache hit, else one of the
    ``_CACHE_MISS_*`` constants above. ``prior_adapter_stamp`` is the
    sorted list of adapter names recorded in the cached manifest
    (empty list when missing / unreadable), returned unconditionally
    so callers can include it in telemetry without re-reading the
    file.

    Three-axis gate:
      1. ``spec_hash`` in the manifest matches ``spec_run.spec_hash``.
      2. Sorted ``active_adapters`` stamp matches sorted current names.
         A missing stamp is treated as drift — pre-Move-3b manifests
         therefore force one regenerate at upgrade time; the
         alternative (treating missing as "anything goes") would leave
         the 2026-04-16 bug wide open for the upgrade window.
      3. If any current adapter's ``spec_triggers(spec_text)`` is
         True, the substrate TOML must exist. Operator-initiated
         cleanup of the substrate dir between runs would otherwise
         silently proceed without wire-contract coverage.
    """
    import yaml as _yaml

    try:
        existing = _yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        return _CACHE_MISS_UNREADABLE, []
    if not isinstance(existing, dict):
        return _CACHE_MISS_UNREADABLE, []

    prior_raw = existing.get("active_adapters")
    # Normalize to sorted list of strings. YAML can represent this
    # block as null, missing, or a list — distinguish "unreadable"
    # (not a list when present) from "missing" (absent). Absent is
    # drift against any non-trivial expected set; explicit empty list
    # matches an empty ``active_adapters`` call.
    if prior_raw is None:
        prior_stamp: list[str] = []
        stamp_present = "active_adapters" in existing
    elif isinstance(prior_raw, list) and all(isinstance(x, str) for x in prior_raw):
        prior_stamp = sorted(prior_raw)
        stamp_present = True
    else:
        return _CACHE_MISS_UNREADABLE, []

    existing_hash = existing.get("spec_hash")

    # Integrity: a stamped manifest must carry a ``stamp_integrity``
    # field that matches ``_stamp_integrity(stored_spec_hash,
    # sorted_names)``. Missing or mismatched integrity means either a
    # pre-integrity manifest (forces one regenerate at upgrade time)
    # or a hand-edited stamp (regenerate rather than trust it). We
    # hash against the manifest's *stored* spec_hash so tampering with
    # only the ``active_adapters`` list is caught even when the spec
    # itself hasn't changed. If the spec_hash has also drifted, the
    # integrity check still fires correctly — integrity mismatch is
    # stricter than spec-hash drift, but both signal regenerate, and
    # reporting ``manifest_unreadable`` here is the more informative
    # classification (the stamp cannot be trusted, regardless of axis).
    if stamp_present:
        stored_integrity = existing.get("stamp_integrity")
        expected_integrity = _stamp_integrity(
            str(existing_hash) if existing_hash is not None else "",
            prior_stamp,
        )
        if not isinstance(stored_integrity, str) or stored_integrity != expected_integrity:
            return _CACHE_MISS_UNREADABLE, prior_stamp

    if existing_hash != spec_run.spec_hash:
        return _CACHE_MISS_SPEC_HASH, prior_stamp

    current_stamp = sorted(a.name for a in active_adapters)
    if not stamp_present or prior_stamp != current_stamp:
        return _CACHE_MISS_ADAPTER_SET, prior_stamp

    # Substrate-required check. ``spec_triggers`` is pure — the
    # adapter looks only at the spec text. We guard with a broad
    # ``except Exception`` so a broken adapter doesn't tank the
    # orchestrator at cache-check time; on exception we conservatively
    # treat the adapter as "triggered" so missing substrate still
    # invalidates the cache. (Programmer errors surface later when
    # the adapter's other hooks run, which is the standard pattern.)
    any_substrate_required = False
    for adapter in active_adapters:
        try:
            if adapter.spec_triggers(spec_text):
                any_substrate_required = True
                break
        except Exception:
            any_substrate_required = True
            break
    if any_substrate_required and not substrate_manifest_path.is_file():
        return _CACHE_MISS_SUBSTRATE, prior_stamp

    return None, prior_stamp


def _emit_architect_manifest_hash(
    *,
    manifest_path: Path,
    spec_run: SpecRun,
    events_file: Path | None,
    cache_hit: bool,
) -> None:
    """Phase 6 I1: emit ARCHITECT_MANIFEST_HASH for a validated manifest.

    Fails safely — telemetry is best-effort and must not abort the
    pipeline. An unreadable/unparseable manifest yields ``("", "")``
    from ``compute_manifest_hash``; we still emit the event with empty
    hash so the run is visibly unhashable in post-run analysis.
    """
    if events_file is None:
        return
    try:
        from darkfactory.events.types import emit_architect_manifest_hash
        from darkfactory.spec.manifest import compute_manifest_hash

        manifest_hash, canonical_json = compute_manifest_hash(manifest_path)
        emit_architect_manifest_hash(
            events_file,
            spec=spec_run.spec_name,
            run_id=spec_run.run_id,
            manifest_hash=manifest_hash,
            canonical_json=canonical_json,
            cache_hit=cache_hit,
        )
    except Exception:
        log.debug(
            "failed to emit ARCHITECT_MANIFEST_HASH event", exc_info=True,
        )


def _emit_scenario_fingerprints(
    *,
    scenarios_dir: Path,
    spec_run: SpecRun,
    phase: str,
    active_adapters: "list[EcosystemAdapter]",
    events_file: Path | None,
) -> None:
    """Phase 6 I2: emit one SCENARIO_FINGERPRINT event per scenario file.

    Called after Test Engineer ``generate`` / ``validate`` / ``coverage_regen``
    phases complete. ``phase`` names the emitting step so V-dimension
    analyses can distinguish structural drift introduced by a retry from
    drift introduced by the initial generation.

    Best-effort: a scan failure for one file does not abort the rest of
    the batch, and a missing events_file is a silent no-op so local
    dry-runs don't need to thread telemetry wiring.
    """
    if events_file is None:
        return
    try:
        from darkfactory.events.types import emit_scenario_fingerprint
        from darkfactory.scenarios.fingerprint import (
            compute_scenario_fingerprint,
            iter_scenario_files,
        )
    except Exception:
        log.debug(
            "failed to import scenario fingerprint helpers", exc_info=True,
        )
        return
    for path in iter_scenario_files(scenarios_dir):
        try:
            fp = compute_scenario_fingerprint(path, active_adapters)
            emit_scenario_fingerprint(
                events_file,
                spec=spec_run.spec_name,
                run_id=spec_run.run_id,
                scenario_path=fp["scenario_path"],
                phase=phase,
                import_set=fp["import_set"],
                symbol_set=fp["symbol_set"],
                assertion_count=fp["assertion_count"],
                test_names=fp["test_names"],
            )
        except Exception:
            log.debug(
                "failed to emit SCENARIO_FINGERPRINT for %s", path,
                exc_info=True,
            )


def _emit_cache_invalidated(
    *,
    events_file: Path | None,
    spec_run: SpecRun,
    reason: str,
    prior_adapters: list[str],
    current_adapters: list[str],
) -> None:
    """Emit ``ARCHITECT_CACHE_INVALIDATED`` if an events file is
    configured, otherwise silently skip. Defensive try/except is
    intentional — telemetry failures must not abort the pipeline,
    which is the same discipline other emit sites use."""
    if events_file is None:
        return
    try:
        from darkfactory.events.types import emit_architect_cache_invalidated
        emit_architect_cache_invalidated(
            events_file,
            spec=spec_run.spec_name,
            run_id=spec_run.run_id,
            reason=reason,
            prior_adapters=prior_adapters,
            current_adapters=current_adapters,
        )
    except Exception:
        log.debug(
            "failed to emit ARCHITECT_CACHE_INVALIDATED event",
            exc_info=True,
        )


def _stamp_integrity(spec_hash: str, sorted_names: list[str]) -> str:
    """Stable hash tying ``spec_hash`` to the sorted adapter-name list.

    NOT crypto — just a mistake-detector. An operator who hand-edits
    ``active_adapters`` in the manifest without updating this field
    fails the cache integrity check on the next run, and the cached
    manifest is regenerated rather than silently reused against a
    tampered stamp. A sophisticated attacker who knows the algorithm
    can recompute the hash; that threat is out of scope for this
    gate (a compromised host filesystem has larger problems than an
    unsigned stamp on the architect manifest).

    Pipe-delimiter + fixed field order keeps the hash deterministic
    across Python versions and YAML emit quirks. Sorted names are
    the caller's responsibility — this function does not re-sort,
    so the cache-check and stamp-write sites can't disagree by
    accident.
    """
    import hashlib
    payload = spec_hash + "|" + ",".join(sorted_names)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stamp_active_adapters(
    manifest_path: Path,
    active_adapters: "list[EcosystemAdapter]",
    spec_hash: str,
) -> None:
    """Write ``active_adapters: [sorted names]`` + ``stamp_integrity``
    into the manifest YAML via round-trip through ``yaml.safe_dump``.

    Round-trip over append: the architect's manifest is machine-
    generated YAML with no comments or block-scalar formatting worth
    preserving. Appending risks a duplicate ``active_adapters`` key
    if the function runs twice, and PyYAML's last-wins behavior on
    duplicates is non-conformant with YAML 1.2 (strict loaders
    reject it). Round-trip guarantees a single unambiguous key.

    Must be called AFTER ``_apply_manifest_to_environment`` succeeds
    — the stamp is the orchestrator's certificate that this manifest
    passed every post-architect validation. Stamping before validation
    would let an unvalidated manifest pose as cache-hit material on
    the next run.
    """
    import yaml as _yaml

    names = sorted(a.name for a in active_adapters)
    data = _yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DarkFactoryError(
            "manifest is not a YAML mapping; refusing to stamp "
            "active_adapters"
        )
    data["active_adapters"] = names
    data["stamp_integrity"] = _stamp_integrity(spec_hash, names)
    manifest_path.write_text(
        _yaml.safe_dump(data, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


def _step_architecture_lock(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    active_adapters: "list[EcosystemAdapter]",
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
    # Per-spec substrate manifest emitted by adapters that ship structured
    # infrastructure-layer requirements (e.g. Postgres). The architect step
    # owns its lifecycle: any branch that unlinks the YAML manifest must
    # also unlink the substrate TOML, otherwise a spec edit that removes
    # the ``## Substrate Primitives`` section leaves a stale manifest on
    # disk that the scenario step would gate on (Test Engineer would then
    # generate substrate scenarios for entries the spec no longer demands).
    # ``_unlink_architecture_artifacts`` below is the single enforcement
    # point — do NOT call ``manifest_path.unlink()`` directly anywhere in
    # this function; route through the helper so the substrate TOML stays
    # in lockstep.
    substrate_manifest_path = (
        arch_dir / "substrate-manifests" / f"{spec_run.spec_name}.toml"
    )

    def _unlink_architecture_artifacts() -> None:
        manifest_path.unlink(missing_ok=True)
        substrate_manifest_path.unlink(missing_ok=True)

    # --fresh or --regen-scenarios: delete existing manifest to force regeneration.
    if (fresh or regen) and manifest_path.exists():
        _unlink_architecture_artifacts()
        log.info("architecture manifest deleted (--%s)", "fresh" if fresh else "regen-scenarios")

    # Resume gate (Move 3b hardening): skip ARCHITECT INVOCATION only
    # when THREE axes all match the cached manifest. The original gate
    # keyed solely on ``spec_hash``, which caused the 2026-04-16 class
    # of silent failures — a spec that triggered ``postgres`` at
    # runtime (via ``adapter.spec_triggers``) reused a cached manifest
    # built against a typescript-only adapter set, so postgres's
    # addendum / consistency rules / substrate manifest were never
    # produced. Each miss axis has a distinct ``reason`` so post-run
    # forensics can attribute burned architect cost:
    #
    #   spec_hash_drift           — spec text changed
    #   adapter_set_drift         — stamped vs current adapter names differ
    #   substrate_manifest_missing — a triggered adapter's substrate
    #                                TOML is absent on disk
    #   manifest_unreadable       — YAML parse failure (treat as stale)
    #
    # On a cache HIT we still re-apply the manifest to the environment
    # contract — otherwise env.toml/delta-log drift (e.g. after a reset
    # procedure that wiped deltas but kept the manifest) would
    # silently leave the legacy bootstrap as the effective contract,
    # discarding the architect's scoped commands. ``amend`` is
    # idempotent via ``parent_hash`` and skips the write when the ops
    # already match the folded view.
    if manifest_path.exists():
        spec_text_for_gate = (
            spec_file.read_text(encoding="utf-8") if spec_file.exists() else ""
        )
        miss_reason, prior_stamp = _classify_architect_cache(
            manifest_path=manifest_path,
            substrate_manifest_path=substrate_manifest_path,
            spec_run=spec_run,
            spec_text=spec_text_for_gate,
            active_adapters=active_adapters,
        )
        current_stamp = sorted(a.name for a in active_adapters)
        if miss_reason is None:
            log.info(
                "architecture manifest is current (spec_hash + adapter set "
                "+ substrate manifest all match); re-applying cached "
                "manifest to environment contract"
            )
            try:
                _apply_manifest_to_environment(
                    manifest_path=manifest_path,
                    spec_file=spec_file,
                    spec_run=spec_run,
                    config=config,
                    project_dir=project_dir,
                    events_file=events_file,
                    active_adapters=active_adapters,
                )
                # Cached manifests may pre-date the normalizer or have been
                # generated by a prior run whose normalization pass did not
                # apply (earlier module revision). Normalize before emitting
                # the manifest-hash event so the hash reflects final content
                # and the Import Contract gate sees the rewritten specifiers.
                from darkfactory.adapters.typescript.import_path_normalize import (
                    normalize_manifest_import_paths,
                )
                normalize_manifest_import_paths(manifest_path, project_dir)
                _emit_architect_manifest_hash(
                    manifest_path=manifest_path,
                    spec_run=spec_run,
                    events_file=events_file,
                    cache_hit=True,
                )
                return
            except DarkFactoryError as exc:
                log.warning(
                    "cached manifest no longer validates against current "
                    "tree (%s); regenerating", exc,
                )
                _unlink_architecture_artifacts()
        else:
            log.info(
                "architecture manifest invalidated (reason=%s); regenerating",
                miss_reason,
            )
            _emit_cache_invalidated(
                events_file=events_file,
                spec_run=spec_run,
                reason=miss_reason,
                prior_adapters=prior_stamp,
                current_adapters=current_stamp,
            )
            _unlink_architecture_artifacts()

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

    # A5: inject per-adapter architect addenda. Each active adapter's
    # ``architect_addendum()`` returns markdown that goes directly into
    # the prompt. Addenda concatenate in adapter-declaration order
    # (the order ``[detected].active_adapters`` was written) so authors
    # can reason about ordering effects. Empty addenda are appended
    # as-is — governance cap enforcement lives in the contract harness,
    # not here.
    #
    # Exception scope is deliberately narrow (``OSError`` only, which
    # covers ``FileNotFoundError`` / permission errors from adapters
    # that load their addendum from a packaged file). Programmer errors
    # — ``AttributeError``, ``TypeError``, etc. — MUST propagate: those
    # mean the adapter is broken, and silently dropping its prompt
    # contribution would let the architect run with a degraded spec and
    # fail two agents later with a misleading error. An I/O failure is
    # the only failure mode where "continue without this adapter's
    # contribution" is a defensible recovery.
    arch_addenda = _compose_adapter_addenda(
        active_adapters,
        lambda a: a.architect_addendum(),
        log_label="architect_addendum()",
    )
    if arch_addenda:
        prompt += "\n\n" + arch_addenda

    # A1 prompt surface (fix for the 2026-04-15 plumbing bug): tell the
    # architect about the consistency rules that will be enforced against
    # its output. Loading the rules from the same sources
    # (adapter.consistency_rules() + project-hints) and composing with
    # the same ``compose_rules`` call the post-architect check uses
    # guarantees the architect is told about exactly what it will be
    # judged against — no silent trip-wires.
    #
    # Exception discipline follows the adapter-addendum block above:
    # programmer errors (AttributeError / TypeError) propagate so a
    # broken adapter or project-hints schema fails loudly before the
    # LLM invocation. ConfigError from ``compile_project_hint_rules``
    # (malformed regex in a project-hints rule) also propagates — the
    # post-architect check would raise on the same input, so failing
    # here is strictly better UX (fail before spending architect time).
    from darkfactory.architecture.consistency import (
        DEFAULT_RULES,
        compile_project_hint_rules,
        compose_rules,
        render_consistency_rules_for_prompt,
    )
    from darkfactory.architecture.project_hints import load_project_hints

    _project_hints = load_project_hints(project_dir)
    _adapter_rules: list = []
    for adapter in active_adapters:
        _adapter_rules.extend(adapter.consistency_rules())
    _project_hint_rules = compile_project_hint_rules(
        _project_hints.policy.consistency_rules,
    )
    # Both rule-composition sites (prompt-render here, A1 enforcement
    # below) must pass the *same three sources* — drift between
    # "architect is told" and "architect is judged against" is the
    # 2026-04-15 plumbing bug that this function helped fix. Keep these
    # two compose_rules calls parameter-equivalent.
    _composed_rules = compose_rules(
        defaults=DEFAULT_RULES,
        adapter_rules=_adapter_rules,
        project_hint_rules=_project_hint_rules,
    )
    _rules_block = render_consistency_rules_for_prompt(_composed_rules)
    if _rules_block:
        prompt += "\n\n" + _rules_block.rstrip()

    # Precision advisory: if the spec validator rated this spec MEDIUM
    # divergence, append a short advisory that points the architect at
    # the validator's Precision Analysis section and tells it to pin one
    # reading per flagged ambiguity. HIGH is already blocked/warned in
    # detection._parse_status_file; LOW needs no hint. Ephemeral —
    # regenerated per run, not persisted as project policy.
    from darkfactory.architecture.precision_hint import compose_precision_hint

    _precision_hint = compose_precision_hint(project_dir, spec_run.spec_name)
    if _precision_hint:
        prompt += "\n\n" + _precision_hint.rstrip()

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
            f"{spec_run.spec_name}.json — read this for shape claims, "
            f"unresolved acceptance-criteria findings, and service advisories."
        )
        # Surface service advisories directly so the architect cannot miss them.
        try:
            import json as _json
            _advisory = _json.loads(precheck_path.read_text(encoding="utf-8"))
            _svc_advisories = _advisory.get("service_advisories", [])
            if _svc_advisories:
                _svc_names = ", ".join(
                    sa["service_keyword"] for sa in _svc_advisories
                )
                prompt += (
                    f"\n\nSERVICE ADVISORY — ACTION REQUIRED: The precheck "
                    f"detected service(s) referenced in the spec but absent "
                    f"from the environment contract: {_svc_names}. You MUST "
                    f"either (a) declare a testing sidecar in your "
                    f"runtime.services block with test_only: true, or "
                    f"(b) explicitly state in your generation report why a "
                    f"sidecar is not needed (e.g., the spec says the service "
                    f"will be mocked in tests). A production deployment "
                    f"target (e.g., 'PostgreSQL on Railway') is NOT a reason "
                    f"to skip — tests run locally, not on the deployment "
                    f"platform."
                )
        except Exception:
            log.debug("failed to read precheck advisory for service injection", exc_info=True)

    prompt += (
        "\n\nENVIRONMENT HINTS: /project/.dark-factory/architecture/hints.json — "
        "best-guess project tooling (package manager, language, lockfiles, "
        "workspace layout) detected from project files. Use these to avoid "
        "proposing commands that contradict the project's actual tooling "
        "(e.g., do not emit npm commands against a pnpm workspace)."
    )

    # G1: Architect manifest retry loop — validation errors are retryable.
    # If _apply_manifest_to_environment raises DarkFactoryError (parse,
    # validation, M1 scoping, or A1 consistency), re-prompt the Architect
    # with the error list and retry up to _MAX_MANIFEST_RETRIES times.
    # Non-retryable failures (AmendmentRejected, EnvironmentConflict,
    # agent exit != 0, no manifest written) propagate immediately.
    _MAX_MANIFEST_RETRIES = 2
    last_validation_error: DarkFactoryError | None = None

    for attempt in range(1, _MAX_MANIFEST_RETRIES + 2):  # +2: first attempt + retries
        if attempt > 1:
            # Re-prompt: the FULL architect prompt + the validation errors. Two
            # bugs fixed here (run18 abort): (1) the corrective used to REPLACE the
            # full prompt, so the retry architect lost the spec/hints/contracts and
            # could only "fix the existing file"; (2) it told the architect to
            # "Read the existing manifest" — but the validation-failure path
            # (`except` below) UNLINKS the invalid manifest, so the file is GONE on
            # retry. With no context and no file the architect no-ops → "no manifest
            # written" abort. Now: give the architect its full context AND tell it
            # the previous manifest was discarded and to WRITE a corrected one fresh.
            assert last_validation_error is not None
            retry_prompt = (
                prompt
                + f"\n\n## PREVIOUS MANIFEST FAILED VALIDATION — REWRITE FROM SCRATCH\n"
                f"Your previous manifest failed validation and HAS BEEN DELETED "
                f"(do not try to read it — it no longer exists). Write a NEW, "
                f"complete, corrected manifest at "
                f"/project/.dark-factory/architecture/{spec_run.spec_name}-manifest.yaml "
                f"that fixes every error below:\n\n{last_validation_error}\n"
            )
            if events_file:
                from darkfactory.events.types import emit_manifest_validation_retry
                emit_manifest_validation_retry(
                    events_file,
                    spec=spec_run.spec_name,
                    run_id=spec_run.run_id,
                    attempt=attempt,
                    max_attempts=_MAX_MANIFEST_RETRIES + 1,
                    errors=str(last_validation_error).splitlines(),
                )
            log.warning(
                "G1: architect manifest retry %d/%d — %s",
                attempt - 1, _MAX_MANIFEST_RETRIES,
                str(last_validation_error)[:200],
            )
        else:
            retry_prompt = prompt

        result = invoke_agent(
            AgentRole.ARCHITECT, retry_prompt,
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

        # Validate/amend first, THEN stamp. The stamp is the orchestrator's
        # certificate that this manifest passed every post-architect
        # validation — stamping before ``_apply_manifest_to_environment``
        # would let an unvalidated manifest (e.g. one that fails the A1
        # consistency-rule pass, or proposes an illegal environment delta)
        # pose as cache-hit material on the next run. If apply raises, the
        # exception propagates and no stamp is written; next run's cache
        # gate reads "stamp missing" → adapter_set_drift → regenerate,
        # which is the correct recovery.
        try:
            _apply_manifest_to_environment(
                manifest_path=manifest_path,
                spec_file=spec_file,
                spec_run=spec_run,
                config=config,
                project_dir=project_dir,
                events_file=events_file,
                active_adapters=active_adapters,
            )
        except DarkFactoryError as exc:
            if attempt <= _MAX_MANIFEST_RETRIES:
                last_validation_error = exc
                # Delete the invalid manifest so the architect rewrites it
                manifest_path.unlink(missing_ok=True)
                continue
            # Exhausted retries — propagate
            raise
        break  # Validation passed

    # Move 3b: stamp the orchestrator-computed adapter identity + a
    # ``stamp_integrity`` hash binding it to the spec_hash. The
    # architect does not own either field — active_adapters is the A5
    # registry union with G1 ``spec_triggers`` hits, and letting the
    # architect hallucinate names would silently bypass the cache gate
    # it's meant to guard.
    _stamp_active_adapters(manifest_path, active_adapters, spec_run.spec_hash)

    # Rewrite .js-specifier-on-.ts-source entries to .ts when the .ts
    # file is what actually exists on disk. The architect keeps
    # emitting the ESM/NodeNext ``.js`` convention for imports, but
    # some projects' vitest configurations can't resolve .js → .ts
    # and fail the Import Contract gate on every iteration. Run
    # before the manifest-hash event so the hash reflects the final
    # (normalized) content.
    from darkfactory.adapters.typescript.import_path_normalize import (
        normalize_manifest_import_paths,
    )
    rewrites = normalize_manifest_import_paths(manifest_path, project_dir)
    if rewrites:
        log.info(
            "import_path_normalize: rewrote %d entry_point specifier(s) in %s",
            len(rewrites), manifest_path.name,
        )

    _emit_architect_manifest_hash(
        manifest_path=manifest_path,
        spec_run=spec_run,
        events_file=events_file,
        cache_hit=False,
    )

    log.info("architecture manifest generated and validated: %s", manifest_path)


def _capture_preflight_baseline(
    project_dir: Path,
) -> tuple[str, frozenset[str]]:
    """Return ``(ref, untracked_paths)`` for the pre-run working tree.

    - ``ref`` is a ``git stash create`` commit sha that captures tracked
      (staged + unstaged) changes, or ``""`` when the tracked tree was
      clean or the git invocation failed. The preflight diffs against
      this ref for tracked-file changes; ``""`` falls back to ``HEAD``.
    - ``untracked_paths`` is the frozenset of paths reported by
      ``git ls-files --others --exclude-standard -z`` at capture time.
      The preflight uses this set to subtract pre-existing untracked
      files from the "implementer added" set computed at check time.

    Untracked capture is a separate call because ``git stash create -u``
    does not reliably encode untracked files into a tree on every git
    version (2.50.1 can omit them entirely), so relying on tree-walk
    against the stash commit would miss pre-existing files and blame
    the implementer for them.

    Empty ref + empty set on total git failure — the preflight then
    degrades to HEAD with no baseline-untracked filter (the same
    pre-fix behavior), which is strictly a safety fallback, not the
    expected path.
    """
    from darkfactory.utils.subprocess_runner import run

    stash_result = run(
        ["git", "-C", str(project_dir), "stash", "create"],
        timeout=30,
    )
    if stash_result.exit_code != 0:
        log.warning(
            "preflight baseline: git stash create exit %d; falling "
            "back to HEAD. stderr=%s",
            stash_result.exit_code,
            stash_result.stderr.strip()[:500],
        )
        ref = ""
    else:
        ref = stash_result.stdout.strip()

    untracked_result = run(
        [
            "git",
            "-C",
            str(project_dir),
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
        ],
        timeout=30,
    )
    if untracked_result.exit_code != 0:
        log.warning(
            "preflight baseline: git ls-files --others exit %d; "
            "baseline-untracked filter will be empty. stderr=%s",
            untracked_result.exit_code,
            untracked_result.stderr.strip()[:500],
        )
        untracked = frozenset()
    else:
        untracked = frozenset(
            p for p in untracked_result.stdout.split("\x00") if p
        )

    if ref:
        log.info(
            "preflight baseline captured: ref=%s untracked_count=%d",
            ref[:12],
            len(untracked),
        )
    else:
        log.debug(
            "preflight baseline: tree had no tracked changes; will "
            "diff against HEAD. untracked_count=%d",
            len(untracked),
        )
    return ref, untracked


def _emit_remediation_if_enabled(
    events_file: Path | None,
    *,
    spec: str,
    run_id: str,
    failure_class: str,
    rule_or_check: str,
    remediation_type: str,
    remediation_target_path: str,
    auto_applicable: bool,
    rationale: str,
    suggested_diff: str = "",
    next_command: str = "",
) -> None:
    """Best-effort C1 emission wrapper.

    Swallows OSError/ValueError from the emit path (bad events_file,
    unwritable directory, unexpected payload) and logs a warning — the
    caller is about to raise a DarkFactoryError and must not have its
    stack trace masked by telemetry plumbing.

    Skips entirely when ``events_file`` is ``None`` (CLI-only contexts
    without an initialized event stream).
    """
    if events_file is None:
        return
    try:
        from darkfactory.events.remediation import emit_remediation_target
        emit_remediation_target(
            events_file,
            spec=spec,
            run_id=run_id,
            failure_class=failure_class,  # type: ignore[arg-type]
            rule_or_check=rule_or_check,
            remediation_type=remediation_type,  # type: ignore[arg-type]
            remediation_target_path=remediation_target_path,
            auto_applicable=auto_applicable,
            rationale=rationale,
            suggested_diff=suggested_diff,
            next_command=next_command,
        )
    except (OSError, ValueError) as exc:
        log.warning(
            "failed to emit remediation_target (spec=%s rule=%s): %s",
            spec, rule_or_check, exc,
        )


def _emit_remediation_outcome_if_pending(
    events_file: Path,
    *,
    spec_run: SpecRun,
    terminal_status: str,
) -> None:
    """If a prior run emitted a REMEDIATION_TARGET for this spec, emit the
    paired REMEDIATION_OUTCOME now that this run has reached a terminal
    state.

    ``terminal_status`` is one of "shipped", "escalated", "failed",
    "stable_failure". Any non-"shipped" value is classified via the
    failed/escalated branch (stable_failure did not ship).

    Uses ``most_recent_unpaired_target`` (not the legacy
    ``most_recent_target_for_spec``) so that a chained-failure sequence
    — run r1 fails and emits T1, human edits, run r2 also fails and
    emits T2 — pairs T1 with the terminal outcome at the end of r2
    rather than silently orphaning T1 because T2 happens to be newer
    and belongs to the current run. ``exclude_run_id=spec_run.run_id``
    skips the current run's own targets; the helper also refuses to
    re-pair any target that already has an outcome event, enforcing the
    "exactly one outcome per target" invariant across resumes.

    ``applied_at`` is derived from ``spec_run.start_time`` — the
    rerun-start timestamp, which approximates when the human's fix was
    actually in place (the fix must have been applied *before* the
    rerun started). The default "now" captured at emission time would
    instead record the terminal-state timestamp, which can be many
    minutes after the fix was applied and misleads the calibration
    window.

    Best-effort: any read/write failure is logged at warning level; never
    mask the terminal event that triggered this call.
    """
    try:
        from darkfactory.events.remediation import (
            classify_outcome,
            emit_remediation_outcome,
            most_recent_unpaired_target,
        )
    except ImportError:  # pragma: no cover — module always present
        return

    try:
        target = most_recent_unpaired_target(
            events_file,
            spec_run.spec_name,
            exclude_run_id=spec_run.run_id,
        )
    except (OSError, ValueError) as exc:
        log.warning("remediation_outcome: failed to read events.jsonl: %s", exc)
        return
    if target is None:
        return

    data = target.get("data") or {}
    rule = data.get("rule_or_check")
    rtype = data.get("remediation_type")
    if not rule or not rtype:
        log.warning(
            "remediation_outcome: prior target missing rule_or_check/"
            "remediation_type; skipping",
        )
        return

    applied_at = _format_applied_at(spec_run.start_time)
    # Pre-implementer gates (spec validator, architect, proposal
    # rehearsal, scenario generation) all run before completed_step is
    # set to 1+. If completed_step is still 0 at terminal, the pipeline
    # aborted before any gate ran (e.g., bootstrap exception, budget
    # exhausted in setup) — we have no evidence the prior rule is
    # actually fixed. See classify_outcome's gates_executed docstring.
    gates_executed = spec_run.completed_step > 0
    try:
        outcome = classify_outcome(
            events_file,
            prior_target=target,
            this_run_id=spec_run.run_id,
            terminal_status=terminal_status,  # type: ignore[arg-type]
            gates_executed=gates_executed,
        )
        emit_remediation_outcome(
            events_file,
            spec=spec_run.spec_name,
            run_id=spec_run.run_id,
            remediation_run_id=target.get("run_id", ""),
            rule_or_check=rule,
            remediation_type=rtype,
            outcome=outcome,
            applied_at=applied_at,
        )
    except (OSError, ValueError) as exc:
        log.warning("remediation_outcome: emit failed: %s", exc)


def _format_applied_at(unix_epoch: float) -> str:
    """Convert a SpecRun.start_time Unix epoch to a UTC ISO-8601 string.

    Used as the ``applied_at`` value for remediation outcomes — the
    rerun's start timestamp is the best available proxy for "when the
    fix was applied" (the human's edit must have landed before the
    rerun kicked off). Falls back to empty string on non-finite values
    so ``emit_remediation_outcome`` can default to terminal-now rather
    than emit a garbage timestamp.
    """
    import math
    if not math.isfinite(unix_epoch) or unix_epoch <= 0:
        return ""
    from datetime import datetime, timezone
    return datetime.fromtimestamp(unix_epoch, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ",
    )


def _consistency_suggested_diff(cerr: "ConsistencyError") -> str:
    """Render a TOML block a user could paste into project-hints.toml
    ``[[policy.consistency_rules]]`` to codify an A1 rule as a
    project-level policy.

    Kept minimal on purpose — the goal is to show the shape, not to
    reproduce the regex-level rule body (those live in code until A6
    promotes them to project-hints as adapter baseline rules).
    """
    # ``description`` may contain triple-quotes; we use basic TOML string
    # with escaped backslashes/quotes to stay safely parseable regardless.
    desc = cerr.description.replace("\\", "\\\\").replace('"', '\\"')
    lines = [
        "[[policy.consistency_rules]]",
        f'name = "{cerr.rule_name}"',
        f'description = "{desc}"',
        "# See darkfactory.architecture.consistency.DEFAULT_RULES for the full",
        "# rule body (patterns + env_paths). A6 will promote rules to this",
        "# file as adapter baselines.",
    ]
    return "\n".join(lines) + "\n"


def _apply_manifest_to_environment(
    *,
    manifest_path: Path,
    spec_file: Path,
    spec_run: SpecRun,
    config: PipelineConfig,
    project_dir: Path,
    active_adapters: "list[EcosystemAdapter]",
    events_file: Path | None = None,
) -> None:
    """Parse + validate a manifest, apply its runtime block as an environment
    delta, and bridge unmodeled runtime fields into the in-memory config.

    Safe to call on both freshly-written and cached manifests. The amend
    function is idempotent via parent_hash and skips writing a new delta
    when the proposed ops are already reflected in the folded view —
    so replays on an already-consistent env contract are no-ops.

    ``events_file`` is threaded so A1 consistency violations can emit
    C1 ``REMEDIATION_TARGET`` events before the ``DarkFactoryError`` is
    raised. When omitted, emission is silently skipped — callers that
    want remediation telemetry must pass the orchestrator's
    ``events_file``.

    ``active_adapters`` is the list of adapters resolved by the A5
    registry for the run. Each adapter contributes its ecosystem-
    baseline consistency rules. Required — pass ``[]`` explicitly for
    a zero-adapter check rather than letting a forgotten parameter
    silently drop adapter-level rules.
    """
    from darkfactory.spec.manifest import (
        check_manifest_schema_adherence,
        parse_manifest,
        validate_command_scoping,
        validate_environment,
        validate_manifest,
        validate_manifest_scope,
        validate_observability_contract,
    )
    from darkfactory.architecture.amend import (
        AmendmentRejected,
        amend_environment_from_manifest,
    )
    from darkfactory.architecture.consistency import (
        DEFAULT_RULES,
        ConsistencyRule,
        check_spec_env_consistency,
        compile_project_hint_rules,
        compose_rules,
    )
    from darkfactory.architecture.deltas import EnvironmentConflict
    from darkfactory.architecture.project_hints import load_project_hints

    manifest_text = manifest_path.read_text(encoding="utf-8")

    # D2 (schema-adherence backstop): catch an architect that drifted off the
    # canonical schema (e.g. emitted ``files:`` instead of ``file_map:``, or an
    # invented ``scenarios:`` block) BEFORE parsing — such a manifest does NOT
    # produce a parse error (the parser silently ignores unrecognized keys and
    # ends up with an empty ``file_map``), so it would otherwise sail through and
    # starve the Test Engineer of structural guidance. Raising here routes into
    # the G1 retry loop, which re-prompts the architect with these corrections.
    # Runs PRE-STAMP only (this function is the architect-output validation path);
    # never moved into parse_manifest, which is re-invoked on stamped manifests.
    schema_errors = check_manifest_schema_adherence(manifest_text)
    if schema_errors:
        raise DarkFactoryError(
            "Architecture manifest schema-adherence errors (use the EXACT "
            "top-level keys from architect.md Step 5):\n"
            + "\n".join(f"  - {e}" for e in schema_errors)
        )

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

    # M1: monorepo command scoping — hard fail if commands.* target a
    # directory inconsistent with source_dirs. Fires pre-delta (before
    # the amendment is written) so the Architect retry loop (G1) can
    # re-prompt with the error list.
    # Sprint 5: anchor command-scope on the project's REAL declared workspaces
    # (drift-proof, machine-authoritative from pnpm-workspace.yaml/package.json)
    # rather than the possibly-stale config source_dirs. Fixes the false-positive
    # where the architect correctly targets the spec's workspace but source_dirs
    # has drifted to a different one. Falls back to source_dirs when no
    # workspaces are detected (non-monorepo repos).
    workspaces: list[str] = []
    for _adapter in active_adapters:
        workspaces.extend(_adapter.detect_workspaces(project_dir))
    workspaces = sorted(set(workspaces))
    scoping_violations = validate_command_scoping(
        parse_result.manifest, config.source_dirs, adapters=active_adapters,
        workspaces=workspaces,
    )
    if scoping_violations:
        if events_file:
            from darkfactory.events.types import emit_monorepo_scoping_violation
            emit_monorepo_scoping_violation(
                events_file,
                spec=spec_run.spec_name,
                run_id=spec_run.run_id,
                violations=scoping_violations,
            )
        raise DarkFactoryError(
            "Architecture manifest command scoping errors:\n"
            + "\n".join(
                f"  - {v['command_key']}: {v['reason']}"
                for v in scoping_violations
            )
        )

    scope_warnings = validate_manifest_scope(parse_result.manifest, spec_text)
    for w in scope_warnings:
        log.warning("manifest scope: %s", w)

    env_warnings = validate_environment(parse_result.manifest, config.language)
    for w in env_warnings:
        log.warning("manifest environment: %s", w)

    obs_warnings = validate_observability_contract(parse_result.manifest, spec_text)
    for w in obs_warnings:
        log.warning("manifest observability: %s", w)

    # A1: spec↔env consistency. Hard fail — a contradictory env contract
    # is what put the implementer on a path that cannot satisfy the spec
    # in the 2026-04-14 database-foundation escalation. See
    # docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md.
    #
    # B5: rule set is composed from three sources.
    #   1. DEFAULT_RULES (framework-universal; post-B5 intentionally empty)
    #   2. Adapter baselines (ecosystem-universal) — from active_adapters
    #   3. Project-hints (project-specific policy) — from
    #      .dark-factory/project-hints.toml [[policy.consistency_rules]]
    # ``compose_rules`` dedupes by name, later-source-wins, so a project-
    # hints rule overrides an adapter or default with the same name.
    # Shadowed rules are logged at INFO (event=consistency_rule_shadow)
    # so operators can trace "which source's rule actually fired"
    # post-mortem.
    project_hints = load_project_hints(project_dir)
    adapter_rules: list[ConsistencyRule] = []
    for adapter in active_adapters:
        adapter_rules.extend(adapter.consistency_rules())
    project_hint_rules = compile_project_hint_rules(
        project_hints.policy.consistency_rules,
    )
    composed_rules = compose_rules(
        defaults=DEFAULT_RULES,
        adapter_rules=adapter_rules,
        project_hint_rules=project_hint_rules,
    )
    consistency_errors = check_spec_env_consistency(
        spec_text=spec_text,
        manifest=parse_result.manifest,
        rules=composed_rules,
    )
    if consistency_errors:
        # C1: emit one REMEDIATION_TARGET per rule violation before raising.
        # Each points at .dark-factory/project-hints.toml so the user can
        # codify the rule as a project-level policy. The raised error
        # still aborts the run — emission is additive, not a side channel.
        for cerr in consistency_errors:
            # auto_applicable=False: the TOML block in suggested_diff is
            # shape-only (name + description). The ConsistencyError
            # payload does not carry the rule's compiled regex, so a C2
            # ``remediate`` pass cannot reconstruct a working
            # [[policy.consistency_rules]] entry from the error alone —
            # the user must copy patterns from the firing rule's source
            # by hand. TODO: if a future C2 needs auto-remediation here,
            # extend ConsistencyError to carry the source ConsistencyRule
            # (or its pattern strings); auto_applicable can then flip.
            _emit_remediation_if_enabled(
                events_file,
                spec=spec_run.spec_name,
                run_id=spec_run.run_id,
                failure_class="consistency_violation",
                rule_or_check=cerr.rule_name,
                remediation_type="edit_env_contract",
                remediation_target_path=(
                    f".dark-factory/architecture/{spec_run.spec_name}-manifest.yaml"
                ),
                auto_applicable=False,
                suggested_diff=_consistency_suggested_diff(cerr),
                rationale=cerr.format(),
            )
        raise DarkFactoryError(
            "Architecture manifest contradicts spec constraints:\n"
            + "\n".join(f"  - {e.format()}" for e in consistency_errors),
            run_id=spec_run.run_id,
        )

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

    # Phase 4: services and test_base_url now flow through the environment
    # contract delta system via refresh_config_from_environment at each
    # step boundary. The bootstrap-only gate and bridge_unmodeled_runtime_fields
    # were both deleted in Phase 4.


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


def _step_proposal_rehearsal(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
) -> None:
    """Step 1.75 — C2 proposal-test typecheck rehearsal.

    Spawns one Proposal Rehearsal agent invocation, parses its stdout
    for proposals, runs tsc --noEmit on them host-side, applies the
    banned-pattern scan, and decides ship/abort. Raises DarkFactoryError
    on abort. Skips silently (INFO log) when the project isn't TS or has
    no anchors.
    """
    step_begin(spec_run.spec_name, "proposal_rehearsal", config, project_dir)

    from darkfactory.adapters.typescript.proposal_rehearsal import (
        build_proposal_prompt,
        run_proposal_rehearsal,
    )
    from darkfactory.spec.anchors import AnchorManifest, manifest_path as _anchor_manifest_path

    # Pre-invocation skip: if the anchor manifest is absent or empty,
    # don't burn a container start on a no-op agent call. The module's
    # run_proposal_rehearsal re-checks this after the fact for safety,
    # but doing it here saves ~$1-3 of LLM cost per run when the spec
    # has no anchors to rehearse (bootstrap specs, sentinel-only specs).
    anchors = AnchorManifest.from_json_path(
        _anchor_manifest_path(project_dir, spec_run.spec_name)
    )
    if anchors is None:
        log.info("C2: anchor manifest missing; skipping proposal rehearsal")
        return
    if not (anchors.ac_ids or anchors.fr_ids or anchors.ec_ids):
        log.info("C2: anchor manifest is empty; skipping proposal rehearsal")
        return

    # Pre-invocation skip for non-TS projects. We check tsconfig
    # existence here to avoid paying for an agent call whose output will
    # be discarded by the module's no-op path. This is belt-and-
    # suspenders — run_proposal_rehearsal also checks.
    if not (project_dir / "tsconfig.json").exists():
        log.info("C2: no tsconfig.json at project root; skipping proposal rehearsal")
        return

    prompt = build_proposal_prompt(spec_run.spec_name, anchors)
    result = invoke_agent(
        AgentRole.PROPOSAL_REHEARSAL, prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path,
        # Cap turns aggressively — this is a single-shot probe, not an
        # iterative task. The default agent_max_turns is tuned for full
        # scenario generation.
        max_turns_override=20,
        phase="rehearsal",
    )
    if result.exit_code != 0:
        # Non-fatal: a failed agent call just means we can't rehearse.
        # Don't block scenario gen on an infra glitch — log and continue.
        log.warning(
            "C2: proposal rehearsal agent failed (exit %d); skipping",
            result.exit_code,
        )
        return

    # Extract the inner text field from Claude's JSON wrapper before
    # parsing sentinels — the raw container stdout includes the JSON
    # wrapper, and the BEGIN_PROPOSALS/END_PROPOSALS pair lives inside
    # the ``result`` field.
    from darkfactory.pipeline.agents import extract_agent_text
    agent_text = extract_agent_text(result.output)

    outcome = run_proposal_rehearsal(
        spec_name=spec_run.spec_name,
        project_dir=project_dir,
        agent_output=agent_text,
    )

    if outcome.skipped:
        log.info("C2: %s", outcome.reason)
        return

    if not outcome.passed:
        # Write a detail file so the human can inspect findings without
        # scrolling the run log. Best-effort — don't let a write failure
        # mask the abort signal.
        try:
            reports_dir = project_dir / ".dark-factory" / "reports"
            reports_dir.mkdir(parents=True, exist_ok=True)
            detail_path = reports_dir / f"{spec_run.spec_name}-proposal-rehearsal.md"
            detail_path.write_text(_format_rehearsal_detail(outcome))
            log.info("C2: rehearsal detail written to %s", detail_path)
        except OSError as exc:
            log.warning("C2: failed to write rehearsal detail: %s", exc)

        # C1: A6 rehearsal failure is a pre-implementer gate failure.
        # Auto_applicable=false — the fix is either a spec rescope or a
        # dependency-install problem; no mechanical diff exists.
        _emit_remediation_if_enabled(
            events_file,
            spec=spec_run.spec_name,
            run_id=spec_run.run_id,
            failure_class="proposal_typecheck_failure",
            rule_or_check="proposal_rehearsal",
            remediation_type="edit_spec",
            remediation_target_path=(
                f".dark-factory/specs/{spec_run.spec_name}.md"
            ),
            auto_applicable=False,
            rationale=outcome.reason,
        )
        raise DarkFactoryError(
            f"Proposal-test rehearsal failed — {outcome.reason}\n"
            f"See .dark-factory/reports/{spec_run.spec_name}-proposal-rehearsal.md "
            f"for per-anchor findings. Re-scope the affected spec sections "
            f"before re-running the pipeline.",
            run_id=spec_run.run_id,
        )

    log.info(
        "C2: proposal rehearsal passed (%d proposals, %d typecheck failures)",
        outcome.total_proposals, outcome.typecheck_failure_count,
    )


def _format_rehearsal_detail(outcome) -> str:
    """Render the failure detail for .dark-factory/reports/<spec>-proposal-rehearsal.md."""
    lines = [
        "# Proposal Rehearsal (C2) — Findings",
        "",
        f"**Total proposals:** {outcome.total_proposals}",
        f"**Typecheck failures:** {outcome.typecheck_failure_count}",
        f"**Banned-pattern hits:** {len(outcome.banned_hits)}",
        "",
        f"**Reason:** {outcome.reason}",
        "",
    ]
    if outcome.banned_hits:
        lines.append("## Banned Patterns")
        lines.append("")
        lines.append("| Anchor | Category | Description |")
        lines.append("|---|---|---|")
        for hit in outcome.banned_hits:
            lines.append(f"| {hit.anchor} | {hit.category} | {hit.description} |")
        lines.append("")
    if outcome.typecheck_findings:
        lines.append("## Typecheck Findings")
        lines.append("")
        lines.append("| File | Line | Code | Message |")
        lines.append("|---|---|---|---|")
        for f in outcome.typecheck_findings:
            stub_line = f.stub_line if f.stub_line is not None else "-"
            lines.append(f"| {f.filepath} | {stub_line} | {f.tsc_code} | {f.message} |")
        lines.append("")
    return "\n".join(lines)


def _run_scenario_authoring_lint(
    active_adapters: "list[EcosystemAdapter]",
    scenarios_dir: Path,
    spec_run: SpecRun,
    events_file: Path | None,
) -> None:
    """Dispatch each active adapter's host-side pre-impl scenario lint.

    Adapters may deterministically auto-correct authoring bugs that
    typecheck clean but fail at runtime (Task #21 RULE A: relative-path
    literals that escape the project root). Host-side and manifest-free —
    nothing crosses to a barrier-restricted agent (see
    ``EcosystemAdapter.lint_scenarios``). Every action is logged and
    evented so a correction is auditable, never silent. Best-effort: an
    adapter that raises is logged and skipped (a lint crash must not abort
    scenario generation).
    """
    for adapter in active_adapters:
        try:
            findings = adapter.lint_scenarios(scenarios_dir)
        except Exception:
            log.warning(
                "scenario-authoring lint failed for adapter %s; skipping",
                getattr(adapter, "name", "?"), exc_info=True,
            )
            continue
        for f in findings:
            log.info(
                "scenario-authoring lint [%s] %s:%d %s",
                f.code, f.path, f.line, f.message,
            )
            if events_file:
                emit_scenario_authoring_lint(
                    events_file,
                    spec=spec_run.spec_name,
                    run_id=spec_run.run_id,
                    adapter=adapter.name,
                    severity=f.severity,
                    code=f.code,
                    path=f.path,
                    line=f.line,
                    message=f.message,
                )


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
    active_adapters: "list[EcosystemAdapter]",
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

    # F Move 2b: the QA Holdout step_begin is HOISTED here, onto the main
    # thread, BEFORE any fan-out. step_begin → refresh_config_from_environment
    # mutates ``config`` in place; running the TE and QA step_begin calls
    # concurrently would be a shared-mutable-config race. Both run serially up
    # front (TE's step_begin is the function's first statement above), so the
    # parallel branches below only ever read config. Order (TE then QA) matches
    # the pre-parallel flow, which freshened TE config then QA config.
    step_begin(spec_run.spec_name, "qa_holdout", config, project_dir)

    # If TE output already existed when the step entered, there is no TE
    # generation work to overlap with: the validation retry loop and coverage
    # floor have already run in a prior invocation and must not re-run (they
    # mutate scenarios_dir and could rewrite files the Implementer has already
    # consumed). Run the QA Holdout serially and return — identical to the
    # pre-parallel resume path, which reached the QA invocation then returned.
    if te_files_existed:
        _invoke_qa_holdout(
            spec_run, config, barriers, project_dir,
            holdouts_dir=holdouts_dir,
            events_file=events_file,
            audit_path=audit_path,
            dry_run=dry_run,
        )
        return

    # F Move 2b: the Test Engineer (visible AC suite → scenarios/) and the QA
    # Holdout (blind adversarial → holdouts/) are INDEPENDENT — each reads only
    # the spec, their containers mask each other's output dir, and they write
    # disjoint trees — so they can run concurrently, saving ~10 min on a
    # ~60-min run. Gated behind config.scenario_parallel_generation (default
    # OFF; dry_run forces serial). When OFF the path is byte-for-byte the old
    # serial flow: QA Holdout first, then TE generation.
    #
    # Concurrency contract (see docs/plans/te-qa-parallelization.md):
    #   - ThreadPoolExecutor(max_workers=1) runs the QA Holdout on a worker
    #     thread; TE generation runs on the main thread inside the ``with``.
    #   - The ``with``-exit calls shutdown(wait=True), which ALWAYS joins the QA
    #     worker — so a TE exception can never orphan the QA container; the TE
    #     exception then propagates and ``qa_future.result()`` is never reached,
    #     so a concurrent QA error cannot mask the TE failure (TE is the gating
    #     role with the retry/coverage-floor/MISNAMED handling).
    #   - ``qa_future.result()`` runs only if TE succeeded; it re-raises any
    #     UNEXPECTED QA exception (the normal QA agent-failure path warns and
    #     returns — it never raises), preserving the pre-parallel behavior where
    #     such an error aborted the run. Note: it now surfaces after the TE
    #     budget rather than before it (documented divergence).
    if config.scenario_parallel_generation and not dry_run:
        with ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="qa-holdout"
        ) as ex:
            qa_future = ex.submit(
                _invoke_qa_holdout,
                spec_run, config, barriers, project_dir,
                holdouts_dir=holdouts_dir,
                events_file=events_file,
                audit_path=audit_path,
                dry_run=dry_run,
            )
            _invoke_test_engineer_generation(
                spec_run, config, barriers, project_dir,
                scenarios_dir=scenarios_dir,
                events_file=events_file,
                audit_path=audit_path,
                dry_run=dry_run,
                active_adapters=active_adapters,
            )
        qa_future.result()
    else:
        _invoke_qa_holdout(
            spec_run, config, barriers, project_dir,
            holdouts_dir=holdouts_dir,
            events_file=events_file,
            audit_path=audit_path,
            dry_run=dry_run,
        )
        _invoke_test_engineer_generation(
            spec_run, config, barriers, project_dir,
            scenarios_dir=scenarios_dir,
            events_file=events_file,
            audit_path=audit_path,
            dry_run=dry_run,
            active_adapters=active_adapters,
        )


def _batch_anchor_ids(manifest: "AnchorManifest | None", batch_size: int) -> list[list[str]]:
    """P1 TE-decomposition: split the manifest's NAMED anchors (AC/EC/FR) into
    batches of at most ``batch_size``, in a deterministic order, for per-batch
    Test-Engineer generation.

    Returns ``[]`` when batching is OFF (``batch_size <= 0``), the manifest is
    absent, or there are no anchors — the caller then runs the monolithic
    single-invocation path (byte-for-byte the pre-P1 flow). Sub-anchor
    Scenario-Intents are NOT enumerated (the manifest stores only per-anchor
    counts), so decomposition is AC/EC/FR granularity only — it does not address
    the spec-5 SI-3 class (that routes to the Validator-Bridge feedback work).
    """
    if batch_size <= 0 or manifest is None:
        return []
    ids = list(manifest.ac_ids) + list(manifest.ec_ids) + list(manifest.fr_ids)
    if not ids:
        return []
    return [ids[i:i + batch_size] for i in range(0, len(ids), batch_size)]


def _te_batch_workers(n_batches: int, config: PipelineConfig) -> int:
    """Concurrency cap for the parallel TE batch fan-out (S2).

    ``config.scenario_batch_max_workers > 0`` OVERRIDES (clamped to ``n_batches``).
    Default (0) = the CONSERVATIVE ``min(n_batches, 2)``: the TE+QA path has
    validated exactly 2 concurrent 4 GB/4 CPU agent containers in production, and
    with ``scenario_parallel_generation`` ON the nested QA Holdout reserves one
    more slot (2 batch-workers + 1 QA = 3 containers is already at the edge of a
    typical 16 GB host). On a provisioned host raise via the override; the
    resource-derived headroom is ``min(cpu//4, mem_gb//4)`` minus the QA reserve.
    """
    if config.scenario_batch_max_workers > 0:
        return max(1, min(n_batches, config.scenario_batch_max_workers))
    return max(1, min(n_batches, 2))


def _run_one_te_batch(
    bi: int,
    anchors: list[str],
    n: int,
    *,
    base_prompt: str,
    scenarios_dir: Path,
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    contracts_ro_args: list[str],
    events_file: Path | None,
    audit_path: Path | None,
) -> bool:
    """Run ONE cold Test-Engineer generate batch (anchor subset ``bi`` of ``n``).

    Returns this batch's timeout-salvage flag (True iff it timed out WITH
    recognized scenario files on disk). Per-batch failure ISOLATION
    (invariant 2): an agent crash, or a timeout with no files, is logged and
    returns False (uncovered anchors fall to the coverage floor's targeted
    regen) — it does NOT abort the phase. It does NOT, however, swallow a
    ``TransientFailure`` from ``invoke_agent`` (infra exhaustion — repeated API
    overload past ``transient_retry_max``): that PROPAGATES by design to drive
    plan-level retry, so the parallel caller MUST call ``.result()`` on every
    future (not a short-circuiting ``any()``) to surface it exactly as the
    sequential path does. Reads ``config`` only (no mutation), so it is safe to
    run concurrently across batches. Byte-for-byte the former in-line loop body
    (extracted so the sequential and parallel paths share one callable).
    """
    batch_prompt = (
        base_prompt
        + f"\n\n## BATCH {bi + 1} of {n} — SCOPED GENERATION\n"
        + "For THIS invocation, generate visible scenarios ONLY for these "
        + f"anchors: {', '.join(anchors)}. The remaining anchors are covered "
        + "by sibling invocations — do NOT generate scenarios for them. "
        + f"Prefix EVERY file you write with 'b{bi}-' so batches never collide."
    )
    result = invoke_agent(
        AgentRole.TEST_ENGINEER, batch_prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path, dry_run=False,
        phase="generate",
        extra_docker_args=contracts_ro_args or None,
        session_container_id="",          # COLD: bound per-turn thinking
        attempt_offset=bi,
    )
    if result.exit_code == 0:
        return False
    # PER-BATCH FAILURE ISOLATION — never raise from a single batch.
    # A TIMEOUT (exit 124) that left recognized files is salvageable exactly like
    # the monolithic path: return True so the function-body backstop (gated
    # sa_max_retries > 0) tsc-validates the persisted files. Gate on
    # sa_max_retries > 0 so we only mark salvage when that loop will run (Codex).
    if (
        result.timed_out
        and config.sa_max_retries > 0
        and has_scenario_files(scenarios_dir)
    ):
        log.warning(
            "P1 batch %d/%d (anchors %s) timed out (exit 124) but left "
            "scenario files — marking timeout-salvage; SA-validate + "
            "coverage floor run after assembly",
            bi + 1, n, ", ".join(anchors),
        )
        if events_file is not None:
            from darkfactory.events.types import emit_scenario_timeout_salvage
            emit_scenario_timeout_salvage(
                events_file, spec=spec_run.spec_name,
                run_id=spec_run.run_id, attempt=bi,
                file_count=count_scenario_files(scenarios_dir),
            )
        return True
    # Non-timeout crash, or timeout with no files: do NOT abort the whole phase —
    # sibling batches still run and any anchors this batch failed to cover fall to
    # the coverage floor's targeted regen. Logged for visibility.
    log.warning(
        "P1 batch %d/%d (anchors %s) failed (exit %d, timed_out=%s) "
        "— continuing; uncovered anchors fall to the coverage floor",
        bi + 1, n, ", ".join(anchors),
        result.exit_code, result.timed_out,
    )
    return False


def _run_batched_te_generation(
    *,
    base_prompt: str,
    batches: list[list[str]],
    scenarios_dir: Path,
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    contracts_ro_args: list[str],
    events_file: Path | None,
    audit_path: Path | None,
) -> bool:
    """P1 TE-decomposition: run one COLD Test-Engineer invocation per batch of
    anchors so each turn reasons over only ``len(batch)`` anchors.

    On heavy specs the monolithic single generate turn exceeds the model's max
    output tokens (one ~16.5K-token thinking block walls the 30-min budget,
    ``exit 124 reason=max_output_tokens``). Splitting the reasoning SCOPE — K
    anchors per invocation — bounds the per-turn thinking. This is the ONLY
    behavioural change: it does not enumerate sub-anchor Scenario-Intents
    (those are out of scope) and so does not claim to fix any specific spec bug;
    it bounds the timeout class.

    Returns whether ANY batch was timeout-salvaged (exit 124 WITH recognized
    files left on disk). The caller threads this into ``_te_timeout_salvaged``
    so the existing function-body salvage backstop (gated on
    ``sa_max_retries > 0``) tsc-validates those persisted files exactly as on
    the monolithic path (invariant 3).

    Design choices that satisfy the reviewer invariants:

    * Every batch passes the SAME ``barriers`` dict and ``AgentRole``
      ``TEST_ENGINEER`` — there is no per-batch barrier divergence
      (invariant 1). No new mount or path is introduced, so there is no new
      barrier-leak surface (invariant 6).
    * Each invocation is COLD (``session_container_id=""``) — a fresh container
      per batch, so per-turn extended-thinking cannot accumulate across batches
      (the whole point of the decomposition). Option B (sequential, cold
      per-batch) per the design.
    * PER-BATCH FAILURE ISOLATION: a single batch does not raise on a non-zero
      agent EXIT (those are isolated and continue). A timeout WITH files flips the
      salvage flag and continues (the SA-validate/coverage-floor backstop runs
      after assembly). Any other non-zero exit is logged and the loop CONTINUES —
      uncovered anchors fall through to the coverage floor, which does targeted
      regen. This keeps the assembly atomic (invariant 2): generation completes as
      a unit before the function's terminal guards. NOTE: a ``TransientFailure``
      from ``invoke_agent`` (infra exhaustion) is NOT isolated — it propagates by
      design (see ``_run_one_te_batch``); the parallel path calls ``.result()`` on
      every future so that raise surfaces exactly as on the sequential path.

    Collision handling (invariant 5): each batch is told to prefix every file
    it writes with ``b{bi}-`` so sibling batches never clobber each other's
    output. A deterministic post-write collision guard (reject/relabel a file
    that ignored the prefix) is a fast-follow — for now a clobbered anchor
    simply ends up uncovered and the coverage floor regenerates it.
    """
    n = len(batches)
    # CONFIG INVARIANT (S2): the per-batch path only READS ``config`` — no
    # ``config.X =``, no ``step_begin`` / ``refresh_config_from_environment``
    # (those run on the main thread BEFORE fan-out, mirroring the TE+QA hoist at
    # the scenario_parallel_generation region). So N batches reading the shared
    # mutable PipelineConfig concurrently is safe — keep it that way.
    # ATOMIC OUTPUT (Codex): an infra failure (e.g. ``TransientFailure`` from
    # invoke_agent's transient-retry exhaustion) propagating out of a batch leaves
    # PARTIAL scenario files on disk — earlier/sibling batches that completed before
    # the raise surfaced. Plan-level retry would then re-enter
    # ``_step_scenario_generation``, see ``has_scenario_files``, take the
    # ``te_files_existed`` resume short-circuit, and SKIP TE regeneration + the
    # validate/coverage-floor backstops — treating partial output as a complete
    # suite. So on ANY propagating exception we DISCARD the partial output before
    # re-raising, making this function atomic w.r.t. scenario files: it either
    # returns with a coherent suite or leaves nothing for resume to mistake as
    # complete. The timeout-SALVAGE path RETURNS True (never raises), so its
    # intentionally-kept partial files are untouched — only a real raise reaches the
    # ``except``. (KeyboardInterrupt/SystemExit are BaseException, NOT caught — the
    # interrupted-run resume semantics are unchanged.)
    try:
        if config.scenario_batch_parallel and n > 1:
            workers = _te_batch_workers(n, config)
            log.info(
                "S2: fanning out %d TE generate batch(es) across %d worker(s) "
                "(scenario_batch_parallel)", n, workers,
            )
            with ThreadPoolExecutor(
                max_workers=workers, thread_name_prefix="te-batch"
            ) as ex:
                futures = [
                    ex.submit(
                        _run_one_te_batch, bi, anchors, n,
                        base_prompt=base_prompt, scenarios_dir=scenarios_dir,
                        spec_run=spec_run, config=config, barriers=barriers,
                        project_dir=project_dir, contracts_ro_args=contracts_ro_args,
                        events_file=events_file, audit_path=audit_path,
                    )
                    for bi, anchors in enumerate(batches)
                ]
            # The ``with``-exit already joined every worker (shutdown(wait=True)).
            # MATERIALIZE every result (list comp, NOT a short-circuiting ``any()``
            # generator): each future's ``.result()`` MUST be called so a sibling
            # batch's ``TransientFailure`` PROPAGATES to plan-level retry exactly as
            # on the sequential path. A lazy ``any(f.result() ...)`` would
            # short-circuit on a lower-index salvage=True and silently swallow a
            # higher-index raise (Linus). Then OR-aggregate the salvage flags.
            results = [f.result() for f in futures]
            return any(results)

        # Sequential fan-out (flag OFF, or a single batch): byte-for-byte the pre-S2
        # cold-per-batch loop.
        salvaged = False
        for bi, anchors in enumerate(batches):
            salvaged = _run_one_te_batch(
                bi, anchors, n,
                base_prompt=base_prompt, scenarios_dir=scenarios_dir,
                spec_run=spec_run, config=config, barriers=barriers,
                project_dir=project_dir, contracts_ro_args=contracts_ro_args,
                events_file=events_file, audit_path=audit_path,
            ) or salvaged
        return salvaged
    except Exception:
        if scenarios_dir.is_dir():
            # Best-effort: a cleanup error must never mask the original failure.
            shutil.rmtree(scenarios_dir, ignore_errors=True)
            log.warning(
                "TE batch generation raised — discarded partial scenario output "
                "in %s so plan-level retry regenerates from a clean state (no "
                "false te_files_existed resume short-circuit on partial files)",
                scenarios_dir,
            )
        raise


def _invoke_test_engineer_generation(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    scenarios_dir: Path,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
    active_adapters: "list[EcosystemAdapter]",
) -> None:
    """Test Engineer scenario-generation body (F Move 2b extraction).

    This is a VERBATIM extraction of the former in-line Test Engineer block of
    ``_step_scenario_generation`` — adapter-contract / db-reset staging, Tier 1
    compilation, prompt assembly, the warm-session empty/misnamed-retry loop,
    the SA-validation retry loop, Tier 1 file writes, and the coverage floor.
    No logic changed; it was lifted into a named helper so the parallel region
    has a single callable for the TE branch.

    The pinned caller ``_step_scenario_generation`` owns the ``step_begin``
    for the ``test_engineer`` step (its first statement), so this helper does
    NOT call ``step_begin`` itself — it is registered in
    ``RETRY_BODY_HELPERS_EXEMPT_FROM_OFFLIST`` in the step_begin enforcement
    test, exactly like ``_invoke_qa_holdout``.

    May raise ``DarkFactoryError`` on TE crash, MISNAMED scenarios, validation
    exhaustion, or coverage-floor failure — identical to the pre-extraction
    flow. The caller runs this on the main thread so those raises propagate
    exactly as before.
    """
    scenarios_dir.mkdir(parents=True, exist_ok=True)

    # Stage adapter-owned wire-contract helpers under
    # scenarios_dir/_contracts/<adapter.name>/ so the test-engineer can
    # import them via a stable relative path (./_contracts/<adapter>)
    # when authoring blind scenarios. Must run AFTER any regen rmtree
    # (in the caller) and BEFORE the test-engineer container starts. Adapters
    # without a contracts_dir() are silently skipped (ABC default).
    #
    # ``spec_text`` is passed so staging gates on each adapter's
    # ``spec_triggers(spec_text)`` taxonomy: a project-active adapter
    # (e.g., postgres because the project uses postgres in general)
    # whose substrate rules don't fire against this spec text has its
    # contracts skipped. Without this, the ``_check_contract_helper_usage``
    # validator rule rejects substrate-clean specs by construction
    # (Phase 5 Stage 1, 2026-04-25 echo-endpoint smoke).
    #
    # The caller only reaches this helper when there is real TE work to do
    # (the ``te_files_existed`` resume short-circuit runs the QA Holdout and
    # returns BEFORE fan-out), so re-staging helpers underneath
    # Implementer-consumed files cannot happen here.
    spec_file_path = (
        project_dir / ".dark-factory" / "specs" / f"{spec_run.spec_name}.md"
    )
    spec_text_for_staging = (
        spec_file_path.read_text(encoding="utf-8")
        if spec_file_path.exists()
        else None
    )
    # Sprint 6 (A3): the architect's substrate manifest — NOT a spec-text regex —
    # is the authority on whether substrate work exists. Computed here and reused
    # by the scenario-architect-addendum gate below. A pure-function spec that
    # merely mentions or DENIES a substrate concept (e.g. "...no tenant-
    # isolation...") trips a substrate-rule regex and would otherwise stage
    # irrelevant DB contract helpers, hard-failing the TE's contract_helper_usage
    # scenario-lint — which the TE is CORRECT not to satisfy (it must not import
    # helpers unrelated to its spec). The path convention is shared across
    # adapters (generic, not Postgres-specific).
    substrate_manifest = (
        project_dir / ".dark-factory" / "architecture"
        / "substrate-manifests" / f"{spec_run.spec_name}.toml"
    )
    # GATE: only stage adapter contract helpers when the architect modeled
    # substrate. No manifest → no helpers staged → empty _contracts/ → the
    # contract_helper_usage check correctly passes (it short-circuits to PASS on
    # no staged helpers). A real substrate spec emits a manifest → helpers stage
    # → the check fires as intended. Correct by construction; preserves the
    # affirmative case without prose-regex negation heuristics.
    if substrate_manifest.is_file():
        stage_adapter_contracts(
            scenarios_dir, active_adapters,
            spec=spec_run.spec_name, run_id=spec_run.run_id,
            events_file=events_file,
            spec_text=spec_text_for_staging,
            # ROADMAP.md Phase 3 B9d: pass ``project_dir`` so each
            # adapter's ``project_contracts_dir(project_dir)`` is consulted
            # and staged under ``_contracts/<adapter>/project/``.
            # Mount-manifest unchanged — the existing ``contracts_ro_args``
            # bind covers the new subdir transparently.
            project_dir=project_dir,
        )
    else:
        log.info(
            "skipping adapter-contract staging for %s: architect emitted no "
            "substrate manifest (no substrate primitives modeled)",
            spec_run.spec_name,
        )

    # Sprint 29: per-test DB clean-state reset is GATE TEST-INFRASTRUCTURE, not a
    # contract helper, so it stages on a DATABASE-ACTIVE signal (a service injects
    # DATABASE_URL) INDEPENDENT of the substrate-manifest gate above. Coupling the
    # reset to that gate was the Sprint-28 bug: a plain Postgres CRUD spec emits no
    # substrate manifest, so the reset never staged and never ran (vitest setup
    # 0ms, id contamination). It lands under _df_setup/<adapter>/ (sibling of
    # _contracts/) so it does NOT trip _check_contract_helper_usage (a staged .ts
    # no test imports). The gate (scripts/lib/scenarios.sh) additionally guards the
    # injection on DATABASE_URL being set at run time, so a stray stage is a no-op.
    if db_reset_active(config):
        stage_db_reset(scenarios_dir, active_adapters)

    # Mount staged _contracts/ read-only inside the TE container so the
    # agent cannot rewrite, delete, or "compile" the helpers.  Docker
    # mount specificity: this :ro bind mount is more specific than the
    # :rw mount on .dark-factory/scenarios/ and therefore wins.
    #
    # Without this, the TE (an LLM with write access to scenarios/) may
    # "helpfully" transpile .ts helpers to .js, introducing bugs like
    # postgres.unsafe() (static, doesn't exist) vs connection.unsafe()
    # (instance method, correct).  See run #4 post-mortem.
    contracts_host = scenarios_dir / "_contracts"
    contracts_ro_args: list[str] = []
    if contracts_host.is_dir() and any(contracts_host.iterdir()):
        container_contracts = (
            f"/project/.dark-factory/scenarios/"
            f"{spec_run.spec_name}/_contracts"
        )
        contracts_ro_args = ["-v", f"{contracts_host}:{container_contracts}:ro"]
        log.info("contracts RO mount: %s → %s", contracts_host, container_contracts)

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
        f"Write output to /project/.dark-factory/scenarios/{spec_run.spec_name}/."
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

    # Per-adapter scenario-architect addenda go after the execution
    # context block so adapter guidance reads as additive constraints on
    # the already-established TOOLCHAIN/HAS_* hints, not in conflict
    # with them.
    #
    # GATE: only inject when the architect emitted a substrate manifest
    # for this spec. Without a manifest there's nothing for the addendum
    # to anchor against, and unconditional injection would leave the
    # Test Engineer reading "generate one substrate scenario per manifest
    # entry" with no manifest to consult — exactly the prose-asks-LLM-to-
    # ignore-itself anti-pattern this whole substrate-verification effort
    # is closing. The path convention (``.dark-factory/architecture/
    # substrate-manifests/<spec-name>.toml``) is shared across adapters,
    # so this gate is generic, not Postgres-specific.
    #
    # ``_compose_adapter_addenda`` swallows OSError per-adapter (so a
    # packaging bug in one adapter doesn't drop scenario gen for the
    # rest of the run) and propagates programmer errors loudly.
    # ``substrate_manifest`` was computed above (shared with the A3 staging gate).
    if substrate_manifest.is_file():
        sa_addenda = _compose_adapter_addenda(
            active_adapters,
            lambda a: a.scenario_architect_addendum(),
            log_label="scenario_architect_addendum()",
        )
        if sa_addenda:
            prompt += "\n\n" + sa_addenda

    # Test Engineer scenario generation, with bounded retry-on-empty. The TE
    # intermittently exits 0 but writes NO scenario files (and no sentinel) — a
    # transient agent no-op (observed on python-fastapi). Rather than abort the
    # whole run, re-invoke with the SAME prompt (the flake is transient, not a
    # comprehension failure — wrong files would be caught by SA-validation
    # below) up to config.scenario_empty_retry_attempts times. A sentinel OR
    # real scenario files is a valid terminal outcome (break, no retry).
    # dry_run keeps single-shot behavior (identical to pre-retry). After
    # exhaustion we raise the same error, so an always-empty spec still surfaces
    # (just after N visible retries).
    _max_empty_retries = config.scenario_empty_retry_attempts if not dry_run else 0

    # S41: set True when we salvage a timed-out TE (exit 124 + files). Used
    # after the SA-validate loop to require a GENUINE tsc pass on the salvaged
    # (possibly truncated) files — a tsc pass-SKIP (toolchain unavailable/hung)
    # must not be accepted as the compile backstop for timed-out output (Codex).
    # Hoisted above both the batched and monolithic generation paths so the
    # function-body salvage backstop (below) consults a single flag regardless
    # of which path produced the scenarios.
    _te_timeout_salvaged = False

    # P1 TE-decomposition: when scenario_batch_size > 0 AND the architect's
    # anchor manifest enumerates anchors, generate scenarios in COLD per-batch
    # invocations (each turn reasons over only K anchors, bounding the per-turn
    # extended-thinking that walls the monolithic single-turn path on heavy
    # specs). Off by default (batch_size == 0) and disabled under dry_run — the
    # K=0 / dry_run path then runs the monolithic warm-session loop below
    # byte-for-byte (invariant 4). The manifest is the architect's authoritative
    # AC/EC/FR list; ``_batch_anchor_ids`` returns ``[]`` when it is absent or
    # empty, so a manifest-less run falls back to the monolithic path even when
    # batching is configured.
    # Optional (Linus #6): short-circuit the manifest load entirely when batching
    # is off so the monolithic path pays no extra IO.
    _use_batched = False
    _batches: list[list[str]] = []
    if config.scenario_batch_size > 0 and not dry_run:
        from darkfactory.spec.anchors import (
            AnchorManifest as _AnchorManifest,
            manifest_path as _manifest_path,
        )
        _manifest_for_batching = _AnchorManifest.from_json_path(
            _manifest_path(project_dir, spec_run.spec_name)
        )
        _batches = _batch_anchor_ids(_manifest_for_batching, config.scenario_batch_size)
        _use_batched = bool(_batches)
        if not _use_batched:
            # Diagnostic: batching was CONFIGURED but produced no batches — almost
            # always a missing/empty anchor manifest at TE-generate time. Logged
            # loudly so a monolithic fall-through (which can hit the very
            # max-output-tokens wall batching exists to avoid) is never silent.
            log.warning(
                "P1 TE-decomposition INERT despite scenario_batch_size=%d: "
                "manifest_loaded=%s, named_anchor_batches=%d — running MONOLITHIC "
                "(manifest path: %s)",
                config.scenario_batch_size,
                _manifest_for_batching is not None,
                len(_batches),
                _manifest_path(project_dir, spec_run.spec_name),
            )

    if _use_batched:
        # WARNING-level (not info) so batched ENGAGEMENT is visible in the run
        # log alongside the INERT diagnostic — otherwise an info marker is
        # filtered and engaged-vs-monolithic is indistinguishable from the log.
        log.warning(
            "P1 TE-decomposition: batched scenario generation for %s "
            "(%d batches of <=%d anchors; NOTE wall time scales ~N×timeout)",
            spec_run.spec_name, len(_batches), config.scenario_batch_size,
        )
        _te_timeout_salvaged = _run_batched_te_generation(
            base_prompt=prompt,
            batches=_batches,
            scenarios_dir=scenarios_dir,
            spec_run=spec_run,
            config=config,
            barriers=barriers,
            project_dir=project_dir,
            contracts_ro_args=contracts_ro_args,
            events_file=events_file,
            audit_path=audit_path,
        )
        # Linus C2: the batched path skips the monolithic empty/misnamed-retry
        # loop, so a batch that wrote files the runner can't collect (e.g. `.js`
        # instead of `*.test.ts`) is invisible to has_scenario_files/coverage and
        # would surface only as a generic floor gap. Preserve the monolithic
        # path's PRECISE MISNAMED_SCENARIOS diagnostic for the total-misname case
        # (files written, none recognized); a partial misname leaves recognized
        # files + lets the coverage floor regenerate the uncovered remainder.
        _batch_misnamed = find_misnamed_scenario_files(
            scenarios_dir, tier1_prefix="00-tier1-"
        )
        if _batch_misnamed and not has_scenario_files(scenarios_dir):
            _dump_misnamed_failure(project_dir, spec_run, _batch_misnamed)
            raise DarkFactoryError(
                "MISNAMED_SCENARIOS: batched test engineer wrote scenario files "
                "that match no recognized test pattern (the runner will not "
                f"collect them): {', '.join(_batch_misnamed[:20])}"
            )

    # F Move 2c: run the empty/misnamed-retry loop in a WARM persistent TE
    # container so a retry resumes the same Claude session via `claude
    # --continue` (mirrors the implementer S1 pre-gate loop, iteration.py) and
    # the TE fixes its output IN-CONTEXT — crucially RENAMES misnamed files
    # (run 2f013c56 wrote `.js` instead of `*.test.ts`, which the runner won't
    # collect and has_scenario_files rejects) instead of a cold
    # clear-and-regenerate that re-derives everything and can inject a NEW
    # defect. Only when retries are possible and not dry_run; a session-start
    # failure degrades to the cold per-call path (te_session_id stays "").
    # contracts_ro_args MUST be baked at start (warm-container mounts are fixed
    # at `docker run` time — passing them only per-exec would lose the
    # read-only _contracts/ helpers on warm turns).
    #
    # SKIPPED ENTIRELY on the batched path (``_use_batched``): the per-batch
    # cold loop in ``_run_batched_te_generation`` already produced the
    # scenarios. Guarding the whole monolithic block behind ``if not
    # _use_batched`` (rather than deep edits inside it) keeps the K=0 / dry_run
    # path byte-for-byte identical to the pre-P1 flow (invariant 4).
    if not _use_batched:
        te_session_id = ""
        if not dry_run and config.scenario_warm_session and _max_empty_retries > 0:
            from darkfactory.pipeline.agents import start_agent_session
            try:
                te_session_id = start_agent_session(
                    AgentRole.TEST_ENGINEER,
                    config=config, barriers=barriers, run_id=spec_run.run_id,
                    project_dir=project_dir, spec_name=spec_run.spec_name,
                    audit_path=audit_path, dry_run=dry_run,
                    extra_docker_args=contracts_ro_args or None,
                )
            except Exception as exc:  # noqa: BLE001 — degrade to cold, never abort on session-start
                log.warning(
                    "F Move 2c: TE warm session start failed (%s); cold-retry fallback",
                    exc,
                )
                te_session_id = ""

        try:
            for _empty_attempt in range(_max_empty_retries + 1):
                corrective = ""
                if _empty_attempt > 0:
                    misnamed = find_misnamed_scenario_files(
                        scenarios_dir, tier1_prefix="00-tier1-"
                    )
                    if misnamed and te_session_id:
                        # PRESERVE-AND-RENAME: do NOT clear — the warm session knows
                        # which files it wrote, so the deterministic corrective
                        # collapses to a rename. This is the signal the cold retry
                        # lacked (the agent ignored the static naming rule).
                        corrective = _format_misnamed_corrective(misnamed)
                        if events_file is not None:
                            from darkfactory.events.types import (
                                emit_scenario_misnamed_retry,
                            )
                            emit_scenario_misnamed_retry(
                                events_file, spec=spec_run.spec_name,
                                run_id=spec_run.run_id, attempt=_empty_attempt,
                                files=misnamed[:20],
                            )
                    else:
                        # Truly empty (wrote nothing) OR cold fallback (no warm
                        # session to rename in): legacy clear-and-regenerate. Clear
                        # the tier-2 output (a flaked attempt could leave a
                        # stray/garbage file), preserving staged tier-1 contracts +
                        # the _contracts/ and _df_setup/ helper trees.
                        _clear_tier2_scenarios(scenarios_dir, tier1_prefix="00-tier1-")
                        if events_file is not None:
                            from darkfactory.events.types import (
                                emit_scenario_empty_retry,
                            )
                            emit_scenario_empty_retry(
                                events_file, spec=spec_run.spec_name,
                                run_id=spec_run.run_id, attempt=_empty_attempt,
                            )

                attempt_prompt = prompt if not corrective else prompt + "\n\n" + corrective
                result = invoke_agent(
                    AgentRole.TEST_ENGINEER, attempt_prompt,
                    config=config, barriers=barriers,
                    run_id=spec_run.run_id, project_dir=project_dir,
                    spec_name=spec_run.spec_name, iteration=0,
                    events_file=events_file, audit_path=audit_path, dry_run=dry_run,
                    phase="generate",
                    extra_docker_args=contracts_ro_args or None,
                    session_container_id=te_session_id,           # "" => cold path
                    use_continue=bool(te_session_id and _empty_attempt > 0),
                    attempt_offset=_empty_attempt,
                )
                if result.exit_code != 0:
                    # S41 salvage: a TIMEOUT (exit 124) that nonetheless left
                    # recognized scenario files is NOT the same as a crash. The TE
                    # was generating real work and was cut off by the wall — often
                    # one hard anchor short (bb spec-4: 54/55, EC-7 uncovered). The
                    # unconditional raise here incinerated that work BEFORE the
                    # purpose-built salvage machinery (SA-validate + coverage floor,
                    # below) could tsc-validate the persisted files and do targeted
                    # regen for the missing anchor. Demote to a fall-through: break
                    # into that machinery, which decides PASS / targeted-regen /
                    # honest-escalate on the ARTIFACT (coverage), not the exit code.
                    # A non-timeout crash, or a timeout with NO files, still aborts
                    # (different cost asymmetry + masking risk; nothing to salvage).
                    # Gate on sa_max_retries > 0 (Codex): salvage is only SAFE when
                    # the SA-validate loop below actually runs — that loop is itself
                    # gated on `config.sa_max_retries > 0`, and it is what tsc-checks
                    # the persisted files. If validation is disabled, salvaging a
                    # timed-out (possibly incomplete/half-written) TE would ship
                    # unvalidated scenarios into the blind gate, so we abort instead.
                    if (
                        result.timed_out
                        and config.sa_max_retries > 0
                        and has_scenario_files(scenarios_dir)
                    ):
                        log.warning(
                            "test engineer timed out (exit 124) but left scenario "
                            "files — salvaging into SA-validate + coverage floor "
                            "instead of aborting (attempt %d)",
                            _empty_attempt,
                        )
                        if events_file is not None:
                            from darkfactory.events.types import (
                                emit_scenario_timeout_salvage,
                            )
                            emit_scenario_timeout_salvage(
                                events_file, spec=spec_run.spec_name,
                                run_id=spec_run.run_id, attempt=_empty_attempt,
                                file_count=count_scenario_files(scenarios_dir),
                            )
                        _te_timeout_salvaged = True
                        break
                    raise DarkFactoryError(f"test engineer scenario generation failed (exit {result.exit_code})")
                # Sentinel OR recognized scenario files => terminal. A mix of good +
                # misnamed still returns False here (has_scenario_files only matches
                # recognized patterns), so the loop continues to rename the rest.
                if has_sentinel_file(scenarios_dir) or has_scenario_files(scenarios_dir):
                    break
                # else: exit 0 but nothing recognized — retry if budget remains.
            else:
                # Budget exhausted. If files exist but are misnamed, raise a PRECISE
                # error + persist a failure artifact (the old generic message lied —
                # files WERE written); else the generic empty error.
                misnamed = find_misnamed_scenario_files(scenarios_dir, tier1_prefix="00-tier1-")
                if misnamed:
                    _dump_misnamed_failure(project_dir, spec_run, misnamed)
                    raise DarkFactoryError(
                        "MISNAMED_SCENARIOS: test engineer wrote scenario files that "
                        "match no recognized test pattern after "
                        f"{_max_empty_retries + 1} attempts (the runner will not "
                        f"collect them): {', '.join(misnamed[:20])}"
                    )
                raise DarkFactoryError("scenario generation succeeded but no scenario files were written")
        finally:
            if te_session_id:
                from darkfactory.pipeline.agents import stop_agent_session
                stop_agent_session(te_session_id, dry_run=dry_run)

    # Fingerprints fire ONCE, on the successful (non-empty) attempt — not on the
    # empty attempts that were retried away.
    _emit_scenario_fingerprints(
        scenarios_dir=scenarios_dir,
        spec_run=spec_run,
        phase="generate",
        active_adapters=active_adapters,
        events_file=events_file,
    )

    # Validate a sentinel if that's what the TE wrote. A sentinel that EXISTS but
    # is rejected is a real failure (not the transient empty case) — raise, no
    # retry, identical to pre-change behavior.
    if has_sentinel_file(scenarios_dir):
        valid, objections = validate_sentinel(get_sentinel_path(scenarios_dir))
        if not valid:
            raise DarkFactoryError(
                "Scenario Architect wrote a sentinel but it was rejected — "
                f"fix the justification or write real scenario files:\n{objections}"
            )
        log.info("scenarios sentinel written and validated")

    # SA validation + retry loop (does NOT consume iteration budget).
    arch_manifest_path = (
        project_dir / ".dark-factory" / "architecture"
        / f"{spec_run.spec_name}-manifest.yaml"
    )
    validation = None  # last validate_scenarios() result (None if loop body never validated)
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
                manifest_path=arch_manifest_path,
                tier1_prefix="00-tier1-",
                adapters=active_adapters,
            )
            # G2: emit import violation event if check 20 ran and failed.
            import_check = next(
                (c for c in validation.checks if c.name == "import_specifiers"),
                None,
            )
            if import_check and not import_check.passed and events_file:
                from darkfactory.events.types import emit_scenario_import_violations
                # Parse counts from detail string.
                import re as _re
                spec_m = _re.search(r"(\d+) specifier", import_check.detail)
                sym_m = _re.search(r"(\d+) symbol", import_check.detail)
                emit_scenario_import_violations(
                    events_file,
                    spec=spec_run.spec_name,
                    run_id=spec_run.run_id,
                    specifier_violations=int(spec_m.group(1)) if spec_m else 0,
                    symbol_violations=int(sym_m.group(1)) if sym_m else 0,
                    details=import_check.failing_files[:10],
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
                # Give actionable guidance when the assertion cap is the issue.
                if "assertion_upper_bound" in validation.report:
                    suffix += (
                        " To reduce assertion count: use expect(obj).toMatchObject({...}) "
                        "instead of multiple toHaveProperty()/toBe() calls on individual "
                        "fields. Do not write separate it() blocks for overlapping anchors "
                        "that hit the same endpoint — consolidate under the more specific "
                        "anchor. If a file STILL exceeds the cap after consolidating, SPLIT "
                        "it into multiple test files by concern (one per error code, FR, or "
                        "anchor group) — the cap is PER FILE, the minimum is 3 files and "
                        "there is NO maximum. Do NOT drop coverage to get under the cap."
                    )

                # ``prompt`` already carries the per-adapter scenario-
                # architect addenda (composed above, conditional on the
                # substrate manifest existing); reusing it here is
                # deliberate so retry attempts inherit the same substrate
                # discipline as attempt #0. A future refactor that
                # rebuilds ``retry_prompt`` from scratch must re-thread
                # the addenda — silently dropping them would re-open the
                # 2026-04-15 substrate-thin failure mode on retries.
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
                    extra_docker_args=contracts_ro_args or None,
                )
                if retry_result.exit_code != 0:
                    log.warning("TE retry %d failed (exit %d)", sa_attempt + 1, retry_result.exit_code)
                else:
                    _emit_scenario_fingerprints(
                        scenarios_dir=scenarios_dir,
                        spec_run=spec_run,
                        phase="validate",
                        active_adapters=active_adapters,
                        events_file=events_file,
                    )
            else:
                log.warning(
                    "TE validation failed after %d attempt(s)",
                    config.sa_max_retries + 1,
                )
                # C1: scenario validator has exhausted retries — a pre-
                # implementer gate failure. Classified as "spec_quality"
                # because the usual fix is rewording ACs/anchors so the TE
                # can generate valid scenarios. Auto_applicable=false —
                # requires human judgment about the spec.
                _emit_remediation_if_enabled(
                    events_file,
                    spec=spec_run.spec_name,
                    run_id=spec_run.run_id,
                    failure_class="spec_quality",
                    rule_or_check="scenario_validator",
                    remediation_type="edit_spec",
                    remediation_target_path=(
                        f".dark-factory/specs/{spec_run.spec_name}.md"
                    ),
                    auto_applicable=False,
                    rationale=validation.report[:2000],
                )
                raise DarkFactoryError(
                    f"TE scenarios failed validation after {config.sa_max_retries + 1} attempts:\n"
                    + validation.report[:2000],
                    run_id=spec_run.run_id,
                )

    # S41 salvage backstop (Codex r2/r3/r4): when we salvaged a timed-out TE,
    # its scenarios MUST have been compile-validated before reaching the blind
    # gate. This block lives at function-body level — OUTSIDE the
    # `if not has_sentinel_file and sa_max_retries>0` validate block above — so
    # the sentinel-coexistence path (which gates OUT that block) cannot bypass
    # it. Two unsafe cases for a timed-out (possibly truncated) result, both
    # abort:
    #   (a) `validation is None` — the SA-validate loop never ran. On the
    #       salvage path (sa_max_retries>0 is gated) this happens IFF a sentinel
    #       coexists with the salvaged real files. A timed-out TE that wrote
    #       BOTH a sentinel and real files is incoherent — don't trust it.
    #   (b) tsc did NOT genuinely compile-validate the bodies — validation ran
    #       but tsc was unavailable / hung / could not resolve node types
    #       (compile_status "skipped"), or it found syntax/code errors
    #       (compile_status "syntax"/"failed"). None of those is a compile
    #       backstop for a possibly-truncated salvaged result.
    # S0(b): the contract is the STRUCTURED ``compile_status`` field, not a
    # ``"skipped"`` substring in the detail (which could not distinguish a
    # truncated file — only "Cannot find module" errors — from a genuine
    # missing-types skip). Ship ONLY on compile_status == "passed" (a genuine
    # tsc pass OR the "no .ts files" pure-python/non-TS case, both "passed").
    # Scoped to the salvage path only — normal generation keeps lenient behavior.
    # SX: the advisory "passed_unresolved_deps" status (TS7006 implicit-any
    # demoted per-file because a same-file import is unresolved) is INTENTIONALLY
    # != "passed" and so is REFUSED here — a statement-boundary-truncated salvaged
    # file can wear the same TS2307+TS7006 shape, and we will not ship a possibly
    # -truncated salvage on a non-genuine compile. Normal (non-salvage) generation
    # treats the advisory status as a pass via CheckResult.passed=True.
    if _te_timeout_salvaged:
        tsc_check = (
            next((c for c in validation.checks
                  if c.name == "typescript_compilation"), None)
            if validation is not None else None
        )
        tsc_not_validated = (
            tsc_check is not None and tsc_check.compile_status != "passed"
        )
        if validation is None or tsc_not_validated:
            why = (
                f"tsc compile_status={tsc_check.compile_status!r}: "
                f"{tsc_check.detail}" if tsc_not_validated
                else "validation did not run (a sentinel coexists with the "
                     "salvaged scenario files)"
            )
            raise DarkFactoryError(
                "TE timed out and was salvaged, but its scenarios were not "
                f"compile-validated ({why}) — refusing to ship unvalidated "
                "timed-out scenarios into the blind gate. Fix the toolchain "
                "(tsc must be available and not hang) / the TE output and "
                "re-run.",
                run_id=spec_run.run_id,
            )

    # Write Tier 1 files now that the SA has finished and the guard checks are done.
    for filename, content in tier1_files:
        (scenarios_dir / filename).write_text(content, encoding="utf-8")
    if tier1_files:
        log.info("wrote %d Tier 1 contract file(s) to %s", len(tier1_files), scenarios_dir)

    # Pre-impl scenario-authoring lint (Task #21, RULE A). Host-side,
    # deterministic, manifest-free: adapters auto-correct authoring bugs
    # that typecheck clean but fail at runtime (a relative-path literal
    # whose ../ run escaped the project root). Runs AFTER Tier 1 files are
    # staged (so framework helpers are present and skipped) and BEFORE the
    # coverage floor. Barrier note: no string crosses to any agent — see
    # ``EcosystemAdapter.lint_scenarios``.
    _run_scenario_authoring_lint(
        active_adapters, scenarios_dir, spec_run, events_file,
    )

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
            contracts_ro_args=contracts_ro_args or None,
            active_adapters=active_adapters,
        )


def _load_scenario_lint_module():
    """Load ``scripts/lib/scenario_lint.py`` as an importable module.

    The script is part of the dark-factory repo, not the target project,
    so it lives at ``<package_root>/../scripts/lib/scenario_lint.py``
    relative to this file. Editable installs work directly; non-editable
    installs do not ship ``scripts/`` (see ``pyproject.toml`` —
    ``packages.find`` includes only ``darkfactory*``). The pre-acceptance
    phase is opt-in (``te_pre_acceptance_check = false`` by default), so
    a missing script in a non-editable deploy only matters when an
    operator explicitly enables the phase.
    """
    import importlib.util as _ilu
    import sys as _sys
    spec_path = (
        Path(__file__).resolve().parent.parent.parent
        / "scripts" / "lib" / "scenario_lint.py"
    )
    if not spec_path.is_file():
        raise DarkFactoryError(
            f"te_pre_acceptance_check is enabled but the scenario_lint "
            f"script was not found at {spec_path}. This phase requires "
            f"the dark-factory repo's scripts/ directory to be present "
            f"on the host (editable install or repo checkout)."
        )
    # Namespaced sys.modules key — the script-as-module side door also
    # used by ``tests/unit/test_scenario_lint.py`` keys on the bare name
    # ``"scenario_lint"``; using a darkfactory-private namespace avoids
    # any collision when both load paths run in the same process.
    _MODULE_KEY = "_darkfactory_scenario_lint"
    cached = _sys.modules.get(_MODULE_KEY)
    if cached is not None:
        return cached
    spec = _ilu.spec_from_file_location(_MODULE_KEY, spec_path)
    if spec is None or spec.loader is None:
        raise DarkFactoryError(
            f"te_pre_acceptance_check: failed to load scenario_lint "
            f"module spec from {spec_path}"
        )
    mod = _ilu.module_from_spec(spec)
    _sys.modules[_MODULE_KEY] = mod
    spec.loader.exec_module(mod)
    return mod


def _step_te_pre_acceptance(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    events_file: Path | None = None,
    audit_path: Path | None = None,
    dry_run: bool = False,
) -> None:
    """ROADMAP.md Phase 2 B1: host-side §4a TE pre-acceptance check.

    Runs on the host (no container) AFTER the Test Engineer has emitted
    scenarios and the existing SA-validation retry loop has completed,
    BEFORE the format-compatibility/workspace-script guards and the
    iteration loop. Reads scenarios from the host filesystem, runs
    ``scripts/lib/scenario_lint.py`` against them, and routes any §4a
    hits back to the TE for retry. On TE retry-budget exhaustion,
    raises ``HardAbort`` with the message prefixed
    ``scenario_author_retry_exhausted:`` so the orchestrator-level
    ``(DarkFactoryError, BudgetExceeded)`` catch routes the abort
    through ``_handle_abort`` (verified READY by B1-cls-audit surface
    (a)).

    Architectural constraint: does NOT modify the TE container's mount
    manifest — the TE retry uses ``invoke_agent`` with the same barrier
    config as the original Step 2 invocation. This keeps Phase 2 on a
    code path disjoint from Phase 3's planned ``barriers.py`` /
    orchestrator container-mount restructuring (B9d).

    Audit log per-attempt to
    ``.dark-factory/audit/te-acceptance-<spec>-iter<n>.json``. The
    audit dir is masked from the Implementer container per
    ``barriers.py`` — ``_AUDIT in implementer_masked`` — so no §4a
    rule names, file:line anchors, or source excerpts can leak to the
    Implementer's iter-N+1 prelude even on a future resume.

    Retry budget (``config.te_pre_acceptance_max_retries``, default 3)
    is SEPARATE from the Implementer iteration cap per Decision §7 in
    ``docs/ROADMAP.md`` — each agent owns its own retry quota.
    """
    step_begin(spec_run.spec_name, "te_pre_acceptance", config, project_dir)

    scenarios_dir = (
        project_dir / ".dark-factory" / "scenarios" / spec_run.spec_name
    )

    # Sentinel = "scenarios not applicable" — nothing to lint. The TE
    # already justified non-applicability; passing through here without
    # a check is correct.
    if has_sentinel_file(scenarios_dir):
        log.info(
            "te_pre_acceptance: sentinel scenarios for %s, skipping",
            spec_run.spec_name,
        )
        return
    if not has_scenario_files(scenarios_dir):
        log.info(
            "te_pre_acceptance: no scenario files for %s, skipping",
            spec_run.spec_name,
        )
        return

    scenario_lint = _load_scenario_lint_module()

    spec_path = (
        project_dir / ".dark-factory" / "specs"
        / f"{spec_run.spec_name}.md"
    )
    forbidden_shapes = []
    if spec_path.exists():
        forbidden_shapes = scenario_lint.load_forbidden_test_shapes(spec_path)

    audit_dir = project_dir / ".dark-factory" / "audit"
    audit_dir.mkdir(parents=True, exist_ok=True)

    # Reconstruct the contracts RO mount (if any) for TE retry — same
    # logic as ``_step_scenario_generation`` so the staged adapter
    # contracts remain visible to the TE on retry.
    contracts_host = scenarios_dir / "_contracts"
    contracts_ro_args: list[str] = []
    if contracts_host.is_dir() and any(contracts_host.iterdir()):
        container_contracts = (
            f"/project/.dark-factory/scenarios/"
            f"{spec_run.spec_name}/_contracts"
        )
        contracts_ro_args = [
            "-v", f"{contracts_host}:{container_contracts}:ro",
        ]

    max_retries = max(0, config.te_pre_acceptance_max_retries)
    last_issues: list = []
    last_retry_exit: int = 0

    # Budget framing: ``max_retries`` retries plus one initial check.
    # On budget=3, the loop executes up to 4 times (attempts 0..3) —
    # attempt 0 is the initial lint, attempts 1..max_retries are
    # post-retry rechecks. The TE is invoked at most ``max_retries``
    # times. ``range(max_retries + 1)`` makes the off-by-one explicit.
    for attempt in range(max_retries + 1):
        issues = scenario_lint.lint_dir(
            scenarios_dir, forbidden_test_shapes=forbidden_shapes,
        )
        last_issues = issues

        # Audit log per-attempt — masked from Implementer.
        audit_file = audit_dir / (
            f"te-acceptance-{spec_run.spec_name}-iter{attempt}.json"
        )
        audit_payload = {
            "spec": spec_run.spec_name,
            "run_id": spec_run.run_id,
            "attempt": attempt,
            "max_retries": max_retries,
            "issue_count": len(issues),
            "prior_te_retry_exit_code": last_retry_exit,
            "issues": [
                {
                    "file": str(i.file),
                    "line": i.line,
                    "rule": i.rule,
                    "excerpt": i.excerpt,
                    "fix_hint": i.fix_hint,
                }
                for i in issues
            ],
        }
        try:
            audit_file.write_text(
                json.dumps(audit_payload, indent=2), encoding="utf-8",
            )
        except OSError as e:
            # Audit dir is barrier-load-bearing (it is masked from the
            # Implementer container precisely so this content stays out
            # of iter-N+1's prelude). A write failure is an ops-grade
            # incident, not a casual warning. The phase is terminal-
            # before-Implementer for this run, so a dropped audit file
            # does not itself create a leak path; we keep the loop
            # running so the abort decision still reaches the user. Ops
            # action: investigate the audit-dir permissions or disk
            # before re-enabling the gate.
            log.error(
                "te_pre_acceptance: audit write failed at %s (%s) — "
                "continuing (terminal-before-Implementer, no leak path)",
                audit_file, e,
            )

        if not issues:
            log.info(
                "te_pre_acceptance: clean (attempt %d, budget %d)",
                attempt, max_retries,
            )
            return

        if attempt >= max_retries:
            break

        # Clear Tier 2 scenarios before each TE retry — preserves Tier
        # 1 deterministic contracts (``00-tier1-*``) and the staged
        # ``_contracts/`` adapter helpers, removes everything else so
        # the TE rewrites against a clean slate. Without this, a TE
        # retry that emits fix files under new names without deleting
        # the offenders would leave the §4a hits in place; the next
        # lint would see them, mis-attribute the failure to the TE,
        # and burn budget on a problem the TE thought it solved.
        # Symmetry note: the existing SA-validation retry loop in
        # ``_step_scenario_generation`` clears Tier 2 the same way —
        # this is the established contract, not a new convention.
        _clear_tier2_scenarios(scenarios_dir, tier1_prefix="00-tier1-")

        feedback = scenario_lint.format_report(issues)
        retry_prompt = (
            f"Read your agent definition at "
            f"/project/.dark-factory/agents/test-engineer.md. "
            f"Read the spec at "
            f"/project/.dark-factory/specs/{spec_run.spec_name}.md. "
            f"Your previous scenario emission contained §4a forbidden "
            f"patterns and has been cleared (Tier 1 contracts and "
            f"_contracts/ helpers are preserved; everything else under "
            f"/project/.dark-factory/scenarios/{spec_run.spec_name}/ "
            f"has been removed). Re-emit the AC-aligned visible suite "
            f"from scratch, avoiding every flagged pattern. The spec's "
            f"§4a forbidden_test_shapes block is the authoritative "
            f"list; the report below lists the prior hits.\n\n"
            f"## Pre-Acceptance Lint Report (attempt {attempt} failed)\n\n"
            f"{feedback}"
        )

        log.info(
            "te_pre_acceptance: %d issue(s) on attempt %d, invoking TE retry "
            "(budget %d)",
            len(issues), attempt, max_retries,
        )

        retry_result = invoke_agent(
            AgentRole.TEST_ENGINEER, retry_prompt,
            config=config, barriers=barriers,
            run_id=spec_run.run_id, project_dir=project_dir,
            spec_name=spec_run.spec_name, iteration=0,
            events_file=events_file, audit_path=audit_path,
            dry_run=dry_run,
            phase="te_pre_acceptance_retry",
            extra_docker_args=contracts_ro_args or None,
        )
        last_retry_exit = retry_result.exit_code
        if last_retry_exit != 0:
            log.warning(
                "te_pre_acceptance: TE retry %d returned exit %d — "
                "next attempt will lint whatever was emitted",
                attempt, last_retry_exit,
            )

    # Budget exhausted — abort. Classification string is the literal
    # taxonomy key (B1-cls-audit surface (c) READY: analyze_trial.py's
    # classifier accepts arbitrary category strings). Routes through
    # ``_handle_abort`` via the orchestrator-level
    # ``(DarkFactoryError, BudgetExceeded)`` catch (B1-cls-audit
    # surface (a) READY at orchestrator.py:794-813). The classification
    # keyword is colon-separated from the human-readable detail; the
    # post-hoc classifier in ``analyze_trial.py`` matches on the
    # taxonomy key alone, so the detail tail is free-form. Promotion
    # to a structured ``error_code`` kwarg on ``HardAbort`` is a
    # downstream cleanup (Linus #5 in B1 review).
    raise HardAbort(
        f"scenario_author_retry_exhausted: TE pre-acceptance §4a check "
        f"hit {len(last_issues)} issue(s) after {max_retries} retry "
        f"attempt(s); last TE retry exit_code={last_retry_exit}. Audit "
        f"logs at .dark-factory/audit/te-acceptance-"
        f"{spec_run.spec_name}-iter*.json",
        run_id=spec_run.run_id,
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

    F Move 2b: ``step_begin("qa_holdout")`` is NOT called here — it is hoisted
    to the pinned caller ``_step_scenario_generation`` so the TE and QA config
    refreshes run serially on the main thread (no shared-mutable-config race
    when this runs on a worker thread). This helper may run concurrently with
    the Test Engineer; it only ever READS ``config``.
    """
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
    contracts_ro_args: list[str] | None = None,
    active_adapters: "list[EcosystemAdapter] | None" = None,
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
        emit_coverage_architect_amend,
        emit_coverage_architect_amend_completed,
        emit_coverage_architect_amend_failed,
        emit_coverage_duplicate_tags,
        emit_coverage_floor_violation,
        emit_coverage_gap,
        emit_coverage_gap_category_missing,
        emit_coverage_pass,
        emit_coverage_regen,
        emit_coverage_spec_vehicle_mismatch,
        emit_coverage_untestable_waived,
    )
    from darkfactory.scenarios.coverage import (
        GAP_CATEGORY_ARCHITECT,
        GAP_CATEGORY_MISSING,
        GAP_CATEGORY_SPEC_VEHICLE,
        _extract_untestable_markers,
        architect_credited_anchors,
        check_spec_type_floor,
        check_substrate_deferral_consistency,
        classify_gaps,
        compute_coverage,
        project_prefixes_from_manifest,
        parse_coverage_gaps_markdown,
        render_floor_remediation,
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

    # Architect-signal coverage credit: compute_coverage only credits Tier-2
    # scenario files. Anchors verified by a non-scenario vehicle the architect
    # declared (the forbidden-pattern scanner, or a substrate primitive — incl.
    # forward-references marked verifiable=false) would otherwise abort a spec
    # that is correct at its build-phase scope. Credit them from ARCHITECT
    # signals only (never the barrier'd TE's COVERAGE-GAPS.md); fail closed on
    # omission. Applied at every coverage recomputation so regen attempts see
    # the same credit.
    _arch_dir = project_dir / ".dark-factory" / "architecture"
    _substrate_toml = _arch_dir / "substrate-manifests" / f"{spec_run.spec_name}.toml"
    _arch_credited = architect_credited_anchors(
        _arch_dir / f"{spec_run.spec_name}-manifest.yaml",
        _substrate_toml,
    )
    # Asymmetric-deferral consistency (Neo F+E2 ruling, 2026-06-21; bb spec-4
    # FR-2 escalation). A ``verifiable=false`` substrate primitive suppresses
    # gate verification but, without a ``covers`` list, never defers its
    # anchors at THIS floor — so an AC asserting the deferred capability gets
    # an unsatisfiable scenario (the bb spec-4 8/12 ENV_ISSUE → env-dominant
    # escalation). Fail closed deterministically BEFORE any regen:
    #   F  — verifiable=false primitive missing ``covers`` (declare it; ``[]`` ok).
    #   E2 — a covered anchor that is also Non-Negotiable/Blocking (re-scope it
    #        or make the primitive verifiable=true).
    # Blocking set parse is best-effort: if it fails, F still fires (it needs
    # no blocking set); E2 simply under-fires rather than crash the floor.
    _blocking: set[str] = set()
    try:
        from darkfactory.spec.precheck import check_non_negotiable_section
        from darkfactory.spec.spec_model import parse_spec_model
        _nn_findings, _nn_map = check_non_negotiable_section(
            parse_spec_model(spec_file.read_text(encoding="utf-8"))
        )
        _blocking = set(_nn_map)
        # A malformed Non-Negotiable section yields a partial/empty map, so the
        # E2 (Blocking-over-deferred) intersection silently under-fires. The
        # validator owns that failure, but ``--from-phase gate`` skips
        # validation — surface it at warning level so a degraded E2 is visible.
        if _nn_findings:
            log.warning(
                "deferral-consistency: Non-Negotiable section has %d parse "
                "issue(s); E2 (Blocking-over-deferred) may under-fire: %s",
                len(_nn_findings),
                "; ".join(f.kind for f in _nn_findings),
            )
    except Exception as exc:  # noqa: BLE001 — never let parse abort the floor
        log.debug("deferral-consistency: blocking-set parse failed: %s", exc)
    # E3 live-target set: anchors the TE still writes scenarios for. The
    # manifest's id lists already EXCLUDE @untestable / vehicle:subprocess
    # anchors, so a covered-deferred anchor still present here will get an
    # unsatisfiable scenario — surface it (non-silent on AC-12/15/17-class
    # non-blocking deferrals, not just the Blocking ones E2 catches).
    _live_anchors = set(manifest.ac_ids) | set(manifest.ec_ids) | set(manifest.fr_ids)
    _deferral_findings = check_substrate_deferral_consistency(
        _substrate_toml, _blocking, live_scenario_anchors=_live_anchors
    )
    if _deferral_findings:
        # Dedup messages (a duplicated covers entry can emit the same finding
        # twice) while preserving order.
        _detail = " | ".join(dict.fromkeys(f.message for f in _deferral_findings))
        log.warning(
            "substrate deferral inconsistency (%d finding(s)): %s",
            len(_deferral_findings), _detail,
        )
        # Persist a structured report so the authoring loop (the
        # /df-rescope-deferred skill) can apply the surfaced re-scope WITHOUT
        # hand-editing the spec. Shared writer — the validate-time gate writes
        # the SAME report shape, so the two producers and the skill never drift.
        try:
            from darkfactory.spec.deferral_check import (
                write_deferral_consistency_report,
            )
            write_deferral_consistency_report(
                project_dir / ".dark-factory" / "reviews",
                spec_run.spec_name, spec_run.run_id,
                [
                    {
                        "kind": f.kind, "primitive": f.primitive,
                        "anchor": f.anchor, "message": f.message,
                    }
                    for f in _deferral_findings
                ],
            )
        except OSError as exc:  # never let a report-write failure mask the abort
            log.warning("deferral-consistency report write failed: %s", exc)
        raise HardAbort(
            f"substrate_deferral_inconsistency: {len(_deferral_findings)} "
            f"finding(s) — a verifiable=false substrate primitive is "
            f"deferred asymmetrically (no `covers`) or backs a Blocking "
            f"anchor. See .dark-factory/reviews/{spec_run.spec_name}-"
            f"deferral-consistency.json and resolve with /df-rescope-deferred. "
            f"{_detail}",
            run_id=spec_run.run_id,
        )

    # Behavioral-credit (opt-in, default OFF). When ON, a Tier-2 scenario credits
    # an anchor only if it imports >=1 project module (ground-truth via the
    # adapters). Project-import prefixes come from the architect manifest's
    # entry_points — the SAME ground-truth the validator's import-specifier check
    # uses — NOT a fresh tsconfig parse (the soundness gate). compute_coverage
    # derives the behavioral_files set from its own scenarios_dir walk, so the
    # path keys match its credit loop by construction (no two-caller drift).
    _behavioral_kwargs: dict = {}
    if config.require_behavioral_credit:
        if active_adapters:
            _prefixes = project_prefixes_from_manifest(
                _arch_dir / f"{spec_run.spec_name}-manifest.yaml"
            )
            _behavioral_kwargs = {
                "require_behavioral_credit": True,
                "adapters": active_adapters,
                "project_prefixes": _prefixes,
                # runtime-boundary anchors: behavioral but no-importable-unit ACs
                # the author declared; their covering scenario is exempt from the
                # import requirement (still demanded + holdout-tested elsewhere).
                "runtime_boundary_anchors": set(manifest.runtime_boundary_ac_ids),
            }
            log.info(
                "coverage floor: behavioral-credit ON (%d manifest-derived "
                "project prefixes)", len(_prefixes),
            )
        else:
            # Flag on but no adapters to classify imports → cannot apply the rule
            # soundly. Fail SAFE to the pre-existing lexical floor (loud) rather
            # than deny all credit (which would abort every spec).
            log.warning(
                "coverage floor: require_behavioral_credit set but no active "
                "adapters — falling back to lexical credit (rule inert this run)",
            )

    def _cov(scenarios_dir_, manifest_):
        """compute_coverage with the run's behavioral-credit kwargs applied."""
        return compute_coverage(scenarios_dir_, manifest_, **_behavioral_kwargs)

    def _credit_report(report):
        """Drop architect-credited anchors from ``report.uncovered_anchors``.

        Mutating uncovered in place — BEFORE _persist_gaps / classify_gaps /
        the architect-amend + regen loops — is intentional, not a precedence
        bug. A credited anchor's vehicle is, BY CONSTRUCTION, a non-scenario one
        (a substrate primitive or the forbidden-pattern scanner that the
        architect declared); it therefore has no Tier-2 SCENARIO gap to fix, so
        an architect-amend or TE coverage-regen for it would be nonsense
        (re-amending an anchor the architect already declared covered). Removing
        it from the set those loops consume is the correct behavior. The credit
        is logged for audit; an architect that wrongly over-credits is an
        architect-output-quality issue, not something the regen loop should mask.
        """
        if not _arch_credited:
            return report
        before = set(report.uncovered_anchors)
        report.uncovered_anchors = [
            a for a in report.uncovered_anchors if a not in _arch_credited
        ]
        newly = sorted(before - set(report.uncovered_anchors))
        if newly:
            log.info(
                "coverage floor: %d anchor(s) credited by architect signal "
                "(non-scenario vehicle): %s",
                len(newly),
                "; ".join(f"{a} [{_arch_credited[a]}]" for a in newly),
            )
        return report

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

    report = _credit_report(_cov(scenarios_dir, manifest))
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
    # Architect-amend flow (D4 of docs/dev/2026-04-15-coverage-gap-architect-amend.md):
    # before entering the TE retry loop, classify coverage gaps from the
    # TE's COVERAGE-GAPS.md. Architect-fixable gaps (missing entry points)
    # route to a scoped architect invocation; spec-vehicle-mismatch gaps
    # emit a distinct event for humans; runtime-untestable falls through to
    # the existing @untestable waiver logic; category-missing signals TE
    # drift without auto-routing.
    gaps_md_path = (
        scenarios_dir / "COVERAGE-GAPS.md"
    )
    seen_architect_gaps: set[tuple[str, str]] = set()
    architect_amends_done = 0
    spec_vehicle_emitted = False
    category_missing_emitted_for: set[str] = set()

    def _architect_amend_round(current_report) -> None:
        """Route architect-fixable gaps back to the architect (up to cap).

        Updates outer-scope ``seen_architect_gaps``, ``architect_amends_done``,
        ``spec_vehicle_emitted``, ``category_missing_emitted_for``.
        """
        nonlocal architect_amends_done, spec_vehicle_emitted
        if not config.coverage_architect_amend:
            return
        if architect_amends_done >= config.coverage_architect_amend_max_calls:
            return
        gaps = parse_coverage_gaps_markdown(gaps_md_path)
        buckets = classify_gaps(gaps, current_report.uncovered_anchors)

        # One-shot per enforcement: spec-vehicle-mismatch gaps.
        sv_gaps = buckets[GAP_CATEGORY_SPEC_VEHICLE]
        if sv_gaps and not spec_vehicle_emitted and events_file:
            emit_coverage_spec_vehicle_mismatch(
                events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                anchors=[
                    {"id": g.anchor_id, "reason": g.reason} for g in sv_gaps
                ],
            )
            spec_vehicle_emitted = True

        # Category-missing → drift warning, no routing.
        missing_gaps = buckets[GAP_CATEGORY_MISSING]
        missing_new = [
            g for g in missing_gaps
            if g.anchor_id not in category_missing_emitted_for
        ]
        if missing_new and events_file:
            emit_coverage_gap_category_missing(
                events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                anchors=[g.anchor_id for g in missing_new],
            )
            category_missing_emitted_for.update(g.anchor_id for g in missing_new)

        # Architect-fixable: filter out (anchor, module) pairs we've already
        # tried. Prevents burning a second amend on a module the architect
        # already refused/failed to expose.
        arch_gaps = [
            g for g in buckets[GAP_CATEGORY_ARCHITECT]
            if (g.anchor_id, g.needs or "") not in seen_architect_gaps
        ]
        if not arch_gaps:
            return

        amend_number = architect_amends_done + 1
        if events_file:
            emit_coverage_architect_amend(
                events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                amend_number=amend_number,
                gaps=[
                    {"anchor": g.anchor_id, "module": g.needs or ""}
                    for g in arch_gaps
                ],
            )
        try:
            outcome, failure_reason, new_specifiers = _architect_amend_entry_points(
                spec_run, config, barriers, project_dir,
                gaps=arch_gaps,
                spec_file=spec_file,
                manifest_path=(
                    project_dir / ".dark-factory" / "architecture"
                    / f"{spec_run.spec_name}-manifest.yaml"
                ),
                amend_number=amend_number,
                events_file=events_file,
                audit_path=audit_path,
                dry_run=dry_run,
            )
        except DarkFactoryError as exc:
            outcome = "validation_failed"
            failure_reason = str(exc)
            new_specifiers = []
            log.warning("architect amend raised: %s", exc)

        seen_architect_gaps.update(
            (g.anchor_id, g.needs or "") for g in arch_gaps
        )
        architect_amends_done += 1
        if events_file:
            if outcome == "validation_failed":
                emit_coverage_architect_amend_failed(
                    events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                    amend_number=amend_number, reason=failure_reason,
                )
            else:
                emit_coverage_architect_amend_completed(
                    events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
                    amend_number=amend_number, outcome=outcome,
                    new_entry_points=new_specifiers,
                )

    # Initial amend round before the TE retry loop.
    _architect_amend_round(report)

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
            extra_docker_args=contracts_ro_args or None,
        )
        if retry_result.exit_code != 0:
            raise DarkFactoryError(
                f"coverage regen attempt {attempt} failed: Test Engineer "
                f"exited with code {retry_result.exit_code} — aborting rather "
                f"than recomputing coverage on unchanged files"
            )

        _emit_scenario_fingerprints(
            scenarios_dir=scenarios_dir,
            spec_run=spec_run,
            phase="coverage_regen",
            active_adapters=active_adapters or [],
            events_file=events_file,
        )

        report = _credit_report(_cov(scenarios_dir, manifest))
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

        # Permit-one-more: if the TE retry surfaced a *new* architect-fixable
        # gap (different module than any we've already amended for), and we
        # still have budget, try one more architect round before the next
        # TE retry.
        #
        # Note on COVERAGE-GAPS.md freshness: each TE re-invocation rewrites
        # its own COVERAGE-GAPS.md under .dark-factory/scenarios/<spec>/.
        # We re-parse that file here. If the TE happens to re-emit the same
        # (anchor, module) pair we already amended on, ``seen_architect_gaps``
        # filters it out — so round 2 is a no-op rather than a duplicate
        # architect invocation. The architect is only re-invoked when TE
        # surfaces genuinely new architect-fixable work.
        _architect_amend_round(report)

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

    # Actionable remediation: translate the data the pipeline already has (the
    # TE's per-anchor gap reasons + the adapter's vehicle classification) into
    # the exact SPEC edit a non-expert author must make, instead of leaving them
    # with a bare anchor list. Host-side, human-facing, grants no credit — so it
    # crosses no information barrier (see render_floor_remediation barrier note).
    remediation = ""
    if report.uncovered_anchors:
        try:
            remediation = render_floor_remediation(
                parse_coverage_gaps_markdown(gaps_md_path),
                list(report.uncovered_anchors),
                active_adapters or [],
            )
        except Exception:  # noqa: BLE001 — remediation must never mask the abort
            log.debug("floor remediation rendering failed", exc_info=True)

    message = "scenario coverage floor violation: " + " | ".join(parts)
    if remediation:
        message += "\n\n" + remediation
    raise DarkFactoryError(message)


def _architect_amend_entry_points(
    spec_run: SpecRun,
    config: PipelineConfig,
    barriers: dict[AgentRole, BarrierConfig],
    project_dir: Path,
    *,
    gaps: list[CoverageGap],
    spec_file: Path,
    manifest_path: Path,
    amend_number: int,
    events_file: Path | None,
    audit_path: Path | None,
    dry_run: bool,
) -> tuple[str, str, list[str]]:
    """Invoke the architect to amend ``test_integration.entry_points``.

    Contract (docs/dev/2026-04-15-coverage-gap-architect-amend.md §D3):
    - Architect preserves every pre-existing ``entry_points[*]`` entry and
      every other top-level section.
    - Architect adds one or more new entries to
      ``test_integration.entry_points`` whose ``path`` resolves against
      an existing ``file_map`` key and whose ``specifier`` does not
      collide with a pre-existing entry's specifier.
    - If any validation fails, the pre-amend manifest is restored on disk
      and the function returns ``("validation_failed", reason, [])``.

    Returns:
        (outcome, failure_reason, new_specifiers)
        outcome ∈ {"success", "no_progress", "validation_failed"}.
        ``new_specifiers`` is the list of newly-added entry_point
        specifiers as actually written to disk: empty when the architect
        added nothing or when validation failed; non-empty on ``success``;
        on ``no_progress`` it is non-empty only when the architect added
        entries on modules the floor did not ask for.
    """
    import yaml

    if not manifest_path.exists():
        return "validation_failed", f"manifest not found: {manifest_path}", []

    pre_amend_text = manifest_path.read_text(encoding="utf-8")
    try:
        pre_amend = yaml.safe_load(pre_amend_text)
        if not isinstance(pre_amend, dict):
            return (
                "validation_failed",
                "pre-amend manifest is not a mapping",
                [],
            )
    except yaml.YAMLError as exc:
        return "validation_failed", f"pre-amend YAML parse: {exc}", []

    pre_ti = pre_amend.get("test_integration") or {}
    pre_entries = list(pre_ti.get("entry_points") or [])
    pre_specifiers = {
        e.get("specifier") for e in pre_entries if isinstance(e, dict)
    }
    # file_map in the canonical architect manifest is a list of dicts
    # (``- path: ... action: ...``). Dict-keyed form (``{path: {...}}``) is
    # accepted as a defensive fallback. Collapse both into a set of paths
    # for the in-file_map check below.
    raw_file_map = pre_amend.get("file_map") or []
    pre_file_map_paths: set[str] = set()
    if isinstance(raw_file_map, list):
        for entry in raw_file_map:
            if isinstance(entry, dict):
                p = entry.get("path")
                if isinstance(p, str) and p:
                    pre_file_map_paths.add(p)
    elif isinstance(raw_file_map, dict):
        pre_file_map_paths = {k for k in raw_file_map if isinstance(k, str)}

    # Build amend prompt. Barrier hygiene (D7): include only anchor IDs,
    # anchor text, TE's Needs/Reason, and the pre-amend manifest. Scrub
    # any ``.dark-factory/scenarios/`` substring from reason text as
    # defense-in-depth against TE echoing scenario filenames.
    try:
        spec_text = spec_file.read_text(encoding="utf-8")
    except OSError:
        spec_text = ""

    gap_lines: list[str] = []
    requested_modules: set[str] = set()
    for gap in gaps:
        anchor_excerpt = (
            _extract_anchor_text(spec_text, gap.anchor_id) if spec_text else ""
        )
        # Barrier hygiene (D7): strip any reference to a scenario file or
        # `.dark-factory/scenarios/` path from reason text before it reaches
        # the architect. Matches (1) paths containing ``scenarios/``, (2)
        # Vitest/Jest-style filenames ``*.spec.ts`` / ``*.test.ts`` / the
        # Python equivalents.
        safe_reason = re.sub(
            r"(\S*\.dark-factory/scenarios/\S+|\S*scenarios/\S+"
            r"|\S+\.(?:spec|test)\.(?:ts|tsx|js|mjs|py)\b)",
            "[redacted]", gap.reason,
        )
        module_line = (
            f"  - Needs module: {gap.needs}" if gap.needs else
            "  - Needs module: (unspecified by TE — infer from anchor text)"
        )
        if gap.needs:
            requested_modules.add(gap.needs)
        gap_lines.append(
            f"### {gap.anchor_id}\n"
            f"  - Reason: {safe_reason}\n"
            f"{module_line}\n"
            f"  - Anchor text from spec:\n{anchor_excerpt or '    (not found)'}"
        )

    amend_prompt = (
        "Read your agent definition at /project/.dark-factory/agents/architect.md. "
        "You are being invoked in COVERAGE-AMEND mode: your previous manifest "
        f"at /project/.dark-factory/architecture/{spec_run.spec_name}-manifest.yaml "
        "is missing one or more entry points the test-engineer needs to cover "
        "spec anchors. Amend the manifest in place with the following rules:\n"
        "\n"
        "PRESERVE:\n"
        "- Every pre-existing top-level section (`file_map`, `commands`, "
        "`runtime`, `dependencies`, `vitest_config`, `forbidden_patterns`, "
        "anything else present) byte-identical.\n"
        "- Every pre-existing `test_integration.entry_points[*]` entry "
        "byte-identical — do NOT rewrite, reorder, or alias existing entries.\n"
        "\n"
        "ADD to `test_integration.entry_points` (only):\n"
        "- One entry for each module listed below that the test-engineer "
        "needs. Each new entry's `path` MUST appear as a key in `file_map`. "
        "Each new entry's `specifier` MUST NOT collide with any existing "
        "specifier already in `entry_points`. Populate `exports` with the "
        "named symbols the test-engineer will need to import (read the "
        "referenced source file to determine its exports).\n"
        "\n"
        "Anchors requiring new entry points:\n"
        + "\n\n".join(gap_lines)
        + "\n\n"
        "Write the amended manifest back to "
        f"/project/.dark-factory/architecture/{spec_run.spec_name}-manifest.yaml. "
        "Do not produce a fresh manifest — load the existing one, amend, write back."
    )

    # Invoke the architect with the amend prompt.
    result = invoke_agent(
        AgentRole.ARCHITECT, amend_prompt,
        config=config, barriers=barriers,
        run_id=spec_run.run_id, project_dir=project_dir,
        spec_name=spec_run.spec_name, iteration=0,
        events_file=events_file, audit_path=audit_path, dry_run=dry_run,
        max_turns_override=config.architect_max_turns,
        phase=f"coverage_amend_{amend_number}",
    )
    def _validate() -> tuple[str, str, list[str]]:
        """Return (outcome, reason, new_specifiers). Caller restores the
        pre-amend manifest whenever outcome == 'validation_failed' — every
        validation branch here can rely on that single restore point instead
        of duplicating write_text calls.
        """
        if result.exit_code != 0:
            return (
                "validation_failed",
                f"architect exited with code {result.exit_code}",
                [],
            )

        try:
            post_text = manifest_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return "validation_failed", f"post-amend manifest unreadable: {exc}", []
        try:
            post = yaml.safe_load(post_text)
        except yaml.YAMLError as exc:
            return "validation_failed", f"post-amend YAML parse: {exc}", []
        if not isinstance(post, dict):
            return "validation_failed", "post-amend manifest is not a mapping", []

        # Paths in the POST file_map (pre-existing ∪ any additive entries the
        # amend introduced). A new entry_point may legitimately reference a
        # file_map path the amend just added (see the additive-file_map rule in
        # the PRESERVE loop), so the new-entry check below validates against the
        # post — not pre — file_map.
        post_file_map_paths: set[str] = set(pre_file_map_paths)
        _post_fm = post.get("file_map")
        if isinstance(_post_fm, list):
            for _e in _post_fm:
                if isinstance(_e, dict):
                    _p = _e.get("path")
                    if isinstance(_p, str) and _p:
                        post_file_map_paths.add(_p)
        elif isinstance(_post_fm, dict):
            post_file_map_paths |= {k for k in _post_fm if isinstance(k, str)}

        # Schema-adherence on the amend path too (Codex): the round-trip loop
        # below only checks keys that were ALREADY present, so an architect that
        # ADDS a new off-schema top-level key during a coverage amend would slip
        # through. Lint only the NEWLY-ADDED keys (set(post) - set(pre)) — the
        # pre-amend manifest already passed the full initial lint, so re-linting
        # pre-existing keys would be redundant (and would false-fail on any
        # tolerated-but-unlisted legacy key already on disk). A drift here returns
        # validation_failed, and the caller restores the canonical pre-amend manifest.
        from darkfactory.spec.manifest import off_schema_top_level_keys
        added_key_errors = off_schema_top_level_keys(set(post) - set(pre_amend))
        if added_key_errors:
            return (
                "validation_failed",
                "post-amend manifest added non-canonical top-level key(s): "
                + "; ".join(added_key_errors),
                [],
            )

        # Every top-level key (except test_integration) must round-trip byte-
        # equal. Check removal before modification so the reason names the
        # actual fault.
        for key, pre_value in pre_amend.items():
            if key == "test_integration":
                # entry_points are checked additively below. Every OTHER
                # test_integration key (module_system, import_extension,
                # vitest_config, tsconfig, required_capabilities) must round-trip
                # byte-equal: a coverage amend touches ONLY entry_points, so a
                # changed/removed sibling key is a misbehaving amend. Without this,
                # an amend silently drops required_capabilities (re-breaking the
                # Hole-A test-environment fix) — and module_system/vitest_config
                # had the same latent exposure.
                pre_ti = pre_value if isinstance(pre_value, dict) else {}
                post_ti_check = post.get("test_integration") or {}
                for ti_key, ti_val in pre_ti.items():
                    if ti_key == "entry_points":
                        continue
                    if post_ti_check.get(ti_key) != ti_val:
                        return (
                            "validation_failed",
                            f"test_integration.`{ti_key}` was modified or removed "
                            "in amend (amend may touch only entry_points)",
                            [],
                        )
                continue
            if key not in post:
                return (
                    "validation_failed",
                    f"top-level section `{key}` was removed",
                    [],
                )
            if key == "file_map":
                # ADDITIVE-ONLY file_map: a coverage amend that adds an
                # entry_point for a module not yet listed REQUIRES adding that
                # module's file_map entry (the new-entry check below demands
                # `path` be a file_map key). A strict byte-equal check made such
                # amends impossible — e.g. the AC-8 logger entry-point amend
                # always needs a new file_map path, so the whole amend was
                # rejected. Allow ADDITIONS; every pre-existing entry must
                # round-trip verbatim; modifying or removing an existing entry
                # is still rejected.
                post_fm = post.get(key)
                if isinstance(pre_value, list):
                    if not isinstance(post_fm, list):
                        return ("validation_failed",
                                "top-level section `file_map` changed shape", [])
                    for pre_entry in pre_value:
                        if pre_entry not in post_fm:
                            return ("validation_failed",
                                    "pre-existing file_map entry was modified or "
                                    f"removed: {pre_entry!r}", [])
                elif isinstance(pre_value, dict):
                    post_map = post_fm if isinstance(post_fm, dict) else {}
                    for k, v in pre_value.items():
                        if post_map.get(k) != v:
                            return ("validation_failed",
                                    f"pre-existing file_map entry `{k}` was "
                                    "modified or removed", [])
                elif post_fm != pre_value:
                    return ("validation_failed",
                            "top-level section `file_map` was modified", [])
                continue
            if post.get(key) != pre_value:
                return (
                    "validation_failed",
                    f"top-level section `{key}` was modified",
                    [],
                )

        post_ti = post.get("test_integration") or {}
        post_entries = list(post_ti.get("entry_points") or [])

        # Preserve every pre-existing entry verbatim.
        for pre_entry in pre_entries:
            if pre_entry not in post_entries:
                return (
                    "validation_failed",
                    f"pre-existing entry_point was modified or removed: {pre_entry!r}",
                    [],
                )

        # New entries: by identity (not in pre_entries).
        new_entries = [e for e in post_entries if e not in pre_entries]
        new_specifiers: list[str] = []
        seen_new_specifiers: set[str] = set()
        for new in new_entries:
            if not isinstance(new, dict):
                return (
                    "validation_failed",
                    f"new entry_point is not a mapping: {new!r}",
                    [],
                )
            path = new.get("path")
            spec_v = new.get("specifier")
            if not isinstance(path, str) or not path:
                return (
                    "validation_failed",
                    f"new entry_point `path`={path!r} is empty or not a string",
                    [],
                )
            if path not in post_file_map_paths:
                return (
                    "validation_failed",
                    f"new entry_point `path`={path!r} not in file_map",
                    [],
                )
            if not isinstance(spec_v, str) or not spec_v:
                return (
                    "validation_failed",
                    f"new entry_point `specifier`={spec_v!r} is empty or not a string",
                    [],
                )
            if spec_v in pre_specifiers:
                return (
                    "validation_failed",
                    f"new entry_point `specifier`={spec_v!r} collides with existing entry",
                    [],
                )
            if spec_v in seen_new_specifiers:
                return (
                    "validation_failed",
                    f"new entry_point `specifier`={spec_v!r} duplicated within amend",
                    [],
                )
            seen_new_specifiers.add(spec_v)
            new_specifiers.append(spec_v)

        # Classify outcome.
        if not new_entries:
            return "no_progress", "", []
        if requested_modules:
            new_paths = {e.get("path") for e in new_entries}
            if not (requested_modules & new_paths):
                # Architect added entries but not on any requested module.
                # Return the new specifiers so the caller's event reflects
                # the real state on disk; the 'no_progress' verdict still
                # blocks the floor.
                return "no_progress", "", new_specifiers
        return "success", "", new_specifiers

    outcome, reason, new_specifiers = _validate()
    if outcome == "validation_failed":
        manifest_path.write_text(pre_amend_text, encoding="utf-8")
    return outcome, reason, new_specifiers


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
    """Delete Tier 2 scenario files and subdirectories, preserving Tier 1
    files, the ``_contracts/`` directory (staged adapter helpers), and the
    ``_df_setup/`` directory (staged gate test-infra, e.g. the per-test DB
    reset). Both staged trees are produced ONCE before the TE
    generate/validate/regen retry loops; wiping them here would silently drop
    them from the final gate run — for ``_df_setup/`` that reintroduces the
    Sprint-28 DB-contamination bug specifically on RETRIED postgres specs
    (Sprint 29 / Codex gate)."""
    for entry in scenarios_dir.iterdir():
        if (
            entry.name.startswith(tier1_prefix)
            or entry.name == "_contracts"
            or entry.name == "_df_setup"
        ):
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        elif entry.is_file():
            entry.unlink()


# F Move 2c: human-readable rename target for the corrective prompt. This is
# DELIBERATELY ADVISORY PROSE for the LLM — NOT a sync obligation with
# detection._TEST_PATTERNS (the authoritative collector glob). It names the
# dominant `.test.ts`/`.test.js`/`.test.mjs` convention so the agent knows what
# to rename to; exact parity with the glob list is neither required nor kept.
_REQUIRED_TEST_NAME_HINT = (
    "*.test.ts / *.test.js / *.test.mjs (or *.spec.ts) — e.g. "
    "`fr-1-workspace-install.js` -> `fr-1-workspace-install.test.ts`"
)


def _format_misnamed_corrective(misnamed: list[str]) -> str:
    """Deterministic corrective appended to the warm-session retry prompt.

    Names the exact offending files the TE itself wrote and the required
    naming pattern. Deterministic (sorted input, no timestamps/randomness) so
    temp=0 reproducibility holds. This is the *signal* the cold retry lacked —
    the agent ignored the static naming rule, so we name its concrete mistake."""
    listed = "\n".join(f"  - {name}" for name in misnamed[:20])
    more = "" if len(misnamed) <= 20 else f"\n  - ...and {len(misnamed) - 20} more"
    return (
        "CRITICAL NAMING ERROR — your previous attempt wrote scenario files that "
        "the test runner will NOT collect because their names match no recognized "
        "test pattern:\n"
        f"{listed}{more}\n\n"
        f"RENAME each of these in place to a recognized pattern: {_REQUIRED_TEST_NAME_HINT}. "
        "Do NOT regenerate the test content — it is correct; only the filename "
        "extension is wrong. Keep every already-correctly-named file untouched."
    )


def _dump_misnamed_failure(
    project_dir: Path, spec_run: SpecRun, misnamed: list[str]
) -> None:
    """Persist a structured failure artifact when the misnamed-retry budget is
    exhausted. Written to ``.dark-factory/failures/`` (masked from all restricted
    agent roles per S33, so barrier-clean) so the terminal error is diagnosable
    (vs the old generic "no scenario files written" that lied — files existed)."""
    try:
        failures_dir = project_dir / ".dark-factory" / "failures"
        failures_dir.mkdir(parents=True, exist_ok=True)
        path = failures_dir / f"{spec_run.spec_name}-misnamed-scenarios.json"
        path.write_text(
            json.dumps(
                {
                    "abort_reason": "MISNAMED_SCENARIOS",
                    "spec": spec_run.spec_name,
                    "run_id": spec_run.run_id,
                    "offending_files": misnamed,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
    except OSError as exc:
        log.debug("could not write misnamed-scenarios failure artifact: %s", exc)


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

    # Protect staged contract helpers from TE modification during repair.
    contracts_host = scenarios_dir / "_contracts"
    _repair_ro_args: list[str] | None = None
    if contracts_host.is_dir() and any(contracts_host.iterdir()):
        container_contracts = (
            f"/project/.dark-factory/scenarios/"
            f"{spec_run.spec_name}/_contracts"
        )
        _repair_ro_args = ["-v", f"{contracts_host}:{container_contracts}:ro"]

    try:
        invoke_agent(
            AgentRole.TEST_ENGINEER, prompt,
            config=config, barriers=barriers,
            run_id=spec_run.run_id, project_dir=project_dir,
            spec_name=spec_run.spec_name, iteration=0,
            events_file=events_file, audit_path=audit_path, dry_run=dry_run,
            phase="repair",
            extra_docker_args=_repair_ro_args,
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

    # Phase 6: re-smoke to verify repair actually fixed crashes.
    # Best-effort — log but don't revert if crashes remain.
    try:
        re_smoke = _step_smoke_test(spec_run, config, project_dir, dry_run=dry_run)
        if re_smoke.crashes > 0:
            log.warning(
                "post-repair smoke still has %d crash(es) — proceeding anyway",
                re_smoke.crashes,
            )
        else:
            log.info("post-repair smoke: all scenarios run cleanly")
    except Exception:
        log.warning("post-repair smoke failed, proceeding anyway", exc_info=True)


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


def _load_latest_defect_categories(
    spec_run: SpecRun, project_dir: Path,
) -> dict[str, dict]:
    """Return a ``test_name -> {category, rationale}`` map from classifier output.

    Walks ``reports/<spec>-defects-iter{N,N-1,...,1}.json`` and returns
    the first readable payload as a flat dict keyed by ``test_name``.
    Empty dict when nothing is found — callers should treat unknown
    categories as non-skippable.
    """
    reports_dir = project_dir / ".dark-factory" / "reports"
    for i in range(spec_run.iteration, 0, -1):
        path = reports_dir / f"{spec_run.spec_name}-defects-iter{i}.json"
        if not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        defects = raw.get("defects") if isinstance(raw, dict) else None
        if not isinstance(defects, list):
            continue
        out: dict[str, dict] = {}
        for d in defects:
            if not isinstance(d, dict):
                continue
            name = d.get("test_name")
            if not name:
                continue
            out[str(name)] = {
                "category": str(d.get("category") or ""),
                "rationale": str(d.get("rationale") or ""),
            }
        if out:
            return out
    return {}


def _write_defect_registry(spec_run: SpecRun, project_dir: Path) -> None:
    """Write defect registry + known-gaps skip list for ship-with-gaps runs.

    Two artifacts are emitted under ``.dark-factory/reports/``:

    1. ``<spec>-defect-registry.md`` — human-readable markdown,
       preserved for observability regardless of gap category.
    2. ``<spec>-known-gaps.json`` — machine-readable skip list, **only
       emitted** when every stable failure is classified as one of
       :data:`SKIPPABLE_CATEGORIES`. If *any* failure is IMPL_BUG /
       SPEC_AMBIGUOUS / UNKNOWN, the JSON is not written — the gap
       stays visible in the markdown but future runs will still
       surface it. ``_handle_ship`` copies both artifacts into the
       spec's tracked ``shipped-scenarios/<spec>/`` archive.
    """
    last_iter = spec_run.current_iteration
    categories = _load_latest_defect_categories(spec_run, project_dir)

    # Resolve category per stable failure (default BRITTLE for legacy
    # runs where the classifier didn't emit a category). This same
    # resolution feeds both the markdown table and the skip-list
    # decision below.
    stable_sorted = sorted(spec_run.stable_failure_names)
    resolved: list[tuple[str, str, str]] = []  # (name, category, rationale)
    for name in stable_sorted:
        meta = categories.get(name, {})
        cat = meta.get("category") or "BRITTLE"
        rationale = meta.get("rationale") or ""
        resolved.append((name, cat, rationale))

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
    for i, (name, cat, _rationale) in enumerate(resolved, 1):
        lines.append(f"| {i} | {name} | {cat} | {spec_run.iteration} |")

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

    # Known-gaps JSON: only emit when EVERY stable failure is
    # skippable. A mixed set means at least one real bug is hiding in
    # there — masking the ENV_ISSUEs alongside it would let the real
    # regression slip through the smoke probe on future runs.
    if not resolved:
        return
    non_skippable = [c for (_n, c, _r) in resolved if c not in SKIPPABLE_CATEGORIES]
    if non_skippable:
        log.info(
            "known-gaps: skipping JSON emission for %s — %d non-skippable "
            "classification(s): %s",
            spec_run.spec_name, len(non_skippable), sorted(set(non_skippable)),
        )
        return

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    gaps_file = KnownGapsFile(
        spec=spec_run.spec_name,
        shipped_at=now,
        gaps=[
            KnownGap(
                test_name=name,
                category=cat,
                anchor=extract_anchor(name),
                added_at=now,
                source=f"oracle_iter{spec_run.iteration}",
                rationale=rationale,
            )
            for (name, cat, rationale) in resolved
        ],
    )
    gaps_path = (
        project_dir / ".dark-factory" / "reports"
        / f"{spec_run.spec_name}-known-gaps.json"
    )
    gaps_path.write_text(
        json.dumps(gaps_file.to_dict(), indent=2, sort_keys=True) + "\n"
    )
    log.info(
        "wrote known-gaps skip list: %s (%d gap%s)",
        gaps_path, len(resolved), "" if len(resolved) == 1 else "s",
    )


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

    # Phase 4.3: capture service names from the environment contract at
    # ship time. Used by Phase 4.3 dependency warnings and Phase 5
    # regression drift detection.
    try:
        from darkfactory.architecture.runtime import load_environment as _load_env
        _ship_env = _load_env(project_dir)
        services_at_ship = tuple(sorted(_ship_env.services))
    except Exception:  # noqa: BLE001 — best-effort, never unship
        services_at_ship = ()

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
                services_at_ship=services_at_ship,
            )
        except Exception:  # noqa: BLE001 — never unship on archive failure
            log.exception(
                "post-ship archive failed for %s", spec_run.spec_name,
            )

    # Copy review and defect-registry into the ship record (best-effort).
    # These files live in gitignored directories (reviews/, reports/) but
    # belong in the tracked shipped-scenarios/<spec>/ directory as quality
    # provenance and gap documentation.  See docs/POST-SHIP-COMMIT.md.
    ship_record_dir = (
        project_dir / ".dark-factory" / "shipped-scenarios" / spec_run.spec_name
    )
    if ship_record_dir.is_dir():
        ship_record_copies = [
            (project_dir / ".dark-factory" / "reviews"
             / f"{spec_run.spec_name}-review.md", "review.md"),
            (project_dir / ".dark-factory" / "reports"
             / f"{spec_run.spec_name}-defect-registry.md", "defect-registry.md"),
            # Known-gaps skip list — consumed by run_scenarios on
            # future regression / smoke-probe runs via the shim it
            # writes into .dark-factory/scenarios/.df-known-gaps-skip.mjs.
            (project_dir / ".dark-factory" / "reports"
             / f"{spec_run.spec_name}-known-gaps.json", ".known-gaps.json"),
        ]
        for src, dest_name in ship_record_copies:
            if src.is_file():
                try:
                    shutil.copy2(src, ship_record_dir / dest_name)
                    log.info(
                        "archived %s -> shipped-scenarios/%s/%s",
                        src.name, spec_run.spec_name, dest_name,
                    )
                except OSError:
                    log.exception("failed to archive %s", src.name)

    # Commit the COMPLETE durable ship record (ledger + scenario archive + the
    # review/defect-registry/known-gaps provenance copied just above), INDEPENDENT
    # of auto_pr, so it survives a later working-copy reset (database-foundation
    # was lost exactly this way). commit_ship_ledger is internally best-effort and
    # never raises, so it cannot unship; it warns loudly if the record is left
    # non-durable (e.g. a gitignored ledger path).
    if config.commit_ledger_on_ship and not dry_run:
        git_ops.commit_ship_ledger(spec_run.spec_name, project_dir)

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
        # C1: pair any prior REMEDIATION_TARGET with an outcome.
        _emit_remediation_outcome_if_pending(
            events_file, spec_run=spec_run, terminal_status="shipped",
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
    # Archive uses the count of iterations that actually ran, NOT
    # spec_run.iteration (which may have been incremented past the last
    # real iteration by the bottom-of-loop +=1).
    _completed = len(spec_run.iterations)
    snapshot_iteration(
        spec_run.archive_root, project_dir,
        spec_run.spec_name, _completed,
    )
    snapshot_iteration_diff_for_completed(
        spec_run, project_dir, _completed,
        config.source_dirs, config.test_dir,
    )
    snapshot_final(
        spec_run.archive_root, project_dir, spec_run.spec_name,
        spec_run.run_id, status="shipped", iteration=_completed,
        abort_reason=spec_run.abort_reason,
    )
    return 0


def _handle_stable_failure(
    spec_run: SpecRun,
    config: PipelineConfig,
    project_dir: Path,
    *,
    state_file: Path,
    events_file: Path | None = None,
    metrics_file: Path | None = None,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> int:
    """Handle a terminal STABLE_FAILURE (Sprint 1, 2026-06-08).

    Reached when an eligible run's Oracle says NO-SHIP and the run is not
    composite-blocked. This is an honest non-ship: it MUST NOT set SHIPPED
    status, MUST NOT append to shipped.jsonl, MUST NOT emit a ship event or
    ship notification, and MUST NOT invoke the escalation agent. It documents
    the stable gaps (defect registry) and records a distinct terminal
    event/metric. Exit code 5.
    """
    def _progress(msg: str) -> None:
        if progress is not None:
            progress(msg)

    spec_run.status = PipelineStatus.STABLE_FAILURE

    # Document the stable gaps (diagnostic only — NOT a ship signal). The run is
    # ship-with-gaps-eligible by definition here, so this always applies; call
    # unconditionally rather than coupling to the soon-to-be-renamed flag.
    _write_defect_registry(spec_run, project_dir)

    write_state(state_file, "status", "stable_failure")
    write_state_or_warn(state_file, "completed_step", "99")

    last_iter = spec_run.current_iteration
    pass_rate = last_iter.pass_rate if last_iter else 0
    if events_file:
        emit_spec_stable_failure(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            iteration=spec_run.iteration,
            iterations=len(spec_run.iterations),
            stable_failures=len(spec_run.stable_failure_names),
            stable_failure_ratio=spec_run.stable_failure_ratio,
            pass_rate=pass_rate,
        )
        emit_cost_summary(
            events_file, spec=spec_run.spec_name, run_id=spec_run.run_id,
            total_turns=spec_run.total_turns,
            estimated_usd=estimate_cost(spec_run.total_turns, config).estimated_usd,
        )
        # Pair any prior REMEDIATION_TARGET with an outcome (parity with the
        # ship/escalate/abort handlers — otherwise remediation metrics leak).
        _emit_remediation_outcome_if_pending(
            events_file, spec_run=spec_run, terminal_status="stable_failure",
        )

    if metrics_file:
        record_end(
            metrics_file, spec_run.spec_name, spec_run.run_id, "stable_failure",
            spec_run.iteration - 1, spec_run.total_turns, spec_run.wall_elapsed,
        )

    _progress(
        f"STABLE_FAILURE: {spec_run.spec_name} — stable failing set, "
        f"Oracle declined to ship (not shipped)"
    )
    _completed = len(spec_run.iterations)
    snapshot_iteration(
        spec_run.archive_root, project_dir, spec_run.spec_name, _completed,
    )
    snapshot_iteration_diff_for_completed(
        spec_run, project_dir, _completed,
        config.source_dirs, config.test_dir,
    )
    snapshot_final(
        spec_run.archive_root, project_dir, spec_run.spec_name,
        spec_run.run_id, status="stable_failure", iteration=_completed,
        abort_reason=spec_run.abort_reason,
    )
    return 5


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
        _emit_remediation_outcome_if_pending(
            events_file, spec_run=spec_run, terminal_status="escalated",
        )

    if metrics_file:
        record_end(
            metrics_file, spec_run.spec_name, spec_run.run_id, "escalated",
            spec_run.iteration - 1, spec_run.total_turns, spec_run.wall_elapsed,
        )

    notify_escalated(config, spec_run.spec_name, spec_run.run_id,
                     len(spec_run.iterations))
    _progress(f"ESCALATED: {spec_run.spec_name} (after {len(spec_run.iterations)} iterations)")
    _completed = len(spec_run.iterations)
    snapshot_iteration(
        spec_run.archive_root, project_dir,
        spec_run.spec_name, _completed,
    )
    snapshot_iteration_diff_for_completed(
        spec_run, project_dir, _completed,
        config.source_dirs, config.test_dir,
    )
    snapshot_final(
        spec_run.archive_root, project_dir, spec_run.spec_name,
        spec_run.run_id, status="escalated", iteration=_completed,
        abort_reason=spec_run.abort_reason,
    )
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
        _emit_remediation_outcome_if_pending(
            events_file, spec_run=spec_run, terminal_status="failed",
        )

    if metrics_file:
        record_end(
            metrics_file, spec_run.spec_name, spec_run.run_id, "aborted",
            spec_run.iteration - 1, spec_run.total_turns, spec_run.wall_elapsed,
        )

    notify_failed(config, spec_run.spec_name, spec_run.run_id, reason)
    if progress is not None:
        progress(f"ABORTED: {spec_run.spec_name} — {reason}")
    _completed = len(spec_run.iterations)
    snapshot_iteration(
        spec_run.archive_root, project_dir,
        spec_run.spec_name, _completed,
    )
    snapshot_iteration_diff_for_completed(
        spec_run, project_dir, _completed,
        config.source_dirs, config.test_dir,
    )
    snapshot_final(
        spec_run.archive_root, project_dir, spec_run.spec_name,
        spec_run.run_id, status="aborted", iteration=_completed,
        abort_reason=spec_run.abort_reason,
    )
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
    _TERMINAL   = ("shipped", "deployed", "rolled_back", "regression-failed", "escalated", "failed", "stable_failure")

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



_TERMINAL_STATUSES = frozenset({
    PipelineStatus.SHIPPED,
    PipelineStatus.DEPLOYED,
    PipelineStatus.ESCALATED,
    PipelineStatus.ROLLED_BACK,
    PipelineStatus.ABORTED,
    PipelineStatus.STABLE_FAILURE,
})


def _write_interrupted_state(state_file: Path, spec_run: SpecRun) -> None:
    """Write interrupted state for crash recovery.

    Skips the write if the pipeline already reached a terminal state —
    prevents the atexit handler from overwriting e.g. ``shipped`` with
    ``interrupted`` on normal process exit.
    """
    if spec_run.status in _TERMINAL_STATUSES:
        return
    write_state_or_warn(state_file, "status", "interrupted")
    write_state_or_warn(state_file, "iteration", str(spec_run.iteration))
    write_state_or_warn(state_file, "completed_step", str(spec_run.completed_step))
