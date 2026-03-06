"""Tests for plan/topo_sort.py — Kahn's algorithm with priority tie-breaking."""

import unittest

from darkfactory.errors import ConfigError
from darkfactory.models.plan import PlanSpec
from darkfactory.plan.topo_sort import topological_sort


class TestTopologicalSort(unittest.TestCase):
    def test_no_deps(self) -> None:
        specs = [
            PlanSpec(name="c", priority=3),
            PlanSpec(name="a", priority=1),
            PlanSpec(name="b", priority=2),
        ]
        result = topological_sort(specs)
        names = [s.name for s in result]
        # Sorted by priority when no deps.
        self.assertEqual(names, ["a", "b", "c"])

    def test_linear_chain(self) -> None:
        specs = [
            PlanSpec(name="a", deps=[], priority=1),
            PlanSpec(name="b", deps=["a"], priority=1),
            PlanSpec(name="c", deps=["b"], priority=1),
        ]
        result = topological_sort(specs)
        names = [s.name for s in result]
        self.assertEqual(names, ["a", "b", "c"])

    def test_diamond_deps(self) -> None:
        specs = [
            PlanSpec(name="a", deps=[], priority=1),
            PlanSpec(name="b", deps=["a"], priority=2),
            PlanSpec(name="c", deps=["a"], priority=1),
            PlanSpec(name="d", deps=["b", "c"], priority=1),
        ]
        result = topological_sort(specs)
        names = [s.name for s in result]
        # a must come first, d must come last.
        self.assertEqual(names[0], "a")
        self.assertEqual(names[-1], "d")
        # c has lower priority number than b, should come first.
        self.assertEqual(names[1], "c")
        self.assertEqual(names[2], "b")

    def test_cycle_raises(self) -> None:
        specs = [
            PlanSpec(name="a", deps=["b"]),
            PlanSpec(name="b", deps=["a"]),
        ]
        with self.assertRaises(ConfigError) as ctx:
            topological_sort(specs)
        self.assertIn("circular dependency", str(ctx.exception))

    def test_single_spec(self) -> None:
        specs = [PlanSpec(name="solo")]
        result = topological_sort(specs)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "solo")

    def test_priority_tiebreaking_within_tier(self) -> None:
        """Specs that become ready at the same time are sorted by priority."""
        specs = [
            PlanSpec(name="base", deps=[], priority=99),
            PlanSpec(name="high", deps=["base"], priority=1),
            PlanSpec(name="low", deps=["base"], priority=10),
            PlanSpec(name="mid", deps=["base"], priority=5),
        ]
        result = topological_sort(specs)
        names = [s.name for s in result]
        self.assertEqual(names[0], "base")
        # After base, all become ready — sorted by priority.
        self.assertEqual(names[1:], ["high", "mid", "low"])


    def test_cross_tier_priority_ordering(self) -> None:
        """Priority is globally respected, not just within a batch."""
        # A(p=1, no deps), B(p=5, no deps), C(p=1, depends on A).
        # After popping A, C becomes ready. Queue has B(p=5).
        # C(p=1) should come before B(p=5) since it has higher priority.
        specs = [
            PlanSpec(name="a", deps=[], priority=1),
            PlanSpec(name="b", deps=[], priority=5),
            PlanSpec(name="c", deps=["a"], priority=1),
        ]
        result = topological_sort(specs)
        names = [s.name for s in result]
        self.assertEqual(names[0], "a")
        # c (priority 1) should come before b (priority 5).
        self.assertEqual(names[1], "c")
        self.assertEqual(names[2], "b")


if __name__ == "__main__":
    unittest.main()
