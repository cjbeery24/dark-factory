"""Tests for check_scenario_intents in spec/precheck.py."""

import unittest

from darkfactory.spec.precheck import check_scenario_intents


def _intent_table(rows: int) -> str:
    """Build a markdown intent table with the given number of rows."""
    header = "| # | Intent | Layer |\n|---|--------|-------|\n"
    lines = [f"| {i} | Intent number {i} | happy_path |" for i in range(1, rows + 1)]
    return header + "\n".join(lines) + "\n"


class TestCheckScenarioIntents(unittest.TestCase):
    def test_section_missing(self) -> None:
        text = "## Problem Statement\n\nJust a problem.\n"
        result = check_scenario_intents(text)
        self.assertFalse(result.present)
        self.assertIn("not found", result.issues[0])

    def test_section_present_with_intents(self) -> None:
        text = (
            "## Scenario Intent\n\n"
            "### FR-1: Authentication\n"
            + _intent_table(7)
            + "\n### FR-2: Authorization\n"
            + _intent_table(5)
        )
        result = check_scenario_intents(text)
        self.assertTrue(result.present)
        self.assertEqual(result.fr_intent_counts["FR-1: Authentication"], 7)
        self.assertEqual(result.fr_intent_counts["FR-2: Authorization"], 5)
        self.assertEqual(result.issues, [])

    def test_too_few_intents_flagged(self) -> None:
        text = (
            "## Scenario Intent\n\n"
            "### FR-1: Login\n"
            + _intent_table(2)
        )
        result = check_scenario_intents(text)
        self.assertTrue(result.present)
        self.assertEqual(result.fr_intent_counts["FR-1: Login"], 2)
        self.assertTrue(any("minimum 5" in i for i in result.issues))

    def test_zero_intents_flagged(self) -> None:
        text = (
            "## Scenario Intent\n\n"
            "### FR-1: Login\n\n"
            "No intents here.\n"
        )
        result = check_scenario_intents(text)
        self.assertTrue(result.present)
        self.assertEqual(result.fr_intent_counts["FR-1: Login"], 0)
        self.assertEqual(len(result.issues), 1)
        self.assertIn("no intents declared", result.issues[0])

    def test_cross_cutting_not_required(self) -> None:
        """Cross-cutting section with <5 intents should NOT generate an issue."""
        text = (
            "## Scenario Intent\n\n"
            "### FR-1: Core\n"
            + _intent_table(6)
            + "\n### Cross-Cutting\n"
            + _intent_table(2)
        )
        result = check_scenario_intents(text)
        self.assertTrue(result.present)
        self.assertEqual(result.fr_intent_counts["Cross-Cutting"], 2)
        self.assertEqual(result.issues, [])

    def test_api_endpoint_headings(self) -> None:
        """API specs use HTTP method + path as sub-headings."""
        text = (
            "## Scenario Intent\n\n"
            "### POST /api/users\n"
            + _intent_table(8)
            + "\n### GET /api/users/:id\n"
            + _intent_table(5)
        )
        result = check_scenario_intents(text)
        self.assertTrue(result.present)
        self.assertEqual(result.fr_intent_counts["POST /api/users"], 8)
        self.assertEqual(result.fr_intent_counts["GET /api/users/:id"], 5)
        self.assertEqual(result.issues, [])

    def test_intents_inside_code_fence_excluded(self) -> None:
        text = (
            "## Scenario Intent\n\n"
            "### FR-1: Example\n"
            + _intent_table(5)
            + "\n```markdown\n"
            "| 6 | Fake intent | edge_case |\n"
            "| 7 | Another fake | error |\n"
            "```\n"
        )
        result = check_scenario_intents(text)
        self.assertEqual(result.fr_intent_counts["FR-1: Example"], 5)

    def test_numbered_section_heading(self) -> None:
        """Scenario Intent section with a number prefix like '## 10. Scenario Intent'."""
        text = (
            "## 10. Scenario Intent\n\n"
            "### FR-1: Feature\n"
            + _intent_table(6)
        )
        result = check_scenario_intents(text)
        self.assertTrue(result.present)
        self.assertEqual(result.fr_intent_counts["FR-1: Feature"], 6)

    def test_section_bounded_by_next_h2(self) -> None:
        """Intent section ends at the next ## heading."""
        text = (
            "## Scenario Intent\n\n"
            "### FR-1: Feature\n"
            + _intent_table(6)
            + "\n## Examples\n\n"
            "| 1 | This is an example table row | not_a_layer |\n"
        )
        result = check_scenario_intents(text)
        self.assertEqual(result.fr_intent_counts["FR-1: Feature"], 6)
        # The row under Examples should NOT be counted.
        self.assertNotIn("(ungrouped)", result.fr_intent_counts)

    def test_ungrouped_intents_when_no_subheadings(self) -> None:
        """If no ### sub-headings, all intents counted as one group."""
        text = (
            "## Scenario Intent\n\n"
            + _intent_table(5)
        )
        result = check_scenario_intents(text)
        self.assertTrue(result.present)
        self.assertEqual(result.fr_intent_counts["(ungrouped)"], 5)

    def test_free_text_subheadings(self) -> None:
        """Templates like bug-fix, security-fix use free-text group names."""
        text = (
            "## Scenario Intent\n\n"
            "### Attack Prevention\n"
            + _intent_table(6)
            + "\n### Normal Operation Preservation\n"
            + _intent_table(5)
        )
        result = check_scenario_intents(text)
        self.assertTrue(result.present)
        self.assertEqual(result.fr_intent_counts["Attack Prevention"], 6)
        self.assertEqual(result.fr_intent_counts["Normal Operation Preservation"], 5)
        self.assertEqual(result.issues, [])

    def test_empty_section_no_content(self) -> None:
        """Scenario Intent heading with no content at all."""
        text = (
            "## Scenario Intent\n\n"
            "## Examples\n\n"
            "Some example content.\n"
        )
        result = check_scenario_intents(text)
        self.assertTrue(result.present)
        self.assertEqual(result.fr_intent_counts["(ungrouped)"], 0)
        self.assertEqual(len(result.issues), 1)
        self.assertIn("no intents declared", result.issues[0])


if __name__ == "__main__":
    unittest.main()
