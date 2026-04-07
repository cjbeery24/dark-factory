"""Phase 6.3 — union smoke probe between specs.

After a plan spec ships, walk the prior shipped specs and ask two
questions for each:

1. **Is its environment contract still reachable?** We attempt
   ``fold_to_hash`` against the current delta log using that spec's
   ``env_hash_at_ship``. If the fold raises "not reached" that means a
   later delta removed (or mutated past) a surface the earlier spec
   depended on. The spec is **stranded**: its archived scenarios would
   run against a contract that no longer exists in the log. That's a
   human decision (revert? rewrite? accept the drift?), not an
   automatic pass/fail.

2. **Do its archived scenarios still pass?** If the env is reachable,
   run the archived scenarios via the normal ``run_scenarios`` path.
   A failure means spec N just silently broke spec N-k — halt.

This module does NOT materialize the historical environment.toml to
disk. The scenarios run against whatever environment.toml currently
exists; the fold_to_hash check is the compatibility proxy. That's a
deliberate simplification over the v2.1 plan language — actually
rewinding environment.toml per probe risks leaving the project in a
half-rewound state on crash, and the archived scenario commands for
budget-brain-v2-style projects don't consult environment.toml at test
time. If a future project relies on the materialized view at scenario
time, this module's contract is exactly the place to add a tmp-dir
materialization step.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.architecture.deltas import (
    EnvironmentConflict,
    TargetHashNotReached,
    fold_to_hash,
)
from darkfactory.models.config import PipelineConfig
from darkfactory.plan.state import load_shipped
from darkfactory.scenarios.runner import run_scenarios
from darkfactory.state.registry import scenario_dir

log = logging.getLogger(__name__)


@dataclass
class SmokeProbeResult:
    """Outcome of probing all prior shipped specs after one new ship."""
    stranded: list[str] = field(default_factory=list)   # env hash no longer reachable
    regressed: list[str] = field(default_factory=list)  # env OK, archived scenarios failed
    passed: list[str] = field(default_factory=list)     # probed and passed
    skipped: list[str] = field(default_factory=list)    # no archive dir / no hash on record

    @property
    def ok(self) -> bool:
        return not self.stranded and not self.regressed


def probe_prior_ships(
    project_dir: Path,
    config: PipelineConfig,
    *,
    exclude: str | None = None,
    timeout: int = 300,
) -> SmokeProbeResult:
    """Probe every prior shipped spec; stop on first failure.

    ``exclude`` should be the name of the spec whose ship just triggered
    the probe — we don't need to probe it against itself. Order of
    iteration is shipped.jsonl append order (chronological), so the
    earliest spec is probed first and the oldest failure surfaces
    fastest.
    """
    result = SmokeProbeResult()
    # strict=True: a corrupt shipped.jsonl row silently shrinking the probe
    # set is exactly the failure mode a safety probe exists to catch. Let
    # it raise — the runner converts the exception into a halt.
    rows = load_shipped(project_dir, strict=True)
    deltas_dir = project_dir / ".dark-factory" / "architecture" / "environment.deltas"

    for row in rows:
        if row.spec_id == exclude:
            continue
        # ShippedSpec.__post_init__ rejects empty env_hash_at_ship, so every
        # row here is guaranteed to have one — no need to skip-on-empty.

        # Step 1 — reachability check via time-travel fold.
        # TargetHashNotReached → stranded (human decision).
        # Any other EnvironmentConflict → corrupt delta log, reraise.
        try:
            fold_to_hash(deltas_dir, row.env_hash_at_ship)
        except TargetHashNotReached as exc:
            log.error(
                "probe: %s STRANDED — env_hash %s no longer reachable: %s",
                row.spec_id, row.env_hash_at_ship[:12], exc,
            )
            result.stranded.append(row.spec_id)
            return result  # hard stop
        except EnvironmentConflict:
            log.error("probe: delta log corrupt while probing %s", row.spec_id)
            raise

        # Step 2 — run archived scenarios. Registry owns the archive
        # path; we symlink it into the expected .dark-factory/scenarios
        # slot exactly the same way run_regression does.
        sdir = scenario_dir(project_dir, row.spec_id)
        if sdir is None:
            log.warning("probe: %s has no archived scenario dir; skipping", row.spec_id)
            result.skipped.append(row.spec_id)
            continue

        target = project_dir / ".dark-factory" / "scenarios" / row.spec_id
        created_symlink = False
        if target.is_symlink():
            # Pre-existing symlink (from a crashed probe or regression run).
            # Verify it resolves to the archive we intend to run; if it
            # points somewhere else, bail loudly rather than silently
            # probe against the wrong content.
            existing = target.resolve(strict=False)
            wanted = sdir.resolve(strict=False)
            if existing != wanted:
                raise RuntimeError(
                    f"probe: stale symlink {target} points at {existing}, "
                    f"expected {wanted} (refusing to probe against wrong archive)"
                )
        elif target.exists():
            # A real directory already lives at the expected slot. Could be
            # the current in-progress scenarios for row.spec_id, or something
            # stranger. Either way, we can't confidently run the archive
            # through it without clobbering live state. Fail loud.
            raise RuntimeError(
                f"probe: {target} already exists as a non-symlink; cannot "
                f"stage archive for {row.spec_id} without risking clobber"
            )
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(sdir)
            created_symlink = True

        try:
            run_result = run_scenarios(
                row.spec_id, config, project_dir, timeout=timeout,
            )
            if run_result.exit_code == 0:
                result.passed.append(row.spec_id)
                log.info("probe PASS: %s", row.spec_id)
            else:
                log.error(
                    "probe REGRESSION: %s failed (exit=%d)",
                    row.spec_id, run_result.exit_code,
                )
                result.regressed.append(row.spec_id)
                return result  # halt on first regression
        finally:
            if created_symlink and target.is_symlink():
                target.unlink()

    return result
