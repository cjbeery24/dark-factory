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
  dark-factory hints {list,lint,add,init}
  dark-factory remediate [run-id] [--latest] [--apply] [--dry-run] [--rerun]
  dark-factory regression
  dark-factory integrate <path>
  dark-factory setup validate
  dark-factory migrate-config
  dark-factory calibrate-cost
  dark-factory contracts auto-eval <helper> [--model] [--rounds] [--out]
  dark-factory contracts init <adapter> [--force] [--dry-run]
  dark-factory contracts verify <adapter>
"""

from __future__ import annotations

import argparse
import dataclasses
import json
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
    p_run.add_argument("--skip-validation", action="store_true",
                       help="reuse a prior PASSing spec-validation instead of "
                            "re-invoking the LLM validator agent, IFF the spec "
                            "bytes are unchanged since that validation (hash "
                            "match) AND both the PASS .status and the .md "
                            "validation report from that run are present. Fails "
                            "CLOSED (re-validates) on any mismatch. The cheap "
                            "deterministic precheck + post-validation gates "
                            "still run; only the slow non-deterministic LLM "
                            "agent call is skipped.")
    p_run.add_argument("--regression", action="store_true")
    p_run.add_argument("--force", action="store_true",
                       help="bypass depends-on soft check against shipped.jsonl")
    p_run.add_argument("--no-adapter", action="store_true",
                       help="DEGRADED RUN: skip adapter fail-fast; pipeline "
                            "runs with no ecosystem-specific precheck / "
                            "consistency rules / architect addenda. Debugging "
                            "escape hatch; not recommended for production.")
    p_run.add_argument("--from-phase", choices=["gate"], default=None,
                       help="Re-enter the pipeline at a specific phase using the "
                            "prior run's on-disk artifacts. 'gate' skips "
                            "validation/architecture/test-engineer and the first "
                            "iteration's INITIAL implementer — running the quality "
                            "gate (then review/oracle) against the EXISTING on-disk "
                            "implementation. Use to verify a gate/framework fix "
                            "against a known-correct impl and ship it without "
                            "re-running the upstream agents. NOTE: the skip is "
                            "one-shot — a pre-gate (lint/typecheck/build) failure "
                            "can still re-invoke the implementer within iteration "
                            "1. Rejects if the prior artifacts (architect "
                            "manifest, scenarios) are absent.")
    # Phase 6 V-dimension anchor flags. Each pins one stage of the
    # pipeline to an artifact from a prior run so variance can be
    # attributed to a single component. See
    # docs/plans/2026-04-16-agent-reliability-and-measurement.md § Phase 6.
    p_run.add_argument("--anchor-manifest", type=Path, default=None,
                       metavar="PATH",
                       help="(Phase 6 V2) seed architect manifest from a "
                            "prior run's .dark-factory/architecture/"
                            "<spec>-manifest.yaml")
    p_run.add_argument("--anchor-scenarios", type=Path, default=None,
                       metavar="DIR",
                       help="(Phase 6 V3) seed Test Engineer scenarios "
                            "from a prior run's .dark-factory/scenarios/<spec>/ "
                            "directory")
    p_run.add_argument("--anchor-environment", type=Path, default=None,
                       metavar="PATH",
                       help="(Phase 6 V4) seed environment.toml from a "
                            "prior run's .dark-factory/architecture/"
                            "environment.toml")

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

    # benchmark
    p_bench = sub.add_parser(
        "benchmark",
        help="run a battery of specs from clean state and emit a JSONL scorecard",
    )
    p_bench.add_argument("battery", help="path to a battery TOML file")
    p_bench.add_argument(
        "--out", default="", help="scorecard JSONL output path (default: ./benchmark-scorecard.jsonl)",
    )
    p_bench.add_argument(
        "--dry-run", dest="bench_dry_run", action="store_true",
        help="print the run plan without executing anything",
    )

    # export-otel
    p_otel = sub.add_parser("export-otel", help="export OTEL traces/metrics")
    p_otel.add_argument("--output", type=Path, default=None, help="output file")
    p_otel.add_argument("--no-traces", action="store_true", help="skip trace export")
    p_otel.add_argument("--no-metrics", action="store_true", help="skip metrics export")

    # contracts — adapter-contracts maintenance subcommands (B9b 3c).
    # Currently houses ``auto-eval``; future ``init``/``verify`` (Phase 4)
    # will register here. Output is structured JSON to stdout (or a
    # caller-chosen file via ``--out``); no ``.dark-factory/`` artifact
    # is written under any condition (Path B per neo's review — auto-
    # eval is a maintainer-only artifact with no per-project semantics,
    # see ``retire_after_runner.py`` module docstring for the rationale).
    p_contracts = sub.add_parser(
        "contracts", help="adapter-contracts maintenance",
    )
    contracts_sub = p_contracts.add_subparsers(dest="contracts_command")
    p_auto_eval = contracts_sub.add_parser(
        "auto-eval",
        help="evaluate whether a universal adapter helper is still needed "
             "by running a vanilla LLM prompt against its canonical "
             "violation fixture",
    )
    p_auto_eval.add_argument(
        "helper",
        help="helper name (e.g., assertUnsetGucIsNullish). Must be a "
             "universal helper registered in retire-after.toml.",
    )
    p_auto_eval.add_argument(
        "--model",
        default="claude-opus-4-7",
        help="model identifier passed to `claude --model` "
             "(default: claude-opus-4-7 — bump on each major model "
             "release; should match the current `retire_after` pin "
             "in retire-after.toml at evaluation time)",
    )
    p_auto_eval.add_argument(
        "--rounds", type=int, default=5,
        help="number of rounds to evaluate (default: 5). Each round "
             "is one independent claude subprocess invocation.",
    )
    p_auto_eval.add_argument(
        "--out", type=Path, default=None,
        help="output file path. If unset, JSON is written to stdout. "
             "MUST NOT be under any project's .dark-factory/ tree — "
             "auto-eval artifacts are maintainer-only and have no "
             "per-project semantics (see ROADMAP.md Phase 3 B9b).",
    )
    p_auto_eval.add_argument(
        "--timeout", type=int, default=120,
        help="per-round subprocess timeout in seconds (default: 120). "
             "Worst-case wall-clock = rounds * timeout.",
    )

    # contracts init (Phase 4 v0): copy framework starter templates
    # into <project>/.dark-factory/contracts/<adapter>/. Unblocks
    # ROADMAP B9a v(N+1) (framework-side deletion of convention
    # helpers — Phase 3 closure).
    p_contracts_init = contracts_sub.add_parser(
        "init",
        help="copy framework project-starter helpers into "
             "<project>/.dark-factory/contracts/<adapter>/",
    )
    p_contracts_init.add_argument(
        "adapter",
        help="adapter name (e.g., postgres). Must have a starter dir "
             "at templates/contracts/<adapter>/project-starters/.",
    )
    p_contracts_init.add_argument(
        "--force", action="store_true",
        help="overwrite files that already exist at the destination "
             "(default: skip existing files and report them).",
    )
    p_contracts_init.add_argument(
        "--dry-run", action="store_true",
        help="print the would-be copy plan without writing any files.",
    )

    # contracts verify (Phase 4 v1): walk project-side helpers and
    # apply the silent-wrong-helper guard registry. Catches helper-
    # source-regression-at-edit-time (developer removes a safety
    # guard while customizing a starter).
    p_contracts_verify = contracts_sub.add_parser(
        "verify",
        help="verify project-side adapter helpers haven't lost their "
             "silent-wrong-helper safety guards or re-introduced foot-"
             "gun shapes",
    )
    p_contracts_verify.add_argument(
        "adapter",
        help="adapter name (e.g., postgres). Must have silent-wrong "
             "registry entries at "
             "darkfactory.contracts.silent_wrong_registry.",
    )

    # events
    p_events = sub.add_parser("events", help="event management")
    events_sub = p_events.add_subparsers(dest="events_command")
    p_rotate = events_sub.add_parser("rotate", help="rotate events file")
    p_rotate.add_argument("--max-size", type=int, default=10 * 1024 * 1024)
    p_rotate.add_argument("--keep", type=int, default=3)
    events_sub.add_parser("migrate", help="migrate metrics to events")

    # hints — manage .dark-factory/project-hints.toml (Sprint 4 / B3)
    p_hints = sub.add_parser("hints", help="manage project-hints.toml policy")
    hints_sub = p_hints.add_subparsers(dest="hints_command")

    # hints list — pretty-print current file (raw, preserves comments)
    hints_sub.add_parser(
        "list", help="print .dark-factory/project-hints.toml (raw, comments preserved)",
    )

    # hints lint — validate the file against the loader schema
    hints_sub.add_parser(
        "lint", help="validate project-hints.toml schema and structure",
    )

    # hints add — append one [[policy.consistency_rules]] rule
    p_hints_add = hints_sub.add_parser(
        "add", help="append a [[policy.consistency_rules]] entry",
    )
    p_hints_add.add_argument("--name", required=True,
                              help="rule name (must be unique within the file)")
    p_hints_add.add_argument("--spec-pattern", action="append", default=[],
                              dest="spec_patterns", metavar="REGEX",
                              help="spec-side regex (repeatable)")
    p_hints_add.add_argument("--forbidden-env", action="append", default=[],
                              dest="forbidden_env", metavar="REGEX",
                              help="env-side regex that must NOT appear (repeatable)")
    p_hints_add.add_argument("--env-path", action="append", default=[],
                              dest="env_paths", metavar="DOTTED",
                              help="environment.toml dotted path to inspect "
                                   "(repeatable, at least one required)")
    p_hints_add.add_argument("--description", default="",
                              help="human-readable rationale")
    p_hints_add.add_argument("--force", action="store_true",
                              help="overwrite an existing rule with the same name "
                                   "(rewrites the file; does NOT preserve comments)")

    # hints stats — per-rule remediation success rates (C5)
    hints_sub.add_parser(
        "stats",
        help="per-rule REMEDIATION_OUTCOME success rates from "
             ".dark-factory/events.jsonl",
    )

    # hints init — interactive baseline [policy] setup
    p_hints_init = hints_sub.add_parser(
        "init", help="interactive baseline [policy] setup",
    )
    p_hints_init.add_argument("--non-interactive", action="store_true",
                               help="skip prompts, write defaults (empty policy)")
    p_hints_init.add_argument("--force", action="store_true",
                               help="overwrite an existing non-empty [policy] "
                                    "(rewrites the file; does NOT preserve comments)")

    # remediate — apply a REMEDIATION_TARGET from a past run (Sprint 5 / C2)
    p_rem = sub.add_parser(
        "remediate",
        help="apply a fix proposed by a failed pipeline run",
    )
    p_rem.add_argument("run_id", nargs="?", default="",
                       help="run_id emitted by the failing pipeline run "
                            "(see `To fix:` line in the failure output); "
                            "omit with --latest to pick the most-recent target")
    p_rem.add_argument("--latest", action="store_true",
                       help="use the most-recent REMEDIATION_TARGET across "
                            "all runs (conflicts with a positional run_id)")
    p_rem.add_argument("--target-index", type=int, default=0,
                       help="when a run emitted multiple REMEDIATION_TARGETs, "
                            "pick index N from the most-recent-first list "
                            "(default 0). Re-invoke with --target-index 1, 2, "
                            "... to work through the list.")
    p_rem.add_argument("--apply", action="store_true",
                       help="skip the y/n confirmation (CI-friendly); "
                            "still force-interactive on novel rules")
    p_rem.add_argument("--dry-run", dest="remediate_dry_run",
                       action="store_true",
                       help="show what would be applied, exit 0 without "
                            "side effects (overrides --apply)")
    p_rem.add_argument("--rerun", action="store_true",
                       help="after successful apply, invoke `dark-factory "
                            "run <spec>` in-process. Partial re-trigger: "
                            "auto-resumes from the failed step (e.g., "
                            "architect on a consistency violation) so "
                            "spec_validator isn't re-run unnecessarily. "
                            "Loop guard: aborts with exit 1 if the re-run "
                            "emits the same rule_or_check again.")
    p_rem.add_argument("--accept-novel", dest="accept_novel",
                       action="store_true",
                       help="DEGRADED RUN: bypass the first-occurrence "
                            "confirmation on a novel rule_or_check. "
                            "Emits a REMEDIATION_ACCEPTED_NOVEL audit "
                            "event. Only meaningful with --apply.")

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
        _print_next_command_hint(args, getattr(e, "run_id", ""))
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)


def _print_next_command_hint(args: argparse.Namespace, run_id: str) -> None:
    """If a DarkFactoryError carries a run_id and the pipeline emitted a
    REMEDIATION_TARGET for it, print a "To fix: ..." hint to stderr.

    The hint is advisory only — any lookup failure (missing events file,
    unreadable file, no matching target) is swallowed so that telemetry
    plumbing never masks the original error the user came to see. The
    error message and non-zero exit status remain authoritative.

    ``next_command`` is read from the target record (callers may override
    the default ``dark-factory remediate <run-id>``); the hint also
    surfaces the in-session ``/df-write-hints from-failure`` alternative
    so users inside a Claude Code session have a one-line path forward.
    """
    if not run_id:
        return
    try:
        project = Path(getattr(args, "project_root", ".")).resolve()
    except (OSError, RuntimeError) as exc:
        log.debug("hint lookup skipped (project_root resolve failed): %s", exc)
        return
    events_file = project / ".dark-factory" / "events.jsonl"
    if not events_file.exists():
        return
    # Local import: darkfactory.events.remediation pulls in the events
    # package, which transitively imports the full pipeline plumbing.
    # Keeping this lazy keeps `dark-factory --help` cheap.
    from darkfactory.events.remediation import find_targets_by_run_id
    try:
        targets = find_targets_by_run_id(events_file, run_id)
    except OSError as exc:
        log.debug("hint lookup skipped (events.jsonl unreadable): %s", exc)
        return
    if not targets:
        return
    target = targets[0]
    next_cmd = (
        target.get("data", {}).get("next_command")
        or f"dark-factory remediate {run_id}"
    )
    print("", file=sys.stderr)
    print(f"To fix: {next_cmd}", file=sys.stderr)
    print(
        f"   or:  /df-write-hints from-failure {run_id}"
        "   (in Claude Code)",
        file=sys.stderr,
    )


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

    # Phase 6 V-anchors: stage prior-run artifacts BEFORE run_pipeline so
    # the orchestrator's cache-hit / "already exist" skip paths see the
    # anchored files. Raises FileNotFoundError early on a bad source
    # path so a miswired flag fails before any pipeline state is mutated.
    from darkfactory.pipeline.anchors import AnchorPaths, apply_anchors
    anchors = AnchorPaths(
        manifest=args.anchor_manifest,
        scenarios=args.anchor_scenarios,
        environment=args.anchor_environment,
    )
    if anchors.any_set() and not resume:
        apply_anchors(anchors, project, spec, progress=print)

    events_file = project / ".dark-factory" / "events.jsonl"
    exit_code = run_pipeline(
        spec, config, project,
        resume=resume,
        fresh=args.fresh,
        regen_scenarios=args.regen_scenarios,
        skip_validation=args.skip_validation,
        dry_run=args.dry_run,
        events_file=events_file,
        progress=print,
        no_adapter=args.no_adapter,
        from_phase=args.from_phase,
    )
    if exit_code == 5:
        print(
            "STABLE_FAILURE — hard gates passed and the failing set was stable "
            "across iterations, but the Oracle declined to ship. This is a "
            "terminal non-ship (not a ship): inspect the defect registry under "
            ".dark-factory/reports/ for the documented gaps."
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


def _cmd_benchmark(args: argparse.Namespace, project: Path) -> None:
    from darkfactory.benchmark.battery import load_battery
    from darkfactory.benchmark.runner import run_battery
    from darkfactory.errors import ConfigError

    try:
        battery = load_battery(Path(args.battery))
    except ConfigError as e:
        print(f"battery error: {e}")
        sys.exit(2)
    out_path = Path(args.out) if args.out else Path.cwd() / "benchmark-scorecard.jsonl"
    # Honor BOTH `dark-factory benchmark <b> --dry-run` (bench_dry_run) and the
    # global `dark-factory --dry-run benchmark <b>` — for a destructive,
    # reset-oriented command, a typed --dry-run must never run for real.
    dry = args.bench_dry_run or getattr(args, "dry_run", False)
    rc = run_battery(battery, out_path=out_path, dry_run=dry, progress=print)
    sys.exit(rc)


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


def _cmd_hints(args: argparse.Namespace, project: Path) -> None:
    """Dispatch ``dark-factory hints {list,lint,add,init}`` subcommands.

    Each branch is short and independent — spawning one handler per
    subcommand would just add surface without isolating any real
    complexity. The dispatcher keeps the flow visible in one screen.
    """
    from darkfactory.architecture.project_hints import (
        ConsistencyRuleSpec,
        PolicySection,
        load_project_hints,
    )
    from darkfactory.architecture.project_hints_writer import (
        append_consistency_rule,
        init_policy,
        project_hints_path,
    )

    cmd = getattr(args, "hints_command", None)
    if cmd is None:
        # No subcommand supplied → usage error. Exit 2 matches argparse's
        # convention for "bad command line" (versus 1 for "ran but failed").
        print("Usage: dark-factory hints {list|lint|add|stats|init}")
        sys.exit(2)

    path = project_hints_path(project)

    if cmd == "list":
        # Raw-print so user-authored comments survive. ``load_project_hints``
        # would strip them via tomllib; list's contract is "show me what's
        # on disk", not "show me what the loader sees". Missing file is
        # a valid state — exit 0 with guidance, matching `lint`.
        if not path.exists():
            print(f"no project-hints.toml at {path}")
            print("run `dark-factory hints init` to create one")
            sys.exit(0)
        print(path.read_text(encoding="utf-8"), end="")
        sys.exit(0)

    if cmd == "lint":
        # `load_project_hints` performs schema validation; it will raise
        # ConfigError on structural problems and WARN on forward-compat
        # issues (unknown keys). Lint surfaces both.
        if not path.exists():
            print(f"no project-hints.toml at {path}")
            print("run `dark-factory hints init` to create one")
            sys.exit(0)
        try:
            hints = load_project_hints(project)
        except ConfigError as exc:
            print(f"FAIL  {path}")
            print(f"      {exc}")
            sys.exit(1)
        print(f"PASS  {path}")
        if hints.synthesized_from_legacy:
            print(
                "      warning: [detected] was synthesized from legacy "
                "hints.json. run `dark-factory init --migrate-hints` to "
                "consolidate.",
            )
        n_rules = len(hints.policy.consistency_rules)
        adapters = ", ".join(hints.detected.active_adapters) or "-"
        print(
            f"      [detected] language={hints.detected.language or '-'} "
            f"adapters={adapters}"
        )
        print(
            f"      [policy] consistency_rules={n_rules} "
            f"workspace_scope={hints.policy.workspace.scope or '-'} "
            f"migrations_mode={hints.policy.migrations.mode or '-'}"
        )
        sys.exit(0)

    if cmd == "add":
        # CLI-layer arg validation: reject silently-inert and malformed-
        # argument cases before touching disk. Exit 2 for all arg errors
        # (matches argparse convention and is consistent across sibling
        # subcommands).
        if not args.name:
            _arg_error("--name must be non-empty")
        if not args.env_paths:
            _arg_error(
                "--env-path is required (at least one). A rule that "
                "inspects no env paths is silently inert — the loader "
                "rejects it at compile time.",
            )
        for flag, values in (
            ("--spec-pattern", args.spec_patterns),
            ("--forbidden-env", args.forbidden_env),
            ("--env-path", args.env_paths),
        ):
            if any(not v for v in values):
                _arg_error(f"{flag} values must be non-empty strings")
        rule = ConsistencyRuleSpec(
            name=args.name,
            spec_patterns=tuple(args.spec_patterns),
            forbidden_env=tuple(args.forbidden_env),
            env_paths=tuple(args.env_paths),
            description=args.description,
        )
        try:
            append_consistency_rule(project, rule, force=args.force)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"added rule {rule.name!r} → {project_hints_path(project)}")
        sys.exit(0)

    if cmd == "stats":
        # C5: aggregate REMEDIATION_OUTCOME events per rule_or_check and
        # print an evidence table. Graduation to Level 4 policy (future)
        # will be gated on the numbers this surfaces — ≥10 outcomes,
        # ≥80% fixed — so the display prioritizes those two numbers over
        # per-outcome breakdown. Ordered by total-desc: rules with the
        # most evidence sit at the top, consistent with "most trust-
        # worthy graduation candidates first."
        from darkfactory.events.remediation import aggregate_outcome_stats
        events_file = project / ".dark-factory" / "events.jsonl"
        try:
            stats = aggregate_outcome_stats(events_file)
        except OSError as exc:
            # Distinguish unreadable events file from the benign empty-
            # file case — aggregate_outcome_stats returns {} for both
            # missing and empty, but _read_records_tolerant may raise
            # on permission errors. Surface to the user.
            print(f"error: cannot read {events_file}: {exc}", file=sys.stderr)
            sys.exit(1)
        if not stats:
            print(f"no remediation outcomes recorded in {events_file}")
            print("  (empty events.jsonl or no REMEDIATION_OUTCOME "
                  "events yet — try a few rerun cycles)")
            sys.exit(0)

        # Column widths: rule names can be long; total/fixed are small.
        rule_width = max(len(r) for r in stats.keys())
        rule_width = max(rule_width, len("rule"))
        print(f"{'rule'.ljust(rule_width)}  total  fixed   %fixed  "
              "(same / different / other)")
        print(f"{'-' * rule_width}  -----  -----  -------  "
              "-------------------------")
        for rule, counts in sorted(
            stats.items(), key=lambda kv: (-kv[1]["total"], kv[0]),
        ):
            total = counts.get("total", 0)
            fixed = counts.get("fixed", 0)
            # One-decimal-place float: integer truncation hid near-
            # threshold rules (79.7% displaying as 79% looked further
            # from the 80% graduation line than it actually was).
            pct = (fixed * 100.0 / total) if total else 0.0
            same = counts.get("same_failure_again", 0)
            diff = counts.get("different_failure", 0)
            other = total - fixed - same - diff
            print(
                f"{rule.ljust(rule_width)}  {total:>5}  {fixed:>5}  "
                f"{pct:>5.1f}%  ({same} / {diff} / {other})"
            )
        sys.exit(0)

    if cmd == "init":
        if args.non_interactive or not sys.stdin.isatty():
            policy = PolicySection()
        else:
            policy = _prompt_baseline_policy()
        try:
            init_policy(project, policy, force=args.force)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"wrote baseline policy → {project_hints_path(project)}")
        if policy == PolicySection():
            print(
                "  (empty policy — edit the file or run "
                "`dark-factory hints add` to extend it)"
            )
        sys.exit(0)

    # argparse with a restricted ``choices``-set makes unreachable paths
    # impossible here — but add_subparsers doesn't enforce choices the
    # same way, so we keep a defensive fallthrough rather than trust
    # dispatch plumbing. Any hit here is a bug in the registration code
    # above (new subparser added without a handler branch).
    raise AssertionError(f"unhandled hints subcommand: {cmd!r}")


def _arg_error(message: str) -> None:
    """Exit 2 with a ``error: ...`` message on stderr.

    Shared by the `hints` subcommands so all CLI-layer arg-validation
    errors produce identical framing. Exit 2 matches argparse's own
    convention for command-line errors.
    """
    print(f"error: {message}", file=sys.stderr)
    sys.exit(2)


def _cmd_remediate(args: argparse.Namespace, project: Path) -> None:
    """Sprint 5 / C2: apply a REMEDIATION_TARGET from a prior run.

    Dispatches by ``data.failure_class``:

    * ``consistency_violation`` — propose the TOML block, confirm
      (unless ``--apply`` and non-novel), apply via
      :func:`append_consistency_rule`, optionally ``--rerun``. Today's
      orchestrator emits ``auto_applicable=False`` and a shape-only
      ``suggested_diff`` for this class (see ``orchestrator.py`` line
      ~1654 TODO); when that TODO is fulfilled and the event carries
      the full rule payload, this branch applies out of the box.
    * ``spec_quality`` / ``precheck_finding`` / ``stub_compile_error``
      / ``proposal_typecheck_failure`` — Guided/Manual classes, no
      auto-write. Surface the rationale, path, and suggested_diff,
      then point at the right follow-up tool.

    Novel-rule guard (C5): when ``data.novel_rule_name`` is true, the
    CLI forces an interactive confirmation *even with* ``--apply``.
    ``--accept-novel`` bypasses this guard in non-interactive contexts
    (CI with explicit operator intent) and emits a
    ``REMEDIATION_ACCEPTED_NOVEL`` audit event so reviewers can
    reconstruct which novel rules entered the project without human y/n.
    """
    from darkfactory.events.remediation import (
        find_latest_target,
        find_targets_by_run_id,
    )

    # ----- arg validation -----
    if args.run_id and args.latest:
        _arg_error("pass either a run_id positional or --latest, not both")
    if not args.run_id and not args.latest:
        _arg_error("provide a run_id (or pass --latest)")
    if args.remediate_dry_run and args.rerun:
        # --rerun after a dry-run is incoherent (the dry-run exits 0
        # before writing anything, so the rerun has nothing to verify
        # fixed). Reject at the arg layer rather than silently skipping
        # the rerun — a user reading the --help for both flags together
        # deserves to know they conflict.
        _arg_error("--dry-run and --rerun cannot be combined")
    if args.accept_novel and not args.apply:
        # --accept-novel is an override for the novel-rule guard, which
        # only fires under --apply. Passing it without --apply is a
        # silent-inert combination — reject here so the check covers
        # every failure class (Guided/Manual targets don't route through
        # the consistency branch where this used to live).
        _arg_error("--accept-novel has no effect without --apply")

    events_file = project / ".dark-factory" / "events.jsonl"

    # ----- resolve target -----
    # The finders raise OSError on unreadable files so we can
    # distinguish "no targets" from "can't read events.jsonl". Without
    # this branch, a permission error would print "no REMEDIATION_TARGET
    # event for run_id ..." and send the user chasing the wrong problem.
    if args.latest:
        try:
            target = find_latest_target(events_file)
        except OSError as exc:
            print(f"error: cannot read {events_file}: {exc}", file=sys.stderr)
            sys.exit(1)
        if target is None:
            print(
                "error: no REMEDIATION_TARGET events in "
                f"{events_file} — nothing to remediate",
                file=sys.stderr,
            )
            sys.exit(1)
        run_id = target.get("run_id", "") or "?"
    else:
        try:
            targets = find_targets_by_run_id(events_file, args.run_id)
        except OSError as exc:
            print(f"error: cannot read {events_file}: {exc}", file=sys.stderr)
            sys.exit(1)
        if not targets:
            print(
                f"error: no REMEDIATION_TARGET event for run_id "
                f"{args.run_id!r} in {events_file}. Check the run_id "
                f"against the failure output, or pass --latest.",
                file=sys.stderr,
            )
            sys.exit(1)
        idx = args.target_index
        if idx < 0 or idx >= len(targets):
            print(
                f"error: --target-index {idx} out of range; "
                f"run {args.run_id!r} emitted {len(targets)} targets "
                f"(valid range: 0..{len(targets) - 1})",
                file=sys.stderr,
            )
            sys.exit(2)
        if len(targets) > 1:
            # Multiple violations in one run — list them so the user can
            # re-invoke with --target-index N to work through them.
            # Pairing at run_id level (remediation_outcome) is collective,
            # not per-target, so we can't auto-filter "already applied"
            # ones; explicit indexing is the honest UX.
            print(
                f"note: run {args.run_id!r} emitted {len(targets)} "
                f"REMEDIATION_TARGET events (most-recent first):",
            )
            for i, t in enumerate(targets):
                d = t.get("data") or {}
                marker = " <-- selected" if i == idx else ""
                print(
                    f"  [{i}] failure_class={d.get('failure_class', '?')} "
                    f"rule_or_check={d.get('rule_or_check', '?')}{marker}",
                )
            print(
                "  re-invoke with --target-index N to pick a different one."
            )
            print()
        target = targets[idx]
        run_id = args.run_id

    # ----- print summary -----
    data = target.get("data") or {}
    failure_class = data.get("failure_class", "")
    spec = target.get("spec", "")
    rationale = data.get("rationale", "") or ""
    rationale_first = rationale.splitlines()[0] if rationale else "(none)"

    print(f"remediation target from run {run_id!r}")
    print(f"  spec:            {spec!r}")
    print(f"  failure_class:   {failure_class}")
    print(f"  rule_or_check:   {data.get('rule_or_check', '?')}")
    print(f"  remediation_type:{data.get('remediation_type', '?')}")
    print(f"  target_path:     {data.get('remediation_target_path', '?')}")
    print(f"  rationale:       {rationale_first}")
    print(f"  novel_rule:      {data.get('novel_rule_name', False)}")
    print()

    # ----- dispatch by failure_class -----
    GUIDED_OR_MANUAL = {
        "spec_quality",
        "precheck_finding",
        "stub_compile_error",
        "proposal_typecheck_failure",
    }
    if failure_class in GUIDED_OR_MANUAL:
        _print_manual_guidance(target)
        sys.exit(0)

    if failure_class != "consistency_violation":
        # Unknown class — future-proofing: the event schema is versioned
        # (SCHEMA_VERSION=1 in remediation.py) but consumers should
        # degrade gracefully rather than crash on forward-compat values.
        print(
            f"error: unrecognized failure_class {failure_class!r}. This "
            f"may be a forward-incompatible event schema; see "
            f"darkfactory/events/remediation.py SCHEMA_VERSION. Showing "
            f"the raw event below for manual inspection:",
            file=sys.stderr,
        )
        print(target, file=sys.stderr)
        sys.exit(1)

    _remediate_consistency(target, args, project, spec, run_id)


def _print_manual_guidance(target: dict) -> None:
    """Print a structured remediation prompt for Guided/Manual classes.

    No auto-write is permitted for these classes (plan §C5). The user
    gets the rationale, the target path, and the `suggested_diff`
    (if populated), along with the right follow-up tool to invoke.
    """
    data = target.get("data") or {}
    failure_class = data.get("failure_class", "")
    rationale = data.get("rationale", "") or ""
    path = data.get("remediation_target_path", "") or ""
    suggested = data.get("suggested_diff", "") or ""
    spec = target.get("spec", "")

    print("Remediation class: MANUAL / GUIDED (no auto-apply)")
    if rationale:
        print("\nRationale:")
        for line in rationale.splitlines():
            print(f"  {line}")
    if path:
        print(f"\nTarget file: {path}")
    if suggested:
        print("\nSuggested change:")
        for line in suggested.splitlines():
            print(f"  {line}")

    print("\nNext steps:")
    if failure_class == "spec_quality":
        print(f"  /df-write-feedback {spec} <iteration>   "
              "(Claude Code; barrier-safe feedback)")
        print(f"  or edit .dark-factory/specs/{spec}.md directly, "
              "then re-run `dark-factory run <spec>`")
    elif failure_class == "precheck_finding":
        print("  apply the suggested change manually, then re-run "
              f"`dark-factory run {spec}`")
    else:  # stub_compile_error / proposal_typecheck_failure
        print(f"  fix the referenced code at {path or '<target_path>'}, "
              f"then re-run `dark-factory run {spec}`")


def _remediate_consistency(
    target: dict,
    args: argparse.Namespace,
    project: Path,
    spec: str,
    run_id: str,
) -> None:
    """Consistency-violation branch of _cmd_remediate.

    Auto-apply requires the event's `suggested_diff` to parse as a
    complete `[[policy.consistency_rules]]` entry — not just
    name+description. If the payload is incomplete (today's state; see
    `_consistency_suggested_diff` in orchestrator.py), we surface the
    limitation and route to `/df-write-hints from-failure`.
    """
    from darkfactory.architecture.project_hints import (
        ConsistencyRuleSpec,
    )
    from darkfactory.architecture.project_hints_writer import (
        append_consistency_rule,
        project_hints_path,
    )
    from darkfactory.events.remediation import (
        emit_remediation_accepted_novel,
    )

    events_file = project / ".dark-factory" / "events.jsonl"

    data = target.get("data") or {}
    rule_name = data.get("rule_or_check", "") or ""
    suggested_diff = data.get("suggested_diff", "") or ""
    auto_applicable = bool(data.get("auto_applicable"))
    novel = bool(data.get("novel_rule_name"))

    if not rule_name:
        print(
            "error: event has no rule_or_check; cannot construct a "
            "consistency-rule entry. Raw event:",
            file=sys.stderr,
        )
        print(target, file=sys.stderr)
        sys.exit(1)

    # Parse the suggested_diff into one of four states; branch on each.
    # Today's orchestrator-emitted diff is empty-or-shape-only; the
    # "ok" and "malformed" branches are forward-looking.
    parse_status, parse_result = _rule_spec_from_suggested_diff(
        suggested_diff, rule_name,
    )

    # Render the proposal (always, regardless of next step)
    print("Proposed change to .dark-factory/project-hints.toml:")
    if suggested_diff:
        for line in suggested_diff.splitlines():
            print(f"  {line}")
    else:
        print(f"  [[policy.consistency_rules]]")
        print(f'  name = "{rule_name}"')
    print()

    if parse_status == "malformed":
        # The event's suggested_diff was emitted but can't be parsed.
        # This is an *emitter bug*, not a shape-only payload — do not
        # route to the hints skill (which would fail at env_paths
        # collection because the emitter is wrong upstream). Surface
        # the reason so a future debugger can find the bad emission.
        print(
            "error: suggested_diff is present but malformed — this "
            f"suggests an emitter bug upstream.\n  reason: {parse_result}",
            file=sys.stderr,
        )
        print(
            "  fix the emitter (see orchestrator.py "
            "`_consistency_suggested_diff`), or edit "
            ".dark-factory/project-hints.toml by hand.",
            file=sys.stderr,
        )
        sys.exit(1)

    if parse_status in ("empty", "shape_only"):
        # Shape-only payload — today's consistency_violation case.
        print(
            "This event's suggested_diff is SHAPE-ONLY: it names the rule "
            "but does not carry `spec_patterns`, `forbidden_env`, or "
            "`env_paths`. ConsistencyError does not expose the source "
            "rule's regex payload (see the TODO at orchestrator.py "
            "`_consistency_suggested_diff`), so `remediate` cannot "
            "auto-write a working rule from this event alone."
        )
        print()
        print("For an interactive walkthrough that collects the missing")
        print("fields and applies the rule:")
        print()
        print(f"  /df-write-hints from-failure {run_id}  "
              "(run in Claude Code)")
        print()
        print(f"Or edit .dark-factory/project-hints.toml by hand, then "
              f"re-run `dark-factory run {spec}`.")
        sys.exit(0)

    # parse_status == "ok" — the writable ConsistencyRuleSpec.
    assert parse_status == "ok"
    rule_spec = parse_result  # ConsistencyRuleSpec

    # ----- complete payload: auto-apply path (not reached today) -----

    if args.remediate_dry_run:
        print("--dry-run: would write the above rule; exit 0 without side "
              "effects")
        sys.exit(0)

    # Novel-rule guard (plan §C5): force interactive even with --apply
    # unless the operator passed --accept-novel to explicitly acknowledge
    # the degraded-run tradeoff.
    force_interactive = novel and not args.accept_novel

    if not auto_applicable:
        # A payload we can parse but the emitter has declared not
        # auto-applicable. Refuse to write; the emitter knows something
        # we don't.
        print(
            "this target has auto_applicable=false; the emitting gate "
            "declined to mark it machine-safe. Refusing to auto-write. "
            f"Edit .dark-factory/project-hints.toml by hand, or invoke "
            f"`/df-write-hints from-failure {run_id}` for guidance."
        )
        sys.exit(0)

    # --accept-novel without --apply is rejected at the top-level arg
    # check in _cmd_remediate (covers every failure class). Here we only
    # handle the novelty-specific warning case.
    if args.accept_novel and not novel:
        # Not novel — the guard wouldn't have fired anyway. Warn but
        # proceed; erroring here would break "belt-and-braces" CI
        # invocations that always pass --accept-novel defensively.
        log.warning(
            "--accept-novel passed but %r is not novel; flag has no "
            "effect on this invocation.",
            rule_name,
        )

    if not args.apply or force_interactive:
        if not sys.stdin.isatty():
            # Split the message by cause — the two conditions lead here
            # via different rules and the user's fix is different for
            # each.
            if novel and args.apply:
                print(
                    "error: NOVEL RULE blocks non-interactive apply. "
                    f"This is the first time {rule_name!r} has ever been "
                    "applied to this project; C5's policy requires "
                    "human confirmation on novel rules. Re-run this "
                    "from a terminal, or pass --accept-novel to proceed "
                    "with an audited degraded run.",
                    file=sys.stderr,
                )
            else:
                print(
                    "error: confirmation required (stdin is not a TTY). "
                    "Pass --apply or run this from a terminal.",
                    file=sys.stderr,
                )
            sys.exit(1)
        if novel:
            print(
                "NOVEL RULE: this rule_or_check has never been applied "
                "to this project before. C5's policy requires interactive "
                "confirmation even under --apply. (Pass --accept-novel "
                "to bypass in CI; use sparingly — the override is logged "
                "as a degraded run.)"
            )
        try:
            answer = input("Apply this rule? [y/N] ").strip().lower()
        except EOFError:
            print("error: no response on stdin", file=sys.stderr)
            sys.exit(1)
        if answer not in ("y", "yes"):
            print("cancelled (no changes made)")
            sys.exit(0)

    # ----- audit: --accept-novel override is a degraded run -----
    # Emit *before* the write so the audit trail still records operator
    # intent even if append_consistency_rule fails (lock contention,
    # disk full, schema conflict). The event timestamp plus the
    # subsequent REMEDIATION_OUTCOME lets reviewers reconstruct the full
    # sequence without inference.
    if novel and args.accept_novel and args.apply:
        try:
            emit_remediation_accepted_novel(
                events_file,
                spec=spec,
                run_id=run_id,
                rule_or_check=rule_name,
                remediation_type="add_project_hint",
                applied_by="ci" if not sys.stdin.isatty() else "human",
                reason="--accept-novel override on first-occurrence rule",
            )
            print(
                f"DEGRADED RUN: --accept-novel bypasses the novel-rule "
                f"guard on {rule_name!r}. Audit event emitted.",
                file=sys.stderr,
            )
        except OSError as exc:
            # Failing closed is the right policy: if we can't audit the
            # override, don't bypass the guard. Surface a clean error
            # instead of a stack trace so CI logs read cleanly.
            print(
                f"error: could not emit REMEDIATION_ACCEPTED_NOVEL "
                f"audit event ({exc}); refusing to bypass novel-rule "
                f"guard without a working audit trail.",
                file=sys.stderr,
            )
            sys.exit(1)

    # ----- apply -----
    try:
        append_consistency_rule(project, rule_spec, force=False)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"wrote rule {rule_spec.name!r} → {project_hints_path(project)}")

    # ----- optional --rerun (C4: partial-re-trigger + loop guard) -----
    if args.rerun:
        exit_code = _rerun_pipeline_after_remediate(
            args, project, spec,
            applied_rule=rule_spec.name,
        )
        sys.exit(exit_code)

    sys.exit(0)


def _rerun_pipeline_after_remediate(
    args: argparse.Namespace,
    project: Path,
    spec: str,
    *,
    applied_rule: str = "",
) -> int:
    """Invoke ``run_pipeline`` after a successful remediate-apply.

    Factored out of ``_cmd_remediate`` so the orchestrator entry point
    has one shape, not two. Diverges from ``_cmd_run`` in that it does
    NOT re-run dependency checks and does NOT honor
    ``--force``/``--regen-scenarios``/``--fresh``/``--resume`` (no such
    surface exists on ``remediate`` today). Shares profile resolution
    with ``_cmd_run`` so spec-specific overrides (e.g.
    ``max_iterations``) take effect on the rerun.

    **Partial re-trigger (C4).** ``run_pipeline`` auto-resumes from the
    last ``completed_step`` when a prior run left the state file in a
    resumable status. For a consistency violation that fires at the
    architect step (1.5), the state is ``completed_step=1`` and the
    rerun starts at architect — spec validator and precheck are not
    re-run. No explicit ``resume=True`` is needed; the default
    ``allow_resume=True`` + the resumable-status list in
    ``_try_auto_resume`` covers it. We deliberately do not pass
    ``fresh=True``: the whole point of the partial re-trigger is to
    skip upstream steps that were already green.

    **Loop guard (C4).** If the rerun emits a new ``REMEDIATION_TARGET``
    for the same ``rule_or_check`` we just applied, remediation did not
    resolve the underlying issue — chaining another remediate would
    just re-fire. Abort with a clean message and exit 1 so CI logs
    read cleanly. Detection is scoped to events appended *after* the
    rerun started (via a byte-offset baseline), so pre-existing targets
    from older runs don't false-positive.
    """
    from darkfactory.config.profiles import resolve_profile
    from darkfactory.pipeline.orchestrator import run_pipeline

    config = _load_config(args, project)
    spec_file = project / ".dark-factory" / "specs" / f"{spec}.md"
    if spec_file.exists():
        config = resolve_profile(spec_file, config)

    events_file = project / ".dark-factory" / "events.jsonl"

    # Baseline the events file size so the loop guard only inspects
    # events emitted during this rerun. Any same-rule target that
    # pre-exists (e.g., the original failure's own target) is by
    # definition below this offset.
    #
    # If stat() fails, we skip the guard entirely rather than false-
    # positive on a clean rerun by treating every pre-existing target
    # as "new." A false-positive "remediation didn't resolve" on a real
    # SHIP is the worst operator UX; silent-skip + a warning is honest.
    try:
        baseline_size = events_file.stat().st_size if events_file.exists() else 0
    except OSError as exc:
        log.warning(
            "loop-guard disabled: cannot stat %s (%s). A same-rule re-run "
            "failure will NOT be detected — investigate if this recurs.",
            events_file, exc,
        )
        baseline_size = None

    # Auto-resume side note for the next reader: when `run_pipeline`
    # picks up from a prior failed run (status="running" because
    # DarkFactoryError from architect doesn't flip status to
    # "interrupted"), it prints a "previous run status is 'running' —
    # may have crashed without interrupt handler" warning at
    # orchestrator.py:3678. That warning is expected here — the prior
    # run didn't crash, it failed a gate. Don't "fix" the warning
    # without understanding why C4 relies on this path.
    #
    # Also: `_handle_resume` (orchestrator.py:3714) restores the prior
    # run's run_id onto the new SpecRun, so a same-rule re-failure
    # emits its new REMEDIATION_TARGET with the OLD run_id. The loop
    # guard matches on `rule_or_check`, not run_id, so this is fine —
    # but don't refactor the guard to key on run_id without revisiting.
    print(f"\nre-running pipeline for spec {spec!r}...\n")
    try:
        exit_code = run_pipeline(
            spec, config, project,
            events_file=events_file,
            progress=print,
        )
    except DarkFactoryError:
        # `run_pipeline` only catches HumanHoldRequired / HardAbort /
        # BudgetExceeded (orchestrator.py:702). A consistency
        # violation on the rerun (the exact scenario plan §C4 is
        # guarding against) raises DarkFactoryError, which would
        # otherwise propagate past the loop guard entirely. Catch it
        # here, run the guard, and re-raise only if the guard did NOT
        # fire — that way the top-level main() handler still gets to
        # print the "To fix:" hint (C3) for unrelated DarkFactoryErrors.
        if _loop_guard_fired(events_file, applied_rule, baseline_size):
            _report_loop_guard_fired(applied_rule)
            return 1
        raise

    if exit_code != 0 and _loop_guard_fired(
        events_file, applied_rule, baseline_size,
    ):
        _report_loop_guard_fired(applied_rule)
        return 1

    return exit_code


def _loop_guard_fired(
    events_file: Path,
    applied_rule: str,
    baseline_size: int | None,
) -> bool:
    """Centralized "was this a same-rule re-failure?" predicate.

    Combines the three short-circuits (no rule provided, baseline
    unavailable, scan-bytes result) into a single call site so the
    two dispatch sites in ``_rerun_pipeline_after_remediate`` (the
    exception branch and the non-zero-exit branch) can't drift.
    """
    if not applied_rule or baseline_size is None:
        return False
    return _rerun_produced_same_failure(
        events_file, applied_rule, baseline_size,
    )


def _report_loop_guard_fired(applied_rule: str) -> None:
    """Print the operator-facing loop-guard message.

    Separate from ``_loop_guard_fired`` so the two dispatch sites share
    one formatter — a future wording change only touches this helper,
    not both call sites.
    """
    print(
        f"\nerror: remediation didn't resolve the failure — "
        f"rule_or_check {applied_rule!r} fired again on the re-run. "
        f"Manual intervention needed.",
        file=sys.stderr,
    )


def _rerun_produced_same_failure(
    events_file: Path,
    applied_rule: str,
    baseline_size: int,
) -> bool:
    """True iff a ``REMEDIATION_TARGET`` for ``applied_rule`` was appended
    to ``events_file`` after ``baseline_size`` bytes.

    Reading from the byte offset (rather than rescanning the whole file)
    scopes the check to events produced by the rerun. Compares on
    ``rule_or_check`` — the C1 schema (``darkfactory/events/remediation.py``)
    contract says this field is the stable project-unique identifier;
    cross-class collisions would be an upstream schema bug, not
    something this guard should defend against.

    OSError and JSON decode errors are swallowed (return False) — the
    loop guard is an advisory brake, not a correctness gate, and a
    transient FS error shouldn't block an otherwise-healthy rerun.

    File shrinkage between baseline capture and this read (log rotation,
    manual truncation) is treated as "can't tell" — return False and
    log a warning. False-positive on a clean rerun is worse than
    false-negative; no pipeline writer in-tree rotates events.jsonl, so
    hitting this branch is a sign something external touched the file.
    """
    from darkfactory.models.events import EventType

    if not events_file.exists():
        return False
    try:
        current_size = events_file.stat().st_size
    except OSError:
        return False
    if current_size < baseline_size:
        log.warning(
            "loop-guard disabled: %s shrank from %d to %d bytes during "
            "rerun — treating as indeterminate. A same-rule re-run "
            "failure will NOT be detected on this call.",
            events_file, baseline_size, current_size,
        )
        return False
    try:
        with events_file.open("rb") as fh:
            fh.seek(baseline_size)
            new_bytes = fh.read()
    except OSError:
        return False

    target_event_name = EventType.REMEDIATION_TARGET.value
    for line in new_bytes.decode("utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") != target_event_name:
            continue
        data = ev.get("data") or {}
        if data.get("rule_or_check") == applied_rule:
            return True
    return False


def _rule_spec_from_suggested_diff(
    suggested_diff: str, rule_name: str,
) -> "tuple[str, ConsistencyRuleSpec | str]":
    """Parse ``suggested_diff`` as TOML and extract a ConsistencyRuleSpec.

    Returns a discriminated tuple so the caller can produce a useful
    error message for each failure mode:

    * ``("ok", ConsistencyRuleSpec(...))`` — complete, writable rule.
    * ``("empty", "")`` — no diff at all; caller treats as shape-only.
    * ``("shape_only", reason)`` — diff parsed but missing required
      fields (no ``env_paths``); route to the hints skill.
    * ``("malformed", reason)`` — diff is present but unparseable /
      wrong shape / stale name. The **emitter** is wrong; the
      user-visible error should say so, not route to a shape-only
      walkthrough that will fail on the same missing fields.

    Collapsing all of these to ``None`` (the original design) sent
    emitter-bug debuggers to the wrong tool. The discrimination is
    cheap (one enum-tag instead of one None) and the caller's dispatch
    stays as a small switch.

    Local to this module rather than added to `project_hints_writer`
    because it's a one-way "untrusted-event → dataclass" adapter, not
    a writer concern.
    """
    import tomllib

    from darkfactory.architecture.project_hints import ConsistencyRuleSpec

    if not suggested_diff.strip():
        return ("empty", "")

    try:
        doc = tomllib.loads(suggested_diff)
    except tomllib.TOMLDecodeError as exc:
        return ("malformed", f"TOML parse error: {exc}")

    policy = doc.get("policy")
    if not isinstance(policy, dict):
        return ("malformed", "missing [policy] table")

    rules = policy.get("consistency_rules")
    if not isinstance(rules, list):
        return ("malformed", "policy.consistency_rules is not an array")

    for entry in rules:
        if not isinstance(entry, dict):
            continue
        if entry.get("name") != rule_name:
            continue
        env_paths = entry.get("env_paths") or []
        if not env_paths:
            # Shape-only: no env_paths means the rule is inert on
            # compile. Route to the hints skill walkthrough.
            return ("shape_only", "no env_paths in suggested_diff")
        try:
            spec = ConsistencyRuleSpec(
                name=rule_name,
                spec_patterns=tuple(entry.get("spec_patterns") or ()),
                forbidden_env=tuple(entry.get("forbidden_env") or ()),
                env_paths=tuple(env_paths),
                description=entry.get("description", "") or "",
            )
            return ("ok", spec)
        except (TypeError, ValueError) as exc:
            return ("malformed", f"cannot construct rule: {exc}")

    return ("malformed", f"no entry matches name={rule_name!r}")


def _prompt_baseline_policy() -> "PolicySection":  # noqa: F821 — local import
    """Walk the user through a small policy. Called only in TTY mode.

    Five questions (plan B3 target: "~5-10 baseline"). Each section is
    optional; the user can skip any of them and the writer will omit
    the empty sub-table from the serialized file.
    """
    from darkfactory.architecture.project_hints import (
        MigrationsPolicy,
        PolicySection,
        WorkspacePolicy,
    )

    print("\nbaseline policy setup — press Enter to skip any field.\n")

    # 1. Workspace scope (the monorepo case).
    scope = input(
        "  workspace scope (e.g. 'apps/api' for a pnpm monorepo; "
        "blank for single-package): "
    ).strip()
    # 2. Banned recursive flags (free-form; common ones documented in the
    #    typescript addendum — we accept comma-separated literals here).
    #    Only meaningful when a workspace scope is declared — we tell the
    #    user *why* we skipped the prompt rather than silently moving on.
    banned = ""
    if scope:
        banned = input(
            "  banned recursive command prefixes "
            "(comma-separated, e.g. 'pnpm -r,pnpm --recursive'; blank for none): "
        ).strip()
    else:
        print(
            "  (skipping banned_recursive — only meaningful when a "
            "workspace scope is set)"
        )
    banned_tuple = tuple(
        b.strip() for b in banned.split(",") if b.strip()
    ) if banned else ()
    workspace = WorkspacePolicy(scope=scope, banned_recursive=banned_tuple)

    # 3. Migration mode + command (only meaningful when a DB is involved).
    mig_mode = input(
        "  migrations mode (one of: cli-only, on-start, skip; blank for none): "
    ).strip()
    mig_cmd = ""
    if mig_mode and mig_mode != "skip":
        mig_cmd = input(
            "  migrations command (e.g. 'cd apps/api && pnpm drizzle:push'; "
            "blank if no CLI step): "
        ).strip()
    migrations = MigrationsPolicy(mode=mig_mode, command=mig_cmd)

    # 4/5. Consistency rules are authored either via `hints add` or the
    # B4 skill; we intentionally do NOT try to stuff them into the init
    # walkthrough (too many knobs for an interactive flow). We end with
    # a pointer to the next step.
    print(
        "\n  (consistency rules are added via `dark-factory hints add` "
        "or `/df-write-hints` — skipped here.)\n"
    )

    return PolicySection(workspace=workspace, migrations=migrations)


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

    result = run_precheck(
        spec_file,
        p4_mechanism_gate_enabled=config.p4_mechanism_token_gate_enabled,
    )
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
    precheck = run_precheck(
        spec_file,
        p4_mechanism_gate_enabled=config.p4_mechanism_token_gate_enabled,
    )
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


def _cmd_contracts(args: argparse.Namespace, project: Path) -> None:
    """Dispatch ``dark-factory contracts {auto-eval,init,verify}`` subcommands.

    ``auto-eval`` (Phase 3 B9b 3c): runs the retire_after auto-eval
    runner and emits an EvalResult to stdout / ``--out``.
    ``init`` (Phase 4 v0): copies framework starter helpers into the
    project's ``.dark-factory/contracts/<adapter>/``.
    ``verify`` (Phase 4 v1): walks project-side helpers and applies
    the silent-wrong-helper guard registry — catches helper-source-
    regression-at-edit-time.
    """
    if args.contracts_command == "auto-eval":
        _cmd_contracts_auto_eval(args, project)
    elif args.contracts_command == "init":
        _cmd_contracts_init(args, project)
    elif args.contracts_command == "verify":
        _cmd_contracts_verify(args, project)
    else:
        print(
            "Usage: dark-factory contracts {auto-eval,init,verify}",
            file=sys.stderr,
        )
        sys.exit(1)


def _cmd_contracts_init(args: argparse.Namespace, project: Path) -> None:
    """Copy framework starter helpers into the project's contracts
    dir. Mirrors ``_cmd_contracts_auto_eval``'s shape: catch
    ``ValueError`` from the helper, exit 1 with stderr diagnostic;
    on success, print a per-file summary with copy/skip counts.
    """
    from darkfactory.contracts.init import init_adapter_contracts

    try:
        result = init_adapter_contracts(
            adapter=args.adapter,
            project_dir=project,
            force=args.force,
            dry_run=args.dry_run,
        )
    except ValueError as exc:
        print(f"contracts init failed: {exc}", file=sys.stderr)
        sys.exit(1)

    new_verb = "Would copy" if args.dry_run else "Copied"
    overwrite_verb = "Would overwrite" if args.dry_run else "Overwrote"
    skip_verb = "Would skip" if args.dry_run else "Skipped"

    if result.files_copied:
        print(f"{new_verb} {len(result.files_copied)} new file(s) "
              f"to {result.dst_dir}:")
        for rel in result.files_copied:
            print(f"  + {rel}")
    if result.force_overwritten:
        # Foregrounded loud — the maintainer asked for ``--force`` and
        # may have edited some of these in-place; they MUST see what
        # got stomped (Linus Phase-4-v0 review).
        print(f"{overwrite_verb} {len(result.force_overwritten)} "
              "existing file(s) with framework starter content "
              "(--force):")
        for rel in result.force_overwritten:
            print(f"  ! {rel}")
    if result.skipped_existing:
        print(f"{skip_verb} {len(result.skipped_existing)} existing "
              "file(s) (use --force to overwrite):")
        for rel in result.skipped_existing:
            print(f"  ~ {rel}")
    if not (result.files_copied
            or result.force_overwritten
            or result.skipped_existing):
        print(f"No starter files to copy from {result.src_dir}.")

    # Point the developer at the project-starter README. The README
    # ships in the same dir as the helpers (proximity-discoverable
    # via `ls`), but proximity isn't enough — neo's Phase-4 follow-up
    # surface audit found zero CLI/skill surfaces explicitly point
    # at it. This pointer closes the "discover what to do after
    # init" journey gap (ε closed "discover init"). Skipped on dry-
    # run + when no files actually exist at the destination.
    if not args.dry_run and (result.files_copied or result.force_overwritten):
        readme_path = result.dst_dir / "README.md"
        if readme_path.is_file():
            print(
                f"\nNext: read {readme_path.relative_to(project)} for "
                "editing instructions before running the pipeline."
            )


def _cmd_contracts_verify(args: argparse.Namespace, project: Path) -> None:
    """Verify project-side adapter helpers against the silent-wrong
    registry. Exit 1 on violations; exit 0 (with summary) on clean.
    """
    from darkfactory.contracts.verify import verify_adapter_helpers

    try:
        result = verify_adapter_helpers(args.adapter, project)
    except ValueError as exc:
        print(f"contracts verify failed: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Verifying {result.adapter} helpers in {result.contracts_dir}")
    print(f"  Files scanned: {len(result.files_scanned)}")
    print(f"  Helpers checked: {len(result.helpers_checked)}")
    if result.helpers_skipped:
        print(
            f"  Helpers skipped (not present in project): "
            f"{len(result.helpers_skipped)}"
        )
        for name in result.helpers_skipped:
            print(f"    ~ {name}")

    if not result.violations:
        print("Verify PASSED — all helpers retain their guards, no "
              "foot-gun shapes detected.")
        return

    # Reasons are author-formatted as "headline\n\nbody" so the
    # split is bulletproof (Linus v1 review final-pass — initial
    # implementation used `.split(". ", 1)` on prose, which is
    # fragile against abbreviations and other non-terminal periods).
    # The registry author owns the structure; the CLI just renders.
    print(f"Verify FAILED — {len(result.violations)} violation(s):")
    for v in result.violations:
        parts = v.reason.split("\n\n", 1)
        headline = parts[0].strip()
        body = parts[1].strip() if len(parts) > 1 else ""
        if v.kind == "missing_guard":
            print(f"  ! {v.helper_or_file} ({v.where}): {headline}")
        else:  # foot_gun_in_helper
            print(
                f"  ! foot-gun {v.helper_or_file} in {v.where}: "
                f"{headline}"
            )
        if body:
            for line in body.split("\n"):
                print(f"    {line}")
    sys.exit(1)


def _cmd_contracts_auto_eval(args: argparse.Namespace, project: Path) -> None:
    """Run the auto-eval workflow for one universal adapter helper.

    Mirrors ``_cmd_export_otel``'s shape: emit ``EvalResult`` JSON to
    stdout by default; with ``--out PATH``, write the same JSON to
    the file and print a one-line summary. No ``.dark-factory/``
    artifact written under any condition (Path B verdict, B9b 3c).

    The runner raises ``ValueError`` for argument validation failures
    (unknown helper, ``rounds < 1``) and ``RuntimeError`` for round-
    level subprocess failures (non-zero exit, timeout, JSON-parse
    failure). Both surface here as exit-code 1 with a one-line stderr
    diagnostic.
    """
    from darkfactory.adapters.postgres.retire_after_runner import run_auto_eval

    try:
        result = run_auto_eval(
            helper_name=args.helper,
            model=args.model,
            rounds=args.rounds,
            timeout=args.timeout,
        )
    except (ValueError, RuntimeError) as exc:
        print(f"auto-eval failed: {exc}", file=sys.stderr)
        sys.exit(1)

    payload = json.dumps(dataclasses.asdict(result), indent=2, sort_keys=True)
    if args.out is not None:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(payload)
        print(f"Wrote eval-result to {args.out}")
    else:
        print(payload)


_COMMANDS.update({
    "init": _cmd_init,
    "specs": _cmd_specs,
    "new-spec": _cmd_new_spec,
    "run": _cmd_run,
    "plan": _cmd_plan,
    "plan-rollback": _cmd_plan_rollback,
    "dashboard": _cmd_dashboard,
    "benchmark": _cmd_benchmark,
    "export-otel": _cmd_export_otel,
    "events": _cmd_events,
    "hints": _cmd_hints,
    "remediate": _cmd_remediate,
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
    "contracts": _cmd_contracts,
})
