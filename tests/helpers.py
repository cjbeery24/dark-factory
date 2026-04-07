"""Shared test utilities."""

import tempfile
from pathlib import Path
from unittest import TestCase


def make_passing_precheck_result():
    """Construct a real PreCheckResult that mimics a clean spec.

    Tests previously used MagicMock for this, which forced production code
    to use defensive `getattr(..., "fatal", False)` because MagicMock auto-
    generates truthy attributes. Phase 1: tests build real results.
    """
    from darkfactory.spec.precheck import PreCheckResult, SpecStructureResult
    return PreCheckResult(
        passed=True,
        structure=SpecStructureResult(sections=[], score=100, passed=True),
        summary="ok",
        fatal=False,
    )


class TempDirTestCase(TestCase):
    """TestCase with a temporary directory that's cleaned up after each test."""

    tmp_dir: Path
    _tmp_obj: tempfile.TemporaryDirectory[str]

    def setUp(self) -> None:
        self._tmp_obj = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp_obj.name)

    def tearDown(self) -> None:
        self._tmp_obj.cleanup()


def make_project(root: Path) -> Path:
    """Create a minimal .dark-factory project structure and return root."""
    (root / ".dark-factory" / "specs").mkdir(parents=True)
    (root / ".dark-factory" / "scenarios").mkdir(parents=True)
    (root / ".dark-factory" / "reviews").mkdir(parents=True)
    (root / ".dark-factory" / "reports").mkdir(parents=True)
    (root / ".dark-factory" / "audit").mkdir(parents=True)
    (root / ".dark-factory" / "state").mkdir(parents=True)
    (root / ".dark-factory" / "plans").mkdir(parents=True)
    return root
