#!/usr/bin/env python3
"""Summary + PR-body builder for the B9b retire_after auto-eval
workflow (`.github/workflows/auto-eval-retire-after.yml`).

Reads `EvalResult` JSONs from a results directory (one per universal
helper, produced by `dark-factory contracts auto-eval --out PATH`),
emits:

* a Markdown summary table to ``$GITHUB_STEP_SUMMARY``
* a step output ``retired=<space-sep helper names>`` to
  ``$GITHUB_OUTPUT`` (consumed by the next step's ``if:`` condition)
* a PR body to ``--pr-body-out PATH`` if any helper has
  ``verdict == "retire"``

Lives under ``scripts/`` (not ``darkfactory/``) because it's CI-only
glue — no production caller imports from here. Extracted from an
inline workflow heredoc per Linus B9b commit-4 review #6: 80 lines of
unsyntax-highlighted, untestable Python in a YAML string is worse
than a tracked file that ``ruff`` and ``mypy`` cover.

The :data:`MANUAL_REMOVAL_PATHS` constant is pinned against the
framework's actual file structure by
``tests/unit/test_auto_eval_workflow.py`` — a maintainer reorganizing
the postgres adapter without updating this list fails CI before the
next auto-eval run produces a PR with stale instructions.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import NamedTuple


# Manual-removal checklist used in the PR body. Each entry is
# (path, instruction). Paths must point at real files in the
# framework — pinned by the unit test. The first entry uses a
# placeholder for the per-helper TS file (``<helper-file>.ts``);
# the test asserts the parent directory exists and contains
# ``.ts`` files instead of asserting the placeholder literally.
MANUAL_REMOVAL_PATHS: tuple[tuple[str, str], ...] = (
    (
        "darkfactory/adapters/postgres/contracts/ts/<helper-file>.ts",
        "delete the per-helper TS source file",
    ),
    (
        "darkfactory/adapters/postgres/contracts/ts/index.ts",
        "remove the helper's re-export",
    ),
    (
        "darkfactory/adapters/postgres/retire-after.toml",
        "remove the matching `[[pin]]` row",
    ),
    (
        "darkfactory/adapters/postgres/retire_after_fixtures.py",
        "remove the `Fixture` entry; `BY_HELPER_NAME` is auto-derived",
    ),
    (
        "darkfactory/adapters/postgres/retire_after_trigger_prompts.py",
        "remove the `TriggerPrompt` entry; `BY_HELPER_NAME` is auto-derived",
    ),
    (
        "darkfactory/adapters/postgres/scenario_architect_addendum.md",
        "remove the helper from the substrate-rule import-list",
    ),
)


class HelperResult(NamedTuple):
    helper_name: str
    verdict: str
    rounds: int
    foot_gun_hits: int
    helper_import_count: int


def load_results(results_dir: Path) -> list[HelperResult]:
    """Read every ``*.json`` in ``results_dir`` and parse as
    ``HelperResult``. Sorted by helper_name for stable output."""
    results: list[HelperResult] = []
    for path in sorted(results_dir.glob("*.json")):
        data = json.loads(path.read_text())
        results.append(HelperResult(
            helper_name=data["helper_name"],
            verdict=data["verdict"],
            rounds=data["rounds"],
            foot_gun_hits=data["foot_gun_hits"],
            helper_import_count=data["helper_import_count"],
        ))
    return results


def render_step_summary(
    results: list[HelperResult], model: str, rounds: str,
) -> str:
    """Markdown summary table for ``$GITHUB_STEP_SUMMARY``."""
    lines = [
        "## B9b retire_after auto-eval summary",
        "",
        f"Model: `{model}`",
        f"Rounds per helper: `{rounds}`",
        "",
        "| Helper | Verdict | Rounds | foot_gun_hits | helper_imports |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| `{r.helper_name}` | **{r.verdict}** | {r.rounds} | "
            f"{r.foot_gun_hits} | {r.helper_import_count} |"
        )
    return "\n".join(lines) + "\n"


def render_pr_body(
    retired: list[HelperResult], model: str, rounds: str,
) -> str:
    """PR body for the draft removal PR. Only called if ``retired``
    is non-empty."""
    lines = [
        "## Auto-eval verdict: retire",
        "",
        f"Model: `{model}`",
        f"Rounds per helper: `{rounds}`",
        "",
        "### Helpers verdicted retire",
        "",
    ]
    for r in retired:
        lines.append(
            f"- `{r.helper_name}` — rounds={r.rounds}, "
            f"foot_gun_hits={r.foot_gun_hits}, "
            f"helper_imports={r.helper_import_count}"
        )
    lines.extend([
        "",
        "### Manual removal required",
        "",
        "Auto-eval is a *calibration signal*, not a removal mechanism. ",
        "To retire a helper, push commits to this branch editing ALL of:",
        "",
    ])
    for path, instruction in MANUAL_REMOVAL_PATHS:
        lines.append(f"- `{path}` — {instruction}")
    lines.append("- Any helper-specific tests in `tests/unit/`")
    lines.extend([
        "",
        "### Per-round model outputs",
        "",
        f"See workflow artifact `auto-eval-results-{model}-<run_id>` "
        "(90-day retention) for the per-round TS model outputs. ",
        "The committed JSONs in `.github/auto-eval-history/` carry "
        "counts and booleans only — no test-shape content.",
        "",
        "### Why a draft PR (not auto-merge)",
        "",
        "ROADMAP.md Phase 3 B9b: helper retirement is a deliberate "
        "framework-governance act. The verdict means *across N rounds, "
        "no foot-gun pattern hit AND no helper import* — but a "
        "low-rounds regex calibration miss can produce a `retire` "
        "verdict for a helper that genuinely catches a foot-gun. The "
        "first few model bumps' worth of verdict data are calibration "
        "signal for the trigger prompts in "
        "`retire_after_trigger_prompts.py`, not removal authorization.",
        "",
        "### Mechanical removal CLI",
        "",
        "Deferred (`dark-factory contracts retire <helper>`) — pending "
        "verdict-calibration data from real model bumps. See "
        "ROADMAP.md Phase 3 B9b in-flight notes.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Summarize auto-eval results and build PR body.",
    )
    parser.add_argument(
        "--results-dir", required=True, type=Path,
        help="Directory containing per-helper EvalResult JSONs.",
    )
    parser.add_argument(
        "--pr-body-out", required=True, type=Path,
        help="Path to write the PR body (only written if any helper "
             "has verdict=retire).",
    )
    args = parser.parse_args()

    # Inputs from the workflow's env block. Required — fail loud if
    # missing rather than substituting empty strings into the body.
    try:
        model = os.environ["INPUT_MODEL"]
        rounds = os.environ["INPUT_ROUNDS"]
        gh_output = Path(os.environ["GITHUB_OUTPUT"])
        gh_summary = Path(os.environ["GITHUB_STEP_SUMMARY"])
    except KeyError as exc:
        print(f"Required env var missing: {exc}", file=sys.stderr)
        return 2

    results = load_results(args.results_dir)
    retired = [r for r in results if r.verdict == "retire"]

    # Step output: space-separated helper names with verdict=retire.
    # Empty string → next step's ``if: != ''`` evaluates false and PR
    # creation is skipped.
    with gh_output.open("a") as f:
        f.write(f"retired={' '.join(r.helper_name for r in retired)}\n")

    with gh_summary.open("a") as f:
        f.write(render_step_summary(results, model, rounds))

    if retired:
        args.pr_body_out.write_text(render_pr_body(retired, model, rounds))
        print(f"Wrote PR body for {len(retired)} retire-verdict helper(s).")
    else:
        print("All helpers still load-bearing — no PR will be opened.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
