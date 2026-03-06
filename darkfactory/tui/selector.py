"""Interactive TUI spec picker.

Replaces scripts/lib/tui.sh (124 lines). Provides an interactive
terminal interface for selecting a spec from available specs.
"""

from __future__ import annotations

import sys
from pathlib import Path


def select_spec(
    project_dir: Path,
    *,
    ci_mode: bool = False,
) -> str:
    """Interactively select a spec from the specs directory.

    In CI mode, errors if multiple specs and no selection provided.
    Auto-selects if only one spec.

    Returns the spec name (without .md extension).
    """
    spec_dir = project_dir / ".dark-factory" / "specs"
    if not spec_dir.is_dir():
        print("No specs directory found.", file=sys.stderr)
        return ""

    specs = sorted(
        [f.stem for f in spec_dir.glob("*.md")],
        key=lambda s: (spec_dir / f"{s}.md").stat().st_mtime,
        reverse=True,
    )

    if not specs:
        print("No spec files found.", file=sys.stderr)
        return ""

    if len(specs) == 1:
        return specs[0]

    if ci_mode:
        print("ERROR: Multiple specs found in CI mode. Specify a spec name.", file=sys.stderr)
        return ""

    # Interactive selection.
    if not sys.stdin.isatty():
        print("ERROR: Interactive mode requires a terminal.", file=sys.stderr)
        return ""

    print("\nAvailable specs (newest first):")
    for i, spec in enumerate(specs, 1):
        mtime = (spec_dir / f"{spec}.md").stat().st_mtime
        from datetime import datetime
        date_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  {i:2d}. {spec}  ({date_str})")
    print()

    while True:
        try:
            choice = input("Select spec [1]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return ""

        if not choice:
            return specs[0]

        if choice.lower() == "q":
            return ""

        try:
            idx = int(choice)
            if 1 <= idx <= len(specs):
                return specs[idx - 1]
            print(f"  Invalid: enter 1-{len(specs)}")
        except ValueError:
            # Try matching by name.
            if choice in specs:
                return choice
            print(f"  Unknown spec: {choice}")
