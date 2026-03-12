"""Tests for built-in health check strategies."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

from darkfactory.deployment.health import (
    check_http,
    check_tcp,
    check_docker,
    check_command,
    run_health_check,
)


class TestCheckHttp:
    def test_success(self) -> None:
        mock_result = MagicMock(exit_code=0)
        with patch("darkfactory.deployment.health.run_subprocess", return_value=mock_result):
            assert check_http("http://localhost:8080/health", timeout=5) is True

    def test_failure_after_timeout(self) -> None:
        mock_result = MagicMock(exit_code=7)
        with patch("darkfactory.deployment.health.run_subprocess", return_value=mock_result):
            assert check_http("http://localhost:8080/health", timeout=1, max_retries=1) is False

    def test_empty_url(self) -> None:
        assert check_http("") is False


class TestCheckTcp:
    def test_success(self) -> None:
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        with patch("darkfactory.deployment.health.socket.create_connection", return_value=mock_conn):
            assert check_tcp("localhost", 8080) is True

    def test_connection_refused(self) -> None:
        with patch("darkfactory.deployment.health.socket.create_connection",
                    side_effect=ConnectionRefusedError):
            assert check_tcp("localhost", 8080) is False

    def test_empty_host(self) -> None:
        assert check_tcp("", 0) is False


class TestCheckDocker:
    def test_healthy(self) -> None:
        mock_result = MagicMock(exit_code=0, stdout="healthy\n")
        with patch("darkfactory.deployment.health.run_subprocess", return_value=mock_result):
            assert check_docker("my-container") is True

    def test_unhealthy(self) -> None:
        mock_result = MagicMock(exit_code=0, stdout="unhealthy\n")
        with patch("darkfactory.deployment.health.run_subprocess", return_value=mock_result):
            assert check_docker("my-container") is False

    def test_not_found(self) -> None:
        mock_result = MagicMock(exit_code=1, stdout="")
        with patch("darkfactory.deployment.health.run_subprocess", return_value=mock_result):
            assert check_docker("missing-container") is False

    def test_empty_container(self) -> None:
        assert check_docker("") is False


class TestCheckCommand:
    def test_success(self) -> None:
        mock_result = MagicMock(exit_code=0)
        with patch("darkfactory.deployment.health.run_subprocess", return_value=mock_result):
            assert check_command("./check.sh", "/project") is True

    def test_failure(self) -> None:
        mock_result = MagicMock(exit_code=1)
        with patch("darkfactory.deployment.health.run_subprocess", return_value=mock_result):
            assert check_command("./check.sh", "/project") is False

    def test_empty_command(self) -> None:
        assert check_command("", "/project") is False


class TestRunHealthCheck:
    def test_dispatches_http(self) -> None:
        with patch("darkfactory.deployment.health.check_http", return_value=True) as mock:
            result = run_health_check("http", url="http://localhost/health")
            assert result is True
            mock.assert_called_once()

    def test_dispatches_tcp(self) -> None:
        with patch("darkfactory.deployment.health.check_tcp", return_value=True) as mock:
            result = run_health_check("tcp", host="localhost", port=8080)
            assert result is True
            mock.assert_called_once()

    def test_dispatches_docker(self) -> None:
        with patch("darkfactory.deployment.health.check_docker", return_value=True) as mock:
            result = run_health_check("docker", container="my-app")
            assert result is True
            mock.assert_called_once()

    def test_dispatches_command(self) -> None:
        with patch("darkfactory.deployment.health.check_command", return_value=True) as mock:
            result = run_health_check("command", cmd="./check.sh", project_dir="/app")
            assert result is True
            mock.assert_called_once()

    def test_unknown_strategy(self) -> None:
        assert run_health_check("unknown") is False
