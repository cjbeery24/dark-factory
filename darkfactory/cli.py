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
import dataclasses
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
    p_init.add_argument("--update", action="store_true",
                        help="refresh framework files (agents/, templates/) from the installed package")
    p_init.add_argument("--migrate-hints", dest="migrate_hints", action="store_true",
                        help="consolidate legacy hints.json into project-hints.toml and prompt to delete the old file")
    p_init.add_argument("--yes", action="store_true",
                        help="with --migrate-hints, auto-delete legacy hints.json without prompting (does not apply to other prompts)")

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
    p_run.add_argument("--fresh", action="store_true",
                       help="force a clean start, ignoring any interrupted state")
    p_run.add_argument("--regen-scenarios", action="store_true")
    p_run.add_argument("--regression", action="store_true")
    p_run.add_argument("--force", action="store_true",
                       help="bypass depends-on soft check against shipped.jsonl")

    # plan
    p_plan = sub.add_parser("plan", help="execute a plan")
    p_plan.add_argument("plan_name", help="plan name")
    p_plan.add_argument("--max-concurrent", type=int, default=0)
    p_plan.add_argument("--resume", action="store_true")
    p_plan.add_argument("--optimize", action="store_true",
                        help="optimize spec order using historical data")
    p_plan.add_argument("--sequential", action="store_true",
                        help="run specs strictly one-at-a-time, halting on first failure (Phase 6)")

    # plan-rollback (Phase 6.5)
    # Note: `plan-resume` is not a separate subcommand — use
    # `dark-factory plan <name> --sequential --resume`.
    p_pback = sub.add_parser("plan-rollback", help="roll back shipped state to a named spec, removing later deltas")
    p_pback.add_argument("--to", dest="target_spec", required=True,
                         help="name of the spec to roll back TO (inclusive — this spec stays shipped)")
    p_pback.add_argument("--dry-run", action="store_true",
                         help="compute what would be rolled back without mutating disk")

    # precheck
    p_precheck = sub.add_parser("precheck", help="run deterministic pre-check on a spec")
    p_precheck.add_argument("spec", help="spec name")
    p_precheck.add_argument("--strict", action="store_true",
                            help="treat advisory findings as fatal (for human authoring + CI)")

    # validate
    p_val = sub.add_parser("validate", help="validate a spec")
    p_val.add_argument("spec", nargs="?", help="spec name (omit to select interactively)")
    p_val.add_argument("--strict", action="store_true",
                       help="treat advisory precheck findings as fatal (for human authoring + CI)")

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

    # post-mortem (E2: calibration dataset)
    p_pm = sub.add_parser(
        "post-mortem",
        help="calibration dataset (append post-mortem rows)",
    )
    pm_sub = p_pm.add_subparsers(dest="post_mortem_command")
    p_pm_append = pm_sub.add_parser(
        "append",
        help="append one row to docs/post-mortem-dataset.jsonl",
    )
    p_pm_append.add_argument("--spec", required=True, help="spec id (e.g., 2026-03-28-database-foundation)")
    p_pm_append.add_argument("--run-id", required=True, help="pipeline run id")
    p_pm_append.add_argument("--date", required=True, help="YYYY-MM-DD")
    p_pm_append.add_argument("--target-project", required=True, help="target project name")
    p_pm_append.add_argument("--outcome", required=True, choices=("shipped", "escalated", "aborted"))
    p_pm_append.add_argument("--iterations", type=int, required=True)
    p_pm_append.add_argument("--validator-verdict", required=True, choices=("PASS", "FAIL"))
    p_pm_append.add_argument("--validator-score", type=int, default=None, help="0..100")
    p_pm_append.add_argument("--validator-divergence", default=None, choices=("LOW", "MEDIUM", "HIGH"))
    p_pm_append.add_argument(
        "--validator-blockers",
        default="",
        help='comma-separated blocker labels (use "" for none)',
    )
    p_pm_append.add_argument(
        "--stuck-set-categories",
        default="",
        help="comma-separated free-form tags (empty for shipped outcome)",
    )
    p_pm_append.add_argument(
        "--root-causes",
        default="",
        help="comma-separated codes from ROOT_CAUSE_CODES vocabulary",
    )
    p_pm_append.add_argument("--dark-factory-head", default=None, help="git SHA of dark-factory at run start")
    p_pm_append.add_argument("--notes", default=None, help="one-line note (no newlines)")
    p_pm_append.add_argument(
        "--dataset",
        type=Path,
        default=None,
        help="override dataset path (default: docs/post-mortem-dataset.jsonl in dark-factory repo root)",
    )

    pm_sub.add_parser("list-codes", help="print the ROOT_CAUSE_CODES vocabulary")

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

    # build
    p_build = sub.add_parser("build", help="build Dark Factory Docker images")
    p_build.add_argument("language", nargs="?", default=None,
                         help="language to build: python, node, go, rust, all (default: auto-detect from config)")

    # calibrate-cost
    sub.add_parser("calibrate-cost", help="calibrate cost estimation against actual token usage")

    # specs (Phase 4.0)
    p_specs = sub.add_parser("specs", help="spec management")
    specs_sub = p_specs.add_subparsers(dest="specs_command")
    p_mfm = specs_sub.add_parser(
        "migrate-frontmatter",
        help="backfill spec_type into existing spec frontmatter",
    )
    p_mfm.add_argument("--apply", action="store_true",
                       help="write changes (default: dry-run)")
    p_mfm.add_argument("--force", action="store_true",
                       help="overwrite existing spec_type values")

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


_ENTERPRISE_MSG = (
    "This command requires enterprise features. "
    "Set enterprise_enabled = true in .dark-factory/config.toml"
)


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

    # Phase 6.4 — depends_on soft check for standalone runs. Name-form
    # deps that aren't in shipped.jsonl are warn-and-gate (--force bypasses).
    # ID-form deps (F-1, F-1a) only resolve inside a plan runner, so in a
    # standalone run we warn and move on — plan §6.4 explicitly calls for
    # "warn and require --force" on *missing* deps, not on unresolvable ones.
    from darkfactory.plan.depends_on import missing_dependencies
    missing_deps, unresolvable = missing_dependencies(spec_file, project)
    if missing_deps and not args.force:
        log.error(
            "spec %r depends on unshipped spec(s): %s. Run the dependencies "
            "first, or pass --force to bypass this check.",
            spec, ", ".join(missing_deps),
        )
        sys.exit(1)
    if unresolvable:
        log.warning(
            "spec %r declares ID-form depends-on entries that cannot be "
            "resolved outside a plan context: %s — ignoring for standalone run",
            spec, ", ".join(unresolvable),
        )
    if missing_deps and args.force:
        log.warning(
            "spec %r has unmet depends-on: missing=%s — proceeding under --force",
            spec, ", ".join(missing_deps),
        )

    # Resolve per-spec profile and field overrides.
    from darkfactory.config.profiles import resolve_profile
    config = resolve_profile(spec_file, config)

    if args.max_iterations > 0:
        config.max_iterations = args.max_iterations

    # Determine resume mode.
    # --fresh  → always start over, ignore state
    # --resume → explicit resume request (backward compat alias)
    # default  → auto-detect inside run_pipeline via _try_auto_resume
    resume = args.resume and not args.fresh

    events_file = project / ".dark-factory" / "events.jsonl"
    exit_code = run_pipeline(
        spec, config, project,
        resume=resume,
        fresh=args.fresh,
        regen_scenarios=args.regen_scenarios,
        dry_run=args.dry_run,
        events_file=events_file,
        progress=print,
    )
    sys.exit(exit_code)


def _cmd_plan(args: argparse.Namespace, project: Path) -> None:
    config = _load_config(args, project)
    events_file = project / ".dark-factory" / "events.jsonl"

    if args.sequential:
        # --sequential is an explicit choice for the strict one-at-a-time
        # runner. Flags that only make sense for the concurrent executor
        # (max-concurrent, optimize) are rejected rather than silently
        # ignored — silent ignore is a footgun.
        if args.max_concurrent > 0:
            print("error: --max-concurrent is incompatible with --sequential", file=sys.stderr)
            sys.exit(2)
        if args.optimize:
            print("error: --optimize is incompatible with --sequential", file=sys.stderr)
            sys.exit(2)
        if getattr(args, "dry_run", False):
            # Dry-run support for the sequential runner is a Phase 6.5-ish
            # follow-up; fail loud rather than silently ignore.
            print("error: --dry-run is not yet supported for --sequential", file=sys.stderr)
            sys.exit(2)
        from darkfactory.plan.runner import run_sequential_plan
        result = run_sequential_plan(
            args.plan_name, config, project,
            resume=args.resume, events_file=events_file,
            progress=print,
        )
        print(
            f"\nPlan complete (sequential): {len(result.shipped)} shipped, "
            f"{'1' if result.failed else '0'} failed, {len(result.skipped)} skipped"
        )
        if result.failed:
            print(f"Halted on: {result.failed} [{result.failure_category}] — {result.failed_reason}")
            if result.skipped:
                print(f"Skipped: {', '.join(result.skipped)}")
            # Exit 1 for pipeline failures; 3 for smoke-probe halts so CI
            # can distinguish "this spec broke" from "this spec silently
            # broke a previously-shipped spec". Chosen to avoid the
            # orchestrator/runner exit-code 2 collision.
            if result.failure_category in ("probe_stranded", "probe_regressed"):
                sys.exit(3)
            sys.exit(result.failed_exit_code or 1)
        sys.exit(0)

    from darkfactory.plan.executor import execute_plan

    if args.max_concurrent > 0:
        config.max_concurrent_specs = args.max_concurrent

    result = execute_plan(
        args.plan_name, config, project,
        resume=args.resume, optimize=args.optimize,
        dry_run=args.dry_run, events_file=events_file,
    )

    print(f"\nPlan complete: {result.shipped} shipped, {result.failed} failed, {result.skipped} skipped")
    sys.exit(1 if result.failed > 0 else 0)


def _cmd_plan_rollback(args: argparse.Namespace, project: Path) -> None:
    """Phase 6.5 — roll back to a named spec, removing later deltas."""
    from darkfactory.plan.rollback import RollbackError, rollback_plan_to
    try:
        result = rollback_plan_to(project, args.target_spec, dry_run=args.dry_run)
    except RollbackError as exc:
        print(f"rollback failed: {exc}", file=sys.stderr)
        sys.exit(1)

    verb = "Would roll back" if args.dry_run else "Rolled back"
    print(f"{verb} to: {result.target}")
    print(f"  Specs affected: {len(result.rolled_back_specs)}")
    for s in result.rolled_back_specs:
        print(f"    - {s}")
    print(f"  Delta files: {len(result.removed_deltas)}")
    for d in result.removed_deltas:
        print(f"    - {d}")
    if not args.dry_run and result.new_env_hash:
        print(f"  New env hash: {result.new_env_hash[:12]}")
    sys.exit(0)


def _cmd_dashboard(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.events.dashboard import build_dashboard, format_dashboard
    events_file = project / ".dark-factory" / "events.jsonl"
    if not events_file.exists():
        print("No events file found. Run the pipeline to generate events.")
        return
    data = build_dashboard(events_file, last_n=args.last, spec_filter=args.spec)
    print(format_dashboard(data))


def _cmd_export_otel(args: argparse.Namespace, project: Path) -> None:
    config = _load_config(args, project)
    if not config.enterprise_enabled:
        print(_ENTERPRISE_MSG)
        return
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


def _cmd_post_mortem(args: argparse.Namespace, project: Path) -> None:
    """E2: append a calibration-dataset row or list the code vocabulary."""
    from darkfactory.calibration.post_mortem import (
        ROOT_CAUSE_CODES,
        PostMortemRow,
        append_row,
        default_dataset_path,
    )

    if args.post_mortem_command == "list-codes":
        for code in sorted(ROOT_CAUSE_CODES):
            print(code)
        return

    if args.post_mortem_command != "append":
        print("Usage: dark-factory post-mortem {append|list-codes}")
        sys.exit(1)

    def _split_csv(raw: str) -> tuple[str, ...]:
        if not raw:
            return ()
        return tuple(s.strip() for s in raw.split(",") if s.strip())

    try:
        row = PostMortemRow(
            spec_id=args.spec,
            run_id=args.run_id,
            date=args.date,
            target_project=args.target_project,
            outcome=args.outcome,
            iterations=args.iterations,
            validator_verdict=args.validator_verdict,
            validator_score=args.validator_score,
            validator_divergence=args.validator_divergence,
            validator_blockers=_split_csv(args.validator_blockers),
            stuck_set_categories=_split_csv(args.stuck_set_categories),
            root_cause_codes=_split_csv(args.root_causes),
            dark_factory_head=args.dark_factory_head,
            notes=args.notes,
        )
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)

    # Default dataset lives in the dark-factory repo, not the target
    # project: calibration data is cross-project and belongs with the
    # framework source.
    if args.dataset is not None:
        path = args.dataset
    else:
        # The dark-factory repo root is the parent of the installed package;
        # find it by walking up from this file. If we can't resolve, fall
        # back to CWD — user can always pass --dataset explicitly.
        pkg_root = Path(__file__).resolve().parent.parent
        path = default_dataset_path(pkg_root)

    append_row(row, path=path)
    print(f"appended row → {path}")


def _cmd_regression(args: argparse.Namespace, project: Path) -> None:
    config = _load_config(args, project)
    if not config.enterprise_enabled:
        print(_ENTERPRISE_MSG)
        return
    from darkfactory.scenarios.regression import run_regression

    changed_files = None
    if args.only_affected:
        from darkfactory.utils.subprocess_runner import run as run_cmd
        result = run_cmd(["git", "-C", str(project), "diff", "--name-only", config.git_base_branch])
        if result.exit_code == 0 and result.stdout.strip():
            changed_files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
        else:
            changed_files = []

    passed, failed, total, deferred = run_regression(
        config, project, changed_files=changed_files,
    )
    print(f"Regression: {passed}/{total} passed, {failed} failed")
    if deferred:
        print(f"Deferred (over budget): {len(deferred)} — {', '.join(deferred)}")
    # Phase 5.4: regression failures exit 2 (more severe than 1=new-scenario
    # failure) so callers can route on severity.
    sys.exit(2 if failed > 0 else 0)


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
    """Run deterministic pre-check on a spec (FR-7).

    Phase 0 of resilience rewrite: precheck is advisory by default. Only
    structural failures (parser error, oversize, empty) exit non-zero.
    Pass --strict to treat any finding as fatal (for human authoring + CI).
    """
    from darkfactory.config.schema import validate_spec_name
    from darkfactory.spec.advisory import write_precheck_advisory
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
    # Always persist the advisory — same code path as the orchestrator.
    # Non-fatal: advisory is context for the Architect, not a gate.
    try:
        write_precheck_advisory(project, spec, result)
    except (OSError, TypeError) as exc:
        log.warning("could not write precheck advisory: %s", exc)
    print(result.summary)
    if result.fatal:
        sys.exit(1)
    if getattr(args, "strict", False) and not result.passed:
        sys.exit(1)
    sys.exit(0)


def _read_status_field(reviews_dir: Path, spec: str, field: str, default: str) -> str:
    status_file = reviews_dir / f"{spec}-validation.status"
    if status_file.exists():
        for line in status_file.read_text().splitlines():
            if line.startswith(f"{field}="):
                return line.partition("=")[2].strip()
    return default


def _cmd_validate(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.config.schema import validate_spec_name
    from darkfactory.spec.precheck import run_precheck

    config = _load_config(args, project)
    spec = args.spec
    if not spec:
        from darkfactory.tui.selector import select_spec
        spec = select_spec(project, ci_mode=getattr(args, "ci", False))
        if not spec:
            log.error("no spec selected")
            sys.exit(1)
    validate_spec_name(spec)
    spec_file = project / ".dark-factory" / "specs" / f"{spec}.md"
    if not spec_file.exists():
        log.error("spec file not found: %s", spec_file)
        sys.exit(1)

    # Resolve per-spec profile and field overrides.
    from darkfactory.config.profiles import resolve_profile
    config = resolve_profile(spec_file, config)

    # Round-count instrumentation: record this precheck invocation against
    # the current spec content hash. run_precheck stays pure (no FS I/O for
    # the counter).
    from darkfactory.spec.precheck_runs import record_precheck_run, spec_hash
    try:
        _hash = spec_hash(spec_file.read_bytes())
        _count = record_precheck_run(project, spec, _hash)
        print(f"precheck run #{_count} for this spec content")
        if _count >= 3:
            print(
                "(round count >= 3 — please open an issue if the diagnostics "
                "aren't telling you what to fix)"
            )
    except OSError as exc:
        log.warning("could not record precheck run: %s", exc)

    # Run deterministic pre-check before invoking the LLM validator.
    # Phase 0: advisory by default. Only structural failures (parser error,
    # oversize, empty) abort. --strict treats any finding as fatal.
    from darkfactory.spec.advisory import write_precheck_advisory
    precheck = run_precheck(spec_file)
    try:
        write_precheck_advisory(project, spec, precheck)
    except (OSError, TypeError) as exc:
        log.warning("could not write precheck advisory: %s", exc)
    if precheck.fatal:
        print(precheck.summary)
        sys.exit(1)
    if getattr(args, "strict", False) and not precheck.passed:
        print(precheck.summary)
        sys.exit(1)
    if not precheck.passed:
        log.warning(
            "pre-check has advisory findings (proceeding); "
            "see .dark-factory/precheck/%s.json", spec,
        )
    elif precheck.ambiguities:
        log.warning("pre-check: %d ambiguity warnings", len(precheck.ambiguities))

    # Phase 4.6: frontmatter + spec-type-specific structural gates that
    # `dark-factory validate` enforces *before* invoking the LLM validator.
    # Failing here is FATAL — the orchestrator's own checks would catch
    # missing spec_type later, but we want a clear error at validate time
    # so the user doesn't pay for an LLM round trip first.
    from darkfactory.spec.frontmatter_gate import check_spec_frontmatter_gate
    ok, message = check_spec_frontmatter_gate(spec_file)
    if not ok:
        print(f"FAIL  {spec}  — {message}")
        sys.exit(1)

    # Run the pipeline in validation-only mode (step 1 only).
    from darkfactory.pipeline.orchestrator import run_pipeline
    from darkfactory.scenarios.detection import validate_spec_result
    try:
        exit_code = run_pipeline(
            spec, config, project, dry_run=args.dry_run, stop_after_step=1,
            allow_resume=False,  # validate is read-only; never auto-resume stale state
            progress=lambda msg: print(msg),
        )
    except DarkFactoryError:
        # Orchestrator raises on validation failure; catch it so the
        # detailed report code below can run instead of the generic
        # top-level "error: ..." handler.
        exit_code = 1

    # Always print the final status so the user knows what happened.
    reviews_dir = project / ".dark-factory" / "reviews"
    passed, reason = validate_spec_result(reviews_dir, spec)
    if passed:
        print(f"PASS  {spec}  (score {_read_status_field(reviews_dir, spec, 'score', '?')}/100)")
    else:
        print(f"FAIL  {spec}  — {reason}")
        score = _read_status_field(reviews_dir, spec, 'score', '?')
        divergence = _read_status_field(reviews_dir, spec, 'divergence', '?')
        blockers = _read_status_field(reviews_dir, spec, 'blockers', '?')
        print(f"      score={score}/100  divergence={divergence}  blockers={blockers}")
        print(f"      details: .dark-factory/reviews/{spec}-validation.md")

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

    # Standalone gate runs without pipeline network infrastructure (no pre-created
    # Docker networks or proxy containers). Disable egress filtering so the container
    # uses the default bridge network with direct internet access instead of trying
    # to join a non-existent internal network.
    config = dataclasses.replace(config, egress_filtering=False)
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

    # Phase 5.2: pass spec_type so feature/bugfix runs union the archived
    # shipped-scenarios into the same invocation. We read the frontmatter
    # directly here rather than threading it through the profile resolver
    # because resolve_profile doesn't expose spec_type to the caller.
    from darkfactory.config.profiles import parse_spec_type
    try:
        spec_type = parse_spec_type(spec_file)
    except Exception:  # noqa: BLE001 — bad frontmatter shouldn't block manual gate runs
        spec_type = None

    result = run_scenarios(
        spec, config, project, dry_run=args.dry_run, spec_type=spec_type,
    )
    sys.exit(result.exit_code)


def _cmd_deploy(args: argparse.Namespace, project: Path) -> None:
    """Manually trigger the post-ship deployment pipeline for a spec."""
    config = _load_config(args, project)
    if not config.enterprise_enabled:
        print(_ENTERPRISE_MSG)
        return
    from darkfactory.config.schema import validate_spec_name
    from darkfactory.models.pipeline import SpecRun
    from darkfactory.pipeline.cleanup import CleanupManager
    from darkfactory.pipeline.orchestrator import post_ship_pipeline
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
        update=args.update,
        migrate_hints=args.migrate_hints,
        yes=args.yes,
    )
    if not result.success:
        for err in result.errors or []:
            log.error("%s", err)
        sys.exit(1)

    lang = result.language or "all"

    print("\nNext steps:")
    print(f"  1. Build Docker images:  dark-factory build {lang}")
    print( "  2. Write a spec:         dark-factory new-spec \"<description>\"")
    print( "  3. Run the pipeline:     dark-factory run <spec-name>")


def _cmd_build(args: argparse.Namespace, project: Path) -> None:
    """Build Dark Factory Docker images."""
    import darkfactory
    framework_dir = Path(darkfactory.__file__).parent.parent
    build_script = framework_dir / "docker" / "build-images.sh"
    if not build_script.exists():
        log.error("build script not found: %s", build_script)
        log.error("dark-factory must be installed as an editable install (pip install -e /path/to/dark-factory)")
        sys.exit(1)

    language = args.language
    if language is None:
        try:
            config = _load_config(args, project)
            language = config.language or "all"
            if language != "all":
                print(f"Detected language from config: {language}")
        except Exception:
            language = "all"

    cmd = ["bash", str(build_script), language]

    if args.dry_run:
        print(f"[dry-run] would run: {' '.join(cmd)}")
        return

    import subprocess
    result = subprocess.run(cmd)
    sys.exit(result.returncode)


def _cmd_calibrate_cost(args: argparse.Namespace, project: Path) -> None:
    """Run cost calibration benchmark."""
    config = _load_config(args, project)
    if not config.enterprise_enabled:
        print(_ENTERPRISE_MSG)
        return
    from darkfactory.intelligence.calibrate import run_calibration
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


def _cmd_specs(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.spec.migrate_frontmatter import migrate_all

    if args.specs_command != "migrate-frontmatter":
        log.error("unknown specs subcommand: %s", args.specs_command)
        sys.exit(1)

    specs_dir = project / ".dark-factory" / "specs"
    results = migrate_all(specs_dir, apply=args.apply, force=args.force)

    applied = sum(1 for r in results if r.applied)
    errored = sum(1 for r in results if r.reason.startswith("error:"))
    skipped = sum(1 for r in results if not r.applied and not r.reason.startswith("error:"))

    for r in results:
        prefix = "APPLIED " if r.applied else "        "
        prev = f" (was {r.previous!r})" if r.previous else ""
        print(f"{prefix}{r.path.name}: {r.reason}{prev}")

    print()
    print(f"migrate-frontmatter: {len(results)} specs, "
          f"{applied} applied, {skipped} planned/skipped, {errored} errored")
    if not args.apply:
        print("(dry-run — re-run with --apply to write changes)")
    if errored:
        sys.exit(2)


_COMMANDS.update({
    "init": _cmd_init,
    "specs": _cmd_specs,
    "new-spec": _cmd_new_spec,
    "run": _cmd_run,
    "plan": _cmd_plan,
    "plan-rollback": _cmd_plan_rollback,
    "dashboard": _cmd_dashboard,
    "export-otel": _cmd_export_otel,
    "events": _cmd_events,
    "post-mortem": _cmd_post_mortem,
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
    "build": _cmd_build,
    "calibrate-cost": _cmd_calibrate_cost,
})
