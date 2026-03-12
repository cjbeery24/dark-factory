"""Tests for darkfactory.deployment.feature_flags."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from darkfactory.deployment.feature_flags import (
    _flag_name,
    check_flag,
    create_flag,
    disable_flag,
    enable_flag,
)
from darkfactory.models.config import PipelineConfig


class TestFlagName(unittest.TestCase):
    def test_format(self):
        self.assertEqual(_flag_name("my-spec"), "df-my-spec")


class TestCreateFlag(unittest.TestCase):
    def test_skip_when_unconfigured(self):
        config = PipelineConfig()
        self.assertTrue(create_flag(config, "spec1", "/tmp"))

    @patch("darkfactory.deployment.feature_flags.run_command")
    def test_success(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = PipelineConfig(flag_create_cmd="scripts/flag-create.sh")
        self.assertTrue(create_flag(config, "spec1", "/tmp"))
        env = mock_cmd.call_args[1]["env"]
        self.assertEqual(env["FLAG_NAME"], "df-spec1")
        self.assertEqual(env["FLAG_DEFAULT"], "off")

    @patch("darkfactory.deployment.feature_flags.run_command")
    def test_failure(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=1)
        config = PipelineConfig(flag_create_cmd="scripts/flag-create.sh")
        self.assertFalse(create_flag(config, "spec1", "/tmp"))

    @patch("darkfactory.deployment.feature_flags.run_command")
    def test_exception(self, mock_cmd):
        mock_cmd.side_effect = Exception("boom")
        config = PipelineConfig(flag_create_cmd="scripts/flag-create.sh")
        self.assertFalse(create_flag(config, "spec1", "/tmp"))


class TestEnableFlag(unittest.TestCase):
    def test_skip_when_unconfigured(self):
        config = PipelineConfig()
        self.assertTrue(enable_flag(config, "spec1", "/tmp"))

    @patch("darkfactory.deployment.feature_flags.run_command")
    def test_success_with_percent(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = PipelineConfig(flag_enable_cmd="scripts/flag-enable.sh")
        self.assertTrue(enable_flag(config, "spec1", "/tmp", percent=50))
        env = mock_cmd.call_args[1]["env"]
        self.assertEqual(env["FLAG_PERCENT"], "50")


class TestDisableFlag(unittest.TestCase):
    def test_skip_when_unconfigured(self):
        config = PipelineConfig()
        self.assertTrue(disable_flag(config, "spec1", "/tmp"))

    @patch("darkfactory.deployment.feature_flags.run_command")
    def test_success(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0)
        config = PipelineConfig(flag_disable_cmd="scripts/flag-disable.sh")
        self.assertTrue(disable_flag(config, "spec1", "/tmp"))


class TestCheckFlag(unittest.TestCase):
    def test_returns_none_when_unconfigured(self):
        config = PipelineConfig()
        self.assertIsNone(check_flag(config, "spec1", "/tmp"))

    @patch("darkfactory.deployment.feature_flags.run_command")
    def test_returns_status(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0, stdout="on\n")
        config = PipelineConfig(flag_check_cmd="scripts/flag-check.sh")
        self.assertEqual(check_flag(config, "spec1", "/tmp"), "on")

    @patch("darkfactory.deployment.feature_flags.run_command")
    def test_returns_none_on_failure(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=1, stdout="")
        config = PipelineConfig(flag_check_cmd="scripts/flag-check.sh")
        self.assertIsNone(check_flag(config, "spec1", "/tmp"))


if __name__ == "__main__":
    unittest.main()
