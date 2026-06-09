"""Tests for mechanical stable-failure SHIP override (Phase 1)."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.helpers import TempDirTestCase, make_project
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.pipeline.iteration import _step_oracle, should_ship_with_gaps
from darkfactory.pipeline.orchestrator import _write_defect_registry
from darkfactory.errors import ConfigError


def _make_config(**overrides) -> PipelineConfig:
    defaults = dict(
        model_id="claude-sonnet-4-20250514",
        source_dirs=["src"],
        max_iterations=3,
        min_ship_pass_rate=80,
        stable_failure_ship_override=True,
        stable_failure_min_ratio=0.8,
        stable_failure_min_iterations=2,
    )
    defaults.update(overrides)
    return PipelineConfig(**defaults)


def _make_spec_run(
    iteration: int = 2,
    stable_failure_ratio: float = 1.0,
    stable_failure_names: set[str] | None = None,
    ship_with_gaps: bool = False,
) -> SpecRun:
    sr = SpecRun(spec_name="test-spec")
    sr.iteration = iteration
    sr.stable_failure_ratio = stable_failure_ratio
    sr.stable_failure_names = stable_failure_names or {"test_a", "test_b"}
    sr.ship_with_gaps = ship_with_gaps
    return sr


class TestMechanicalShipOverrideConditions(unittest.TestCase):
    """Verify should_ship_with_gaps() — the actual function used in the iteration loop."""

    def test_override_fires_all_conditions_met(self) -> None:
        config = _make_config()
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        self.assertTrue(should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90))

    def test_override_fires_when_gate_exit_nonzero_but_hard_gates_pass(self) -> None:
        """The key fix: override fires when scenarios fail (exit_code=1) but hard gates pass."""
        config = _make_config()
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        # hard_gates_pass=True means Build/Lint/TypeCheck all passed
        self.assertTrue(should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=88))

    def test_override_blocked_by_low_pass_rate(self) -> None:
        config = _make_config(min_ship_pass_rate=80)
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        self.assertFalse(should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=50))

    def test_override_blocked_by_failed_hard_gates(self) -> None:
        config = _make_config()
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        self.assertFalse(should_ship_with_gaps(config, spec_run, hard_gates_pass=False, pass_rate=90))

    def test_override_blocked_when_disabled(self) -> None:
        config = _make_config(stable_failure_ship_override=False)
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        self.assertFalse(should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90))

    def test_override_blocked_by_low_iteration(self) -> None:
        config = _make_config(stable_failure_min_iterations=3)
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        self.assertFalse(should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90))

    def test_override_blocked_by_low_stable_ratio(self) -> None:
        config = _make_config(stable_failure_min_ratio=0.8)
        spec_run = _make_spec_run(iteration=2, stable_failure_ratio=0.5)
        self.assertFalse(should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90))


class TestBlockingAcVeto(unittest.TestCase):
    """Phase D: author-declared blocking ACs veto SHIP_WITH_GAPS promotion.

    should_ship_with_gaps consults ``SpecRun.ac_blocking`` against the
    last iteration's ``failing_ac_ids``. Any overlap is a veto, even
    when every other promotion condition is satisfied.
    """

    def _make_spec_run_with_failing_acs(
        self, ac_blocking: dict, failing_acs: set[str],
    ) -> SpecRun:
        sr = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        sr.ac_blocking = ac_blocking
        last_iter = IterationState(number=2)
        last_iter.failing_ac_ids = failing_acs
        sr.iterations.append(last_iter)
        return sr

    def test_blocking_ac_failing_vetoes_promotion(self) -> None:
        config = _make_config()
        spec_run = self._make_spec_run_with_failing_acs(
            ac_blocking={"AC-4": True}, failing_acs={"AC-4"},
        )
        self.assertFalse(
            should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90),
        )

    def test_non_blocking_ac_failing_allows_promotion(self) -> None:
        config = _make_config()
        spec_run = self._make_spec_run_with_failing_acs(
            ac_blocking={"AC-4": True}, failing_acs={"AC-7"},
        )
        self.assertTrue(
            should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90),
        )

    def test_empty_blocking_dict_allows_promotion(self) -> None:
        """Author declared zero blocking ACs (justification provided at
        precheck time) — veto clause is a no-op."""
        config = _make_config()
        spec_run = self._make_spec_run_with_failing_acs(
            ac_blocking={}, failing_acs={"AC-4", "AC-7"},
        )
        self.assertTrue(
            should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90),
        )

    def test_mixed_failing_with_one_blocking_vetoes(self) -> None:
        """One non-blocking failure plus one blocking failure → veto."""
        config = _make_config()
        spec_run = self._make_spec_run_with_failing_acs(
            ac_blocking={"EC-3": True}, failing_acs={"AC-7", "EC-3"},
        )
        self.assertFalse(
            should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90),
        )


class TestEffortGate(unittest.TestCase):
    """Phase E (2026-04-22): the final iteration's last implementer attempt
    must have produced net new file changes. Zero-diff final attempts are
    treated as implementer exhaustion and veto SHIP_WITH_GAPS promotion.

    Reproduces the 4736bbd9506148ef run shape: iter-2 had two attempts
    (337s then 30s). The 30-second attempt could not have meaningfully
    modified code; promotion should have been vetoed.
    """

    def _make_spec_run_with_attempt_counts(
        self, attempt_file_counts: list[int],
    ) -> SpecRun:
        sr = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        last_iter = IterationState(number=2)
        last_iter.attempt_file_counts = list(attempt_file_counts)
        sr.iterations.append(last_iter)
        return sr

    def test_zero_diff_final_attempt_vetoes_promotion(self) -> None:
        """Two attempts, final attempt added zero files beyond prior → veto.
        This is the 30s-bail signature from run 4736bbd9."""
        config = _make_config()
        spec_run = self._make_spec_run_with_attempt_counts([5, 5])
        self.assertFalse(
            should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90),
        )

    def test_nonzero_diff_final_attempt_allows_promotion(self) -> None:
        config = _make_config()
        spec_run = self._make_spec_run_with_attempt_counts([5, 8])
        self.assertTrue(
            should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90),
        )

    def test_single_attempt_passes_gate_trivially(self) -> None:
        """Iterations with one attempt have no prior to compare against;
        the effort gate must not spuriously veto them."""
        config = _make_config()
        spec_run = self._make_spec_run_with_attempt_counts([5])
        self.assertTrue(
            should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90),
        )

    def test_empty_attempt_counts_passes_gate_trivially(self) -> None:
        """Dry-run iterations skip count_files_changed, leaving the list
        empty. The gate must tolerate that and defer to the scalar rules."""
        config = _make_config()
        spec_run = self._make_spec_run_with_attempt_counts([])
        self.assertTrue(
            should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90),
        )

    def test_effort_gate_applies_only_to_final_pair(self) -> None:
        """Three attempts with a zero-diff final pair still vetoes;
        earlier non-zero deltas do not rescue promotion."""
        config = _make_config()
        spec_run = self._make_spec_run_with_attempt_counts([2, 6, 6])
        self.assertFalse(
            should_ship_with_gaps(config, spec_run, hard_gates_pass=True, pass_rate=90),
        )

    def test_blocking_ac_veto_runs_before_effort_gate(self) -> None:
        """A blocking-AC veto still fires when the effort gate would also
        fail. Order between the two doesn't matter for correctness, but
        pin behavior so a future refactor doesn't accidentally skip one."""
        config = _make_config()
        sr = _make_spec_run(iteration=2, stable_failure_ratio=1.0)
        sr.ac_blocking = {"AC-4": True}
        last_iter = IterationState(number=2)
        last_iter.failing_ac_ids = {"AC-4"}
        last_iter.attempt_file_counts = [5, 5]  # would also trigger effort gate
        sr.iterations.append(last_iter)
        self.assertFalse(
            should_ship_with_gaps(config, sr, hard_gates_pass=True, pass_rate=90),
        )


class TestDefectRegistry(TempDirTestCase):
    """Test defect registry markdown generation."""

    def test_writes_registry_file(self) -> None:
        project_dir = make_project(self.tmp_dir)
        spec_run = _make_spec_run(
            iteration=2,
            stable_failure_names={"auth.test.ts > login fails", "api.test.ts > timeout"},
            ship_with_gaps=True,
        )
        # Add an iteration state so current_iteration works.
        iter_state = IterationState(
            number=2,
            gate_exit_code=1,  # Non-zero: scenarios failed
            hard_gates_pass=True,
            pass_rate=87,
            scenario_pass_count=20,
            scenario_total=23,
        )
        spec_run.iterations.append(iter_state)

        _write_defect_registry(spec_run, project_dir)

        report_path = project_dir / ".dark-factory" / "reports" / "test-spec-defect-registry.md"
        self.assertTrue(report_path.exists())
        content = report_path.read_text()
        self.assertIn("ORACLE_WITH_GAPS", content)
        self.assertIn("87%", content)
        self.assertIn("auth.test.ts > login fails", content)
        self.assertIn("api.test.ts > timeout", content)
        self.assertIn("20/23", content)

    def _seed_defects_iter(
        self, project_dir: Path, spec_name: str, iter_num: int,
        defects: list[dict],
    ) -> None:
        """Seed a classifier output file the registry writer reads from."""
        reports = project_dir / ".dark-factory" / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / f"{spec_name}-defects-iter{iter_num}.json").write_text(
            json.dumps({"iteration": iter_num, "schema_version": 1, "defects": defects})
        )

    def test_writes_known_gaps_when_all_skippable(self) -> None:
        """ENV_ISSUE-only run produces a .known-gaps.json alongside the markdown."""
        project_dir = make_project(self.tmp_dir)
        spec_run = _make_spec_run(
            iteration=2,
            stable_failure_names={
                "[AC-2] npm install rejected by engine",
                "[EC-1] pnpm install fallback",
            },
            ship_with_gaps=True,
        )
        spec_run.iterations.append(
            IterationState(
                number=2, hard_gates_pass=True, pass_rate=94,
                scenario_pass_count=74, scenario_total=78,
            )
        )
        self._seed_defects_iter(
            project_dir, spec_run.spec_name, 2,
            [
                {"test_name": "[AC-2] npm install rejected by engine",
                 "category": "ENV_ISSUE",
                 "rationale": "host npm version dependent"},
                {"test_name": "[EC-1] pnpm install fallback",
                 "category": "ENV_ISSUE",
                 "rationale": "host pnpm version dependent"},
            ],
        )

        _write_defect_registry(spec_run, project_dir)

        gaps_path = (
            project_dir / ".dark-factory" / "reports"
            / f"{spec_run.spec_name}-known-gaps.json"
        )
        self.assertTrue(gaps_path.exists())
        payload = json.loads(gaps_path.read_text())
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["spec"], spec_run.spec_name)
        self.assertEqual(len(payload["gaps"]), 2)
        names = {g["test_name"] for g in payload["gaps"]}
        self.assertIn("[AC-2] npm install rejected by engine", names)
        self.assertIn("[EC-1] pnpm install fallback", names)
        # Anchors should be parsed from the bracket prefix.
        anchors = {g["anchor"] for g in payload["gaps"]}
        self.assertEqual(anchors, {"AC-2", "EC-1"})
        # Categories carry through unchanged.
        for g in payload["gaps"]:
            self.assertEqual(g["category"], "ENV_ISSUE")
            self.assertTrue(g["source"].startswith("oracle_iter"))

        # Markdown still written regardless.
        md_path = (
            project_dir / ".dark-factory" / "reports"
            / f"{spec_run.spec_name}-defect-registry.md"
        )
        self.assertTrue(md_path.exists())
        self.assertIn("ENV_ISSUE", md_path.read_text())

    def test_no_known_gaps_when_mixed_with_impl_bug(self) -> None:
        """Mixed classification (ENV_ISSUE + IMPL_BUG) must NOT produce a skip list."""
        project_dir = make_project(self.tmp_dir)
        spec_run = _make_spec_run(
            iteration=2,
            stable_failure_names={
                "[AC-2] env thing",
                "[AC-3] real bug",
            },
            ship_with_gaps=True,
        )
        spec_run.iterations.append(
            IterationState(
                number=2, hard_gates_pass=True, pass_rate=80,
                scenario_pass_count=8, scenario_total=10,
            )
        )
        self._seed_defects_iter(
            project_dir, spec_run.spec_name, 2,
            [
                {"test_name": "[AC-2] env thing", "category": "ENV_ISSUE"},
                {"test_name": "[AC-3] real bug", "category": "IMPL_BUG"},
            ],
        )

        _write_defect_registry(spec_run, project_dir)

        gaps_path = (
            project_dir / ".dark-factory" / "reports"
            / f"{spec_run.spec_name}-known-gaps.json"
        )
        self.assertFalse(gaps_path.exists(),
                         "known-gaps.json must not be emitted when any failure is non-skippable")

        # Markdown still written, with both rows.
        md_path = (
            project_dir / ".dark-factory" / "reports"
            / f"{spec_run.spec_name}-defect-registry.md"
        )
        self.assertTrue(md_path.exists())
        content = md_path.read_text()
        self.assertIn("ENV_ISSUE", content)
        self.assertIn("IMPL_BUG", content)

    def test_no_known_gaps_when_classifier_missing(self) -> None:
        """Missing classifier output → categories default to BRITTLE → skippable."""
        project_dir = make_project(self.tmp_dir)
        spec_run = _make_spec_run(
            iteration=2,
            stable_failure_names={"some stable failure"},
            ship_with_gaps=True,
        )
        spec_run.iterations.append(
            IterationState(number=2, hard_gates_pass=True, pass_rate=90,
                           scenario_pass_count=9, scenario_total=10)
        )
        # No defects JSON seeded.

        _write_defect_registry(spec_run, project_dir)

        # BRITTLE is skippable, so the known-gaps file IS written in
        # this legacy path. Verified to document the fallback.
        gaps_path = (
            project_dir / ".dark-factory" / "reports"
            / f"{spec_run.spec_name}-known-gaps.json"
        )
        self.assertTrue(gaps_path.exists())
        payload = json.loads(gaps_path.read_text())
        self.assertEqual(payload["gaps"][0]["category"], "BRITTLE")


class TestHardGateParsing(TempDirTestCase):
    """Test _parse_hard_gate_status parsing from summary file."""

    def test_all_hard_gates_pass(self) -> None:
        from darkfactory.pipeline.quality_gate import GateResult, _parse_hard_gate_status
        project_dir = make_project(self.tmp_dir)
        summary = project_dir / ".dark-factory" / "reports" / "test-spec-quality-gate-summary.md"
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(
            "| Gate | Status |\n"
            "|------|--------|\n"
            "| Build | PASS |\n"
            "| Lint | PASS |\n"
            "| Type Check | PASS |\n"
            "| Blind Scenarios | FAIL |\n"
        )
        result = GateResult(exit_code=1)
        _parse_hard_gate_status(result, project_dir, "test-spec")
        self.assertTrue(result.hard_gates_pass)

    def test_build_fails(self) -> None:
        from darkfactory.pipeline.quality_gate import GateResult, _parse_hard_gate_status
        project_dir = make_project(self.tmp_dir)
        summary = project_dir / ".dark-factory" / "reports" / "test-spec-quality-gate-summary.md"
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(
            "| Gate | Status |\n"
            "|------|--------|\n"
            "| Build | FAIL |\n"
            "| Lint | PASS |\n"
            "| Type Check | PASS |\n"
        )
        result = GateResult(exit_code=2)
        _parse_hard_gate_status(result, project_dir, "test-spec")
        self.assertFalse(result.hard_gates_pass)

    def test_lint_fails(self) -> None:
        from darkfactory.pipeline.quality_gate import GateResult, _parse_hard_gate_status
        project_dir = make_project(self.tmp_dir)
        summary = project_dir / ".dark-factory" / "reports" / "test-spec-quality-gate-summary.md"
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(
            "| Gate | Status |\n"
            "|------|--------|\n"
            "| Build | PASS |\n"
            "| Lint | FAIL |\n"
            "| Type Check | PASS |\n"
        )
        result = GateResult(exit_code=1)
        _parse_hard_gate_status(result, project_dir, "test-spec")
        self.assertFalse(result.hard_gates_pass)

    def test_skipped_gates_treated_as_pass(self) -> None:
        from darkfactory.pipeline.quality_gate import GateResult, _parse_hard_gate_status
        project_dir = make_project(self.tmp_dir)
        summary = project_dir / ".dark-factory" / "reports" / "test-spec-quality-gate-summary.md"
        summary.parent.mkdir(parents=True, exist_ok=True)
        summary.write_text(
            "| Gate | Status |\n"
            "|------|--------|\n"
            "| Build | PASS |\n"
            "| Lint | SKIP |\n"
            "| Type Check | SKIP |\n"
        )
        result = GateResult(exit_code=0)
        _parse_hard_gate_status(result, project_dir, "test-spec")
        self.assertTrue(result.hard_gates_pass)

    def test_missing_summary_stays_false(self) -> None:
        from darkfactory.pipeline.quality_gate import GateResult, _parse_hard_gate_status
        project_dir = make_project(self.tmp_dir)
        result = GateResult(exit_code=1)
        _parse_hard_gate_status(result, project_dir, "test-spec")
        self.assertFalse(result.hard_gates_pass)


class TestConfigValidation(unittest.TestCase):
    """Test config validation for stable-failure fields."""

    def test_min_ratio_out_of_range(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(stable_failure_min_ratio=1.5)

    def test_min_ratio_negative(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(stable_failure_min_ratio=-0.1)

    def test_min_iterations_zero(self) -> None:
        with self.assertRaises(ConfigError):
            PipelineConfig(stable_failure_min_iterations=0)

    def test_valid_config(self) -> None:
        config = PipelineConfig(
            stable_failure_min_ratio=0.5,
            stable_failure_min_iterations=3,
        )
        self.assertEqual(config.stable_failure_min_ratio, 0.5)
        self.assertEqual(config.stable_failure_min_iterations, 3)


class TestStableFailureTerminal(TempDirTestCase):
    """Sprint 1 (2026-06-08): an eligible run whose Oracle says NO-SHIP becomes
    a terminal STABLE_FAILURE — NOT a forced SHIP. _step_oracle sets
    spec_run.stable_failure and keeps the NO-SHIP verdict; the orchestrator
    routes it to exit 5 (covered in test_orchestrator / the integrity test)."""

    def _call_step_oracle(
        self,
        oracle_output: str = "NO-SHIP\nSome analysis.",
        ship_with_gaps: bool = True,
        stable_failure_ratio: float = 1.0,
        pass_rate: int = 85,
        enterprise_enabled: bool = False,
        min_satisfaction_score: int = 0,
        total_turns: int = 0,
        barrier_violations: int = 0,
    ) -> tuple[bool, IterationState, SpecRun]:
        """Helper: invoke _step_oracle with mocked invoke_agent."""
        config = _make_config(
            enterprise_enabled=enterprise_enabled,
            min_satisfaction_score=min_satisfaction_score,
        )
        spec_run = _make_spec_run(
            iteration=2,
            stable_failure_ratio=stable_failure_ratio,
            ship_with_gaps=ship_with_gaps,
        )
        spec_run.total_turns = total_turns
        iter_state = IterationState(
            number=2, gate_exit_code=1, hard_gates_pass=True,
            pass_rate=pass_rate, scenario_pass_count=17, scenario_total=20,
        )
        iter_state.barrier_violations = barrier_violations
        spec_run.iterations.append(iter_state)
        project_dir = make_project(self.tmp_dir)
        # Write a minimal spec file so the oracle prompt can reference it.
        spec_file = project_dir / ".dark-factory" / "specs" / "test-spec.md"
        spec_file.write_text("# Test Spec\n")

        mock_result = AgentResult(
            role=AgentRole.ORACLE, exit_code=0, output=oracle_output,
            turns_used=5, wall_seconds=1.0,
        )
        with patch("darkfactory.pipeline.iteration.invoke_agent", return_value=mock_result):
            shipped = _step_oracle(
                spec_run, iter_state, config, {},
                project_dir, events_file=None, dry_run=True,
            )
        return shipped, iter_state, spec_run

    def test_eligible_noship_routes_to_stable_failure(self) -> None:
        """Eligible + Oracle NO-SHIP → no ship, NO-SHIP verdict, stable_failure set.
        (Previously this asserted an override to SHIP — the override is gone.)"""
        shipped, iter_state, spec_run = self._call_step_oracle(
            oracle_output="NO-SHIP\nAnalysis text.",
            ship_with_gaps=True,
        )
        self.assertFalse(shipped)
        self.assertEqual(iter_state.oracle_verdict, "NO-SHIP")
        self.assertTrue(spec_run.stable_failure)

    def test_no_stable_failure_when_not_eligible(self) -> None:
        """ship_with_gaps=False → plain NO-SHIP, stable_failure stays False."""
        shipped, iter_state, spec_run = self._call_step_oracle(
            oracle_output="NO-SHIP\nAnalysis text.",
            ship_with_gaps=False,
        )
        self.assertFalse(shipped)
        self.assertEqual(iter_state.oracle_verdict, "NO-SHIP")
        self.assertFalse(spec_run.stable_failure)

    def test_satisfaction_floor_does_not_block_stable_failure(self) -> None:
        """Q3: the satisfaction floor is no longer consulted — an eligible
        NO-SHIP still becomes STABLE_FAILURE regardless of a low score."""
        shipped, iter_state, spec_run = self._call_step_oracle(
            oracle_output="NO-SHIP\nAnalysis text.",
            ship_with_gaps=True,
            min_satisfaction_score=50,  # iter_state.satisfaction_score defaults to 0
        )
        self.assertFalse(shipped)
        self.assertEqual(iter_state.oracle_verdict, "NO-SHIP")
        self.assertTrue(spec_run.stable_failure)

    def test_composite_blocked_does_not_stable_fail(self) -> None:
        """Q3 split: a composite-blocked (anomaly / barrier-violation) run must
        ESCALATE, not launder into STABLE_FAILURE. stable_failure stays False so
        the orchestrator routes it to _handle_escalation."""
        shipped, iter_state, spec_run = self._call_step_oracle(
            oracle_output="NO-SHIP\nAnalysis text.",
            ship_with_gaps=True,
            enterprise_enabled=True,
            total_turns=900,        # > anomaly_max_spec_turns (800) → +30
            barrier_violations=1,   # → +40 ; total 70 >= COMPOSITE_BLOCK
        )
        self.assertFalse(shipped)
        self.assertEqual(iter_state.oracle_verdict, "NO-SHIP")
        self.assertFalse(spec_run.stable_failure)

    def test_stable_failure_keeps_noship_verdict(self) -> None:
        """Invariant the HOLD-safety / no-ship-side-effect argument rests on:
        the verdict is never 'SHIP' when stable_failure fires."""
        _shipped, iter_state, spec_run = self._call_step_oracle(ship_with_gaps=True)
        self.assertTrue(spec_run.stable_failure)
        self.assertEqual(iter_state.oracle_verdict, "NO-SHIP")

    def test_legacy_ship_with_gaps_event_no_longer_emitted(self) -> None:
        """The override is gone, so _step_oracle no longer emits SHIP_WITH_GAPS.
        The terminal SPEC_STABLE_FAILURE event is emitted by
        _handle_stable_failure (orchestrator-level test)."""
        project_dir = make_project(self.tmp_dir)
        spec_file = project_dir / ".dark-factory" / "specs" / "test-spec.md"
        spec_file.write_text("# Test Spec\n")
        events_file = self.tmp_dir / "events.jsonl"

        config = _make_config()
        spec_run = _make_spec_run(iteration=2, ship_with_gaps=True)
        iter_state = IterationState(
            number=2, gate_exit_code=1, hard_gates_pass=True,
            pass_rate=85, scenario_pass_count=17, scenario_total=20,
        )
        spec_run.iterations.append(iter_state)

        mock_result = AgentResult(
            role=AgentRole.ORACLE, exit_code=0, output="NO-SHIP\nAnalysis.",
            turns_used=5, wall_seconds=1.0,
        )
        with patch("darkfactory.pipeline.iteration.invoke_agent", return_value=mock_result):
            _step_oracle(
                spec_run, iter_state, config, {},
                project_dir, events_file=events_file, dry_run=True,
            )

        self.assertTrue(spec_run.stable_failure)
        if events_file.exists():
            events = [
                json.loads(line)
                for line in events_file.read_text().strip().splitlines() if line
            ]
            self.assertEqual(
                [e for e in events if e.get("event") == "SHIP_WITH_GAPS"], [],
                "SHIP_WITH_GAPS must no longer be emitted from _step_oracle",
            )

    def test_oracle_prompt_not_biased_toward_ship(self) -> None:
        """Sprint 1 core claim: the Oracle is NOT told to SHIP. The old
        ship-bias prompt block is removed; for a ship_with_gaps-eligible run the
        rendered Oracle prompt must contain neither the "Ship-With-Gaps
        Eligibility" header nor "SHOULD output SHIP". Regression lock for the
        BLOCKING finding (Linus + Codex) that the bias block survived the
        override removal."""
        config = _make_config()
        spec_run = _make_spec_run(iteration=2, ship_with_gaps=True)
        iter_state = IterationState(
            number=2, gate_exit_code=1, hard_gates_pass=True,
            pass_rate=85, scenario_pass_count=17, scenario_total=20,
        )
        spec_run.iterations.append(iter_state)
        project_dir = make_project(self.tmp_dir)
        spec_file = project_dir / ".dark-factory" / "specs" / "test-spec.md"
        spec_file.write_text("# Test Spec\n")

        mock_result = AgentResult(
            role=AgentRole.ORACLE, exit_code=0, output="NO-SHIP\nAnalysis.",
            turns_used=5, wall_seconds=1.0,
        )
        with patch(
            "darkfactory.pipeline.iteration.invoke_agent", return_value=mock_result,
        ) as mock_invoke:
            _step_oracle(
                spec_run, iter_state, config, {},
                project_dir, events_file=None, dry_run=True,
            )

        # The Oracle prompt is the 2nd positional arg to invoke_agent.
        prompt = mock_invoke.call_args_list[0].args[1]
        self.assertNotIn("SHOULD output SHIP", prompt)
        self.assertNotIn("Ship-With-Gaps Eligibility", prompt)

    def test_oracle_output_persisted(self) -> None:
        """Oracle raw output should be written to a file (unchanged behavior)."""
        project_dir = make_project(self.tmp_dir)
        spec_file = project_dir / ".dark-factory" / "specs" / "test-spec.md"
        spec_file.write_text("# Test Spec\n")

        config = _make_config()
        spec_run = _make_spec_run(iteration=2, ship_with_gaps=True)
        iter_state = IterationState(
            number=2, gate_exit_code=1, hard_gates_pass=True,
            pass_rate=85, scenario_pass_count=17, scenario_total=20,
        )
        spec_run.iterations.append(iter_state)

        oracle_text = "NO-SHIP\nDetailed analysis here."
        mock_result = AgentResult(
            role=AgentRole.ORACLE, exit_code=0, output=oracle_text,
            turns_used=5, wall_seconds=1.0,
        )
        with patch("darkfactory.pipeline.iteration.invoke_agent", return_value=mock_result):
            _step_oracle(
                spec_run, iter_state, config, {},
                project_dir, events_file=None, dry_run=True,
            )

        output_path = (
            project_dir / ".dark-factory" / "reviews"
            / "test-spec-oracle-output-iter2.md"
        )
        self.assertTrue(output_path.exists())
        self.assertEqual(output_path.read_text(), oracle_text)


class TestShipWithGapsFlagPersistence(unittest.TestCase):
    """Verify ship_with_gaps flag is not reset between iterations."""

    def test_flag_persists_across_iterations(self) -> None:
        spec_run = SpecRun(spec_name="test-spec")
        spec_run.iteration = 2
        spec_run.ship_with_gaps = True
        # Simulate what happens between iterations — nothing resets the flag.
        spec_run.iteration = 3
        self.assertTrue(spec_run.ship_with_gaps)


if __name__ == "__main__":
    unittest.main()
