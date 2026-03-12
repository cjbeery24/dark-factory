"""Tests for git/ops.py — git branch/commit/push/PR operations."""

import unittest
from pathlib import Path
from unittest.mock import patch, call

from darkfactory.git.ops import (
    preflight,
    create_branch,
    commit_changes,
    push_branch,
    create_pr,
    merge_queue_add,
    auto_merge_high_trust,
    push_worktree,
    branch_name,
    BRANCH_RUN_ID_LEN,
)
from darkfactory.errors import ConfigError, DarkFactoryError
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import RunResult


def _ok(stdout: str = "", stderr: str = "") -> RunResult:
    return RunResult(exit_code=0, stdout=stdout, stderr=stderr)


def _fail(stderr: str = "", stdout: str = "") -> RunResult:
    return RunResult(exit_code=1, stdout=stdout, stderr=stderr)


class TestPreflight(unittest.TestCase):
    @patch("darkfactory.git.ops.run")
    def test_not_git_repo(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _fail(stderr="not a git repo")
        config = PipelineConfig()
        with self.assertRaises(DarkFactoryError):
            preflight(config, Path("/tmp/project"))

    @patch("darkfactory.git.ops.run")
    def test_gh_not_authenticated(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.side_effect = [_ok(), _fail(stderr="not logged in")]
        config = PipelineConfig()
        with self.assertRaises(DarkFactoryError):
            preflight(config, Path("/tmp/project"))

    @patch("darkfactory.git.ops.run")
    def test_clean_preflight(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok()
        config = PipelineConfig()
        # Should not raise.
        preflight(config, Path("/tmp/project"))


class TestCreateBranch(unittest.TestCase):
    @patch("darkfactory.git.ops.run")
    def test_branch_name(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok()
        config = PipelineConfig()
        branch = create_branch("my-spec", "abcdef1234567890", config, Path("/tmp/p"))
        self.assertEqual(branch, "dark-factory/my-spec-abcdef12")
        # Verify git checkout -b was called.
        cmd = mock_run.call_args[0][0]
        self.assertIn("-b", cmd)
        self.assertIn("dark-factory/my-spec-abcdef12", cmd)


class TestCommitChanges(unittest.TestCase):
    @patch("darkfactory.git.ops.run")
    def test_nothing_to_commit(self, mock_run: unittest.mock.MagicMock) -> None:
        # git add calls succeed, diff --cached --quiet returns 0 (no changes).
        mock_run.return_value = _ok()
        result = commit_changes("spec", ["src"], "tests", Path("/tmp/p"))
        self.assertFalse(result)

    @patch("darkfactory.git.ops.run")
    def test_commit_success(self, mock_run: unittest.mock.MagicMock) -> None:
        def side_effect(cmd: list[str], **kwargs: object) -> RunResult:
            if "diff" in cmd and "--cached" in cmd:
                return _fail()  # exit 1 = there are staged changes
            return _ok()
        mock_run.side_effect = side_effect
        result = commit_changes("spec", ["src"], "tests", Path("/tmp/p"))
        self.assertTrue(result)

    @patch("darkfactory.git.ops.run")
    def test_skip_hooks(self, mock_run: unittest.mock.MagicMock) -> None:
        def side_effect(cmd: list[str], **kwargs: object) -> RunResult:
            if "diff" in cmd and "--cached" in cmd:
                return _fail()
            return _ok()
        mock_run.side_effect = side_effect
        commit_changes("spec", ["src"], "tests", Path("/tmp/p"), skip_hooks=True)
        # Find the commit call.
        commit_call = [c for c in mock_run.call_args_list if "commit" in c[0][0]]
        self.assertTrue(len(commit_call) > 0)
        self.assertIn("--no-verify", commit_call[0][0][0])


class TestPushBranch(unittest.TestCase):
    @patch("darkfactory.git.ops.run")
    def test_push_success(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok()
        config = PipelineConfig()
        result = push_branch("dark-factory/spec-abc", config, Path("/tmp/p"))
        self.assertTrue(result)

    @patch("darkfactory.git.ops.run")
    def test_push_failure(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _fail()
        config = PipelineConfig()
        result = push_branch("dark-factory/spec-abc", config, Path("/tmp/p"))
        self.assertFalse(result)


class TestCreatePR(unittest.TestCase):
    @patch("darkfactory.git.ops.run")
    def test_existing_pr(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok(stdout="https://github.com/org/repo/pull/42\n")
        config = PipelineConfig()
        url = create_pr("spec", config, Path("/tmp/p"))
        self.assertEqual(url, "https://github.com/org/repo/pull/42")

    @patch("darkfactory.git.ops.run")
    def test_create_new_pr(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.side_effect = [
            _fail(),  # No existing PR.
            _ok(stdout="https://github.com/org/repo/pull/99\n"),
        ]
        config = PipelineConfig()
        url = create_pr("spec", config, Path("/tmp/p"), run_id="run123")
        self.assertEqual(url, "https://github.com/org/repo/pull/99")

    @patch("darkfactory.git.ops.run")
    def test_pr_creation_failure(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _fail(stderr="error creating PR")
        config = PipelineConfig()
        url = create_pr("spec", config, Path("/tmp/p"))
        self.assertEqual(url, "")


class TestBranchName(unittest.TestCase):
    def test_format(self) -> None:
        name = branch_name("my-spec", "abcdef1234567890")
        self.assertEqual(name, f"dark-factory/my-spec-abcdef12")
        self.assertEqual(len("abcdef12"), BRANCH_RUN_ID_LEN)

    def test_validates_spec_name(self) -> None:
        with self.assertRaises(ConfigError):
            create_branch("../escape", "run1", PipelineConfig(), Path("/tmp/p"))

    @patch("darkfactory.git.ops.run")
    def test_rejects_invalid_chars(self, mock_run: unittest.mock.MagicMock) -> None:
        with self.assertRaises(ConfigError):
            create_branch("spec with spaces", "run1", PipelineConfig(), Path("/tmp/p"))


class TestMergeQueue(unittest.TestCase):
    @patch("darkfactory.git.ops.run")
    def test_success(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok()
        result = merge_queue_add(Path("/tmp/p"))
        self.assertTrue(result)

    @patch("darkfactory.git.ops.run")
    def test_failure(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _fail()
        result = merge_queue_add(Path("/tmp/p"))
        self.assertFalse(result)


class TestPushWorktree(unittest.TestCase):
    @patch("darkfactory.git.ops.run")
    def test_success(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok(stdout="dark-factory/spec-abc\n")
        config = PipelineConfig()
        result = push_worktree(Path("/tmp/wt"), config)
        self.assertTrue(result)

    @patch("darkfactory.git.ops.run")
    def test_no_branch(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _fail()
        config = PipelineConfig()
        result = push_worktree(Path("/tmp/wt"), config)
        self.assertFalse(result)


class TestStagePaths(unittest.TestCase):
    @patch("darkfactory.git.ops.run")
    def test_batches_git_add(self, mock_run: unittest.mock.MagicMock) -> None:
        """Verify _stage_paths calls git add once with all paths."""
        import tempfile
        from darkfactory.git.ops import _stage_paths
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "src").mkdir()
            (p / "tests").mkdir()
            (p / "package.json").write_text("{}")
            mock_run.return_value = _ok()
            _stage_paths(p, ["src", "tests", "package.json", "nonexistent"])
            # Should be exactly one git add call.
            self.assertEqual(mock_run.call_count, 1)
            cmd = mock_run.call_args[0][0]
            self.assertIn("add", cmd)
            # Should include existing paths but not nonexistent.
            self.assertIn("src", cmd)
            self.assertIn("package.json", cmd)
            self.assertNotIn("nonexistent", cmd)


class TestAutoMerge(unittest.TestCase):
    @patch("darkfactory.git.ops.run")
    def test_below_threshold(self, mock_run: unittest.mock.MagicMock) -> None:
        result = auto_merge_high_trust(Path("/tmp/p"), 79)
        self.assertFalse(result)
        mock_run.assert_not_called()

    @patch("darkfactory.git.ops.run")
    def test_at_threshold(self, mock_run: unittest.mock.MagicMock) -> None:
        mock_run.return_value = _ok()
        result = auto_merge_high_trust(Path("/tmp/p"), 80)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
