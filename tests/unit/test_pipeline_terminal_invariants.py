"""Codebase-level invariants for the iteration-loop terminal exits.

Run d717e915 aborted at preflight and left an incomplete per-run
archive because ``iteration.py`` raised bare ``DarkFactoryError`` at
the preflight-persisted and scenario-infrastructure sites. The outer
catch in ``orchestrator.py`` was ``(HardAbort, BudgetExceeded)``, so
those raises propagated past ``_handle_abort`` and no
``final/meta.json`` was ever written.

Two fixes went in:

1. The catch was broadened to ``(DarkFactoryError, BudgetExceeded)``
   so the archive handler runs for any abort-path raise.
2. The two terminal raises in ``iteration.py`` were converted to
   ``HardAbort`` and paired with ``emit_preflight_violation`` so
   Monitor tails see a structured event instead of a stdout line.

These tests lock in the second fix at the pattern level. A future
engineer who adds a new terminal abort to ``iteration.py`` and reaches
for bare ``DarkFactoryError`` will fail this test with a pointed
message about the ``HardAbort`` + event convention.

Run e5634bc6 then aborted at the scenario-infrastructure fast-fail
because Import Contract FAILed (5 declared entry points unresolvable);
``scenario_total == 0`` was a downstream symptom, not the primary
defect. The guard at ``iteration.py`` was narrowed to bypass fast-fail
when Import Contract or Substrate Verification is FAIL, preserving the
oracle signal that classifies the real root cause.
``TestScenarioInfraFastfailPredicate`` locks in that bypass and the
baseline (genuine scenario-infra breakage still aborts).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from darkfactory.pipeline.iteration import should_scenario_infra_fastfail
from darkfactory.pipeline.quality_gate import GateResult

ITERATION_PY = (
    Path(__file__).resolve().parents[2]
    / "darkfactory"
    / "pipeline"
    / "iteration.py"
)
ORCHESTRATOR_PY = (
    Path(__file__).resolve().parents[2]
    / "darkfactory"
    / "pipeline"
    / "orchestrator.py"
)


class TestIterationTerminalRaises(unittest.TestCase):
    """Iteration-loop aborts must use ``HardAbort`` + an event."""

    def test_no_bare_dark_factory_error_raises_in_iteration(self) -> None:
        # ``iteration.py`` catches ``DarkFactoryError`` raised from
        # deeper modules in three retry loops, but must never *raise*
        # ``DarkFactoryError`` itself — every terminal abort in the
        # iteration loop must be ``HardAbort`` so it also carries a
        # ``PREFLIGHT_VIOLATION`` event upstream.
        source = ITERATION_PY.read_text(encoding="utf-8")
        hits = [
            (i, line)
            for i, line in enumerate(source.splitlines(), start=1)
            if re.search(r"\braise\s+DarkFactoryError\b", line)
        ]
        self.assertEqual(
            hits, [],
            msg=(
                "iteration.py must not `raise DarkFactoryError` — use "
                "`HardAbort` with a paired `emit_preflight_violation`. "
                "See run d717e915 post-mortem and "
                "darkfactory/pipeline/iteration.py history at the "
                "preflight-persisted and scenario-infrastructure "
                f"raise sites. Offending lines: {hits}"
            ),
        )

    def test_orchestrator_catch_routes_dark_factory_error(self) -> None:
        # The outer catch in ``run_single_spec`` must include
        # ``DarkFactoryError`` (not just ``HardAbort``) so an
        # accidentally-bare raise still reaches ``_handle_abort`` and
        # the run archive captures ``final/meta.json``. Enforce the
        # token-level presence so a future refactor that narrows the
        # catch trips this test before silently regressing archive
        # bookkeeping.
        source = ORCHESTRATOR_PY.read_text(encoding="utf-8")
        self.assertIn(
            "except (DarkFactoryError, BudgetExceeded)",
            source,
            msg=(
                "orchestrator.py run_single_spec must catch "
                "(DarkFactoryError, BudgetExceeded) so every abort-"
                "path raise — HardAbort or bare DarkFactoryError — "
                "routes through _handle_abort. Run d717e915 showed "
                "the narrow (HardAbort, BudgetExceeded) catch "
                "bypasses the archive handler."
            ),
        )


class TestScenarioInfraFastfailPredicate(unittest.TestCase):
    """Scenario-infra fast-fail must bypass on upstream structural FAIL.

    Run e5634bc6 aborted here when the real root cause was Import
    Contract FAIL (5 unresolvable entry points). ``scenario_total == 0``
    was a downstream symptom — the implementer could have fixed the
    missing files on iter-2, but the guard terminated first. These
    tests lock the narrowed predicate.
    """

    def _gate_result(self, **overrides: object) -> GateResult:
        defaults: dict[str, object] = {
            "exit_code": 1,
            "scenario_total": 0,
            "sentinel_present": False,
            "import_contract_status": "",
            "substrate_verification_status": "",
        }
        defaults.update(overrides)
        return GateResult(**defaults)  # type: ignore[arg-type]

    def test_baseline_genuine_scenario_infra_failure_still_aborts(self) -> None:
        # Regression lock: the original fast-fail must still fire when
        # neither upstream gate explains the zero-parse. This is the
        # case the guard was designed for — scenarios exist on disk but
        # the runner parsed zero, likely a format mismatch.
        gate = self._gate_result()
        self.assertTrue(
            should_scenario_infra_fastfail(gate, validate_scenarios=True),
            msg=(
                "Fast-fail must still trigger when no upstream gate "
                "explains scenario_total=0 — otherwise a genuine "
                "scenario format mismatch would silently waste budget."
            ),
        )

    def test_import_contract_fail_bypasses_fastfail(self) -> None:
        # Run e5634bc6 regression: Import Contract FAIL means declared
        # entry points didn't resolve; scenarios that import the same
        # paths would also fail to load. Implementer-fixable on iter-2,
        # so let the iteration loop continue.
        gate = self._gate_result(import_contract_status="FAIL")
        self.assertFalse(
            should_scenario_infra_fastfail(gate, validate_scenarios=True),
            msg=(
                "Import Contract FAIL explains scenario_total=0; "
                "fast-fail would suppress the oracle signal that "
                "classifies the unresolved-import defect."
            ),
        )

    def test_substrate_verification_fail_bypasses_fastfail(self) -> None:
        # Substrate manifest malformed is architect-owned, but letting
        # the iteration loop continue preserves the oracle signal that
        # classifies the defect rather than hard-aborting iter-1.
        gate = self._gate_result(substrate_verification_status="FAIL")
        self.assertFalse(
            should_scenario_infra_fastfail(gate, validate_scenarios=True),
            msg=(
                "Substrate Verification FAIL indicates a structural "
                "contract defect; the iteration loop must continue so "
                "the oracle weighs it rather than hard-aborting here."
            ),
        )

    def test_exit_code_2_bypasses_fastfail(self) -> None:
        # Build/setup failure aborted before any scenario could run;
        # scenario_total=0 is expected and not diagnostic.
        gate = self._gate_result(exit_code=2)
        self.assertFalse(
            should_scenario_infra_fastfail(gate, validate_scenarios=True),
        )

    def test_empty_statuses_treated_as_non_fail(self) -> None:
        # Runs predating the Import Contract / Substrate Verification
        # phases have empty status strings. That must not be conflated
        # with FAIL — the original fast-fail still applies.
        gate = self._gate_result(
            import_contract_status="",
            substrate_verification_status="",
        )
        self.assertTrue(
            should_scenario_infra_fastfail(gate, validate_scenarios=True),
        )

    def test_sentinel_present_bypasses_fastfail(self) -> None:
        # The "SCENARIOS-NOT-APPLICABLE" sentinel intentionally signals
        # zero scenarios; fast-fail must respect that.
        gate = self._gate_result(sentinel_present=True)
        self.assertFalse(
            should_scenario_infra_fastfail(gate, validate_scenarios=True),
        )

    def test_validate_scenarios_disabled_bypasses_fastfail(self) -> None:
        gate = self._gate_result()
        self.assertFalse(
            should_scenario_infra_fastfail(gate, validate_scenarios=False),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
