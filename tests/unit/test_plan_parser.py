"""Tests for plan/parser.py — markdown plan table parsing."""

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.errors import ConfigError
from darkfactory.plan.parser import parse_plan, parse_target_files


class TestParsePlan(TempDirTestCase):
    def _setup_plan(self, content: str) -> Path:
        plan_dir = self.tmp_dir / ".dark-factory" / "plans"
        plan_dir.mkdir(parents=True)
        plan_file = plan_dir / "test-plan.md"
        plan_file.write_text(content)
        return plan_file

    def _setup_spec(self, name: str, content: str = "# Spec\n") -> None:
        spec_dir = self.tmp_dir / ".dark-factory" / "specs"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (spec_dir / f"{name}.md").write_text(content)

    def test_basic_table(self) -> None:
        self._setup_spec("auth")
        self._setup_spec("api")
        plan = self._setup_plan(
            "# Plan\n\n"
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| auth | — | 1 |\n"
            "| api | auth | 2 |\n"
        )
        specs = parse_plan(plan, self.tmp_dir)
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0].name, "auth")
        self.assertEqual(specs[0].deps, [])
        self.assertEqual(specs[0].priority, 1)
        self.assertEqual(specs[1].name, "api")
        self.assertEqual(specs[1].deps, ["auth"])
        self.assertEqual(specs[1].priority, 2)

    def test_no_specs_raises(self) -> None:
        plan = self._setup_plan("# Empty Plan\n")
        with self.assertRaises(ConfigError):
            parse_plan(plan, self.tmp_dir)

    def test_duplicate_spec_raises(self) -> None:
        plan = self._setup_plan(
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| auth | — | 1 |\n"
            "| auth | — | 2 |\n"
        )
        with self.assertRaises(ConfigError):
            parse_plan(plan, self.tmp_dir)

    def test_invalid_spec_name_raises(self) -> None:
        plan = self._setup_plan(
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| ../bad | — | 1 |\n"
        )
        with self.assertRaises(ConfigError):
            parse_plan(plan, self.tmp_dir)

    def test_non_numeric_priority_raises(self) -> None:
        self._setup_spec("auth")
        plan = self._setup_plan(
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| auth | — | high |\n"
        )
        with self.assertRaises(ConfigError):
            parse_plan(plan, self.tmp_dir)

    def test_unknown_dep_raises(self) -> None:
        self._setup_spec("api")
        plan = self._setup_plan(
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| api | nonexistent | 1 |\n"
        )
        with self.assertRaises(ConfigError):
            parse_plan(plan, self.tmp_dir)

    def test_metadata_overrides_table_deps(self) -> None:
        self._setup_spec("auth")
        self._setup_spec("api", "depends-on: [auth]\n# API Spec\n")
        plan = self._setup_plan(
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| auth | — | 1 |\n"
            "| api | — | 2 |\n"
        )
        specs = parse_plan(plan, self.tmp_dir)
        api = next(s for s in specs if s.name == "api")
        self.assertEqual(api.deps, ["auth"])

    def test_default_priority(self) -> None:
        self._setup_spec("auth")
        plan = self._setup_plan(
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| auth | — |  |\n"
        )
        specs = parse_plan(plan, self.tmp_dir)
        self.assertEqual(specs[0].priority, 99)

    def test_multiple_deps(self) -> None:
        self._setup_spec("a")
        self._setup_spec("b")
        self._setup_spec("c")
        plan = self._setup_plan(
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| a | — | 1 |\n"
            "| b | — | 1 |\n"
            "| c | a, b | 2 |\n"
        )
        specs = parse_plan(plan, self.tmp_dir)
        c = next(s for s in specs if s.name == "c")
        self.assertEqual(c.deps, ["a", "b"])


class TestParseTargetFiles(TempDirTestCase):
    def test_parses_target_files(self) -> None:
        spec = self.tmp_dir / "spec.md"
        spec.write_text(
            "target-files:\n"
            "  - src/foo.ts\n"
            "  - src/bar.ts\n"
            "\n"
            "# Spec\n"
        )
        files = parse_target_files(spec)
        self.assertEqual(files, ["src/foo.ts", "src/bar.ts"])

    def test_empty_target_files(self) -> None:
        spec = self.tmp_dir / "spec.md"
        spec.write_text("# Spec\nNo target files here.\n")
        files = parse_target_files(spec)
        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()
