"""Tests for the model-based acceptance-criteria check."""

import unittest

from darkfactory.spec.precheck import check_acceptance_criteria_model
from darkfactory.spec.spec_model import parse_spec_model


def _check(text: str):
    return check_acceptance_criteria_model(parse_spec_model(text))


class TestAcceptanceCriteriaModel(unittest.TestCase):
    def test_malformed_gwt(self) -> None:
        text = (
            "## Acceptance Criteria\n\n"
            "| ID | Description |\n"
            "|----|-------------|\n"
            "| AC-1 | something happens |\n"
        )
        findings = _check(text)
        self.assertTrue(any(f.kind == "MALFORMED_GWT" and f.criterion_id == "AC-1" for f in findings))

    def test_table_broken_at_ac(self) -> None:
        text = (
            "## Acceptance Criteria\n\n"
            "| ID | Description |\n"
            "|----|-------------|\n"
            "| AC-1 | GIVEN x WHEN y THEN z |\n\n"
            "```json\n"
            '{"interrupt": true}\n'
            "```\n\n"
            "| ID | Description |\n"
            "|----|-------------|\n"
            "| AC-2 | GIVEN a WHEN b THEN c |\n"
        )
        findings = _check(text)
        kinds = [f.kind for f in findings]
        self.assertIn("TABLE_BROKEN_AT_AC", kinds)

    def test_bold_pseudo_heading_collision(self) -> None:
        text = (
            "## Acceptance Criteria\n\n"
            "**AC-2 stderr shape:** the server emits the canonical envelope.\n\n"
            "| ID | Description |\n"
            "|----|-------------|\n"
            "| AC-1 | GIVEN x WHEN y THEN z |\n"
        )
        findings = _check(text)
        self.assertTrue(any(f.kind == "BOLD_PSEUDO_HEADING_COLLISION" and f.criterion_id == "AC-2" for f in findings))


    def test_table_broken_suppresses_malformed_gwt_in_later_tables(self) -> None:
        # Regression for the AC precedence fix: when TABLE_BROKEN_AT_AC
        # fires, ALL rows in the second-and-later tables must be suppressed
        # (not just the straddling row). Only one TABLE_BROKEN_AT_AC
        # finding should be emitted, and the second fragment's malformed
        # rows should NOT generate MALFORMED_GWT findings.
        text = (
            "## Acceptance Criteria\n\n"
            "| ID | Description |\n"
            "|----|-------------|\n"
            "| AC-1 | GIVEN x WHEN y THEN z |\n\n"
            "```json\n"
            '{"interrupt": true}\n'
            "```\n\n"
            "| ID | Description |\n"
            "|----|-------------|\n"
            "| AC-2 | still malformed one |\n"
            "| AC-3 | still malformed two |\n"
            "| AC-4 | still malformed three |\n"
        )
        findings = _check(text)
        kinds = [f.kind for f in findings]
        self.assertEqual(kinds.count("TABLE_BROKEN_AT_AC"), 1)
        self.assertEqual(
            kinds.count("MALFORMED_GWT"),
            0,
            f"expected no MALFORMED_GWT findings in broken second table, got {findings}",
        )


if __name__ == "__main__":
    unittest.main()
