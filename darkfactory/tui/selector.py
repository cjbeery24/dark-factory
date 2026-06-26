"""Interactive TUI spec picker.

Replaces scripts/lib/tui.sh (124 lines). Provides an interactive
terminal interface for selecting a spec from available specs.
"""

from __future__ import annotations

import sys
from pathlib import Path

from darkfactory.plan.next_specs import compute_next


def select_spec(
    project_dir: Path,
    *,
    ci_mode: bool = False,
) -> str:
    """Interactively select a spec from the specs directory.

    Partitions specs (via :func:`compute_next`) into *runnable* — unshipped
    with all plan dependencies shipped, in dependency order — and *blocked*
    (waiting on unshipped deps), using the durable ``shipped.jsonl`` ledger.
    Only runnable specs are selectable; blocked specs are shown for context.
    Auto-selects when exactly one is runnable. In CI mode, errors if multiple
    runnable specs and no selection is provided.

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

    # Determine the runnable next spec(s) from durable, rollback-aware ship
    # state + the plan dependency graph — NOT ephemeral state/*.state files
    # (which are wiped on reset and miss pre-reset ships). See
    # darkfactory.plan.next_specs.
    nxt = compute_next(project_dir, all_specs)

    if not nxt.runnable and not nxt.blocked:
        print("All specs have already shipped.", file=sys.stderr)
        return ""

    runnable = nxt.runnable
    if not runnable:
        # Everything left is blocked on an unshipped dependency.
        print(
            "No runnable specs — all remaining are blocked on unshipped "
            "dependencies:",
            file=sys.stderr,
        )
        for spec, missing in nxt.blocked.items():
            print(f"  {spec} — needs {', '.join(missing)}", file=sys.stderr)
        return ""

    if len(runnable) == 1:
        return runnable[0]

    if ci_mode:
        print("ERROR: Multiple specs found in CI mode. Specify a spec name.", file=sys.stderr)
        return ""

    if not sys.stdin.isatty():
        print("ERROR: Interactive mode requires a terminal.", file=sys.stderr)
        return ""

    print(f"\nRunnable specs ({len(runnable)} ready, {nxt.shipped_count} shipped):")
    for i, spec in enumerate(runnable, 1):
        marker = " ◀ next (per declared plan deps)" if i == 1 else ""
        print(f"  {i:2d}. {spec}{marker}")
    if nxt.blocked:
        print(f"\n  Blocked ({len(nxt.blocked)} — waiting on unshipped deps):")
        for spec, missing in nxt.blocked.items():
            print(f"     {spec} — needs {', '.join(missing)}")
    print()

    while True:
        try:
            choice = input("Select spec [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

        if not choice:
            return runnable[0]

        if choice.lower() == "q":
            return ""

        try:
            idx = int(choice)
            if 1 <= idx <= len(runnable):
                return runnable[idx - 1]
            print(f"  Invalid: enter 1-{len(runnable)}")
        except ValueError:
            if choice in runnable:
                return choice
            print(f"  Unknown spec: {choice}")
