#!/usr/bin/env python3
"""Extract structured trial outcome from a pipeline archive + trial metadata.

Invoked by experiment-trial.sh after each pipeline run terminates.
Produces one JSON record per trial that downstream aggregation can
consume to evaluate the experiment's pre-committed decision rules.

Schema of the output JSON:
    {
      "trial_id": str,           # "phase-0-baseline-20260422-230000"
      "phase": str,
      "spec": str,
      "df_branch": str,
      "df_sha": str,
      "target_sha": str,
      "started_at": str (ISO),
      "finished_at": str (ISO),
      "wall_time_seconds": int,
      "archive_path": str | null,
      "outcome": str,            # one of the 3 terminal events or "NO_ARCHIVE"/"UNKNOWN"
      "shipped": bool,
      "shipped_with_no_known_bugs": bool,
      "iteration_count": int,
      "pass_rate": int | null,   # from last GATE_COMPLETED event
      "satisfaction": int | null,
      "scenario_total": int | null,
      "scenario_crashed": int | null,
      "gate_exit_code": int | null,
      "classification_counts": {category: count, ...},
      "known_bugs": {bug_name: occurrence_count, ...},
    }

Key extraction notes (verified 2026-04-22 against a real archive):
  - pass_rate is NOT in final/meta.json. It lives in
    final/events.jsonl GATE_COMPLETED events. We take the last.
  - Terminal events are exactly three: SPEC_SHIPPED, SPEC_ESCALATED,
    SPEC_FAILED. No SPEC_NOSHIP, SPEC_ABORTED, SPEC_SHIPPED_WITH_GAPS.
  - Classification counts live at iter-<N>/reports/*-defects-*.json.
"""
import argparse
import json
import pathlib
import re
import sys
from datetime import datetime, timezone

# Terminal events the pipeline actually emits (verified against
# darkfactory/models/events.py).
TERMINAL_EVENTS = {"SPEC_SHIPPED", "SPEC_ESCALATED", "SPEC_FAILED"}
SHIP_OUTCOMES = {"SPEC_SHIPPED"}

# Known RLS bug patterns. The production classifier at
# darkfactory/pipeline/classification_hints.py uses per-describe-body
# extraction; for post-hoc archive analysis, whole-file regex matching
# is coarser but sufficient for "did this pattern appear anywhere."
# False positives across describe boundaries are acceptable — the
# signal we want is "is the bug pattern present in this trial's
# scenarios," and file-level presence answers that.
KNOWN_BUGS = {
    # Peer connection reading tx-local app.* GUC — always broken
    # because SET LOCAL is tx-scoped.
    "peer_client_guc_read": re.compile(
        r"postgres\s*\(\s*process\.env\.[A-Z_][A-Z0-9_]*"
        r"[\s\S]*?current_setting\s*\(\s*['\"`]app\."
    ),
    # `SET LOCAL` with parameter-binding template — Postgres rejects
    # `could not determine data type of parameter $1`.
    "parameterized_set_local": re.compile(
        r"tx`SET\s+LOCAL\s+[^`]*\$\{"
    ),
    # ENABLE ROW LEVEL SECURITY without a FORCE in the same file —
    # table owners bypass policies unless FORCE is set.
    "owner_bypass_missing_force": re.compile(
        r"ENABLE\s+ROW\s+LEVEL\s+SECURITY"
    ),
    # current_setting('app.*') that strictly compares to '' or null —
    # the postgres.js wire surface makes both ambiguous.
    "strict_null_empty_guc_check": re.compile(
        r"current_setting\s*\(\s*['\"`]app\.[a-z_]+['\"`][^)]*\)"
        r"[\s\S]{0,200}?(===|!==|toBe\()\s*(null|''|\"\")"
    ),
}

_FORCE_RLS = re.compile(r"FORCE\s+ROW\s+LEVEL\s+SECURITY")


def last_terminal_event(events_path: pathlib.Path):
    """Return the last terminal-event JSON from events.jsonl, or None."""
    if not events_path.exists():
        return None
    with events_path.open() as f:
        lines = f.readlines()
    for line in reversed(lines):
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("event") in TERMINAL_EVENTS:
            return ev
    return None


def extract_gate_metrics(archive: pathlib.Path) -> dict:
    """Pull the last GATE_COMPLETED event's data fields.

    The last GATE_COMPLETED reflects the final gate result for this
    run. If multiple iterations ran, we take the last one's metrics.
    """
    events = archive / "final" / "events.jsonl"
    last_data = {}
    if not events.exists():
        return {
            "pass_rate": None,
            "satisfaction": None,
            "scenario_total": None,
            "scenario_crashed": None,
            "gate_exit_code": None,
        }
    with events.open() as f:
        for line in f:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event") == "GATE_COMPLETED":
                last_data = ev.get("data", {})
    return {
        "pass_rate": last_data.get("pass_rate"),
        "satisfaction": last_data.get("satisfaction"),
        "scenario_total": last_data.get("scenario_total"),
        "scenario_crashed": last_data.get("scenario_crashed"),
        "gate_exit_code": last_data.get("exit_code"),
    }


def grep_known_bugs(archive: pathlib.Path) -> dict:
    """Count files containing each known-bug pattern.

    Note: counts FILES, not occurrences. A file with the bug four
    times counts as 1. This matches the decision-rule concern:
    "did the bug appear in this trial's scenarios at all."
    """
    counts = {name: 0 for name in KNOWN_BUGS}
    for path in archive.glob("iter-*/scenarios/**/*.test.ts"):
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for name, pattern in KNOWN_BUGS.items():
            if pattern.search(text):
                # owner_bypass: a FORCE in the same file zeroes the
                # signal — the table is forced, so enabling without
                # forcing isn't the footgun.
                if name == "owner_bypass_missing_force" and _FORCE_RLS.search(text):
                    continue
                counts[name] += 1
    return counts


def count_iterations(archive: pathlib.Path) -> int:
    return len(list(archive.glob("iter-*")))


def classification_counts(archive: pathlib.Path) -> dict:
    iters = sorted(archive.glob("iter-*"))
    if not iters:
        return {}
    last_iter = iters[-1]
    defects_file = next(last_iter.glob("reports/*-defects-*.json"), None)
    if defects_file is None:
        return {}
    try:
        data = json.loads(defects_file.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    counts = {}
    for defect in data.get("defects", []):
        category = defect.get("category", "UNKNOWN")
        counts[category] = counts.get(category, 0) + 1
    return counts


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--archive", required=True,
                    help="Path to trial's archive dir, or 'NONE'")
    ap.add_argument("--meta", required=True,
                    help="Trial metadata JSON path")
    ap.add_argument("--wall-time", type=int, required=True,
                    help="Wall time seconds")
    ap.add_argument("--out", required=True,
                    help="Where to write the structured result JSON")
    args = ap.parse_args()

    meta = json.loads(pathlib.Path(args.meta).read_text())
    result = dict(meta)
    result["wall_time_seconds"] = args.wall_time
    result["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    archive_path = None
    if args.archive and args.archive != "NONE":
        p = pathlib.Path(args.archive)
        if p.is_dir():
            archive_path = p

    if archive_path is None:
        result.update({
            "archive_path": None,
            "outcome": "NO_ARCHIVE",
            "iteration_count": 0,
            "pass_rate": None,
            "satisfaction": None,
            "scenario_total": None,
            "scenario_crashed": None,
            "gate_exit_code": None,
            "classification_counts": {},
            "known_bugs": {name: 0 for name in KNOWN_BUGS},
            "shipped": False,
        })
    else:
        last_ev = last_terminal_event(archive_path / "final" / "events.jsonl")
        outcome = (last_ev or {}).get("event", "UNKNOWN")
        gate = extract_gate_metrics(archive_path)
        result.update({
            "archive_path": str(archive_path),
            "outcome": outcome,
            "iteration_count": count_iterations(archive_path),
            "classification_counts": classification_counts(archive_path),
            "known_bugs": grep_known_bugs(archive_path),
            "shipped": outcome in SHIP_OUTCOMES,
            **gate,
        })

    # Decision-rule-ready composite: "shipped AND no known bugs present."
    # This is the value the phase decision rule consults.
    result["shipped_with_no_known_bugs"] = (
        result.get("shipped", False)
        and all(v == 0 for v in result["known_bugs"].values())
    )

    pathlib.Path(args.out).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
