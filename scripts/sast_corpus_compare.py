#!/usr/bin/env python3
"""Offline regex-vs-Semgrep corpus comparison — SAST-swap demote evidence (step 1).

The in-pipeline compare (Sprint 30) validated that the Semgrep compare WIRING
fires in a real run. But the demote decision ("does Semgrep subsume regex X?") is
a property of the SCANNERS ON CODE, not of the pipeline — and clean pipeline impls
produce 0/0/0, so the regex-false-positive signal is rare per live run. This tool
gathers that evidence OFFLINE at corpus scale, with zero API cost, by running the
SAME functions the pipeline uses (``prepass.analyze_security`` for the bespoke
regexes, ``sast.run_semgrep`` for the vendored Semgrep ruleset, ``sast.build_compare``
for reconciliation) over a body of real code, and aggregating per-category /
per-regex agree / regex-only / regex-only-HIGH / semgrep-only counts.

Reads the demote bar in docs/curation-audit.md:
- regex_only_HIGH on safe code = candidate FALSE POSITIVE (the demote signal).
- semgrep_only = coverage the regex MISSES (the cost of demoting that category).
- agree = both flag the same site (Semgrep corroborates the regex).

Usage:
    python scripts/sast_corpus_compare.py \
        --corpus python:/tmp/semgrep-rules/python/lang/security \
        --corpus node:/tmp/semgrep-rules/javascript/lang/security \
        --corpus python:/Users/.../dark-factory/darkfactory \
        --out /tmp/sast-corpus-report.json

Each --corpus is ``<language>:<path>`` (language ∈ {python, node}). Requires
``dark-factory[sast]`` (semgrep) installed.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

from darkfactory.review.prepass import analyze_security
from darkfactory.review.patterns import SECURITY_PATTERNS
from darkfactory.review.sast import build_compare, run_semgrep, _probe_semgrep


def _scan_one(label: str, language: str, path: Path) -> dict:
    """Run both scanners over one corpus entry; return aggregated stats."""
    # owned_surface=None → every finding gating (no owned-surface concept offline;
    # we want ALL findings). source_dirs=["."] → scan the whole corpus path.
    reg = analyze_security(path, ["."], language, owned_surface=None)
    sast = run_semgrep(path, ["."], language, owned_surface=None)
    summary, rows = build_compare(reg, sast)

    # Per-category + per-regex-pattern aggregation over the gating rows.
    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in rows:
        if r["scope"] != "gating":
            continue
        cat = r["category"]
        v = r["verdict"]
        by_cat[cat][v] += 1
        if v == "regex_only" and (r.get("regex") or {}).get("severity") == "HIGH":
            by_cat[cat]["regex_only_high"] += 1

    return {
        "label": label,
        "language": language,
        "path": str(path),
        "sast_ran": summary["sast_ran"],
        "sast_error": summary["sast_error"],
        "totals": {
            "agree": summary["agree"],
            "regex_only": summary["regex_only"],
            "semgrep_only": summary["semgrep_only"],
            "regex_only_high": summary["regex_only_high"],
        },
        "by_category": {k: dict(v) for k, v in by_cat.items()},
        "regex_finding_count": len(reg.findings),
        "semgrep_finding_count": len(sast.findings),
    }


def _merge_category(acc: dict, entry: dict) -> None:
    for cat, counts in entry["by_category"].items():
        for k, n in counts.items():
            acc[cat][k] = acc[cat].get(k, 0) + n


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", action="append", default=[], metavar="LANG:PATH",
                    help="corpus entry as <language>:<path>; repeatable")
    ap.add_argument("--out", type=Path, default=Path("/tmp/sast-corpus-report.json"))
    args = ap.parse_args(argv)

    if _probe_semgrep() is None:
        print("ERROR: semgrep not installed/in-pin (need dark-factory[sast]).", file=sys.stderr)
        return 2
    if not args.corpus:
        print("ERROR: pass at least one --corpus LANG:PATH", file=sys.stderr)
        return 2

    entries: list[dict] = []
    overall: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for spec in args.corpus:
        lang, _, p = spec.partition(":")
        path = Path(p)
        if lang not in ("python", "node"):
            print(f"skip {spec}: language must be python|node", file=sys.stderr)
            continue
        if not path.is_dir():
            print(f"skip {spec}: not a dir: {path}", file=sys.stderr)
            continue
        print(f"scanning [{lang}] {path} ...", file=sys.stderr)
        e = _scan_one(p, lang, path)
        entries.append(e)
        _merge_category(overall, e)

    report = {
        "kind": "sast_corpus_compare_report",
        "regex_pattern_budget": sum(len(v) for v in SECURITY_PATTERNS.values()),
        "corpus_entries": entries,
        "overall_by_category": {k: dict(v) for k, v in overall.items()},
    }
    args.out.write_text(json.dumps(report, indent=2))

    # Human-readable summary to stdout.
    print(f"\n=== SAST corpus compare — {len(entries)} corpus entr(ies) ===")
    print(f"{'category':<26} {'agree':>6} {'rgx_only':>9} {'rgx_HIGH':>9} {'sg_only':>8}")
    print("-" * 62)
    cats = sorted(overall, key=lambda c: -(overall[c].get("regex_only_high", 0)))
    for cat in cats:
        c = overall[cat]
        print(f"{cat:<26} {c.get('agree',0):>6} {c.get('regex_only',0):>9} "
              f"{c.get('regex_only_high',0):>9} {c.get('semgrep_only',0):>8}")
    print("-" * 62)
    print("Demote read: high 'rgx_HIGH' on SAFE code = candidate false positives "
          "(demote target). 'sg_only' = coverage Semgrep adds. See docs/curation-audit.md.")
    print(f"\nfull report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
