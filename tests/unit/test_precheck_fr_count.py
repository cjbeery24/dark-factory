"""Tests for check_fr_count in spec/precheck.py."""

import unittest

from darkfactory.spec.precheck import check_fr_count


class TestCheckFrCount(unittest.TestCase):
    def test_single_fr(self) -> None:
        text = "## 2. Functional Requirements\n\n### FR-1: Do something\n"
        count, lines = check_fr_count(text)
        self.assertEqual(count, 1)
        self.assertEqual(lines, [3])

    def test_five_frs(self) -> None:
        frs = "\n".join(f"### FR-{i}: Requirement {i}" for i in range(1, 6))
        text = f"## Functional Requirements\n\n{frs}\n"
        count, _ = check_fr_count(text)
        self.assertEqual(count, 5)

    def test_nine_frs(self) -> None:
        frs = "\n".join(f"### FR-{i}: Requirement {i}" for i in range(1, 10))
        text = f"## Functional Requirements\n\n{frs}\n"
        count, _ = check_fr_count(text)
        self.assertEqual(count, 9)

    def test_no_frs(self) -> None:
        text = "## Problem Statement\n\nJust a problem.\n"
        count, lines = check_fr_count(text)
        self.assertEqual(count, 0)
        self.assertEqual(lines, [])

    def test_frs_inside_code_fence_excluded(self) -> None:
        text = (
            "## Functional Requirements\n\n"
            "### FR-1: Real requirement\n\n"
            "```markdown\n"
            "### FR-2: This is inside a code fence\n"
            "### FR-3: Also inside\n"
            "```\n\n"
            "### FR-2: Another real requirement\n"
        )
        count, _ = check_fr_count(text)
        self.assertEqual(count, 2)

    def test_h2_level_fr_headings(self) -> None:
        """FR headings at ## level should also be counted."""
        text = "## FR-1: Top level FR\n\n## FR-2: Another\n"
        count, _ = check_fr_count(text)
        self.assertEqual(count, 2)

    def test_case_insensitive(self) -> None:
        text = "### fr-1: lowercase\n### FR-2: uppercase\n"
        count, _ = check_fr_count(text)
        self.assertEqual(count, 2)

    def test_line_numbers_are_one_based(self) -> None:
        text = "# Spec\n\n### FR-1: First\n\n### FR-2: Second\n"
        _, lines = check_fr_count(text)
        self.assertEqual(lines, [3, 5])


if __name__ == "__main__":
    unittest.main()
