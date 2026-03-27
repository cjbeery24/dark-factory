"""Tests for docker/mounts.py — mount/tmpfs argument building."""

import os
import unittest

from tests.helpers import TempDirTestCase
from darkfactory.docker.mounts import (
    build_project_mount,
    build_tmpfs_masks,
    build_cache_volumes,
    build_platform_isolation_overlays,
    check_symlink_safety,
    check_hardlink_safety,
)
from darkfactory.errors import BarrierViolation


class TestProjectMount(unittest.TestCase):
    def test_readonly(self) -> None:
        args = build_project_mount("/host/project", rw=False)
        self.assertEqual(args, ["-v", "/host/project:/project:ro"])

    def test_readwrite(self) -> None:
        args = build_project_mount("/host/project", rw=True)
        self.assertEqual(args, ["-v", "/host/project:/project:rw"])


class TestTmpfsMasks(unittest.TestCase):
    def test_builds_masks(self) -> None:
        args = build_tmpfs_masks("/host", [".dark-factory/scenarios", ".git"])
        self.assertEqual(len(args), 4)  # 2 args per mask.
        self.assertIn("--tmpfs", args)
        self.assertTrue(any("scenarios" in a for a in args))
        self.assertTrue(any(".git" in a for a in args))

    def test_empty_masks(self) -> None:
        args = build_tmpfs_masks("/host", [])
        self.assertEqual(args, [])


class TestCacheVolumes(unittest.TestCase):
    def test_returns_volumes(self) -> None:
        args = build_cache_volumes("implementer")
        self.assertTrue(len(args) > 0)
        self.assertTrue(all(a == "-v" or "cache" in a or "npm" in a or "pip" in a or "go" in a or "cargo" in a for a in args))


class TestSymlinkSafety(TempDirTestCase):
    def test_no_symlinks(self) -> None:
        (self.tmp_dir / ".dark-factory" / "scenarios").mkdir(parents=True)
        # Should not raise.
        check_symlink_safety(self.tmp_dir, [".dark-factory/scenarios"])

    def test_detects_symlink(self) -> None:
        (self.tmp_dir / "real_scenarios").mkdir()
        (self.tmp_dir / ".dark-factory").mkdir()
        os.symlink(
            str(self.tmp_dir / "real_scenarios"),
            str(self.tmp_dir / ".dark-factory" / "scenarios"),
        )
        with self.assertRaises(BarrierViolation):
            check_symlink_safety(self.tmp_dir, [".dark-factory/scenarios"])

    def test_detects_reverse_symlink(self) -> None:
        """Symlink in visible dir pointing into masked dir."""
        (self.tmp_dir / "src").mkdir()
        (self.tmp_dir / ".dark-factory" / "scenarios").mkdir(parents=True)
        secret = self.tmp_dir / ".dark-factory" / "scenarios" / "test.py"
        secret.write_text("secret test")
        os.symlink(str(secret), str(self.tmp_dir / "src" / "leaked.py"))
        with self.assertRaises(BarrierViolation):
            check_symlink_safety(
                self.tmp_dir,
                [".dark-factory/scenarios"],
                visible_dirs=["src"],
            )


class TestHardlinkSafety(TempDirTestCase):
    def test_no_hardlinks(self) -> None:
        (self.tmp_dir / "src").mkdir()
        (self.tmp_dir / "src" / "main.py").write_text("code")
        (self.tmp_dir / ".dark-factory" / "scenarios").mkdir(parents=True)
        (self.tmp_dir / ".dark-factory" / "scenarios" / "test.py").write_text("test")
        # Should not raise.
        check_hardlink_safety(
            self.tmp_dir, ["src"], [".dark-factory/scenarios"],
        )

    def test_detects_hardlink(self) -> None:
        (self.tmp_dir / "src").mkdir()
        (self.tmp_dir / ".dark-factory" / "scenarios").mkdir(parents=True)
        # Create a file in masked dir.
        secret = self.tmp_dir / ".dark-factory" / "scenarios" / "secret.py"
        secret.write_text("test content")
        # Create a hardlink in source dir.
        hardlink = self.tmp_dir / "src" / "linked.py"
        os.link(str(secret), str(hardlink))
        with self.assertRaises(BarrierViolation):
            check_hardlink_safety(
                self.tmp_dir, ["src"], [".dark-factory/scenarios"],
            )


class TestPlatformIsolationOverlays(unittest.TestCase):
    def test_node_rw_adds_tmpfs(self) -> None:
        args = build_platform_isolation_overlays("node", project_rw=True)
        self.assertIn("--tmpfs", args)
        self.assertTrue(any("node_modules" in a for a in args))

    def test_node_ro_skips_tmpfs(self) -> None:
        args = build_platform_isolation_overlays("node", project_rw=False)
        self.assertEqual(args, [])

    def test_python_rw_adds_tmpfs(self) -> None:
        args = build_platform_isolation_overlays("python", project_rw=True)
        self.assertIn("--tmpfs", args)
        self.assertTrue(any(".venv" in a for a in args))

    def test_python_ro_skips_tmpfs(self) -> None:
        args = build_platform_isolation_overlays("python", project_rw=False)
        self.assertEqual(args, [])

    def test_unknown_language_returns_empty(self) -> None:
        args = build_platform_isolation_overlays("go")
        self.assertEqual(args, [])

    def test_default_project_rw_is_true(self) -> None:
        """Backward compat: omitting project_rw still adds overlays."""
        args = build_platform_isolation_overlays("node")
        self.assertTrue(len(args) > 0)


if __name__ == "__main__":
    unittest.main()
