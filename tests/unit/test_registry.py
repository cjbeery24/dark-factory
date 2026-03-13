"""Tests for state/registry.py — shipped scenario registry."""

import unittest

from tests.helpers import TempDirTestCase
from darkfactory.errors import ConfigError
from darkfactory.state.registry import (
    register,
    deregister,
    list_specs,
    scenario_dir,
    read_meta,
    migrate_from_state,
)


class TestRegister(TempDirTestCase):
    def _setup_scenarios(self, spec_name: str) -> None:
        """Create scenario files for a spec."""
        scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / spec_name
        scenarios.mkdir(parents=True)
        (scenarios / "test_happy.py").write_text("def test_happy(): pass")
        (scenarios / "test_edge.py").write_text("def test_edge(): pass")

    def test_register_success(self) -> None:
        self._setup_scenarios("my-spec")
        result = register(self.tmp_dir, "my-spec")
        self.assertTrue(result)
        # Verify files copied.
        registered = self.tmp_dir / ".dark-factory" / "shipped-scenarios" / "my-spec"
        self.assertTrue(registered.exists())
        self.assertTrue((registered / "test_happy.py").exists())
        self.assertTrue((registered / "test_edge.py").exists())

    def test_register_no_scenarios(self) -> None:
        result = register(self.tmp_dir, "missing")
        self.assertFalse(result)

    def test_register_empty_dir(self) -> None:
        scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / "empty"
        scenarios.mkdir(parents=True)
        result = register(self.tmp_dir, "empty")
        self.assertFalse(result)

    def test_register_idempotent(self) -> None:
        self._setup_scenarios("spec")
        register(self.tmp_dir, "spec")
        register(self.tmp_dir, "spec")
        specs = list_specs(self.tmp_dir)
        self.assertEqual(specs, ["spec"])

    def test_rejects_path_traversal(self) -> None:
        with self.assertRaises(ConfigError):
            register(self.tmp_dir, "../escape")
        with self.assertRaises(ConfigError):
            register(self.tmp_dir, "sub/spec")
        with self.assertRaises(ConfigError):
            register(self.tmp_dir, "")

    def test_ignores_dotfiles(self) -> None:
        scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / "spec"
        scenarios.mkdir(parents=True)
        (scenarios / "test.py").write_text("test")
        (scenarios / ".hidden").write_text("hidden")
        register(self.tmp_dir, "spec")
        registered = self.tmp_dir / ".dark-factory" / "shipped-scenarios" / "spec"
        files = [f.name for f in registered.iterdir() if f.is_file() and not f.name.startswith(".")]
        self.assertEqual(files, ["test.py"])


class TestDeregister(TempDirTestCase):
    def test_deregister_success(self) -> None:
        scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / "spec"
        scenarios.mkdir(parents=True)
        (scenarios / "test.py").write_text("test")
        register(self.tmp_dir, "spec")
        result = deregister(self.tmp_dir, "spec")
        self.assertTrue(result)
        self.assertEqual(list_specs(self.tmp_dir), [])

    def test_deregister_not_found(self) -> None:
        result = deregister(self.tmp_dir, "missing")
        self.assertFalse(result)


class TestListSpecs(TempDirTestCase):
    def test_empty(self) -> None:
        self.assertEqual(list_specs(self.tmp_dir), [])

    def test_multiple(self) -> None:
        for name in ["spec-b", "spec-a", "spec-c"]:
            scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / name
            scenarios.mkdir(parents=True)
            (scenarios / "test.py").write_text("test")
            register(self.tmp_dir, name)
        specs = list_specs(self.tmp_dir)
        self.assertEqual(specs, ["spec-a", "spec-b", "spec-c"])


class TestScenarioDir(TempDirTestCase):
    def test_registered(self) -> None:
        scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / "spec"
        scenarios.mkdir(parents=True)
        (scenarios / "test.py").write_text("test")
        register(self.tmp_dir, "spec")
        d = scenario_dir(self.tmp_dir, "spec")
        self.assertIsNotNone(d)
        self.assertTrue(d.exists())

    def test_not_registered(self) -> None:
        d = scenario_dir(self.tmp_dir, "missing")
        self.assertIsNone(d)


class TestReadMeta(TempDirTestCase):
    def test_reads_metadata(self) -> None:
        scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / "spec"
        scenarios.mkdir(parents=True)
        (scenarios / "test.py").write_text("test")
        register(self.tmp_dir, "spec")
        meta = read_meta(self.tmp_dir, "spec")
        self.assertEqual(meta["spec"], "spec")
        self.assertIn("registered", meta)
        self.assertIn("scenario_count", meta)

    def test_not_registered(self) -> None:
        meta = read_meta(self.tmp_dir, "missing")
        self.assertEqual(meta, {})


class TestMigrateFromState(TempDirTestCase):
    def test_migrates_shipped(self) -> None:
        # Create legacy state files.
        state_dir = self.tmp_dir / ".dark-factory" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "spec-a.state").write_text("status=shipped\n")
        (state_dir / "spec-b.state").write_text("status=failed\n")
        # Create scenario dirs for migration.
        for name in ["spec-a", "spec-b"]:
            scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / name
            scenarios.mkdir(parents=True)
            (scenarios / "test.py").write_text("test")
        count = migrate_from_state(self.tmp_dir)
        self.assertEqual(count, 1)  # Only shipped spec.
        self.assertEqual(list_specs(self.tmp_dir), ["spec-a"])

    def test_skips_if_already_populated(self) -> None:
        # Pre-populate registry.
        scenarios = self.tmp_dir / ".dark-factory" / "scenarios" / "existing"
        scenarios.mkdir(parents=True)
        (scenarios / "test.py").write_text("test")
        register(self.tmp_dir, "existing")
        # Create legacy state.
        state_dir = self.tmp_dir / ".dark-factory" / "state"
        state_dir.mkdir(parents=True)
        (state_dir / "old.state").write_text("status=shipped\n")
        count = migrate_from_state(self.tmp_dir)
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
