#!/usr/bin/env python3
"""One-shot migration: add `## Non-Negotiable Acceptance Criteria` to old specs.

Phase D (2026-04-20) introduced a hard precheck rule: every spec must
declare which AC / EC / FR / NFR IDs are release-blocking. Specs
written before this rule have no such section and will now fail
validation. This script walks a directory of specs and inserts a stub
section with a ``Justification:`` paragraph — the safest default, which
tells the pipeline that no AC is blocking. The author can edit the
stub afterward to list specific IDs.

Usage::

    python scripts/migrate_specs_add_blocking.py <spec_dir> [<spec_dir> ...]
    python scripts/migrate_specs_add_blocking.py --dry-run <spec_dir>

Exit codes: 0 on success (zero or more edits), 1 on any error.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

STUB = """## Non-Negotiable Acceptance Criteria

Justification: this spec predates the Phase D Non-Negotiable Acceptance
Criteria rule (2026-04-20) and was migrated by a one-shot script. The
author should replace this paragraph with an explicit list of blocking
AC / EC / FR / NFR IDs, or leave the justification in place if no
criterion is release-blocking for this spec.

---

"""

_SCENARIO_RE = re.compile(r"(?m)^## (?:\d+[a-z]?\.\s*)?Scenario Intent\b")
_AC_RE = re.compile(r"(?m)^## (?:\d+[a-z]?\.\s*)?Acceptance Criteria\b")


def migrate_file(path: Path, *, dry_run: bool) -> str:
    text = path.read_text()
    if "Non-Negotiable Acceptance Criteria" in text:
        return "skip-already-migrated"

    anchor = _SCENARIO_RE.search(text)
    if anchor is None:
        ac = _AC_RE.search(text)
        if ac is None:
            return "skip-no-anchor"
        # Insert at end of file as a fallback.
        new_text = text.rstrip() + "\n\n" + STUB.rstrip() + "\n"
    else:
        start = anchor.start()
        pre = text[:start]
        pre_stripped = pre.rstrip()
        if pre_stripped.endswith("---"):
            cut = pre.rfind("---")
            new_text = text[:cut] + STUB + text[start:]
        else:
            new_text = text[:start] + STUB + text[start:]

    if dry_run:
        return "would-migrate"
    path.write_text(new_text)
    return "migrated"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="+", type=Path,
                    help="Directories (or individual .md files) to migrate.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would change without writing.")
    args = ap.parse_args(argv)

    targets: list[Path] = []
    for p in args.paths:
        if p.is_dir():
            targets.extend(sorted(p.glob("*.md")))
        elif p.is_file():
            targets.append(p)
        else:
            print(f"ERROR: {p} does not exist", file=sys.stderr)
            return 1

    counts: dict[str, int] = {}
    for t in targets:
        result = migrate_file(t, dry_run=args.dry_run)
        counts[result] = counts.get(result, 0) + 1
        print(f"{result:25s} {t}")

    print("\nSummary:")
    for k, v in sorted(counts.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
