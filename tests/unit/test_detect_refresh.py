"""Tests for refresh_gate_commands and _patch_config_toml."""

from __future__ import annotations

import os
import stat
import tomllib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from darkfactory.config.loader import load_config
from darkfactory.setup.detect import DetectionResult, _patch_config_toml, refresh_gate_commands


def _write_config(tmp_path: Path, content: str) -> Path:
    config_path = tmp_path / ".dark-factory" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(content)
    return config_path


def _make_config(**kwargs: str) -> object:
    cfg = MagicMock()
    cfg.build_cmd = kwargs.get("build_cmd", "")
    cfg.lint_cmd = kwargs.get("lint_cmd", "")
    cfg.typecheck_cmd = kwargs.get("typecheck_cmd", "")
    cfg.test_cmd = kwargs.get("test_cmd", "")
    return cfg


class TestNoOpWhenAllCommandsSet:
    def test_no_file_write_and_empty_list(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, "[core]\nlanguage = \"node\"\n")
        cfg = _make_config(
            build_cmd="npm run build",
            lint_cmd="npm run lint",
            typecheck_cmd="tsc --noEmit",
            test_cmd="npm test",
        )
        detected = DetectionResult(
            build_cmd="pnpm run build",
            lint_cmd="pnpm run lint",
            typecheck_cmd="pnpm run typecheck",
            test_cmd="pnpm test",
        )
        mtime_before = config_path.stat().st_mtime
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            result = refresh_gate_commands(tmp_path, cfg, config_path)
        assert result == []
        assert config_path.stat().st_mtime == mtime_before
        assert cfg.build_cmd == "npm run build"
        assert cfg.lint_cmd == "npm run lint"
        assert cfg.typecheck_cmd == "tsc --noEmit"
        assert cfg.test_cmd == "npm test"


class TestFillsAbsentCommands:
    def test_all_four_filled(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, "[core]\nlanguage = \"node\"\n")
        cfg = _make_config()
        detected = DetectionResult(
            build_cmd="pnpm run build",
            lint_cmd="pnpm run lint",
            typecheck_cmd="pnpm run typecheck",
            test_cmd="pnpm test",
        )
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            result = refresh_gate_commands(tmp_path, cfg, config_path)
        assert len(result) == 4
        fields = dict(result)
        assert fields["build_cmd"] == "pnpm run build"
        assert fields["lint_cmd"] == "pnpm run lint"
        assert fields["typecheck_cmd"] == "pnpm run typecheck"
        assert fields["test_cmd"] == "pnpm test"
        assert cfg.build_cmd == "pnpm run build"
        assert cfg.lint_cmd == "pnpm run lint"
        assert cfg.typecheck_cmd == "pnpm run typecheck"
        assert cfg.test_cmd == "pnpm test"
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
        assert data["gates"]["build_cmd"] == "pnpm run build"
        assert data["gates"]["lint_cmd"] == "pnpm run lint"
        assert data["gates"]["typecheck_cmd"] == "pnpm run typecheck"
        assert data["gates"]["test_cmd"] == "pnpm test"


class TestPartialFill:
    def test_only_absent_fields_updated(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, "[core]\nlanguage = \"node\"\n")
        cfg = _make_config(build_cmd="npm run build", lint_cmd="npm run lint")
        detected = DetectionResult(
            build_cmd="pnpm run build",
            lint_cmd="pnpm run lint",
            typecheck_cmd="pnpm run typecheck",
            test_cmd="pnpm test",
        )
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            result = refresh_gate_commands(tmp_path, cfg, config_path)
        fields = dict(result)
        assert set(fields.keys()) == {"typecheck_cmd", "test_cmd"}
        assert cfg.build_cmd == "npm run build"
        assert cfg.lint_cmd == "npm run lint"
        assert cfg.typecheck_cmd == "pnpm run typecheck"
        assert cfg.test_cmd == "pnpm test"
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
        assert "build_cmd" not in data.get("gates", {})
        assert "lint_cmd" not in data.get("gates", {})
        assert data["gates"]["typecheck_cmd"] == "pnpm run typecheck"
        assert data["gates"]["test_cmd"] == "pnpm test"


class TestDetectionException:
    def test_config_unchanged_no_crash_warning_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        config_path = _write_config(tmp_path, "[core]\nlanguage = \"node\"\n")
        cfg = _make_config()
        mtime_before = config_path.stat().st_mtime
        with patch("darkfactory.setup.detect.detect_project", side_effect=RuntimeError("boom")):
            with caplog.at_level(logging.WARNING, logger="darkfactory.setup.detect"):
                result = refresh_gate_commands(tmp_path, cfg, config_path)
        assert result == []
        assert config_path.stat().st_mtime == mtime_before
        assert cfg.build_cmd == ""
        assert any("detection failed" in r.message for r in caplog.records)


class TestMkstempOSError:
    """OSError from mkstemp (e.g. no space in parent dir) is caught inside
    _patch_config_toml; in-memory config is still updated."""
    def test_warning_logged_in_memory_updated(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        import logging
        config_path = _write_config(tmp_path, "[core]\nlanguage = \"node\"\n")
        cfg = _make_config()
        detected = DetectionResult(build_cmd="npm run build", test_cmd="npm test")
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            with patch("darkfactory.setup.detect.tempfile.mkstemp", side_effect=OSError("no space left")):
                with caplog.at_level(logging.WARNING, logger="darkfactory.setup.detect"):
                    result = refresh_gate_commands(tmp_path, cfg, config_path)
        assert cfg.build_cmd == "npm run build"
        assert cfg.test_cmd == "npm test"
        assert any("could not write config.toml" in r.message for r in caplog.records)
        assert len(result) == 2


class TestRoundTripToml:
    def test_patched_values_loadable_and_comment_notice_logged(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        import logging
        content = (
            "[core]\n"
            "language = \"node\"\n\n"
            "[gates]\n"
            "# this is a hand-authored comment\n"
            "timeout = 300\n"
        )
        config_path = _write_config(tmp_path, content)
        cfg = _make_config()
        detected = DetectionResult(build_cmd="npm run build", test_cmd="npm test")
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            with caplog.at_level(logging.DEBUG, logger="darkfactory.setup.detect"):
                refresh_gate_commands(tmp_path, cfg, config_path)
        loaded = load_config(config_path)
        assert loaded.build_cmd == "npm run build"
        assert loaded.test_cmd == "npm test"
        assert loaded.gate_timeout == 300
        assert any("hand-authored comments" in r.message for r in caplog.records)


class TestAbsentGatesSection:
    def test_gates_section_added_with_new_commands(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, "[core]\nlanguage = \"python\"\n")
        cfg = _make_config()
        detected = DetectionResult(build_cmd="pip install -e . -q", test_cmd="pytest")
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            refresh_gate_commands(tmp_path, cfg, config_path)
        loaded = load_config(config_path)
        assert loaded.build_cmd == "pip install -e . -q"
        assert loaded.test_cmd == "pytest"


class TestAtomicWriteCrashSafety:
    def test_original_unchanged_on_replace_failure_in_memory_still_updated(self, tmp_path: Path) -> None:
        original_content = "[core]\nlanguage = \"node\"\n"
        config_path = _write_config(tmp_path, original_content)
        cfg = _make_config()
        detected = DetectionResult(build_cmd="npm run build", test_cmd="npm test")
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            with patch("darkfactory.setup.detect.os.replace", side_effect=OSError("disk full")):
                result = refresh_gate_commands(tmp_path, cfg, config_path)
        assert config_path.read_text() == original_content
        assert cfg.build_cmd == "npm run build"
        assert cfg.test_cmd == "npm test"
        assert len(result) == 2

    def test_in_memory_not_updated_when_patch_raises_non_oserror(self, tmp_path: Path) -> None:
        # Verifies write-first ordering: if _patch_config_toml propagates a non-OSError
        # exception (e.g. KeyboardInterrupt escapes the inner BaseException handler and
        # is not caught by the outer OSError handler), the in-memory update is skipped.
        original_content = "[core]\nlanguage = \"node\"\n"
        config_path = _write_config(tmp_path, original_content)
        cfg = _make_config()
        detected = DetectionResult(build_cmd="npm run build", test_cmd="npm test")
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            with patch("darkfactory.setup.detect.os.replace", side_effect=KeyboardInterrupt):
                with pytest.raises(KeyboardInterrupt):
                    refresh_gate_commands(tmp_path, cfg, config_path)
        assert cfg.build_cmd == ""
        assert cfg.test_cmd == ""


class TestTestCmdDualSectionRoundTrip:
    def test_core_test_cmd_prevents_refresh(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, "[core]\ntest_cmd = \"pytest\"\n")
        cfg = load_config(config_path)
        assert cfg.test_cmd == "pytest"
        detected = DetectionResult(
            build_cmd="pip install -e . -q",
            lint_cmd="ruff check .",
            typecheck_cmd="mypy .",
            test_cmd="python -m pytest",
        )
        mtime_before = config_path.stat().st_mtime
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            result = refresh_gate_commands(tmp_path, cfg, config_path)
        fields = dict(result)
        assert "test_cmd" not in fields
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
        assert "test_cmd" not in data.get("gates", {})


class TestIdempotency:
    def test_second_refresh_is_noop(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, "[core]\nlanguage = \"node\"\n")
        detected = DetectionResult(build_cmd="npm run build", test_cmd="npm test")
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            cfg = load_config(config_path)
            first = refresh_gate_commands(tmp_path, cfg, config_path)
        assert len(first) == 2
        mtime_after_first = config_path.stat().st_mtime
        cfg2 = load_config(config_path)
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            second = refresh_gate_commands(tmp_path, cfg2, config_path)
        assert second == []
        assert config_path.stat().st_mtime == mtime_after_first


class TestHookCallSitePlacement:
    """The autodetect delta hook fires once per full iteration, not per pre-gate
    retry. This is enforced by code structure: _autodetect_delta_hook is called at
    a single fixed point in run_iteration_loop, outside the pre-gate retry loop.
    This test verifies that structural invariant by inspecting the source."""

    def test_hook_called_once_outside_pregate_loop(self) -> None:
        import inspect
        from darkfactory.pipeline import iteration
        source = inspect.getsource(iteration.run_iteration_loop)
        # Exactly one call site.
        assert source.count("_autodetect_delta_hook") == 1, (
            "_autodetect_delta_hook should appear exactly once in run_iteration_loop"
        )
        assert "for pre_gate_attempt in range" in source, (
            "pre-gate retry loop not found in run_iteration_loop — update this test if the loop was refactored"
        )
        # The hook call appears before the pre-gate block, not inside it.
        hook_pos = source.index("_autodetect_delta_hook")
        pregate_loop_pos = source.index("for pre_gate_attempt in range")
        assert hook_pos < pregate_loop_pos, (
            "_autodetect_delta_hook must appear before the pre-gate retry loop, not inside it"
        )


class TestTestCmdWritePath:
    def test_test_cmd_written_under_gates_not_core(self, tmp_path: Path) -> None:
        config_path = _write_config(tmp_path, "[core]\nlanguage = \"node\"\n")
        cfg = _make_config()
        detected = DetectionResult(test_cmd="pnpm -r run test")
        with patch("darkfactory.setup.detect.detect_project", return_value=detected):
            result = refresh_gate_commands(tmp_path, cfg, config_path)
        fields = dict(result)
        assert fields["test_cmd"] == "pnpm -r run test"
        with config_path.open("rb") as fh:
            data = tomllib.load(fh)
        assert data["gates"]["test_cmd"] == "pnpm -r run test"
        assert "test_cmd" not in data.get("core", {})
        loaded = load_config(config_path)
        assert loaded.test_cmd == "pnpm -r run test"
