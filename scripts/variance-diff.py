#!/usr/bin/env python3
"""Phase 6 I3: post-run variance-diff analysis.

Given two pipeline run_ids — both of which ran on the same spec — emits
normalized diff distances on architect manifest, scenario fingerprints,
and classifier determinism across the matched (N, N+1) iteration pairs.

Reads events from the canonical ``.dark-factory/audit/events.jsonl`` on
disk. Designed to run *after* both pipelines have completed — no
connection to live pipeline state.

Output is plain text to stdout by default; pass ``--json`` for a machine-
readable payload intended for piping into the Phase 6 results table.

Metric definitions (kept in sync with docs/plans/2026-04-16-*.md):

- ``manifest_diff_distance`` — 1.0 if manifest hashes differ, 0.0 if
  identical. When hashes differ, a ``canonical_jaccard`` field breaks
  the tie with the Jaccard similarity of canonical-JSON tokens so two
  "differ" runs can still be ranked by how much they differ.

- ``scenario_diff_distance`` — mean per-file fingerprint distance. A
  file missing from one run counts as distance 1.0. A file present in
  both counts as the average of four substructure distances:
  Jaccard on ``import_set`` / ``symbol_set`` / ``test_names``, plus
  absolute-difference-over-max on ``assertion_count``.

- ``classifier_determinism`` — fraction of (classifier input hash,
  output hash) observations where the same input hash produced
  different output hashes across the two runs. Expressed as a
  percentage (0-100).

Exits 0 regardless of what it measures; this is an analysis tool, not
a gate.

Usage:

    python3 scripts/variance-diff.py \\
        --project-dir /path/to/target \\
        --run-a <run_id_a> --run-b <run_id_b> [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path


def _load_events(events_path: Path, run_id: str) -> list[dict]:
    out: list[dict] = []
    if not events_path.is_file():
        return out
    with events_path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("run_id") == run_id:
                out.append(ev)
    return out


def _last_of_type(events: list[dict], event_type: str) -> dict | None:
    for ev in reversed(events):
        if ev.get("type") == event_type:
            return ev
    return None


def _by_type(events: list[dict], event_type: str) -> list[dict]:
    return [ev for ev in events if ev.get("type") == event_type]


def _jaccard(a: list[str] | set, b: list[str] | set) -> float:
    """Jaccard similarity in [0, 1]; 1.0 when both empty (no drift)."""
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    inter = sa & sb
    return len(inter) / len(union) if union else 1.0


def _token_jaccard(a: str, b: str) -> float:
    """Jaccard on canonical-JSON character 3-grams.

    Gives a readable "how different" number when two manifest hashes
    already tell us *that* they differ — fine-grained comparison of
    the canonical serialization without pulling in a diff library.
    """
    if not a and not b:
        return 1.0
    def _grams(s: str, n: int = 3) -> set[str]:
        return {s[i:i + n] for i in range(max(0, len(s) - n + 1))}
    return _jaccard(_grams(a), _grams(b))


def _abs_over_max(a: int, b: int) -> float:
    """|a-b| / max(a, b); 0.0 when both are zero (no drift)."""
    m = max(a, b)
    return 0.0 if m == 0 else abs(a - b) / m


def _manifest_diff(events_a: list[dict], events_b: list[dict]) -> dict:
    a = _last_of_type(events_a, "ARCHITECT_MANIFEST_HASH")
    b = _last_of_type(events_b, "ARCHITECT_MANIFEST_HASH")
    if a is None or b is None:
        return {
            "present": False,
            "reason": (
                "missing ARCHITECT_MANIFEST_HASH on "
                + ("run_a" if a is None else "")
                + ("+" if a is None and b is None else "")
                + ("run_b" if b is None else "")
            ),
        }
    ha = (a.get("data") or {}).get("manifest_hash", "")
    hb = (b.get("data") or {}).get("manifest_hash", "")
    ja = (a.get("data") or {}).get("canonical_json", "")
    jb = (b.get("data") or {}).get("canonical_json", "")
    identical = bool(ha) and ha == hb
    return {
        "present": True,
        "hash_a": ha,
        "hash_b": hb,
        "distance": 0.0 if identical else 1.0,
        "canonical_jaccard": 1.0 if identical else _token_jaccard(ja, jb),
        "cache_hit_a": (a.get("data") or {}).get("cache_hit", False),
        "cache_hit_b": (b.get("data") or {}).get("cache_hit", False),
    }


def _pick_last_phase_fingerprints(events: list[dict]) -> dict[str, dict]:
    """For each ``scenario_path``, keep only the most recent fingerprint.

    TE emits one event per file per phase (generate → validate →
    coverage_regen). The last emission is the set the pipeline handed
    to the implementer; that is the set the variance comparison is
    interested in. Earlier phases are preserved in the log for
    separate per-phase-drift analysis but are collapsed here.
    """
    latest: dict[str, dict] = {}
    for ev in _by_type(events, "SCENARIO_FINGERPRINT"):
        data = ev.get("data") or {}
        path = data.get("scenario_path", "")
        if not path:
            continue
        latest[path] = data
    return latest


def _fingerprint_distance(a: dict, b: dict) -> float:
    """Average of per-field distances in [0, 1]."""
    d_imp = 1.0 - _jaccard(a.get("import_set", []), b.get("import_set", []))
    d_sym = 1.0 - _jaccard(a.get("symbol_set", []), b.get("symbol_set", []))
    d_tests = 1.0 - _jaccard(
        a.get("test_names", []), b.get("test_names", [])
    )
    d_asrt = _abs_over_max(
        int(a.get("assertion_count") or 0),
        int(b.get("assertion_count") or 0),
    )
    return (d_imp + d_sym + d_tests + d_asrt) / 4.0


def _normalize_path(full_path: str, run_id: str) -> str:
    """Strip the absolute prefix so two runs compare by the trailing
    scenario path, not by the host path they happened to live under.

    ``.dark-factory/scenarios/<spec>/<file>`` is the stable suffix;
    anything preceding it (a temp dir, a project root) is run-specific.
    """
    marker = "/.dark-factory/scenarios/"
    idx = full_path.find(marker)
    return full_path[idx + 1:] if idx >= 0 else full_path


def _scenario_diff(events_a: list[dict], events_b: list[dict]) -> dict:
    by_a = _pick_last_phase_fingerprints(events_a)
    by_b = _pick_last_phase_fingerprints(events_b)
    if not by_a and not by_b:
        return {"present": False, "reason": "no SCENARIO_FINGERPRINT events on either run"}
    norm_a: dict[str, dict] = {
        _normalize_path(p, "a"): v for p, v in by_a.items()
    }
    norm_b: dict[str, dict] = {
        _normalize_path(p, "b"): v for p, v in by_b.items()
    }
    all_paths = sorted(set(norm_a) | set(norm_b))
    per_file: list[dict] = []
    for path in all_paths:
        fa, fb = norm_a.get(path), norm_b.get(path)
        if fa is None or fb is None:
            per_file.append({"path": path, "distance": 1.0, "presence": "missing"})
            continue
        per_file.append({
            "path": path,
            "distance": round(_fingerprint_distance(fa, fb), 4),
            "presence": "both",
        })
    mean_d = (
        sum(x["distance"] for x in per_file) / len(per_file)
        if per_file else 0.0
    )
    return {
        "present": True,
        "file_count_a": len(norm_a),
        "file_count_b": len(norm_b),
        "shared_files": len([x for x in per_file if x["presence"] == "both"]),
        "missing_files": len([x for x in per_file if x["presence"] == "missing"]),
        "mean_distance": round(mean_d, 4),
        "per_file": per_file,
    }


def _classifier_determinism(
    events_a: list[dict], events_b: list[dict],
) -> dict:
    """Fraction of adjacent classifier invocations with identical input
    but different output hashes, across both runs combined."""
    # Build (run_tag, input_hash) → list of output_hash observations.
    obs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for tag, events in (("a", events_a), ("b", events_b)):
        for ev in _by_type(events, "CLASSIFIER_IO_HASH"):
            data = ev.get("data") or {}
            ihash = data.get("input_hash", "")
            ohash = data.get("output_hash", "")
            if ihash:
                obs[(tag, ihash)].append(ohash)
    # Also cross-run: same input_hash across runs A and B.
    cross: dict[str, list[str]] = defaultdict(list)
    for (tag, ihash), ohashes in obs.items():
        for oh in ohashes:
            cross[ihash].append(oh)
    total_pairs = 0
    mismatch_pairs = 0
    for ihash, ohashes in cross.items():
        if len(ohashes) < 2:
            continue
        # Compare each unique adjacent pair (N*(N-1)/2) to characterize
        # the full cross-product without blowing up on dup outputs.
        seen: list[str] = []
        for oh in ohashes:
            for prior in seen:
                total_pairs += 1
                if prior != oh:
                    mismatch_pairs += 1
            seen.append(oh)
    det_pct = (
        100.0 * mismatch_pairs / total_pairs if total_pairs else 0.0
    )
    return {
        "present": total_pairs > 0,
        "total_pairs": total_pairs,
        "mismatch_pairs": mismatch_pairs,
        "determinism_pct": round(det_pct, 2),
    }


def _iteration_zero_gain(events: list[dict]) -> int:
    """Count ITERATION_TEMPORAL_SIGNATURE events where no tests flipped."""
    count = 0
    for ev in _by_type(events, "ITERATION_TEMPORAL_SIGNATURE"):
        data = ev.get("data") or {}
        moved_pass = int(data.get("tests_moved_to_pass") or 0)
        moved_fail = int(data.get("tests_moved_to_fail") or 0)
        unchanged = int(data.get("tests_unchanged") or 0)
        if moved_pass + moved_fail == 0 and unchanged > 0:
            count += 1
    return count


def _format_text(report: dict) -> str:
    out: list[str] = []
    out.append(
        f"variance-diff: run_a={report['run_a']} run_b={report['run_b']}"
    )
    out.append(f"events: a={report['events_a']} b={report['events_b']}")
    m = report["manifest"]
    if m.get("present"):
        out.append(
            f"manifest_diff_distance={m['distance']:.2f} "
            f"(canonical_jaccard={m['canonical_jaccard']:.3f}) "
            f"cache_hit_a={m['cache_hit_a']} cache_hit_b={m['cache_hit_b']}"
        )
    else:
        out.append(f"manifest: [missing] {m.get('reason', '')}")
    s = report["scenarios"]
    if s.get("present"):
        out.append(
            f"scenario_diff_distance={s['mean_distance']:.4f} "
            f"files: shared={s['shared_files']} missing={s['missing_files']}"
        )
    else:
        out.append(f"scenarios: [missing] {s.get('reason', '')}")
    c = report["classifier"]
    if c.get("present"):
        out.append(
            f"classifier_determinism={c['determinism_pct']:.2f}% "
            f"({c['mismatch_pairs']}/{c['total_pairs']} mismatched pairs)"
        )
    else:
        out.append("classifier_determinism: [no cross-run input_hash overlap]")
    out.append(
        f"zero_gain_iterations: a={report['zero_gain_a']} "
        f"b={report['zero_gain_b']}"
    )
    return "\n".join(out)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project-dir", required=True, type=Path,
        help="Target project root containing .dark-factory/audit/events.jsonl",
    )
    parser.add_argument("--run-a", required=True, help="First run_id")
    parser.add_argument("--run-b", required=True, help="Second run_id")
    parser.add_argument(
        "--json", action="store_true",
        help="Emit machine-readable JSON instead of text.",
    )
    args = parser.parse_args(argv)

    events_path = args.project_dir / ".dark-factory" / "audit" / "events.jsonl"
    events_a = _load_events(events_path, args.run_a)
    events_b = _load_events(events_path, args.run_b)

    report = {
        "run_a": args.run_a,
        "run_b": args.run_b,
        "events_path": str(events_path),
        "events_a": len(events_a),
        "events_b": len(events_b),
        "manifest": _manifest_diff(events_a, events_b),
        "scenarios": _scenario_diff(events_a, events_b),
        "classifier": _classifier_determinism(events_a, events_b),
        "zero_gain_a": _iteration_zero_gain(events_a),
        "zero_gain_b": _iteration_zero_gain(events_b),
    }

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(_format_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
