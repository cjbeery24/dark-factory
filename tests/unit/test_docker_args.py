"""Tests for docker/args.py — Docker CLI argument building."""

import tempfile
import unittest
from pathlib import Path

from tests.helpers import make_project

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

    def test_ci_true_present_as_consecutive_items(self) -> None:
        config = PipelineConfig()
        args = build_base_args(config, "run1")
        pairs = list(zip(args, args[1:]))
        self.assertIn(("-e", "CI=true"), pairs)

    def test_home_agent_tmpfs_owned_by_gate_user(self) -> None:
        # Sprint 23: the /home/agent tmpfs holds the python user-site install dir
        # (PYTHONUSERBASE=/home/agent/.local), so it must be owned by the gate user
        # (uid 1000) — matching the other tmpfs overlays. Without uid=1000, pip's
        # --user console scripts (e.g. alembic) could be unwritable.
        args = build_base_args(PipelineConfig(), "run1")
        home_tmpfs = [
            a for a in args
            if isinstance(a, str) and a.startswith("/home/agent:")
        ]
        self.assertEqual(len(home_tmpfs), 1, args)
        self.assertIn("uid=1000", home_tmpfs[0])
        self.assertIn("gid=1000", home_tmpfs[0])


class TestBuildAgentArgs(unittest.TestCase):
    def test_implementer_rw_mount(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp))
            args = build_agent_args(
                AgentRole.IMPLEMENTER, config, barriers, "run1", tmp,
            )
        # Project mount should be rw.
        mount_args = [a for a in args if tmp in a and ":rw" in a]
        self.assertTrue(len(mount_args) > 0)

    def test_scenario_architect_ro_mount(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp))
            args = build_agent_args(
                AgentRole.SCENARIO_ARCHITECT, config, barriers, "run1", tmp,
            )
        mount_args = [a for a in args if tmp in a and ":ro" in a]
        self.assertTrue(len(mount_args) > 0)

    def test_implementer_has_tmpfs_masks(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp))
            args = build_agent_args(
                AgentRole.IMPLEMENTER, config, barriers, "run1", tmp,
            )
        # Implementer masks holdouts + reports (scenarios is its TDD target,
        # not masked). See barriers.py: implementer_masked.
        self.assertTrue(any(".dark-factory/holdouts" in a for a in args))
        self.assertTrue(any(".dark-factory/reports" in a for a in args))

    def test_implementer_allows_lockfile_update(self) -> None:
        import tempfile
        config = PipelineConfig()
        config.source_dirs = ["src"]
        barriers = configure_barriers(["src"])
        with tempfile.TemporaryDirectory() as tmp:
            args = build_agent_args(
                AgentRole.IMPLEMENTER, config, barriers, "run1", tmp,
            )
        pairs = list(zip(args, args[1:]))
        # CI=true is set (vitest non-interactive), but frozen-lockfile is
        # overridden so the Implementer can add deps and update the lockfile.
        self.assertIn(("-e", "CI=true"), pairs)
        self.assertIn(("-e", "npm_config_frozen_lockfile=false"), pairs)
        self.assertIn(("-e", "YARN_ENABLE_IMMUTABLE_INSTALLS=false"), pairs)

    def test_non_implementer_no_lockfile_override(self) -> None:
        config = PipelineConfig()
        config.source_dirs = ["src"]
        barriers = configure_barriers(["src"])
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp))
            args = build_agent_args(
                AgentRole.SCENARIO_ARCHITECT, config, barriers, "run1", tmp,
            )
        joined = " ".join(args)
        self.assertNotIn("npm_config_frozen_lockfile", joined)
        self.assertNotIn("YARN_ENABLE_IMMUTABLE_INSTALLS", joined)

    def test_labels(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp))
            args = build_agent_args(
                AgentRole.IMPLEMENTER, config, barriers, "run1", tmp,
            )
        self.assertIn("df-run-id=run1", " ".join(args))
        self.assertIn("df-role=implementer", " ".join(args))

    def test_network(self) -> None:
        config = PipelineConfig()
        barriers = configure_barriers(["src"])
        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp))
            args = build_agent_args(
                AgentRole.IMPLEMENTER, config, barriers, "run1", tmp,
                network="df-run1-execution",
            )
        self.assertIn("--network", args)
        self.assertIn("df-run1-execution", args)


class TestSourceDirsLiveResolution(unittest.TestCase):
    """Phase 4.5.3: build_agent_args must read source_dirs off the live
    config, not the snapshot baked into the cached `barriers` dict.

    Architect amendments to `layout.source_dirs` arrive mid-run; if the
    next agent's mount config still reflects the orchestrator-start
    snapshot, the barrier silently leaks the new directories.
    """

    def test_amended_source_dirs_visible_in_next_agent_args(self) -> None:
        config = PipelineConfig()
        config.source_dirs = ["src"]
        # Cache barriers as the orchestrator does at startup.
        cached_barriers = configure_barriers(["src"])

        # Architect amends source_dirs mid-run.
        config.source_dirs = ["apps/api/src", "apps/web/src"]

        with tempfile.TemporaryDirectory() as tmp:
            make_project(Path(tmp))
            args = build_agent_args(
                AgentRole.SCENARIO_ARCHITECT, config, cached_barriers,
                "run1", tmp,
            )
            joined = " ".join(args)
            # New paths must be masked.
            self.assertIn("apps/api/src", joined)
            self.assertIn("apps/web/src", joined)
            # Stale `src` must NOT appear as a tmpfs mask target.
            tmpfs_args = [a for a in args if "type=tmpfs" in a]
            self.assertFalse(
                any(a.endswith("/src") and "apps" not in a for a in tmpfs_args),
                f"stale src/ still masked: {tmpfs_args}",
            )


    def test_claude_args_barrier_prompt_does_not_enumerate_source_dirs(self) -> None:
        # build_claude_args reads denied_tools and barrier_prompt off
        # the cached barriers dict — this is only safe if neither
        # interpolates source_dirs. Lock that assumption in: the SA
        # prompt must not reference any specific source path.
        config = PipelineConfig()
        cached = configure_barriers(["src"])
        args = build_claude_args(
            AgentRole.SCENARIO_ARCHITECT, config, cached,
        )
        joined = " ".join(args)
        self.assertNotIn("src/", joined)
        self.assertNotIn("apps/", joined)


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

    def _captured_docker_args(self, role: AgentRole, egress_filtering: bool, services=None) -> list[str]:
        """Invoke invoke_agent with a mocked execute_container and return captured docker_args."""
        from unittest.mock import patch
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.docker.containers import RunResult

        with tempfile.TemporaryDirectory() as tmpdir:
            make_project(Path(tmpdir))
            result_json = '{"result": "success", "session_id": "test-session-id", "is_error": false}'
            mock_result = RunResult(exit_code=0, stdout=result_json, stderr="")

            captured: list[list[str]] = []

            def fake_execute(docker_args, image, claude_args, **kwargs):
                captured.append(list(docker_args))
                return mock_result

            config = PipelineConfig(egress_filtering=egress_filtering, services=services or [])
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

    def test_no_proxy_includes_service_hostnames(self) -> None:
        """Service container hostnames are exempted from proxy."""
        from darkfactory.models.config import ServiceDef

        services = [
            ServiceDef(name="postgres", image="postgres:16"),
            ServiceDef(name="redis", image="redis:7"),
        ]
        args = self._captured_docker_args(
            AgentRole.SPEC_VALIDATOR, egress_filtering=True, services=services,
        )
        self.assertIn("NO_PROXY=localhost,127.0.0.1,postgres,redis", args)
        self.assertIn("no_proxy=localhost,127.0.0.1,postgres,redis", args)


class TestExtraDockerArgs(unittest.TestCase):
    """Tests for invoke_agent extra_docker_args (contracts RO mount)."""

    def _captured_docker_args(
        self,
        role: AgentRole,
        extra_docker_args: list[str] | None = None,
    ) -> list[str]:
        from unittest.mock import patch
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.docker.containers import RunResult

        with tempfile.TemporaryDirectory() as tmpdir:
            make_project(Path(tmpdir))
            result_json = '{"result": "success", "session_id": "test-session-id", "is_error": false}'
            mock_result = RunResult(exit_code=0, stdout=result_json, stderr="")
            captured: list[list[str]] = []

            def fake_execute(docker_args, image, claude_args, **kwargs):
                captured.append(list(docker_args))
                return mock_result

            config = PipelineConfig()
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
                    extra_docker_args=extra_docker_args,
                )

            return captured[0]

    def test_extra_args_appended(self) -> None:
        ro_mount = ["-v", "/host/_contracts:/project/_contracts:ro"]
        args = self._captured_docker_args(AgentRole.TEST_ENGINEER, extra_docker_args=ro_mount)
        self.assertIn("-v", args)
        self.assertIn("/host/_contracts:/project/_contracts:ro", args)

    def test_extra_args_after_standard_mounts(self) -> None:
        """RO mount must appear after the RW overlay so Docker specificity wins."""
        ro_mount = ["-v", "/host/_contracts:/project/.dark-factory/scenarios/spec/_contracts:ro"]
        args = self._captured_docker_args(AgentRole.TEST_ENGINEER, extra_docker_args=ro_mount)
        # Find the RW scenarios mount and the RO contracts mount.
        rw_idx = next(
            (i for i, a in enumerate(args) if "scenarios" in a and ":rw" in a),
            -1,
        )
        ro_idx = next(
            (i for i, a in enumerate(args) if "_contracts" in a and ":ro" in a),
            -1,
        )
        self.assertGreater(rw_idx, -1, "RW scenarios mount not found")
        self.assertGreater(ro_idx, -1, "RO contracts mount not found")
        self.assertGreater(ro_idx, rw_idx, "RO mount must come after RW mount")

    def test_none_extra_args_no_effect(self) -> None:
        args_without = self._captured_docker_args(AgentRole.TEST_ENGINEER, extra_docker_args=None)
        args_empty = self._captured_docker_args(AgentRole.TEST_ENGINEER, extra_docker_args=[])
        # Both should produce the same args (no extra mounts).
        self.assertFalse(any("_contracts" in a for a in args_without))
        self.assertFalse(any("_contracts" in a for a in args_empty))


if __name__ == "__main__":
    unittest.main()
