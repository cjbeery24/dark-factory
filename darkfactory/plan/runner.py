"""Phase 6 — sequential plan runner.

Thin outer loop that runs plan specs one at a time in topological order,
threading on-disk state (shipped.jsonl, environment.deltas/, archived
scenarios) between specs. Coexists with `execute_plan` in executor.py —
that module does concurrent dispatch; this module does strict serial
execution, which is what the Phase 6 plan calls for.

The loop is deliberately thin: each step's post-ship work (scenario
archival, shipped-row append, delta fold) already runs inside
``run_pipeline._handle_ship``. The runner's only job is to detect
shipment, halt on failure, and report.

Deferred to later commits in Phase 6:
- 6.3 union smoke probe between specs (needs fold_to_hash variant)
- 6.4 depends_on soft check on standalone `dark-factory run`
- 6.5 resume + rollback commands
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from darkfactory.models.config import PipelineConfig

FailureCategory = Literal[
    "",                   # no failure
    "pipeline_failed",    # run_pipeline exit != 0
    "silent_no_ship",     # run_pipeline exit == 0 but no new shipped row
    "probe_stranded",     # prior spec's env hash no longer reachable
    "probe_regressed",    # prior spec's archived scenarios now fail
]
from darkfactory.plan.parser import parse_plan
from darkfactory.plan.smoke_probe import SmokeProbeResult, probe_prior_ships
from darkfactory.plan.state import load_shipped
from darkfactory.plan.topo_sort import topological_sort

log = logging.getLogger(__name__)


@dataclass
class SequentialPlanResult:
    """Outcome of a sequential plan run."""
    shipped: list[str] = field(default_factory=list)         # all shipped (pre-existing + newly)
    newly_shipped: list[str] = field(default_factory=list)   # shipped by *this* run
    failed: str | None = None          # spec that halted the run, if any
    failed_reason: str = ""            # human-readable halt reason
    skipped: list[str] = field(default_factory=list)  # not reached due to halt
    failed_exit_code: int = 0          # exit code from the failing spec (when applicable)
    failure_category: FailureCategory = ""  # typed classification of the halt
    probe_results: list[tuple[str, SmokeProbeResult]] = field(default_factory=list)
    # ^^ (spec_name, probe_result) recorded after each successful ship


def run_sequential_plan(
    plan_name: str,
    config: PipelineConfig,
    project_dir: Path,
    *,
    resume: bool = False,
    events_file: Path | None = None,
    progress: Callable[[str], None] | None = None,
    smoke_probe: bool = True,
) -> SequentialPlanResult:
    """Run a plan's specs one-by-one, halting on the first failure.

    Each spec goes through ``run_pipeline`` in the normal way. On ship,
    ``_handle_ship`` archives scenarios, appends a shipped.jsonl row, and
    (via the architecture amend path) refolds the environment delta log.
    If a spec does not ship, the runner halts and the remaining specs are
    reported as skipped — Phase 6 is "halt on first failure", not
    "continue past failures".
    """
    from darkfactory.pipeline.orchestrator import run_pipeline  # local import to avoid cycles

    def _report(msg: str) -> None:
        log.info(msg)
        if progress is not None:
            progress(msg)

    plan_file = project_dir / ".dark-factory" / "plans" / f"{plan_name}.md"
    if not plan_file.exists():
        raise FileNotFoundError(f"plan file not found: {plan_file}")

    specs = parse_plan(plan_file, project_dir)
    ordered = topological_sort(specs)

    # Resume: skip specs already in shipped.jsonl.
    already_shipped: set[str] = set()
    if resume:
        already_shipped = {r.spec_id for r in load_shipped(project_dir, strict=False)}
        if already_shipped:
            _report(
                f"resume: skipping {len(already_shipped)} already-shipped spec(s)"
            )

    result = SequentialPlanResult()

    for i, spec in enumerate(ordered):
        if spec.name in already_shipped:
            result.shipped.append(spec.name)
            continue

        _report(f"[{i + 1}/{len(ordered)}] running spec: {spec.name}")

        # Snapshot shipped.jsonl BEFORE the call so the trust check below
        # diffs against pre-call state — otherwise a spec that was
        # "already" in the log (e.g. from a prior partial run) would look
        # successful even if this invocation wrote nothing.
        pre_rows = {r.spec_id for r in load_shipped(project_dir, strict=False)}

        exit_code = run_pipeline(
            spec.name,
            config,
            project_dir,
            resume=resume,
            events_file=events_file,
            progress=progress,
        )

        # Authoritative shipped check — we trust shipped.jsonl, not the
        # exit code alone. run_pipeline returns 0 on ship; shipped.jsonl
        # has the row appended by _handle_ship. If the row is missing
        # from the post-call diff, something went wrong even if exit==0.
        post_rows = {r.spec_id for r in load_shipped(project_dir, strict=False)}
        newly = post_rows - pre_rows
        if exit_code == 0 and spec.name in newly:
            _report(f"[{i + 1}/{len(ordered)}] shipped: {spec.name}")
            result.shipped.append(spec.name)
            result.newly_shipped.append(spec.name)

            # Phase 6.3 — union smoke probe. Check every *prior* shipped
            # spec for (a) env-contract reachability and (b) archived
            # scenario regression. Halt on stranded OR regressed.
            if smoke_probe:
                probe = probe_prior_ships(project_dir, config, exclude=spec.name)
                result.probe_results.append((spec.name, probe))
                if not probe.ok:
                    if probe.stranded:
                        reason = (
                            f"prior spec stranded after {spec.name} shipped: "
                            f"{', '.join(probe.stranded)}"
                        )
                        result.failure_category = "probe_stranded"
                    else:
                        reason = (
                            f"regression in prior spec(s) after {spec.name}: "
                            f"{', '.join(probe.regressed)}"
                        )
                        result.failure_category = "probe_regressed"
                    _report(f"[{i + 1}/{len(ordered)}] HALT (smoke probe): {reason}")
                    result.failed = spec.name
                    result.failed_reason = reason
                    # Exit code is intentionally 0 here — probe failures are a
                    # typed category, not a subprocess exit code. Downstream
                    # callers must branch on failure_category, not exit_code.
                    result.failed_exit_code = 0
                    result.skipped = [s.name for s in ordered[i + 1:]]
                    break
            continue

        # Halt — record the failed spec and bank the rest as skipped.
        if exit_code == 0:
            reason = "run_pipeline returned 0 but no shipped.jsonl row was written"
            result.failure_category = "silent_no_ship"
        else:
            reason = f"run_pipeline exited {exit_code}"
            result.failure_category = "pipeline_failed"
        _report(
            f"[{i + 1}/{len(ordered)}] HALT: {spec.name} did not ship "
            f"({reason}); deferring {len(ordered) - i - 1} remaining spec(s)"
        )
        result.failed = spec.name
        result.failed_reason = reason
        result.failed_exit_code = exit_code
        result.skipped = [s.name for s in ordered[i + 1:]]
        break

    return result
