"""Tests for transient failure retry (Phase 7A)."""

import unittest

from darkfactory.errors import TransientFailure
from darkfactory.models.config import PipelineConfig
from darkfactory.pipeline.agents import _is_transient
from darkfactory.utils.backoff import transient_backoff


class TestTransientClassification(unittest.TestCase):
    def test_oom_exit_code(self) -> None:
        self.assertTrue(_is_transient(137, ""))

    def test_oom_in_stderr(self) -> None:
        self.assertTrue(_is_transient(1, "Cannot allocate memory"))

    def test_network_timeout(self) -> None:
        self.assertTrue(_is_transient(1, "connection timed out"))

    def test_request_timed_out(self) -> None:
        self.assertTrue(_is_transient(1, "request timed out"))

    def test_deadline_exceeded(self) -> None:
        self.assertTrue(_is_transient(1, "context deadline exceeded"))

    def test_test_timeout_not_transient(self) -> None:
        self.assertFalse(_is_transient(1, "test timeout: 30s expired"))

    def test_connection_refused(self) -> None:
        self.assertTrue(_is_transient(1, "Connection refused"))

    def test_docker_daemon(self) -> None:
        self.assertTrue(_is_transient(1, "Cannot connect to the Docker daemon"))

    def test_dns_failure(self) -> None:
        self.assertTrue(_is_transient(1, "Name resolution failed"))

    def test_rate_limit(self) -> None:
        self.assertTrue(_is_transient(1, "429 Too Many Requests"))

    def test_service_unavailable(self) -> None:
        self.assertTrue(_is_transient(1, "503 Service Unavailable"))

    def test_502_bad_gateway(self) -> None:
        self.assertTrue(_is_transient(1, "502 Bad Gateway"))

    def test_implementation_failure_not_transient(self) -> None:
        self.assertFalse(_is_transient(1, "SyntaxError: unexpected token"))

    def test_normal_exit_not_transient(self) -> None:
        self.assertFalse(_is_transient(1, "test failed: expected 3 got 5"))

    def test_exit_0_not_transient(self) -> None:
        self.assertFalse(_is_transient(0, ""))


class TestTransientBackoff(unittest.TestCase):
    def test_increases_with_attempt(self) -> None:
        # Run multiple times to account for jitter — d1 base (60) > d0 max (37.5).
        for _ in range(10):
            d0 = transient_backoff(0, 30.0)
            d1 = transient_backoff(1, 30.0)
            self.assertGreater(d1, d0)

    def test_zero_base(self) -> None:
        d = transient_backoff(0, 0.0)
        self.assertEqual(d, 0.0)

    def test_jitter_range(self) -> None:
        # With base=30, attempt=0: delay=30, jitter in [0, 7.5].
        # So result in [30, 37.5].
        for _ in range(20):
            d = transient_backoff(0, 30.0)
            self.assertGreaterEqual(d, 30.0)
            self.assertLessEqual(d, 37.5)


class TestTransientConfig(unittest.TestCase):
    def test_default_values(self) -> None:
        config = PipelineConfig()
        self.assertEqual(config.transient_retry_max, 2)
        self.assertEqual(config.transient_retry_base_s, 30.0)

    def test_negative_retry_max_rejected(self) -> None:
        with self.assertRaises(Exception):
            PipelineConfig(transient_retry_max=-1)

    def test_negative_base_s_rejected(self) -> None:
        with self.assertRaises(Exception):
            PipelineConfig(transient_retry_base_s=-1.0)

    def test_zero_retry_max_allowed(self) -> None:
        config = PipelineConfig(transient_retry_max=0)
        self.assertEqual(config.transient_retry_max, 0)


class TestTransientFailureError(unittest.TestCase):
    def test_is_dark_factory_error(self) -> None:
        from darkfactory.errors import DarkFactoryError
        e = TransientFailure("OOM")
        self.assertIsInstance(e, DarkFactoryError)

    def test_message(self) -> None:
        e = TransientFailure("connection timed out")
        self.assertEqual(str(e), "connection timed out")


class TestTransientRetryConfigParsing(unittest.TestCase):
    def test_loader_handles_transient_retry(self) -> None:
        from darkfactory.config.loader import _build_config
        data = {
            "agents": {
                "transient_retry": {
                    "max": 5,
                    "base_s": 10.0,
                },
            },
        }
        config = _build_config(data)
        self.assertEqual(config.transient_retry_max, 5)
        self.assertEqual(config.transient_retry_base_s, 10.0)


if __name__ == "__main__":
    unittest.main()
