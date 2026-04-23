#!/usr/bin/env python3
"""Aggregate trial records from one or more phases and evaluate the
pre-committed decision rule.

Reads $LOGDIR/trials.jsonl (one JSON per line, produced by
analyze_trial.py per trial) and prints a phase-by-phase summary plus
the decision-rule verdict.

Decision rule (pre-committed 2026-04-22):
  For each phase:
    - clean_ships = trials where outcome == SPEC_SHIPPED AND no known
      bug patterns were present in scenarios (shipped_with_no_known_bugs)
    - N = total trials in phase
    - If clean_ships / N >= 2/3 AND phase has N >= 3:
        → "SUFFICIENT"  (ship the phase's changes, stop the experiment)
    - If N == 3 and clean_ships / N == 1/3:
        → "AMBIGUOUS"  (extend to N=6 for this phase)
    - Else:
        → "INSUFFICIENT"  (advance to next phase)

  Additional guard (Neo's round-3 tightening): any SPEC_SHIPPED with
  a known-bug recurrence downgrades that trial from the clean-ships
  count — it's "lucky pass," not "correct."

Phase 0 (control, no changes) has a separate interpretation:
  - clean_ships / N >= 2/3: ambient variance is doing the work,
    later phases' SUFFICIENT verdicts are suspect. Flag for review.

Usage:
    python3 experiment-aggregate.py /tmp/df-experiment/phase-0-baseline
    python3 experiment-aggregate.py /tmp/df-experiment/*/
"""
import argparse
import json
import pathlib
import sys
from collections import defaultdict


def load_phase_trials(logdir: pathlib.Path) -> list[dict]:
    """Load trials.jsonl for one phase; return list of trial dicts."""
    path = logdir / "trials.jsonl"
    if not path.exists():
        return []
    trials = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            trials.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"WARN: unparseable line in {path}", file=sys.stderr)
    return trials


def classify_phase(trials: list[dict]) -> dict:
    """Apply the pre-committed decision rule to a phase's trials."""
    n = len(trials)
    clean_ships = sum(1 for t in trials if t.get("shipped_with_no_known_bugs", False))
    lucky_ships = sum(
        1 for t in trials
        if t.get("shipped", False) and not t.get("shipped_with_no_known_bugs", False)
    )
    outcomes = [t.get("outcome", "UNKNOWN") for t in trials]

    if n < 3:
        verdict = "INCOMPLETE"
    elif clean_ships / n >= 2 / 3:
        verdict = "SUFFICIENT"
    elif n == 3 and clean_ships == 1:
        verdict = "AMBIGUOUS"
    else:
        verdict = "INSUFFICIENT"

    pass_rates = [t["pass_rate"] for t in trials if t.get("pass_rate") is not None]

    return {
        "n": n,
        "clean_ships": clean_ships,
        "lucky_ships": lucky_ships,
        "outcomes": outcomes,
        "verdict": verdict,
        "mean_pass_rate": (sum(pass_rates) / len(pass_rates)) if pass_rates else None,
        "median_iterations": sorted(t.get("iteration_count", 0) for t in trials)[n // 2] if n else None,
    }


def bug_presence_summary(trials: list[dict]) -> dict:
    """Count trials that had each known-bug pattern present."""
    counts = defaultdict(int)
    for t in trials:
        for bug, n in (t.get("known_bugs") or {}).items():
            if n > 0:
                counts[bug] += 1
    return dict(counts)


def format_phase(phase_name: str, trials: list[dict]) -> str:
    summary = classify_phase(trials)
    bugs = bug_presence_summary(trials)
    lines = [
        f"=== {phase_name} ===",
        f"  N: {summary['n']}",
        f"  Clean ships (shipped + no known bugs): {summary['clean_ships']}",
        f"  Lucky ships (shipped but with bugs):    {summary['lucky_ships']}",
        f"  Outcomes: {', '.join(summary['outcomes'])}",
        f"  Mean pass_rate: {summary['mean_pass_rate']}",
        f"  Median iterations: {summary['median_iterations']}",
        f"  Known-bug presence (trials/N):",
    ]
    if bugs:
        for bug, n in sorted(bugs.items()):
            lines.append(f"    {bug}: {n}/{summary['n']}")
    else:
        lines.append("    (none present in any trial)")
    lines.append(f"  VERDICT: {summary['verdict']}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("logdirs", nargs="+",
                    help="One or more $LOGDIR paths (phase directories)")
    ap.add_argument("--json", action="store_true",
                    help="Output JSON summary instead of text")
    args = ap.parse_args()

    results = {}
    for logdir_str in args.logdirs:
        logdir = pathlib.Path(logdir_str)
        if not logdir.is_dir():
            print(f"WARN: {logdir} is not a directory", file=sys.stderr)
            continue
        phase_name = logdir.name
        trials = load_phase_trials(logdir)
        if not trials:
            print(f"WARN: no trials in {logdir}/trials.jsonl", file=sys.stderr)
            continue
        results[phase_name] = {
            "trials": trials,
            "summary": classify_phase(trials),
            "known_bug_presence": bug_presence_summary(trials),
        }

    if args.json:
        # Strip trials field for JSON output (too verbose); keep summaries
        out = {k: {"summary": v["summary"], "known_bug_presence": v["known_bug_presence"]}
               for k, v in results.items()}
        print(json.dumps(out, indent=2))
        return 0

    for phase_name in results:
        print(format_phase(phase_name, results[phase_name]["trials"]))
        print()

    # Cross-phase decision roll-up
    print("=== Cross-phase roll-up ===")
    phase0 = next((p for p in results if "phase-0" in p), None)
    if phase0 and results[phase0]["summary"]["clean_ships"] >= 2:
        print("  WARNING: Phase 0 (control) shipped cleanly ≥2/N times — "
              "ambient variance is doing some of the work. Treat later phases' "
              "SUFFICIENT verdicts with caution.")
    for name, data in results.items():
        print(f"  {name}: {data['summary']['verdict']} "
              f"({data['summary']['clean_ships']}/{data['summary']['n']} clean)")

    return 0


if __name__ == "__main__":
    sys.exit(main())
