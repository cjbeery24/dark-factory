"""Regression for the substrate-verifier status mapping.

The verifier used to emit ``"fail"`` for ANY non-empty findings list,
ignoring severity — so the first non-error (info/warning) finding ever
added would spuriously fail the substrate gate. ``_status_for`` now keys
on error-severity only.
"""
from __future__ import annotations

import unittest

from darkfactory.adapters.base import Finding
from darkfactory.pipeline.substrate_verifier_entry import _status_for


class TestStatusFor(unittest.TestCase):
    def test_none_is_skip(self) -> None:
        self.assertEqual(_status_for(None), "skip")

    def test_empty_is_pass(self) -> None:
        self.assertEqual(_status_for([]), "pass")

    def test_error_finding_is_fail(self) -> None:
        self.assertEqual(
            _status_for([Finding(severity="error", code="x", message="m")]),
            "fail",
        )

    def test_info_only_is_pass(self) -> None:
        # THE regression: a non-error finding must NOT fail the gate.
        self.assertEqual(
            _status_for([Finding(severity="info", code="snap", message="m")]),
            "pass",
        )

    def test_warning_only_is_pass(self) -> None:
        self.assertEqual(
            _status_for([Finding(severity="warning", code="w", message="m")]),
            "pass",
        )

    def test_error_plus_info_is_fail(self) -> None:
        self.assertEqual(
            _status_for([
                Finding(severity="error", code="x", message="m"),
                Finding(severity="info", code="snap", message="m"),
            ]),
            "fail",
        )

    def test_unreadable_severity_fails_closed(self) -> None:
        # An item whose severity can't be read is treated as an error.
        self.assertEqual(_status_for([object()]), "fail")


if __name__ == "__main__":
    unittest.main()
