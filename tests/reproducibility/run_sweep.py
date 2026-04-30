"""CLI to run the full reproducibility sweep and write the catch-rate report.

Invocation::

    python -m tests.reproducibility.run_sweep [--runs N] [--out PATH]
                                              [--surfaces validator,densifier]
                                              [--fixture FIXTURE_ID]

Output is a Markdown report at ``--out`` (default
``docs/pipeline-run-reports/<date>-phase-3-5-session-2-typology-catch-rates.md``)
plus a JSON sidecar at the same stem. The JSON sidecar carries the
per-run RunEvaluation records so a future audit can re-aggregate
without re-invoking the LLM.

The SHIP criterion is encoded in ``aggregate_fixture`` and is what the
report's ``Verdict`` column reflects per row:

* TP fixtures: catches ≥ ⌈0.8 × runs⌉, all slots in permitted_slots,
  discriminator stable when the fixture flags it.
* Refinement fixtures: false positives ≤ ⌊runs / 5⌋ (1 for the canonical
  5-run sweep).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path

from tests.reproducibility.harness import (
    DEFAULT_MODEL,
    REPO_ROOT,
    Fixture,
    FixtureAggregate,
    claude_available,
    load_fixtures,
    run_fixture_sweep,
)


def _today() -> str:
    return dt.date.today().isoformat()


def _default_out_path() -> Path:
    return (
        REPO_ROOT
        / "docs"
        / "pipeline-run-reports"
        / f"{_today()}-phase-3-5-session-2-typology-catch-rates.md"
    )


def _serialize(obj):
    if is_dataclass(obj):
        return {k: _serialize(v) for k, v in asdict(obj).items()}
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, (list, tuple)):
        return [_serialize(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _row(agg: FixtureAggregate, fixture: Fixture) -> str:
    verdict = "PASS" if agg.passed else "FAIL"
    if fixture.expected_catch:
        catch_col = f"{agg.catches}/{agg.runs}"
        slots = ", ".join(agg.slots_caught) or "—"
        fp_col = f"{agg.false_positives}/{agg.runs}"
        # Line precision: in-range / measured-runs (where a line was emitted).
        measured = agg.catches - agg.line_precision_unmeasured
        if measured > 0:
            precision_col = f"{agg.line_precision_caught}/{measured}"
        elif agg.catches > 0:
            precision_col = f"0/0 (no lines emitted)"
        else:
            precision_col = "—"
    else:
        catch_col = "—"  # not measured for refinement
        slots = "—"
        fp_col = f"{agg.false_positives}/{agg.runs}"
        precision_col = "—"
    reason = agg.failure_reason or ""
    return (
        f"| {fixture.fixture_id} | {agg.surface} | {fixture.type_category} | "
        f"{catch_col} | {fp_col} | {slots} | {precision_col} | {verdict} | {reason} |"
    )


def render_report(
    aggregates: list[tuple[Fixture, FixtureAggregate]],
    *,
    runs: int,
    model: str,
    surfaces: list[str],
) -> str:
    today = _today()
    total = len(aggregates)
    passed = sum(1 for _, a in aggregates if a.passed)
    failed = total - passed
    lines: list[str] = []
    lines.append(
        f"# Phase 3.5 Session 2 — typology catch-rate report ({today})"
    )
    lines.append("")
    lines.append(
        f"**Model**: `{model}` · **Runs per (fixture × surface)**: {runs} · "
        f"**Surfaces**: {', '.join(surfaces)}"
    )
    lines.append("")
    lines.append(
        "## Scope (what this measurement does and does not say)"
    )
    lines.append("")
    lines.append(
        "Per Neo's Session-2 design pass, this harness measures **Consistency-"
        "Check-only / Step-4.5-only behavior** in isolation. The validator "
        "surface receives a Consistency-Check-only directive in stdin (skips "
        "Completeness Checklist, Precision Analysis, Scope Check, Vehicle "
        "Selection, Foot-gun checks); the densifier surface receives a "
        "\"Steps 1-4 just finished — run Step 4.5\" framing instead of the "
        "interactive REVISE / SHIP_ANYWAY loop."
    )
    lines.append("")
    lines.append(
        "**Implication.** A green report here transfers to production iff "
        "the typology procedure is the load-bearing component of catch "
        "behavior in production prompts. If real production runs at the full "
        "validator's attention budget catch contradictions less often than "
        "this harness reports, the gap is **attention-budget interference**, "
        "not typology-procedure failure. Session 3 (bb-v2 regression on a "
        "real 5-FR spec) is the test of that gap and is OUT OF SCOPE here."
    )
    lines.append("")
    lines.append("## Aggregate")
    lines.append("")
    lines.append(f"- Total cases: {total}")
    lines.append(f"- Passed: {passed}")
    lines.append(f"- Failed: {failed}")
    lines.append("")
    lines.append("## Per-fixture × surface results")
    lines.append("")
    lines.append(
        "| fixture | surface | category | catches | false-pos | slots | line-prec | verdict | reason |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for fixture, agg in aggregates:
        lines.append(_row(agg, fixture))
    lines.append("")
    lines.append("## Per-run detail")
    lines.append("")
    for fixture, agg in aggregates:
        lines.append(f"### {fixture.fixture_id} · {agg.surface}")
        lines.append("")
        if not agg.per_run:
            lines.append("(no runs recorded)")
            lines.append("")
            continue
        for i, r in enumerate(agg.per_run, 1):
            line = (
                f"- run {i}: caught={r.caught} slot={r.slot or '—'} "
                f"fp={r.fp}"
            )
            if r.discriminator:
                line += f" discriminator=`{r.discriminator}`"
            if r.notes:
                line += f" — {r.notes}"
            lines.append(line)
        lines.append("")
    lines.append("## Methodology")
    lines.append("")
    lines.append(
        "Each (fixture × surface) was invoked `runs` times via subprocess "
        "`claude -p --output-format=json --model=<model>`. The validator "
        "surface used `--settings '{\"temperature\": 0}'` (Phase 3 "
        "lockdown); the densifier surface used the default temperature "
        "(no production temperature pin on the skill)."
    )
    lines.append("")
    lines.append(
        "The validator system-append concatenated `agents/spec-validator.md`, "
        "the postgres adapter validator addendum (when the fixture declared "
        "`active_adapters: [\"postgres\"]`), and the validator barrier prompt "
        "from `darkfactory/docker/barriers.py:189-197`. The densifier "
        "system-append was the Step-4.5-through-Step-5 slice of "
        "`.claude/skills/df-densify-spec/SKILL.md` (which inlines the SSOT "
        "block)."
    )
    lines.append("")
    lines.append(
        "Anchor matching is at the **fr_id + field level**. Line precision "
        "is reported separately as the `line-prec` column (in-range catches "
        "/ catches with a line emitted) — not a ship gate. Slot stability "
        "was strict 5/5-same-type except for fixtures with explicit "
        "`permitted_slots` (currently only `T4-borderline-entity`, which "
        "permits T1 OR T4 with a discriminator-stability requirement)."
    )
    lines.append("")
    lines.append(
        "Why line precision is advisory: in calibration runs the validator "
        "often emitted lineless anchors (`FR-3.processing` not "
        "`FR-3.processing@line15`), and the densifier sometimes echoed the "
        "SSOT worked example's literal `@line62` regardless of the "
        "fixture's actual line numbers. Treating line precision as a hard "
        "match criterion would reject correct catches; treating it as an "
        "advisory metric preserves the catch signal AND surfaces the "
        "precision regression."
    )
    lines.append("")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_sweep")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument(
        "--surfaces", default="validator,densifier",
        help="comma-separated subset of {validator, densifier}",
    )
    parser.add_argument(
        "--fixture", default=None,
        help="comma-separated fixture_ids to run (default: all)",
    )
    parser.add_argument(
        "--out", default=str(_default_out_path()),
        help="output report path (Markdown). JSON sidecar written alongside.",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument(
        "--timeout-seconds", type=int, default=300,
        help="per-invocation timeout",
    )
    args = parser.parse_args(argv)

    if not claude_available():
        print("error: claude CLI not on PATH", file=sys.stderr)
        return 2

    surfaces = [s.strip() for s in args.surfaces.split(",") if s.strip()]
    for s in surfaces:
        if s not in {"validator", "densifier"}:
            print(f"error: unknown surface {s!r}", file=sys.stderr)
            return 2

    fixtures = load_fixtures()
    if args.fixture:
        wanted = {x.strip() for x in args.fixture.split(",") if x.strip()}
        fixtures = [f for f in fixtures if f.fixture_id in wanted]
        if not fixtures:
            print(
                f"error: no fixture matched {args.fixture!r}",
                file=sys.stderr,
            )
            return 2

    pairs = [(f, s) for f in fixtures for s in surfaces]
    print(
        f"running {len(pairs)} cases × {args.runs} runs = "
        f"{len(pairs) * args.runs} invocations (model={args.model})",
        file=sys.stderr,
    )

    aggregates: list[tuple[Fixture, FixtureAggregate]] = []
    for fixture, surface in pairs:
        print(
            f"  [{fixture.fixture_id} · {surface}] running {args.runs} invocations…",
            file=sys.stderr,
            flush=True,
        )
        agg = run_fixture_sweep(
            fixture, surface, runs=args.runs, model=args.model,
            timeout_seconds=args.timeout_seconds,
        )
        print(
            f"    -> {('PASS' if agg.passed else 'FAIL')} "
            f"catches={agg.catches}/{agg.runs} fps={agg.false_positives}",
            file=sys.stderr,
            flush=True,
        )
        aggregates.append((fixture, agg))

    out_md = Path(args.out)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    md = render_report(
        aggregates, runs=args.runs, model=args.model, surfaces=surfaces,
    )
    out_md.write_text(md, encoding="utf-8")
    out_json = out_md.with_suffix(".json")
    out_json.write_text(
        json.dumps(
            [
                {
                    "fixture_id": f.fixture_id,
                    "type_category": f.type_category,
                    "surface": agg.surface,
                    "expected_catch": f.expected_catch,
                    "passed": agg.passed,
                    "failure_reason": agg.failure_reason,
                    "runs": agg.runs,
                    "catches": agg.catches,
                    "false_positives": agg.false_positives,
                    "slots_caught": agg.slots_caught,
                    "slot_stable": agg.slot_stable,
                    "discriminator_stable": agg.discriminator_stable,
                    "line_precision_caught": agg.line_precision_caught,
                    "line_precision_unmeasured": agg.line_precision_unmeasured,
                    "per_run": [_serialize(r) for r in agg.per_run],
                }
                for f, agg in aggregates
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    n_failed = sum(1 for _, a in aggregates if not a.passed)

    def _rel(p: Path) -> str:
        try:
            return str(p.relative_to(REPO_ROOT))
        except ValueError:
            return str(p)

    print(
        f"wrote report → {_rel(out_md)}\n"
        f"wrote sidecar → {_rel(out_json)}\n"
        f"summary: {len(aggregates) - n_failed}/{len(aggregates)} passed",
        file=sys.stderr,
    )
    return 1 if n_failed else 0


if __name__ == "__main__":
    sys.exit(main())
