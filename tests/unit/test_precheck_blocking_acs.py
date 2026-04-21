"""Tests for the Non-Negotiable Acceptance Criteria precheck rule.

Phase D (2026-04-20): every spec must declare its blocking ACs — or
state a Justification paragraph explaining why none are blocking.
The rule is hard-fail; there is no grace period.
"""

import unittest

from darkfactory.spec.blocking_acs import parse_blocking_acs
from darkfactory.spec.precheck import check_non_negotiable_section
from darkfactory.spec.spec_model import parse_spec_model


_AC_TABLE = (
    "| ID | Criterion |\n"
    "|----|-----------|\n"
    "| AC-1 | GIVEN x WHEN y THEN z |\n"
    "| AC-4 | GIVEN a WHEN b THEN c |\n"
    "| EC-3 | GIVEN null WHEN submit THEN reject |\n"
)


def _check(body: str):
    text = f"## Acceptance Criteria\n\n{_AC_TABLE}\n{body}"
    return check_non_negotiable_section(parse_spec_model(text))


class TestNonNegotiableSection(unittest.TestCase):
    def test_missing_section_fails(self) -> None:
        text = f"## Acceptance Criteria\n\n{_AC_TABLE}"
        findings, parsed = check_non_negotiable_section(parse_spec_model(text))
        self.assertEqual([f.kind for f in findings], ["MISSING_SECTION"])
        self.assertEqual(parsed, {})

    def test_empty_section_without_justification_fails(self) -> None:
        findings, parsed = _check("## Non-Negotiable Acceptance Criteria\n\n")
        self.assertEqual([f.kind for f in findings], ["EMPTY_WITHOUT_JUSTIFICATION"])
        self.assertEqual(parsed, {})

    def test_empty_section_with_justification_passes(self) -> None:
        body = (
            "## Non-Negotiable Acceptance Criteria\n\n"
            "Justification: this spec is a pure refactor with no runtime invariants.\n"
        )
        findings, parsed = _check(body)
        self.assertEqual(findings, [])
        self.assertEqual(parsed, {})

    def test_resolved_ids_pass(self) -> None:
        body = (
            "## Non-Negotiable Acceptance Criteria\n\n"
            "- **AC-4** — tenant-isolation invariant.\n"
            "- **EC-3** — null tenant_id must reject.\n"
        )
        findings, parsed = _check(body)
        self.assertEqual(findings, [])
        self.assertEqual(parsed, {"AC-4": True, "EC-3": True})

    def test_unresolved_id_fails(self) -> None:
        body = (
            "## Non-Negotiable Acceptance Criteria\n\n"
            "- **AC-99** — this AC is not defined anywhere.\n"
        )
        findings, parsed = _check(body)
        kinds = [(f.kind, f.id) for f in findings]
        self.assertIn(("UNRESOLVED_ID", "AC-99"), kinds)
        # Parser still populates the map — runtime can still consume it,
        # even though the precheck has already failed.
        self.assertEqual(parsed, {"AC-99": True})

    def test_mixed_resolved_and_unresolved(self) -> None:
        body = (
            "## Non-Negotiable Acceptance Criteria\n\n"
            "- **AC-4** — real AC, resolves.\n"
            "- **AC-99** — fake AC, does not resolve.\n"
        )
        findings, parsed = _check(body)
        kinds = [(f.kind, f.id) for f in findings]
        self.assertEqual(kinds, [("UNRESOLVED_ID", "AC-99")])
        self.assertEqual(parsed, {"AC-4": True, "AC-99": True})

    def test_alternative_section_name_accepted(self) -> None:
        body = (
            "## Blocking Acceptance Criteria\n\n"
            "- **AC-4** — blocking.\n"
        )
        findings, parsed = _check(body)
        self.assertEqual(findings, [])
        self.assertEqual(parsed, {"AC-4": True})

    def test_numbered_prefix_on_section_name_stripped(self) -> None:
        body = (
            "## 8a. Non-Negotiable Acceptance Criteria\n\n"
            "- **AC-4** — blocking.\n"
        )
        findings, parsed = _check(body)
        self.assertEqual(findings, [])
        self.assertEqual(parsed, {"AC-4": True})


class TestParseBlockingAcs(unittest.TestCase):
    """The runtime parser (darkfactory.spec.blocking_acs) mirrors the
    precheck extractor but always returns a dict — never findings."""

    def test_empty_when_section_missing(self) -> None:
        text = "## Acceptance Criteria\n\n| ID | C |\n|----|---|\n| AC-1 | x |\n"
        self.assertEqual(parse_blocking_acs(parse_spec_model(text)), {})

    def test_populated_from_bullets(self) -> None:
        text = (
            "## Non-Negotiable Acceptance Criteria\n\n"
            "- **AC-4** — invariant.\n"
            "- **EC-3** — invariant.\n"
        )
        result = parse_blocking_acs(parse_spec_model(text))
        self.assertEqual(result, {"AC-4": True, "EC-3": True})

    def test_ids_uppercased(self) -> None:
        text = (
            "## Non-Negotiable Acceptance Criteria\n\n"
            "- **ac-4** — lowercase input.\n"
        )
        result = parse_blocking_acs(parse_spec_model(text))
        self.assertEqual(result, {"AC-4": True})

    def test_justification_prose_is_scanned(self) -> None:
        """Sloppy prose that names an ID in the justification paragraph
        counts — the precheck rule catches structural shape; the parser
        is intentionally promiscuous here."""
        text = (
            "## Non-Negotiable Acceptance Criteria\n\n"
            "Justification: we chose not to block AC-4 because ...\n"
        )
        result = parse_blocking_acs(parse_spec_model(text))
        self.assertEqual(result, {"AC-4": True})


if __name__ == "__main__":
    unittest.main()
