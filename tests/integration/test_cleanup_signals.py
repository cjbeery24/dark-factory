"""Integration tests for cleanup manager and signal handling.

Tests CleanupManager lifecycle — registration, reentrancy guard,
abort flag, and callback invocation. Signal delivery is tested
in-process via os.kill.
"""

from __future__ import annotations

import os
import signal
from unittest.mock import MagicMock, patch

import pytest

from darkfactory.pipeline.cleanup import CleanupManager


@pytest.mark.integration
class TestCleanupManager:
    """CleanupManager lifecycle."""

    def test_register_idempotent(self):
        mgr = CleanupManager(run_id="test-run")
        mgr.register()
        mgr.register()  # Should not raise or double-register
        assert mgr._registered

    def test_abort_flag_starts_false(self):
        mgr = CleanupManager(run_id="test-run")
        assert mgr.abort_requested is False

    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    def test_cleanup_calls_teardown(self, mock_networks, mock_containers, mock_proxies):
        mgr = CleanupManager(run_id="test-run", dry_run=True)
        mgr.cleanup()

        mock_proxies.assert_called_once_with("test-run", dry_run=True)
        mock_containers.assert_called_once_with("test-run", dry_run=True)
        mock_networks.assert_called_once_with("test-run", dry_run=True)

    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    def test_cleanup_reentrancy_guard(self, mock_networks, mock_containers, mock_proxies):
        mgr = CleanupManager(run_id="test-run")
        mgr.cleanup()
        mgr.cleanup()  # Second call should be a no-op

        # Each should be called exactly once despite two cleanup() calls
        assert mock_proxies.call_count == 1
        assert mock_containers.call_count == 1
        assert mock_networks.call_count == 1

    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    def test_no_run_id_skips_docker_cleanup(self, mock_networks, mock_containers, mock_proxies):
        mgr = CleanupManager()  # No run_id
        mgr.cleanup()
        mock_proxies.assert_not_called()
        mock_containers.assert_not_called()
        mock_networks.assert_not_called()

    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    def test_on_interrupt_callback(self, mock_networks, mock_containers, mock_proxies):
        callback = MagicMock()
        mgr = CleanupManager(run_id="test-run", on_interrupt=callback)
        mgr.cleanup()
        callback.assert_called_once()

    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    def test_callback_failure_does_not_block_cleanup(
        self, mock_networks, mock_containers, mock_proxies
    ):
        callback = MagicMock(side_effect=RuntimeError("callback boom"))
        mgr = CleanupManager(run_id="test-run", on_interrupt=callback)
        # Should not raise despite callback failure
        mgr.cleanup()
        # Docker cleanup should still proceed
        mock_proxies.assert_called_once()

    @patch("darkfactory.pipeline.cleanup.teardown_proxies", side_effect=Exception("proxy boom"))
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    def test_partial_failure_continues(self, mock_networks, mock_containers, mock_proxies):
        mgr = CleanupManager(run_id="test-run")
        # Should not raise despite proxy teardown failure
        mgr.cleanup()
        # Container and network cleanup should still proceed
        mock_containers.assert_called_once()
        mock_networks.assert_called_once()

    @patch("darkfactory.pipeline.cleanup.run")
    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    def test_deploy_teardown_cmd_shell(self, mock_networks, mock_containers, mock_proxies, mock_run):
        mgr = CleanupManager(
            run_id="test-run",
            deploy_teardown_cmd="./teardown.sh",
            deploy_cmd_allow_shell=True,
        )
        mgr.cleanup()
        mock_run.assert_called_once_with(
            ["bash", "-c", "./teardown.sh"], timeout=30
        )

    @patch("darkfactory.deployment.command.run_command")
    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    def test_deploy_teardown_cmd_with_project_dir(
        self, mock_networks, mock_containers, mock_proxies, mock_run_cmd
    ):
        mgr = CleanupManager(
            run_id="test-run",
            deploy_teardown_cmd="./teardown.sh",
            deploy_cmd_allow_shell=False,
            project_dir="/tmp/proj",
        )
        mgr.cleanup()
        mock_run_cmd.assert_called_once_with(
            "./teardown.sh", "/tmp/proj", timeout=30
        )


@pytest.mark.integration
class TestSignalHandling:
    """Signal handler sets abort flag and triggers cleanup."""

    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    def test_sigterm_sets_abort_flag(self, mock_networks, mock_containers, mock_proxies):
        orig_term = signal.getsignal(signal.SIGTERM)
        orig_int = signal.getsignal(signal.SIGINT)
        try:
            mgr = CleanupManager(run_id="test-run")
            mgr.register()

            with pytest.raises(SystemExit) as exc_info:
                os.kill(os.getpid(), signal.SIGTERM)

            assert mgr.abort_requested is True
            assert exc_info.value.code == 128 + signal.SIGTERM
        finally:
            signal.signal(signal.SIGTERM, orig_term)
            signal.signal(signal.SIGINT, orig_int)

    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    def test_sigint_sets_abort_flag(self, mock_networks, mock_containers, mock_proxies):
        orig_term = signal.getsignal(signal.SIGTERM)
        orig_int = signal.getsignal(signal.SIGINT)
        try:
            mgr = CleanupManager(run_id="test-run")
            mgr.register()

            with pytest.raises(SystemExit) as exc_info:
                os.kill(os.getpid(), signal.SIGINT)

            assert mgr.abort_requested is True
            assert exc_info.value.code == 128 + signal.SIGINT
        finally:
            signal.signal(signal.SIGTERM, orig_term)
            signal.signal(signal.SIGINT, orig_int)
