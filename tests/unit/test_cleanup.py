"""Tests for pipeline/cleanup.py — signal handling and resource cleanup."""

import unittest
from unittest.mock import patch, MagicMock

from darkfactory.pipeline.cleanup import CleanupManager


class TestCleanupManager(unittest.TestCase):
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    def test_cleanup_all(
        self,
        mock_proxies: MagicMock,
        mock_containers: MagicMock,
        mock_networks: MagicMock,
    ) -> None:
        mgr = CleanupManager(run_id="run1")
        mgr.cleanup()
        mock_proxies.assert_called_once_with("run1", dry_run=False)
        mock_containers.assert_called_once_with("run1", dry_run=False)
        mock_networks.assert_called_once_with("run1", dry_run=False)

    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    def test_reentrancy_guard(
        self,
        mock_proxies: MagicMock,
        mock_containers: MagicMock,
        mock_networks: MagicMock,
    ) -> None:
        mgr = CleanupManager(run_id="run1")
        mgr.cleanup()
        mgr.cleanup()  # Second call should be no-op.
        self.assertEqual(mock_proxies.call_count, 1)

    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    def test_on_interrupt_callback(
        self,
        mock_proxies: MagicMock,
        mock_containers: MagicMock,
        mock_networks: MagicMock,
    ) -> None:
        callback = MagicMock()
        mgr = CleanupManager(run_id="run1", on_interrupt=callback)
        mgr.cleanup()
        callback.assert_called_once()

    @patch("darkfactory.pipeline.cleanup.run")
    @patch("darkfactory.pipeline.cleanup.cleanup_networks")
    @patch("darkfactory.pipeline.cleanup.cleanup_containers")
    @patch("darkfactory.pipeline.cleanup.teardown_proxies")
    def test_deploy_teardown(
        self,
        mock_proxies: MagicMock,
        mock_containers: MagicMock,
        mock_networks: MagicMock,
        mock_run: MagicMock,
    ) -> None:
        mgr = CleanupManager(run_id="run1", deploy_teardown_cmd="echo teardown")
        mgr.cleanup()
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertIn("echo teardown", cmd)

    def test_no_run_id(self) -> None:
        """No run_id means no Docker cleanup."""
        mgr = CleanupManager()
        # Should not raise.
        mgr.cleanup()


if __name__ == "__main__":
    unittest.main()
