"""B2 (Sprint 24): clean Setup/build failures (gate exit_code==2) are routed
OUT of the code-trajectory classifier and INTO a setup-directed feedback path.

A setup abort runs no tests, so its satisfaction_score is an artifact 0 —
byte-identical to a 0%-pass test iteration. Before B2 it polluted
``score_history`` → PLATEAU → the generic "fundamentally different approach
needed" code-strategy, which misdirected the implementer (the blocker is
env/build/migration, not application logic) and burned the whole budget.

Coverage here (the loop interaction itself is too coupled to unit-test
directly — it's exercised end-to-end by the pipeline integration tests):
  * ``_extract_setup_failure_summary`` — pure summary parsing.
  * ``write_setup_failure_feedback`` — barrier-clean synthetic feedback.
  * ``SpecRun.consecutive_setup_failures`` default + counter/escalation
    semantics + the trajectory-exclusion, via a tick that mirrors the
    loop's B2 branch.
"""

from __future__ import annotations

from pathlib import Path

from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.pipeline.escape_hatch_feedback import (
    REASON_SETUP_FAILURE,
    write_setup_failure_feedback,
)
from darkfactory.pipeline.iteration import _extract_setup_failure_summary


def _write_summary(project_dir: Path, spec_name: str, body: str) -> Path:
    reports = project_dir / ".dark-factory" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    path = reports / f"{spec_name}-quality-gate-summary.md"
    path.write_text(body, encoding="utf-8")
    return path


_FENCED_SUMMARY = (
    "# Quality Gate Summary: spec-x\n"
    "| Gate | Status |\n"
    "| Install | PASS |\n"
    "| Setup | FAIL |\n"
    "<!-- END GATE TABLE -->\n"
    "\n### Setup Failure Details\n"
    "```\n"
    "drizzle.config.json file does not exist\n"
    "drizzle-kit: command not found\n"
    "```\n"
    "<!-- SETUP_FAILURE=true -->\n"
    "## Result: FAIL (setup)\n"
)


# ---------------------------------------------------------------------------
# _extract_setup_failure_summary
# ---------------------------------------------------------------------------


def test_extract_prefers_fenced_setup_block(tmp_path: Path) -> None:
    _write_summary(tmp_path, "spec-x", _FENCED_SUMMARY)
    got = _extract_setup_failure_summary(tmp_path, "spec-x")
    assert "drizzle.config.json file does not exist" in got
    assert "drizzle-kit: command not found" in got
    # Must NOT bleed in the gate table or the result trailer.
    assert "| Gate |" not in got
    assert "## Result" not in got


def test_extract_fallback_strips_gate_table_when_no_block(tmp_path: Path) -> None:
    body = (
        "| Setup | FAIL |\n"
        "<!-- END GATE TABLE -->\n"
        "alembic: connection refused to db:5432\n"
    )
    _write_summary(tmp_path, "spec-x", body)
    got = _extract_setup_failure_summary(tmp_path, "spec-x")
    assert "alembic: connection refused" in got
    assert "| Setup |" not in got


def test_extract_returns_empty_when_summary_missing(tmp_path: Path) -> None:
    assert _extract_setup_failure_summary(tmp_path, "no-such-spec") == ""


# ---------------------------------------------------------------------------
# write_setup_failure_feedback
# ---------------------------------------------------------------------------


def _iter_state(n: int) -> IterationState:
    st = IterationState(number=n)
    st.gate_exit_code = 2
    return st


def test_feedback_is_setup_directed_not_code_rethink(tmp_path: Path) -> None:
    sr = SpecRun(spec_name="spec-x")
    out = write_setup_failure_feedback(
        sr, _iter_state(1), tmp_path,
        setup_error="drizzle.config.json file does not exist",
    )
    text = out.read_text(encoding="utf-8")
    # Carries the actual setup error...
    assert "drizzle.config.json file does not exist" in text
    # ...and an explicit "this is NOT application logic" directive...
    assert "Setup/build" in text
    assert "NOT an application-logic problem" in text
    # ...and crucially does NOT deliver the misdirecting code-strategy.
    assert "fundamentally different approach" not in text.lower()
    assert REASON_SETUP_FAILURE in text
    # Filename convention: feedback-iter<N>.md (read as iter N+1's prior).
    assert out.name == "spec-x-feedback-iter1.md"


def test_feedback_truncates_runaway_setup_output(tmp_path: Path) -> None:
    sr = SpecRun(spec_name="spec-x")
    huge = "x" * 10_000
    out = write_setup_failure_feedback(
        sr, _iter_state(2), tmp_path, setup_error=huge,
    )
    text = out.read_text(encoding="utf-8")
    assert "truncated at" in text
    assert len(text) < 6_000


def test_feedback_handles_empty_setup_error(tmp_path: Path) -> None:
    sr = SpecRun(spec_name="spec-x")
    out = write_setup_failure_feedback(
        sr, _iter_state(1), tmp_path, setup_error="   ",
    )
    text = out.read_text(encoding="utf-8")
    assert "no setup error text was captured" in text


def test_feedback_avoids_pass_rate_barrier_forms(tmp_path: Path) -> None:
    """The barrier scanner rejects ``<N>/<M> passed`` / ``<N>% pass`` forms;
    the setup directive is prose + a fenced error block and must not synthesize
    either form on its own."""
    sr = SpecRun(spec_name="spec-x")
    out = write_setup_failure_feedback(
        sr, _iter_state(1), tmp_path, setup_error="ModuleNotFoundError: psycopg2",
    )
    text = out.read_text(encoding="utf-8")
    import re
    assert not re.search(r"\d+/\d+ passed", text)
    assert not re.search(r"\d+% pass", text)


# ---------------------------------------------------------------------------
# Counter / escalation / trajectory-exclusion (mirrors the loop B2 branch)
# ---------------------------------------------------------------------------


def test_spec_run_default_setup_failures_is_zero() -> None:
    assert SpecRun(spec_name="x").consecutive_setup_failures == 0


def _apply_setup_failure_tick(
    spec_run: SpecRun,
    score_history: list[int],
    *,
    gate_exit_code: int,
    satisfaction: int,
    hard_gate_build: bool = True,
    is_hang: bool = False,
) -> None:
    """Mirror the B2 branch in run_iteration_loop: a clean setup failure is
    EXCLUDED from score_history; only non-hang failures drive the counter +
    ENV_ISSUE escalation (hangs have their own counter and must not double-
    count, but are still kept out of the trajectory and do not reset the
    streak); any test-running iteration resets the counter."""
    if gate_exit_code == 2 and hard_gate_build:
        if not is_hang:
            spec_run.consecutive_setup_failures += 1
            if spec_run.consecutive_setup_failures >= 2:
                spec_run.escalation_recommended = True
                spec_run.escalation_category = "ENV_ISSUE"
        return  # NOTE: score_history is intentionally NOT appended (hang too).
    spec_run.consecutive_setup_failures = 0
    score_history.append(satisfaction)


def test_two_consecutive_setup_failures_escalate_env_issue() -> None:
    sr = SpecRun(spec_name="s")
    hist: list[int] = []
    _apply_setup_failure_tick(sr, hist, gate_exit_code=2, satisfaction=0)
    assert sr.consecutive_setup_failures == 1
    assert sr.escalation_recommended is False
    _apply_setup_failure_tick(sr, hist, gate_exit_code=2, satisfaction=0)
    assert sr.consecutive_setup_failures == 2
    assert sr.escalation_recommended is True
    assert sr.escalation_category == "ENV_ISSUE"
    assert hist == []  # never polluted the trajectory


def test_clean_iteration_between_setup_failures_resets() -> None:
    sr = SpecRun(spec_name="s")
    hist: list[int] = []
    _apply_setup_failure_tick(sr, hist, gate_exit_code=2, satisfaction=0)
    assert sr.consecutive_setup_failures == 1
    _apply_setup_failure_tick(sr, hist, gate_exit_code=1, satisfaction=40)
    assert sr.consecutive_setup_failures == 0
    _apply_setup_failure_tick(sr, hist, gate_exit_code=2, satisfaction=0)
    assert sr.consecutive_setup_failures == 1
    assert sr.escalation_recommended is False


def test_mixed_history_excludes_setup_failure_from_trajectory() -> None:
    """exit1(s=30), exit1(s=40), exit2 → score_history == [30, 40], NOT
    [30, 40, 0]. The trailing setup-failure 0 would otherwise drag an
    IMPROVING run toward PLATEAU/DEGRADING."""
    sr = SpecRun(spec_name="s")
    hist: list[int] = []
    _apply_setup_failure_tick(sr, hist, gate_exit_code=1, satisfaction=30)
    _apply_setup_failure_tick(sr, hist, gate_exit_code=1, satisfaction=40)
    _apply_setup_failure_tick(sr, hist, gate_exit_code=2, satisfaction=0)
    assert hist == [30, 40]


def test_hang_does_not_increment_clean_failure_counter() -> None:
    """A setup HANG (its own counter at the top of the loop) must not also bump
    consecutive_setup_failures — but it stays out of the trajectory and does
    NOT reset the clean-failure streak (setup is still broken)."""
    sr = SpecRun(spec_name="s")
    hist: list[int] = []
    _apply_setup_failure_tick(sr, hist, gate_exit_code=2, satisfaction=0)
    assert sr.consecutive_setup_failures == 1
    # A hang iteration: counter unchanged, no trajectory pollution.
    _apply_setup_failure_tick(
        sr, hist, gate_exit_code=2, satisfaction=0, is_hang=True,
    )
    assert sr.consecutive_setup_failures == 1
    assert sr.escalation_recommended is False
    assert hist == []
    # A following clean build failure reaches 2 → escalate (3 broken setups).
    _apply_setup_failure_tick(sr, hist, gate_exit_code=2, satisfaction=0)
    assert sr.consecutive_setup_failures == 2
    assert sr.escalation_category == "ENV_ISSUE"


def test_hard_gate_build_disabled_flows_through_to_trajectory() -> None:
    """When build is not a hard gate, an exit-2 iteration is NOT claimed by the
    B2 route (matches the loop guard) and its score is recorded normally."""
    sr = SpecRun(spec_name="s")
    hist: list[int] = []
    _apply_setup_failure_tick(
        sr, hist, gate_exit_code=2, satisfaction=0, hard_gate_build=False,
    )
    assert sr.consecutive_setup_failures == 0
    assert hist == [0]
