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
        itself, so excluding them is what keeps grep workflows usable.

        Phase 1a also added a parallel ls-files call for untracked files
        — assert the same pathspecs flow through both call sites so the
        include/exclude semantics are identical for tracked and
        untracked file capture."""
        import tempfile
        mock_run.return_value = _ok(stdout="")  # both diff and ls-files empty
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "p.patch"
            capture_iteration_diff(
                Path(d), "abc", "def",
                ["src", "lib"], "tests", out,
            )
            # Inspect BOTH calls: diff (first) and ls-files (second).
            self.assertEqual(mock_run.call_count, 2)
            diff_cmd = mock_run.call_args_list[0][0][0]
            ls_cmd = mock_run.call_args_list[1][0][0]

            # Diff call: SHA range, includes, excludes.
            self.assertIn("diff", diff_cmd)
            self.assertIn("abc..def", diff_cmd)
            self.assertIn("src", diff_cmd)
            self.assertIn("lib", diff_cmd)
            self.assertIn("tests", diff_cmd)
            self.assertIn("package.json", diff_cmd)
            self.assertIn("pyproject.toml", diff_cmd)
            # Lockfile excludes — glob form so monorepo nested
            # lockfiles (e.g. frontend/package-lock.json) are also
            # excluded. The literal :(exclude)package-lock.json form
            # would only match the repo-root file.
            self.assertIn(":(exclude,glob)**/package-lock.json", diff_cmd)
            self.assertIn(":(exclude,glob)**/uv.lock", diff_cmd)
            # Build artifacts + .dark-factory tree excluded.
            self.assertIn(":(exclude,glob)**/dist/**", diff_cmd)
            self.assertIn(":(exclude,glob).dark-factory/**", diff_cmd)

            # ls-files call: same include/exclude pathspecs.
            self.assertIn("ls-files", ls_cmd)
            self.assertIn("--others", ls_cmd)
            self.assertIn("--exclude-standard", ls_cmd)
            self.assertIn("src", ls_cmd)
            self.assertIn("lib", ls_cmd)
            self.assertIn(":(exclude,glob)**/package-lock.json", ls_cmd)
            self.assertIn(":(exclude,glob).dark-factory/**", ls_cmd)

    @patch("darkfactory.git.ops.run")
    def test_untracked_files_appended_as_synthetic_blocks(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Phase 1a (Gap 3 follow-up): untracked files appear in the
        captured patch as synthetic ``diff --git /dev/null b/<path>``
        blocks. Closes the git 2.50.1 stash-create-u skew that caused
        the 13:50 run's iter-1 patch to omit the entire db/ tree."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            # Create real untracked files for the helper to read.
            (project_dir / "src").mkdir()
            (project_dir / "src" / "new.py").write_text("def new():\n    pass\n")
            (project_dir / "src" / "another.py").write_text("x = 1\n")

            # First call: git diff (tracked-only) returns small diff.
            # Second call: git ls-files --others returns the two untracked files.
            mock_run.side_effect = [
                _ok(stdout="diff --git a/src/old.py b/src/old.py\n+modified\n"),
                _ok(stdout="src/new.py\nsrc/another.py\n"),
            ]
            out = project_dir / "iter-1.patch"
            ok = capture_iteration_diff(
                project_dir, "abc", "def", ["src"], "tests", out,
            )
            self.assertTrue(ok)
            content = out.read_text()
            # Tracked diff present.
            self.assertIn("src/old.py", content)
            # Untracked files appended as synthetic blocks.
            self.assertIn("diff --git a/src/new.py b/src/new.py", content)
            self.assertIn("new file mode 100644", content)
            self.assertIn("--- /dev/null", content)
            self.assertIn("+++ b/src/new.py", content)
            self.assertIn("+def new():", content)
            self.assertIn("+    pass", content)
            self.assertIn("+++ b/src/another.py", content)
            self.assertIn("+x = 1", content)

    @patch("darkfactory.git.ops.run")
    def test_untracked_binary_file_emits_binary_marker(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Binary heuristic: first 8 KB contains NUL → emit
        ``Binary files differ`` instead of attempting to read content
        as text. Matches git's default behavior for untracked binaries
        diffed against /dev/null."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            (project_dir / "src").mkdir()
            # Synthetic binary: header + NUL byte.
            (project_dir / "src" / "asset.bin").write_bytes(
                b"\x89PNG\x00binary content here"
            )
            mock_run.side_effect = [
                _ok(stdout=""),  # no tracked diff
                _ok(stdout="src/asset.bin\n"),  # untracked binary
            ]
            out = project_dir / "iter-1.patch"
            ok = capture_iteration_diff(
                project_dir, "abc", "def", ["src"], "tests", out,
            )
            self.assertTrue(ok)
            content = out.read_text()
            self.assertIn("diff --git a/src/asset.bin b/src/asset.bin", content)
            self.assertIn(
                "Binary files /dev/null and b/src/asset.bin differ",
                content,
            )
            # MUST NOT have attempted to inline the binary content.
            self.assertNotIn("\x00", content)
            self.assertNotIn("\x89", content)

    @patch("darkfactory.git.ops.run")
    def test_untracked_files_excluded_per_pathspec(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """The ls-files call inherits the same exclude pathspec list
        as git diff, so lockfiles, .dark-factory tree, dist/ etc. are
        excluded from untracked capture too. (Verified at the cmdline-
        construction level — git's exclude pathspec semantics are
        out-of-process correctness.)"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            (project_dir / "src").mkdir()
            (project_dir / "src" / "ok.py").write_text("ok\n")
            mock_run.side_effect = [
                _ok(stdout=""),
                _ok(stdout="src/ok.py\n"),
            ]
            out = project_dir / "iter-1.patch"
            capture_iteration_diff(
                project_dir, "abc", "def", ["src"], "tests", out,
            )
            # Last call is ls-files — verify it carries the exclude pathspecs.
            ls_cmd = mock_run.call_args_list[-1][0][0]
            self.assertIn("ls-files", ls_cmd)
            self.assertIn("--others", ls_cmd)
            self.assertIn("--exclude-standard", ls_cmd)
            self.assertIn(":(exclude,glob)**/package-lock.json", ls_cmd)
            self.assertIn(":(exclude,glob).dark-factory/**", ls_cmd)
            self.assertIn(":(exclude,glob)**/dist/**", ls_cmd)

    @patch("darkfactory.git.ops.run")
    def test_untracked_file_above_per_file_cap_truncated(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Per-file 200 KB cap: oversized untracked text file is
        truncated with a marker. Prevents a single generated baseline
        or vendored asset from blowing past the global 1 MB cap."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            (project_dir / "src").mkdir()
            # Write a 250 KB text file (above 200 KB cap, no NUL bytes).
            big = "x" * 250_000
            (project_dir / "src" / "big.py").write_text(big)
            mock_run.side_effect = [
                _ok(stdout=""),
                _ok(stdout="src/big.py\n"),
            ]
            out = project_dir / "iter-1.patch"
            capture_iteration_diff(
                project_dir, "abc", "def", ["src"], "tests", out,
            )
            content = out.read_text()
            self.assertIn("diff --git a/src/big.py b/src/big.py", content)
            self.assertIn("[truncated;", content)
            self.assertIn("250000 bytes total", content)
            # Body present but shorter than full file.
            self.assertLess(len(content), 250_000 + 1000)

    @patch("darkfactory.git.ops.run")
    def test_untracked_file_no_trailing_newline_marker(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Linus B1 fix: files without trailing newline must emit
        git's canonical ``\\ No newline at end of file`` marker.
        Without this, ``git apply --check`` round-trips fail on
        no-newline files because the synthesized hunk's line-count
        accounting silently disagrees with reality."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            (project_dir / "src").mkdir()
            # Write file without trailing newline.
            (project_dir / "src" / "no_nl.py").write_bytes(b"line1\nline2")
            mock_run.side_effect = [
                _ok(stdout=""),
                _ok(stdout="src/no_nl.py\n"),
            ]
            out = project_dir / "iter-1.patch"
            capture_iteration_diff(
                project_dir, "abc", "def", ["src"], "tests", out,
            )
            content = out.read_text()
            self.assertIn("\\ No newline at end of file", content)
            self.assertIn("+line1", content)
            self.assertIn("+line2", content)

    @patch("darkfactory.git.ops.run")
    def test_untracked_file_with_trailing_newline_no_marker(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Files WITH trailing newline must NOT carry the no-newline
        marker. Pinned alongside the B1 fix to prevent over-correction."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            (project_dir / "src").mkdir()
            (project_dir / "src" / "nl.py").write_bytes(b"line1\nline2\n")
            mock_run.side_effect = [
                _ok(stdout=""),
                _ok(stdout="src/nl.py\n"),
            ]
            out = project_dir / "iter-1.patch"
            capture_iteration_diff(
                project_dir, "abc", "def", ["src"], "tests", out,
            )
            content = out.read_text()
            self.assertNotIn("\\ No newline at end of file", content)

    @patch("darkfactory.git.ops.run")
    def test_untracked_empty_file_emits_header_only(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Linus S1 fix: empty new files emit header-only blocks (no
        ``@@`` hunk), matching git's actual output. The pre-fix code
        emitted ``@@ -0,0 +0,0 @@`` which is parseable but non-
        canonical."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            (project_dir / "src").mkdir()
            (project_dir / "src" / "empty.py").write_text("")
            mock_run.side_effect = [
                _ok(stdout=""),
                _ok(stdout="src/empty.py\n"),
            ]
            out = project_dir / "iter-1.patch"
            capture_iteration_diff(
                project_dir, "abc", "def", ["src"], "tests", out,
            )
            content = out.read_text()
            self.assertIn("diff --git a/src/empty.py b/src/empty.py", content)
            self.assertIn("new file mode 100644", content)
            self.assertIn("+++ b/src/empty.py", content)
            # No @@ hunk for empty files.
            self.assertNotIn("@@", content)

    @patch("darkfactory.git.ops.run")
    def test_untracked_symlink_skipped_with_warning(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Linus S2 fix: untracked symlinks are skipped (real git emits
        mode 120000 with the link target as content; we don't synthesize
        that, so emitting mode 100644 + target content would lie about
        both the mode and the source). The skip is logged at warning
        level so post-mortem readers see it."""
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            (project_dir / "src").mkdir()
            # Create a symlink target + the symlink itself.
            (project_dir / "src" / "real.py").write_text("real content\n")
            try:
                os.symlink("real.py", project_dir / "src" / "link.py")
            except (OSError, NotImplementedError):
                self.skipTest("symlink creation not supported on this platform")

            mock_run.side_effect = [
                _ok(stdout=""),
                _ok(stdout="src/real.py\nsrc/link.py\n"),
            ]
            out = project_dir / "iter-1.patch"
            capture_iteration_diff(
                project_dir, "abc", "def", ["src"], "tests", out,
            )
            content = out.read_text()
            # Real file: present.
            self.assertIn("diff --git a/src/real.py b/src/real.py", content)
            self.assertIn("+real content", content)
            # Symlink: NOT present (skipped).
            self.assertNotIn("diff --git a/src/link.py b/src/link.py", content)

    @patch("darkfactory.git.ops.run")
    def test_untracked_single_read_pattern_no_double_open(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """Linus B2 fix: untracked file is read exactly once into bytes,
        then sniffed for NUL byte from the same buffer. The pre-fix code
        opened the file twice (8 KB sniff + full read), creating a stat-
        race window where a file mutating mid-read could produce wrong
        binary classification or wrong truncation accounting.

        We can't easily count opens against the real filesystem in a
        unit test (no stat-injection point), but we can verify that
        the byte-level read-once invariant holds on a normal file by
        asserting the binary heuristic uses the same buffer as the
        decode (no separate sniff/full mismatch)."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            (project_dir / "src").mkdir()
            # File with NUL in the first 8 KB → must be classified
            # binary even though the rest is text.
            content = b"\x00 binary header" + b"text padding\n" * 1000
            (project_dir / "src" / "mixed.bin").write_bytes(content)
            mock_run.side_effect = [
                _ok(stdout=""),
                _ok(stdout="src/mixed.bin\n"),
            ]
            out = project_dir / "iter-1.patch"
            capture_iteration_diff(
                project_dir, "abc", "def", ["src"], "tests", out,
            )
            patch_content = out.read_text()
            # Binary classified correctly — header + binary marker, no
            # text content leaks.
            self.assertIn(
                "Binary files /dev/null and b/src/mixed.bin differ",
                patch_content,
            )
            self.assertNotIn("text padding", patch_content)
            self.assertNotIn("\x00", patch_content)

    @patch("darkfactory.git.ops.run")
    def test_untracked_ls_files_failure_does_not_break_capture(
        self, mock_run: unittest.mock.MagicMock,
    ) -> None:
        """ls-files failing must not break the patch capture — the
        tracked-file diff still ships. Best-effort by design."""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            project_dir = Path(d)
            mock_run.side_effect = [
                _ok(stdout="diff --git a/src/x.py b/src/x.py\n+ok\n"),
                _fail(stderr="ls-files unavailable for some reason"),
            ]
            out = project_dir / "iter-1.patch"
            ok = capture_iteration_diff(
                project_dir, "abc", "def", ["src"], "tests", out,
            )
            self.assertTrue(ok)
            content = out.read_text()
            # Tracked diff still in the patch.
            self.assertIn("src/x.py", content)
            # No synthetic blocks added (ls-files failed).
            self.assertNotIn("/dev/null", content)

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
        # Three responses: first the full diff, second the ls-files
        # untracked-capture call (Phase 1a — empty result), third the
        # --stat call inside truncation.
        mock_run.side_effect = [
            _ok(stdout=big_diff),
            _ok(stdout=""),  # no untracked files to append
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
