"""Integration tests for selective regression and scenario registry.

Mock boundary: run_scenarios (requires Docker) is mocked. Real:
registry, coverage, filesystem, events, FileLock.

Depends on: registry_fixture from conftest.py (Phase 0).

Does NOT duplicate (already in tests/unit/test_selective_regression.py):
- TestAffectedSpecs (exact duplicate)
- TestTargetFileStorage (register/read_target_files)
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tests.integration.conftest import read_events
from darkfactory.scenarios.regression import (
    _link_scenarios,
    _unlink_scenarios,
    run_regression,
)
from darkfactory.state.registry import (
    deregister,
    list_specs,
    migrate_from_state,
    register,
    scenario_dir,
)
from darkfactory.utils.subprocess_runner import RunResult


# --- Helpers ---

def _make_config(project_dir: Path):
    from darkfactory.config.loader import load_config
    return load_config(project_dir / ".dark-factory" / "config.toml")


@pytest.fixture
def patch_run_scenarios():
    """Patch run_scenarios at the regression module's import site."""
    state: dict = {}

    def _setup(results: list[RunResult]) -> MagicMock:
        mock = MagicMock(side_effect=results)
        patcher = patch(
            "darkfactory.scenarios.regression.run_scenarios",
            mock,
        )
        patcher.start()
        state["patcher"] = patcher
        return mock

    yield _setup

    if "patcher" in state:
        state["patcher"].stop()


# --- Tests ---


@pytest.mark.integration
class TestRegressionLifecycle:
    """Full register -> change -> affected -> run lifecycle."""

    def test_full_cycle_register_change_affected_run(
        self, project_dir, registry_fixture, patch_run_scenarios
    ):
        """Register specs, change a file, find affected specs, run regression."""
        config = _make_config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        mock = patch_run_scenarios([
            RunResult(exit_code=0, stdout="all passed"),
        ])

        passed, failed, total = run_regression(
            config, project_dir,
            changed_files=["src/auth.py"],  # Only affects add-auth.
            events_file=events_file,
            run_id="test-run",
        )

        assert passed == 1
        assert failed == 0
        assert total == 1
        assert mock.call_count == 1

    def test_exclude_list_honored(
        self, project_dir, registry_fixture, patch_run_scenarios
    ):
        """Excluded specs are not run even if affected."""
        config = _make_config(project_dir)

        mock = patch_run_scenarios([
            RunResult(exit_code=0, stdout="all passed"),
            RunResult(exit_code=0, stdout="all passed"),
        ])

        passed, failed, total = run_regression(
            config, project_dir,
            exclude=["add-auth"],
        )

        # add-auth excluded, add-api and add-ui should run.
        assert total == 2
        assert mock.call_count == 2

    def test_empty_registry_returns_zeros(self, project_dir):
        """Empty registry -> (0, 0, 0) with no errors."""
        config = _make_config(project_dir)

        passed, failed, total = run_regression(config, project_dir)

        assert (passed, failed, total) == (0, 0, 0)

    def test_regression_events_emitted(
        self, project_dir, registry_fixture, patch_run_scenarios
    ):
        """Regression run emits events to the events file."""
        config = _make_config(project_dir)
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        patch_run_scenarios([
            RunResult(exit_code=0, stdout="passed"),
            RunResult(exit_code=0, stdout="passed"),
            RunResult(exit_code=0, stdout="passed"),
        ])

        run_regression(
            config, project_dir,
            events_file=events_file,
            run_id="ev-test",
        )

        events = read_events(events_file)
        regression_events = [e for e in events if e.get("event") == "REGRESSION_RUN"]
        assert len(regression_events) == 1
        assert regression_events[0]["data"]["passed"] == 3


@pytest.mark.integration
class TestSymlinkLifecycle:
    """Symlink creation and cleanup for regression runs."""

    def test_link_and_unlink_scenarios(self, project_dir, registry_fixture):
        """_link_scenarios creates symlink, _unlink_scenarios removes cleanly."""
        source = project_dir / ".dark-factory" / "shipped-scenarios" / "add-auth"
        target = project_dir / ".dark-factory" / "scenarios" / "add-auth"

        assert not target.exists()

        _link_scenarios(source, target)

        assert target.is_symlink()
        assert target.resolve() == source.resolve()
        assert any(f.name.endswith(".test.ts") for f in target.iterdir())

        _unlink_scenarios(target)

        assert not target.exists()
        assert not target.is_symlink()

    def test_no_dangling_symlinks_after_cleanup(self, project_dir, registry_fixture):
        """After unlinking all scenarios, no dangling symlinks remain."""
        scenarios_dir = project_dir / ".dark-factory" / "scenarios"
        scenarios_dir.mkdir(parents=True, exist_ok=True)

        for spec_name in registry_fixture:
            source = project_dir / ".dark-factory" / "shipped-scenarios" / spec_name
            target = scenarios_dir / spec_name
            _link_scenarios(source, target)

        # Verify all linked.
        for spec_name in registry_fixture:
            assert (scenarios_dir / spec_name).is_symlink()

        # Unlink all.
        for spec_name in registry_fixture:
            _unlink_scenarios(scenarios_dir / spec_name)

        # No dangling symlinks.
        for child in scenarios_dir.iterdir():
            assert not child.is_symlink(), f"dangling symlink: {child}"


@pytest.mark.integration
@pytest.mark.slow
class TestRegistryConcurrency:
    """Concurrent register/deregister with FileLock."""

    def test_concurrent_register_deregister(self, project_dir):
        """Two threads register and deregister concurrently without corruption."""
        for name in ("con-reg-a", "con-reg-b"):
            spec_file = project_dir / ".dark-factory" / "specs" / f"{name}.md"
            spec_file.write_text(f"# {name}\ntarget-files: src/{name}.py\n")
            sdir = project_dir / ".dark-factory" / "scenarios" / name
            sdir.mkdir(parents=True)
            (sdir / "test.py").write_text("assert True")

        errors: list[Exception] = []

        def register_loop(spec_name: str):
            try:
                for _ in range(5):
                    register(project_dir, spec_name)
                    deregister(project_dir, spec_name)
                # Final register so it exists at the end.
                register(project_dir, spec_name)
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=register_loop, args=("con-reg-a",))
        t2 = threading.Thread(target=register_loop, args=("con-reg-b",))

        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Concurrent ops raised: {errors}"
        specs = list_specs(project_dir)
        assert "con-reg-a" in specs
        assert "con-reg-b" in specs


@pytest.mark.integration
class TestRegistryMigration:
    """migrate_from_state populates registry from legacy state files."""

    def test_migrate_from_legacy_state(self, project_dir):
        """Legacy state files with status=shipped are migrated to registry."""
        state_dir = project_dir / ".dark-factory" / "state"
        state_dir.mkdir(parents=True, exist_ok=True)
        (state_dir / "legacy-reg.state").write_text("status=shipped\nrun_id=abc123\n")

        sdir = project_dir / ".dark-factory" / "scenarios" / "legacy-reg"
        sdir.mkdir(parents=True)
        (sdir / "test_one.py").write_text("assert True")

        (project_dir / ".dark-factory" / "specs" / "legacy-reg.md").write_text(
            "# legacy-reg\ntarget-files: src/legacy.py\n"
        )

        count = migrate_from_state(project_dir)

        assert count == 1
        assert "legacy-reg" in list_specs(project_dir)
        assert scenario_dir(project_dir, "legacy-reg") is not None
