"""Tests for plan optimizer (Phase 7D)."""

import json
import tempfile
import unittest
from pathlib import Path

from darkfactory.models.plan import PlanSpec
from darkfactory.plan.optimizer import SpecHistory, _group_by_deps, _load_history, optimize_plan


class TestGroupByDeps(unittest.TestCase):
    def test_flat_specs(self) -> None:
        specs = [PlanSpec(name="a"), PlanSpec(name="b"), PlanSpec(name="c")]
        tiers = _group_by_deps(specs)
        self.assertEqual(len(tiers), 1)
        self.assertEqual(len(tiers[0]), 3)

    def test_chained_deps(self) -> None:
        specs = [
            PlanSpec(name="a"),
            PlanSpec(name="b", deps=["a"]),
            PlanSpec(name="c", deps=["b"]),
        ]
        tiers = _group_by_deps(specs)
        self.assertEqual(len(tiers), 3)
        self.assertEqual(tiers[0][0].name, "a")
        self.assertEqual(tiers[1][0].name, "b")
        self.assertEqual(tiers[2][0].name, "c")

    def test_parallel_deps(self) -> None:
        specs = [
            PlanSpec(name="base"),
            PlanSpec(name="a", deps=["base"]),
            PlanSpec(name="b", deps=["base"]),
        ]
        tiers = _group_by_deps(specs)
        self.assertEqual(len(tiers), 2)
        self.assertEqual(len(tiers[1]), 2)


class TestOptimizePlan(unittest.TestCase):
    def test_no_events_returns_original(self) -> None:
        specs = [PlanSpec(name="a"), PlanSpec(name="b")]
        result = optimize_plan(specs, events_file=None)
        self.assertEqual([s.name for s in result], ["a", "b"])

    def test_failure_first_ordering(self) -> None:
        events_file = Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            # Write history: "b" has high failure rate.
            events = [
                {"event_type": "SPEC_SHIPPED", "spec_name": "a", "data": {"wall_seconds": 10}},
                {"event_type": "SPEC_SHIPPED", "spec_name": "a", "data": {"wall_seconds": 10}},
                {"event_type": "SPEC_FAILED", "spec_name": "b", "data": {}},
                {"event_type": "SPEC_FAILED", "spec_name": "b", "data": {}},
            ]
            with events_file.open("w") as f:
                for e in events:
                    f.write(json.dumps(e) + "\n")

            specs = [PlanSpec(name="a"), PlanSpec(name="b")]
            result = optimize_plan(specs, events_file)
            # "b" should come first (higher failure rate).
            self.assertEqual(result[0].name, "b")
        finally:
            events_file.unlink(missing_ok=True)

    def test_longest_first_within_tier(self) -> None:
        events_file = Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            events = [
                {"event_type": "SPEC_SHIPPED", "spec_name": "short", "data": {"wall_seconds": 5}},
                {"event_type": "SPEC_SHIPPED", "spec_name": "long", "data": {"wall_seconds": 100}},
            ]
            with events_file.open("w") as f:
                for e in events:
                    f.write(json.dumps(e) + "\n")

            specs = [PlanSpec(name="short"), PlanSpec(name="long")]
            result = optimize_plan(specs, events_file)
            # "long" should come first (longer wall time for critical path).
            self.assertEqual(result[0].name, "long")
        finally:
            events_file.unlink(missing_ok=True)

    def test_preserves_dependency_order(self) -> None:
        events_file = Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            events = [
                {"event_type": "SPEC_SHIPPED", "spec_name": "base", "data": {"wall_seconds": 1}},
                {"event_type": "SPEC_SHIPPED", "spec_name": "dep", "data": {"wall_seconds": 100}},
            ]
            with events_file.open("w") as f:
                for e in events:
                    f.write(json.dumps(e) + "\n")

            specs = [PlanSpec(name="base"), PlanSpec(name="dep", deps=["base"])]
            result = optimize_plan(specs, events_file)
            # "base" must still come before "dep" despite shorter wall time.
            self.assertEqual(result[0].name, "base")
            self.assertEqual(result[1].name, "dep")
        finally:
            events_file.unlink(missing_ok=True)


class TestLoadHistory(unittest.TestCase):
    def test_empty_file(self) -> None:
        f = Path(tempfile.mktemp(suffix=".jsonl"))
        f.touch()
        try:
            result = _load_history(f)
            self.assertEqual(result, {})
        finally:
            f.unlink(missing_ok=True)

    def test_missing_file(self) -> None:
        result = _load_history(Path("/nonexistent"))
        self.assertEqual(result, {})

    def test_computes_averages(self) -> None:
        f = Path(tempfile.mktemp(suffix=".jsonl"))
        try:
            events = [
                {"event_type": "SPEC_SHIPPED", "spec_name": "x", "data": {"wall_seconds": 10}},
                {"event_type": "SPEC_SHIPPED", "spec_name": "x", "data": {"wall_seconds": 20}},
                {"event_type": "SPEC_FAILED", "spec_name": "x", "data": {}},
            ]
            with f.open("w") as fp:
                for e in events:
                    fp.write(json.dumps(e) + "\n")
            result = _load_history(f)
            self.assertIn("x", result)
            self.assertAlmostEqual(result["x"].avg_wall_seconds, 10.0)  # 30/3
            self.assertAlmostEqual(result["x"].failure_rate, 1/3)
            self.assertEqual(result["x"].run_count, 3)
        finally:
            f.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
