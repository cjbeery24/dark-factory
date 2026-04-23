#!/usr/bin/env python3
"""Archive-audit gate — reject destructive refactors that would break
correctly-written archived scenarios.

Context
-------

Track A ships a destructive refactor of the postgres tx_guc harness
(``assertTxGucEquals`` → ``assertAppSetsTxGuc`` + ``assertTxGucRoundTrips``).
Pre-experiment validation #5 verified that no archived scenario uses
``assertTxGucEquals`` correctly, so the split is safe today — but the
same refactor shape recurs (rename a helper, split a callback into
two narrower primitives, collapse two helpers into one). Each time,
someone has to scan every archived scenario by hand and argue from
the emptiness of the match set that the refactor is safe.

This script mechanizes that argument: given a list of removed/renamed
symbols and an archive root, it greps every archived scenario file
for each symbol, groups matches by symbol + file, and exits non-zero
if any matches are found. The output names which files would break
and cites the removal reason so the operator can either upgrade the
archived scenarios or abandon the refactor.

Scope
-----

The gate is **offline** — it does not run in the pipeline container.
It runs when a maintainer is about to land a destructive refactor to
an adapter contract surface. Once the refactor is landed, no future
pipeline run can produce a scenario using the old symbol (the symbol
is gone from the staged adapter contract), so the gate is a one-shot
safety net.

Usage::

    python3 archive_audit_gate.py \\
        --archive-root <dir> \\
        --removed-symbol <name>:<reason> \\
        [--removed-symbol <name>:<reason> ...] \\
        [--file-glob <pattern>]

Exit codes:
    0 — no archived scenario references any removed symbol
    1 — one or more references found; refactor unsafe
    2 — usage error (malformed --removed-symbol, no archive root)

``--file-glob`` defaults to ``.test.ts .test.js .spec.ts .spec.js``
(whitespace-separated); override for non-Node ecosystems.
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


_DEFAULT_GLOBS = ("*.test.ts", "*.test.js", "*.spec.ts", "*.spec.js")


@dataclass(frozen=True)
class RemovedSymbol:
    """A symbol the maintainer is removing (or renaming)."""

    name: str
    reason: str

    @classmethod
    def parse(cls, spec: str) -> "RemovedSymbol":
        """Parse a ``name:reason`` CLI spec.

        The separator is the **first** colon; subsequent colons are
        part of the reason text (reason strings often embed URLs,
        timestamps, or other colon-bearing tokens).
        """
        if ":" not in spec:
            raise argparse.ArgumentTypeError(
                f"--removed-symbol: expected ``name:reason``, got {spec!r}",
            )
        name, _, reason = spec.partition(":")
        name = name.strip()
        reason = reason.strip()
        if not name:
            raise argparse.ArgumentTypeError(
                "--removed-symbol: name part is empty",
            )
        if not reason:
            raise argparse.ArgumentTypeError(
                "--removed-symbol: reason part is empty",
            )
        return cls(name=name, reason=reason)


@dataclass(frozen=True)
class Hit:
    file: Path
    line: int
    excerpt: str
    symbol: RemovedSymbol


def _compile_pattern(symbol: RemovedSymbol) -> re.Pattern[str]:
    """Word-boundary match on the symbol name.

    A bare substring search would match ``assertTxGucEqualsButDifferent``,
    which is a legitimate helper name the operator did not touch. ``\\b``
    at either end prevents that while still catching the symbol in
    any position a JS/TS tokenizer would recognize (import list,
    identifier, destructuring).
    """
    return re.compile(rf"\b{re.escape(symbol.name)}\b")


def scan_file(path: Path, symbols: list[RemovedSymbol]) -> list[Hit]:
    """Return every hit for every symbol in ``path``."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines = text.splitlines()
    hits: list[Hit] = []
    for sym in symbols:
        pat = _compile_pattern(sym)
        for line_no, line in enumerate(lines, start=1):
            if pat.search(line):
                hits.append(Hit(
                    file=path,
                    line=line_no,
                    excerpt=line.strip()[:160],
                    symbol=sym,
                ))
    return hits


def scan_archive(
    archive_root: Path,
    symbols: list[RemovedSymbol],
    globs: tuple[str, ...] = _DEFAULT_GLOBS,
) -> list[Hit]:
    """Walk ``archive_root`` for scenario files; aggregate hits.

    Returns hits deterministically ordered by (symbol.name, file, line)
    so the report is reviewer-stable across runs.
    """
    if not archive_root.is_dir():
        return []
    hits: list[Hit] = []
    seen: set[Path] = set()
    for pattern in globs:
        for path in archive_root.rglob(pattern):
            if path in seen:
                continue
            seen.add(path)
            hits.extend(scan_file(path, symbols))
    hits.sort(key=lambda h: (h.symbol.name, str(h.file), h.line))
    return hits


def format_report(
    hits: list[Hit], symbols: list[RemovedSymbol], archive_root: Path,
) -> str:
    """Render a reviewer-stable markdown report.

    Even on the clean path we emit a short summary so the operator
    can tell the difference between "audit ran and passed" vs "audit
    was never invoked."
    """
    if not hits:
        names = ", ".join(s.name for s in symbols)
        return (
            f"archive-audit-gate: PASS — no archived scenario references "
            f"[{names}] under {archive_root}\n"
        )
    lines: list[str] = [
        f"archive-audit-gate: FAIL — {len(hits)} reference(s) across "
        f"{len({h.file for h in hits})} file(s)\n",
    ]
    # Group by symbol so the fix-hint (reason) is colocated with the files.
    by_symbol: dict[str, list[Hit]] = {}
    for h in hits:
        by_symbol.setdefault(h.symbol.name, []).append(h)
    for name, group in by_symbol.items():
        reason = group[0].symbol.reason
        lines.append(f"\n## {name} ({len(group)} reference(s))\n")
        lines.append(f"**Reason:** {reason}\n")
        for h in group:
            rel = h.file.relative_to(archive_root) if archive_root in h.file.parents else h.file
            lines.append(f"- {rel}:{h.line} — `{h.excerpt}`")
        lines.append("")
    lines.append(
        "\nUpgrade the listed files, or abandon the refactor. The "
        "destructive change cannot land while any archived scenario "
        "still uses the removed symbols as named.\n",
    )
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--archive-root", type=Path, required=True,
        help="Directory under which to scan for archived scenarios.",
    )
    ap.add_argument(
        "--removed-symbol", type=RemovedSymbol.parse,
        action="append", default=[], required=True,
        help=(
            "One or more ``name:reason`` pairs. Repeat for each symbol "
            "being removed or renamed."
        ),
    )
    ap.add_argument(
        "--file-glob", nargs="+", default=list(_DEFAULT_GLOBS),
        help=(
            f"File-name glob patterns to scan. Defaults to "
            f"{' '.join(_DEFAULT_GLOBS)}."
        ),
    )
    args = ap.parse_args(argv)

    if not args.archive_root.is_dir():
        print(
            f"archive-audit-gate: ERROR — archive root does not exist "
            f"or is not a directory: {args.archive_root}",
            file=sys.stderr,
        )
        return 2

    hits = scan_archive(
        args.archive_root, args.removed_symbol, tuple(args.file_glob),
    )
    sys.stdout.write(format_report(hits, args.removed_symbol, args.archive_root))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
