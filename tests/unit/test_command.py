"""Tests for darkfactory.deployment.command — safe command validation + execution."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from darkfactory.deployment.command import validate_command, run_command


class TestValidateCommand(unittest.TestCase):
    """Test validate_command path validation and safety checks."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        # Create a valid script file.
        self.script = Path(self.tmpdir) / "deploy.sh"
        self.script.write_text("#!/bin/bash\necho ok\n")
        self.script.chmod(self.script.stat().st_mode | stat.S_IEXEC)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_valid_script_path(self):
        result = validate_command("deploy.sh", self.tmpdir, "deploy_cmd")
        self.assertEqual(result, self.script.resolve())

    def test_valid_absolute_path(self):
        result = validate_command(str(self.script), self.tmpdir, "deploy_cmd")
        self.assertEqual(result, self.script.resolve())

    def test_script_with_args(self):
        result = validate_command("deploy.sh --env staging", self.tmpdir, "deploy_cmd")
        self.assertEqual(result, self.script.resolve())

    def test_rejects_shell_metacharacters_semicolon(self):
        with self.assertRaises(ValueError) as ctx:
            validate_command("echo hello; rm -rf /", self.tmpdir, "deploy_cmd")
        self.assertIn("shell metacharacters", str(ctx.exception))

    def test_rejects_shell_metacharacters_pipe(self):
        with self.assertRaises(ValueError) as ctx:
            validate_command("cat file | grep foo", self.tmpdir, "deploy_cmd")
        self.assertIn("shell metacharacters", str(ctx.exception))

    def test_rejects_shell_metacharacters_dollar(self):
        with self.assertRaises(ValueError) as ctx:
            validate_command("echo $HOME", self.tmpdir, "deploy_cmd")
        self.assertIn("shell metacharacters", str(ctx.exception))

    def test_rejects_shell_metacharacters_backtick(self):
        with self.assertRaises(ValueError) as ctx:
            validate_command("echo `whoami`", self.tmpdir, "deploy_cmd")
        self.assertIn("shell metacharacters", str(ctx.exception))

    def test_rejects_empty_command(self):
        with self.assertRaises(ValueError) as ctx:
            validate_command("", self.tmpdir, "deploy_cmd")
        self.assertIn("empty", str(ctx.exception))

    def test_rejects_nonexistent_script(self):
        with self.assertRaises(ValueError) as ctx:
            validate_command("nonexistent.sh", self.tmpdir, "deploy_cmd")
        self.assertIn("not found", str(ctx.exception))

    def test_rejects_non_executable(self):
        non_exec = Path(self.tmpdir) / "noexec.sh"
        non_exec.write_text("#!/bin/bash\n")
        non_exec.chmod(0o644)
        with self.assertRaises(ValueError) as ctx:
            validate_command("noexec.sh", self.tmpdir, "deploy_cmd")
        self.assertIn("not executable", str(ctx.exception))

    def test_rejects_directory(self):
        subdir = Path(self.tmpdir) / "subdir"
        subdir.mkdir()
        with self.assertRaises(ValueError) as ctx:
            validate_command("subdir", self.tmpdir, "deploy_cmd")
        self.assertIn("not a regular file", str(ctx.exception))

    def test_rejects_outside_project_dir(self):
        outside = tempfile.mkdtemp()
        outside_script = Path(outside) / "evil.sh"
        outside_script.write_text("#!/bin/bash\n")
        outside_script.chmod(0o755)
        try:
            with self.assertRaises(ValueError) as ctx:
                validate_command(str(outside_script), self.tmpdir, "deploy_cmd")
            self.assertIn("outside project directory", str(ctx.exception))
        finally:
            import shutil
            shutil.rmtree(outside)


class TestRunCommand(unittest.TestCase):
    """Test run_command execution."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.script = Path(self.tmpdir) / "test.sh"
        self.script.write_text("#!/bin/bash\necho hello\n")
        self.script.chmod(self.script.stat().st_mode | stat.S_IEXEC)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir)

    def test_run_captures_output(self):
        result = run_command(str(self.script), self.tmpdir, capture=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)

    def test_run_with_env(self):
        env_script = Path(self.tmpdir) / "env.sh"
        env_script.write_text("#!/bin/bash\necho $MY_VAR\n")
        env_script.chmod(0o755)
        result = run_command(
            str(env_script), self.tmpdir,
            env={"MY_VAR": "test_value"}, capture=True,
        )
        self.assertEqual(result.returncode, 0)
        self.assertIn("test_value", result.stdout)

    def test_run_timeout(self):
        slow_script = Path(self.tmpdir) / "slow.sh"
        slow_script.write_text("#!/bin/bash\nsleep 30\n")
        slow_script.chmod(0o755)
        import subprocess
        with self.assertRaises(subprocess.TimeoutExpired):
            run_command(str(slow_script), self.tmpdir, timeout=1)

    def test_run_relative_path(self):
        result = run_command("test.sh", self.tmpdir, capture=True)
        self.assertEqual(result.returncode, 0)
        self.assertIn("hello", result.stdout)


if __name__ == "__main__":
    unittest.main()
