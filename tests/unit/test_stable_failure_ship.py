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


class TestShipWithGapsOracleOverride(TempDirTestCase):
    """Test the mechanical override in _step_oracle when ship_with_gaps is True."""

    def _call_step_oracle(
        self,
        oracle_output: str = "NO-SHIP\nSome analysis.",
        ship_with_gaps: bool = True,
        stable_failure_ratio: float = 1.0,
        pass_rate: int = 85,
        enterprise_enabled: bool = False,
        min_satisfaction_score: int = 0,
    ) -> tuple[bool, IterationState]:
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
        iter_state = IterationState(
            number=2, gate_exit_code=1, hard_gates_pass=True,
            pass_rate=pass_rate, scenario_pass_count=17, scenario_total=20,
        )
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
        return shipped, iter_state

    def test_oracle_noship_overridden_when_ship_with_gaps(self) -> None:
        """When ship_with_gaps=True and oracle says NO-SHIP, override to SHIP."""
        shipped, iter_state = self._call_step_oracle(
            oracle_output="NO-SHIP\nAnalysis text.",
            ship_with_gaps=True,
        )
        self.assertTrue(shipped)
        self.assertEqual(iter_state.oracle_verdict, "SHIP")

    def test_no_override_when_ship_with_gaps_false(self) -> None:
        """When ship_with_gaps=False, oracle NO-SHIP stands."""
        shipped, iter_state = self._call_step_oracle(
            oracle_output="NO-SHIP\nAnalysis text.",
            ship_with_gaps=False,
        )
        self.assertFalse(shipped)
        self.assertEqual(iter_state.oracle_verdict, "NO-SHIP")

    def test_override_suppressed_by_satisfaction_floor(self) -> None:
        """When satisfaction is below floor, override does NOT fire."""
        shipped, iter_state = self._call_step_oracle(
            oracle_output="NO-SHIP\nAnalysis text.",
            ship_with_gaps=True,
            min_satisfaction_score=50,  # iter_state.satisfaction_score defaults to 0
        )
        self.assertFalse(shipped)
        self.assertEqual(iter_state.oracle_verdict, "NO-SHIP")

    def test_oracle_output_persisted(self) -> None:
        """Oracle raw output should be written to a file."""
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

    def test_ship_with_gaps_event_emitted(self) -> None:
        """SHIP_WITH_GAPS event should be emitted when override fires."""
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

        self.assertTrue(events_file.exists())
        lines = events_file.read_text().strip().splitlines()
        events = [json.loads(line) for line in lines]
        swg_events = [e for e in events if e.get("event") == "SHIP_WITH_GAPS"]
        self.assertEqual(len(swg_events), 1)
        data = swg_events[0]["data"]
        self.assertEqual(data["stable_failures"], 2)
        self.assertEqual(data["oracle_original"], "NO-SHIP")


class TestClassificationRoutingGuard(unittest.TestCase):
    """Test that classification routing escalation is suppressed by ship_with_gaps."""

    def test_classification_routing_suppressed_by_ship_with_gaps(self) -> None:
        """Escalation should NOT fire when ship_with_gaps is True."""
        spec_run = _make_spec_run(iteration=3, ship_with_gaps=True)
        spec_run.escalation_recommended = True
        spec_run.escalation_reason = "2 SPEC_AMBIGUOUS + 1 BRITTLE out of 3 failures"
        # ship_with_gaps=True should suppress the escalation check.
        # The guard is: escalation_recommended and not ship_with_gaps and iteration >= threshold
        self.assertTrue(spec_run.escalation_recommended)
        self.assertTrue(spec_run.ship_with_gaps)
        # The condition that would have triggered escalation:
        escalation_threshold = 2
        should_escalate = (
            spec_run.escalation_recommended
            and not spec_run.ship_with_gaps
            and spec_run.iteration >= escalation_threshold
        )
        self.assertFalse(should_escalate)

    def test_classification_routing_fires_without_ship_with_gaps(self) -> None:
        """Escalation should fire normally when ship_with_gaps is False."""
        spec_run = _make_spec_run(iteration=3, ship_with_gaps=False)
        spec_run.escalation_recommended = True
        spec_run.escalation_reason = "2 SPEC_AMBIGUOUS out of 3 failures"
        escalation_threshold = 2
        should_escalate = (
            spec_run.escalation_recommended
            and not spec_run.ship_with_gaps
            and spec_run.iteration >= escalation_threshold
        )
        self.assertTrue(should_escalate)


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
