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
from unittest.mock import patch

import pytest

from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.models.satisfaction import TrajectoryType
from darkfactory.pipeline.iteration import (
    _b1_implementer_idle,
    _b1_should_early_escalate,
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
    """iter-2 PLATEAU + stable + NOT idle → escalate."""

    def test_fires_on_exact_conditions(self):
        assert _b1_should_early_escalate(
            ship_with_gaps=False, iteration=2,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=False,
        )

    def test_suppressed_when_ship_with_gaps(self):
        assert not _b1_should_early_escalate(
            ship_with_gaps=True, iteration=2,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=False,
        )

    def test_suppressed_when_idle_true(self):
        """Idle implementer at iter-2: don't early-escalate, the case
        belongs to the iter-3 didn't-try branch."""
        assert not _b1_should_early_escalate(
            ship_with_gaps=False, iteration=2,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=True,
        )

    def test_suppressed_when_idle_unknown(self):
        """``idle=None`` signals ``preserve existing policy`` — B1 must
        NOT fire on an unavailable signal, or we'd escalate early on
        first-iter dry-runs etc."""
        assert not _b1_should_early_escalate(
            ship_with_gaps=False, iteration=2,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=None,
        )

    def test_suppressed_when_failing_set_not_stable(self):
        """Without a stable failing set, the cheap-exit rationale
        doesn't hold — the implementer's effort might yet fix things."""
        assert not _b1_should_early_escalate(
            ship_with_gaps=False, iteration=2,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=False, implementer_idle=False,
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
                ship_with_gaps=False, iteration=2,
                trajectory=traj, stable_failing_set=True,
                implementer_idle=False,
            ), f"B1 iter-2 escalation must not fire on {traj.value}"

    def test_does_not_fire_at_iter_1(self):
        assert not _b1_should_early_escalate(
            ship_with_gaps=False, iteration=1,
            trajectory=TrajectoryType.PLATEAU,
            stable_failing_set=True, implementer_idle=False,
        )

    def test_does_not_fire_at_iter_3_or_later(self):
        """Iter-3+ is covered by the existing escalation rule, not B1
        iter-2 early exit. Keeping the iter-2 branch strictly ``==``
        avoids double-firing."""
        for i in [3, 4, 5]:
            assert not _b1_should_early_escalate(
                ship_with_gaps=False, iteration=i,
                trajectory=TrajectoryType.PLATEAU,
                stable_failing_set=True, implementer_idle=False,
            )


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
