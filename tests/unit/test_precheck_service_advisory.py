"""Phase 4.4 — service advisory detection in spec precheck."""

from __future__ import annotations

import unittest

from darkfactory.spec.precheck import (
    ServiceAdvisoryFinding,
    check_service_references,
)


class TestCheckServiceReferences(unittest.TestCase):
    def test_detects_postgres_reference(self) -> None:
        text = "## FR-1\nThe app stores data in PostgreSQL.\n"
        findings = check_service_references(text, frozenset())
        assert len(findings) == 1
        assert findings[0].service_keyword == "PostgreSQL"
        assert findings[0].line_number == 2

    def test_ignores_service_already_in_contract(self) -> None:
        text = "## FR-1\nUse PostgreSQL for persistence.\n"
        findings = check_service_references(text, frozenset({"postgres"}))
        assert len(findings) == 0

    def test_detects_multiple_services(self) -> None:
        text = "Use PostgreSQL and Redis for caching.\n"
        findings = check_service_references(text, frozenset())
        names = {f.service_keyword.lower() for f in findings}
        assert "postgresql" in names
        assert "redis" in names

    def test_deduplicates_same_canonical(self) -> None:
        text = "PostgreSQL on line 1.\npostgres on line 2.\npg_isready on line 3.\n"
        findings = check_service_references(text, frozenset())
        # All three map to canonical "postgres" — only the first match is reported.
        assert len(findings) == 1

    def test_case_insensitive(self) -> None:
        text = "We need REDIS for caching.\n"
        findings = check_service_references(text, frozenset())
        assert len(findings) == 1
        assert findings[0].service_keyword == "REDIS"

    def test_no_false_positive_on_substring(self) -> None:
        """'redis' inside 'predisposition' should not match."""
        text = "The user has a predisposition to use databases.\n"
        findings = check_service_references(text, frozenset())
        assert len(findings) == 0

    def test_empty_text(self) -> None:
        assert check_service_references("", frozenset()) == []

    def test_context_truncated(self) -> None:
        long_line = "PostgreSQL " + "x" * 200
        findings = check_service_references(long_line, frozenset())
        assert len(findings) == 1
        assert len(findings[0].context) <= 120


if __name__ == "__main__":
    unittest.main()
