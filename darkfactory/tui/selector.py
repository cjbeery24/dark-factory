"""Interactive TUI spec picker.

Replaces scripts/lib/tui.sh (124 lines). Provides an interactive
terminal interface for selecting a spec from available specs.
"""

from __future__ import annotations

import sys
from pathlib import Path


_SHIPPED_STATUSES = frozenset({"shipped", "deployed"})


def _spec_is_shipped(project_dir: Path, spec_name: str) -> bool:
    """Return True if the spec's state file shows shipped or deployed."""
    from darkfactory.state.store import read_state
    state_file = project_dir / ".dark-factory" / "state" / f"{spec_name}.state"
    return read_state(state_file, "status") in _SHIPPED_STATUSES


def _ordered_specs(project_dir: Path, specs: list[str]) -> list[str]:
    """Return specs sorted by plan dependency order if a plan exists.

    Falls back to newest-first (original behavior) if no plan is found
    or the plan can't be parsed.
    """
    plans_dir = project_dir / ".dark-factory" / "plans"
    if not plans_dir.is_dir():
        return specs

    plan_files = sorted(plans_dir.glob("*.md"), key=lambda f: f.stat().st_mtime, reverse=True)
    if not plan_files:
        return specs

    try:
        import re
        from darkfactory.plan.parser import parse_plan
        from darkfactory.plan.topo_sort import topological_sort
        plan_specs = parse_plan(plan_files[0], project_dir)
        ordered = topological_sort(plan_specs)
        # Build a map from name (with or without date prefix) to full spec stem.
        suffix_to_full: dict[str, str] = {}
        for s in specs:
            suffix_to_full[s] = s  # exact match
            stripped = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", s)
            suffix_to_full[stripped] = s  # date-stripped match
        # Return plan-ordered specs first, then any extras not in the plan.
        result = [suffix_to_full[s.name] for s in ordered if s.name in suffix_to_full]
        result += [s for s in specs if s not in set(result)]
        return result
    except Exception:
        return specs


def select_spec(
    project_dir: Path,
    *,
    ci_mode: bool = False,
) -> str:
    """Interactively select a spec from the specs directory.

    Filters out already-shipped specs and sorts remaining specs by
    build-phase order from the plan. Auto-selects if only one spec
    remains. In CI mode, errors if multiple specs and no selection provided.

    Returns the spec name (without .md extension).
    """
    spec_dir = project_dir / ".dark-factory" / "specs"
    if not spec_dir.is_dir():
        print("No specs directory found.", file=sys.stderr)
        return ""

    all_specs = sorted(
        [f.stem for f in spec_dir.glob("*.md")],
        key=lambda s: (spec_dir / f"{s}.md").stat().st_mtime,
        reverse=True,
    )

    if not all_specs:
        print("No spec files found.", file=sys.stderr)
        return ""

    # Filter to unshipped specs only.
    unshipped = [s for s in all_specs if not _spec_is_shipped(project_dir, s)]

    if not unshipped:
        print("All specs have already shipped.", file=sys.stderr)
        return ""

    # Sort by plan dependency order.
    ordered = _ordered_specs(project_dir, unshipped)

    if len(ordered) == 1:
        return ordered[0]

    if ci_mode:
        print("ERROR: Multiple specs found in CI mode. Specify a spec name.", file=sys.stderr)
        return ""

    if not sys.stdin.isatty():
        print("ERROR: Interactive mode requires a terminal.", file=sys.stderr)
        return ""

    shipped_count = len(all_specs) - len(unshipped)
    print(f"\nAvailable specs ({len(ordered)} remaining, {shipped_count} shipped):")
    for i, spec in enumerate(ordered, 1):
        marker = " ◀ next" if i == 1 else ""
        print(f"  {i:2d}. {spec}{marker}")
    print()

    while True:
        try:
            choice = input("Select spec [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

        if not choice:
            return ordered[0]

        if choice.lower() == "q":
            return ""

        try:
            idx = int(choice)
            if 1 <= idx <= len(ordered):
                return ordered[idx - 1]
            print(f"  Invalid: enter 1-{len(ordered)}")
        except ValueError:
            if choice in ordered:
                return choice
            print(f"  Unknown spec: {choice}")
