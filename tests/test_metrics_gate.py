"""Tests for darkfactory.deployment.metrics_gate."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from darkfactory.deployment.metrics_gate import (
    collect_and_evaluate,
    collect_metrics,
    evaluate_thresholds,
    parse_metric_output,
)
from darkfactory.deployment.models import MetricThreshold


class TestParseMetricOutput(unittest.TestCase):
    def test_plain_float(self):
        self.assertAlmostEqual(parse_metric_output("0.05"), 0.05)

    def test_integer(self):
        self.assertAlmostEqual(parse_metric_output("42"), 42.0)

    def test_negative(self):
        self.assertAlmostEqual(parse_metric_output("-1.5"), -1.5)

    def test_whitespace(self):
        self.assertAlmostEqual(parse_metric_output("  0.05  \n"), 0.05)

    def test_empty(self):
        self.assertEqual(parse_metric_output(""), float("inf"))

    def test_multi_line(self):
        self.assertEqual(parse_metric_output("0.05\n0.06"), float("inf"))

    def test_non_numeric(self):
        self.assertEqual(parse_metric_output("five percent"), float("inf"))

    def test_binary_data(self):
        self.assertEqual(parse_metric_output("\x00\xff"), float("inf"))

    def test_very_large_number(self):
        self.assertAlmostEqual(parse_metric_output("9999999.9"), 9999999.9)

    def test_scientific_notation(self):
        self.assertAlmostEqual(parse_metric_output("1.5e-3"), 0.0015)


class TestEvaluateThresholds(unittest.TestCase):
    def test_no_breach(self):
        thresholds = [MetricThreshold(name="err", max_value=0.05)]
        breached = evaluate_thresholds({"err": 0.03}, thresholds)
        self.assertEqual(breached, [])

    def test_max_breach(self):
        thresholds = [MetricThreshold(name="err", max_value=0.05)]
        breached = evaluate_thresholds({"err": 0.10}, thresholds)
        self.assertEqual(breached, ["err"])

    def test_min_breach(self):
        thresholds = [MetricThreshold(name="success", min_value=99.0)]
        breached = evaluate_thresholds({"success": 98.5}, thresholds)
        self.assertEqual(breached, ["success"])

    def test_both_bounds_pass(self):
        thresholds = [MetricThreshold(name="lat", max_value=500, min_value=10)]
        breached = evaluate_thresholds({"lat": 250}, thresholds)
        self.assertEqual(breached, [])

    def test_both_bounds_max_breach(self):
        thresholds = [MetricThreshold(name="lat", max_value=500, min_value=10)]
        breached = evaluate_thresholds({"lat": 600}, thresholds)
        self.assertEqual(breached, ["lat"])

    def test_missing_metric(self):
        thresholds = [MetricThreshold(name="err", max_value=0.05)]
        breached = evaluate_thresholds({}, thresholds)
        self.assertEqual(breached, [])

    def test_multiple_thresholds(self):
        thresholds = [
            MetricThreshold(name="err", max_value=0.05),
            MetricThreshold(name="lat", max_value=500),
        ]
        breached = evaluate_thresholds({"err": 0.10, "lat": 200}, thresholds)
        self.assertEqual(breached, ["err"])


class TestCollectMetrics(unittest.TestCase):
    @patch("darkfactory.deployment.metrics_gate.run_command")
    def test_collects_values(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0, stdout="0.03\n")
        thresholds = [MetricThreshold(name="err", max_value=0.05, metric_cmd="scripts/check.sh")]
        metrics = collect_metrics(thresholds, "/tmp")
        self.assertAlmostEqual(metrics["err"], 0.03)

    @patch("darkfactory.deployment.metrics_gate.run_command")
    def test_failure_returns_inf(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=1, stdout="")
        thresholds = [MetricThreshold(name="err", max_value=0.05, metric_cmd="scripts/check.sh")]
        metrics = collect_metrics(thresholds, "/tmp")
        self.assertEqual(metrics["err"], float("inf"))

    def test_no_metric_cmd_returns_inf(self):
        thresholds = [MetricThreshold(name="err", max_value=0.05)]
        metrics = collect_metrics(thresholds, "/tmp")
        self.assertEqual(metrics["err"], float("inf"))


class TestCollectAndEvaluate(unittest.TestCase):
    @patch("darkfactory.deployment.metrics_gate.run_command")
    def test_full_pass(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0, stdout="0.03\n")
        thresholds = [MetricThreshold(name="err", max_value=0.05, metric_cmd="scripts/check.sh")]
        metrics, breached = collect_and_evaluate(thresholds, "/tmp")
        self.assertEqual(breached, [])
        self.assertAlmostEqual(metrics["err"], 0.03)

    @patch("darkfactory.deployment.metrics_gate.run_command")
    def test_full_breach(self, mock_cmd):
        mock_cmd.return_value = MagicMock(returncode=0, stdout="0.10\n")
        thresholds = [MetricThreshold(name="err", max_value=0.05, metric_cmd="scripts/check.sh")]
        metrics, breached = collect_and_evaluate(thresholds, "/tmp")
        self.assertEqual(breached, ["err"])


if __name__ == "__main__":
    unittest.main()
