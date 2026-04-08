"""Tests for docker/barriers.py."""

import unittest

from darkfactory.docker.barriers import configure_barriers
from darkfactory.models.agent import AgentRole


class TestBarriers(unittest.TestCase):
    def test_implementer_masks_holdouts_not_visible_scenarios(self) -> None:
        """Phase 4.6b.1: Implementer reads Test Engineer's visible tests
        under .dark-factory/scenarios/ but cannot see QA Holdout's
        adversarial tests under .dark-factory/holdouts/."""
        barriers = configure_barriers(["src"])
        impl = barriers[AgentRole.IMPLEMENTER]
        self.assertIn(".dark-factory/holdouts", impl.masked_dirs)
        self.assertIn(".dark-factory/reports", impl.masked_dirs)
        self.assertIn(".git", impl.masked_dirs)
        self.assertNotIn(".dark-factory/scenarios", impl.masked_dirs)

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

    def test_implementer_failures_dir_is_masked(self) -> None:
        barriers = configure_barriers(["src"])
        self.assertIn(".dark-factory/failures", barriers[AgentRole.IMPLEMENTER].masked_dirs)

    def test_architect_masks_scenarios_not_source(self) -> None:
        barriers = configure_barriers(["src", "lib"])
        arch = barriers[AgentRole.ARCHITECT]
        self.assertIn(".dark-factory/scenarios", arch.masked_dirs)
        self.assertIn(".dark-factory/audit", arch.masked_dirs)
        self.assertIn(".dark-factory/state", arch.masked_dirs)
        self.assertIn(".dark-factory/failures", arch.masked_dirs)
        self.assertIn(".git", arch.masked_dirs)
        # Architect CAN read source code (unlike SA) — needs codebase conventions.
        self.assertNotIn("src", arch.masked_dirs)
        self.assertNotIn("lib", arch.masked_dirs)
        # Architect CAN read and write reports (generation report output).
        self.assertNotIn(".dark-factory/reports", arch.masked_dirs)

    def test_architect_denied_tools(self) -> None:
        barriers = configure_barriers(["src"])
        arch = barriers[AgentRole.ARCHITECT]
        self.assertIn("WebSearch", arch.denied_tools)
        self.assertIn("WebFetch", arch.denied_tools)

    def test_architect_write_dirs(self) -> None:
        barriers = configure_barriers(["src"])
        arch = barriers[AgentRole.ARCHITECT]
        self.assertIn(".dark-factory/architecture", arch.write_dirs)
        self.assertIn(".dark-factory/reports", arch.write_dirs)

    def test_architect_has_barrier_prompt(self) -> None:
        barriers = configure_barriers(["src"])
        self.assertIn("MUST NOT", barriers[AgentRole.ARCHITECT].barrier_prompt)


if __name__ == "__main__":
    unittest.main()
