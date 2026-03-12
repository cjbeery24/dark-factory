"""Tests for docker/barriers.py."""

import unittest

from darkfactory.docker.barriers import configure_barriers
from darkfactory.models.agent import AgentRole


class TestBarriers(unittest.TestCase):
    def test_implementer_masks_scenarios(self) -> None:
        barriers = configure_barriers(["src"])
        impl = barriers[AgentRole.IMPLEMENTER]
        self.assertIn(".dark-factory/scenarios", impl.masked_dirs)
        self.assertIn(".dark-factory/reports", impl.masked_dirs)
        self.assertIn(".git", impl.masked_dirs)

    def test_scenario_architect_masks_source(self) -> None:
        barriers = configure_barriers(["src", "lib"])
        sa = barriers[AgentRole.SCENARIO_ARCHITECT]
        self.assertIn("src", sa.masked_dirs)
        self.assertIn("lib", sa.masked_dirs)
        self.assertIn(".git", sa.masked_dirs)

    def test_oracle_no_masks(self) -> None:
        barriers = configure_barriers(["src"])
        oracle = barriers[AgentRole.ORACLE]
        self.assertEqual(oracle.masked_dirs, [])

    def test_all_roles_present(self) -> None:
        barriers = configure_barriers(["src"])
        for role in AgentRole:
            self.assertIn(role, barriers)

    def test_implementer_has_barrier_prompt(self) -> None:
        barriers = configure_barriers(["src"])
        self.assertIn("MUST NOT", barriers[AgentRole.IMPLEMENTER].barrier_prompt)


if __name__ == "__main__":
    unittest.main()
