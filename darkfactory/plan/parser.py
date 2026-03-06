"""Markdown plan table parsing.

Replaces parse_plan() from dark-factory-plan.sh — parses the plan markdown
table and reads depends-on metadata from individual spec files.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from darkfactory.config.schema import validate_spec_name
from darkfactory.errors import ConfigError
from darkfactory.models.plan import PlanSpec

log = logging.getLogger(__name__)

# Matches a table separator row: | --- | --- | ... |
_TABLE_SEP_RE = re.compile(r'^\s*\|[\s-]+\|')
# Matches the header row containing "Spec"
_TABLE_HEADER_RE = re.compile(r'^\|.*\bSpec\b.*\|', re.IGNORECASE)
# Matches a table data row: | value | value | ... |
_TABLE_ROW_RE = re.compile(r'^\|')
# Matches depends-on metadata in a spec file
_DEPENDS_ON_RE = re.compile(r'^depends-on:\s*\[([^\]]*)\]')


def parse_plan(
    plan_file: Path,
    project_dir: Path,
) -> list[PlanSpec]:
    """Parse a plan markdown file into a list of PlanSpec objects.

    The plan file must contain a markdown table with columns:
    | Spec | Dependencies | Priority |

    After parsing, reads depends-on metadata from each spec file
    as the source of truth for dependencies (overrides the table).

    Raises ConfigError on invalid plans (no specs, duplicates, bad names,
    unknown dependencies).
    """
    specs = _parse_table(plan_file)

    if not specs:
        raise ConfigError(f"no specs found in plan file: {plan_file}")

    # Override deps from individual spec metadata.
    _apply_spec_metadata(specs, project_dir)

    # Validate all dependencies reference known specs.
    spec_names = {s.name for s in specs}
    for spec in specs:
        for dep in spec.deps:
            if dep not in spec_names:
                raise ConfigError(
                    f"spec {spec.name!r} depends on unknown spec {dep!r}"
                )

    # Parse target_files from spec metadata.
    for spec in specs:
        spec_file = project_dir / ".dark-factory" / "specs" / f"{spec.name}.md"
        if spec_file.exists():
            spec.target_files = parse_target_files(spec_file)

    return specs


def _parse_table(plan_file: Path) -> list[PlanSpec]:
    """Parse the markdown table from the plan file."""
    specs: list[PlanSpec] = []
    seen_names: set[str] = set()
    in_table = False

    for line in plan_file.read_text().splitlines():
        # Skip empty lines and headings.
        if not line.strip() or line.strip().startswith("#"):
            continue

        # Skip table separator rows.
        if _TABLE_SEP_RE.match(line):
            continue

        # Detect table header row.
        if _TABLE_HEADER_RE.match(line):
            in_table = True
            continue

        if in_table and _TABLE_ROW_RE.match(line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]

            if not cells or not cells[0].strip():
                continue

            name = cells[0].strip()

            try:
                validate_spec_name(name)
            except ConfigError:
                raise ConfigError(f"invalid spec name in plan: {name!r}")

            if name in seen_names:
                raise ConfigError(f"duplicate spec in plan: {name!r}")
            seen_names.add(name)

            # Parse dependencies (column 2).
            deps: list[str] = []
            if len(cells) > 1:
                dep_str = cells[1].strip()
                if dep_str and dep_str not in ("—", "-", "none", "None"):
                    deps = [d.strip() for d in dep_str.split(",") if d.strip()]

            # Parse priority (column 3).
            priority = 99
            if len(cells) > 2:
                prio_str = cells[2].strip()
                if prio_str:
                    if not prio_str.isdigit():
                        raise ConfigError(
                            f"non-numeric priority {prio_str!r} for spec {name!r}"
                        )
                    priority = int(prio_str)

            specs.append(PlanSpec(name=name, deps=deps, priority=priority))
        elif in_table:
            # Non-table line after table started — stop parsing.
            break

    return specs


def _apply_spec_metadata(
    specs: list[PlanSpec],
    project_dir: Path,
) -> None:
    """Override deps from individual spec file metadata blocks."""
    for spec in specs:
        spec_file = project_dir / ".dark-factory" / "specs" / f"{spec.name}.md"
        if not spec_file.exists():
            log.warning("spec file not found: %s", spec_file)
            continue

        for line in spec_file.read_text().splitlines():
            m = _DEPENDS_ON_RE.match(line)
            if m:
                raw = m.group(1).strip()
                if raw:
                    spec.deps = [d.strip() for d in raw.split(",") if d.strip()]
                else:
                    spec.deps = []
                break


def parse_target_files(spec_file: Path) -> list[str]:
    """Extract target-files from a spec's YAML metadata block.

    Expects a block like:
        target-files:
          - src/foo.ts
          - src/bar.ts

    Returns list of file paths.
    """
    files: list[str] = []
    in_target_files = False

    for line in spec_file.read_text().splitlines():
        if line.startswith("target-files:"):
            in_target_files = True
            continue

        if in_target_files:
            # End of block: non-indented line.
            if not line.startswith(" ") and not line.startswith("\t"):
                break
            stripped = line.strip()
            if stripped.startswith("- "):
                files.append(stripped[2:].strip())

    return files
