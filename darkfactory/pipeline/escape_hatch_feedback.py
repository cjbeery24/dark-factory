"""Synthetic feedback for iteration-loop escape-hatch exits.

Task 2 of docs/plans/2026-04-22-iteration-steering-invariants.md.

The iteration loop has four pre-review escape hatches that skip the
entire post-gate pipeline (defect classifier, reviewer, oracle, feedback
composer) and short-circuit with ``continue`` / ``return False``:

* Priority 1.5 self-test regression (``iteration.py`` — rolls back when
  iter N-1 self-tests PASS -> iter N self-tests FAIL)
* R1 gate regression (``iteration.py`` — rolls back when iter N's
  exit code is worse OR its pass rate is lower than iter N-1's)
* B1 early escalation at iter 2
* Trajectory PLATEAU / bad trajectory at iter >= 3

Before this module existed, every one of those paths left
``reviews/<spec>-feedback-iter<N>.md`` un-written, so iter-N+1's
implementer prompt pointed at a non-existent file (or, before the
per-run cleanup invariant shipped in Task 1, a stale cross-run one).
The escalation agent had no current-iteration review to anchor on.

``write_escape_hatch_feedback`` below is called from all four sites.
It writes a barrier-clean synthetic feedback file so the next agent
always has fresh, current-run inputs — regardless of which terminal
or rollback path the iteration took.

Barrier rules
-------------
Spec-anchor vocabulary only. Pass-rate numbers, exit codes, and
rollback git refs are safe — the implementer already sees the spec
path. AC-IDs (``AC-4``, ``FR-2``, ``EC-3``) are spec anchors, also
safe. Test names, assertion messages, and file:line traces are NOT
emitted. Inlined ``feedback-iter<N-1>.md`` was produced by the oracle
under the same barrier rules, so re-inlining preserves compliance.

Numeric data is formatted as ``label: value`` key:value pairs; the
``audit.checker`` barrier scanner rejects ``<N>/<M> passed`` and
``<N>% pass`` forms, both of which this helper avoids.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from darkfactory.models.pipeline import IterationState, SpecRun

log = logging.getLogger(__name__)


# Cap for inlined prior-iteration feedback. Matches the existing
# ``_INLINE_MAX_CHARS`` used by ``_format_prior_iteration_context_inline``
# in ``iteration.py`` (duplicated here to keep this module's import
# graph thin — a barrier-sensitive helper should not import the 4k-line
# iteration module). Telescoping iter-N synthetic files would otherwise
# compound prior-iter payloads into unbounded prompt budget.
_INLINE_MAX_CHARS = 8_000


# Valid ``reason`` discriminators for this helper. Matching the plan's
# vocabulary exactly — each reason maps 1:1 to one escape hatch call
# site. Any other value raises rather than silently writing a
# misattributed feedback file.
REASON_SELF_TEST_REGRESSION = "SELF_TEST_REGRESSION"
REASON_GATE_REGRESSION = "GATE_REGRESSION"
REASON_B1_EARLY_ESCALATION = "B1_EARLY_ESCALATION"
REASON_TRAJECTORY_PLATEAU = "TRAJECTORY_PLATEAU"

_REGRESSION_REASONS = frozenset({
    REASON_SELF_TEST_REGRESSION, REASON_GATE_REGRESSION,
})
_EARLY_ESCALATION_REASONS = frozenset({
    REASON_B1_EARLY_ESCALATION, REASON_TRAJECTORY_PLATEAU,
})
VALID_REASONS = _REGRESSION_REASONS | _EARLY_ESCALATION_REASONS


def _feedback_path(
    project_dir: Path, spec_name: str, iteration: int,
) -> Path:
    return (
        project_dir / ".dark-factory" / "reviews"
        / f"{spec_name}-feedback-iter{iteration}.md"
    )


def _render_numeric_delta(
    iter_state: IterationState,
    prev_iter_state: Optional[IterationState],
) -> str:
    """Render the current/prior numeric block.

    Formatted as ``label: value`` pairs. Avoids ``<N>/<M> passed``
    and ``<N>% pass`` forms (both flagged by the barrier scanner in
    ``audit/checker.py``). Values are plain integers.
    """
    lines = [
        "## Iteration state",
        "",
        f"- current iter: {iter_state.number}",
        f"- exit_code: {iter_state.gate_exit_code}",
        f"- pass_rate: {iter_state.pass_rate}",
        f"- scenario_total: {iter_state.scenario_total}",
        f"- satisfaction_score: {iter_state.satisfaction_score}",
    ]
    if prev_iter_state is not None:
        lines.extend([
            "",
            f"- prior iter: {prev_iter_state.number}",
            f"- prior exit_code: {prev_iter_state.gate_exit_code}",
            f"- prior pass_rate: {prev_iter_state.pass_rate}",
            f"- prior scenario_total: {prev_iter_state.scenario_total}",
            f"- prior satisfaction_score: {prev_iter_state.satisfaction_score}",
        ])
    return "\n".join(lines)


def _render_pass_rate_history(spec_run: SpecRun) -> str:
    """Render pass-rate history as a bracketed list.

    Used in the trajectory case. The raw integer list does not match
    the barrier scanner's ``\\d+/\\d+`` or ``\\d+%`` patterns.
    """
    history = [it.pass_rate for it in spec_run.iterations]
    return f"- pass_rate history: {history}"


def _stable_failing_ac_ids(spec_run: SpecRun) -> tuple[list[str], bool]:
    """Intersection of ``failing_ac_ids`` across iterations with a
    non-empty set.

    Returns ``(sorted_ids, had_any_data)``. Iterations with empty
    ``failing_ac_ids`` (build-fail path, or iters where no AC/FR/EC
    tag could be extracted from any failing test name) are skipped —
    they contribute no data, not a wipe of the intersection. If all
    iterations have empty sets, ``had_any_data`` is False so the
    caller can distinguish "no diagnostic data" from "ACs failed
    inconsistently across iterations."

    Using intersection — not union — makes "stable" in the directive
    text honest: only ACs that failed in *every* data-carrying
    iteration are listed. An AC that failed only on iter-1 and was
    fixed by iter-3 does not appear.
    """
    populated = [
        set(it.failing_ac_ids)
        for it in spec_run.iterations
        if it.failing_ac_ids
    ]
    if not populated:
        return [], False
    stable = set.intersection(*populated)
    return sorted(stable), True


def _render_stable_failure_summary(
    spec_run: SpecRun, iter_state: IterationState,
) -> str:
    """Count + stable AC-ID intersection for the trajectory case.

    Uses ``spec_run.stable_failure_names`` only for its LENGTH (the
    count). The set itself contains test names, which are barrier
    leakage — we never emit the names themselves.

    The AC-ID list is the *intersection* across iterations with
    non-empty ``failing_ac_ids`` (see ``_stable_failing_ac_ids``) —
    the set that failed in every data-carrying iteration. An empty
    intersection over a populated set is a distinct diagnostic from
    "no data": it means iterations failed on different ACs, which
    is itself a meaningful plateau shape.
    """
    stable_acs, had_data = _stable_failing_ac_ids(spec_run)
    lines = [
        f"- stable failure count: {len(spec_run.stable_failure_names)}",
    ]
    if had_data:
        lines.append(
            f"- stable failing AC/FR/EC IDs "
            f"(intersection across data-carrying iters): {stable_acs}"
        )
        if not stable_acs:
            lines.append(
                "- note: iterations failed on disjoint AC/FR/EC sets — "
                "plateau is driven by distinct failures per iteration"
            )
    else:
        lines.append(
            "- stable failing AC/FR/EC IDs: [] "
            "(no iteration extracted AC/FR/EC tags from failing test names)"
        )
    if spec_run.trajectory_class is not None:
        lines.append(f"- trajectory: {spec_run.trajectory_class.value}")
    if spec_run.trajectory_strategy:
        lines.append(
            f"- strategy recommendation: {spec_run.trajectory_strategy}"
        )
    return "\n".join(lines)


def _render_directive(
    reason: str, rollback_ref: str, rolled_back: bool,
    prior_iter_number: Optional[int],
) -> str:
    if reason in _REGRESSION_REASONS:
        prior_clause = (
            f"iter {prior_iter_number}'s" if prior_iter_number is not None
            else "the prior iteration's"
        )
        if rolled_back and rollback_ref:
            rollback_clause = (
                f"The rollback to `{rollback_ref}` restored "
                f"{prior_clause} source tree. "
            )
        elif rollback_ref:
            rollback_clause = (
                "The rollback was attempted but failed; the working "
                "tree may still hold the regressed state. "
            )
        else:
            rollback_clause = ""
        return (
            "## Directive\n\n"
            "Your changes regressed the gate outcome relative to the "
            f"prior iteration. {rollback_clause}"
            f"Treat {prior_clause} guidance (below) as your starting "
            "point; do NOT re-introduce the rolled-back changes."
        )
    # Early-escalation path. The AC/FR/EC list above is the INTERSECTION
    # across data-carrying iterations (see ``_stable_failing_ac_ids``) —
    # the directive phrasing must match that semantics, not claim more
    # than the payload supports.
    return (
        "## Directive\n\n"
        "Iteration did not converge. This run is escalating. "
        "The spec-anchor summary above lists the AC/FR/EC IDs that "
        "failed in every data-carrying iteration of this run (or "
        "explicitly notes when iterations failed on disjoint sets). "
        "Follow-up diagnosis should focus on that set."
    )


def _read_prior_feedback(
    project_dir: Path, spec_name: str, iteration: int,
) -> Optional[str]:
    """Read ``reviews/<spec>-feedback-iter<N-1>.md`` if it exists.

    Returns the contents capped at ``_INLINE_MAX_CHARS`` with an
    explicit truncation marker, or ``None`` when the prior iteration
    did not emit a feedback file (the build-fail path at
    ``iteration.py`` ``gate_result.exit_code == 2`` skips review/oracle
    and continues without composing feedback).

    Size cap is essential: the escape-hatch synthetic file itself
    becomes the next iter's ``feedback-iter<N-1>.md``, so telescoping
    inlining could compound unbounded. A hostile iter-1 oracle payload
    in a 3-iter run would otherwise land in iter-3's prompt at 3x its
    source size. Bounded at the source with a truncation marker that
    the implementer prompt can skip past.
    """
    assert iteration >= 2, "escape hatches never fire at iter 1"
    prior = _feedback_path(project_dir, spec_name, iteration - 1)
    if not prior.exists():
        return None
    try:
        text = prior.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.warning(
            "escape_hatch_feedback: could not read prior feedback %s (%s)",
            prior, exc,
        )
        return None
    if len(text) > _INLINE_MAX_CHARS:
        text = text[:_INLINE_MAX_CHARS] + (
            f"\n\n[... truncated at {_INLINE_MAX_CHARS} chars to bound "
            "telescoping inlining ...]"
        )
    return text


def write_escape_hatch_feedback(
    spec_run: SpecRun,
    iter_state: IterationState,
    reason: str,
    project_dir: Path,
    *,
    prev_iter_state: Optional[IterationState] = None,
    rollback_ref: str = "",
    rolled_back: bool = False,
) -> tuple[Path, bool]:
    """Write synthetic feedback for iter N when an escape hatch fires.

    Returns ``(path, prior_feedback_inlined)``.
    ``prior_feedback_inlined`` is True when
    ``reviews/<spec>-feedback-iter<N-1>.md`` existed and was inlined;
    False when it was absent (prior iteration exited via the
    build-fail path or was iter 1) and only the header+delta block
    were written.

    Raises ``ValueError`` on an unknown ``reason``. Regression cases
    (``SELF_TEST_REGRESSION`` / ``GATE_REGRESSION``) require
    ``prev_iter_state`` — refusing to fabricate a comparison is
    preferable to writing garbage.
    """
    if reason not in VALID_REASONS:
        raise ValueError(
            f"unknown escape-hatch reason {reason!r}; "
            f"expected one of {sorted(VALID_REASONS)}"
        )
    if reason in _REGRESSION_REASONS and prev_iter_state is None:
        raise ValueError(
            f"reason {reason} requires prev_iter_state — regression "
            "feedback is only meaningful with a prior iteration to "
            "compare against"
        )

    header = (
        f"# Escape-hatch feedback — iter {iter_state.number}\n\n"
        f"**Reason:** `{reason}`\n\n"
        f"Spec: `.dark-factory/specs/{spec_run.spec_name}.md`\n"
    )

    sections: list[str] = [header]

    if reason in _REGRESSION_REASONS:
        sections.append(_render_numeric_delta(iter_state, prev_iter_state))
    else:
        # Early-escalation cases carry pass-rate history + stable-
        # failure summary instead of a two-row delta.
        sections.append(_render_numeric_delta(iter_state, None))
        sections.append(_render_pass_rate_history(spec_run))
        sections.append(_render_stable_failure_summary(spec_run, iter_state))

    sections.append(
        _render_directive(
            reason, rollback_ref, rolled_back,
            prev_iter_state.number if prev_iter_state is not None else None,
        )
    )

    prior_body = _read_prior_feedback(
        project_dir, spec_run.spec_name, iter_state.number,
    )
    prior_feedback_inlined = prior_body is not None
    if prior_body is not None:
        sections.append(
            "## Prior iteration feedback (inlined)\n\n" + prior_body.rstrip()
        )
    else:
        sections.append(
            "## Prior iteration feedback\n\n"
            "_No feedback file from the prior iteration is available "
            "(prior iteration exited via the build-fail path or this "
            "is iter 1). Proceed using the numeric delta and directive "
            "above._"
        )

    out = _feedback_path(project_dir, spec_run.spec_name, iter_state.number)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n\n".join(sections) + "\n", encoding="utf-8")
    log.info(
        "escape_hatch_feedback: wrote %s (reason=%s, prior_inlined=%s)",
        out, reason, prior_feedback_inlined,
    )
    return out, prior_feedback_inlined
