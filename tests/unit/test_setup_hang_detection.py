"""Tests for setup-phase hang detection (Task 45).

The quality-gate bash script wraps ``setup_cmd`` under ``timeout`` and, on
expiration, emits a distinct ``<!-- SETUP_HANG=true -->`` sentinel in the
gate summary. The iteration loop reads this sentinel to route consecutive
hangs to ENV_ISSUE escalation at threshold=1 — the Implementer cannot fix
a blocked migration from inside its writable scope.

These tests cover:

* Sentinel detection helper (``_gate_summary_has_setup_hang``) — pure,
  easy to exercise directly.
* ``SpecRun.consecutive_setup_hangs`` field exists and defaults to 0.

The in-loop escalation wiring is exercised by the pipeline integration
tests; the interaction with ``run_iteration_loop`` is too coupled to
justify a unit-test harness of its own.
"""

from __future__ import annotations

from pathlib import Path

from darkfactory.models.pipeline import SpecRun
from darkfactory.pipeline.iteration import _gate_summary_has_setup_hang


def _write_summary(project_dir: Path, spec_name: str, body: str) -> Path:
    reports = project_dir / ".dark-factory" / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    summary = reports / f"{spec_name}-quality-gate-summary.md"
    summary.write_text(body, encoding="utf-8")
    return summary


def test_detector_true_when_sentinel_present(tmp_path: Path) -> None:
    _write_summary(
        tmp_path,
        "spec-x",
        "| Setup | HANG |\n<!-- END GATE TABLE -->\n<!-- SETUP_HANG=true -->\n",
    )
    assert _gate_summary_has_setup_hang(tmp_path, "spec-x") is True


def test_detector_false_on_clean_setup_fail(tmp_path: Path) -> None:
    """A SETUP_FAILURE sentinel must not be misread as a hang."""
    _write_summary(
        tmp_path,
        "spec-x",
        "| Setup | FAIL |\n<!-- END GATE TABLE -->\n<!-- SETUP_FAILURE=true -->\n",
    )
    assert _gate_summary_has_setup_hang(tmp_path, "spec-x") is False


def test_detector_false_on_pass(tmp_path: Path) -> None:
    _write_summary(tmp_path, "spec-x", "| Setup | PASS |\n")
    assert _gate_summary_has_setup_hang(tmp_path, "spec-x") is False


def test_detector_false_when_summary_missing(tmp_path: Path) -> None:
    assert _gate_summary_has_setup_hang(tmp_path, "no-such-spec") is False


def test_detector_ignores_bare_string_in_captured_output(tmp_path: Path) -> None:
    """setup_cmd output that happens to contain ``SETUP_HANG=true`` must
    not trigger the sentinel. The detector matches the full HTML-comment
    form, so captured stdout cannot spoof it.
    """
    _write_summary(
        tmp_path,
        "spec-x",
        "| Setup | FAIL |\n"
        "<!-- END GATE TABLE -->\n"
        "### Setup Failure Details\n"
        "```\n"
        "error: unknown env var SETUP_HANG=true in migration config\n"
        "```\n"
        "<!-- SETUP_FAILURE=true -->\n",
    )
    assert _gate_summary_has_setup_hang(tmp_path, "spec-x") is False


def test_spec_run_default_setup_hangs_is_zero() -> None:
    sr = SpecRun(spec_name="x")
    assert sr.consecutive_setup_hangs == 0


# ---------------------------------------------------------------------------
# Counter-reset semantics
# ---------------------------------------------------------------------------
#
# The field docstring promises: "Resets to 0 on any iteration whose gate
# does NOT carry the sentinel". The tests below exercise the three scenarios
# that most often leak through a naive implementation:
#
#   HANG → HANG                — counter reaches 2, escalation triggered.
#   HANG → clean → HANG        — counter resets between the two hangs;
#                                 only one is current; no escalation.
#   HANG → non-hang-fail       — counter resets (non-hang failure is
#                                 still "no sentinel present").
#
# These assertions simulate only the detect-and-update block; the
# integration with run_iteration_loop is exercised end-to-end by the
# pipeline integration tests.


def _apply_hang_tick(spec_run: SpecRun, project_dir: Path) -> None:
    """Mirror the increment/reset logic from run_iteration_loop."""
    if _gate_summary_has_setup_hang(project_dir, spec_run.spec_name):
        spec_run.consecutive_setup_hangs += 1
    else:
        spec_run.consecutive_setup_hangs = 0


def test_two_consecutive_hangs_reach_escalation_threshold(tmp_path: Path) -> None:
    sr = SpecRun(spec_name="s")
    hang = "| Setup | HANG |\n<!-- SETUP_HANG=true -->\n"

    _write_summary(tmp_path, "s", hang)
    _apply_hang_tick(sr, tmp_path)
    assert sr.consecutive_setup_hangs == 1

    _write_summary(tmp_path, "s", hang)
    _apply_hang_tick(sr, tmp_path)
    assert sr.consecutive_setup_hangs == 2


def test_clean_iteration_between_hangs_resets_counter(tmp_path: Path) -> None:
    """HANG → clean setup → HANG: the counter must read 1 after the
    second hang, not 2. Without the reset this would spuriously escalate
    after any cumulative pair of hangs."""
    sr = SpecRun(spec_name="s")

    _write_summary(tmp_path, "s", "| Setup | HANG |\n<!-- SETUP_HANG=true -->\n")
    _apply_hang_tick(sr, tmp_path)
    assert sr.consecutive_setup_hangs == 1

    _write_summary(tmp_path, "s", "| Setup | PASS |\n")
    _apply_hang_tick(sr, tmp_path)
    assert sr.consecutive_setup_hangs == 0

    _write_summary(tmp_path, "s", "| Setup | HANG |\n<!-- SETUP_HANG=true -->\n")
    _apply_hang_tick(sr, tmp_path)
    assert sr.consecutive_setup_hangs == 1


def test_non_hang_build_failure_resets_counter(tmp_path: Path) -> None:
    """HANG → SETUP_FAILURE (non-hang): counter resets. The two failure
    modes are different root causes; consecutive count must track only
    the hang mode."""
    sr = SpecRun(spec_name="s")

    _write_summary(tmp_path, "s", "| Setup | HANG |\n<!-- SETUP_HANG=true -->\n")
    _apply_hang_tick(sr, tmp_path)
    assert sr.consecutive_setup_hangs == 1

    _write_summary(tmp_path, "s", "| Setup | FAIL |\n<!-- SETUP_FAILURE=true -->\n")
    _apply_hang_tick(sr, tmp_path)
    assert sr.consecutive_setup_hangs == 0
