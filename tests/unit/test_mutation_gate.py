"""Tests for mutation testing quality gate (Phase 7B)."""

import unittest

from darkfactory.models.config import PipelineConfig
from darkfactory.pipeline.quality_gate import GateResult


class TestMutationGateResult(unittest.TestCase):
    def test_gate_result_has_mutation_score(self) -> None:
        result = GateResult(exit_code=0)
        self.assertEqual(result.mutation_score, 0)

    def test_gate_result_stores_mutation_score(self) -> None:
        result = GateResult(exit_code=0, mutation_score=85)
        self.assertEqual(result.mutation_score, 85)


class TestMutationConfig(unittest.TestCase):
    def test_mutation_min_score_default(self) -> None:
        config = PipelineConfig()
        self.assertEqual(config.mutation_min_score, 0)

    def test_mutation_min_score_custom(self) -> None:
        config = PipelineConfig(mutation_min_score=80)
        self.assertEqual(config.mutation_min_score, 80)

    def test_mutation_cmd_default_empty(self) -> None:
        config = PipelineConfig()
        self.assertEqual(config.mutation_cmd, "")

    def test_spec_sanitization_default_enabled(self) -> None:
        config = PipelineConfig()
        self.assertTrue(config.spec_sanitization_enabled)

    def test_spec_sanitization_can_disable(self) -> None:
        config = PipelineConfig(spec_sanitization_enabled=False)
        self.assertFalse(config.spec_sanitization_enabled)


if __name__ == "__main__":
    unittest.main()
