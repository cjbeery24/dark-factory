"""Tests for _parse_oracle_decision — pure function, no I/O."""

import json
import unittest

from darkfactory.pipeline.iteration import _parse_oracle_decision


class TestParseOracleDecision(unittest.TestCase):
    def test_ship_exact(self) -> None:
        self.assertEqual(_parse_oracle_decision("SHIP"), "SHIP")

    def test_ship_with_suffix(self) -> None:
        self.assertEqual(_parse_oracle_decision("SHIP: approved"), "SHIP")

    def test_ship_whitespace(self) -> None:
        self.assertEqual(_parse_oracle_decision("  SHIP  "), "SHIP")

    def test_ship_lowercase(self) -> None:
        self.assertEqual(_parse_oracle_decision("ship"), "SHIP")

    def test_no_ship(self) -> None:
        self.assertEqual(_parse_oracle_decision("NO-SHIP"), "NO-SHIP")

    def test_empty_output(self) -> None:
        self.assertEqual(_parse_oracle_decision(""), "NO-SHIP")

    def test_multiline_ship_on_later_line(self) -> None:
        self.assertEqual(
            _parse_oracle_decision("Summary\n\nSHIP"), "NO-SHIP"
        )

    def test_arbitrary_first_line(self) -> None:
        self.assertEqual(_parse_oracle_decision("Looking good"), "NO-SHIP")

    def test_ship_markdown_bold(self) -> None:
        self.assertEqual(_parse_oracle_decision("**SHIP**\n\nDetails"), "SHIP")

    def test_ship_markdown_italic(self) -> None:
        self.assertEqual(_parse_oracle_decision("_SHIP_\n\nDetails"), "SHIP")

    def test_no_ship_markdown_bold(self) -> None:
        self.assertEqual(_parse_oracle_decision("**NO-SHIP**\n\nDetails"), "NO-SHIP")

    def test_no_ship_with_suffix(self) -> None:
        self.assertEqual(_parse_oracle_decision("NO-SHIP: needs work"), "NO-SHIP")

    def test_raw_json_returns_noship(self) -> None:
        """Parser expects pre-extracted text; raw JSON is the caller's bug."""
        raw = json.dumps({"type": "result", "result": "SHIP\n\nApproved."})
        self.assertEqual(_parse_oracle_decision(raw), "NO-SHIP")


if __name__ == "__main__":
    unittest.main()
