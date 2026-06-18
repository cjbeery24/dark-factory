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
        # Sprint 33 + P1 — a SIGKILL'd (timeout) agent leaves its PARTIAL
        # stream-json transcript in a failure dump. P1: EVERY timed-out attempt is
        # dumped in-loop (not just the final one), with the FULL transcript.
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
        config, barriers = self._make_config_and_barriers()  # agent_retries=1 → 2 attempts
        killed = RunResult(exit_code=EXIT_TIMEOUT, timed_out=True, stdout=partial_stream, stderr="")
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
            dumps = sorted((Path(tmpdir) / ".dark-factory" / "failures").glob("failure_*.json"))
            # P1: BOTH timed-out attempts are dumped (was 1 — only the last).
            self.assertEqual(len(dumps), 2)
            names = " ".join(p.name for p in dumps)
            self.assertIn("attempt1-timeout", names)
            self.assertIn("attempt2-timeout", names)
            for d in dumps:
                rec = json.loads(d.read_text())
                self.assertTrue(rec["timed_out"])
                # Full capture — small stream is NOT truncated, middle preserved.
                self.assertFalse(rec["stdout_truncated"])
                self.assertIn("fr1-workspace.test.ts", rec["stdout"])
                self.assertIn("fr2-typecheck.test.ts", rec["stdout"])

    def test_timeout_then_success_still_dumps_the_timeout(self):
        # P1 — the exact gap this run exposed: a timeout followed by a SUCCESS
        # left NO dump (post-loop only fired on a non-zero FINAL result). Now the
        # timed-out attempt is captured even though the invoke ultimately succeeds.
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.models.agent import AgentRole
        from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT
        timed = RunResult(exit_code=EXIT_TIMEOUT, timed_out=True, stdout="partial work before kill", stderr="")
        ok_stream = json.dumps({"type": "result", "result": "done",
                                "usage": {"output_tokens": 5}}) + "\n"
        ok = RunResult(exit_code=0, stdout=ok_stream, stderr="")
        config, barriers = self._make_config_and_barriers()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("darkfactory.pipeline.agents.check_language_image"), \
                 patch("darkfactory.pipeline.agents._run_safety_checks"), \
                 patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.build_claude_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.execute_container",
                       side_effect=[timed, ok]):  # attempt1 timeout, attempt2 success
                result = invoke_agent(
                    AgentRole.IMPLEMENTER, prompt="t", config=config, barriers=barriers,
                    run_id="run1", project_dir=Path(tmpdir), spec_name="my-spec",
                )
            self.assertEqual(result.exit_code, 0)  # invoke succeeded overall
            dumps = list((Path(tmpdir) / ".dark-factory" / "failures").glob("failure_*.json"))
            self.assertEqual(len(dumps), 1)  # the timed-out attempt-1 IS captured
            rec = json.loads(dumps[0].read_text())
            self.assertTrue(rec["timed_out"])
            self.assertIn("attempt1-timeout", dumps[0].name)
            self.assertIn("partial work before kill", rec["stdout"])

    def test_cap_overflow_timeout_dumps_exactly_once(self):
        # P1 partition (Linus): a cap-overflow is exit-124/timed_out, so the
        # in-loop timeout dump fires, THEN the S34 non-retryable short-circuit
        # breaks. The post-loop dump must skip it (not timed_out) → exactly ONE
        # dump, not a redundant second.
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.models.agent import AgentRole
        from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT
        overflow = ("API Error: Claude's response exceeded the 32000 output token "
                    "maximum. To configure this behavior, set the "
                    "CLAUDE_CODE_MAX_OUTPUT_TOKENS environment variable.")
        killed = RunResult(exit_code=EXIT_TIMEOUT, timed_out=True, stdout=overflow, stderr="")
        config, barriers = self._make_config_and_barriers()
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("darkfactory.pipeline.agents.check_language_image"), \
                 patch("darkfactory.pipeline.agents._run_safety_checks"), \
                 patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.build_claude_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.execute_container",
                       return_value=killed) as mock_exec:
                invoke_agent(
                    AgentRole.TEST_ENGINEER, prompt="t", config=config, barriers=barriers,
                    run_id="run1", project_dir=Path(tmpdir), spec_name="my-spec",
                )
            self.assertEqual(mock_exec.call_count, 1)  # cap-overflow is non-retryable
            dumps = list((Path(tmpdir) / ".dark-factory" / "failures").glob("failure_*.json"))
            self.assertEqual(len(dumps), 1)  # one in-loop dump, post-loop skipped
            self.assertIn("attempt1-timeout", dumps[0].name)

    def test_timeout_dump_preserves_middle_of_large_stream(self):
        # P1 — full-capture: a stream larger than the default 25k head/tail but
        # under the timeout limit is captured whole, so the MIDDLE (where the tool
        # calls / writes live) survives instead of being elided.
        from darkfactory.pipeline.agents import invoke_agent
        from darkfactory.models.agent import AgentRole
        from darkfactory.utils.subprocess_runner import EXIT_TIMEOUT
        middle = "MIDDLE_MARKER_" + ("x" * 40_000) + "_MIDDLE_END"  # ~40k, in the elided zone
        big = ("H" * 26_000) + middle + ("T" * 26_000)  # > 50k default head+tail
        config, barriers = self._make_config_and_barriers()
        killed = RunResult(exit_code=EXIT_TIMEOUT, timed_out=True, stdout=big, stderr="")
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("darkfactory.pipeline.agents.check_language_image"), \
                 patch("darkfactory.pipeline.agents._run_safety_checks"), \
                 patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.build_claude_args", return_value=[]), \
                 patch("darkfactory.pipeline.agents.execute_container", return_value=killed):
                invoke_agent(
                    AgentRole.TEST_ENGINEER, prompt="t", config=config, barriers=barriers,
                    run_id="run1", project_dir=Path(tmpdir), spec_name="my-spec",
                )
            dumps = list((Path(tmpdir) / ".dark-factory" / "failures").glob("failure_*.json"))
            rec = json.loads(dumps[0].read_text())
            self.assertFalse(rec["stdout_truncated"])  # under the timeout limit → whole
            # The full elided-zone content survives end-to-end (start AND end of
            # the middle), and nothing was omitted.
            self.assertIn("MIDDLE_MARKER_", rec["stdout"])
            self.assertIn("_MIDDLE_END", rec["stdout"])
            self.assertNotIn("chars omitted", rec["stdout"])
            self.assertEqual(len(rec["stdout"]), len(big))  # byte-for-byte whole

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

    def test_failure_dump_embeds_stream_metrics(self):
        # S0(a): the dump record carries the parsed tool-mix + write-conversion
        # so a human sees exploration-vs-generation without re-parsing the stream.
        from darkfactory.pipeline.agents import _write_failure_dump
        from darkfactory.models.agent import AgentRole
        stream = "\n".join([
            json.dumps({"type": "system", "subtype": "init"}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/spec.md"}}]}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Glob", "input": {"pattern": "**/*"}}]}}),
        ])
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_failure_dump(
                Path(tmpdir), AgentRole.TEST_ENGINEER, "spec", "run1234",
                0, 124, True, 900.0, 1, [], stream, "",
            )
            dumps = list((Path(tmpdir) / ".dark-factory" / "failures").glob("*.json"))
            data = json.loads(dumps[0].read_text())
            sm = data["stream_metrics"]
            self.assertEqual(sm["num_assistant_messages"], 2)
            self.assertEqual(sm["tool_use_counts"], {"Read": 1, "Glob": 1})
            self.assertFalse(sm["wrote_any_scenario"])
            self.assertIsNone(sm["first_write_turn"])

    def test_failure_dump_writes_full_stream_sidecar_when_truncated(self):
        # S0(a): a stream too big for the head/tail window gets its FULL transcript
        # in a .stream.jsonl sidecar UNDER failures/ (inherits the S33 dir mask) so
        # the middle (where the tool calls live) is never lost.
        from darkfactory.pipeline.agents import _write_failure_dump
        from darkfactory.models.agent import AgentRole
        long_stdout = "A" * 25_000 + "MIDDLE_MARKER" + "M" * 60_000 + "Z" * 25_000
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_failure_dump(
                Path(tmpdir), AgentRole.TEST_ENGINEER, "spec", "run1234",
                0, 124, True, 900.0, 1, [], long_stdout, "",
            )
            failures = Path(tmpdir) / ".dark-factory" / "failures"
            data = json.loads(next(failures.glob("*.json")).read_text())
            self.assertTrue(data["stdout_truncated"])
            sidecar_name = data["full_stream_sidecar"]
            self.assertIsNotNone(sidecar_name)
            sidecar = failures / sidecar_name
            # Sidecar must live UNDER failures/ (inherits the barrier mask).
            self.assertEqual(sidecar.parent, failures)
            full = sidecar.read_text()
            self.assertEqual(full, long_stdout)  # full fidelity, no omission
            self.assertIn("MIDDLE_MARKER", full)  # the middle the JSON dump elides

    def test_failure_dump_no_sidecar_when_not_truncated(self):
        from darkfactory.pipeline.agents import _write_failure_dump
        from darkfactory.models.agent import AgentRole
        with tempfile.TemporaryDirectory() as tmpdir:
            _write_failure_dump(
                Path(tmpdir), AgentRole.TEST_ENGINEER, "spec", "run1234",
                0, 124, True, 900.0, 1, [], "short stream", "",
            )
            failures = Path(tmpdir) / ".dark-factory" / "failures"
            data = json.loads(next(failures.glob("*.json")).read_text())
            self.assertIsNone(data["full_stream_sidecar"])
            self.assertEqual(list(failures.glob("*.stream.jsonl")), [])

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


class TestStreamMetrics(unittest.TestCase):
    """S0(a): _parse_stream_metrics derives real turn/tool/write signals from a
    partial stream-json transcript (the only data a SIGKILL'd agent leaves)."""

    def test_exploration_bound_stream(self):
        # The spec-4 TE shape: all Read/Glob, zero scenario writes → the
        # exploration-vs-generation discriminator must surface it directly.
        from darkfactory.pipeline.agents import _parse_stream_metrics
        lines = [json.dumps({"type": "system", "subtype": "init"})]
        for _ in range(30):
            lines.append(json.dumps({"type": "assistant", "message": {
                "usage": {"output_tokens": 10},
                "content": [{"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}}]}}))
        for _ in range(10):
            lines.append(json.dumps({"type": "assistant", "message": {
                "usage": {"output_tokens": 5},
                "content": [{"type": "tool_use", "name": "Glob", "input": {"pattern": "*"}}]}}))
        m = _parse_stream_metrics("\n".join(lines))
        self.assertEqual(m["num_assistant_messages"], 40)
        self.assertEqual(m["total_tool_uses"], 40)
        self.assertEqual(m["tool_use_counts"], {"Read": 30, "Glob": 10})
        self.assertEqual(m["output_tokens_stream_lowerbound"], 30 * 10 + 10 * 5)
        self.assertFalse(m["wrote_any_scenario"])
        self.assertIsNone(m["first_write_turn"])
        self.assertEqual(m["scenario_write_count"], 0)

    def test_generation_stream_records_first_write_turn(self):
        from darkfactory.pipeline.agents import _parse_stream_metrics
        lines = [
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/spec.md"}}]}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": "fr1-x.test.ts"}}]}}),
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": "fr2-y.test.ts"}}]}}),
        ]
        m = _parse_stream_metrics("\n".join(lines))
        self.assertTrue(m["wrote_any_scenario"])
        self.assertEqual(m["first_write_turn"], 2)  # 2nd assistant msg
        self.assertEqual(m["scenario_write_count"], 2)

    def test_non_scenario_write_does_not_count(self):
        # A Write to a non-scenario path (e.g. a package.json) is NOT generation.
        from darkfactory.pipeline.agents import _parse_stream_metrics
        m = _parse_stream_metrics(json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": "pkg/project/package.json"}}]}}))
        self.assertFalse(m["wrote_any_scenario"])
        self.assertEqual(m["scenario_write_count"], 0)
        self.assertEqual(m["tool_use_counts"], {"Write": 1})  # still counted as a tool use

    def test_torn_tail_line_counted_not_fatal(self):
        from darkfactory.pipeline.agents import _parse_stream_metrics
        stream = (
            json.dumps({"type": "assistant", "message": {"content": []}}) + "\n"
            + '{"type":"assistant","message":{"content":[{"type":"tool_use",'  # torn
        )
        m = _parse_stream_metrics(stream)
        self.assertEqual(m["num_assistant_messages"], 1)
        self.assertEqual(m["unparseable_lines"], 1)

    def test_empty_stream(self):
        from darkfactory.pipeline.agents import _parse_stream_metrics
        m = _parse_stream_metrics("")
        self.assertEqual(m["num_assistant_messages"], 0)
        self.assertFalse(m["wrote_any_scenario"])

    def test_is_scenario_filename(self):
        from darkfactory.pipeline.agents import _is_scenario_filename
        for ok in ("fr1-x.test.ts", "a/b/foo.spec.mjs", "x.scenario.py",
                   "test_thing.py", "pkg/foo_test.go", "j.scenarios.md", "k.journey.md"):
            self.assertTrue(_is_scenario_filename(ok), ok)
        for no in ("", "package.json", "src/index.ts", "README.md", "tsconfig.json"):
            self.assertFalse(_is_scenario_filename(no), no)


class TestTelemetryV2(unittest.TestCase):
    """S0(a): telemetry schema v2 — adds stream metrics WITHOUT corrupting the
    billed output_tokens the cost path sums."""

    def _emit_and_read(self, usage, stream_metrics, envelope_present=True, tmpdir=None):
        from darkfactory.pipeline.agents import _emit_telemetry
        import contextlib
        ctx = contextlib.nullcontext(tmpdir) if tmpdir else tempfile.TemporaryDirectory()
        with ctx as d:
            _emit_telemetry(
                Path(d), run_id="r1", spec_name="s", role="test-engineer",
                iteration=0, phase="generate", wall_clock_ms=900000,
                usage=usage, exit_code=124, model_id="claude-x",
                stream_metrics=stream_metrics, envelope_present=envelope_present,
            )
            path = Path(d) / ".dark-factory" / "reports" / "run-telemetry.jsonl"
            return json.loads(path.read_text().strip())

    def test_timeout_line_keeps_billed_output_zero_but_records_stream_lowerbound(self):
        # The landmine Neo flagged: NEVER overwrite output_tokens (billed, summed
        # for cost) with the stream lower-bound. On a no-envelope timeout the
        # billed value is 0 and the stream value lives in its own field.
        usage = {"input_tokens": 0, "output_tokens": 0,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        sm = {"num_assistant_messages": 65, "total_tool_uses": 44,
              "tool_use_counts": {"Read": 30}, "output_tokens_stream_lowerbound": 2284,
              "first_write_turn": None, "scenario_write_count": 0,
              "wrote_any_scenario": False, "unparseable_lines": 0}
        rec = self._emit_and_read(usage, sm, envelope_present=False)
        self.assertEqual(rec["schema_version"], 2)
        self.assertEqual(rec["output_tokens"], 0)  # billed — NOT overwritten
        self.assertEqual(rec["output_tokens_stream_lowerbound"], 2284)
        self.assertEqual(rec["usage_source"], "stream_partial")
        self.assertEqual(rec["num_assistant_messages"], 65)
        self.assertFalse(rec["wrote_any_scenario"])
        self.assertIsNone(rec["first_write_turn"])

    def test_envelope_usage_source(self):
        usage = {"input_tokens": 100, "output_tokens": 50,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        rec = self._emit_and_read(usage, {"num_assistant_messages": 3})
        self.assertEqual(rec["usage_source"], "envelope")
        self.assertEqual(rec["output_tokens"], 50)

    def test_all_zero_but_present_envelope_classifies_as_envelope(self):
        # Codex nit: an envelope that arrives with all-zero tokens (valid on a
        # SIGKILL mid-response) must classify as "envelope", not "none" —
        # envelope_present is the signal, not token truthiness.
        usage = {"input_tokens": 0, "output_tokens": 0,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        rec = self._emit_and_read(usage, {}, envelope_present=True)
        self.assertEqual(rec["usage_source"], "envelope")

    def test_no_usage_no_stream_source_none(self):
        usage = {"input_tokens": 0, "output_tokens": 0,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        rec = self._emit_and_read(usage, {}, envelope_present=False)
        self.assertEqual(rec["usage_source"], "none")

    def test_v2_line_round_trips_through_parse_run_usage_without_cost_poison(self):
        # The seam test (Linus): the whole point of S0(a) is "never corrupt the
        # billed output_tokens the cost path sums". Emit a v2 line with a big
        # stream lower-bound + zero billed output, run the REAL cost consumer, and
        # assert it sums the billed zero (not the lower-bound) and ignores the new
        # fields. Guards the cross-module contract a future schema bump could break.
        from darkfactory.benchmark.scorecard import parse_run_usage
        usage = {"input_tokens": 0, "output_tokens": 0,
                 "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
        sm = {"num_assistant_messages": 65, "total_tool_uses": 44,
              "tool_use_counts": {"Read": 30, "Glob": 10},
              "output_tokens_stream_lowerbound": 999999,
              "first_write_turn": None, "scenario_write_count": 0,
              "wrote_any_scenario": False, "unparseable_lines": 0}
        with tempfile.TemporaryDirectory() as tmpdir:
            self._emit_and_read(usage, sm, envelope_present=False, tmpdir=tmpdir)
            telem = Path(tmpdir) / ".dark-factory" / "reports" / "run-telemetry.jsonl"
            ru = parse_run_usage(telem, run_id="r1")
            # billed output is the zero envelope value — the 999999 lower-bound is
            # NOT summed as cost.
            self.assertEqual(ru.output_tokens, 0)
            self.assertEqual(ru.agent_invocations, 1)


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


class TestArchitectScenariosBarrierWiring(unittest.TestCase):
    """P2: invoke_agent passes .dark-factory/scenarios as subtractable, so the
    architect's addendum-echo of that masked path is suppressed end-to-end —
    while a non-subtractable masked dir (holdouts) still fires."""

    def _make_config_and_barriers(self):
        from darkfactory.models.config import PipelineConfig
        from darkfactory.docker.barriers import configure_barriers
        config = PipelineConfig(language="node")
        return config, configure_barriers(config.source_dirs)

    def _invoke_with_output(self, tmpdir, role, output_text, prompt):
        from darkfactory.pipeline.agents import invoke_agent
        proj = Path(tmpdir)
        (proj / ".dark-factory" / "audit").mkdir(parents=True, exist_ok=True)
        audit_path = proj / ".dark-factory" / "audit" / "a.log"
        events = proj / ".dark-factory" / "events.jsonl"
        config, barriers = self._make_config_and_barriers()
        envelope = json.dumps({"type": "result", "result": output_text,
                               "usage": {"output_tokens": 5}}) + "\n"
        ok = RunResult(exit_code=0, stdout=envelope, stderr="")
        with patch("darkfactory.pipeline.agents.check_language_image"), \
             patch("darkfactory.pipeline.agents._run_safety_checks"), \
             patch("darkfactory.pipeline.agents.build_agent_args", return_value=[]), \
             patch("darkfactory.pipeline.agents.build_claude_args", return_value=[]), \
             patch("darkfactory.pipeline.agents.execute_container", return_value=ok):
            return invoke_agent(
                role, prompt=prompt, config=config, barriers=barriers,
                run_id="run1", project_dir=proj, spec_name="my-spec",
                audit_path=audit_path, events_file=events,
            )

    def test_architect_scenarios_echo_suppressed(self):
        from darkfactory.models.agent import AgentRole
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._invoke_with_output(
                tmpdir, AgentRole.ARCHITECT,
                output_text="entry_points resolve under .dark-factory/scenarios/my-spec/",
                # addendum-style prompt names the scenarios path → in excluded_text
                prompt="The Test Engineer writes to .dark-factory/scenarios/<spec>/*.test.ts",
            )
            self.assertNotIn(".dark-factory/scenarios", result.barrier_violations)

    def test_qa_holdout_scenarios_echo_STILL_fires_even_in_excluded_text(self):
        # Codex BLOCK fix: scenarios subtraction is ARCHITECT-ONLY, so qa-holdout
        # (not the architect) never has scenarios subtractable and fires even when
        # scenarios IS in its excluded_text (here via the prompt — modeling an
        # untrusted spec that names the path). A spec must not be able to suppress
        # a non-architect role's scenarios barrier signal.
        from darkfactory.models.agent import AgentRole
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._invoke_with_output(
                tmpdir, AgentRole.QA_HOLDOUT,
                output_text="copied .dark-factory/scenarios/my-spec/01-contract.test.ts",
                prompt="the spec discusses .dark-factory/scenarios/<spec>/ test layout",
            )
            self.assertIn(".dark-factory/scenarios", result.barrier_violations)

    def test_architect_holdouts_echo_still_fires(self):
        # Safety: holdouts is masked AND not subtractable → fires even if named
        # in the prompt. Confirms the fix didn't broaden suppression.
        from darkfactory.models.agent import AgentRole
        with tempfile.TemporaryDirectory() as tmpdir:
            result = self._invoke_with_output(
                tmpdir, AgentRole.ARCHITECT,
                output_text="saw .dark-factory/holdouts/my-spec/adv.test.ts",
                prompt="do not access .dark-factory/holdouts/",
            )
            self.assertIn(".dark-factory/holdouts", result.barrier_violations)


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
        killed = RunResult(exit_code=EXIT_TIMEOUT, timed_out=True, stdout=overflow_stream, stderr="")
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
        killed = RunResult(exit_code=EXIT_TIMEOUT, timed_out=True, stdout="partial work, no error", stderr="")
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


class TestAgentDefinitionInjection(unittest.TestCase):
    """D1: the architect injects only its marked schema SECTION; full-def roles
    inject their whole definition (unchanged)."""

    def test_architect_is_in_inject_roles(self):
        from darkfactory.pipeline.agents import _AGENT_DEF_INJECT_ROLES
        from darkfactory.models.agent import AgentRole
        self.assertIn(AgentRole.ARCHITECT, _AGENT_DEF_INJECT_ROLES)

    def test_architect_injects_only_marked_schema_section(self):
        from darkfactory.pipeline.agents import _agent_definition_for_injection
        from darkfactory.models.agent import AgentRole
        text = _agent_definition_for_injection(AgentRole.ARCHITECT)
        self.assertTrue(text)
        # The marked region carries the canonical schema...
        self.assertIn("file_map:", text)
        # ...but NOT the rest of the (305-line) definition (e.g. Amend Mode),
        # and the marker comments themselves are stripped.
        self.assertNotIn("Amend Mode", text)
        self.assertNotIn("INJECT-SECTION", text)

    def test_full_def_roles_inject_entire_definition(self):
        from darkfactory.pipeline.agents import (
            _agent_definition_for_injection, _read_agent_definition,
        )
        from darkfactory.models.agent import AgentRole
        # No markers in implementer.md → full def, byte-identical to a plain read.
        inj = _agent_definition_for_injection(AgentRole.IMPLEMENTER)
        full = _read_agent_definition(AgentRole.IMPLEMENTER)
        self.assertEqual(inj, full)
        self.assertNotIn("INJECT-SECTION", inj)

    def test_unterminated_marker_falls_back_to_full(self):
        from darkfactory.pipeline import agents
        from unittest.mock import patch
        broken = "intro\n<!-- INJECT-SECTION-START -->\nschema body, no end marker\n"
        with patch.object(agents, "_read_agent_definition", return_value=broken):
            out = agents._agent_definition_for_injection(
                __import__("darkfactory.models.agent", fromlist=["AgentRole"]).AgentRole.ARCHITECT
            )
        self.assertEqual(out, broken)  # fallback to full, not a truncated tail

    def test_multiple_sections_concatenated(self):
        from darkfactory.pipeline import agents
        from unittest.mock import patch
        from darkfactory.models.agent import AgentRole
        doc = ("a\n<!-- INJECT-SECTION-START -->\nFIRST\n<!-- INJECT-SECTION-END -->\n"
               "b\n<!-- INJECT-SECTION-START -->\nSECOND\n<!-- INJECT-SECTION-END -->\nc")
        with patch.object(agents, "_read_agent_definition", return_value=doc):
            out = agents._agent_definition_for_injection(AgentRole.ARCHITECT)
        self.assertEqual(out, "FIRST\n\nSECOND")

    def test_nested_or_misordered_markers_fall_back_to_full(self):
        # Codex nit: a second START before the first section's END = malformed;
        # degrade to the full def, never inject a section with untrusted bounds.
        from darkfactory.pipeline import agents
        from unittest.mock import patch
        from darkfactory.models.agent import AgentRole
        nested = ("<!-- INJECT-SECTION-START -->\nouter\n<!-- INJECT-SECTION-START -->\n"
                  "inner\n<!-- INJECT-SECTION-END -->\n")
        with patch.object(agents, "_read_agent_definition", return_value=nested):
            out = agents._agent_definition_for_injection(AgentRole.ARCHITECT)
        self.assertEqual(out, nested)  # full fallback, not a partial


if __name__ == "__main__":
    unittest.main()
