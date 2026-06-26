"""Unit tests for B1 — refined trajectory escalation.

Covers the ``_b1_implementer_idle`` helper (git-diff-driven effort
detection) and the two policy changes it feeds:

1. iter-2 + PLATEAU + stable failing set + NON-IDLE diff → early
   escalate (save one iteration of budget).
2. iter-3+ + PLATEAU + IDLE diff → suppress trajectory escalation;
   let the Oracle decide on merits.

We test the helper by exercising its inputs against a real
``tmp_path`` project layout; we test the policy by driving the
iteration loop's decision surface directly, not by standing up a
full pipeline run (which would duplicate ``test_iteration.py``'s
integration coverage).
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.models.satisfaction import TrajectoryType
from darkfactory.pipeline.iteration import (
    _b1_implementer_idle,
    _b1_should_early_escalate,
    _final_attempt_made_no_progress,
    _should_trajectory_escalate_iter3,
    _b1_should_suppress_iter3_plateau,
)


# ---------------------------------------------------------------------------
# _b1_implementer_idle helper
# ---------------------------------------------------------------------------


class TestImplementerIdleSignal:
    def _mk_spec(self, shas: list[str]) -> SpecRun:
        """Build a SpecRun with N iterations whose end SHAs are ``shas``."""
        spec = SpecRun(spec_name="spec-x")
        for i, sha in enumerate(shas, start=1):
            spec.iterations.append(IterationState(number=i, iteration_end_sha=sha))
        return spec

    def test_single_iteration_returns_none(self, tmp_path: Path):
        """B1 cannot decide on iter-1 — no prior iteration to diff against."""
        spec = self._mk_spec(["abc123"])
        assert _b1_implementer_idle(spec, tmp_path) is None

    def test_dry_run_returns_none(self, tmp_path: Path):
        """Dry-run bypasses git entirely; SHAs are empty by contract."""
        spec = self._mk_spec(["", ""])
        assert _b1_implementer_idle(spec, tmp_path, dry_run=True) is None

    def test_missing_prior_sha_returns_none(self, tmp_path: Path):
        spec = self._mk_spec(["", "head123"])
        assert _b1_implementer_idle(spec, tmp_path) is None

    def test_missing_current_sha_returns_none(self, tmp_path: Path):
        spec = self._mk_spec(["prev123", ""])
        assert _b1_implementer_idle(spec, tmp_path) is None

    def test_git_diff_nonzero_exit_returns_none(self, tmp_path: Path):
        """When git fails (e.g., invalid ref, not a repo), degrade to
        None so the existing escalation policy is preserved — B1 never
        changes policy based on a broken signal."""
        spec = self._mk_spec(["prev123", "head456"])

        class _Result:
            exit_code = 128
            stdout = ""
            stderr = "fatal: bad revision"

        with patch(
            "darkfactory.utils.subprocess_runner.run",
            return_value=_Result(),
        ):
            assert _b1_implementer_idle(spec, tmp_path) is None

    def test_empty_diff_means_idle(self, tmp_path: Path):
        spec = self._mk_spec(["prev123", "head456"])

        class _Result:
            exit_code = 0
            stdout = "   \n"  # whitespace-only → treated as empty
            stderr = ""

        with patch(
            "darkfactory.utils.subprocess_runner.run",
            return_value=_Result(),
        ):
            assert _b1_implementer_idle(spec, tmp_path) is True

    def test_nonempty_diff_means_not_idle(self, tmp_path: Path):
        spec = self._mk_spec(["prev123", "head456"])

        class _Result:
            exit_code = 0
            stdout = "src/app.ts\nsrc/util.ts\n"
            stderr = ""

        with patch(
            "darkfactory.utils.subprocess_runner.run",
            return_value=_Result(),
        ):
            assert _b1_implementer_idle(spec, tmp_path) is False

    def test_diff_uses_last_two_iterations(self, tmp_path: Path):
        """With >2 iterations, only the most recent prior/current pair
        matter. Catches a regression where a loop over ``iterations``
        would accidentally compare wrong SHAs."""
        spec = self._mk_spec(["a", "b", "c"])

        captured: list[list[str]] = []

        class _Result:
            exit_code = 0
            stdout = ""
            stderr = ""

        def _fake_run(cmd, timeout):  # noqa: ANN001 — test shim
            captured.append(list(cmd))
            return _Result()

        with patch(
            "darkfactory.utils.subprocess_runner.run",
            side_effect=_fake_run,
        ):
            _b1_implementer_idle(spec, tmp_path)

        # The diff arg should be ``b..c`` (iterations[-2] to iterations[-1]).
        diff_arg = next(a for a in captured[0] if ".." in a)
        assert diff_arg == "b..c"


# ---------------------------------------------------------------------------
# Escalation policy: exercise the *production* predicates directly
# ---------------------------------------------------------------------------
#
# These tests import ``_b1_should_early_escalate`` and
# ``_b1_should_suppress_iter3_plateau`` from ``iteration.py`` — the same
# predicates the iteration loop calls. The escalation site at the loop's
# trajectory block wires them in a single place, so if the production
# condition changes, these tests see it too. No mirror functions here.


class TestB1EarlyEscalationDecision:
    """iter-2 PLATEAU + stable + NOT idle + final attempt made progress → escalate."""

    def test_fires_on_exact_conditions(self):
        assert _b1_should_early_escalate(
            ship_with_gaps=False, iteration=2, max_iterations=3,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=False,
            final_attempt_no_progress=False,
        )

    def test_suppressed_at_final_iteration_when_max_is_2(self):
        # RC6 completion (Linus+Codex flagged): under max_iterations==2, iter-2
        # IS the final iteration → B1 must defer to the oracle, not pre-empt it
        # (the same premature-escalation bug RC6 closed at the iter>=3 site).
        assert not _b1_should_early_escalate(
            ship_with_gaps=False, iteration=2, max_iterations=2,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=False,
            final_attempt_no_progress=False,
        )

    def test_suppressed_when_ship_with_gaps(self):
        assert not _b1_should_early_escalate(
            ship_with_gaps=True, iteration=2, max_iterations=3,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=False,
            final_attempt_no_progress=False,
        )

    def test_suppressed_when_idle_true(self):
        """Idle implementer at iter-2: don't early-escalate, the case
        belongs to the iter-3 didn't-try branch."""
        assert not _b1_should_early_escalate(
            ship_with_gaps=False, iteration=2, max_iterations=3,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=True,
            final_attempt_no_progress=False,
        )

    def test_suppressed_when_idle_unknown(self):
        """``idle=None`` signals ``preserve existing policy`` — B1 must
        NOT fire on an unavailable signal, or we'd escalate early on
        first-iter dry-runs etc."""
        assert not _b1_should_early_escalate(
            ship_with_gaps=False, iteration=2, max_iterations=3,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=None,
            final_attempt_no_progress=False,
        )

    def test_suppressed_when_failing_set_not_stable(self):
        """Without a stable failing set, the cheap-exit rationale
        doesn't hold — the implementer's effort might yet fix things."""
        assert not _b1_should_early_escalate(
            ship_with_gaps=False, iteration=2, max_iterations=3,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=False, implementer_idle=False,
            final_attempt_no_progress=False,
        )

    def test_suppressed_on_non_plateau_trajectories(self):
        """Non-PLATEAU trajectories are explicitly unchanged by B1."""
        for traj in [
            TrajectoryType.DEGRADING,
            TrajectoryType.OSCILLATING,
            TrajectoryType.IMPROVING,
            TrajectoryType.RECOVERING,
            TrajectoryType.MIXED,
        ]:
            assert not _b1_should_early_escalate(
                ship_with_gaps=False, iteration=2, max_iterations=3,
                trajectory=traj, stable_failing_set=True,
                implementer_idle=False,
                final_attempt_no_progress=False,
            ), f"B1 iter-2 escalation must not fire on {traj.value}"

    def test_suppressed_when_final_attempt_made_no_progress(self):
        """RC5 (run-427991975dc744bf): all other firing conditions hold,
        but the iteration's final implementer attempt added zero net new
        files (a timeout/compaction bail). The 'implementer tried, same
        tests fail -> test-side defect' premise is false — the run never
        delivered a real fix attempt — so B1 must NOT early-escalate and
        must leave the remaining iteration to apply the diagnosed fix."""
        assert not _b1_should_early_escalate(
            ship_with_gaps=False, iteration=2, max_iterations=3,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=False,
            final_attempt_no_progress=True,
        )

    def test_does_not_fire_at_iter_1(self):
        assert not _b1_should_early_escalate(
            ship_with_gaps=False, iteration=1, max_iterations=3,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=False,
            final_attempt_no_progress=False,
        )

    def test_does_not_fire_at_iter_3_or_later(self):
        """Iter-3+ is covered by the existing escalation rule, not B1
        iter-2 early exit. Keeping the iter-2 branch strictly ``==``
        avoids double-firing."""
        for i in [3, 4, 5]:
            assert not _b1_should_early_escalate(
                ship_with_gaps=False, iteration=i, max_iterations=3,
                trajectory=TrajectoryType.PLATEAU,
                stable_failing_set=True, implementer_idle=False,
                final_attempt_no_progress=False,
            )


class TestFinalAttemptMadeNoProgress:
    """The shared exhaustion predicate behind the Phase-E effort gate AND
    B1's RC5 suppressor — ``>= 2`` attempts whose last two cumulative
    file-change counts are equal."""

    @staticmethod
    def _iter(counts):
        return SimpleNamespace(attempt_file_counts=list(counts))

    def test_none_iter(self):
        assert _final_attempt_made_no_progress(None) is False

    def test_single_attempt_is_progress(self):
        # No prior attempt to compare against — trivially "made progress".
        assert _final_attempt_made_no_progress(self._iter([6])) is False

    def test_zero_attempts(self):
        assert _final_attempt_made_no_progress(self._iter([])) is False

    def test_final_attempt_zero_net_new(self):
        # run-427991975dc744bf: att1 wrote 6 files, att2 (timeout) 0 new.
        assert _final_attempt_made_no_progress(self._iter([6, 6])) is True

    def test_final_attempt_made_progress(self):
        assert _final_attempt_made_no_progress(self._iter([6, 9])) is False

    def test_uses_last_two_only(self):
        # Earlier equal pair does not count; only the final two attempts.
        assert _final_attempt_made_no_progress(self._iter([4, 4, 7])) is False
        assert _final_attempt_made_no_progress(self._iter([4, 7, 7])) is True


class TestB1Iter3Suppression:
    """iter-3+ + PLATEAU + idle → suppress trajectory escalation."""

    def test_suppresses_when_plateau_and_idle(self):
        assert _b1_should_suppress_iter3_plateau(
            trajectory=TrajectoryType.PLATEAU, implementer_idle=True,
        )

    def test_does_not_suppress_non_plateau(self):
        for traj in [
            TrajectoryType.DEGRADING,
            TrajectoryType.OSCILLATING,
            TrajectoryType.MIXED,
        ]:
            assert not _b1_should_suppress_iter3_plateau(
                trajectory=traj, implementer_idle=True,
            ), (
                f"{traj.value}: B1 must not suppress non-PLATEAU escalations "
                f"even when implementer is idle — those categories have "
                f"different semantics than 'didn't try'"
            )

    def test_does_not_suppress_when_not_idle(self):
        """Idle=False at iter-3+ is the normal escalation path: the
        implementer tried but the trajectory is still bad. Existing
        rule applies."""
        assert not _b1_should_suppress_iter3_plateau(
            trajectory=TrajectoryType.PLATEAU, implementer_idle=False,
        )

    def test_does_not_suppress_when_idle_unknown(self):
        """``idle=None``: preserve existing policy; escalate as today."""
        assert not _b1_should_suppress_iter3_plateau(
            trajectory=TrajectoryType.PLATEAU, implementer_idle=None,
        )


class TestShouldTrajectoryEscalateIter3:
    """RC6 (run-6e2e4fb8): the iter>=3 trajectory escalation must defer to the
    oracle on the FINAL iteration and on any PASSING-gate plateau — finishing
    the RC5 job at the sibling pre-oracle escalation site."""

    def _kw(self, **over):
        kw = dict(
            ship_with_gaps=False,
            iteration=3,
            max_iterations=5,          # a future iteration remains by default
            trajectory=TrajectoryType.PLATEAU,
            hard_gates_pass=False,     # not a passing-gate plateau by default
            pass_rate=40,
            min_ship_pass_rate=80,
        )
        kw.update(over)
        return kw

    def test_fires_on_bad_trajectory_with_future_iteration_and_failing_gate(self):
        # PLATEAU at iter3, room to save (3 < 5), gate not passing -> escalate.
        assert _should_trajectory_escalate_iter3(**self._kw())

    def test_guard_a_final_iteration_defers_to_oracle(self):
        # iteration == max_iterations: no budget to save -> oracle owns the verdict.
        assert not _should_trajectory_escalate_iter3(**self._kw(iteration=3, max_iterations=3))
        assert not _should_trajectory_escalate_iter3(**self._kw(iteration=5, max_iterations=5))

    def test_guard_b_passing_gate_plateau_defers_to_oracle(self):
        # Hard gates pass + pass_rate >= floor: the PLATEAU is at the ceiling, not
        # a stuck implementer -> defer to the oracle even with iterations remaining.
        assert not _should_trajectory_escalate_iter3(
            **self._kw(hard_gates_pass=True, pass_rate=100, max_iterations=5)
        )

    def test_run_6e2e4fb8_exact_case_no_escalation(self):
        # The bug: PLATEAU, iter3 == max_iters 3, passing gate (100%). Blocked by
        # BOTH guards -> the oracle must evaluate the final gate-passing code.
        assert not _should_trajectory_escalate_iter3(
            **self._kw(iteration=3, max_iterations=3, hard_gates_pass=True, pass_rate=100)
        )

    def test_ship_with_gaps_defers(self):
        assert not _should_trajectory_escalate_iter3(**self._kw(ship_with_gaps=True))

    def test_below_iter3_does_not_fire(self):
        assert not _should_trajectory_escalate_iter3(**self._kw(iteration=2))

    def test_non_escalation_trajectory_does_not_fire(self):
        for traj in (TrajectoryType.IMPROVING, TrajectoryType.RECOVERING):
            assert not _should_trajectory_escalate_iter3(**self._kw(trajectory=traj))

    def test_escalation_trajectories_all_fire(self):
        for traj in (
            TrajectoryType.DEGRADING,
            TrajectoryType.OSCILLATING,
            TrajectoryType.PLATEAU,
        ):
            assert _should_trajectory_escalate_iter3(**self._kw(trajectory=traj))

    def test_passing_gate_needs_BOTH_hard_gates_and_pass_rate(self):
        # hard gates pass but pass_rate below floor -> still escalates (not a clean
        # ceiling-plateau); pass_rate high but hard gates failing -> still escalates.
        assert _should_trajectory_escalate_iter3(**self._kw(hard_gates_pass=True, pass_rate=50))
        assert _should_trajectory_escalate_iter3(**self._kw(hard_gates_pass=False, pass_rate=100))
