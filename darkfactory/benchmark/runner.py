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


def _framework_reset(target: Path) -> None:
    """Wipe per-run runtime state so ``--fresh`` truly starts clean.

    ``--fresh`` on the CLI ignores the state file, but stale scenario/report
    files on disk would still be picked up, and a leftover state dir invites an
    accidental resume. Clean both, plus orphan ``df-run-id`` containers/networks
    from any crashed prior run.
    """
    df = target / ".dark-factory"
    for d in _RUNTIME_DIRS:
        shutil.rmtree(df / d, ignore_errors=True)
    (df / "events.jsonl").unlink(missing_ok=True)
    # Orphan Docker cleanup — best-effort; never fail the run on this.
    for cmd in (
        "docker ps -a --filter label=df-run-id -q | xargs -r docker rm -f",
        "docker network ls --filter name=df- -q | xargs -r docker network rm",
    ):
        try:
            subprocess.run(cmd, shell=True, capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass


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

    def _record(outcome: str, exit_code: int, wall_s: float) -> None:
        metrics = parse_run_metrics(events_path)
        append_scorecard(out_path, ScorecardRow(
            spec=spec, stack=target.stack, target=str(target.path), run=run_idx,
            outcome=outcome, exit_code=exit_code,
            cost_usd=metrics.cost_usd, iterations=metrics.iterations,
            run_id=metrics.run_id, wall_s=round(wall_s, 1), ts=time.time(),
        ))

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

    # 2. Framework-owned reset (runtime + ship-state wipe + orphan cleanup).
    _framework_reset(target.path)

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
        _record("error", -1, wall_s)
        progress(
            f"  [{spec} #{run_idx}] error: TIMED OUT after {timeout_s}s "
            f"(killed; stderr → {sidecar})"
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
    _record(outcome.value, exit_code, wall_s)
    metrics = parse_run_metrics(events_path)
    cost = f"${metrics.cost_usd}" if metrics.cost_usd is not None else "n/a"
    progress(
        f"  [{spec} #{run_idx}] {outcome.value} (exit={exit_code}, "
        f"iters={metrics.iterations}, cost={cost}, {wall_s:.0f}s)"
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
