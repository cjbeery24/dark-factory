"""Tests for Phase 7 — deterministic preflight checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from darkfactory.pipeline.preflight import _extract_binary, run_preflight_check
from darkfactory.models.config import PipelineConfig, ServiceDef


class TestExtractBinary:
    def test_simple_command(self) -> None:
        assert _extract_binary("pytest") == "pytest"

    def test_command_with_args(self) -> None:
        assert _extract_binary("pnpm run build") == "pnpm"

    def test_cd_prefix(self) -> None:
        assert _extract_binary("cd src && pnpm run build") == "pnpm"

    def test_empty_string(self) -> None:
        assert _extract_binary("") is None

    def test_non_cd_compound(self) -> None:
        assert _extract_binary("export FOO=bar && pytest") == "export"


class TestPreflightPassesWhenValid:
    def test_passes_when_all_commands_valid(self, tmp_path) -> None:
        config = PipelineConfig(
            build_cmd="pip install -e .",
            test_cmd="pytest",
        )
        errors = run_preflight_check(config, tmp_path)
        assert errors == []

    def test_only_build_cmd_is_sufficient(self, tmp_path) -> None:
        config = PipelineConfig(build_cmd="go build ./...", language="go")
        errors = run_preflight_check(config, tmp_path)
        assert errors == []

    def test_only_test_cmd_is_sufficient(self, tmp_path) -> None:
        config = PipelineConfig(test_cmd="pytest")
        errors = run_preflight_check(config, tmp_path)
        assert errors == []


class TestPreflightFailures:
    def test_fails_when_no_gate_commands(self, tmp_path) -> None:
        config = PipelineConfig()
        errors = run_preflight_check(config, tmp_path)
        assert any("build_cmd" in e and "test_cmd" in e for e in errors)

    def test_empty_commands_are_not_checked(self, tmp_path) -> None:
        """Empty command fields should not trigger binary warnings."""
        config = PipelineConfig(test_cmd="pytest")
        with patch("darkfactory.pipeline.preflight.shutil.which", return_value=None):
            errors = run_preflight_check(config, tmp_path)
        # test_cmd set → no hard error. Binary check is a warning only.
        assert errors == []

    def test_docker_daemon_check(self, tmp_path) -> None:
        config = PipelineConfig(
            test_cmd="jest",
            services=[ServiceDef(name="postgres", image="postgres:16")],
        )
        with patch("darkfactory.pipeline.preflight.shutil.which") as mock_which:
            mock_which.side_effect = lambda x: None if x == "docker" else "/usr/bin/" + x
            errors = run_preflight_check(config, tmp_path)
        assert any("docker" in e.lower() for e in errors)

    def test_no_docker_error_without_services(self, tmp_path) -> None:
        config = PipelineConfig(test_cmd="pytest")
        with patch("darkfactory.pipeline.preflight.shutil.which", return_value=None):
            errors = run_preflight_check(config, tmp_path)
        assert errors == []
