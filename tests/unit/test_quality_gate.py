"""Tests for darkfactory/pipeline/quality_gate.py."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, call, patch

from darkfactory.errors import ConfigError
from darkfactory.models.config import PipelineConfig, ServiceDef
from darkfactory.pipeline.quality_gate import (
    GateResult,
    _FormatFlags,
    _accumulate_ab_scenario_counts,
    _accumulate_category_breakdowns,
    _append_gate_report_row,
    _apply_crash_adjustment,
    _browser_server_container_name,
    _collect_inject_env,
    _detect_formats,
    _launch_gate_container,
    _rewrite_base_url,
    _run_format_a_runner,
    _run_format_b_runner,
    _server_container_name,
    _start_server_container,
    _stop_server_container,
    _topo_sort_services,
    _wait_for_server,
)
from darkfactory.utils.subprocess_runner import RunResult


def _config(**kwargs) -> PipelineConfig:
    defaults = dict(language="python", gate_timeout=300)
    defaults.update(kwargs)
    return PipelineConfig(**defaults)


# ==============================================================================
# _rewrite_base_url
# ==============================================================================


class TestRewriteBaseUrl(unittest.TestCase):

    def test_localhost_replaced(self):
        result = _rewrite_base_url("http://localhost:8000", "df-abc-server")
        self.assertEqual(result, "http://df-abc-server:8000")

    def test_127_0_0_1_replaced(self):
        result = _rewrite_base_url("http://127.0.0.1:8000", "df-abc-server")
        self.assertEqual(result, "http://df-abc-server:8000")

    def test_custom_hostname_passes_through(self):
        result = _rewrite_base_url("http://myserver:8000", "df-abc-server")
        self.assertEqual(result, "http://myserver:8000")

    def test_scheme_preserved(self):
        result = _rewrite_base_url("https://localhost:443", "df-abc-server")
        self.assertEqual(result, "https://df-abc-server:443")

    def test_path_preserved(self):
        result = _rewrite_base_url("http://localhost:8000/api/v1", "df-abc-server")
        self.assertEqual(result, "http://df-abc-server:8000/api/v1")

    def test_no_port_localhost(self):
        result = _rewrite_base_url("http://localhost", "df-abc-server")
        self.assertEqual(result, "http://df-abc-server")

    def test_no_port_127(self):
        result = _rewrite_base_url("http://127.0.0.1", "df-abc-server")
        self.assertEqual(result, "http://df-abc-server")

    def test_query_and_fragment_preserved(self):
        result = _rewrite_base_url("http://localhost:8080/path?k=v#frag", "srv")
        self.assertEqual(result, "http://srv:8080/path?k=v#frag")

    def test_empty_string_passes_through(self):
        # urlparse("") has hostname=None → passes through
        result = _rewrite_base_url("", "df-abc-server")
        self.assertEqual(result, "")

    def test_server_name_with_dashes(self):
        result = _rewrite_base_url("http://localhost:3000", "df-run123-server")
        self.assertEqual(result, "http://df-run123-server:3000")

    def test_netloc_reconstructed_not_replaced(self):
        """Explicit netloc reconstruction prevents fragile str.replace behavior."""
        # Port must be preserved correctly via explicit f"{server_name}:{port}"
        result = _rewrite_base_url("http://localhost:8080", "my-server")
        self.assertEqual(result, "http://my-server:8080")


# ==============================================================================
# _detect_formats
# ==============================================================================


class TestDetectFormats(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name)
        self.spec = "my-spec"
        self.scenarios_dir = self.project / ".dark-factory" / "scenarios" / self.spec
        self.scenarios_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _write(self, name: str) -> None:
        (self.scenarios_dir / name).write_text("")

    def test_no_scenarios_dir_returns_empty(self):
        flags = _detect_formats(self.project, "nonexistent")
        self.assertFalse(flags.has_http)
        self.assertFalse(flags.has_journey)
        self.assertFalse(flags.has_executable)

    def test_scenarios_md_does_not_set_has_http(self):
        """Format A retired in Phase 4.6b.1a — *.scenarios.md never sets has_http."""
        self._write("api.scenarios.md")
        flags = _detect_formats(self.project, self.spec)
        self.assertFalse(flags.has_http)
        self.assertFalse(flags.has_journey)
        self.assertFalse(flags.has_executable)

    def test_detects_journey_format(self):
        self._write("login.journey.md")
        flags = _detect_formats(self.project, self.spec)
        self.assertFalse(flags.has_http)
        self.assertTrue(flags.has_journey)
        self.assertFalse(flags.has_executable)

    def test_detects_executable_test_py(self):
        self._write("test_contract.py")
        flags = _detect_formats(self.project, self.spec)
        self.assertFalse(flags.has_http)
        self.assertFalse(flags.has_journey)
        self.assertTrue(flags.has_executable)

    def test_detects_executable_test_dot_star(self):
        self._write("01-contract.test.js")
        flags = _detect_formats(self.project, self.spec)
        self.assertTrue(flags.has_executable)

    def test_detects_all_formats(self):
        self._write("api.scenarios.md")  # retired — has_http stays False
        self._write("login.journey.md")
        self._write("test_contract.py")
        flags = _detect_formats(self.project, self.spec)
        self.assertFalse(flags.has_http)
        self.assertTrue(flags.has_journey)
        self.assertTrue(flags.has_executable)

    def test_plain_md_does_not_trigger_http(self):
        self._write("README.md")
        flags = _detect_formats(self.project, self.spec)
        self.assertFalse(flags.has_http)
        self.assertFalse(flags.has_journey)

    def test_empty_dir_returns_empty(self):
        flags = _detect_formats(self.project, self.spec)
        self.assertFalse(flags.has_http)
        self.assertFalse(flags.has_journey)
        self.assertFalse(flags.has_executable)


# ==============================================================================
# _server_container_name / _browser_server_container_name
# ==============================================================================


class TestContainerNames(unittest.TestCase):

    def test_server_name(self):
        self.assertEqual(_server_container_name("abc123"), "df-abc123-server")

    def test_browser_server_name(self):
        self.assertEqual(_browser_server_container_name("abc123"), "df-abc123-browser-server")

    def test_server_name_stable(self):
        self.assertEqual(_server_container_name("run-id"), _server_container_name("run-id"))


# ==============================================================================
# _start_server_container
# ==============================================================================


class TestStartServerContainer(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call(self, *, server_cmd="uvicorn app:app", base_url="http://localhost:8000",
               run_id="run1", name=None, **config_kwargs):
        config = _config(**config_kwargs)
        with patch("darkfactory.pipeline.quality_gate.start_container") as mock_start:
            mock_start.return_value = "abc123"
            returned_name = _start_server_container(
                config, run_id, self.project, server_cmd,
                base_url=base_url, name=name,
            )
        return returned_name, mock_start

    def test_returns_container_name(self):
        name, _ = self._call()
        self.assertEqual(name, "df-run1-server")

    def test_custom_name_returned(self):
        name, _ = self._call(name="custom-server")
        self.assertEqual(name, "custom-server")

    def test_start_container_called_once(self):
        _, mock_start = self._call()
        mock_start.assert_called_once()

    def test_server_cmd_passed_as_bash_c(self):
        _, mock_start = self._call(server_cmd="gunicorn app:app -w 4")
        cmd_arg = mock_start.call_args[0][2]  # positional 'cmd'
        # Python language prepends a guarded pip install before the server cmd.
        self.assertEqual(cmd_arg, [
            "-c",
            "[ -f requirements.txt ] && pip install -r requirements.txt 2>&1; "
            "gunicorn app:app -w 4",
        ])

    def test_node_install_prefix(self):
        _, mock_start = self._call(server_cmd="node dist/index.js", language="node")
        cmd_arg = mock_start.call_args[0][2]
        self.assertEqual(cmd_arg, [
            "-c",
            "pnpm install --frozen-lockfile 2>&1 && node dist/index.js",
        ])

    def test_no_install_prefix_for_unknown_language(self):
        _, mock_start = self._call(server_cmd="./server", language="rust")
        cmd_arg = mock_start.call_args[0][2]
        self.assertEqual(cmd_arg, ["-c", "./server"])

    def test_image_uses_config_language(self):
        _, mock_start = self._call()
        image_arg = mock_start.call_args[0][1]  # positional 'image'
        self.assertEqual(image_arg, "dark-factory-python")

    def test_no_port_published_in_docker_args(self):
        """Port publishing removed — health checks use docker exec."""
        _, mock_start = self._call(base_url="http://localhost:8000")
        docker_args = mock_start.call_args[0][0]
        self.assertNotIn("-p", docker_args)

    def test_network_included_in_docker_args(self):
        _, mock_start = self._call(run_id="myrun")
        docker_args = mock_start.call_args[0][0]
        self.assertIn("--network", docker_args)

    def test_dry_run_passed_through(self):
        config = _config()
        with patch("darkfactory.pipeline.quality_gate.start_container") as mock_start:
            mock_start.return_value = ""
            _start_server_container(
                config, "run1", self.project, "cmd",
                base_url="http://localhost:8000", dry_run=True,
            )
        _, kw = mock_start.call_args
        self.assertTrue(kw.get("dry_run"))


# ==============================================================================
# _wait_for_server
# ==============================================================================


class TestWaitForServer(unittest.TestCase):

    def _mock_run(self, exit_code=0, stderr=""):
        """Return a mock for the subprocess runner."""
        from darkfactory.utils.subprocess_runner import RunResult
        return MagicMock(return_value=RunResult(exit_code=exit_code, stderr=stderr))

    def test_no_op_in_dry_run(self):
        _wait_for_server("container", 8000, "/health", 5, dry_run=True)

    def test_returns_immediately_on_healthy(self):
        with patch("darkfactory.pipeline.quality_gate._run", self._mock_run(exit_code=0)):
            _wait_for_server("container", 8000, "/health", 5)

    def test_raises_timeout_when_server_never_responds(self):
        with patch("darkfactory.pipeline.quality_gate._run", self._mock_run(exit_code=1, stderr="refused")):
            # monotonic: deadline, start, while-check, debug-log, while-check(>deadline), elapsed
            with patch("time.monotonic", side_effect=[0, 0, 0.1, 0.2, 99, 99]):
                with patch("time.sleep"):
                    with self.assertRaises(TimeoutError):
                        _wait_for_server("container", 8000, "/health", 1)

    def test_docker_exec_uses_correct_url(self):
        """Verify docker exec wget is called with the right URL."""
        mock = self._mock_run(exit_code=0)
        with patch("darkfactory.pipeline.quality_gate._run", mock):
            _wait_for_server("myserver", 9000, "/readyz", 5)
        cmd = mock.call_args[0][0]
        self.assertEqual(cmd[:3], ["docker", "exec", "myserver"])
        self.assertIn("http://127.0.0.1:9000/readyz", cmd)


# ==============================================================================
# _stop_server_container
# ==============================================================================


class TestStopServerContainer(unittest.TestCase):

    def test_stop_then_remove_called(self):
        with patch("darkfactory.pipeline.quality_gate.stop_container") as mock_stop, \
             patch("darkfactory.pipeline.quality_gate.remove_container") as mock_rm:
            _stop_server_container("mycontainer")

        mock_stop.assert_called_once_with("mycontainer", dry_run=False)
        mock_rm.assert_called_once_with("mycontainer", dry_run=False)

    def test_dry_run_forwarded(self):
        with patch("darkfactory.pipeline.quality_gate.stop_container") as mock_stop, \
             patch("darkfactory.pipeline.quality_gate.remove_container") as mock_rm:
            _stop_server_container("mycontainer", dry_run=True)

        mock_stop.assert_called_once_with("mycontainer", dry_run=True)
        mock_rm.assert_called_once_with("mycontainer", dry_run=True)


# ==============================================================================
# _run_format_a_runner
# ==============================================================================


class TestRunFormatARunner(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call(self, spec_name="my-spec", base_url="http://df-run1-server:8000",
               run_id="run1", **config_kwargs):
        config = _config(**config_kwargs)
        ok = RunResult(exit_code=0, stdout="", stderr="")
        with patch("darkfactory.pipeline.quality_gate.execute_container", return_value=ok) as m:
            result = _run_format_a_runner(
                spec_name, config, run_id, self.project, base_url,
            )
        return result, m

    def test_returns_run_result(self):
        result, _ = self._call()
        self.assertEqual(result.exit_code, 0)

    def test_uses_python_image(self):
        _, mock_exec = self._call()
        image_arg = mock_exec.call_args[0][1]
        self.assertEqual(image_arg, "dark-factory-python")

    def test_runner_script_in_cmd(self):
        _, mock_exec = self._call()
        cmd = mock_exec.call_args[0][2]
        self.assertTrue(any("http-scenario-runner.py" in a for a in cmd))

    def test_spec_name_in_scenarios_path(self):
        _, mock_exec = self._call(spec_name="my-spec")
        cmd = mock_exec.call_args[0][2]
        self.assertTrue(any("my-spec" in a for a in cmd))

    def test_base_url_forwarded(self):
        _, mock_exec = self._call(base_url="http://df-run1-server:8000")
        cmd = mock_exec.call_args[0][2]
        url_idx = cmd.index("--base-url")
        self.assertEqual(cmd[url_idx + 1], "http://df-run1-server:8000")

    def test_timeout_from_config(self):
        _, mock_exec = self._call(gate_timeout=600)
        kw = mock_exec.call_args[1]
        self.assertEqual(kw.get("timeout"), 600)


# ==============================================================================
# _run_format_b_runner
# ==============================================================================


class TestRunFormatBRunner(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _call(self, spec_name="my-spec", base_url="http://df-run1-server:3000",
               run_id="run1", **config_kwargs):
        config = _config(**config_kwargs)
        ok = RunResult(exit_code=0, stdout="", stderr="")
        with patch("darkfactory.pipeline.quality_gate.execute_container", return_value=ok) as m:
            result = _run_format_b_runner(
                spec_name, config, run_id, self.project, base_url,
            )
        return result, m

    def test_uses_playwright_image(self):
        _, mock_exec = self._call()
        image_arg = mock_exec.call_args[0][1]
        self.assertEqual(image_arg, "dark-factory-playwright")

    def test_journey_runner_script_in_cmd(self):
        _, mock_exec = self._call()
        cmd = mock_exec.call_args[0][2]
        self.assertTrue(any("journey-runner.py" in a for a in cmd))

    def test_base_url_forwarded(self):
        _, mock_exec = self._call(base_url="http://df-run1-browser-server:3000")
        cmd = mock_exec.call_args[0][2]
        url_idx = cmd.index("--base-url")
        self.assertEqual(cmd[url_idx + 1], "http://df-run1-browser-server:3000")

    def test_returns_failing_result(self):
        config = _config()
        fail = RunResult(exit_code=1, stdout="FAIL", stderr="")
        with patch("darkfactory.pipeline.quality_gate.execute_container", return_value=fail):
            result = _run_format_b_runner("spec", config, "run1", self.project, "http://x")
        self.assertEqual(result.exit_code, 1)


# ==============================================================================
# _append_gate_report_row
# ==============================================================================


class TestAppendGateReportRow(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name)
        self.spec = "my-spec"
        reports_dir = self.project / ".dark-factory" / "reports"
        reports_dir.mkdir(parents=True)
        self.report_path = reports_dir / f"{self.spec}-quality-gate.md"

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_pass_appended_when_exit_code_0(self):
        self.report_path.write_text("| Gate | Status |\n")
        _append_gate_report_row(self.project, self.spec, "Blind Scenarios (HTTP)", 0)
        content = self.report_path.read_text()
        self.assertIn("| Blind Scenarios (HTTP) | PASS |", content)

    def test_fail_appended_when_exit_code_nonzero(self):
        self.report_path.write_text("| Gate | Status |\n")
        _append_gate_report_row(self.project, self.spec, "Blind Scenarios (Journey)", 1)
        content = self.report_path.read_text()
        self.assertIn("| Blind Scenarios (Journey) | FAIL |", content)

    def test_no_op_when_report_does_not_exist(self):
        # Should not raise even if the report file doesn't exist
        _append_gate_report_row(self.project, self.spec, "HTTP", 0)

    def test_appends_not_overwrites(self):
        self.report_path.write_text("existing line\n")
        _append_gate_report_row(self.project, self.spec, "HTTP", 0)
        content = self.report_path.read_text()
        self.assertIn("existing line", content)
        self.assertIn("HTTP", content)

    def test_multiple_rows_appended(self):
        self.report_path.write_text("")
        _append_gate_report_row(self.project, self.spec, "HTTP", 0)
        _append_gate_report_row(self.project, self.spec, "Journey", 1)
        content = self.report_path.read_text()
        self.assertIn("PASS", content)
        self.assertIn("FAIL", content)


# ==============================================================================
# _accumulate_ab_scenario_counts
# ==============================================================================


class TestAccumulateAbScenarioCounts(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name)
        self.spec = "my-spec"
        self.reports_dir = self.project / ".dark-factory" / "reports"
        self.reports_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _canonical(self) -> Path:
        return self.reports_dir / f"{self.spec}-scenario-counts.env"

    def _fragment(self, suffix: str, passed: int, failed: int, total: int) -> None:
        path = self.reports_dir / f"{self.spec}-scenario-counts-{suffix}.env"
        path.write_text(
            f"SCENARIO_PASSED={passed}\n"
            f"SCENARIO_FAILED={failed}\n"
            f"SCENARIO_TOTAL={total}\n"
        )

    def test_no_canonical_no_fragments_writes_zeros(self):
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        content = self._canonical().read_text()
        self.assertIn("SCENARIO_PASSED=0", content)
        self.assertIn("SCENARIO_TOTAL=0", content)

    def test_merges_single_fragment(self):
        self._fragment("http", passed=3, failed=1, total=4)
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertEqual(gate_result.scenario_passed, 3)
        self.assertEqual(gate_result.scenario_failed, 1)
        self.assertEqual(gate_result.scenario_total, 4)

    def test_merges_multiple_fragments(self):
        self._fragment("http", passed=2, failed=0, total=2)
        self._fragment("journey", passed=1, failed=1, total=2)
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertEqual(gate_result.scenario_passed, 3)
        self.assertEqual(gate_result.scenario_failed, 1)
        self.assertEqual(gate_result.scenario_total, 4)

    def test_merges_executable_and_http_fragments(self):
        # Both executable and HTTP fragments present — both are summed.
        self._fragment("executable", passed=5, failed=0, total=5)
        self._fragment("http", passed=3, failed=1, total=4)
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertEqual(gate_result.scenario_passed, 8)
        self.assertEqual(gate_result.scenario_failed, 1)
        self.assertEqual(gate_result.scenario_total, 9)

    def test_pass_rate_computed(self):
        self._fragment("http", passed=3, failed=1, total=4)
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertEqual(gate_result.pass_rate, 75)

    def test_zero_total_gives_zero_rate(self):
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertEqual(gate_result.pass_rate, 0)

    def test_canonical_file_written(self):
        self._fragment("http", passed=2, failed=0, total=2)
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertTrue(self._canonical().exists())
        content = self._canonical().read_text()
        self.assertIn("SCENARIO_PASSED=2", content)

    def test_gate_result_updated(self):
        self._fragment("http", passed=4, failed=0, total=4)
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertEqual(gate_result.scenario_total, 4)

    def test_fragments_from_other_specs_not_included(self):
        """Fragments for a different spec must not be merged."""
        other_spec = "other-spec"
        other_frag = self.reports_dir / f"{other_spec}-scenario-counts-http.env"
        other_frag.write_text(
            "SCENARIO_PASSED=99\nSCENARIO_FAILED=0\nSCENARIO_TOTAL=99\n"
        )
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertEqual(gate_result.scenario_total, 0)

    def test_fragments_deleted_after_merge(self):
        """Fragment files must be deleted after merging to prevent double-counting."""
        self._fragment("http", passed=2, failed=0, total=2)
        frag_path = self.reports_dir / f"{self.spec}-scenario-counts-http.env"
        self.assertTrue(frag_path.exists())
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertFalse(frag_path.exists())

    def test_satisfaction_score_preserved_from_canonical(self):
        """SATISFACTION_SCORE from existing canonical file must not be dropped."""
        self._canonical().write_text(
            "SCENARIO_PASSED=5\nSCENARIO_FAILED=0\nSCENARIO_TOTAL=5\n"
            "SCENARIO_RATE=100\nSATISFACTION_SCORE=88\n"
        )
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertEqual(gate_result.satisfaction_score, 88)

    def test_satisfaction_score_direct_set_not_summed(self):
        """SATISFACTION_SCORE is last-writer-wins, not summed across fragments."""
        self._fragment("executable", passed=5, failed=0, total=5)
        sat_frag = self.reports_dir / f"{self.spec}-scenario-counts-satisfaction.env"
        sat_frag.write_text(
            "SCENARIO_PASSED=10\nSCENARIO_FAILED=2\nSCENARIO_TOTAL=12\n"
            "SATISFACTION_SCORE=82\nMULTI_RUN_N=3\nMULTI_RUN_K=2\n"
        )
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        # SATISFACTION_SCORE should be 82, not 82+0
        self.assertEqual(gate_result.satisfaction_score, 82)
        # Counts should be summed: 5+10=15 passed, 0+2=2 failed, 5+12=17 total
        self.assertEqual(gate_result.scenario_passed, 15)
        self.assertEqual(gate_result.scenario_failed, 2)
        self.assertEqual(gate_result.scenario_total, 17)

    def test_scenario_crashed_preserved(self):
        """SCENARIO_CRASHED and SCENARIO_ASSERT_FAILED in fragments appear in canonical."""
        frag = self.reports_dir / f"{self.spec}-scenario-counts-executable.env"
        frag.write_text(
            "SCENARIO_PASSED=8\nSCENARIO_FAILED=2\nSCENARIO_TOTAL=10\n"
            "SCENARIO_CRASHED=1\nSCENARIO_ASSERT_FAILED=1\n"
        )
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertEqual(gate_result.scenario_crashed, 1)
        self.assertEqual(gate_result.scenario_assert_failed, 1)

    def test_unknown_keys_dropped_with_warning(self):
        """Unknown keys in fragments are dropped (not written to canonical)."""
        frag = self.reports_dir / f"{self.spec}-scenario-counts-executable.env"
        frag.write_text(
            "SCENARIO_PASSED=5\nSCENARIO_FAILED=0\nSCENARIO_TOTAL=5\n"
            "BOGUS_KEY=42\n"
        )
        gate_result = GateResult(exit_code=0)
        with self.assertLogs("darkfactory.pipeline.quality_gate", level="WARNING") as cm:
            _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertTrue(any("BOGUS_KEY" in msg for msg in cm.output))
        content = self._canonical().read_text()
        self.assertNotIn("BOGUS_KEY", content)

    def test_spec_name_prefix_no_collision(self):
        """Spec 'auth' must not match fragments for spec 'auth-admin'."""
        # Create a fragment for a different spec that shares a prefix
        other_frag = self.reports_dir / "auth-admin-scenario-counts-http.env"
        other_frag.write_text(
            "SCENARIO_PASSED=99\nSCENARIO_FAILED=0\nSCENARIO_TOTAL=99\n"
        )
        # Run accumulation for spec "auth"
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, "auth", gate_result)
        # Should NOT have picked up auth-admin's counts
        self.assertEqual(gate_result.scenario_total, 0)

    def test_backwards_compat_no_fragments_canonical_exists(self):
        """When no fragments exist but canonical does, canonical is left untouched."""
        self._canonical().write_text(
            "SCENARIO_PASSED=7\nSCENARIO_FAILED=3\nSCENARIO_TOTAL=10\n"
            "SCENARIO_RATE=70\nSATISFACTION_SCORE=65\n"
        )
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        self.assertEqual(gate_result.scenario_passed, 7)
        self.assertEqual(gate_result.satisfaction_score, 65)
        # Verify canonical was NOT overwritten
        content = self._canonical().read_text()
        self.assertIn("SATISFACTION_SCORE=65", content)

    def test_empty_fragment_file(self):
        """A 0-byte fragment is handled gracefully (no crash, counts stay zero)."""
        frag = self.reports_dir / f"{self.spec}-scenario-counts-executable.env"
        frag.write_text("")
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        # Empty fragment → zero counts
        self.assertEqual(gate_result.scenario_total, 0)

    def test_satisfaction_fragment_all_keys(self):
        """Satisfaction fragment with full key set: counts summed, metadata direct-set."""
        self._fragment("executable", passed=5, failed=1, total=6)
        sat_frag = self.reports_dir / f"{self.spec}-scenario-counts-satisfaction.env"
        sat_frag.write_text(
            "SCENARIO_PASSED=20\nSCENARIO_FAILED=4\nSCENARIO_TOTAL=24\n"
            "SCENARIO_RATE=83\nSATISFACTION_SCORE=78\n"
            "MULTI_RUN_N=5\nMULTI_RUN_K=3\n"
        )
        gate_result = GateResult(exit_code=0)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        # Counts summed: 5+20=25, 1+4=5, 6+24=30
        self.assertEqual(gate_result.scenario_passed, 25)
        self.assertEqual(gate_result.scenario_failed, 5)
        self.assertEqual(gate_result.scenario_total, 30)
        # Metadata: direct-set from satisfaction fragment
        self.assertEqual(gate_result.satisfaction_score, 78)
        # Rate recomputed: 25/30 = 83%
        self.assertEqual(gate_result.pass_rate, 83)

    def test_satisfaction_defaults_to_pass_rate_when_no_fragment(self):
        """HTTP-only specs (no satisfaction fragment) should get pass_rate as satisfaction."""
        http_frag = self.reports_dir / f"{self.spec}-scenario-counts-http.env"
        http_frag.write_text(
            "SCENARIO_PASSED=18\nSCENARIO_FAILED=5\nSCENARIO_TOTAL=23\n"
        )
        gate_result = GateResult(exit_code=1)
        _accumulate_ab_scenario_counts(self.project, self.spec, gate_result)
        # No satisfaction fragment → satisfaction defaults to pass rate (18/23 = 78%)
        self.assertEqual(gate_result.pass_rate, 78)
        self.assertEqual(gate_result.satisfaction_score, 78)


# ==============================================================================
# _accumulate_category_breakdowns
# ==============================================================================


class TestAccumulateCategoryBreakdowns(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name)
        self.spec = "my-spec"
        self.reports_dir = self.project / ".dark-factory" / "reports"
        self.reports_dir.mkdir(parents=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def _canonical(self) -> Path:
        return self.reports_dir / f"{self.spec}-category-breakdown.env"

    def test_no_fragments_noop(self):
        """No breakdown fragments → no canonical written."""
        _accumulate_category_breakdowns(self.project, self.spec)
        self.assertFalse(self._canonical().exists())

    def test_passed_total_summed(self):
        """CAT_01_PASSED and CAT_01_TOTAL are summed across fragments."""
        frag1 = self.reports_dir / f"{self.spec}-category-breakdown-executable.env"
        frag1.write_text("CAT_01_PASSED=3\nCAT_01_TOTAL=5\nCAT_01_WEIGHT=100\n")
        frag2 = self.reports_dir / f"{self.spec}-category-breakdown-http.env"
        frag2.write_text("CAT_01_PASSED=2\nCAT_01_TOTAL=4\nCAT_01_WEIGHT=100\n")
        _accumulate_category_breakdowns(self.project, self.spec)
        content = self._canonical().read_text()
        self.assertIn("CAT_01_PASSED=5", content)
        self.assertIn("CAT_01_TOTAL=9", content)

    def test_weight_not_summed(self):
        """CAT_01_WEIGHT is last-writer-wins, NOT summed."""
        frag1 = self.reports_dir / f"{self.spec}-category-breakdown-executable.env"
        frag1.write_text("CAT_01_WEIGHT=100\n")
        frag2 = self.reports_dir / f"{self.spec}-category-breakdown-satisfaction.env"
        frag2.write_text("CAT_01_WEIGHT=100\n")
        _accumulate_category_breakdowns(self.project, self.spec)
        content = self._canonical().read_text()
        # Weight should be 100, not 200
        self.assertIn("CAT_01_WEIGHT=100", content)
        self.assertNotIn("CAT_01_WEIGHT=200", content)

    def test_fragments_deleted_after_merge(self):
        """Fragment files are deleted after merging."""
        frag = self.reports_dir / f"{self.spec}-category-breakdown-executable.env"
        frag.write_text("CAT_01_PASSED=3\nCAT_01_TOTAL=5\n")
        _accumulate_category_breakdowns(self.project, self.spec)
        self.assertFalse(frag.exists())
        self.assertTrue(self._canonical().exists())


# ==============================================================================
# Server deduplication in run_quality_gate orchestration
# ==============================================================================


class TestServerDeduplication(unittest.TestCase):
    """Verify server startup logic when browser and HTTP share the same server command."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name)
        self.spec = "my-spec"
        scenarios_dir = self.project / ".dark-factory" / "scenarios" / self.spec
        scenarios_dir.mkdir(parents=True)
        # Only journey scenarios — no HTTP scenarios
        (scenarios_dir / "login.journey.md").write_text("")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_shared_server_started_when_only_journey_exists(self):
        """When has_http=False, has_journey=True, cmds_same: server must still be started."""
        from darkfactory.pipeline.quality_gate import run_quality_gate
        config = _config(
            test_server_cmd="uvicorn app:app",
            browser_server_cmd="uvicorn app:app",  # same — shared server
            browser_base_url="http://localhost:3000",
        )
        ok = RunResult(exit_code=0, stdout="", stderr="")

        with patch("darkfactory.pipeline.quality_gate.execute_container", return_value=ok), \
             patch("darkfactory.pipeline.quality_gate.start_container", return_value="cid") as mock_start, \
             patch("darkfactory.pipeline.quality_gate._wait_for_server"), \
             patch("darkfactory.pipeline.quality_gate._check_playwright_image"), \
             patch("darkfactory.pipeline.quality_gate.check_language_image"), \
             patch("darkfactory.pipeline.quality_gate.stop_container"), \
             patch("darkfactory.pipeline.quality_gate.remove_container"):
            run_quality_gate(self.spec, config, "run1", self.project)

        # Server must have been started despite no HTTP scenarios
        mock_start.assert_called_once()

    def test_journey_runner_gets_container_url_not_localhost(self):
        """Runner container must receive rewritten URL (container name), not localhost."""
        from darkfactory.pipeline.quality_gate import run_quality_gate
        config = _config(
            test_server_cmd="uvicorn app:app",
            browser_server_cmd="uvicorn app:app",
            test_base_url="http://localhost:8000",
            browser_base_url="http://localhost:3000",
        )
        ok = RunResult(exit_code=0, stdout="", stderr="")
        captured_cmds: list[list[str]] = []

        def capture_execute(docker_args, image, cmd, **kw):
            captured_cmds.append((image, list(cmd)))
            return ok

        with patch("darkfactory.pipeline.quality_gate.execute_container", side_effect=capture_execute), \
             patch("darkfactory.pipeline.quality_gate.start_container", return_value="cid") as mock_start, \
             patch("darkfactory.pipeline.quality_gate._wait_for_server"), \
             patch("darkfactory.pipeline.quality_gate._check_playwright_image"), \
             patch("darkfactory.pipeline.quality_gate.check_language_image"), \
             patch("darkfactory.pipeline.quality_gate.stop_container"), \
             patch("darkfactory.pipeline.quality_gate.remove_container"):
            run_quality_gate(self.spec, config, "run1", self.project)

        # Find the journey runner call
        journey_calls = [(img, cmd) for img, cmd in captured_cmds
                         if img == "dark-factory-playwright"]
        self.assertEqual(len(journey_calls), 1, "Expected exactly one journey runner call")
        _, journey_cmd = journey_calls[0]
        url_idx = journey_cmd.index("--base-url")
        url = journey_cmd[url_idx + 1]
        self.assertNotIn("localhost", url, "Runner should not receive localhost URL")


class TestMultiRunEnvForwarding(unittest.TestCase):
    """Verify multi-run env vars are forwarded to the gate container."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.project = Path(self.tmpdir.name)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_multi_run_env_passed_to_container(self):
        """extra_env dict becomes -e flags in docker_args."""
        ok = RunResult(exit_code=0, stdout="", stderr="")
        config = _config()

        with patch("darkfactory.pipeline.quality_gate.execute_container", return_value=ok) as mock_exec:
            _launch_gate_container(
                "spec", config, "run1", self.project, ["bash"],
                timeout=300,
                extra_env={"SCENARIO_MULTI_RUN_N": "3", "SCENARIO_MULTI_RUN_K": "2"},
            )

        docker_args = mock_exec.call_args[0][0]
        # Find all -e flags
        env_pairs = []
        for i, arg in enumerate(docker_args):
            if arg == "-e" and i + 1 < len(docker_args):
                env_pairs.append(docker_args[i + 1])
        self.assertIn("SCENARIO_MULTI_RUN_N=3", env_pairs)
        self.assertIn("SCENARIO_MULTI_RUN_K=2", env_pairs)

    def test_no_extra_env_when_default(self):
        """No multi-run env vars when extra_env is empty."""
        ok = RunResult(exit_code=0, stdout="", stderr="")
        config = _config()

        with patch("darkfactory.pipeline.quality_gate.execute_container", return_value=ok) as mock_exec:
            _launch_gate_container(
                "spec", config, "run1", self.project, ["bash"],
                timeout=300,
            )

        docker_args = mock_exec.call_args[0][0]
        env_pairs = []
        for i, arg in enumerate(docker_args):
            if arg == "-e" and i + 1 < len(docker_args):
                env_pairs.append(docker_args[i + 1])
        multi_run_envs = [e for e in env_pairs if "MULTI_RUN" in e]
        self.assertEqual(multi_run_envs, [])

    def test_run_quality_gate_forwards_multi_run(self):
        """run_quality_gate passes multi-run config to container via extra_env."""
        from darkfactory.pipeline.quality_gate import run_quality_gate
        config = _config(multi_run_n=3, multi_run_k=2)
        ok = RunResult(exit_code=0, stdout="", stderr="")

        with patch("darkfactory.pipeline.quality_gate.execute_container", return_value=ok) as mock_exec, \
             patch("darkfactory.pipeline.quality_gate.check_language_image"):
            run_quality_gate("spec", config, "run1", self.project)

        docker_args = mock_exec.call_args[0][0]
        env_pairs = []
        for i, arg in enumerate(docker_args):
            if arg == "-e" and i + 1 < len(docker_args):
                env_pairs.append(docker_args[i + 1])
        self.assertIn("SCENARIO_MULTI_RUN_N=3", env_pairs)
        self.assertIn("SCENARIO_MULTI_RUN_K=2", env_pairs)

    def test_run_quality_gate_no_multi_run_at_default(self):
        """run_quality_gate does not forward multi-run when N=1 (default)."""
        from darkfactory.pipeline.quality_gate import run_quality_gate
        config = _config()  # multi_run_n=1 by default
        ok = RunResult(exit_code=0, stdout="", stderr="")

        with patch("darkfactory.pipeline.quality_gate.execute_container", return_value=ok) as mock_exec, \
             patch("darkfactory.pipeline.quality_gate.check_language_image"):
            run_quality_gate("spec", config, "run1", self.project)

        docker_args = mock_exec.call_args[0][0]
        env_pairs = []
        for i, arg in enumerate(docker_args):
            if arg == "-e" and i + 1 < len(docker_args):
                env_pairs.append(docker_args[i + 1])
        multi_run_envs = [e for e in env_pairs if "MULTI_RUN" in e]
        self.assertEqual(multi_run_envs, [])


class TestCrashAdjustment(unittest.TestCase):
    """Tests for _apply_crash_adjustment."""

    def test_no_crashes_unchanged(self):
        """Pass rate and effective counts unchanged when no crashes."""
        gr = GateResult(exit_code=0, scenario_passed=8, scenario_failed=2,
                        scenario_total=10, pass_rate=80)
        _apply_crash_adjustment(gr)
        self.assertEqual(gr.pass_rate, 80)
        self.assertFalse(gr.scenario_crash_adjusted)
        # Effective fields stay at default (0) — no adjustment performed.
        self.assertEqual(gr.scenario_effective_total, 0)
        self.assertEqual(gr.scenario_effective_failed, 0)

    def test_crashes_excluded_from_denominator(self):
        """Crashes reduce the denominator: 5 passed / 7 effective = 71%."""
        gr = GateResult(exit_code=1, scenario_passed=5, scenario_failed=5,
                        scenario_total=10, pass_rate=50,
                        scenario_crashed=3, scenario_assert_failed=2)
        _apply_crash_adjustment(gr)
        self.assertEqual(gr.pass_rate, 71)  # 5*100//7 = 71
        self.assertTrue(gr.scenario_crash_adjusted)
        # Raw counts unchanged.
        self.assertEqual(gr.scenario_total, 10)
        self.assertEqual(gr.scenario_failed, 5)
        # Effective counts reflect crash exclusion.
        self.assertEqual(gr.scenario_effective_total, 7)
        self.assertEqual(gr.scenario_effective_failed, 2)

    def test_all_crashed_returns_100(self):
        """When all scenarios crashed, no evidence of impl failure -> 100%."""
        gr = GateResult(exit_code=1, scenario_passed=0, scenario_failed=5,
                        scenario_total=5, pass_rate=0,
                        scenario_crashed=5, scenario_assert_failed=0)
        _apply_crash_adjustment(gr)
        self.assertEqual(gr.pass_rate, 100)
        self.assertTrue(gr.scenario_crash_adjusted)
        self.assertEqual(gr.scenario_effective_total, 0)
        self.assertEqual(gr.scenario_effective_failed, 0)

    def test_no_crashes_no_adjustment(self):
        """Zero crashed with zero total — early return, no adjustment."""
        gr = GateResult(exit_code=0, scenario_passed=0, scenario_failed=0,
                        scenario_total=0, pass_rate=0)
        _apply_crash_adjustment(gr)
        self.assertEqual(gr.pass_rate, 0)
        self.assertFalse(gr.scenario_crash_adjusted)

    def test_pass_rate_capped_at_100(self):
        """Ensure pass_rate never exceeds 100 even with count inconsistencies."""
        # Artificially set passed > effective_total to test the clamp.
        gr = GateResult(exit_code=1, scenario_passed=8, scenario_failed=2,
                        scenario_total=10, pass_rate=80,
                        scenario_crashed=5, scenario_assert_failed=0)
        _apply_crash_adjustment(gr)
        # effective_total=5, 8*100//5=160 -> clamped to 100
        self.assertEqual(gr.pass_rate, 100)
        self.assertEqual(gr.scenario_effective_total, 5)

    def test_log_emitted(self):
        """Crash adjustment logs the rate change."""
        gr = GateResult(exit_code=1, scenario_passed=7, scenario_failed=3,
                        scenario_total=10, pass_rate=70,
                        scenario_crashed=2, scenario_assert_failed=1)
        with self.assertLogs("darkfactory.pipeline.quality_gate", level="INFO") as cm:
            _apply_crash_adjustment(gr)
        self.assertTrue(any("crash-adjusted" in m for m in cm.output))


class TestCollectInjectEnv(unittest.TestCase):
    def test_merges_from_multiple_services(self) -> None:
        cfg = _config(services=[
            ServiceDef(name="postgres", image="postgres:16", inject_env={"DATABASE_URL": "pg://x"}),
            ServiceDef(name="redis", image="redis:7", inject_env={"REDIS_URL": "redis://y"}),
        ])
        merged = _collect_inject_env(cfg)
        self.assertEqual(merged, {"DATABASE_URL": "pg://x", "REDIS_URL": "redis://y"})

    def test_empty_services(self) -> None:
        cfg = _config(services=[])
        self.assertEqual(_collect_inject_env(cfg), {})

    def test_collision_raises(self) -> None:
        cfg = _config(services=[
            ServiceDef(name="postgres", image="postgres:16", inject_env={"DATABASE_URL": "pg://x"}),
            ServiceDef(name="postgres2", image="postgres:16", inject_env={"DATABASE_URL": "pg://y"}),
        ])
        with self.assertRaises(ConfigError):
            _collect_inject_env(cfg)


# ==============================================================================
# Phase 4.3: _topo_sort_services
# ==============================================================================


class TestTopoSortServices(unittest.TestCase):
    def test_no_deps_preserves_order(self) -> None:
        a = ServiceDef(name="a", image="a:1")
        b = ServiceDef(name="b", image="b:1")
        assert _topo_sort_services([a, b]) == [a, b]

    def test_single_dependency(self) -> None:
        pg = ServiceDef(name="postgres", image="pg:16")
        app = ServiceDef(name="app", image="app:1", depends_on=["postgres"])
        # app depends on postgres — postgres must come first regardless of input order.
        result = _topo_sort_services([app, pg])
        names = [s.name for s in result]
        assert names.index("postgres") < names.index("app")

    def test_diamond_dependency(self) -> None:
        a = ServiceDef(name="a", image="a:1")
        b = ServiceDef(name="b", image="b:1", depends_on=["a"])
        c = ServiceDef(name="c", image="c:1", depends_on=["a"])
        d = ServiceDef(name="d", image="d:1", depends_on=["b", "c"])
        result = _topo_sort_services([d, c, b, a])
        names = [s.name for s in result]
        assert names.index("a") < names.index("b")
        assert names.index("a") < names.index("c")
        assert names.index("b") < names.index("d")
        assert names.index("c") < names.index("d")

    def test_cycle_raises(self) -> None:
        a = ServiceDef(name="a", image="a:1", depends_on=["b"])
        b = ServiceDef(name="b", image="b:1", depends_on=["a"])
        with self.assertRaisesRegex(ConfigError, "circular"):
            _topo_sort_services([a, b])

    def test_missing_dep_raises(self) -> None:
        a = ServiceDef(name="a", image="a:1", depends_on=["missing"])
        with self.assertRaisesRegex(ConfigError, "not defined"):
            _topo_sort_services([a])

    def test_self_dependency_raises(self) -> None:
        a = ServiceDef(name="a", image="a:1", depends_on=["a"])
        with self.assertRaisesRegex(ConfigError, "depends on itself"):
            _topo_sort_services([a])

    def test_duplicate_service_name_raises(self) -> None:
        a1 = ServiceDef(name="a", image="a:1")
        a2 = ServiceDef(name="a", image="a:2")
        with self.assertRaisesRegex(ConfigError, "duplicate"):
            _topo_sort_services([a1, a2])

    def test_empty_list(self) -> None:
        assert _topo_sort_services([]) == []

    def test_chain_of_three(self) -> None:
        c = ServiceDef(name="c", image="c:1", depends_on=["b"])
        b = ServiceDef(name="b", image="b:1", depends_on=["a"])
        a = ServiceDef(name="a", image="a:1")
        result = _topo_sort_services([c, b, a])
        names = [s.name for s in result]
        assert names == ["a", "b", "c"]


# ==============================================================================
# Phase 4.3: ServiceDef.depends_on
# ==============================================================================


class TestServiceDefDependsOn(unittest.TestCase):
    def test_depends_on_default_empty(self) -> None:
        svc = ServiceDef(name="pg", image="pg:16")
        assert svc.depends_on == []

    def test_depends_on_set(self) -> None:
        svc = ServiceDef(name="app", image="app:1", depends_on=["pg", "redis"])
        assert svc.depends_on == ["pg", "redis"]


if __name__ == "__main__":
    unittest.main()
