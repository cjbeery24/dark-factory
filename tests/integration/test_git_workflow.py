"""Integration tests for git operations with real repositories.

All tests use tmp_path with git init — no Docker required.
Tests that need gh CLI or remote operations are skipped if gh is unavailable.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from darkfactory.git.detect import detect_toolchain, detect_timeout_cmd
from darkfactory.git.ops import branch_name, commit_changes, count_files_changed, create_branch
from darkfactory.git.worktree import cleanup_all, create, list_worktrees, remove
from darkfactory.models.config import PipelineConfig


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with an initial commit."""
    subprocess.run(
        ["git", "init", "-b", "main", str(tmp_path)],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "test@test.com"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        capture_output=True, check=True,
    )
    # Initial commit (git worktree requires at least one commit)
    (tmp_path / "README.md").write_text("# Test\n")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "README.md"],
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "commit", "-m", "initial"],
        capture_output=True, check=True,
    )
    return tmp_path


@pytest.fixture
def config() -> PipelineConfig:
    return PipelineConfig(
        source_dirs=["src"],
        test_dir="tests",
        git_base_branch="main",
    )


@pytest.mark.integration
class TestBranchName:
    """Branch name computation."""

    def test_format(self):
        name = branch_name("add-auth", "abcdef1234567890")
        assert name == "dark-factory/add-auth-abcdef12"

    def test_truncates_run_id(self):
        name = branch_name("spec", "a" * 32)
        assert name == "dark-factory/spec-aaaaaaaa"

    def test_special_characters_in_spec_name(self):
        # branch_name does NOT validate — it is a pure formatter
        name = branch_name("my spec!@#", "abc12345")
        assert name == "dark-factory/my spec!@#-abc12345"


@pytest.mark.integration
class TestCreateBranch:
    """Branch creation with real git."""

    def test_branch_created(self, git_repo: Path, config: PipelineConfig):
        branch = create_branch("test-spec", "run12345678", config, git_repo)
        assert branch == "dark-factory/test-spec-run12345"

        # Verify HEAD is on the new branch
        result = subprocess.run(
            ["git", "-C", str(git_repo), "branch", "--show-current"],
            capture_output=True, text=True,
        )
        assert result.stdout.strip() == branch

    def test_branch_exists_in_list(self, git_repo: Path, config: PipelineConfig):
        branch = create_branch("feat", "abc12345", config, git_repo)
        result = subprocess.run(
            ["git", "-C", str(git_repo), "branch", "--list"],
            capture_output=True, text=True,
        )
        assert branch in result.stdout

    def test_duplicate_branch_raises(self, git_repo: Path, config: PipelineConfig):
        create_branch("dup-spec", "run12345678", config, git_repo)
        # Switch back to main so we can try creating the same branch again
        subprocess.run(
            ["git", "-C", str(git_repo), "checkout", "main"],
            capture_output=True, check=True,
        )
        with pytest.raises(Exception):
            create_branch("dup-spec", "run12345678", config, git_repo)


@pytest.mark.integration
class TestCommitChanges:
    """Staging and committing with real git."""

    def test_commit_staged_files(self, git_repo: Path):
        src = git_repo / "src"
        src.mkdir()
        (src / "main.py").write_text("def main(): pass\n")

        committed = commit_changes(
            "test-spec", ["src"], "tests", git_repo,
            message="test commit", skip_hooks=True,
        )
        assert committed

        # Verify commit is in log
        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline", "-1"],
            capture_output=True, text=True,
        )
        assert "test commit" in result.stdout

    def test_nothing_to_commit(self, git_repo: Path):
        committed = commit_changes(
            "test-spec", ["src"], "tests", git_repo,
            message="empty", skip_hooks=True,
        )
        assert not committed

    def test_project_files_staged(self, git_repo: Path):
        (git_repo / "pyproject.toml").write_text('[project]\nname = "test"\n')
        src = git_repo / "src"
        src.mkdir()
        (src / "app.py").write_text("x = 1\n")

        commit_changes(
            "test-spec", ["src"], "tests", git_repo,
            message="with project files", skip_hooks=True,
        )

        result = subprocess.run(
            ["git", "-C", str(git_repo), "show", "--name-only", "--format="],
            capture_output=True, text=True,
        )
        assert "pyproject.toml" in result.stdout
        assert "src/app.py" in result.stdout


@pytest.mark.integration
class TestCountFilesChanged:
    """File change counting with real git."""

    def test_counts_new_files(self, git_repo: Path):
        src = git_repo / "src"
        src.mkdir()
        (src / "a.py").write_text("a = 1\n")
        (src / "b.py").write_text("b = 2\n")

        count = count_files_changed(git_repo, ["src"])
        assert count == 2

    def test_counts_modified_files(self, git_repo: Path):
        src = git_repo / "src"
        src.mkdir()
        (src / "a.py").write_text("a = 1\n")
        subprocess.run(
            ["git", "-C", str(git_repo), "add", "src/a.py"],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["git", "-C", str(git_repo), "commit", "-m", "add a"],
            capture_output=True, check=True,
        )
        (src / "a.py").write_text("a = 2\n")

        count = count_files_changed(git_repo, ["src"])
        assert count == 1

    def test_empty_dir_zero_count(self, git_repo: Path):
        count = count_files_changed(git_repo, ["src"])
        assert count == 0


@pytest.mark.integration
class TestWorktree:
    """Git worktree lifecycle with real git."""

    def test_create_worktree(self, git_repo: Path):
        (git_repo / ".dark-factory").mkdir(parents=True, exist_ok=True)
        wt = create(git_repo, "test-spec", "run12345678")
        assert wt.exists()
        assert (wt / "README.md").exists()  # File from initial commit

    def test_worktree_has_branch(self, git_repo: Path):
        (git_repo / ".dark-factory").mkdir(parents=True, exist_ok=True)
        wt = create(git_repo, "test-spec", "run12345678")
        result = subprocess.run(
            ["git", "-C", str(wt), "branch", "--show-current"],
            capture_output=True, text=True,
        )
        assert result.stdout.strip() == "dark-factory/test-spec-run12345"

    def test_worktree_isolation(self, git_repo: Path):
        (git_repo / ".dark-factory").mkdir(parents=True, exist_ok=True)
        wt = create(git_repo, "test-spec", "run12345678")

        # Create file in worktree
        (wt / "new_file.txt").write_text("worktree only\n")

        # File should NOT exist in main repo
        assert not (git_repo / "new_file.txt").exists()

    def test_list_worktrees(self, git_repo: Path):
        (git_repo / ".dark-factory").mkdir(parents=True, exist_ok=True)
        wt = create(git_repo, "test-spec", "run12345678")
        trees = list_worktrees(git_repo)
        assert len(trees) >= 2  # main + worktree
        assert any("test-spec" in t for t in trees)

    def test_remove_worktree(self, git_repo: Path):
        (git_repo / ".dark-factory").mkdir(parents=True, exist_ok=True)
        wt = create(git_repo, "test-spec", "run12345678")
        assert wt.exists()

        removed = remove(git_repo, wt, force=True)
        assert removed
        assert not wt.exists()

    def test_remove_nonexistent_returns_true(self, git_repo: Path):
        wt = git_repo / ".dark-factory" / "worktrees" / "nonexistent"
        assert remove(git_repo, wt)

    def test_cleanup_all(self, git_repo: Path):
        (git_repo / ".dark-factory").mkdir(parents=True, exist_ok=True)
        create(git_repo, "spec-a", "run11111111")
        create(git_repo, "spec-b", "run22222222")

        count = cleanup_all(git_repo, force=True)
        assert count == 2

    def test_copies_config(self, git_repo: Path):
        df = git_repo / ".dark-factory"
        df.mkdir(parents=True, exist_ok=True)
        config_path = df / "config.toml"
        config_path.write_text('[core]\nlanguage = "python"\n')

        wt = create(
            git_repo, "test-spec", "run12345678",
            config_path=config_path,
        )
        assert (wt / ".dark-factory" / "config.toml").exists()

    def test_copies_spec(self, git_repo: Path):
        df = git_repo / ".dark-factory"
        df.mkdir(parents=True, exist_ok=True)
        spec_dir = df / "specs"
        spec_dir.mkdir()
        spec_path = spec_dir / "test-spec.md"
        spec_path.write_text("# Test Spec\n")

        wt = create(
            git_repo, "test-spec", "run12345678",
            spec_path=spec_path,
        )
        assert (wt / ".dark-factory" / "specs" / "test-spec.md").exists()


@pytest.mark.integration
class TestToolchainDetection:
    """Toolchain detection with real filesystem."""

    def test_python(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        assert detect_toolchain(tmp_path) == "python"

    def test_node(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "x"}')
        assert detect_toolchain(tmp_path) == "node"

    def test_go(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/x\n")
        assert detect_toolchain(tmp_path) == "go"

    def test_rust(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text('[package]\nname = "x"\n')
        assert detect_toolchain(tmp_path) == "rust"

    def test_unknown(self, tmp_path: Path):
        assert detect_toolchain(tmp_path) == "unknown"

    def test_priority_node_over_python(self, tmp_path: Path):
        # package.json comes first in detection order
        (tmp_path / "package.json").write_text('{"name": "x"}')
        (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
        assert detect_toolchain(tmp_path) == "node"


@pytest.mark.integration
class TestTimeoutDetection:
    """Timeout command detection."""

    def test_returns_valid_value(self):
        result = detect_timeout_cmd()
        assert result in {"timeout", "gtimeout", ""}
