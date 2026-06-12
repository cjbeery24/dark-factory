"""Tests for autodetect_delta — Phase 2 of config unification.

Validates that auto-detection writes gaps through the delta log instead
of patching config.toml directly.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

import pytest

from darkfactory.architecture.deltas import (
    fold_deltas,
    hash_view,
    list_deltas,
    materialize,
)
from darkfactory.architecture.environment import EnvironmentContract, canonical_dump
from darkfactory.architecture.runtime import _reset_cache
from darkfactory.models.config import PipelineConfig
from darkfactory.setup.autodetect_delta import write_autodetect_delta
from darkfactory.setup.detect import DetectionResult


@pytest.fixture(autouse=True)
def _clean_cache():
    _reset_cache()
    yield
    _reset_cache()


def _setup_project(tmp_path: Path, *, with_delta: bool = False) -> Path:
    """Create minimal project structure with environment dirs."""
    arch_dir = tmp_path / ".dark-factory" / "architecture"
    arch_dir.mkdir(parents=True, exist_ok=True)
    deltas_dir = arch_dir / "environment.deltas"
    deltas_dir.mkdir(exist_ok=True)
    env_path = arch_dir / "environment.toml"

    if with_delta:
        # Write a prior delta so we test chaining.
        contract = EnvironmentContract()
        parent_hash = hash_view(contract)
        import tomli_w
        delta = {
            "schema_version": 2,
            "spec_id": "bootstrap",
            "parent_hash": parent_hash,
            "applied_at": "2026-01-01T00:00:00Z",
            "ops": [
                {"op": "set", "path": "runtime.language", "value": "node"},
            ],
        }
        (deltas_dir / "0001-bootstrap.toml").write_text(
            tomli_w.dumps(delta), encoding="utf-8",
        )
        materialize(deltas_dir, env_path)
    else:
        # Write empty environment.toml so load_environment works.
        env_path.write_bytes(canonical_dump(EnvironmentContract()))

    return tmp_path


class TestFillsEmptyCommandsViaDelta:
    def test_all_four_commands_filled(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        config = PipelineConfig()
        detected = DetectionResult(
            build_cmd="pnpm run build",
            lint_cmd="pnpm run lint",
            typecheck_cmd="pnpm run typecheck",
            test_cmd="pnpm test",
        )
        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            result = write_autodetect_delta(project, config)

        assert len(result) == 4
        fields = dict(result)
        assert fields["build_cmd"] == "pnpm run build"
        assert fields["lint_cmd"] == "pnpm run lint"
        assert fields["typecheck_cmd"] == "pnpm run typecheck"
        assert fields["test_cmd"] == "pnpm test"

        # Config was updated.
        assert config.build_cmd == "pnpm run build"
        assert config.test_cmd == "pnpm test"

        # Provenance is "auto-detect", not "environment.toml".
        assert config.get_provenance("build_cmd") == "auto-detect"
        assert config.get_provenance("lint_cmd") == "auto-detect"
        assert config.get_provenance("typecheck_cmd") == "auto-detect"
        assert config.get_provenance("test_cmd") == "auto-detect"

        # A delta file was written.
        deltas_dir = project / ".dark-factory" / "architecture" / "environment.deltas"
        delta_files = list_deltas(deltas_dir)
        assert any("auto-detect" in p.name for p in delta_files)

    def test_install_cmd_maps_to_commands_install(self, tmp_path: Path) -> None:
        # Sprint 22: the python dep-install rides install_cmd -> commands.install
        # (the legacy PipelineConfig field `server_setup_cmd`) so it runs at the
        # gate Install stage BEFORE Setup — a [dev]-declared migration tool is
        # then present for the Setup migration (no `alembic: command not found`).
        project = _setup_project(tmp_path)
        config = PipelineConfig()
        cmd = "pip install -e '.[dev]' -q --break-system-packages"
        detected = DetectionResult(install_cmd=cmd, test_cmd="pytest")
        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            result = write_autodetect_delta(project, config)
        # The result is keyed by the PipelineConfig field; commands.install's is
        # the legacy `server_setup_cmd`.
        fields = dict(result)
        assert fields["server_setup_cmd"] == cmd
        assert config.server_setup_cmd == cmd
        assert config.get_provenance("server_setup_cmd") == "auto-detect"

    def test_partial_fill(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        config = PipelineConfig(build_cmd="existing build", lint_cmd="existing lint")
        detected = DetectionResult(
            build_cmd="pnpm run build",
            lint_cmd="pnpm run lint",
            typecheck_cmd="pnpm run typecheck",
            test_cmd="pnpm test",
        )
        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            result = write_autodetect_delta(project, config)

        fields = dict(result)
        assert set(fields.keys()) == {"typecheck_cmd", "test_cmd"}
        # Pre-existing values preserved.
        assert config.build_cmd == "existing build"
        assert config.lint_cmd == "existing lint"


class TestNoopWhenAllCommandsSet:
    def test_returns_empty_list(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        config = PipelineConfig(
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
        deltas_dir = project / ".dark-factory" / "architecture" / "environment.deltas"
        count_before = len(list(deltas_dir.iterdir()))
        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            result = write_autodetect_delta(project, config)

        assert result == []
        # No new delta file.
        assert len(list(deltas_dir.iterdir())) == count_before


class TestDeltaChainsWithPriorDeltas:
    def test_new_delta_chains_after_bootstrap(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path, with_delta=True)
        config = PipelineConfig()
        detected = DetectionResult(build_cmd="npm run build", test_cmd="npm test")
        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            result = write_autodetect_delta(project, config)

        assert len(result) == 2
        # Delta file number should be 0002 (after 0001-bootstrap).
        deltas_dir = project / ".dark-factory" / "architecture" / "environment.deltas"
        delta_files = list_deltas(deltas_dir)
        assert delta_files[-1].name.startswith("0002-")

        # Fold should succeed — parent hash chain is intact.
        contract = fold_deltas(deltas_dir)
        assert contract.commands.build == "npm run build"
        assert contract.commands.test == "npm test"
        assert contract.runtime.language == "node"  # from bootstrap


class TestWarnsForEachGapFilled:
    def test_warnings_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        project = _setup_project(tmp_path)
        config = PipelineConfig()
        detected = DetectionResult(build_cmd="make", test_cmd="pytest")
        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            with caplog.at_level(logging.WARNING, logger="darkfactory.setup.autodetect_delta"):
                write_autodetect_delta(project, config)

        warnings = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("build_cmd" in w and "Architect" in w for w in warnings)
        assert any("test_cmd" in w and "Architect" in w for w in warnings)


class TestDetectionFailureReturnsEmpty:
    def test_returns_empty_on_exception(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        import logging

        project = _setup_project(tmp_path)
        config = PipelineConfig()
        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            side_effect=RuntimeError("boom"),
        ):
            with caplog.at_level(logging.WARNING, logger="darkfactory.setup.autodetect_delta"):
                result = write_autodetect_delta(project, config)

        assert result == []
        assert any("detection failed" in r.message for r in caplog.records)


class TestIdempotentSecondCall:
    def test_second_call_skips_delta_write(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        config = PipelineConfig()
        detected = DetectionResult(build_cmd="npm run build", test_cmd="npm test")

        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            first = write_autodetect_delta(project, config)

        assert len(first) == 2
        deltas_dir = project / ".dark-factory" / "architecture" / "environment.deltas"
        count_after_first = len(list_deltas(deltas_dir))

        # Second call with same detection — config already has values.
        # (config fields are set from first call, so no gaps to fill.)
        _reset_cache()
        config2 = PipelineConfig(build_cmd="npm run build", test_cmd="npm test")
        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            second = write_autodetect_delta(project, config2)

        assert second == []
        assert len(list_deltas(deltas_dir)) == count_after_first

    def test_rerun_with_empty_config_is_idempotent(self, tmp_path: Path) -> None:
        """If config is empty again but delta already set the values, skip."""
        project = _setup_project(tmp_path)
        config = PipelineConfig()
        detected = DetectionResult(build_cmd="npm run build")

        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            first = write_autodetect_delta(project, config)
        assert len(first) == 1

        deltas_dir = project / ".dark-factory" / "architecture" / "environment.deltas"
        count_after_first = len(list_deltas(deltas_dir))

        # Fresh config (empty) but delta log already has the value.
        _reset_cache()
        config2 = PipelineConfig()
        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            second = write_autodetect_delta(project, config2)

        # The function sees config.build_cmd is empty, detection says
        # "npm run build", but the idempotency check sees the materialized
        # view already has commands.build = "npm run build" — skips write.
        assert len(second) == 1  # still reports the fill
        assert config2.build_cmd == "npm run build"
        assert config2.get_provenance("build_cmd") == "auto-detect"
        assert len(list_deltas(deltas_dir)) == count_after_first  # no new delta


class TestDoesNotSetBootstrapOnlyPaths:
    def test_language_not_written(self, tmp_path: Path) -> None:
        """Even if the mapping were expanded to include runtime.language,
        the BOOTSTRAP_ONLY_PATHS guard must prevent it."""
        project = _setup_project(tmp_path)
        config = PipelineConfig()
        # Detection returns language info but no commands.
        detected = DetectionResult(language="python")
        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            result = write_autodetect_delta(project, config)

        # No ops because DetectionResult.language doesn't map to any
        # command field in _DETECT_TO_ENV.
        assert result == []


class TestRollbackOnMaterializeFailure:
    def test_delta_file_removed_env_restored(self, tmp_path: Path) -> None:
        project = _setup_project(tmp_path)
        env_path = project / ".dark-factory" / "architecture" / "environment.toml"
        original_content = env_path.read_bytes()
        config = PipelineConfig()
        detected = DetectionResult(build_cmd="make")

        with patch(
            "darkfactory.setup.autodetect_delta.detect_project",
            return_value=detected,
        ):
            with patch(
                "darkfactory.setup.autodetect_delta.materialize",
                side_effect=RuntimeError("disk full"),
            ):
                with pytest.raises(RuntimeError, match="disk full"):
                    write_autodetect_delta(project, config)

        # environment.toml restored to original.
        assert env_path.read_bytes() == original_content

        # No auto-detect delta file left behind.
        deltas_dir = project / ".dark-factory" / "architecture" / "environment.deltas"
        assert not any("auto-detect" in p.name for p in deltas_dir.iterdir())
