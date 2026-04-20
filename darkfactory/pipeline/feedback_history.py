"""Priority 1 (2026-04-20 plan revision): implementer self-correction loop.

Builds the iter >= 2 implementer prompt augmentations:

1. ``build_cumulative_history`` — concatenated chronological feedback from
   every prior iteration, with classifier rationale entries. The raw
   cadence signal ("FR-7 failed in iter 1 AND iter 2") is preserved — no
   dedupe. Scenarios are already visible to the implementer, so cadence
   itself does not leak information beyond what the scenario directory
   exposes.

2. ``build_iter_diff`` — unified diff of changes the implementer wrote on
   the previous iteration, scoped to ``config.source_dirs``. Gives the
   implementer visibility into its own prior attempt.

3. ``ROOT_CAUSE_PREAMBLE_TEMPLATE`` — the structured preamble the
   implementer is required to emit before writing code on iter >= 2.
   Forces naming the primary failure, root cause location, fix scope
   (``local-patch`` / ``signature-change`` / ``module-rework``), and
   how the approach differs from the prior attempt.

4. ``audit_history_for_barrier_leak`` — asserts the generated history
   contains no holdout scenario names, no holdout assertion text, and
   no paths outside the visible scenarios directory. Fails loudly so
   a misrouted read is caught before the prompt reaches the agent.

These helpers are intentionally pure (take paths + text, return text +
errors). The iteration loop composes them into the final prompt.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


# Byte/line caps. Cumulative history can grow fast with many iterations
# + many defects per iter. Budget is the implementer prompt's total
# word count — uncapped history on iter 3 of a spec with 60 scenarios
# starves spec and manifest context.
_HISTORY_MAX_CHARS = 40_000
_DIFF_MAX_CHARS = 20_000


ROOT_CAUSE_PREAMBLE_TEMPLATE = """\
## Required root-cause analysis (iter >= 2)

Before writing any code, emit this preamble exactly once. The pipeline \
audits post-hoc for its presence.

```
## Root cause analysis
- Primary failure from iter N-1: <anchor ID (FR-N or EC-N)> \u2014 <one-line description>
- Root cause: <file:line> \u2014 <one-sentence mechanism>
- Proposed fix scope: [local-patch | signature-change | module-rework]
- Why this differs from the iter N-1 attempt: <one sentence>
```

Guidance on fix scope:
- ``local-patch`` \u2014 the failure is a bug inside an existing \
function body; no API surface change required.
- ``signature-change`` \u2014 the failure requires changing a function \
signature, type alias, or interface. Before writing the change, \
enumerate callsites in ``/project/.dark-factory/scenarios/`` and in \
existing source files; confirm they all remain valid under the new \
signature, or update them in the same iteration.
- ``module-rework`` \u2014 the failure pattern indicates that the \
current module structure is wrong (circular responsibility, misplaced \
abstraction). Restructure decisively; do not add another band-aid.

### Example of a signature-change failure mode (from V2-R3, database-foundation spec)

Iter 1 used ``withTenantDb(id, cb: (tx: PostgresSql) => ...)`` where \
``tx`` was a postgres.js tagged-template connection inside a \
transaction. Iter 2 switched to Drizzle's ``db.transaction()``, \
changing the callback signature to ``(tx: DrizzleTx) => ...``. This \
fixed some RLS blind scenarios but broke every visible scenario's \
``await tx\\`SELECT current_setting(...)\\`` \u2014 ``DrizzleTx`` is \
not a tagged-template function. Lesson: when changing callback \
signatures, enumerate callsites BEFORE writing the change. If a \
signature change breaks visible scenarios, either pick a different \
signature or bundle the callsite updates into the same iteration.
"""


@dataclass(frozen=True)
class HistoryBuildResult:
    """Output of ``build_cumulative_history``.

    ``text`` is the concatenated chronological history ready to inject
    into the prompt (empty string if no prior iterations exist).
    ``iterations_included`` is the list of iteration numbers whose
    feedback contributed to ``text`` \u2014 useful for event telemetry
    and for unit tests asserting "iter 1 AND iter 2 both present on
    iter-3 prompt."
    """

    text: str
    iterations_included: list[int]


def build_cumulative_history(
    reviews_dir: Path,
    reports_dir: Path,
    spec_name: str,
    current_iteration: int,
) -> HistoryBuildResult:
    """Concatenate feedback + classifier rationales for every prior iteration.

    Reads:
      ``{reviews_dir}/{spec_name}-feedback-iter{N}.md`` for N in 1..current-1
      ``{reports_dir}/{spec_name}-defects-iter{N}.json`` for the same N

    Returns empty on iter <= 1 (no history yet).

    Truncates to ``_HISTORY_MAX_CHARS`` with a clear notice; preserves
    the most-recent iteration in full since it's most relevant to
    iter-N decisions.
    """
    if current_iteration <= 1:
        return HistoryBuildResult(text="", iterations_included=[])

    blocks: list[str] = []
    included: list[int] = []
    for prior_n in range(1, current_iteration):
        fb_path = reviews_dir / f"{spec_name}-feedback-iter{prior_n}.md"
        defects_path = reports_dir / f"{spec_name}-defects-iter{prior_n}.json"
        fb_text = _safe_read(fb_path)
        defect_lines = _format_defect_rationales(defects_path)
        if not fb_text and not defect_lines:
            continue
        parts: list[str] = [f"### Iteration {prior_n} feedback"]
        if fb_text:
            parts.append(fb_text.strip())
        if defect_lines:
            parts.append("#### Classifier rationales (iter {n})".format(n=prior_n))
            parts.append("\n".join(defect_lines))
        blocks.append("\n\n".join(parts))
        included.append(prior_n)

    if not blocks:
        return HistoryBuildResult(text="", iterations_included=[])

    header = (
        "## Cumulative chronological iteration history\n\n"
        "Below is the feedback and classifier output from every prior "
        "iteration in order. Read this before starting \u2014 repeated "
        "failures on the same anchor across iterations are a signal "
        "that a local patch is not enough.\n"
    )
    joined = header + "\n\n" + "\n\n---\n\n".join(blocks)
    if len(joined) > _HISTORY_MAX_CHARS:
        # Preserve the header + the most recent block intact; truncate
        # older blocks. This matters more than proportional trimming
        # because iter N-1 drives the preamble decision most directly.
        most_recent = blocks[-1]
        remaining = _HISTORY_MAX_CHARS - len(header) - len(most_recent) - 200
        older = "\n\n---\n\n".join(blocks[:-1])
        if remaining > 0 and older:
            older = older[:remaining] + (
                "\n\n... [earlier history truncated; see "
                ".dark-factory/reviews/ for full files] ..."
            )
            joined = header + "\n\n" + older + "\n\n---\n\n" + most_recent
        else:
            joined = header + "\n\n" + most_recent
    return HistoryBuildResult(text=joined, iterations_included=included)


def build_iter_diff(
    project_dir: Path,
    source_dirs: list[str],
    *,
    iteration: int,
) -> str:
    """Produce a unified diff of the prior iteration's implementer changes.

    Uses ``git diff HEAD~1 HEAD`` scoped to ``source_dirs``. Caller is
    responsible for ensuring the iteration-boundary commit exists (the
    iteration loop already does per-iter snapshots for rollback
    support). If the diff cannot be produced, returns an empty string
    rather than raising \u2014 we'd rather lose one signal channel
    than block the whole implementer invocation.
    """
    if iteration <= 1 or not source_dirs:
        return ""
    try:
        cmd = ["git", "diff", "HEAD~1", "HEAD", "--"] + list(source_dirs)
        proc = subprocess.run(
            cmd, cwd=project_dir, capture_output=True, text=True,
            timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("iter diff unavailable: %s", exc)
        return ""
    if proc.returncode != 0:
        log.warning("iter diff exit %d stderr=%r", proc.returncode, proc.stderr[:200])
        return ""
    diff = proc.stdout
    if not diff.strip():
        return ""
    if len(diff) > _DIFF_MAX_CHARS:
        diff = diff[:_DIFF_MAX_CHARS] + (
            "\n\n... [diff truncated at {n} chars; see ``git diff "
            "HEAD~1 HEAD`` for full output] ...".format(n=_DIFF_MAX_CHARS)
        )
    return (
        "## Diff of your iter-{prev} changes\n\n"
        "The following diff shows what you wrote on the previous "
        "iteration. Review it before making new changes \u2014 repeated "
        "patches to the same lines are a signal that the previous "
        "approach was wrong at a deeper level.\n\n"
        "```diff\n{diff}\n```\n"
    ).format(prev=iteration - 1, diff=diff)


def audit_history_for_barrier_leak(
    history_text: str,
    *,
    holdouts_dir: Path,
    scenarios_dir: Path,
) -> list[str]:
    """Assert history contains no cross-barrier content.

    Returns a list of violation strings \u2014 empty means clean.
    Violations are not raised; the caller decides whether to fall back
    to dedupe/filtering (still cleaner than silently leaking).

    Checks:
      1. No filename of a holdout test file (e.g. ``rls-boundary-conditions
         .test.ts``) appears in ``history_text``.
      2. No path prefix outside the scenarios dir or the reviews dir appears
         as a ``/project/...`` reference in the history.

    Intentionally narrow: cadence/frequency/classification distribution
    are considered legitimate signal, not leaks. See plan-revision
    barrier threat model note.
    """
    violations: list[str] = []
    if holdouts_dir.is_dir():
        for hf in holdouts_dir.rglob("*"):
            if hf.is_file() and hf.name in history_text:
                violations.append(
                    f"holdout filename leaked into history: {hf.name}"
                )
    # Path-prefix check: any ``/project/.dark-factory/holdouts/`` or
    # ``/project/.dark-factory/reports/`` reference is a hard leak.
    for forbidden in (
        "/.dark-factory/holdouts/",
        "/.dark-factory/reports/",
        "/.dark-factory/failures/",
        "/.dark-factory/audit/",
        "/.dark-factory/state/",
    ):
        if forbidden in history_text:
            violations.append(f"forbidden path prefix present: {forbidden}")
    return violations


def _safe_read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _format_defect_rationales(defects_path: Path) -> list[str]:
    """Format classifier defects as one-line rationale bullets.

    Intentionally lossy \u2014 we want ``anchor_ids | category | one-line
    rationale``, not the full per-test dict. Keeps the history readable
    when there are 20+ defects per iteration.
    """
    raw = _safe_read(defects_path)
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    defects = data.get("defects") or []
    if not isinstance(defects, list):
        return []
    lines: list[str] = []
    for d in defects:
        if not isinstance(d, dict):
            continue
        anchors = d.get("cited_anchor_ids") or []
        if isinstance(anchors, list):
            anchor_str = ",".join(str(a) for a in anchors) or "?"
        else:
            anchor_str = str(anchors)
        category = str(d.get("category") or "?")
        rationale = str(d.get("rationale") or "").strip()
        rationale = re.sub(r"\s+", " ", rationale)
        if len(rationale) > 240:
            rationale = rationale[:237] + "..."
        lines.append(f"- [{anchor_str}] {category}: {rationale}")
    return lines
