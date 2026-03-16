"""Tests for docker/containers.py — container lifecycle."""

import unittest
from unittest.mock import patch

from darkfactory.docker.containers import check_docker_image, check_language_image, start_container
from darkfactory.errors import ConfigError, TransientFailure
from darkfactory.utils.subprocess_runner import RunResult


def _fake_run_ok(stdout="abc123\n"):
    """Return a fake run() that succeeds with the given stdout."""
    def fake_run(cmd, **kw):
        return RunResult(exit_code=0, stdout=stdout, stderr="")
    return fake_run


def _ok():
    return RunResult(exit_code=0, stdout="", stderr="")


def _fail():
    return RunResult(exit_code=1, stdout="", stderr="error")


class TestStartContainer(unittest.TestCase):
    def _captured_cmd(self, docker_args, image, cmd, **kwargs):
        """Call start_container with a fake run() and return the captured command list."""
        captured: list[list[str]] = []

        def fake_run(cmd, **kw):
            captured.append(list(cmd))
            return RunResult(exit_code=0, stdout="abc123\n", stderr="")

        with patch("darkfactory.docker.containers.run", side_effect=fake_run):
            start_container(docker_args, image, cmd, **kwargs)

        return captured[0]

    def test_uses_detached_flag(self):
        cmd = self._captured_cmd([], "myimage", ["echo", "hi"])
        self.assertIn("-d", cmd)

    def test_docker_run_prefix(self):
        cmd = self._captured_cmd([], "myimage", ["echo", "hi"])
        self.assertEqual(cmd[:2], ["docker", "run"])

    def test_image_and_cmd_appended_after_args(self):
        cmd = self._captured_cmd(["--memory=2g"], "myimage", ["echo", "hi"])
        img_idx = cmd.index("myimage")
        self.assertIn("--memory=2g", cmd[:img_idx])
        self.assertEqual(cmd[img_idx + 1:], ["echo", "hi"])

    def test_name_added_to_cmd(self):
        """name kwarg must produce --name in the command."""
        cmd = self._captured_cmd([], "myimage", [], name="mycontainer")
        self.assertIn("--name", cmd)
        name_idx = cmd.index("--name")
        self.assertEqual(cmd[name_idx + 1], "mycontainer")

    def test_name_not_in_cmd_when_omitted(self):
        cmd = self._captured_cmd([], "myimage", [])
        self.assertNotIn("--name", cmd)

    def test_name_appears_before_image(self):
        """--name must come before the image name."""
        cmd = self._captured_cmd([], "myimage", [], name="srv")
        name_idx = cmd.index("--name")
        img_idx = cmd.index("myimage")
        self.assertLess(name_idx, img_idx)

    def test_returns_stripped_container_id(self):
        with patch("darkfactory.docker.containers.run", side_effect=_fake_run_ok("abc123\n")):
            result = start_container([], "myimage", [])
        self.assertEqual(result, "abc123")

    def test_dry_run_returns_empty_string(self):
        """dry_run=True should propagate to run() and return ''."""
        captured_kwargs: list[dict] = []

        def fake_run(cmd, **kw):
            captured_kwargs.append(kw)
            return RunResult(exit_code=0, stdout="", stderr="")

        with patch("darkfactory.docker.containers.run", side_effect=fake_run):
            result = start_container([], "myimage", [], dry_run=True)

        self.assertTrue(captured_kwargs[0].get("dry_run"))
        self.assertEqual(result, "")

    def test_raises_on_nonzero_exit_with_stderr(self):
        def fake_run(cmd, **kw):
            return RunResult(exit_code=1, stdout="", stderr="docker: error")

        with patch("darkfactory.docker.containers.run", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as ctx:
                start_container([], "myimage", [], name="badcontainer")

        self.assertIn("badcontainer", str(ctx.exception))
        self.assertIn("docker: error", str(ctx.exception))

    def test_raises_falls_back_to_stdout_when_stderr_empty(self):
        """When stderr is empty, stdout should appear in the error message."""
        def fake_run(cmd, **kw):
            return RunResult(exit_code=1, stdout="Error response from daemon", stderr="")

        with patch("darkfactory.docker.containers.run", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as ctx:
                start_container([], "myimage", [])

        self.assertIn("Error response from daemon", str(ctx.exception))

    def test_raises_uses_image_name_when_no_name(self):
        def fake_run(cmd, **kw):
            return RunResult(exit_code=1, stdout="", stderr="oops")

        with patch("darkfactory.docker.containers.run", side_effect=fake_run):
            with self.assertRaises(RuntimeError) as ctx:
                start_container([], "myimage", [])

        self.assertIn("myimage", str(ctx.exception))

    def test_audit_path_logs_command(self):
        """When audit_path is given, audit_log should be called."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            audit_path = Path(tmpdir) / "audit.log"
            with patch("darkfactory.docker.containers.run", side_effect=_fake_run_ok()):
                start_container([], "myimage", [], audit_path=audit_path)
            content = audit_path.read_text()

        self.assertIn("START_CONTAINER", content)
        self.assertIn("myimage", content)


def _daemon_ok_image_fail(cmd, **kw):
    """Simulate daemon running but image missing."""
    if cmd == ["docker", "info"]:
        return _ok()
    return _fail()


class TestCheckDockerImage(unittest.TestCase):
    def test_image_present_no_error(self):
        """No exception when both docker info and image inspect succeed."""
        with patch("darkfactory.docker.containers.run", return_value=_ok()):
            check_docker_image("dark-factory-python")  # should not raise

    def test_image_missing_raises_config_error(self):
        """ConfigError with build hint when image inspect fails."""
        with patch("darkfactory.docker.containers.run", side_effect=_daemon_ok_image_fail):
            with self.assertRaises(ConfigError) as ctx:
                check_docker_image("dark-factory-python")

        msg = str(ctx.exception)
        self.assertIn("dark-factory-python", msg)
        self.assertIn("bash docker/build-images.sh python", msg)

    def test_daemon_not_running_raises_transient_failure(self):
        """TransientFailure when docker info fails (daemon not running)."""
        with patch("darkfactory.docker.containers.run", return_value=_fail()):
            with self.assertRaises(TransientFailure) as ctx:
                check_docker_image("dark-factory-python")

        self.assertIn("Docker daemon", str(ctx.exception))


class TestCheckLanguageImage(unittest.TestCase):
    def test_image_missing_raises_with_spec_context(self):
        """ConfigError includes spec name when spec_name is provided."""
        with patch("darkfactory.docker.containers.run", side_effect=_daemon_ok_image_fail):
            with self.assertRaises(ConfigError) as ctx:
                check_language_image("rust", spec_name="my-spec")

        msg = str(ctx.exception)
        self.assertIn("my-spec", msg)
        self.assertIn("dark-factory-rust", msg)

    def test_image_missing_no_spec_name(self):
        """ConfigError passes through unchanged when spec_name is empty."""
        with patch("darkfactory.docker.containers.run", side_effect=_daemon_ok_image_fail):
            with self.assertRaises(ConfigError) as ctx:
                check_language_image("rust")

        msg = str(ctx.exception)
        self.assertIn("dark-factory-rust", msg)
        self.assertNotIn("Spec '", msg)

    def test_image_missing_explicit_empty_spec_name(self):
        """Explicit spec_name='' behaves identically to no spec_name."""
        with patch("darkfactory.docker.containers.run", side_effect=_daemon_ok_image_fail):
            with self.assertRaises(ConfigError) as ctx:
                check_language_image("rust", spec_name="")

        self.assertNotIn("Spec '", str(ctx.exception))

    def test_daemon_down_raises_transient_failure_unwrapped(self):
        """TransientFailure propagates unchanged — spec context is NOT prepended."""
        with patch("darkfactory.docker.containers.run", return_value=_fail()):
            with self.assertRaises(TransientFailure) as ctx:
                check_language_image("python", spec_name="my-spec")

        msg = str(ctx.exception)
        self.assertIn("Docker daemon", msg)
        self.assertNotIn("Spec '", msg)


if __name__ == "__main__":
    unittest.main()
