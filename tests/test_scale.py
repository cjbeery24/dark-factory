"""Tests for scale validation (Phase 7D)."""

import unittest
from unittest.mock import MagicMock, patch

from darkfactory.models.config import PipelineConfig
from darkfactory.scenarios.scale import ScaleResult, _run_one


class TestScaleResult(unittest.TestCase):
    def test_percentiles_empty(self) -> None:
        r = ScaleResult()
        self.assertEqual(r.p50, 0.0)
        self.assertEqual(r.p95, 0.0)
        self.assertEqual(r.p99, 0.0)

    def test_percentiles_computed(self) -> None:
        r = ScaleResult(latencies=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
        self.assertAlmostEqual(r.p50, 6.0)  # index 5
        self.assertAlmostEqual(r.p95, 10.0)  # index 9
        self.assertAlmostEqual(r.p99, 10.0)  # index 9

    def test_scenarios_per_hour(self) -> None:
        r = ScaleResult(total=10, wall_seconds=36.0)
        self.assertAlmostEqual(r.scenarios_per_hour, 1000.0)


class TestScaleConfig(unittest.TestCase):
    def test_defaults(self) -> None:
        config = PipelineConfig()
        self.assertEqual(config.scale_concurrency, 4)

    def test_custom(self) -> None:
        config = PipelineConfig(scale_concurrency=8)
        self.assertEqual(config.scale_concurrency, 8)

    def test_invalid_concurrency(self) -> None:
        from darkfactory.errors import ConfigError
        with self.assertRaises(ConfigError):
            PipelineConfig(scale_concurrency=0)


class TestRunOne(unittest.TestCase):
    @patch("darkfactory.scenarios.scale.run_scenarios")
    def test_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(exit_code=0)
        config = PipelineConfig()
        from pathlib import Path
        ok, latency = _run_one("spec", config, Path("/tmp"))
        self.assertTrue(ok)
        self.assertGreater(latency, 0)

    @patch("darkfactory.scenarios.scale.run_scenarios")
    def test_failure(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(exit_code=1)
        config = PipelineConfig()
        from pathlib import Path
        ok, latency = _run_one("spec", config, Path("/tmp"))
        self.assertFalse(ok)

    @patch("darkfactory.scenarios.scale.run_scenarios")
    def test_exception_propagates(self, mock_run: MagicMock) -> None:
        mock_run.side_effect = RuntimeError("boom")
        config = PipelineConfig()
        from pathlib import Path
        with self.assertRaises(RuntimeError):
            _run_one("spec", config, Path("/tmp"))


if __name__ == "__main__":
    unittest.main()
