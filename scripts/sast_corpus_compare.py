#!/usr/bin/env python3
"""Offline regex-vs-Semgrep corpus comparison — SAST-swap demote evidence (step 1).

The in-pipeline compare (Sprint 30) validated that the Semgrep compare WIRING
fires in a real run. But the demote decision ("does Semgrep subsume regex X?") is
a property of the SCANNERS ON CODE, not of the pipeline — and clean pipeline impls
produce 0/0/0, so the regex-false-positive signal is rare per live run. This tool
gathers that evidence OFFLINE at corpus scale, with zero API cost, by running the
SAME functions the pipeline uses (``prepass.analyze_security`` for the bespoke
regexes, ``sast.run_semgrep`` for the vendored Semgrep ruleset, ``sast.build_compare``
for reconciliation) over a body of real code.

Two corpus KINDS, interpreted differently (this is the v2 cleanup):
- ``safe``    : real, mature OSS code presumed free of blatant vulns. Here a regex
                HIGH finding Semgrep does NOT corroborate (``regex_only_high``) is a
                candidate FALSE POSITIVE — the demote signal.
- ``targets`` : deliberately-vulnerable code (e.g. semgrep-rules' own test targets).
                Here ``agree`` + ``semgrep_only`` measure recall/coverage, and
                ``regex_only`` = a real vuln the regex caught that the vendored
                Semgrep subset missed (a KEEP signal for that regex).

CODE-ONLY STAGING: both scanners see the SAME code-only file set (copied to a temp
dir, filtered by language extension, data/vendored dirs skipped). This removes the
v1 asymmetry where the regex scanner matched pattern-strings inside ``*.yaml`` rule
data that Semgrep never reads.

Usage:
    python scripts/sast_corpus_compare.py \
        --corpus python:targets:/tmp/semgrep-rules/python/lang/security \
        --corpus node:targets:/tmp/semgrep-rules/javascript/lang/security \
        --corpus python:safe:/tmp/corpus-safe/requests/src \
        --corpus node:safe:/tmp/corpus-safe/fastify/lib \
        --out /tmp/sast-corpus-report.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from darkfactory.review.prepass import analyze_security
from darkfactory.review.patterns import SECURITY_PATTERNS
from darkfactory.review.sast import build_compare, run_semgrep, _probe_semgrep

_CODE_EXT = {
    "python": {".py"},
    "node": {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"},
}
# Dirs that are never application code under test (vendored deps, VCS, caches,
# build output, and our OWN vendored rule DATA — the v1 noise source).
_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".mypy_cache", ".pytest_cache", ".tox", "site-packages", "sast_rules",
    "test", "tests", "__tests__", "fixtures", "examples", "vendor",
}


def _stage_code(src: Path, language: str) -> tuple[Path, int]:
    """Copy only language code files into a fresh temp dir (symmetric corpus for
    both scanners). Returns (staged_dir, file_count). Skips data/vendored dirs."""
    exts = _CODE_EXT[language]
    staged = Path(tempfile.mkdtemp(prefix="sast-corpus-"))
    n = 0
    for f in src.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in exts:
            continue
        if any(part in _SKIP_DIRS for part in f.relative_to(src).parts[:-1]):
            continue
        out = staged / f.relative_to(src)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, out)
        n += 1
    return staged, n


def _scan_one(label: str, language: str, kind: str, src: Path) -> dict:
    staged, n_files = _stage_code(src, language)
    try:
        reg = analyze_security(staged, ["."], language, owned_surface=None)
        sast = run_semgrep(staged, ["."], language, owned_surface=None)
        summary, rows = build_compare(reg, sast)
    finally:
        shutil.rmtree(staged, ignore_errors=True)

    by_cat: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    fp_examples: list[dict] = []
    for r in rows:
        if r["scope"] != "gating":
            continue
        cat, v = r["category"], r["verdict"]
        by_cat[cat][v] += 1
        if v == "regex_only" and (r.get("regex") or {}).get("severity") == "HIGH":
            by_cat[cat]["regex_only_high"] += 1
            if kind == "safe" and len(fp_examples) < 40:
                fp_examples.append({
                    "category": cat, "file": r["file"], "line": r["line"],
                    "matched": (r.get("regex") or {}).get("matched", ""),
                })

    return {
        "label": label, "language": language, "kind": kind, "path": str(src),
        "files_scanned": n_files,
        "sast_ran": summary["sast_ran"], "sast_error": summary["sast_error"],
        "totals": {k: summary[k] for k in ("agree", "regex_only", "semgrep_only", "regex_only_high")},
        "by_category": {k: dict(v) for k, v in by_cat.items()},
        "regex_finding_count": len(reg.findings),
        "semgrep_finding_count": len(sast.findings),
        "fp_examples": fp_examples,  # safe-corpus candidate false positives
    }


def _merge(acc: dict, entry: dict) -> None:
    for cat, counts in entry["by_category"].items():
        for k, nn in counts.items():
            acc[cat][k] = acc[cat].get(k, 0) + nn


def _print_table(title: str, agg: dict, fp_focus: bool) -> None:
    print(f"\n=== {title} ===")
    if not agg:
        print("  (no findings)")
        return
    print(f"{'category':<26} {'agree':>6} {'rgx_only':>9} {'rgx_HIGH':>9} {'sg_only':>8}")
    print("-" * 62)
    key = (lambda c: -agg[c].get("regex_only_high", 0)) if fp_focus else (lambda c: -agg[c].get("semgrep_only", 0))
    for cat in sorted(agg, key=key):
        c = agg[cat]
        print(f"{cat:<26} {c.get('agree',0):>6} {c.get('regex_only',0):>9} "
              f"{c.get('regex_only_high',0):>9} {c.get('semgrep_only',0):>8}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", action="append", default=[], metavar="LANG:KIND:PATH")
    ap.add_argument("--out", type=Path, default=Path("/tmp/sast-corpus-report.json"))
    args = ap.parse_args(argv)

    if _probe_semgrep() is None:
        print("ERROR: semgrep not installed/in-pin (need dark-factory[sast]).", file=sys.stderr)
        return 2
    if not args.corpus:
        print("ERROR: pass at least one --corpus LANG:KIND:PATH", file=sys.stderr)
        return 2

    entries: list[dict] = []
    safe_agg: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    tgt_agg: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for spec in args.corpus:
        parts = spec.split(":", 2)
        if len(parts) != 3:
            print(f"skip {spec}: expected LANG:KIND:PATH", file=sys.stderr); continue
        lang, kind, p = parts
        path = Path(p)
        if lang not in _CODE_EXT or kind not in ("safe", "targets") or not path.is_dir():
            print(f"skip {spec}: bad lang/kind/path", file=sys.stderr); continue
        print(f"scanning [{lang}/{kind}] {path} ...", file=sys.stderr)
        e = _scan_one(p, lang, kind, path)
        entries.append(e)
        _merge(safe_agg if kind == "safe" else tgt_agg, e)

    report = {
        "kind": "sast_corpus_compare_report_v2",
        "regex_pattern_budget": sum(len(v) for v in SECURITY_PATTERNS.values()),
        "safe_overall_by_category": {k: dict(v) for k, v in safe_agg.items()},
        "targets_overall_by_category": {k: dict(v) for k, v in tgt_agg.items()},
        "corpus_entries": entries,
    }
    args.out.write_text(json.dumps(report, indent=2))

    _print_table("SAFE corpora — regex FALSE-POSITIVE signal (high rgx_HIGH = demote candidate)",
                 safe_agg, fp_focus=True)
    _print_table("TARGETS corpora — recall/coverage (high sg_only = Semgrep adds coverage)",
                 tgt_agg, fp_focus=False)

    # Show a few concrete safe-corpus FP sites for eyeballing.
    fp_rows = [fp for e in entries if e["kind"] == "safe" for fp in e["fp_examples"]]
    if fp_rows:
        print("\n--- sample SAFE-corpus regex HIGH findings Semgrep did NOT corroborate (candidate FPs) ---")
        for fp in fp_rows[:25]:
            print(f"  [{fp['category']}] {fp['file']}:{fp['line']}  {fp['matched'][:70]!r}")
    print(f"\nfull report -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
