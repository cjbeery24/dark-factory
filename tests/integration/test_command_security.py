"""Integration tests for command validation and execution security.

Target: darkfactory/deployment/command.py — validate_command(), run_command()
Mock boundary: Minimal. Real filesystem with tmp_path, real subprocess execution.
Why integration: Tests real filesystem interactions (symlinks, permissions, subprocess exec).
"""

from __future__ import annotations

import os
import subprocess
import threading
from pathlib import Path

import pytest

from darkfactory.deployment.command import run_command, validate_command


@pytest.mark.integration
class TestPathTraversalRejection:
    """validate_command rejects paths that escape the project directory."""

    def test_relative_traversal(self, tmp_path: Path):
        """../../etc/passwd style traversal is rejected."""
        script = tmp_path / "run.sh"
        script.write_text("#!/bin/sh\necho ok\n")
        os.chmod(script, 0o755)

        with pytest.raises(ValueError, match="outside project directory"):
            validate_command("../../etc/passwd", str(tmp_path / "sub"), "test")

    def test_absolute_path_outside_project(self, tmp_path: Path):
        """Absolute path outside project dir is rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        script = outside / "evil.sh"
        script.write_text("#!/bin/sh\necho pwned\n")
        os.chmod(script, 0o755)

        project = tmp_path / "project"
        project.mkdir()

        with pytest.raises(ValueError, match="outside project directory"):
            validate_command(str(script), str(project), "test")

    def test_symlink_escaping_project(self, tmp_path: Path):
        """Symlink pointing outside project is caught by .resolve()."""
        outside = tmp_path / "outside"
        outside.mkdir()
        target_script = outside / "real.sh"
        target_script.write_text("#!/bin/sh\necho pwned\n")
        os.chmod(target_script, 0o755)

        project = tmp_path / "project"
        project.mkdir()
        link = project / "link.sh"
        link.symlink_to(target_script)

        with pytest.raises(ValueError, match="outside project directory"):
            validate_command("link.sh", str(project), "test")


@pytest.mark.integration
class TestValidCommandExecution:
    """run_command executes valid scripts successfully."""

    def test_script_executes_successfully(self, tmp_path: Path):
        """Script in project dir executes and returns output."""
        script = tmp_path / "hello.sh"
        script.write_text("#!/bin/sh\necho hello world\n")
        os.chmod(script, 0o755)

        result = run_command("hello.sh", str(tmp_path), capture=True)
        assert result.returncode == 0
        assert "hello world" in result.stdout

    def test_script_with_args(self, tmp_path: Path):
        """Arguments are passed through to the script."""
        script = tmp_path / "echo_args.sh"
        script.write_text('#!/bin/sh\necho "$1 $2"\n')
        os.chmod(script, 0o755)

        result = run_command("echo_args.sh foo bar", str(tmp_path), capture=True)
        assert result.returncode == 0
        assert "foo bar" in result.stdout

    def test_correct_working_directory(self, tmp_path: Path):
        """Script runs with cwd set to project_dir."""
        script = tmp_path / "pwd.sh"
        script.write_text("#!/bin/sh\npwd\n")
        os.chmod(script, 0o755)

        result = run_command("pwd.sh", str(tmp_path), capture=True)
        assert result.returncode == 0
        # Use .resolve() on both sides for macOS /tmp -> /private/tmp
        assert Path(result.stdout.strip()).resolve() == tmp_path.resolve()


@pytest.mark.integration
class TestTimeoutEnforcement:
    """run_command kills long-running scripts after timeout."""

    def test_timeout_kills_script(self, tmp_path: Path):
        """Script sleeping 10s is killed after 2s timeout."""
        script = tmp_path / "slow.sh"
        script.write_text("#!/bin/sh\nsleep 10\n")
        os.chmod(script, 0o755)

        with pytest.raises(subprocess.TimeoutExpired):
            run_command("slow.sh", str(tmp_path), timeout=2, capture=True)


@pytest.mark.integration
class TestEnvironmentIsolation:
    """run_command passes env vars and isolates between calls."""

    def test_env_vars_passed(self, tmp_path: Path):
        """Environment variables are available inside the script."""
        script = tmp_path / "env.sh"
        script.write_text('#!/bin/sh\necho "$MY_VAR"\n')
        os.chmod(script, 0o755)

        result = run_command(
            "env.sh", str(tmp_path),
            env={"MY_VAR": "secret123"},
            capture=True,
        )
        assert result.returncode == 0
        assert "secret123" in result.stdout

    def test_env_does_not_leak_between_calls(self, tmp_path: Path):
        """Env from one call does not leak to subsequent calls."""
        # Use a UUID-prefixed name to avoid collision with real env vars
        var_name = "DF_LEAK_TEST_8f3a2b1c"
        script = tmp_path / "check_env.sh"
        script.write_text(f'#!/bin/sh\necho "val=${{{var_name}:-unset}}"\n')
        os.chmod(script, 0o755)

        # First call sets the var
        run_command(
            "check_env.sh", str(tmp_path),
            env={var_name: "leaked"},
            capture=True,
        )

        # Second call without the env var — must be unset
        result = run_command("check_env.sh", str(tmp_path), capture=True)
        assert "val=unset" in result.stdout


@pytest.mark.integration
class TestSymlinkAttack:
    """Symlink-based escape attempts are rejected."""

    def test_symlink_to_outside_rejected(self, tmp_path: Path):
        """Symlink to script outside project is rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        real = outside / "script.sh"
        real.write_text("#!/bin/sh\necho pwned\n")
        os.chmod(real, 0o755)

        project = tmp_path / "project"
        project.mkdir()
        link = project / "script.sh"
        link.symlink_to(real)

        with pytest.raises(ValueError, match="outside project directory"):
            validate_command("script.sh", str(project), "test")

    def test_chained_symlinks_resolved_and_rejected(self, tmp_path: Path):
        """Chain of symlinks ultimately pointing outside project is rejected."""
        outside = tmp_path / "outside"
        outside.mkdir()
        real = outside / "real.sh"
        real.write_text("#!/bin/sh\necho pwned\n")
        os.chmod(real, 0o755)

        project = tmp_path / "project"
        project.mkdir()
        # Chain: link1 -> link2 -> real (outside)
        link2 = project / "link2.sh"
        link2.symlink_to(real)
        link1 = project / "link1.sh"
        link1.symlink_to(link2)

        with pytest.raises(ValueError, match="outside project directory"):
            validate_command("link1.sh", str(project), "test")


@pytest.mark.integration
@pytest.mark.slow
class TestConcurrentExecution:
    """Concurrent run_command calls maintain environment isolation."""

    def test_parallel_env_isolation(self, tmp_path: Path):
        """Two run_command calls in threads see their own env vars."""
        script = tmp_path / "whoami.sh"
        script.write_text('#!/bin/sh\necho "$THREAD_ID"\n')
        os.chmod(script, 0o755)

        results: dict[str, str] = {}
        errors: list[Exception] = []

        def run_with_id(thread_id: str):
            try:
                r = run_command(
                    "whoami.sh", str(tmp_path),
                    env={"THREAD_ID": thread_id},
                    capture=True,
                )
                results[thread_id] = r.stdout.strip()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=run_with_id, args=("thread-1",))
        t2 = threading.Thread(target=run_with_id, args=("thread-2",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"Thread errors: {errors}"
        assert results.get("thread-1") == "thread-1"
        assert results.get("thread-2") == "thread-2"
