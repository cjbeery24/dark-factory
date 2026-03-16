"""Tests for pipeline/agents.py — agent invocation pre-flight checks."""

import unittest
from unittest.mock import patch

from darkfactory.errors import ConfigError, TransientFailure
from darkfactory.utils.subprocess_runner import RunResult


def _ok():
    return RunResult(exit_code=0, stdout="", stderr="")


def _fail():
    return RunResult(exit_code=1, stdout="", stderr="error")


def _daemon_ok_image_fail(cmd, **kw):
    """Simulate daemon running but language image missing."""
    if cmd == ["docker", "info"]:
        return _ok()
    return _fail()


class TestCheckLanguageImageFromAgents(unittest.TestCase):
    """Tests for check_language_image as called from invoke_agent.

    check_language_image lives in containers.py but is imported and called
    inside invoke_agent. These tests verify the call and error propagation.
    """

    def _invoke_with_patched_image_check(self, side_effect):
        """Call invoke_agent with check_language_image patched to the given side_effect."""
        import tempfile
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.docker.barriers import configure_barriers
        from darkfactory.models.agent import AgentRole
        from darkfactory.models.config import PipelineConfig

        config = PipelineConfig(language="rust")
        barriers = configure_barriers(["src"])

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("darkfactory.pipeline.agents.check_language_image", side_effect=side_effect), \
                 patch("darkfactory.pipeline.agents.execute_container"), \
                 patch("darkfactory.pipeline.agents._run_safety_checks"):
                invoke_agent(
                    AgentRole.SPEC_VALIDATOR,
                    prompt="test",
                    config=config,
                    barriers=barriers,
                    run_id="testrun",
                    project_dir=tmpdir,  # type: ignore[arg-type]
                    spec_name="my-spec",
                )

    def test_config_error_raised_when_image_missing(self):
        """ConfigError from check_language_image propagates out of invoke_agent unchanged."""
        err = ConfigError("Spec 'my-spec': Docker image 'dark-factory-rust' is not available.")
        with self.assertRaises(ConfigError) as ctx:
            self._invoke_with_patched_image_check(side_effect=err)
        # Verify invoke_agent does not swallow or re-wrap the error.
        self.assertIs(ctx.exception, err)

    def test_transient_failure_raised_when_daemon_down(self):
        """TransientFailure from check_language_image propagates out of invoke_agent unchanged."""
        err = TransientFailure("Docker daemon is not running.")
        with self.assertRaises(TransientFailure) as ctx:
            self._invoke_with_patched_image_check(side_effect=err)
        self.assertIs(ctx.exception, err)

    def test_check_language_image_called_before_safety_checks(self):
        """check_language_image is called before _run_safety_checks."""
        call_order: list[str] = []

        def record_image_check(language, spec_name=""):
            call_order.append("image_check")

        def record_safety_checks(*args, **kwargs):
            call_order.append("safety_checks")

        import tempfile
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.docker.barriers import configure_barriers
        from darkfactory.models.agent import AgentRole
        from darkfactory.models.config import PipelineConfig

        config = PipelineConfig(language="python")
        barriers = configure_barriers(["src"])
        ok_result = RunResult(exit_code=0, stdout="x" * 100, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("darkfactory.pipeline.agents.check_language_image", side_effect=record_image_check), \
                 patch("darkfactory.pipeline.agents._run_safety_checks", side_effect=record_safety_checks), \
                 patch("darkfactory.pipeline.agents.execute_container", return_value=ok_result):
                invoke_agent(
                    AgentRole.SPEC_VALIDATOR,
                    prompt="test",
                    config=config,
                    barriers=barriers,
                    run_id="testrun",
                    project_dir=tmpdir,  # type: ignore[arg-type]
                )

        self.assertEqual(call_order[0], "image_check")
        self.assertEqual(call_order[1], "safety_checks")


if __name__ == "__main__":
    unittest.main()
