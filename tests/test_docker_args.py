"""Tests for docker/args.py — Docker CLI argument building."""

import unittest

from darkfactory.docker.args import build_base_args, build_agent_args, build_claude_args
from darkfactory.docker.barriers import configure_barriers
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig


class TestBuildBaseArgs(unittest.TestCase):
    def test_contains_security_args(self) -> None:
        config = PipelineConfig()
        args = build_base_args(config, "run1")
        self.assertIn("--cap-drop=ALL", args)
        self.assertIn("--pids-limit=256", args)
        self.assertIn("--memory=4g", args)

    def test_io_limits(self) -> None:
        config = PipelineConfig(
            agent_io_device="/dev/sda",
            agent_io_read_bps="100m",
            agent_io_write_bps="50m",
        )
        args = build_base_args(config, "run1")
        self.assertTrue(any("device-read-bps" in a for a in args))
        self.assertTrue(any("device-write-bps" in a for a in args))


class TestBuildAgentArgs(unittest.TestCase):
    def test_implementer_rw_mount(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        args = build_agent_args(
            AgentRole.IMPLEMENTER, config, barriers, "run1", "/project",
        )
        # Project mount should be rw.
        mount_args = [a for a in args if "/project" in a and ":rw" in a]
        self.assertTrue(len(mount_args) > 0)

    def test_scenario_architect_ro_mount(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        args = build_agent_args(
            AgentRole.SCENARIO_ARCHITECT, config, barriers, "run1", "/project",
        )
        mount_args = [a for a in args if "/project" in a and ":ro" in a]
        self.assertTrue(len(mount_args) > 0)

    def test_implementer_has_tmpfs_masks(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        args = build_agent_args(
            AgentRole.IMPLEMENTER, config, barriers, "run1", "/project",
        )
        tmpfs_args = [a for a in args if "scenarios" in a and "tmpfs" not in a]
        # Should have tmpfs for scenarios dir.
        self.assertTrue(any(".dark-factory/scenarios" in a for a in args))

    def test_labels(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        args = build_agent_args(
            AgentRole.IMPLEMENTER, config, barriers, "run1", "/project",
        )
        self.assertIn("df-run-id=run1", " ".join(args))
        self.assertIn("df-role=implementer", " ".join(args))

    def test_network(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        args = build_agent_args(
            AgentRole.IMPLEMENTER, config, barriers, "run1", "/project",
            network="df-run1-execution",
        )
        self.assertIn("--network", args)
        self.assertIn("df-run1-execution", args)


class TestBuildClaudeArgs(unittest.TestCase):
    def test_basic_args(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        args = build_claude_args(
            AgentRole.IMPLEMENTER, config, barriers,
        )
        self.assertIn("claude", args)
        self.assertIn("-p", args)
        self.assertIn("--dangerously-skip-permissions", args)
        self.assertIn("--max-turns=200", args)

    def test_denied_tools(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        args = build_claude_args(AgentRole.IMPLEMENTER, config, barriers)
        # Implementer should have denied tools (WebSearch/WebFetch).
        self.assertIn("--disallowedTools", args)

    def test_implementer_does_not_deny_read(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        args = build_claude_args(AgentRole.IMPLEMENTER, config, barriers)
        # Read/Glob/Grep must NOT be denied — implementer needs them.
        denied_idx = [i for i, a in enumerate(args) if a == "--disallowedTools"]
        denied_values = [args[i + 1] for i in denied_idx if i + 1 < len(args)]
        self.assertNotIn("Read", denied_values)
        self.assertNotIn("Glob", denied_values)
        self.assertNotIn("Grep", denied_values)

    def test_barrier_prompt(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        args = build_claude_args(AgentRole.IMPLEMENTER, config, barriers)
        self.assertIn("--append-system-prompt", args)


if __name__ == "__main__":
    unittest.main()
