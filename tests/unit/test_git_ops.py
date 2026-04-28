"""Tests for git/ops.py — git branch/commit/push/PR operations."""

import unittest
from pathlib import Path
from unittest.mock import patch

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
    capture_iteration_diff,
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
        self.assertEqual(name, "dark-factory/my-spec-abcdef12")
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


class TestCaptureIterationDiff(unittest.TestCase):
    """Tests for capture_iteration_diff — per-iteration patch archival.

    Closes the "archive captures change-counts not change-contents"
    framework gap (Gap 3 of the 2026-04-28 deferred-bugs queue): without
    a per-iteration patch, post-mortem cannot reason about iter-1 vs
    iter-2 implementer intent.
    """

    @patch("darkfactory.git.ops.run")
    def test_writes_patch_file(self, mock_run: unittest.mock.MagicMock) -> None:
        import tempfile
        diff_text = "diff --git a/src/foo.py b/src/foo.py\n+added line\n"
        mock_run.return_value = _ok(stdout=diff_text)
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "iter-1" / "implementer-changes.patch"
            ok = capture_iteration_diff(
                Path(d), "abc123", "def456",
                ["src"], "tests", out,
            )
            self.assertTrue(ok)
            self.assertTrue(out.exists())
            self.assertEqual(out.read_text(), diff_text)

    @patch("darkfactory.git.ops.run")
    def test_returns_false_on_missing_shas(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        out = Path("/tmp/should-not-exist.patch")
        self.assertFalse(
            capture_iteration_diff(Path("/tmp"), "", "def", ["src"], "tests", out),
        )
        self.assertFalse(
            capture_iteration_diff(Path("/tmp"), "abc", "", ["src"], "tests", out),
        )
        # No git invocation when SHAs are absent — the helper short-circuits.
        mock_run.assert_not_called()

    @patch("darkfactory.git.ops.run")
    def test_returns_false_on_git_failure(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        import tempfile
        mock_run.return_value = _fail(stderr="bad object")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "iter-1" / "implementer-changes.patch"
            ok = capture_iteration_diff(
                Path(d), "abc", "def", ["src"], "tests", out,
            )
            self.assertFalse(ok)
            self.assertFalse(out.exists())

    @patch("darkfactory.git.ops.run")
    def test_empty_diff_still_writes_file(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Empty diff = "implementer made no scoped changes". That's a
        signal worth preserving (B1 idle-iteration analysis depends on
        it), so the file MUST be written even when the diff is empty."""
        import tempfile
        mock_run.return_value = _ok(stdout="")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "iter-1" / "implementer-changes.patch"
            ok = capture_iteration_diff(
                Path(d), "abc", "def", ["src"], "tests", out,
            )
            self.assertTrue(ok)
            self.assertTrue(out.exists())
            self.assertEqual(out.read_text(), "")

    @patch("darkfactory.git.ops.run")
    def test_pathspec_includes_source_test_manifests_excludes_lockfiles_and_dot_dark_factory(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Verify the git diff command line carries the expected
        include + exclude pathspecs. This is the architectural contract
        — lockfiles balloon the patch, .dark-factory mirrors the archive
        itself, so excluding them is what keeps grep workflows usable."""
        import tempfile
        mock_run.return_value = _ok(stdout="diff --git a/src/foo.py b/src/foo.py\n")
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "p.patch"
            capture_iteration_diff(
                Path(d), "abc", "def",
                ["src", "lib"], "tests", out,
            )
            cmd = mock_run.call_args[0][0]
            # Source/test/manifest includes.
            self.assertIn("src", cmd)
            self.assertIn("lib", cmd)
            self.assertIn("tests", cmd)
            self.assertIn("package.json", cmd)
            self.assertIn("pyproject.toml", cmd)
            # Lockfile excludes — glob form so monorepo nested
            # lockfiles (e.g. frontend/package-lock.json) are also
            # excluded. The literal :(exclude)package-lock.json form
            # would only match the repo-root file.
            self.assertIn(":(exclude,glob)**/package-lock.json", cmd)
            self.assertIn(":(exclude,glob)**/uv.lock", cmd)
            # Build artifacts + .dark-factory tree excluded.
            self.assertIn(":(exclude,glob)**/dist/**", cmd)
            self.assertIn(":(exclude,glob).dark-factory/**", cmd)
            # SHA range form (a..b) — never a single sha which would
            # diff against working tree and conflate untracked changes.
            self.assertIn("abc..def", cmd)

    @patch("darkfactory.git.ops.run")
    def test_truncates_above_one_megabyte(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Above 1 MB the full diff must be replaced by --stat + head/tail
        with a truncation marker. Disk and grep workflows both benefit."""
        import tempfile
        # Build a > 1 MB diff: 2000 lines * 600 chars/line ~ 1.2 MB.
        # Lots of distinct lines so head/tail don't overlap.
        big_lines = [
            f"+{'x' * 600} line_{i}" for i in range(2000)
        ]
        big_diff = "diff --git a/src/foo.py b/src/foo.py\n" + "\n".join(big_lines)
        # Two responses: first the full diff, second the --stat call.
        mock_run.side_effect = [
            _ok(stdout=big_diff),
            _ok(stdout=" src/foo.py | 2000 ++++\n 1 file changed\n"),
        ]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "iter-1" / "implementer-changes.patch"
            ok = capture_iteration_diff(
                Path(d), "abc", "def", ["src"], "tests", out,
            )
            self.assertTrue(ok)
            content = out.read_text()
            # Truncation marker present.
            self.assertIn("Diff exceeded", content)
            self.assertIn("Truncated to --stat + head/tail.", content)
            # --stat block included.
            self.assertIn("--stat", content)
            self.assertIn("src/foo.py", content)
            # Body smaller than original.
            self.assertLess(len(content), len(big_diff))
            # Both head and tail markers present.
            self.assertIn("First 500 lines", content)
            self.assertIn("Last 500 lines", content)
            self.assertIn("lines truncated", content)


if __name__ == "__main__":
    unittest.main()
