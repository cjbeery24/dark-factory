"""Tests for tool documentation injection (Phase 4)."""

from __future__ import annotations

import unittest

from darkfactory.scenarios.tool_docs import TOOL_DOCS
from darkfactory.pipeline.orchestrator import _detect_tools_in_spec


class TestDetectToolsInSpec(unittest.TestCase):

    def test_detects_biome(self) -> None:
        self.assertIn("biome", _detect_tools_in_spec("Configure Biome for formatting"))

    def test_detects_multiple_tools(self) -> None:
        result = _detect_tools_in_spec("Set up ESLint and pnpm workspace")
        self.assertIn("eslint", result)
        self.assertIn("pnpm", result)

    def test_no_tools(self) -> None:
        self.assertEqual(_detect_tools_in_spec("Simple HTTP server"), [])

    def test_case_insensitive(self) -> None:
        self.assertIn("vitest", _detect_tools_in_spec("Configure VITEST for testing"))

    def test_no_substring_match(self) -> None:
        self.assertNotIn("biome", _detect_tools_in_spec("Add biometric authentication"))

    def test_all_entries_nonempty(self) -> None:
        for tool, doc in TOOL_DOCS.items():
            self.assertTrue(doc.strip(), f"TOOL_DOCS[{tool!r}] is empty")

    def test_yaml_not_triggered_by_generic_mention(self) -> None:
        """Bare 'yaml' or '.yml' should NOT trigger yaml_assertions docs."""
        result = _detect_tools_in_spec("Configure the CI pipeline YAML file")
        self.assertNotIn("yaml_assertions", result)

    def test_yaml_triggered_by_assertion_context(self) -> None:
        """yaml.parse or yaml assertion context should trigger docs."""
        result = _detect_tools_in_spec("Use yaml.parse to verify the .yml config")
        self.assertIn("yaml_assertions", result)


class TestToolDocsRegistry(unittest.TestCase):

    def test_known_tools_present(self) -> None:
        expected = {"biome", "eslint", "railway", "pnpm", "vitest", "yaml_assertions"}
        self.assertEqual(expected, set(TOOL_DOCS.keys()))


if __name__ == "__main__":
    unittest.main()
