"""Compute which spec(s) are ready to run through the pipeline next.

Answers "what should I run next?" from **durable, rollback-aware** ship
state (``.dark-factory/plan/shipped.jsonl`` via :func:`load_shipped`) plus
the plan dependency graph — NOT the ephemeral per-spec ``state/*.state``
files, which are git-ignored, wiped on reset, and miss pre-reset ships
(the bug that made the interactive picker recommend an already-shipped
spec as "next").

A spec is **runnable** when it is unshipped and *every* resolved
dependency is shipped. Remaining unshipped specs are **blocked**, tagged
with the unshipped dependencies they wait on. ``parse_plan`` resolves
dep IDs (``F-2a``) to spec names, which equal ``shipped.jsonl`` spec ids,
so the comparison is a direct set membership.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path


log = logging.getLogger(__name__)

_SHIPPED_STATUSES = frozenset({"shipped", "deployed"})

_DATE_PREFIX_RE = re.compile(r"^\d{4}-\d{2}-\d{2}-")


def _strip_date(name: str) -> str:
    """Reduce a spec id to its date-stripped slug.

    Spec stems / ``shipped.jsonl`` ids carry a date prefix
    (``2026-03-28-monorepo-tooling``), but plan-resolved dependency names
    are bare slugs (``monorepo-tooling``). Comparing the two requires
    normalizing to the slug.
    """
    return _DATE_PREFIX_RE.sub("", name)


@dataclass
class NextSpecs:
    """Result of :func:`compute_next`.

    - ``runnable``: unshipped specs whose deps are all shipped, in plan
      (topological) order. The first entry is the recommended "next".
    - ``blocked``: unshipped specs that still wait on unshipped deps,
      mapped to the list of unshipped dependency names.
    - ``shipped_count``: how many of the input specs are already shipped.
    """

    runnable: list[str] = field(default_factory=list)
    blocked: dict[str, list[str]] = field(default_factory=dict)
    shipped_count: int = 0


def _shipped_ids(project_dir: Path) -> set[str]:
    """Durable, rollback-aware set of shipped spec ids (== spec names).

    ``load_shipped`` is the single reader of "what is currently shipped":
    it filters rolled-back specs by default, so a spec rolled back via
    ``rollback_plan_to`` correctly drops out of this set.
    """
    from darkfactory.plan.state import load_shipped

    try:
        return {r.spec_id for r in load_shipped(project_dir, strict=False)}
    except Exception:
        # A CORRUPT durable ledger makes every spec look unshipped — the picker
        # could then recommend re-running already-shipped specs, the exact bug
        # this module fixes. Degrade so the TUI stays usable, but make it LOUD.
        log.warning(
            "could not read shipped ledger — treating all specs as UNSHIPPED; "
            "spec recommendations may be wrong until shipped.jsonl is repaired",
            exc_info=True,
        )
        return set()


def _rolled_back_ids(project_dir: Path) -> set[str]:
    from darkfactory.plan.rollback import load_rolled_back

    try:
        return load_rolled_back(project_dir)
    except Exception:
        log.warning("could not read rollback log — ignoring rollbacks", exc_info=True)
        return set()


def is_shipped(
    project_dir: Path,
    spec_name: str,
    shipped_ids: set[str] | None = None,
    rolled_back: set[str] | None = None,
) -> bool:
    """True if ``spec_name`` should be treated as already shipped.

    Precedence: durable ledger first; an explicit rollback overrides any
    stale state; finally a per-spec state file marked shipped/deployed is
    honored as a fallback — this only fires on a *torn ship* (state written
    but the ledger append failed) or a legacy pre-ledger project, and never
    for a rolled-back spec.
    """
    if shipped_ids is None:
        shipped_ids = _shipped_ids(project_dir)
    if rolled_back is None:
        rolled_back = _rolled_back_ids(project_dir)
    # Exact match is correct here: the ledger spec_id equals the on-disk spec
    # stem by construction (the orchestrator ships spec_id=spec_run.spec_name;
    # the file is <spec_name>.md). compute_next's dependency check normalizes
    # slugs instead, because plan deps are bare slugs — a different namespace.
    if spec_name in shipped_ids:
        return True
    if spec_name in rolled_back:
        return False
    from darkfactory.state.store import read_state

    state_file = project_dir / ".dark-factory" / "state" / f"{spec_name}.state"
    return read_state(state_file, "status") in _SHIPPED_STATUSES


def _order_and_deps(
    project_dir: Path, names: list[str]
) -> tuple[list[str], dict[str, list[str]]]:
    """Plan-topo order ``names`` and return each one's resolved deps.

    Falls back to the input order with empty deps if no parseable plan
    exists (so callers degrade to "everything runnable, mtime order").
    """
    deps_by_name: dict[str, list[str]] = {}
    plans_dir = project_dir / ".dark-factory" / "plans"
    if not plans_dir.is_dir():
        return list(names), deps_by_name

    plan_files = sorted(
        plans_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True
    )
    if not plan_files:
        return list(names), deps_by_name

    try:
        from darkfactory.plan.parser import parse_plan
        from darkfactory.plan.topo_sort import topological_sort

        plan_specs = parse_plan(plan_files[0], project_dir)
        ordered_specs = topological_sort(plan_specs)
        # Map plan names (bare slug or dated stem) to the caller's stems. On a
        # slug collision (two specs, same slug, different date) the last
        # candidate wins — accepted; campaigns don't re-author under a new date.
        suffix_to_full: dict[str, str] = {}
        for s in names:
            suffix_to_full[s] = s
            suffix_to_full[_strip_date(s)] = s
        for ps in plan_specs:
            full = suffix_to_full.get(ps.name)
            if full is not None:
                deps_by_name[full] = [suffix_to_full.get(d, d) for d in ps.deps]
        seen: set[str] = set()
        ordered: list[str] = []
        for ps in ordered_specs:
            full = suffix_to_full.get(ps.name)
            if full is not None and full not in seen:
                ordered.append(full)
                seen.add(full)
        ordered += [s for s in names if s not in seen]
        return ordered, deps_by_name
    except Exception:
        log.warning(
            "plan unparseable/cyclic — falling back to flat order, dependency "
            "gating disabled",
            exc_info=True,
        )
        return list(names), deps_by_name


def compute_next(project_dir: Path, all_spec_names: list[str]) -> NextSpecs:
    """Partition ``all_spec_names`` into shipped / runnable / blocked.

    ``runnable`` is in plan-dependency order; its first element is the
    recommended next spec. A spec is runnable iff every resolved plan
    dependency is in the durable shipped set.
    """
    shipped_ids = _shipped_ids(project_dir)
    rolled_back = _rolled_back_ids(project_dir)
    unshipped = [
        s
        for s in all_spec_names
        if not is_shipped(project_dir, s, shipped_ids, rolled_back)
    ]
    ordered, deps_by_name = _order_and_deps(project_dir, unshipped)

    # Compare on date-stripped slugs: plan deps are bare slugs, shipped ids
    # carry a date prefix.
    shipped_keys = {_strip_date(s) for s in shipped_ids}

    runnable: list[str] = []
    blocked: dict[str, list[str]] = {}
    for s in ordered:
        missing = [
            d for d in deps_by_name.get(s, []) if _strip_date(d) not in shipped_keys
        ]
        if missing:
            blocked[s] = missing
        else:
            runnable.append(s)

    return NextSpecs(
        runnable=runnable,
        blocked=blocked,
        shipped_count=len(all_spec_names) - len(unshipped),
    )
