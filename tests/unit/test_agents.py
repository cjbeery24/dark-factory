"""Tests for pipeline/agents.py — agent invocation pre-flight checks."""

import json
import tempfile
import unittest
from pathlib import Path
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
        from tests.helpers import make_project
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.docker.barriers import configure_barriers
        from darkfactory.models.agent import AgentRole
        from darkfactory.models.config import PipelineConfig

        config = PipelineConfig(language="python")
        barriers = configure_barriers(["src"])
        ok_result = RunResult(exit_code=0, stdout="x" * 100, stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            make_project(Path(tmpdir))
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


class TestFailureDump(unittest.TestCase):
    """Tests for _write_failure_dump and its integration with invoke_agent."""

    def _make_config_and_barriers(self, language="python"):
        from darkfactory.docker.barriers import configure_barriers
        from darkfactory.models.config import PipelineConfig
        config = PipelineConfig(language=language)
        barriers = configure_barriers(["src"])
        return config, barriers

    def _invoke_failing(self, tmpdir, *, dry_run=False, stdout="agent output", stderr="error"):
        """Call invoke_agent with execute_container always returning exit_code=1."""
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.models.agent import AgentRole
        config, barriers = self._make_config_and_barriers()
        fail_result = RunResult(exit_code=1, stdout=stdout, stderr=stderr)
        with patch("darkfactory.pipeline.agents.check_language_image"), \
             patch("darkfactory.pipeline.agents._run_safety_checks"), \
             patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]), \
             patch("darkfactory.pipeline.agents.build_claude_args", return_value=[]), \
             patch("darkfactory.pipeline.agents.execute_container", return_value=fail_result):
            return invoke_agent(
                AgentRole.IMPLEMENTER,
                prompt="test",
                config=config,
                barriers=barriers,
                run_id="abcd1234efgh5678",
                project_dir=Path(tmpdir),
                spec_name="my-spec",
                dry_run=dry_run,
            )

    def test_timeout_failure_dump_captures_partial_stream(self):
        # Sprint 33 — the whole point: a SIGKILL'd (timeout) agent now leaves a
        # PARTIAL stream-json transcript in its failure dump (previously empty,
        # because --output-format=json buffered the envelope and lost it on kill).
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.models.agent import AgentRole
        from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT
        partial_stream = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": "fr1-workspace.test.ts"}}]}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": "fr2-typecheck.test.ts"}}]}}),
        ])  # NO result line — killed mid-stream
        config, barriers = self._make_config_and_barriers()
        killed = RunResult(exit_code=EXIT_TIMEOUT, stdout=partial_stream, stderr="")
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("darkfactory.pipeline.agents.check_language_image"), \
                 patch("darkfactory.pipeline.agents._run_safety_checks"), \
                 patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.build_claude_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.execute_container", return_value=killed):
                result = invoke_agent(
                    AgentRole.TEST_ENGINEER, prompt="t", config=config, barriers=barriers,
                    run_id="run1", project_dir=Path(tmpdir), spec_name="my-spec",
                )
            self.assertTrue(result.timed_out)
            dumps = list((Path(tmpdir) / ".dark-factory" / "failures").glob("failure_*.json"))
            self.assertEqual(len(dumps), 1)
            dumped_stdout = json.loads(dumps[0].read_text())["stdout"]
            # The partial transcript is captured — we can SEE what the agent did.
            self.assertIn("fr1-workspace.test.ts", dumped_stdout)
            self.assertIn("fr2-typecheck.test.ts", dumped_stdout)
            self.assertIn("tool_use", dumped_stdout)

    def test_failure_dump_written_on_exhausted_retries(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._invoke_failing(tmpdir)
            self.assertNotEqual(result.exit_code, 0)
            failures_dir = Path(tmpdir) / ".dark-factory" / "failures"
            dumps = list(failures_dir.glob("failure_*.json"))
            self.assertEqual(len(dumps), 1)
            data = json.loads(dumps[0].read_text())
            self.assertEqual(data["agent_role"], "implementer")
            self.assertEqual(data["spec_name"], "my-spec")
            self.assertIn("abcd1234", data["run_id"])

    def test_failure_dump_not_written_on_success(self):
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.models.agent import AgentRole
        config, barriers = self._make_config_and_barriers()
        # A realistic clean run: stream-json ending in a result envelope (Sprint
        # 33 — a non-JSON placeholder would now correctly trip the exit-0/no-result
        # guard, since a real successful agent always emits a {"type":"result"} line).
        ok_stream = json.dumps({"type": "result", "subtype": "success",
                                "result": "done", "usage": {"output_tokens": 50}})
        ok_result = RunResult(exit_code=0, stdout=ok_stream, stderr="")
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("darkfactory.pipeline.agents.check_language_image"), \
                 patch("darkfactory.pipeline.agents._run_safety_checks"), \
                 patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.build_claude_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.execute_container", return_value=ok_result):
                invoke_agent(
                    AgentRole.IMPLEMENTER, prompt="test",
                    config=config, barriers=barriers,
                    run_id="run1", project_dir=Path(tmpdir),
                )
            failures_dir = Path(tmpdir) / ".dark-factory" / "failures"
            self.assertFalse(failures_dir.exists())

    def test_failure_dump_not_written_in_dry_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._invoke_failing(tmpdir, dry_run=True)
            failures_dir = Path(tmpdir) / ".dark-factory" / "failures"
            self.assertFalse(failures_dir.exists())

    def test_failure_dump_head_tail_truncation(self):
        from darkfactory.pipeline.agents import _write_failure_dump
        from darkfactory.models.agent import AgentRole
        long_stdout = "A" * 25_000 + "M" * 60_000 + "Z" * 25_000
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_failure_dump(
                Path(tmpdir), AgentRole.IMPLEMENTER, "spec", "run1234",
                1, 99, False, 1.0, 1, [], long_stdout, "",
            )
            dumps = list((Path(tmpdir) / ".dark-factory" / "failures").glob("*.json"))
            data = json.loads(dumps[0].read_text())
            self.assertTrue(data["stdout_truncated"])
            self.assertTrue(data["stdout"].startswith("A" * 25_000))
            self.assertTrue(data["stdout"].endswith("Z" * 25_000))
            self.assertIn("chars omitted", data["stdout"])

    def test_failure_dump_short_stdout_not_truncated(self):
        from darkfactory.pipeline.agents import _write_failure_dump
        from darkfactory.models.agent import AgentRole
        short_stdout = "hello world"
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_failure_dump(
                Path(tmpdir), AgentRole.IMPLEMENTER, "spec", "run1234",
                1, 99, False, 1.0, 1, [], short_stdout, "",
            )
            dumps = list((Path(tmpdir) / ".dark-factory" / "failures").glob("*.json"))
            data = json.loads(dumps[0].read_text())
            self.assertFalse(data["stdout_truncated"])
            self.assertEqual(data["stdout"], short_stdout)

    def test_failure_dump_write_error_does_not_mask_original(self):
        """_write_failure_dump swallows internal errors — dump failure never propagates."""
        import pathlib
        from darkfactory.pipeline.agents import _write_failure_dump
        from darkfactory.models.agent import AgentRole
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(pathlib.Path, "write_text", side_effect=PermissionError("no write")):
                # Should not raise — all errors inside _write_failure_dump are swallowed.
                _write_failure_dump(
                    Path(tmpdir), AgentRole.IMPLEMENTER, "spec", "run1234",
                    1, 99, False, 1.0, 1, [], "stdout", "stderr",
                )


class TestExtractResultEnvelope(unittest.TestCase):
    """Sprint 33: reconstruct the legacy result envelope from a stream-json run."""

    def _stream(self, with_result=True):
        lines = [
            json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": "fr1.test.ts"}}]}}),
        ]
        if with_result:
            lines.append(json.dumps({
                "type": "result", "subtype": "success",
                "result": "wrote 3 tests",
                "usage": {"input_tokens": 12, "output_tokens": 4500,
                          "cache_read_input_tokens": 900000}}))
        return "\n".join(lines)

    def test_stream_json_result_line_extracted(self):
        from darkfactory.pipeline.agents import (
            _extract_result_envelope, _extract_usage, extract_agent_text)
        env = _extract_result_envelope(self._stream(with_result=True))
        self.assertEqual(json.loads(env)["type"], "result")
        self.assertEqual(extract_agent_text(env), "wrote 3 tests")
        self.assertEqual(_extract_usage(env)["output_tokens"], 4500)
        self.assertEqual(_extract_usage(env)["cache_read_input_tokens"], 900000)

    def test_sigkilled_stream_no_result_line_yields_empty(self):
        # The timeout case: partial stream, no result envelope → "" (legacy
        # empty-stdout behavior). The raw stream is preserved elsewhere (dump).
        from darkfactory.pipeline.agents import _extract_result_envelope
        partial = self._stream(with_result=False)
        self.assertEqual(_extract_result_envelope(partial), "")
        self.assertIn("fr1.test.ts", partial)  # raw still holds the transcript

    def test_single_nonresult_line_sigkill_yields_empty(self):
        # The most likely early-timeout shape: SIGKILL'd after exactly one line
        # (claude's first emission is the system-init event). Must NOT be returned
        # as the envelope — it's not a result (Codex P2 / Linus C2).
        from darkfactory.pipeline.agents import _extract_result_envelope, extract_agent_text
        for one_line in (
            json.dumps({"type": "system", "subtype": "init", "session_id": "s1", "tools": []}),
            json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}),
        ):
            with self.subTest(line=one_line[:30]):
                self.assertEqual(_extract_result_envelope(one_line), "")
                # and downstream gets nothing, not the raw stream event
                self.assertEqual(extract_agent_text(_extract_result_envelope(one_line)), "")

    def test_legacy_single_json_passthrough(self):
        from darkfactory.pipeline.agents import _extract_result_envelope, _extract_usage
        legacy = json.dumps({"type": "result", "result": "ok", "usage": {"output_tokens": 9}})
        self.assertEqual(_extract_result_envelope(legacy), legacy)
        self.assertEqual(_extract_usage(_extract_result_envelope(legacy))["output_tokens"], 9)

    def test_empty_and_garbage(self):
        from darkfactory.pipeline.agents import _extract_result_envelope
        self.assertEqual(_extract_result_envelope(""), "")
        self.assertEqual(_extract_result_envelope("   "), "")
        self.assertEqual(_extract_result_envelope("not json at all\nstill not"), "")


class TestExtractAgentText(unittest.TestCase):
    """Tests for extract_agent_text — JSON output unwrapping."""

    def test_extracts_result_from_json(self) -> None:
        from darkfactory.pipeline.agents import extract_agent_text
        raw = json.dumps({"result": "# Escalation Report\n\nDetails here.", "session_id": "abc"})
        self.assertEqual(extract_agent_text(raw), "# Escalation Report\n\nDetails here.")

    def test_plain_text_passthrough(self) -> None:
        from darkfactory.pipeline.agents import extract_agent_text
        self.assertEqual(extract_agent_text("plain markdown"), "plain markdown")

    def test_malformed_json_passthrough(self) -> None:
        from darkfactory.pipeline.agents import extract_agent_text
        self.assertEqual(extract_agent_text("{bad json"), "{bad json")

    def test_empty_string(self) -> None:
        from darkfactory.pipeline.agents import extract_agent_text
        self.assertEqual(extract_agent_text(""), "")

    def test_json_without_result_key(self) -> None:
        from darkfactory.pipeline.agents import extract_agent_text
        raw = json.dumps({"messages": [], "session_id": "abc"})
        self.assertEqual(extract_agent_text(raw), raw)


class TestResolveMaxOutputTokens(unittest.TestCase):
    """Tests for resolve_max_output_tokens — per-model output-cap derivation."""

    def test_family_derivation(self):
        from darkfactory.pipeline.agents import resolve_max_output_tokens as r
        self.assertEqual(r("claude-sonnet-4-6"), 64000)
        self.assertEqual(r("claude-haiku-4-5"), 64000)
        self.assertEqual(r("claude-opus-4-8"), 128000)
        self.assertEqual(r("claude-fable-5"), 128000)

    def test_dated_suffix_still_matches_family(self):
        from darkfactory.pipeline.agents import resolve_max_output_tokens as r
        # model ids carry date suffixes; substring match must still resolve.
        self.assertEqual(r("claude-sonnet-4-20250514"), 64000)

    def test_unknown_model_gets_conservative_floor(self):
        from darkfactory.pipeline.agents import resolve_max_output_tokens as r
        # still 2x Claude Code's 32000 unset default.
        self.assertEqual(r("some-future-model"), 64000)
        self.assertEqual(r(""), 64000)

    def test_positive_override_wins(self):
        from darkfactory.pipeline.agents import resolve_max_output_tokens as r
        self.assertEqual(r("claude-sonnet-4-6", 100000), 100000)
        # zero / negative override falls back to family derivation.
        self.assertEqual(r("claude-sonnet-4-6", 0), 64000)
        self.assertEqual(r("claude-sonnet-4-6", -5), 64000)


class TestMaxOutputTokensCap(unittest.TestCase):
    """The output-token cap env var is injected into every agent container."""

    def _make_config_and_barriers(self, language="python"):
        from darkfactory.models.config import PipelineConfig
        from darkfactory.docker.barriers import configure_barriers
        return PipelineConfig(language=language), configure_barriers(["src"])

    def test_cap_env_injected_derived_from_role_model(self):
        from darkfactory.pipeline.agents import _build_full_agent_docker_args
        from darkfactory.models.agent import AgentRole
        config, barriers = self._make_config_and_barriers()
        # default model_id is sonnet → 64000.
        with patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]):
            args = _build_full_agent_docker_args(
                AgentRole.TEST_ENGINEER, config=config, barriers=barriers,
                run_id="r1", project_dir=Path("/tmp/x"),
            )
        self.assertIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS=64000", args)

    def test_cap_env_respects_per_role_model_id(self):
        from darkfactory.pipeline.agents import _build_full_agent_docker_args
        from darkfactory.models.agent import AgentRole
        config, barriers = self._make_config_and_barriers()
        config.model_ids = {"test-engineer": "claude-opus-4-8"}
        with patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]):
            args = _build_full_agent_docker_args(
                AgentRole.TEST_ENGINEER, config=config, barriers=barriers,
                run_id="r1", project_dir=Path("/tmp/x"),
            )
        self.assertIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000", args)

    def test_cap_env_respects_config_override(self):
        from darkfactory.pipeline.agents import _build_full_agent_docker_args
        from darkfactory.models.agent import AgentRole
        config, barriers = self._make_config_and_barriers()
        config.max_output_tokens = 90000
        with patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]):
            args = _build_full_agent_docker_args(
                AgentRole.IMPLEMENTER, config=config, barriers=barriers,
                run_id="r1", project_dir=Path("/tmp/x"),
            )
        self.assertIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS=90000", args)

    def test_session_start_forwards_model_override_to_cap(self):
        # Linus C1: a warm session bakes the cap at docker-run time; a
        # SWITCH_MODEL override must be honored at session start or the cap
        # tracks the configured model, not the running one.
        from darkfactory.pipeline.agents import start_agent_session
        from darkfactory.models.agent import AgentRole
        config, barriers = self._make_config_and_barriers()  # default sonnet → 64000
        captured = {}

        def _capture_start(docker_args, image, cmd, **kw):
            captured["args"] = docker_args
            return "container123"

        with patch("darkfactory.pipeline.agents.check_language_image"), \
             patch("darkfactory.pipeline.agents._run_safety_checks"), \
             patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]), \
             patch("darkfactory.pipeline.agents.start_container", side_effect=_capture_start):
            start_agent_session(
                AgentRole.IMPLEMENTER, config=config, barriers=barriers,
                run_id="r1", project_dir=Path("/tmp/x"),
                model_override="claude-opus-4-8",
            )
        self.assertIn("CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000", captured["args"])


class TestMaxOutputTokensOverflowShortCircuit(unittest.TestCase):
    """A cap-overflow agent run is NON-RETRYABLE (no second agent_timeout)."""

    def _make_config_and_barriers(self, language="python"):
        from darkfactory.models.config import PipelineConfig
        from darkfactory.docker.barriers import configure_barriers
        # agent_retries default is 1 → max_attempts 2; a normal failure retries
        # once. The overflow short-circuit must stop after a SINGLE attempt.
        return PipelineConfig(language=language), configure_barriers(["src"])

    def test_overflow_detector(self):
        from darkfactory.pipeline.agents import _is_max_output_tokens_overflow as f
        # Future-CLI error field (belt-and-suspenders).
        self.assertTrue(f('{"type":"assistant","error":"max_output_tokens"}'))
        # The signal actually observed in the S33 dump — prose anchored to the
        # "API Error:" envelope on one line.
        self.assertTrue(f(
            "API Error: Claude's response exceeded the 32000 output token "
            "maximum. To configure this behavior, set the "
            "CLAUDE_CODE_MAX_OUTPUT_TOKENS environment variable."))
        self.assertFalse(f("normal stream output"))
        self.assertFalse(f(""))
        # A scenario file that merely QUOTES the phrase (no "API Error:" prefix
        # on the line) must NOT be misread as a cap overflow.
        self.assertFalse(f(
            'expect(msg).toBe("exceeded the 8 output token maximum allowed")'))
        # Comma-grouped count, in case a future CLI reformats the number — a
        # miss here would reopen the 900s hang (Codex A1-a).
        self.assertTrue(f(
            "API Error: Claude's response exceeded the 32,000 output token maximum."))

    def test_overflow_is_not_retried(self):
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.models.agent import AgentRole
        from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT
        config, barriers = self._make_config_and_barriers()
        # Prose-only stream matching the REAL S33 dump (no "error" field — the
        # live CLI does not emit one; the prose in the assistant text is the
        # actual signal).
        overflow_stream = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "message": {
                "model": "<synthetic>", "content": [{"type": "text", "text":
                    "API Error: Claude's response exceeded the 32000 output token "
                    "maximum. To configure this behavior, set the "
                    "CLAUDE_CODE_MAX_OUTPUT_TOKENS environment variable."}]}}),
        ])
        killed = RunResult(exit_code=EXIT_TIMEOUT, stdout=overflow_stream, stderr="")
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("darkfactory.pipeline.agents.check_language_image"), \
                 patch("darkfactory.pipeline.agents._run_safety_checks"), \
                 patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.build_claude_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.execute_container",
                       return_value=killed) as mock_exec:
                result = invoke_agent(
                    AgentRole.TEST_ENGINEER, prompt="t", config=config,
                    barriers=barriers, run_id="run1",
                    project_dir=Path(tmpdir), spec_name="my-spec",
                )
            # Single attempt — NOT retried (a normal exit-124 would run twice).
            self.assertEqual(mock_exec.call_count, 1)
            self.assertEqual(result.exit_code, EXIT_TIMEOUT)
            # Failure dump still captures the partial transcript (observability).
            dumps = list((Path(tmpdir) / ".dark-factory" / "failures").glob("failure_*.json"))
            self.assertEqual(len(dumps), 1)
            self.assertIn("output token maximum", dumps[0].read_text())

    def test_ordinary_timeout_still_retries(self):
        # Guard: a plain timeout WITHOUT the overflow signature retries as before.
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.models.agent import AgentRole
        from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT
        config, barriers = self._make_config_and_barriers()
        killed = RunResult(exit_code=EXIT_TIMEOUT, stdout="partial work, no error", stderr="")
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("darkfactory.pipeline.agents.check_language_image"), \
                 patch("darkfactory.pipeline.agents._run_safety_checks"), \
                 patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.build_claude_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.execute_container",
                       return_value=killed) as mock_exec:
                invoke_agent(
                    AgentRole.TEST_ENGINEER, prompt="t", config=config,
                    barriers=barriers, run_id="run1",
                    project_dir=Path(tmpdir), spec_name="my-spec",
                )
            self.assertEqual(mock_exec.call_count, 2)  # retried once


if __name__ == "__main__":
    unittest.main()
