"""CLI entry point — argparse subcommands.

Provides:
  dark-factory init
  dark-factory run <spec>
  dark-factory plan <plan-name>
  dark-factory precheck <spec>
  dark-factory validate <spec>
  dark-factory gate <spec>
  dark-factory scenarios <spec>
  dark-factory deploy <spec>
  dark-factory dashboard
  dark-factory export-otel
  dark-factory events rotate
  dark-factory regression
  dark-factory integrate <path>
  dark-factory setup validate
  dark-factory migrate-config
  dark-factory calibrate-cost
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from darkfactory.errors import ConfigError, DarkFactoryError

log = logging.getLogger("darkfactory")


def main(argv: list[str] | None = None) -> None:
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        prog="dark-factory",
        description="Dark Factory — AI development framework",
    )
    parser.add_argument("--project-root", type=Path, default=Path("."),
                        help="project root directory")
    parser.add_argument("--config", type=Path, default=None,
                        help="config file path")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would be done without executing")
    parser.add_argument("--debug", action="store_true",
                        help="enable debug logging")
    parser.add_argument("--verbose", action="store_true",
                        help="enable verbose logging")
    parser.add_argument("--ci", action="store_true",
                        help="CI mode: no color, no interactive prompts")

    sub = parser.add_subparsers(dest="command")

    # init
    p_init = sub.add_parser("init", help="initialize a Dark Factory project")
    p_init.add_argument("--non-interactive", action="store_true",
                        help="skip interactive prompts, use detected defaults")
    p_init.add_argument("--language", default="",
                        help="override language detection (python, node, go, rust)")

    # new-spec
    p_newspec = sub.add_parser("new-spec", help="generate a draft spec from a description")
    p_newspec.add_argument("description", help="short description of the feature/fix/refactor")
    p_newspec.add_argument("--template", default="",
                           help="override template auto-detection (feature, bug-fix, refactor, api, etc.)")

    # run
    p_run = sub.add_parser("run", help="run the pipeline for a spec")
    p_run.add_argument("spec", nargs="?", help="spec name")
    p_run.add_argument("--max-iterations", type=int, default=0)
    p_run.add_argument("--resume", action="store_true")
    p_run.add_argument("--regen-scenarios", action="store_true")
    p_run.add_argument("--regression", action="store_true")

    # plan
    p_plan = sub.add_parser("plan", help="execute a plan")
    p_plan.add_argument("plan_name", help="plan name")
    p_plan.add_argument("--max-concurrent", type=int, default=0)
    p_plan.add_argument("--resume", action="store_true")
    p_plan.add_argument("--optimize", action="store_true",
                        help="optimize spec order using historical data")

    # precheck
    p_precheck = sub.add_parser("precheck", help="run deterministic pre-check on a spec")
    p_precheck.add_argument("spec", help="spec name")

    # validate
    p_val = sub.add_parser("validate", help="validate a spec")
    p_val.add_argument("spec", help="spec name")

    # gate
    p_gate = sub.add_parser("gate", help="run quality gate")
    p_gate.add_argument("spec", help="spec name")

    # scenarios
    p_scen = sub.add_parser("scenarios", help="run scenarios")
    p_scen.add_argument("spec", help="spec name")

    # dashboard
    p_dash = sub.add_parser("dashboard", help="show event dashboard")
    p_dash.add_argument("--last", type=int, default=0, help="show last N runs")
    p_dash.add_argument("--spec", default="", help="filter by spec")

    # export-otel
    p_otel = sub.add_parser("export-otel", help="export OTEL traces/metrics")
    p_otel.add_argument("--output", type=Path, default=None, help="output file")
    p_otel.add_argument("--no-traces", action="store_true", help="skip trace export")
    p_otel.add_argument("--no-metrics", action="store_true", help="skip metrics export")

    # events
    p_events = sub.add_parser("events", help="event management")
    events_sub = p_events.add_subparsers(dest="events_command")
    p_rotate = events_sub.add_parser("rotate", help="rotate events file")
    p_rotate.add_argument("--max-size", type=int, default=10 * 1024 * 1024)
    p_rotate.add_argument("--keep", type=int, default=3)
    events_sub.add_parser("migrate", help="migrate metrics to events")

    # regression
    p_reg = sub.add_parser("regression", help="run regression tests")
    p_reg.add_argument("--only-affected", action="store_true",
                        help="only run specs whose target-files overlap with changed files")

    # integrate
    p_int = sub.add_parser("integrate", help="integrate into a project")
    p_int.add_argument("path", type=Path, help="target project path")
    p_int.add_argument("--language", default="")
    p_int.add_argument("--symlink", action="store_true")
    p_int.add_argument("--force", action="store_true")

    # setup
    p_setup = sub.add_parser("setup", help="setup tools")
    setup_sub = p_setup.add_subparsers(dest="setup_command")
    setup_sub.add_parser("validate", help="validate environment setup")

    # migrate-config
    p_migrate = sub.add_parser("migrate-config", help="convert config.sh to config.toml")
    p_migrate.add_argument("--config-path", type=Path,
                           default=Path(".dark-factory/config.sh"))
    p_migrate.add_argument("--output", type=Path,
                           default=Path(".dark-factory/config.toml"))

    # scale-test
    p_scale = sub.add_parser("scale-test", help="run scale validation across all registered specs")
    p_scale.add_argument("--concurrency", type=int, default=0,
                          help="max parallel workers (default: config.scale_concurrency)")

    # deploy
    p_deploy = sub.add_parser("deploy", help="manually trigger post-ship deployment for a spec")
    p_deploy.add_argument("spec", help="spec name")
    p_deploy.add_argument("--run-id", default="", help="existing run ID (default: generate new)")

    # confirm
    p_confirm = sub.add_parser("confirm", help="confirm a held spec (autonomy level 1)")
    p_confirm.add_argument("spec", help="spec name to confirm")

    # metrics
    p_metrics = sub.add_parser("metrics", help="show metrics summary")
    p_metrics.add_argument("--file", type=Path, default=None)

    # calibrate-cost
    sub.add_parser("calibrate-cost", help="calibrate cost estimation against actual token usage")

    args = parser.parse_args(argv)

    # Setup logging.
    _setup_logging(args)

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        _dispatch(args)
    except ConfigError as e:
        log.error("config error: %s", e)
        sys.exit(1)
    except DarkFactoryError as e:
        log.error("error: %s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


def _setup_logging(args: argparse.Namespace) -> None:
    """Configure logging based on CLI flags."""
    if args.debug:
        level = logging.DEBUG
    elif args.verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    fmt = "%(levelname)s: %(message)s"
    if args.debug:
        fmt = "%(asctime)s %(name)s %(levelname)s: %(message)s"

    logging.basicConfig(level=level, format=fmt, stream=sys.stderr)


_COMMANDS: dict[str, object] = {}  # populated after function definitions


def _dispatch(args: argparse.Namespace) -> None:
    """Dispatch to the appropriate subcommand handler."""
    project = args.project_root.resolve()
    handler = _COMMANDS.get(args.command)
    if handler is None:
        log.error("unknown command: %s", args.command)
        sys.exit(1)
    handler(args, project)  # type: ignore[operator]


def _load_config(args: argparse.Namespace, project: Path):
    """Load config from file or defaults."""
    from darkfactory.config.loader import load_config
    config_path = args.config or (project / ".dark-factory" / "config.toml")
    return load_config(config_path)


def _cmd_run(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.pipeline.orchestrator import run_pipeline
    from darkfactory.config.schema import validate_spec_name
    config = _load_config(args, project)

    spec = args.spec
    if not spec:
        from darkfactory.tui.selector import select_spec
        spec = select_spec(project, ci_mode=args.ci)
        if not spec:
            log.error("no spec selected")
            sys.exit(1)

    # Validate spec name and check if file exists
    validate_spec_name(spec)
    spec_file = project / ".dark-factory" / "specs" / f"{spec}.md"
    if not spec_file.exists():
        log.error("spec file not found: %s", spec_file)
        sys.exit(1)

    # Resolve per-spec profile and field overrides.
    from darkfactory.config.profiles import resolve_profile
    config = resolve_profile(spec_file, config)

    if args.max_iterations > 0:
        config.max_iterations = args.max_iterations

    exit_code = run_pipeline(spec, config, project, dry_run=args.dry_run, progress=print)
    sys.exit(exit_code)


def _cmd_plan(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.plan.executor import execute_plan
    config = _load_config(args, project)

    if args.max_concurrent > 0:
        config.max_concurrent_specs = args.max_concurrent

    events_file = project / ".dark-factory" / "events.jsonl"
    result = execute_plan(
        args.plan_name, config, project,
        resume=args.resume, optimize=args.optimize,
        dry_run=args.dry_run, events_file=events_file,
    )

    print(f"\nPlan complete: {result.shipped} shipped, {result.failed} failed, {result.skipped} skipped")
    sys.exit(1 if result.failed > 0 else 0)


def _cmd_dashboard(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.events.dashboard import build_dashboard, format_dashboard
    events_file = project / ".dark-factory" / "events.jsonl"
    if not events_file.exists():
        print("No events file found. Run the pipeline to generate events.")
        return
    data = build_dashboard(events_file, last_n=args.last, spec_filter=args.spec)
    print(format_dashboard(data))


def _cmd_export_otel(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.events.export_otel import export_to_file, export_traces, export_metrics
    import json
    events_file = project / ".dark-factory" / "events.jsonl"
    if not events_file.exists():
        print("No events file found.")
        return

    do_traces = not args.no_traces
    do_metrics = not args.no_metrics

    if args.output:
        export_to_file(events_file, args.output, traces=do_traces, metrics=do_metrics)
        print(f"Exported to {args.output}")
    else:
        result = {}
        if do_traces:
            result["traces"] = export_traces(events_file)
        if do_metrics:
            result["metrics"] = export_metrics(events_file)
        print(json.dumps(result, indent=2))


def _cmd_events(args: argparse.Namespace, project: Path) -> None:
    events_file = project / ".dark-factory" / "events.jsonl"
    if args.events_command == "rotate":
        from darkfactory.events.rotate import rotate
        if rotate(events_file, max_size=args.max_size, keep_count=args.keep):
            print("Events file rotated.")
        else:
            print("No rotation needed.")
    elif args.events_command == "migrate":
        from darkfactory.events.rotate import migrate_metrics
        metrics_file = project / ".dark-factory" / "metrics.jsonl"
        count = migrate_metrics(metrics_file, events_file)
        print(f"Migrated {count} records.")
    else:
        print("Usage: dark-factory events {rotate|migrate}")
        sys.exit(1)


def _cmd_regression(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.scenarios.regression import run_regression
    config = _load_config(args, project)

    changed_files = None
    if args.only_affected:
        from darkfactory.utils.subprocess_runner import run as run_cmd
        result = run_cmd(["git", "-C", str(project), "diff", "--name-only", config.git_base_branch])
        if result.exit_code == 0 and result.stdout.strip():
            changed_files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        else:
            changed_files = []

    passed, failed, total = run_regression(config, project, changed_files=changed_files)
    print(f"Regression: {passed}/{total} passed, {failed} failed")
    sys.exit(1 if failed > 0 else 0)


def _cmd_integrate(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.integration.integrator import integrate
    success = integrate(
        args.path.resolve(), project,
        language=args.language, symlink=args.symlink,
        force=args.force, dry_run=args.dry_run,
    )
    sys.exit(0 if success else 1)


def _cmd_setup(args: argparse.Namespace, project: Path) -> None:
    if args.setup_command == "validate":
        from darkfactory.setup.validator import validate_setup
        config = _load_config(args, project)
        report = validate_setup(project, config)
        for check in report.checks:
            status = check.status.upper()
            print(f"  {status:<6} {check.message}")
        print(f"\n{report.pass_count} passed, {report.fail_count} failed, {report.warn_count} warnings")
        sys.exit(0 if report.passed else 1)
    else:
        print("Usage: dark-factory setup validate")
        sys.exit(1)


def _cmd_migrate_config(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.config.loader import migrate_config
    input_path = project / args.config_path
    output_path = project / args.output
    migrate_config(input_path, output_path)
    print(f"Migrated {input_path} → {output_path}")


def _cmd_metrics(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.setup.metrics_summary import format_table, summarize_metrics
    metrics_file = args.file or (project / ".dark-factory" / "metrics.jsonl")
    if not metrics_file.exists():
        print("No metrics file found.")
        return
    summary = summarize_metrics(metrics_file)
    print(format_table(summary))


def _cmd_precheck(args: argparse.Namespace, project: Path) -> None:
    """Run deterministic pre-check on a spec (FR-7)."""
    from darkfactory.config.schema import validate_spec_name
    from darkfactory.spec.precheck import run_precheck

    config = _load_config(args, project)
    spec = args.spec
    validate_spec_name(spec)
    spec_file = project / ".dark-factory" / "specs" / f"{spec}.md"
    if not spec_file.exists():
        log.error("spec file not found: %s", spec_file)
        sys.exit(1)

    # Resolve per-spec profile and field overrides.
    from darkfactory.config.profiles import resolve_profile
    config = resolve_profile(spec_file, config)

    result = run_precheck(spec_file)
    print(result.summary)
    sys.exit(0 if result.passed else 1)


def _cmd_validate(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.config.schema import validate_spec_name
    from darkfactory.spec.precheck import run_precheck

    config = _load_config(args, project)
    spec = args.spec
    validate_spec_name(spec)
    spec_file = project / ".dark-factory" / "specs" / f"{spec}.md"
    if not spec_file.exists():
        log.error("spec file not found: %s", spec_file)
        sys.exit(1)

    # Resolve per-spec profile and field overrides.
    from darkfactory.config.profiles import resolve_profile
    config = resolve_profile(spec_file, config)

    # Run deterministic pre-check before invoking the LLM validator.
    precheck = run_precheck(spec_file)
    if not precheck.passed:
        print(precheck.summary)
        sys.exit(1)
    elif precheck.ambiguities:
        log.warning("pre-check: %d ambiguity warnings", len(precheck.ambiguities))

    # Run the pipeline in validation-only mode (step 1 only).
    from darkfactory.pipeline.orchestrator import run_pipeline
    exit_code = run_pipeline(spec, config, project, dry_run=args.dry_run)
    sys.exit(exit_code)


def _cmd_gate(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.pipeline.quality_gate import run_quality_gate
    from darkfactory.config.schema import validate_spec_name
    config = _load_config(args, project)

    # Validate spec and resolve profile
    spec = args.spec
    validate_spec_name(spec)
    spec_file = project / ".dark-factory" / "specs" / f"{spec}.md"
    if not spec_file.exists():
        log.error("spec file not found: %s", spec_file)
        sys.exit(1)

    # Resolve per-spec profile and field overrides.
    from darkfactory.config.profiles import resolve_profile
    config = resolve_profile(spec_file, config)

    result = run_quality_gate(spec, config, "standalone", project, dry_run=args.dry_run)
    print(f"Gate result: exit_code={result.exit_code} pass_rate={result.pass_rate}% satisfaction={result.satisfaction_score}")
    sys.exit(result.exit_code)


def _cmd_scenarios(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.scenarios.runner import run_scenarios
    from darkfactory.config.schema import validate_spec_name
    config = _load_config(args, project)

    # Validate spec and resolve profile
    spec = args.spec
    validate_spec_name(spec)
    spec_file = project / ".dark-factory" / "specs" / f"{spec}.md"
    if not spec_file.exists():
        log.error("spec file not found: %s", spec_file)
        sys.exit(1)

    # Resolve per-spec profile and field overrides.
    from darkfactory.config.profiles import resolve_profile
    config = resolve_profile(spec_file, config)

    result = run_scenarios(spec, config, project, dry_run=args.dry_run)
    sys.exit(result.exit_code)


def _cmd_deploy(args: argparse.Namespace, project: Path) -> None:
    """Manually trigger the post-ship deployment pipeline for a spec."""
    from darkfactory.config.schema import validate_spec_name
    from darkfactory.models.pipeline import SpecRun
    from darkfactory.pipeline.cleanup import CleanupManager
    from darkfactory.pipeline.orchestrator import post_ship_pipeline

    config = _load_config(args, project)
    spec = args.spec
    validate_spec_name(spec)

    if not config.canary_enabled:
        log.error("canary_enabled is false — nothing to deploy")
        sys.exit(1)

    state_dir = project / ".dark-factory" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    events_file = project / ".dark-factory" / "events.jsonl"
    state_file = state_dir / f"{spec}.state"

    spec_run = SpecRun(spec_name=spec)
    if args.run_id:
        spec_run.run_id = args.run_id

    cleanup = CleanupManager(
        run_id=spec_run.run_id,
        dry_run=args.dry_run,
        deploy_teardown_cmd=config.deploy_teardown_cmd,
        project_dir=str(project),
    )
    cleanup.register()

    try:
        success = post_ship_pipeline(
            spec_run, config, project,
            state_file=state_file,
            events_file=events_file,
            cleanup_manager=cleanup,
        )
        if success:
            print(f"Deployment successful: {spec}")
            sys.exit(0)
        else:
            reason = ""
            if spec_run.deployment_result:
                reason = spec_run.deployment_result.rollback_reason or spec_run.deployment_result.error
            print(f"Deployment failed: {spec} — {reason}")
            sys.exit(1)
    finally:
        cleanup.cleanup()


def _cmd_new_spec(args: argparse.Namespace, project: Path) -> None:
    """Generate a draft spec from a description."""
    from darkfactory.spec.generator import classify_template, generate_spec

    template_name = args.template or classify_template(args.description)
    print(f"Template: {template_name} (auto-detected)" if not args.template
          else f"Template: {template_name} (user-specified)")
    print("Generating draft spec...")

    spec_path = generate_spec(
        args.description,
        project,
        template_override=args.template,
        framework_dir=project,
    )

    print(f"\nDraft written: {spec_path}")
    print("\nEdit the spec, then run:")
    print(f"  dark-factory run {spec_path.stem}")


def _cmd_init(args: argparse.Namespace, project: Path) -> None:
    """Initialize a Dark Factory project."""
    from darkfactory.setup.init import init_project
    result = init_project(
        project,
        non_interactive=args.non_interactive or args.ci,
        language=args.language,
    )
    if not result.success:
        for err in result.errors or []:
            log.error("%s", err)
        sys.exit(1)

    print("\nReady. Run the pipeline on a spec:")
    print("  dark-factory run <spec-name>")


def _cmd_calibrate_cost(args: argparse.Namespace, project: Path) -> None:
    """Run cost calibration benchmark."""
    from darkfactory.intelligence.calibrate import run_calibration
    config = _load_config(args, project)
    factor = run_calibration(config, project)
    print(f"\nCorrection factor: {factor:.2f}")
    print("Future cost estimates will be adjusted automatically.")


def _cmd_scale_test(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.scenarios.scale import run_scale_validation
    config = _load_config(args, project)
    events_file = project / ".dark-factory" / "events.jsonl"

    result = run_scale_validation(
        config, project,
        concurrency=args.concurrency,
        events_file=events_file if events_file.exists() else None,
    )
    print(f"Scale: {result.passed}/{result.total} passed, {result.failed} failed")
    print(f"  Wall time: {result.wall_seconds:.1f}s")
    print(f"  Throughput: {result.scenarios_per_hour:.0f} scenarios/hr")
    print(f"  Latency: p50={result.p50:.1f}s p95={result.p95:.1f}s p99={result.p99:.1f}s")
    sys.exit(1 if result.failed > 0 else 0)


def _cmd_confirm(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.pipeline.orchestrator import run_pipeline
    from darkfactory.config.schema import validate_spec_name
    config = _load_config(args, project)
    spec = args.spec

    # Validate spec
    validate_spec_name(spec)
    spec_file = project / ".dark-factory" / "specs" / f"{spec}.md"
    if not spec_file.exists():
        log.error("spec file not found: %s", spec_file)
        sys.exit(1)

    # Resolve per-spec profile and field overrides.
    from darkfactory.config.profiles import resolve_profile
    config = resolve_profile(spec_file, config)

    hold_file = project / ".dark-factory" / "state" / f"{spec}.hold"
    if not hold_file.exists():
        log.error("no hold found for spec %r — nothing to confirm", spec)
        sys.exit(1)

    # Read hold metadata for logging.
    hold_data = hold_file.read_text()
    log.info("confirming held spec: %s\n%s", spec, hold_data.strip())

    # Remove hold and resume the pipeline to complete ship.
    hold_file.unlink()
    exit_code = run_pipeline(
        spec, config, project,
        resume=True,
        dry_run=args.dry_run,
        progress=print,
    )
    sys.exit(exit_code)


_COMMANDS.update({
    "init": _cmd_init,
    "new-spec": _cmd_new_spec,
    "run": _cmd_run,
    "plan": _cmd_plan,
    "dashboard": _cmd_dashboard,
    "export-otel": _cmd_export_otel,
    "events": _cmd_events,
    "regression": _cmd_regression,
    "integrate": _cmd_integrate,
    "setup": _cmd_setup,
    "migrate-config": _cmd_migrate_config,
    "deploy": _cmd_deploy,
    "scale-test": _cmd_scale_test,
    "confirm": _cmd_confirm,
    "metrics": _cmd_metrics,
    "precheck": _cmd_precheck,
    "validate": _cmd_validate,
    "gate": _cmd_gate,
    "scenarios": _cmd_scenarios,
    "calibrate-cost": _cmd_calibrate_cost,
})
