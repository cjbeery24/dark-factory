"""Integration tests for plan parsing, topological sort, and conflict detection.

No Docker needed — tests exercise plan parsing from markdown files,
topological sort with real dependency graphs, and file conflict detection.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from darkfactory.errors import ConfigError
from darkfactory.models.plan import PlanSpec
from darkfactory.plan.conflict import detect_conflicts, serialize_conflicts
from darkfactory.plan.parser import parse_plan, parse_target_files
from darkfactory.plan.topo_sort import topological_sort


def _write_plan(path: Path, table: str) -> Path:
    plan_file = path / ".dark-factory" / "plans" / "test-plan.md"
    plan_file.parent.mkdir(parents=True, exist_ok=True)
    plan_file.write_text(f"# Test Plan\n\n{table}\n")
    return plan_file


def _write_spec(path: Path, name: str, content: str = "") -> Path:
    spec_file = path / ".dark-factory" / "specs" / f"{name}.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(content or f"# {name}\n\nA test spec.\n")
    return spec_file


@pytest.mark.integration
class TestPlanParsing:
    """Parse plan markdown tables into PlanSpec lists."""

    def test_simple_plan(self, tmp_path: Path):
        _write_spec(tmp_path, "add-auth")
        _write_spec(tmp_path, "add-api")
        plan_file = _write_plan(tmp_path, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
            "| add-api | add-auth | 2 |\n"
        ))
        specs = parse_plan(plan_file, tmp_path)
        assert len(specs) == 2
        assert specs[0].name == "add-auth"
        assert specs[0].deps == []
        assert specs[0].priority == 1
        assert specs[1].name == "add-api"
        assert specs[1].deps == ["add-auth"]
        assert specs[1].priority == 2

    def test_no_specs_raises(self, tmp_path: Path):
        plan_file = _write_plan(tmp_path, "Just some text, no table.\n")
        with pytest.raises(ConfigError, match="no specs found"):
            parse_plan(plan_file, tmp_path)

    def test_duplicate_spec_raises(self, tmp_path: Path):
        _write_spec(tmp_path, "add-auth")
        plan_file = _write_plan(tmp_path, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
            "| add-auth | — | 2 |\n"
        ))
        with pytest.raises(ConfigError, match="duplicate"):
            parse_plan(plan_file, tmp_path)

    def test_unknown_dependency_raises(self, tmp_path: Path):
        _write_spec(tmp_path, "add-auth")
        plan_file = _write_plan(tmp_path, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | nonexistent | 1 |\n"
        ))
        with pytest.raises(ConfigError, match="unknown spec"):
            parse_plan(plan_file, tmp_path)

    def test_spec_metadata_overrides_deps(self, tmp_path: Path):
        # Plan says add-api depends on add-auth, but spec metadata overrides
        _write_spec(tmp_path, "add-auth")
        _write_spec(tmp_path, "add-api",
                     "# add-api\ndepends-on: []\n\nA spec.\n")
        plan_file = _write_plan(tmp_path, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | 1 |\n"
            "| add-api | add-auth | 2 |\n"
        ))
        specs = parse_plan(plan_file, tmp_path)
        api_spec = next(s for s in specs if s.name == "add-api")
        assert api_spec.deps == []

    def test_non_numeric_priority_raises(self, tmp_path: Path):
        _write_spec(tmp_path, "add-auth")
        plan_file = _write_plan(tmp_path, (
            "| Spec | Dependencies | Priority |\n"
            "| --- | --- | --- |\n"
            "| add-auth | — | high |\n"
        ))
        with pytest.raises(ConfigError, match="non-numeric"):
            parse_plan(plan_file, tmp_path)


@pytest.mark.integration
class TestParseTargetFiles:
    """Extract target-files from spec metadata."""

    def test_parses_target_files(self, tmp_path: Path):
        spec = _write_spec(tmp_path, "test-spec", (
            "# test-spec\n\n"
            "target-files:\n"
            "  - src/auth.py\n"
            "  - src/middleware.py\n"
            "\n## Description\nSome text.\n"
        ))
        files = parse_target_files(spec)
        assert files == ["src/auth.py", "src/middleware.py"]

    def test_no_target_files(self, tmp_path: Path):
        spec = _write_spec(tmp_path, "test-spec", "# test-spec\n\nNo targets.\n")
        files = parse_target_files(spec)
        assert files == []


@pytest.mark.integration
class TestTopologicalSort:
    """Kahn's algorithm with priority tie-breaking."""

    def test_linear_chain(self):
        specs = [
            PlanSpec(name="a", deps=[]),
            PlanSpec(name="b", deps=["a"]),
            PlanSpec(name="c", deps=["b"]),
        ]
        result = topological_sort(specs)
        names = [s.name for s in result]
        assert names == ["a", "b", "c"]

    def test_priority_ordering(self):
        specs = [
            PlanSpec(name="a", deps=[], priority=2),
            PlanSpec(name="b", deps=[], priority=1),
            PlanSpec(name="c", deps=[], priority=3),
        ]
        result = topological_sort(specs)
        names = [s.name for s in result]
        assert names == ["b", "a", "c"]

    def test_diamond_dependency(self):
        specs = [
            PlanSpec(name="a", deps=[]),
            PlanSpec(name="b", deps=["a"]),
            PlanSpec(name="c", deps=["a"]),
            PlanSpec(name="d", deps=["b", "c"]),
        ]
        result = topological_sort(specs)
        names = [s.name for s in result]
        assert names.index("a") < names.index("b")
        assert names.index("a") < names.index("c")
        assert names.index("b") < names.index("d")
        assert names.index("c") < names.index("d")

    def test_circular_dependency_raises(self):
        specs = [
            PlanSpec(name="a", deps=["b"]),
            PlanSpec(name="b", deps=["a"]),
        ]
        with pytest.raises(ConfigError, match="circular dependency"):
            topological_sort(specs)

    def test_empty_plan(self):
        assert topological_sort([]) == []

    def test_single_spec(self):
        specs = [PlanSpec(name="a")]
        result = topological_sort(specs)
        assert [s.name for s in result] == ["a"]


@pytest.mark.integration
class TestConflictDetection:
    """File conflict detection between specs."""

    def test_no_conflicts(self):
        specs = [
            PlanSpec(name="a", target_files=["src/a.py"]),
            PlanSpec(name="b", target_files=["src/b.py"]),
        ]
        report = detect_conflicts(specs)
        assert not report.has_conflicts

    def test_shared_file_detected(self):
        specs = [
            PlanSpec(name="a", target_files=["src/shared.py"]),
            PlanSpec(name="b", target_files=["src/shared.py", "src/b.py"]),
        ]
        report = detect_conflicts(specs)
        assert report.has_conflicts
        assert "src/shared.py" in report.file_owners
        assert set(report.file_owners["src/shared.py"]) == {"a", "b"}

    def test_no_target_files(self):
        specs = [PlanSpec(name="a"), PlanSpec(name="b")]
        report = detect_conflicts(specs)
        assert not report.has_conflicts


@pytest.mark.integration
class TestSerializeConflicts:
    """Conflict serialization adds synthetic dependency edges."""

    def test_serializes_shared_files(self):
        specs = [
            PlanSpec(name="a", target_files=["src/shared.py"], priority=1),
            PlanSpec(name="b", target_files=["src/shared.py"], priority=2),
        ]
        dispatch_order = topological_sort(specs)
        serialize_conflicts(specs, dispatch_order)

        # b should now depend on a (a is first in dispatch order)
        b_spec = next(s for s in specs if s.name == "b")
        assert "a" in b_spec.deps

    def test_no_conflicts_no_changes(self):
        specs = [
            PlanSpec(name="a", target_files=["src/a.py"]),
            PlanSpec(name="b", target_files=["src/b.py"]),
        ]
        dispatch_order = topological_sort(specs)
        serialize_conflicts(specs, dispatch_order)

        for s in specs:
            assert s.deps == []
