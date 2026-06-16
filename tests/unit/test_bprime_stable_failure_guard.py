"""B' (2026-06-16): eligible-NO-SHIP stable-failure terminal guard.

Run 2f013c56 (bb monorepo-tooling) terminated STABLE_FAILURE at iter-2 of 3:
the Oracle declined a ship-with-gaps-eligible run because an actionable
structural gate (Import Contract: missing root tsconfig) and an FR-3 gap were
still open — and the Oracle's own output said iter-3 would close them and
reach APPROVE. But the eligible-NO-SHIP terminal (``_step_oracle``) fired
regardless of *why* the Oracle declined, denying iter-3.

These tests exercise ``_stable_failure_terminal_suppressed_by_actionable_gate``
— the pure predicate the terminal calls — in isolation, for the same reason
``test_b1_trajectory_escalation`` tests its predicates directly: the predicate
is pure and the terminal that wires it is a general path that ANY
ship-with-gaps-eligible NO-SHIP with an open structural gate reaches (that is
the path the run-2f013c56 bug report came from). Unit-testing the predicate is
about hermeticity (no orchestrator/LLM mocking), not because production won't
hit it.

The ``_effective_max_iterations`` tests pin the ceiling SOURCE: the guard must
use the same ceiling the loop uses, or it reasons against the wrong number
(Linus B' review).
"""
from __future__ import annotations

from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import SpecRun
from darkfactory.pipeline.iteration import (
    _effective_max_iterations,
    _stable_failure_terminal_suppressed_by_actionable_gate as _suppressed,
)


class TestStableFailureTerminalGuard:
    # --- core 3 cases (Neo design) -------------------------------------

    def test_actionable_gate_open_and_iterations_remain_reiterates(self):
        """Import Contract FAIL at iter-2 of 3 → suppress terminal, re-loop.
        This is the exact run-2f013c56 case."""
        assert _suppressed(
            iteration=2, max_iterations=3,
            import_contract_status="FAIL",
            substrate_verification_status="",
        )

    def test_gates_clean_terminates(self):
        """Pure test-side plateau — structural gates clean → terminal fires
        (unchanged legacy behaviour)."""
        assert not _suppressed(
            iteration=2, max_iterations=3,
            import_contract_status="PASS",
            substrate_verification_status="",
        )

    def test_actionable_gate_open_but_iterations_exhausted_terminates(self):
        """Import Contract FAIL on the FINAL iteration → terminate honestly
        (no infinite loop). The load-bearing iteration<max clause."""
        assert not _suppressed(
            iteration=3, max_iterations=3,
            import_contract_status="FAIL",
            substrate_verification_status="",
        )

    # --- variants ------------------------------------------------------

    def test_substrate_verification_fail_also_reiterates(self):
        assert _suppressed(
            iteration=2, max_iterations=3,
            import_contract_status="PASS",
            substrate_verification_status="FAIL",
        )

    def test_both_structural_gates_pass_terminates(self):
        assert not _suppressed(
            iteration=2, max_iterations=3,
            import_contract_status="PASS",
            substrate_verification_status="PASS",
        )

    def test_skip_status_is_not_fail_terminates(self):
        """SKIP (no command configured) is not an actionable failure."""
        assert not _suppressed(
            iteration=2, max_iterations=3,
            import_contract_status="SKIP",
            substrate_verification_status="SKIP",
        )

    def test_empty_status_is_not_fail_terminates(self):
        """Unparseable/missing gate status must not be read as actionable."""
        assert not _suppressed(
            iteration=2, max_iterations=3,
            import_contract_status="",
            substrate_verification_status="",
        )

    def test_iteration_past_max_terminates(self):
        """Defensive: iteration already at/over max never re-loops."""
        assert not _suppressed(
            iteration=4, max_iterations=3,
            import_contract_status="FAIL",
            substrate_verification_status="FAIL",
        )

    def test_single_iteration_budget_terminates_at_iter1(self):
        """max_iterations=1: even with a fail, no iteration remains."""
        assert not _suppressed(
            iteration=1, max_iterations=1,
            import_contract_status="FAIL",
            substrate_verification_status="",
        )


class TestEffectiveMaxIterationsCeiling:
    """Pins the ceiling SOURCE the guard must use (Linus B' review). The guard
    must reason against ``effective_max_iterations or config.max_iterations`` —
    the same value the iteration loop bounds on — NOT raw config.max_iterations,
    which dynamic-iterations can exceed."""

    def _spec(self, effective: int) -> SpecRun:
        s = SpecRun(spec_name="x", run_id="y")
        s.effective_max_iterations = effective
        return s

    def test_unset_falls_back_to_config(self):
        assert _effective_max_iterations(self._spec(0), PipelineConfig()) == 3

    def test_dynamic_lowers_below_config(self):
        # enterprise dynamic-iterations predicted a lower ceiling
        assert _effective_max_iterations(self._spec(2), PipelineConfig()) == 2

    def test_guard_terminates_on_dynamic_final_iteration(self):
        """The exact bug Linus caught: effective=2, config=3, on the real final
        iteration (2). With the wrong ceiling (config=3) the guard would
        suppress (2<3) and route to escalation; with the correct ceiling (2)
        it terminates honestly as STABLE_FAILURE."""
        spec, cfg = self._spec(2), PipelineConfig()  # cfg.max_iterations == 3
        ceiling = _effective_max_iterations(spec, cfg)
        assert ceiling == 2
        assert not _suppressed(
            iteration=2, max_iterations=ceiling,
            import_contract_status="FAIL",
            substrate_verification_status="",
        )
        # sanity: the OLD (buggy) ceiling would have wrongly suppressed
        assert _suppressed(
            iteration=2, max_iterations=cfg.max_iterations,
            import_contract_status="FAIL",
            substrate_verification_status="",
        )
