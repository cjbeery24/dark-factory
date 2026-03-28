"""Tests for plan/parser.py — markdown plan table parsing."""

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.errors import ConfigError
from darkfactory.plan.parser import parse_plan, parse_target_files


def _feature_table(*rows: tuple[str, str, str, str, str]) -> str:
    """Build a feature decomposition table from (id, name, type, deps, desc) tuples."""
    lines = [
        "| ID | Feature Name | Spec Type | Depends On | Description |",
        "| --- | --- | --- | --- | --- |",
    ]
    for fid, name, stype, deps, desc in rows:
        lines.append(f"| {fid} | {name} | {stype} | {deps} | {desc} |")
    return "\n".join(lines) + "\n"


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
            + _feature_table(
                ("F-1", "Auth", "feature", "—", "Authentication"),
                ("F-2", "API", "api", "F-1", "API endpoints"),
            )
        )
        specs = parse_plan(plan, self.tmp_dir)
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0].name, "auth")
        self.assertEqual(specs[0].deps, [])
        self.assertEqual(specs[1].name, "api")
        self.assertEqual(specs[1].deps, ["auth"])

    def test_no_specs_raises(self) -> None:
        plan = self._setup_plan("# Empty Plan\n")
        with self.assertRaises(ConfigError):
            parse_plan(plan, self.tmp_dir)

    def test_duplicate_spec_raises(self) -> None:
        plan = self._setup_plan(
            _feature_table(
                ("F-1", "Auth", "feature", "—", "Auth"),
                ("F-2", "Auth", "feature", "—", "Auth again"),
            )
        )
        # Duplicate names produce a warning and skip, resulting in 1 spec.
        specs = parse_plan(plan, self.tmp_dir)
        self.assertEqual(len(specs), 1)

    def test_invalid_feature_id_skipped(self) -> None:
        plan = self._setup_plan(
            _feature_table(
                ("bad", "Auth", "feature", "—", "Auth"),
            )
        )
        with self.assertRaises(ConfigError):
            parse_plan(plan, self.tmp_dir)

    def test_unknown_table_dep_dropped(self) -> None:
        """Unknown dep IDs in the table are silently dropped (logged as warning)."""
        self._setup_spec("api")
        plan = self._setup_plan(
            _feature_table(
                ("F-1", "API", "api", "F-99", "API endpoints"),
            )
        )
        specs = parse_plan(plan, self.tmp_dir)
        self.assertEqual(specs[0].deps, [])

    def test_metadata_overrides_table_deps(self) -> None:
        self._setup_spec("auth")
        self._setup_spec("api", "depends-on: [F-1]\n# API Spec\n")
        plan = self._setup_plan(
            _feature_table(
                ("F-1", "Auth", "feature", "—", "Auth"),
                ("F-2", "API", "api", "—", "API"),
            )
        )
        specs = parse_plan(plan, self.tmp_dir)
        api = next(s for s in specs if s.name == "api")
        self.assertEqual(api.deps, ["auth"])

    def test_multiple_deps(self) -> None:
        self._setup_spec("a")
        self._setup_spec("b")
        self._setup_spec("c")
        plan = self._setup_plan(
            _feature_table(
                ("F-1", "A", "feature", "—", "A"),
                ("F-2", "B", "feature", "—", "B"),
                ("F-3", "C", "feature", "F-1, F-2", "C"),
            )
        )
        specs = parse_plan(plan, self.tmp_dir)
        c = next(s for s in specs if s.name == "c")
        self.assertEqual(sorted(c.deps), ["a", "b"])

    # --- Sub-feature ID tests (F-1a, F-1b) ---

    def test_sub_feature_ids_parsed(self) -> None:
        """Sub-feature IDs like F-1a, F-1b must be accepted by the parser."""
        self._setup_spec("monorepo-structure")
        self._setup_spec("api-scaffold")
        plan = self._setup_plan(
            _feature_table(
                ("F-1a", "Monorepo Structure", "feature", "—", "pnpm workspace"),
                ("F-1b", "API Scaffold", "feature", "F-1a", "Fastify setup"),
            )
        )
        specs = parse_plan(plan, self.tmp_dir)
        self.assertEqual(len(specs), 2)
        self.assertEqual(specs[0].name, "monorepo-structure")
        self.assertEqual(specs[0].deps, [])
        self.assertEqual(specs[1].name, "api-scaffold")
        self.assertEqual(specs[1].deps, ["monorepo-structure"])

    def test_sub_feature_mixed_with_regular(self) -> None:
        """Sub-features can coexist with regular feature IDs."""
        self._setup_spec("scaffold")
        self._setup_spec("scaffold-ci")
        self._setup_spec("database")
        plan = self._setup_plan(
            _feature_table(
                ("F-1a", "Scaffold", "feature", "—", "Base project"),
                ("F-1b", "Scaffold CI", "feature", "F-1a", "CI config"),
                ("F-2", "Database", "feature", "F-1a", "DB setup"),
            )
        )
        specs = parse_plan(plan, self.tmp_dir)
        self.assertEqual(len(specs), 3)
        db = next(s for s in specs if s.name == "database")
        self.assertEqual(db.deps, ["scaffold"])

    def test_sub_feature_dep_resolution(self) -> None:
        """Dependencies between sub-features resolve correctly."""
        self._setup_spec("web")
        self._setup_spec("shared")
        plan = self._setup_plan(
            _feature_table(
                ("F-1a", "Shared", "feature", "—", "Shared types"),
                ("F-1b", "Web", "feature", "F-1a", "Web app"),
            )
        )
        specs = parse_plan(plan, self.tmp_dir)
        web = next(s for s in specs if s.name == "web")
        self.assertEqual(web.deps, ["shared"])


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
