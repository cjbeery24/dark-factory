"""Tests for git/worktree.py — worktree lifecycle."""

import unittest
from pathlib import Path
from unittest.mock import patch

from darkfactory.errors import ConfigError, DarkFactoryError
from darkfactory.git.worktree import create, remove, list_worktrees, cleanup_all
from darkfactory.utils.subprocess_runner import RunResult

from tests.helpers import TempDirTestCase


def _ok(stdout: str = "", stderr: str = "") -> RunResult:
    return RunResult(exit_code=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "", stdout: str = "") -> RunResult:
    return RunResult(exit_code=1, stdout=stdout, stderr=stderr)


class TestCreate(TempDirTestCase):
    @patch("darkfactory.git.worktree.run")
    def test_creates_worktree(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok()
        run_id = "abcdef1234567890extra"
        path = create(self.tmp_dir, "my-spec", run_id)
        # Verify branch name uses first 8 chars of run_id (shared constant).
        cmd = mock_run.call_args[0][0]
        self.assertIn("-b", cmd)
        branch_idx = cmd.index("-b") + 1
        self.assertEqual(cmd[branch_idx], "dark-factory/my-spec-abcdef12")
        # Verify worktree path uses first 8 chars of run_id.
        self.assertIn("my-spec-abcdef12", str(path))

    @patch("darkfactory.git.worktree.run")
    def test_create_failure(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _fail(stderr="branch already exists")
        with self.assertRaises(DarkFactoryError):
            create(self.tmp_dir, "spec", "run123")

    def test_validates_spec_name(self) -> None:
        with self.assertRaises(ConfigError):
            create(self.tmp_dir, "../escape", "run1")
        with self.assertRaises(ConfigError):
            create(self.tmp_dir, "bad spec", "run1")

    @patch("darkfactory.git.worktree.run")
    def test_copies_config(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok()
        config = self.tmp_dir / "config.toml"
        config.write_text("[core]\nlanguage = 'node'\n")
        # Create worktree dir so copy has a destination.
        wt_dir = self.tmp_dir / ".dark-factory" / "worktrees" / "spec-run12345"
        wt_dir.mkdir(parents=True)
        path = create(self.tmp_dir, "spec", "run1234567890", config_path=config)
        dest = path / ".dark-factory" / config.name
        self.assertTrue(dest.exists())

    @patch("darkfactory.git.worktree.run")
    def test_copies_spec(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok()
        spec_file = self.tmp_dir / "my-spec.md"
        spec_file.write_text("# Spec\n")
        # run_id[:8] = "run12345"
        wt_dir = self.tmp_dir / ".dark-factory" / "worktrees" / "spec-run12345"
        wt_dir.mkdir(parents=True)
        path = create(self.tmp_dir, "spec", "run1234567890", spec_path=spec_file)
        dest = path / ".dark-factory" / "specs" / spec_file.name
        self.assertTrue(dest.exists())


class TestRemove(unittest.TestCase):
    def test_nonexistent_path(self) -> None:
        result = remove(Path("/tmp"), Path("/tmp/does-not-exist"))
        self.assertTrue(result)

    @patch("darkfactory.git.worktree.run")
    def test_uncommitted_changes(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok(stdout=" M file.py\n")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            wt = Path(d) / "wt"
            wt.mkdir()
            result = remove(Path(d), wt)
            self.assertFalse(result)

    @patch("darkfactory.git.worktree.run")
    def test_force_skips_checks(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok(stdout="main\n")
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            wt = Path(d) / "wt"
            wt.mkdir()
            result = remove(Path(d), wt, force=True)
            self.assertTrue(result)
            # Should not have called status --porcelain (safety check skipped).
            cmds = [c[0][0] for c in mock_run.call_args_list]
            porcelain_calls = [c for c in cmds if "--porcelain" in c]
            self.assertEqual(len(porcelain_calls), 0)


class TestListWorktrees(unittest.TestCase):
    @patch("darkfactory.git.worktree.run")
    def test_parses_porcelain(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok(
            stdout=(
                "worktree /home/user/project\n"
                "HEAD abc123\n"
                "branch refs/heads/main\n"
                "\n"
                "worktree /home/user/project/.dark-factory/worktrees/spec-abc\n"
                "HEAD def456\n"
                "branch refs/heads/dark-factory/spec-abc\n"
                "\n"
            )
        )
        paths = list_worktrees(Path("/home/user/project"))
        self.assertEqual(len(paths), 2)
        self.assertEqual(paths[0], "/home/user/project")
        self.assertIn("spec-abc", paths[1])

    @patch("darkfactory.git.worktree.run")
    def test_failure(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _fail()
        paths = list_worktrees(Path("/tmp/project"))
        self.assertEqual(paths, [])


class TestCleanupAll(unittest.TestCase):
    @patch("darkfactory.git.worktree.remove")
    @patch("darkfactory.git.worktree.list_worktrees")
    def test_only_dark_factory_worktrees(
        self,
        mock_list: unittest.mock.MagicMock,
        mock_remove: unittest.mock.MagicMock,
    ) -> None:
        mock_list.return_value = [
            "/project",
            "/project/.dark-factory/worktrees/spec-abc",
            "/other/worktree",
        ]
        mock_remove.return_value = True
        count = cleanup_all(Path("/project"))
        # Only the dark-factory worktree should be removed.
        self.assertEqual(count, 1)
        mock_remove.assert_called_once()


if __name__ == "__main__":
    unittest.main()
