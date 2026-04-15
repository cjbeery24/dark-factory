"""Tests for plan/executor.py, plan/conflict.py, plan/governor.py."""

import unittest
from unittest.mock import MagicMock, patch

from tests.helpers import TempDirTestCase
from darkfactory.errors import ConfigError
from darkfactory.models.config import PipelineConfig
from darkfactory.models.plan import PlanSpec
from darkfactory.plan.conflict import detect_conflicts, serialize_conflicts
from darkfactory.plan.governor import check_budget_pre_dispatch


class TestConflictDetection(unittest.TestCase):
    def test_no_conflicts(self) -> None:
        specs = [
            PlanSpec(name="a", target_files=["src/a.ts"]),
            PlanSpec(name="b", target_files=["src/b.ts"]),
        ]
        report = detect_conflicts(specs)
        self.assertFalse(report.has_conflicts)

    def test_has_conflicts(self) -> None:
        specs = [
            PlanSpec(name="a", target_files=["src/shared.ts", "src/a.ts"]),
            PlanSpec(name="b", target_files=["src/shared.ts", "src/b.ts"]),
        ]
        report = detect_conflicts(specs)
        self.assertTrue(report.has_conflicts)
        self.assertIn("src/shared.ts", report.file_owners)
        self.assertEqual(report.file_owners["src/shared.ts"], ["a", "b"])

    def test_no_target_files(self) -> None:
        specs = [PlanSpec(name="a"), PlanSpec(name="b")]
        report = detect_conflicts(specs)
        self.assertFalse(report.has_conflicts)


class TestSerializeConflicts(unittest.TestCase):
    def test_adds_dependency(self) -> None:
        specs = [
            PlanSpec(name="a", target_files=["src/shared.ts"], priority=1),
            PlanSpec(name="b", target_files=["src/shared.ts"], priority=2),
        ]
        dispatch_order = list(specs)  # a before b
        serialize_conflicts(specs, dispatch_order)
        b = next(s for s in specs if s.name == "b")
        self.assertIn("a", b.deps)

    def test_no_duplicate_deps(self) -> None:
        specs = [
            PlanSpec(name="a", target_files=["f1.ts", "f2.ts"], priority=1),
            PlanSpec(name="b", target_files=["f1.ts", "f2.ts"], deps=["a"], priority=2),
        ]
        dispatch_order = list(specs)
        serialize_conflicts(specs, dispatch_order)
        b = next(s for s in specs if s.name == "b")
        self.assertEqual(b.deps.count("a"), 1)


class TestGovernorBudget(unittest.TestCase):
    def test_within_budget(self) -> None:
        self.assertTrue(check_budget_pre_dispatch(0, 200, 1000))

    def test_exceeds_budget(self) -> None:
        self.assertFalse(check_budget_pre_dispatch(900, 200, 1000))

    def test_unlimited_budget(self) -> None:
        self.assertTrue(check_budget_pre_dispatch(99999, 200, 0))

    def test_exact_budget(self) -> None:
        self.assertTrue(check_budget_pre_dispatch(800, 200, 1000))


class TestExecutePlan(TempDirTestCase):
    def _setup_plan(self, specs: list[str]) -> None:
        plan_dir = self.tmp_dir / ".dark-factory" / "plans"
        plan_dir.mkdir(parents=True)
        spec_dir = self.tmp_dir / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True, exist_ok=True)
        state_dir = self.tmp_dir / ".dark-factory" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)

        # Match the /df-write-plan feature-decomposition table format that
        # parse_plan() expects: header "| ID | Feature Name | ... |".
        rows = "| ID | Feature Name | Spec Type | Depends On | Description |\n"
        rows += "| --- | --- | --- | --- | --- |\n"
        for i, s in enumerate(specs, start=1):
            rows += f"| F-{i} | {s} | feature | — | {s} feature |\n"
            (spec_dir / f"{s}.md").write_text(
                f"# {s}\n\n"
                f"<!-- dark-factory-metadata\n"
                f"spec_type: feature\n"
                f"-->\n"
            )

        (plan_dir / "test-plan.md").write_text(f"# Plan\n\n{rows}")

    @patch("darkfactory.plan.executor.preflight", return_value=[])
    @patch("darkfactory.plan.executor.worktree")
    @patch("darkfactory.plan.executor.run_pipeline")
    def test_dry_run(
        self, mock_pipeline: MagicMock, mock_wt: MagicMock, mock_pf: MagicMock,
    ) -> None:
        from darkfactory.plan.executor import execute_plan
        self._setup_plan(["auth"])
        config = PipelineConfig()
        result = execute_plan("test-plan", config, self.tmp_dir, dry_run=True)
        mock_pipeline.assert_not_called()
        self.assertEqual(result.shipped, 0)

    def test_missing_plan_raises(self) -> None:
        from darkfactory.plan.executor import execute_plan
        config = PipelineConfig()
        with self.assertRaises(ConfigError):
            execute_plan("nonexistent", config, self.tmp_dir)


class TestSetupValidator(TempDirTestCase):
    def test_missing_directories(self) -> None:
        from darkfactory.setup.validator import validate_setup
        report = validate_setup(self.tmp_dir)
        self.assertGreater(report.fail_count, 0)

    def test_basic_structure(self) -> None:
        from darkfactory.setup.validator import validate_setup
        for d in [".dark-factory", ".dark-factory/specs", ".dark-factory/scenarios",
                   ".dark-factory/reviews", ".dark-factory/reports"]:
            (self.tmp_dir / d).mkdir(parents=True, exist_ok=True)
        report = validate_setup(self.tmp_dir)
        # Should have some passes for the dirs we created.
        self.assertGreater(report.pass_count, 0)


class TestMetricsSummary(TempDirTestCase):
    def test_empty_file(self) -> None:
        from darkfactory.setup.metrics_summary import summarize_metrics
        metrics = self.tmp_dir / "metrics.jsonl"
        metrics.write_text("")
        summary = summarize_metrics(metrics)
        self.assertEqual(summary.total, 0)

    def test_basic_summary(self) -> None:
        import json
        from darkfactory.setup.metrics_summary import summarize_metrics
        metrics = self.tmp_dir / "metrics.jsonl"
        records = [
            {"spec": "auth", "run_id": "r1", "outcome": "shipped", "iters": 1, "wall_s": 120},
            {"spec": "api", "run_id": "r2", "outcome": "shipped", "iters": 2, "wall_s": 300},
            {"spec": "ui", "run_id": "r3", "outcome": "failed", "iters": 3, "wall_s": 600},
        ]
        metrics.write_text("\n".join(json.dumps(r) for r in records) + "\n")
        summary = summarize_metrics(metrics)
        self.assertEqual(summary.total, 3)
        self.assertEqual(summary.shipped, 2)
        self.assertEqual(summary.failed, 1)
        # Ship rate: 2*100//3 = 66
        self.assertEqual(summary.ship_rate, 66)
        # First pass: only auth (iters=1)
        self.assertEqual(summary.first_pass_count, 1)
        # First pass rate: 1*100//2 = 50
        self.assertEqual(summary.first_pass_rate, 50)

    def test_format_table(self) -> None:
        import json
        from darkfactory.setup.metrics_summary import format_table, summarize_metrics
        metrics = self.tmp_dir / "metrics.jsonl"
        metrics.write_text(json.dumps({"spec": "a", "run_id": "r1", "outcome": "shipped", "iters": 1, "wall_s": 60}) + "\n")
        summary = summarize_metrics(metrics)
        table = format_table(summary)
        self.assertIn("SPEC", table)
        self.assertIn("shipped", table.lower())

    def test_missing_file(self) -> None:
        from darkfactory.setup.metrics_summary import summarize_metrics
        summary = summarize_metrics(self.tmp_dir / "nonexistent.jsonl")
        self.assertEqual(summary.total, 0)


if __name__ == "__main__":
    unittest.main()
