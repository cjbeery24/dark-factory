"""Tests for darkfactory.spec.validator_output."""

import unittest

from darkfactory.spec.validator_output import (
    audit_free_variables,
    parse_unresolved_contracts,
)


class TestParseUnresolvedContracts(unittest.TestCase):
    def test_empty_list(self) -> None:
        text = "Some report\n\n```yaml\nunresolved_contracts: []\n```\n"
        self.assertEqual(parse_unresolved_contracts(text), [])

    def test_non_empty_list(self) -> None:
        text = (
            "preamble\n\n"
            "```yaml\n"
            "unresolved_contracts:\n"
            "  - surface: error envelope\n"
            "    section: FR-3\n"
            "    sketches_diverged_on: details field shape\n"
            "```\n"
        )
        result = parse_unresolved_contracts(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["surface"], "error envelope")
        self.assertEqual(result[0]["section"], "FR-3")

    def test_missing_block_returns_none(self) -> None:
        text = "## Report\n\nNo YAML block here.\n"
        self.assertIsNone(parse_unresolved_contracts(text))

    def test_empty_input_returns_none(self) -> None:
        self.assertIsNone(parse_unresolved_contracts(""))

    def test_markdown_noise_around_block(self) -> None:
        text = (
            "# Validation Report\n\n"
            "Bunch of text. Headings. Tables.\n\n"
            "## Conclusion\n\n"
            "All good.\n\n"
            "```yaml\n"
            "unresolved_contracts: []\n"
            "```\n\n"
            "Trailing prose.\n"
        )
        self.assertEqual(parse_unresolved_contracts(text), [])

    def test_last_yaml_block_wins(self) -> None:
        # If two YAML blocks both contain unresolved_contracts (e.g., the
        # validator wrote a draft and then the final), the last one wins.
        text = (
            "```yaml\nunresolved_contracts: []\n```\n\n"
            "Then I changed my mind:\n\n"
            "```yaml\n"
            "unresolved_contracts:\n"
            "  - surface: log line\n"
            "```\n"
        )
        result = parse_unresolved_contracts(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["surface"], "log line")

    def test_unrelated_yaml_block_ignored(self) -> None:
        text = (
            "```yaml\n"
            "score: 92\n"
            "verdict: PASS\n"
            "```\n"
        )
        self.assertIsNone(parse_unresolved_contracts(text))

    def test_bare_string_entry_normalised(self) -> None:
        text = (
            "```yaml\n"
            "unresolved_contracts:\n"
            "  - error envelope shape\n"
            "```\n"
        )
        result = parse_unresolved_contracts(text)
        self.assertEqual(result, [{"surface": "error envelope shape"}])


class TestAuditFreeVariables(unittest.TestCase):
    def test_no_section(self) -> None:
        self.assertEqual(audit_free_variables("Just a report.\n"), [])

    def test_clean_section(self) -> None:
        text = (
            "## Free Variables\n\n"
            "| ID | Variable | Constrained | Free |\n"
            "|----|----------|-------------|------|\n"
            "| FV-1 | sort algorithm | result is sorted | choice of algorithm |\n"
            "| FV-2 | cache backend | TTL=60s | in-memory vs disk |\n"
        )
        self.assertEqual(audit_free_variables(text), [])

    def test_misclassified_envelope_caught(self) -> None:
        text = (
            "## Free Variables\n\n"
            "| ID | Variable | Constrained | Free |\n"
            "|----|----------|-------------|------|\n"
            "| FV-1 | error envelope | must contain error code | JSON key naming |\n"
        )
        offenders = audit_free_variables(text)
        self.assertEqual(len(offenders), 1)
        self.assertIn("envelope", offenders[0].lower())

    def test_misclassified_log_line_caught(self) -> None:
        text = (
            "## Free Variables\n\n"
            "| ID | Variable | Constrained | Free |\n"
            "|----|----------|-------------|------|\n"
            "| FV-1 | request log line | must include requestId | exact field order |\n"
        )
        offenders = audit_free_variables(text)
        self.assertEqual(len(offenders), 1)

    def test_section_ends_at_next_heading(self) -> None:
        # A token in a later section must not be flagged.
        text = (
            "## Free Variables\n\n"
            "| ID | Variable | Constrained | Free |\n"
            "|----|----------|-------------|------|\n"
            "| FV-1 | sort algorithm | result is sorted | choice of algorithm |\n\n"
            "## Other\n\n"
            "Some json envelope discussion here.\n"
        )
        self.assertEqual(audit_free_variables(text), [])


if __name__ == "__main__":
    unittest.main()
