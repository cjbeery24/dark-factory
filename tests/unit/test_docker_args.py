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


class TestProxyEnvInjection(unittest.TestCase):
    """Tests for proxy env var injection in invoke_agent."""

    def _captured_docker_args(self, role: AgentRole, egress_filtering: bool) -> list[str]:
        """Invoke invoke_agent with a mocked execute_container and return captured docker_args."""
        import tempfile
        from unittest.mock import patch
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.docker.containers import RunResult

        with tempfile.TemporaryDirectory() as tmpdir:
            result_json = '{"result": "success", "session_id": "test-session-id", "is_error": false}'
            mock_result = RunResult(exit_code=0, stdout=result_json, stderr="")

            captured: list[list[str]] = []

            def fake_execute(docker_args, image, claude_args, **kwargs):
                captured.append(list(docker_args))
                return mock_result

            config = PipelineConfig(egress_filtering=egress_filtering)
            barriers = configure_barriers(["src"])

            with patch("darkfactory.pipeline.agents.execute_container", side_effect=fake_execute), \
                 patch("darkfactory.pipeline.agents._run_safety_checks"), \
                 patch("darkfactory.pipeline.agents.check_language_image"):
                invoke_agent(
                    role,
                    prompt="test prompt",
                    config=config,
                    barriers=barriers,
                    run_id="testrun",
                    project_dir=tmpdir,  # type: ignore[arg-type]
                )

            return captured[0]

    def test_analysis_role_gets_analysis_proxy(self) -> None:
        args = self._captured_docker_args(AgentRole.SPEC_VALIDATOR, egress_filtering=True)
        proxy_url = "http://df-testrun-proxy-analysis:8080"
        self.assertIn(f"HTTPS_PROXY={proxy_url}", args)

    def test_execution_role_gets_execution_proxy(self) -> None:
        args = self._captured_docker_args(AgentRole.IMPLEMENTER, egress_filtering=True)
        proxy_url = "http://df-testrun-proxy-execution:8080"
        self.assertIn(f"HTTPS_PROXY={proxy_url}", args)

    def test_no_proxy_when_egress_filtering_disabled(self) -> None:
        args = self._captured_docker_args(AgentRole.SPEC_VALIDATOR, egress_filtering=False)
        self.assertFalse(any("HTTPS_PROXY" in a for a in args))
        self.assertFalse(any("HTTP_PROXY" in a for a in args))

    def test_both_upper_and_lower_proxy_vars_set(self) -> None:
        args = self._captured_docker_args(AgentRole.SPEC_VALIDATOR, egress_filtering=True)
        self.assertTrue(any("HTTPS_PROXY=" in a for a in args))
        self.assertTrue(any("HTTP_PROXY=" in a for a in args))
        self.assertTrue(any("https_proxy=" in a for a in args))
        self.assertTrue(any("http_proxy=" in a for a in args))

    def test_no_proxy_set_to_localhost(self) -> None:
        args = self._captured_docker_args(AgentRole.SPEC_VALIDATOR, egress_filtering=True)
        self.assertIn("NO_PROXY=localhost,127.0.0.1", args)
        self.assertIn("no_proxy=localhost,127.0.0.1", args)


if __name__ == "__main__":
    unittest.main()
