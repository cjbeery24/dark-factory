"""Benchmark batch runner — impure orchestration over ``dark-factory run``.

Per run: battery source-reset → framework reset (wipe runtime + state +
events.jsonl, clean orphan containers) → ``dark-factory run <spec> --fresh``
(subprocess, real exit code) → classify outcome → parse metrics → append a
scorecard row.

Concurrency precondition: NEVER run two batteries against the same target
concurrently — both wipe/append the same events.jsonl (torn-line race).
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from darkfactory.benchmark.battery import Battery, BatteryTarget
from darkfactory.benchmark.scorecard import (
    ScorecardRow,
    append_scorecard,
    classify_outcome,
    parse_run_metrics,
    parse_run_usage,
)

log = logging.getLogger(__name__)

# Runtime + durable-ship state wiped before each run (framework reset).
# Preserves specs/, config.toml, archive/, contracts/, project-hints.toml.
# `plan/` (holds shipped.jsonl) and `shipped-scenarios/` MUST be wiped too:
# `dark-factory run` does a depends-on soft check against shipped.jsonl
# (cli.py:611), so a prior benchmark ship would otherwise leak into a later
# run as an already-shipped dependency — making outcomes depend on run order.
_RUNTIME_DIRS = (
    "scenarios", "reports", "reviews", "audit", "state",
    "failures", "scratch", "resolution-check", "holdouts",
    "plan", "shipped-scenarios",
)


def _framework_reset(target: Path) -> str | None:
    """Wipe per-run runtime state so ``--fresh`` truly starts clean.

    ``--fresh`` on the CLI ignores the state file, but stale scenario/report
    files on disk would still be picked up, and a leftover state dir invites an
    accidental resume. Clean those, reset the env-contract to the source pin, plus
    orphan ``df-run-id`` containers/networks from any crashed prior run.

    Returns an error string if the hermetic env-contract reset was ATTEMPTED
    (git target) but FAILED — the caller MUST treat that as a runner-level
    failure, because a silently-defeated reset re-contaminates the measurement
    (the exact failure mode this guards). Returns None on success or when the
    target is not a git repo (then the battery's ``reset_cmd`` owns hermeticity).
    """
    df = target / ".dark-factory"
    for d in _RUNTIME_DIRS:
        shutil.rmtree(df / d, ignore_errors=True)
    (df / "events.jsonl").unlink(missing_ok=True)
    # Hermetic env-contract reset (the env-contract analog of the source reset).
    # The battery resets project SOURCE to a git pin, but the env-contract under
    # .dark-factory/architecture/ accumulates ACROSS specs: each spec appends an
    # NNNN-<spec>.toml delta and fold_deltas re-applies the WHOLE chain at run
    # start. ``git reset --hard <pin>`` restores the *tracked* baseline contract
    # but never removes UNTRACKED files, so post-baseline deltas survive unless
    # the battery reset_cmd ALSO clears them. The canonical reset_cmd
    # (battery.py) does ``git clean -fd -e .dark-factory/specs``, but a reset_cmd
    # that over-excludes (e.g. ``-e .dark-factory``) or omits the clean leaves the
    # deltas in place, so a later spec silently inherits an earlier spec's
    # env-contract — e.g. database-foundation's ``setup = drizzle-kit migrate`` +
    # postgres service leaking into an in-memory CRUD spec, failing the gate's
    # Setup stage before any scenario runs (scenario_total=0 -> mislabeled PLATEAU
    # -> escalation). This framework-owned clean makes the env-contract reset
    # robust regardless of reset_cmd shape, and closes a hole no reset_cmd ``-fd``
    # can: ``-x`` is LOAD-BEARING — if a target gitignores the env-contract path,
    # a plain ``git clean -fd`` silently SKIPS the polluting (ignored-untracked)
    # deltas and inerts the reset; ``-x`` cleans ignored-untracked too.
    # ``git clean`` never removes TRACKED files even with ``-x``, so the committed
    # baseline (bootstrap delta + environment.toml + lock, already restored by
    # ``git reset --hard``) survives; the pathspec scopes the wipe to
    # architecture/ only. Benchmark-only: a real evolving project WANTS delta
    # persistence across features and never calls _framework_reset.
    err: str | None = None
    if (target / ".git").exists():  # git target → reset_cmd is git-based
        try:
            proc = subprocess.run(
                ["git", "clean", "-fdx", "--", ".dark-factory/architecture/"],
                cwd=str(target), capture_output=True, text=True, timeout=60,
            )
            if proc.returncode != 0:
                err = (
                    f"git clean failed (rc={proc.returncode}): "
                    f"{(proc.stderr or proc.stdout).strip()[:200]}"
                )
        except (OSError, subprocess.SubprocessError) as exc:
            err = f"git clean errored: {exc}"
    # Orphan Docker cleanup — best-effort; never fail the run on this.
    for cmd in (
        "docker ps -a --filter label=df-run-id -q | xargs -r docker rm -f",
        "docker network ls --filter name=df- -q | xargs -r docker network rm",
    ):
        try:
            subprocess.run(cmd, shell=True, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    return err


def _write_stderr_sidecar(out_path: Path, spec: str, run_idx: int, text: str) -> Path:
    """Persist a failed run's stderr next to the scorecard (don't silently drop it)."""
    sidecar = out_path.parent / f"{spec}-run{run_idx}-stderr.log"
    try:
        sidecar.parent.mkdir(parents=True, exist_ok=True)
        sidecar.write_text(text or "", encoding="utf-8")
    except OSError:
        pass
    return sidecar


def _run_one(
    target: BatteryTarget,
    spec: str,
    run_idx: int,
    out_path: Path,
    progress: Callable[[str], None],
    timeout_s: int | None,
) -> tuple[bool, str]:
    """Execute one (spec, run) and append a scorecard row.

    Returns (runner_ok, outcome_value). ``runner_ok`` is False only on a
    runner-level failure (e.g. the battery reset_cmd failed) — a non-zero
    pipeline exit, a crash, or a timeout is run DATA, not a runner failure.
    """
    events_path = target.path / ".dark-factory" / "events.jsonl"
    registry = (
        target.path / ".dark-factory" / "reports" / f"{spec}-defect-registry.md"
    )

    telemetry_path = target.path / ".dark-factory" / "reports" / "run-telemetry.jsonl"

    def _record(
        outcome: str, exit_code: int, wall_s: float,
    ) -> tuple[float | None, int | None, int, str | None, str | None]:
        metrics = parse_run_metrics(events_path)
        # REAL usage from run-telemetry.jsonl (present on ALL outcomes incl.
        # abort). cost_usd is the real-token, cache-aware, per-line cost — NOT
        # the cap-based COST_SUMMARY. Filter by run_id defensively.
        usage = parse_run_usage(telemetry_path, run_id=metrics.run_id)
        append_scorecard(out_path, ScorecardRow(
            spec=spec, stack=target.stack, target=str(target.path), run=run_idx,
            outcome=outcome, exit_code=exit_code,
            cost_usd=usage.cost_usd, iterations=metrics.iterations,
            run_id=metrics.run_id, wall_s=round(wall_s, 1), ts=time.time(),
            input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
            cache_read_tokens=usage.cache_read_tokens,
            cache_creation_tokens=usage.cache_creation_tokens,
            agent_invocations=usage.agent_invocations,
            local_invocations=usage.local_invocations, model_id=usage.model_id,
            last_stage=metrics.last_stage, abort_reason=metrics.abort_reason,
        ))
        return usage.cost_usd, metrics.iterations, usage.agent_invocations, \
            metrics.last_stage, metrics.abort_reason

    # 1. Battery-owned project SOURCE reset (shell — may contain && / pins).
    reset = subprocess.run(
        target.reset_cmd, shell=True, cwd=str(target.path),
        capture_output=True, text=True,
    )
    if reset.returncode != 0:
        progress(
            f"  [{spec} #{run_idx}] RUNNER ERROR: reset_cmd failed "
            f"(rc={reset.returncode}): {reset.stderr.strip()[:200]}"
        )
        append_scorecard(out_path, ScorecardRow(
            spec=spec, stack=target.stack, target=str(target.path), run=run_idx,
            outcome="error", exit_code=-1, cost_usd=None, iterations=None,
            run_id=None, wall_s=0.0, ts=time.time(),
        ))
        return False, "error"

    # 2. Framework-owned reset (runtime + ship-state wipe + env-contract +
    # orphan cleanup). A failed hermetic env-contract reset is a runner-level
    # failure: proceeding would silently re-contaminate the measurement.
    reset_err = _framework_reset(target.path)
    if reset_err is not None:
        progress(
            f"  [{spec} #{run_idx}] RUNNER ERROR: hermetic env-contract reset "
            f"failed: {reset_err}"
        )
        append_scorecard(out_path, ScorecardRow(
            spec=spec, stack=target.stack, target=str(target.path), run=run_idx,
            outcome="error", exit_code=-1, cost_usd=None, iterations=None,
            run_id=None, wall_s=0.0, ts=time.time(),
        ))
        return False, "error"

    # 3. Run the pipeline from clean state. --fresh defeats any auto-resume.
    start = time.monotonic()
    try:
        proc = subprocess.run(
            ["dark-factory", "run", spec, "--fresh"],
            cwd=str(target.path), capture_output=True, text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        wall_s = time.monotonic() - start
        sidecar = _write_stderr_sidecar(
            out_path, spec, run_idx,
            (exc.stderr or b"").decode(errors="replace") if isinstance(exc.stderr, bytes)
            else (exc.stderr or ""),
        )
        cost, _iters, invocations, stage, _reason = _record("error", -1, wall_s)
        cost_str = f"${cost:.4f}" if cost is not None else "n/a"
        stage_str = f", stage={stage}" if stage else ""
        progress(
            f"  [{spec} #{run_idx}] error: TIMED OUT after {timeout_s}s "
            f"(killed; agents={invocations}, cost={cost_str}{stage_str}; "
            f"stderr → {sidecar})"
        )
        return True, "error"  # a timeout is run data, not a runner failure
    wall_s = time.monotonic() - start
    exit_code = proc.returncode

    # 4. Classify + record. On an unexpected ERROR exit, persist stderr.
    outcome = classify_outcome(exit_code, registry.exists())
    if outcome.value == "error":
        sidecar = _write_stderr_sidecar(out_path, spec, run_idx, proc.stderr or "")
        progress(
            f"  [{spec} #{run_idx}] error (exit={exit_code}; stderr → {sidecar})"
        )
    cost, iters, invocations, stage, reason = _record(outcome.value, exit_code, wall_s)
    cost_str = f"${cost:.4f}" if cost is not None else "n/a"
    # Surface WHERE it died + WHY (concise) inline; full detail in the archive.
    stage_str = f", stage={stage}" if stage else ""
    reason_str = f"\n      ↳ {reason}" if reason else ""
    progress(
        f"  [{spec} #{run_idx}] {outcome.value} (exit={exit_code}, "
        f"iters={iters}, agents={invocations}, cost={cost_str}{stage_str}, "
        f"{wall_s:.0f}s){reason_str}"
    )
    return True, outcome.value


def run_battery(
    battery: Battery,
    *,
    out_path: Path,
    dry_run: bool = False,
    progress: Callable[[str], None] = print,
) -> int:
    """Run a battery, emitting one scorecard row per run to ``out_path`` (JSONL).

    Returns 0 if every planned run executed (regardless of pipeline outcomes),
    non-zero if any run hit a runner-level failure (e.g. reset_cmd failed).
    """
    total = sum(len(t.specs) * battery.runs for t in battery.targets)
    progress(
        f"Benchmark: {len(battery.targets)} target(s), {total} run(s) "
        f"({battery.runs}x per spec) -> {out_path}"
    )

    if dry_run:
        for t in battery.targets:
            warn = "  [enterprise enabled — exit 1/2 ambiguous]" if t.enterprise_warning else ""
            progress(f"- {t.stack} @ {t.path}{warn}")
            for spec in t.specs:
                progress(f"    {spec}  x{battery.runs}")
        progress("dry-run: nothing executed")
        return 0

    runner_failures = 0
    for t in battery.targets:
        if t.enterprise_warning:
            progress(
                f"WARNING: {t.path} enables enterprise — exit codes 1/2 are "
                f"ambiguous (rollback/regression overload); FAILED/ESCALATED "
                f"rows for this target may be mislabeled."
            )
        if t.setup_cmd:
            progress(f"setup ({t.stack} @ {t.path}): {t.setup_cmd}")
            setup = subprocess.run(
                t.setup_cmd, shell=True, cwd=str(t.path),
                capture_output=True, text=True,
            )
            if setup.returncode != 0:
                progress(
                    f"  RUNNER ERROR: setup_cmd failed (rc={setup.returncode}) "
                    f"— skipping target: {setup.stderr.strip()[:200]}"
                )
                runner_failures += 1
                continue
        for spec in t.specs:
            for run_idx in range(1, battery.runs + 1):
                ok, _ = _run_one(
                    t, spec, run_idx, out_path, progress, battery.run_timeout_s,
                )
                if not ok:
                    runner_failures += 1

    if runner_failures:
        progress(f"DONE with {runner_failures} runner-level failure(s).")
        return 1
    progress("DONE — all planned runs executed.")
    return 0
