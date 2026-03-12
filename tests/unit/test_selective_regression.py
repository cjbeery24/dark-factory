"""Tests for selective regression (Phase 7B)."""

import shutil
import tempfile
import unittest
from pathlib import Path

from darkfactory.scenarios.coverage import affected_specs
from darkfactory.state.registry import META_FILE, register, read_target_files


class TestTargetFileStorage(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.project = Path(self.tmpdir)
        self.spec_name = "test-spec"
        # Create spec with target-files.
        specs_dir = self.project / ".dark-factory" / "specs"
        specs_dir.mkdir(parents=True)
        (specs_dir / f"{self.spec_name}.md").write_text(
            "# Test Spec\ntarget-files: src/foo.py, src/bar.py\n"
        )
        # Create scenarios.
        scenarios_dir = self.project / ".dark-factory" / "scenarios" / self.spec_name
        scenarios_dir.mkdir(parents=True)
        (scenarios_dir / "test_one.py").write_text("assert True")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_register_stores_target_files(self) -> None:
        register(self.project, self.spec_name)
        meta_path = (
            self.project / ".dark-factory" / "shipped-scenarios"
            / self.spec_name / META_FILE
        )
        content = meta_path.read_text()
        self.assertIn("target_files=src/foo.py, src/bar.py", content)

    def test_read_target_files_from_registry(self) -> None:
        register(self.project, self.spec_name)
        files = read_target_files(self.project, self.spec_name)
        self.assertEqual(files, {"src/foo.py", "src/bar.py"})

    def test_read_target_files_empty_when_not_set(self) -> None:
        # Create a spec without target-files.
        spec2 = "no-targets"
        specs_dir = self.project / ".dark-factory" / "specs"
        (specs_dir / f"{spec2}.md").write_text("# No targets\n")
        scenarios_dir = self.project / ".dark-factory" / "scenarios" / spec2
        scenarios_dir.mkdir(parents=True)
        (scenarios_dir / "test_one.py").write_text("assert True")
        register(self.project, spec2)
        files = read_target_files(self.project, spec2)
        self.assertEqual(files, set())


class TestAffectedSpecs(unittest.TestCase):
    def setUp(self) -> None:
        self.tmpdir = tempfile.mkdtemp()
        self.project = Path(self.tmpdir)
        for name, targets in [("spec-a", "src/a.py, src/shared.py"),
                               ("spec-b", "src/b.py, src/shared.py"),
                               ("spec-c", "src/c.py")]:
            specs_dir = self.project / ".dark-factory" / "specs"
            specs_dir.mkdir(parents=True, exist_ok=True)
            (specs_dir / f"{name}.md").write_text(f"# {name}\ntarget-files: {targets}\n")
            scenarios_dir = self.project / ".dark-factory" / "scenarios" / name
            scenarios_dir.mkdir(parents=True, exist_ok=True)
            (scenarios_dir / "test.py").write_text("pass")
            register(self.project, name)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_overlap_returns_affected(self) -> None:
        result = affected_specs(self.project, {"src/a.py"})
        self.assertEqual(result, ["spec-a"])

    def test_shared_file_returns_multiple(self) -> None:
        result = affected_specs(self.project, {"src/shared.py"})
        self.assertEqual(result, ["spec-a", "spec-b"])

    def test_no_overlap_returns_empty(self) -> None:
        result = affected_specs(self.project, {"src/unrelated.py"})
        self.assertEqual(result, [])

    def test_empty_changed_files(self) -> None:
        result = affected_specs(self.project, set())
        self.assertEqual(result, [])

    def test_multiple_changes_union(self) -> None:
        result = affected_specs(self.project, {"src/a.py", "src/c.py"})
        self.assertEqual(result, ["spec-a", "spec-c"])


class TestCoverageReadTargetFiles(unittest.TestCase):
    def test_read_target_files_via_registry(self) -> None:
        tmpdir = tempfile.mkdtemp()
        try:
            project = Path(tmpdir)
            name = "cov-spec"
            specs_dir = project / ".dark-factory" / "specs"
            specs_dir.mkdir(parents=True)
            (specs_dir / f"{name}.md").write_text("# Test\ntarget-files: x.py, y.py\n")
            scenarios_dir = project / ".dark-factory" / "scenarios" / name
            scenarios_dir.mkdir(parents=True)
            (scenarios_dir / "t.py").write_text("pass")
            register(project, name)
            files = read_target_files(project, name)
            self.assertEqual(files, {"x.py", "y.py"})
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
