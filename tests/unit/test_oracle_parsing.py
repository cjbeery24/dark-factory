"""Tests for _parse_oracle_decision — pure function, no I/O.

This is the SHIP-GATE verdict parser. A false-SHIP (shipping an oracle that
meant NO-SHIP) is catastrophic; a false-NO-SHIP only escalates a good run.
The parser is therefore fail-closed and NO-SHIP-dominant: it ships ONLY on a
bare standalone ``SHIP`` token with NO standalone ``NO-SHIP`` token anywhere.
"""

import json
import unittest

from darkfactory.pipeline.iteration import _parse_oracle_decision


class TestParseOracleDecision(unittest.TestCase):
    # --- happy paths -----------------------------------------------------
    def test_ship_exact(self) -> None:
        self.assertEqual(_parse_oracle_decision("SHIP"), "SHIP")

    def test_ship_whitespace(self) -> None:
        self.assertEqual(_parse_oracle_decision("  SHIP  "), "SHIP")

    def test_ship_lowercase(self) -> None:
        self.assertEqual(_parse_oracle_decision("ship"), "SHIP")

    def test_no_ship(self) -> None:
        self.assertEqual(_parse_oracle_decision("NO-SHIP"), "NO-SHIP")

    def test_ship_markdown_bold(self) -> None:
        self.assertEqual(_parse_oracle_decision("**SHIP**\n\nDetails"), "SHIP")

    def test_ship_markdown_italic(self) -> None:
        self.assertEqual(_parse_oracle_decision("_SHIP_\n\nDetails"), "SHIP")

    def test_no_ship_markdown_bold(self) -> None:
        self.assertEqual(_parse_oracle_decision("**NO-SHIP**\n\nDetails"), "NO-SHIP")

    # --- prose-preamble recovery (the Sprint 20 bug) ---------------------
    def test_multiline_ship_on_later_line(self) -> None:
        # BEHAVIOR CHANGE (Sprint 20): a standalone SHIP after a prose preamble
        # now ships. The old parser read only the first non-empty line and
        # discarded this as NO-SHIP, escalating clean oracle-approved ships.
        self.assertEqual(_parse_oracle_decision("Summary\n\nSHIP"), "SHIP")

    def test_prose_preamble_before_ship_real_run(self) -> None:
        # The exact Tier-C de-risk #1 escalation: oracle decided SHIP but led
        # with a prose sentence; the bare SHIP token is on a later line.
        self.assertEqual(
            _parse_oracle_decision(
                "Based on my analysis, this is a clear **SHIP** decision.\n\n"
                "SHIP\n\n**Spec:** notes-crud-pg\nRationale..."
            ),
            "SHIP",
        )

    def test_prose_preamble_before_no_ship(self) -> None:
        self.assertEqual(
            _parse_oracle_decision(
                "Looking at everything carefully...\n\nNO-SHIP\n\nBlocker: FR-3"
            ),
            "NO-SHIP",
        )

    # --- fail-closed defaults --------------------------------------------
    def test_empty_output(self) -> None:
        self.assertEqual(_parse_oracle_decision(""), "NO-SHIP")

    def test_none_output_fails_closed(self) -> None:
        # An upstream extraction failure must not crash the ship gate.
        self.assertEqual(_parse_oracle_decision(None), "NO-SHIP")  # type: ignore[arg-type]

    def test_arbitrary_first_line(self) -> None:
        self.assertEqual(_parse_oracle_decision("Looking good"), "NO-SHIP")

    def test_no_standalone_token_fails_closed(self) -> None:
        self.assertEqual(
            _parse_oracle_decision("I will not ship this.\n\nThe blockers remain."),
            "NO-SHIP",
        )

    def test_raw_json_returns_noship(self) -> None:
        """Parser expects pre-extracted text; raw JSON is the caller's bug."""
        raw = json.dumps({"type": "result", "result": "SHIP\n\nApproved."})
        self.assertEqual(_parse_oracle_decision(raw), "NO-SHIP")

    # --- false-SHIP guards (gate-found; the catastrophic direction) ------
    def test_suffix_ship_colon_is_non_compliant_fails_closed(self) -> None:
        # Suffixed verdicts are NOT matched (prompt mandates a bare token); a
        # 'SHIP:' prefix over a multi-line scan would false-match prose, so it
        # fails closed.
        self.assertEqual(_parse_oracle_decision("SHIP: approved"), "NO-SHIP")

    def test_no_ship_with_suffix_still_no_ship(self) -> None:
        self.assertEqual(_parse_oracle_decision("NO-SHIP: needs work"), "NO-SHIP")

    def test_ship_colon_in_prose_does_not_false_ship(self) -> None:
        # Linus C1: a 'SHIP:' conditional line inside a NO-SHIP rationale must
        # NOT ship.
        self.assertEqual(
            _parse_oracle_decision(
                "Based on my analysis, I considered whether to ship.\n"
                "SHIP: would be acceptable only if FR-3 were resolved.\n\n"
                "NO-SHIP"
            ),
            "NO-SHIP",
        )

    def test_bold_ship_colon_label_does_not_false_ship(self) -> None:
        # Linus C1 bold variant: '**SHIP:**' strips to 'SHIP:' — must not ship.
        self.assertEqual(
            _parse_oracle_decision("**SHIP:** pending\n\nNO-SHIP"), "NO-SHIP"
        )

    def test_ship_heading_is_not_a_verdict(self) -> None:
        # Codex F1: '#' is NOT stripped, so a '## SHIP' heading before a NO-SHIP
        # verdict cannot false-ship.
        self.assertEqual(
            _parse_oracle_decision("## SHIP\n\nNO-SHIP: blocker remains"), "NO-SHIP"
        )

    def test_ship_blockers_prose_does_not_false_ship(self) -> None:
        self.assertEqual(
            _parse_oracle_decision("SHIP blockers:\nFix FR-3 first.\n\nNO-SHIP"),
            "NO-SHIP",
        )

    def test_ship_word_in_prose_does_not_false_ship(self) -> None:
        self.assertEqual(
            _parse_oracle_decision(
                "NO-SHIP\n\nThe implementation could SHIP if FR-3 were fixed."
            ),
            "NO-SHIP",
        )

    # --- NO-SHIP dominates regardless of order ---------------------------
    def test_no_ship_first_then_ship(self) -> None:
        self.assertEqual(_parse_oracle_decision("NO-SHIP\n\nSHIP\n"), "NO-SHIP")

    def test_ship_first_then_no_ship_no_ship_wins(self) -> None:
        # A stray earlier SHIP line must never override a real standalone NO-SHIP.
        self.assertEqual(_parse_oracle_decision("SHIP\n\nNO-SHIP\n"), "NO-SHIP")

    # --- markdown-near-miss guards (anchored match, not strip-then-compare) ---
    def test_bullet_ship_is_not_a_verdict(self) -> None:
        # '* SHIP' is a bullet, not a verdict: the '* ' (space after) is not an
        # adjacent emphasis wrap, so it must NOT strip to a bare SHIP token.
        self.assertEqual(_parse_oracle_decision("* SHIP"), "NO-SHIP")

    def test_bullet_ship_before_no_ship_does_not_false_ship(self) -> None:
        self.assertEqual(_parse_oracle_decision("* SHIP\n\nNO-SHIP"), "NO-SHIP")

    def test_backtick_ship_is_a_verdict(self) -> None:
        self.assertEqual(_parse_oracle_decision("`SHIP`"), "SHIP")

    def test_double_underscore_ship_is_a_verdict(self) -> None:
        self.assertEqual(_parse_oracle_decision("__SHIP__"), "SHIP")

    def test_unclosed_emphasis_is_not_a_verdict(self) -> None:
        # '**SHIP' (no closing marker) must NOT match — symmetric markers only.
        self.assertEqual(_parse_oracle_decision("**SHIP"), "NO-SHIP")
        self.assertEqual(_parse_oracle_decision("**SHIP\n\nNO-SHIP"), "NO-SHIP")

    def test_mismatched_emphasis_is_not_a_verdict(self) -> None:
        # '*SHIP_' (open '*', close '_') must NOT match.
        self.assertEqual(_parse_oracle_decision("*SHIP_"), "NO-SHIP")

    def test_other_list_and_quote_markers_not_verdicts(self) -> None:
        for line in ("> SHIP", "- SHIP", "1. SHIP", "***SHIP***", "SHIP (see above)"):
            self.assertEqual(_parse_oracle_decision(line), "NO-SHIP", line)

    # --- non-str / whitespace fail-closed ---
    def test_non_str_input_fails_closed(self) -> None:
        self.assertEqual(_parse_oracle_decision(42), "NO-SHIP")  # type: ignore[arg-type]
        self.assertEqual(_parse_oracle_decision(["SHIP"]), "NO-SHIP")  # type: ignore[arg-type]

    def test_whitespace_only_fails_closed(self) -> None:
        self.assertEqual(_parse_oracle_decision("   \n\t\n"), "NO-SHIP")


if __name__ == "__main__":
    unittest.main()
