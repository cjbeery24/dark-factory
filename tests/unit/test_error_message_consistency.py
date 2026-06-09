"""Tests for the deterministic error-code → message consistency lint (Sprint 3b).

Catches the class the LLM validator detects only ~2/4 of the time: the same
error code mapped to two different literal message strings across spec sections.
"""

from __future__ import annotations

import unittest

from darkfactory.spec.error_message_consistency import (
    validate_error_message_consistency,
)


def _codes(spec: str) -> set[str]:
    return {v["code"] for v in validate_error_message_consistency(spec)}


class TestErrorMessageConsistency(unittest.TestCase):
    # --- the real failure: same code, two messages across sources -----------
    def test_object_literal_conflict_flagged(self) -> None:
        # §5.5 wire-contract form vs §10 example form — both object-literal.
        spec = (
            '| `"1.234"` | `{ ok: false, error: { code: "TOO_MANY_DECIMALS", '
            'message: "Amount must have at most 2 decimal places" } }` |\n'
            '// error: { code: "TOO_MANY_DECIMALS", '
            'message: "At most 2 decimal places are allowed" }\n'
        )
        v = validate_error_message_consistency(spec)
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["code"], "TOO_MANY_DECIMALS")
        self.assertEqual(len(v[0]["messages"]), 2)

    def test_normative_table_vs_example_conflict(self) -> None:
        # §5 normative message table vs a §10 example object literal.
        spec = (
            "| Code | `error.message` (exact) |\n"
            "|---|---|\n"
            '| `TOO_MANY_DECIMALS` | `"Amount must have at most 2 decimal places"` |\n'
            '// { code: "TOO_MANY_DECIMALS", message: "At most 2 decimal places are allowed" }\n'
        )
        self.assertEqual(_codes(spec), {"TOO_MANY_DECIMALS"})

    # --- consistent spec: no false positive ---------------------------------
    def test_consistent_code_not_flagged(self) -> None:
        spec = (
            "| Code | `error.message` (exact) |\n"
            "|---|---|\n"
            '| `EMPTY` | `"Amount is required"` |\n'
            '| `TOO_MANY_DECIMALS` | `"Amount must have at most 2 decimal places"` |\n'
            '// { code: "EMPTY", message: "Amount is required" }\n'
            '// { code: "TOO_MANY_DECIMALS", message: "Amount must have at most 2 decimal places" }\n'
        )
        self.assertEqual(validate_error_message_consistency(spec), [])

    # --- precision guards (Neo's false-positive cases) ----------------------
    def test_fr_intent_table_not_a_false_positive(self) -> None:
        # FR intent table pairs a backticked CODE with a quoted INPUT, not a
        # message. Header has no "message" column → excluded.
        spec = (
            "| # | Intent | Layer | AC |\n"
            "|---|--------|-------|----|\n"
            '| 3 | `parseAmount("abc")` returns `NOT_A_NUMBER` | error | |\n'
            '| 5 | `parseAmount("$$100")` returns `NOT_A_NUMBER` | error | |\n'
        )
        self.assertEqual(validate_error_message_consistency(spec), [])

    def test_input_output_table_only_counts_object_literal(self) -> None:
        # A | Input | Output | table: the quoted INPUT in col 1 must not be
        # treated as a message; only the code:/message: object literal counts.
        spec = (
            "| Input | Output |\n"
            "|---|---|\n"
            '| `"abc"` | `{ ok: false, error: { code: "NOT_A_NUMBER", '
            'message: "Amount must be a number" } }` |\n'
            '| `"1.2.3"` | `{ ok: false, error: { code: "NOT_A_NUMBER", '
            'message: "Amount must be a number" } }` |\n'
        )
        # NOT_A_NUMBER resolves to exactly ONE message ("Amount must be a
        # number"); the inputs "abc"/"1.2.3" are not captured → no violation.
        self.assertEqual(validate_error_message_consistency(spec), [])

    def test_placeholder_message_not_counted(self) -> None:
        # `message: ...` ellipsis and <non-empty> placeholder must not count as
        # a distinct message.
        spec = (
            '| `EMPTY` is `{ code: "EMPTY", message: "Amount is required" }` |\n'
            '// { code: "EMPTY", message: ... }\n'
        )
        self.assertEqual(validate_error_message_consistency(spec), [])

    # --- single-word codes are in scope (FIX-IT regression) -----------------
    def test_single_word_code_conflict_flagged(self) -> None:
        # EMPTY / TIMEOUT have no underscore — they must still be validated.
        spec = (
            "| Code | `message` |\n"
            "|---|---|\n"
            '| `EMPTY` | `"Amount is required"` |\n'
            '// { code: "EMPTY", message: "Field must not be blank" }\n'
        )
        v = validate_error_message_consistency(spec)
        self.assertEqual(len(v), 1)
        self.assertEqual(v[0]["code"], "EMPTY")

    def test_single_word_code_consistent_not_flagged(self) -> None:
        spec = (
            "| Code | `message` |\n"
            "|---|---|\n"
            '| `TIMEOUT` | `"Request timed out"` |\n'
            '// { code: "TIMEOUT", message: "Request timed out" }\n'
        )
        self.assertEqual(validate_error_message_consistency(spec), [])

    def test_empty_spec(self) -> None:
        self.assertEqual(validate_error_message_consistency(""), [])

    def test_no_codes(self) -> None:
        self.assertEqual(
            validate_error_message_consistency("Just prose, no error codes here."),
            [],
        )

    def test_table_without_message_header_excluded(self) -> None:
        # A code table whose second column is a CONDITION (prose), not a message.
        spec = (
            "| Code | Condition |\n"
            "|---|---|\n"
            "| `OUT_OF_RANGE` | The computed cents value fails `Number.isSafeInteger` |\n"
        )
        self.assertEqual(validate_error_message_consistency(spec), [])

    def test_multiple_codes_each_consistent(self) -> None:
        spec = (
            "| Code | `message` |\n"
            "|---|---|\n"
            '| `EMPTY` | `"Amount is required"` |\n'
            '| `NOT_A_NUMBER` | `"Amount must be a number"` |\n'
            '| `OUT_OF_RANGE` | `"Amount is out of the allowed range"` |\n'
        )
        self.assertEqual(validate_error_message_consistency(spec), [])


if __name__ == "__main__":
    unittest.main()
