"""Phase 4 B4: round-trip validator for §4a forbidden_test_shapes.

The validator runs each shape's regex against its declared
``canonical_violation`` (if any — must match) and ``canonical_clean``
(if any — must not match). Catches the database-foundation Phase 1
H2 failure mode where wrong-dialect SQL regexes silently never bit
TS scenarios.

Tests two surfaces:

1. **Parser extension**: ``ForbiddenTestShape`` carries the new
   ``canonical_violation`` and ``canonical_clean`` fields when
   present in YAML; absence stays None; non-string values become
   parse errors.

2. **Validator behavior**: ``validate_forbidden_test_shape_round_trip``
   reports nothing when entries pass / no canonical fields; reports
   pattern-doesn't-match-violation; reports pattern-matches-clean;
   reports regex-compilation-failure as well.

3. **Pipeline integration**: ``extract_negative_space`` populates
   ``NegativeSpaceFindings.round_trip_errors`` from the validator.
"""
from __future__ import annotations

import re
import unittest

from darkfactory.spec.negative_space import (
    ForbiddenTestShape,
    extract_negative_space,
    validate_forbidden_test_shape_round_trip,
)


class TestParserExtensionRecognizesCanonicalFields(unittest.TestCase):
    """The parser populates the new fields when YAML carries them."""

    def _section_4a(self, body: str) -> str:
        return f"## 4a. Negative Space\n\n```yaml\n{body}\n```\n"

    def test_canonical_violation_extracted(self) -> None:
        spec_text = self._section_4a(
            "forbidden_test_shapes:\n"
            "  - pattern: 'foo'\n"
            "    reason: 'bar'\n"
            "    canonical_violation: |\n"
            "      const x = foo();\n"
        )
        findings = extract_negative_space(spec_text)
        self.assertEqual(len(findings.forbidden_test_shapes), 1)
        shape = findings.forbidden_test_shapes[0]
        self.assertEqual(shape.canonical_violation, "const x = foo();\n")
        self.assertIsNone(shape.canonical_clean)

    def test_canonical_clean_extracted(self) -> None:
        spec_text = self._section_4a(
            "forbidden_test_shapes:\n"
            "  - pattern: 'foo'\n"
            "    reason: 'bar'\n"
            "    canonical_clean: |\n"
            "      const y = bar();\n"
        )
        findings = extract_negative_space(spec_text)
        shape = findings.forbidden_test_shapes[0]
        self.assertIsNone(shape.canonical_violation)
        self.assertEqual(shape.canonical_clean, "const y = bar();\n")

    def test_both_fields_extracted(self) -> None:
        spec_text = self._section_4a(
            "forbidden_test_shapes:\n"
            "  - pattern: 'foo'\n"
            "    reason: 'bar'\n"
            "    canonical_violation: 'foo()'\n"
            "    canonical_clean: 'bar()'\n"
        )
        findings = extract_negative_space(spec_text)
        shape = findings.forbidden_test_shapes[0]
        self.assertEqual(shape.canonical_violation, "foo()")
        self.assertEqual(shape.canonical_clean, "bar()")

    def test_absent_fields_default_to_none(self) -> None:
        spec_text = self._section_4a(
            "forbidden_test_shapes:\n"
            "  - pattern: 'foo'\n"
            "    reason: 'bar'\n"
        )
        findings = extract_negative_space(spec_text)
        shape = findings.forbidden_test_shapes[0]
        self.assertIsNone(shape.canonical_violation)
        self.assertIsNone(shape.canonical_clean)

    def test_non_string_canonical_violation_is_parse_error(self) -> None:
        spec_text = self._section_4a(
            "forbidden_test_shapes:\n"
            "  - pattern: 'foo'\n"
            "    reason: 'bar'\n"
            "    canonical_violation: 42\n"
        )
        findings = extract_negative_space(spec_text)
        self.assertEqual(len(findings.parse_errors), 1)
        self.assertIn("canonical_violation", findings.parse_errors[0])
        self.assertIn("must be a string", findings.parse_errors[0])

    def test_non_string_canonical_clean_is_parse_error(self) -> None:
        spec_text = self._section_4a(
            "forbidden_test_shapes:\n"
            "  - pattern: 'foo'\n"
            "    reason: 'bar'\n"
            "    canonical_clean: ['list', 'not allowed']\n"
        )
        findings = extract_negative_space(spec_text)
        self.assertEqual(len(findings.parse_errors), 1)
        self.assertIn("canonical_clean", findings.parse_errors[0])


class TestValidatorBehavior(unittest.TestCase):
    """``validate_forbidden_test_shape_round_trip`` reports the
    five-class set: nothing-on-success, no-canonical-fields-skip,
    violation-mismatch, clean-match, regex-compile-fail."""

    def test_no_errors_when_no_canonical_fields(self) -> None:
        shapes = [
            ForbiddenTestShape(
                pattern="foo",
                reason="r",
                applies_to=["scenarios"],
            ),
        ]
        self.assertEqual(
            validate_forbidden_test_shape_round_trip(shapes), [],
        )

    def test_no_errors_when_violation_matches(self) -> None:
        shapes = [
            ForbiddenTestShape(
                pattern=r"\bfoo\(",
                reason="r",
                canonical_violation="const x = foo();",
            ),
        ]
        self.assertEqual(
            validate_forbidden_test_shape_round_trip(shapes), [],
        )

    def test_error_when_violation_does_not_match(self) -> None:
        shapes = [
            ForbiddenTestShape(
                pattern=r"\bbaz\(",  # wrong regex
                reason="r",
                canonical_violation="const x = foo();",
            ),
        ]
        errors = validate_forbidden_test_shape_round_trip(shapes)
        self.assertEqual(len(errors), 1)
        self.assertIn("does NOT match", errors[0])
        self.assertIn("canonical_violation", errors[0])

    def test_no_errors_when_clean_does_not_match(self) -> None:
        shapes = [
            ForbiddenTestShape(
                pattern=r"\bfoo\(",
                reason="r",
                canonical_clean="const x = bar();",
            ),
        ]
        self.assertEqual(
            validate_forbidden_test_shape_round_trip(shapes), [],
        )

    def test_error_when_clean_matches(self) -> None:
        shapes = [
            ForbiddenTestShape(
                pattern=r"\bfoo",  # too broad — also matches `foobar`
                reason="r",
                canonical_clean="const x = foobar();",
            ),
        ]
        errors = validate_forbidden_test_shape_round_trip(shapes)
        self.assertEqual(len(errors), 1)
        self.assertIn("MATCHES", errors[0])
        self.assertIn("canonical_clean", errors[0])

    def test_error_when_regex_does_not_compile(self) -> None:
        shapes = [
            ForbiddenTestShape(
                pattern="(unclosed",  # invalid regex
                reason="r",
                canonical_violation="anything",
            ),
        ]
        errors = validate_forbidden_test_shape_round_trip(shapes)
        self.assertEqual(len(errors), 1)
        self.assertIn("regex compilation failed", errors[0])

    def test_both_violation_mismatch_and_clean_match_reported(self) -> None:
        """A single shape can fail both directions — both reported."""
        shapes = [
            ForbiddenTestShape(
                pattern=r"\bfoo",  # matches both violation-target and clean
                reason="r",
                canonical_violation="bar()",  # doesn't match
                canonical_clean="foobar()",  # DOES match
            ),
        ]
        errors = validate_forbidden_test_shape_round_trip(shapes)
        self.assertEqual(len(errors), 2)
        self.assertTrue(any("does NOT match" in e for e in errors))
        self.assertTrue(any("MATCHES" in e for e in errors))

    def test_error_message_includes_index_and_pattern(self) -> None:
        """A spec with N entries; an error on the 3rd entry must
        say [2] (zero-indexed) so the spec author can find it."""
        shapes = [
            ForbiddenTestShape(pattern="ok1", reason="r"),
            ForbiddenTestShape(pattern="ok2", reason="r"),
            ForbiddenTestShape(
                pattern="bad-pattern",
                reason="r",
                canonical_violation="not matching",
            ),
        ]
        errors = validate_forbidden_test_shape_round_trip(shapes)
        self.assertEqual(len(errors), 1)
        self.assertIn("forbidden_test_shapes[2]", errors[0])
        self.assertIn("bad-pattern", errors[0])


class TestPipelineIntegration(unittest.TestCase):
    """``extract_negative_space`` calls the validator and populates
    ``NegativeSpaceFindings.round_trip_errors``."""

    def test_clean_spec_yields_empty_round_trip_errors(self) -> None:
        spec_text = (
            "## 4a. Negative Space\n\n"
            "```yaml\n"
            "forbidden_test_shapes:\n"
            "  - pattern: '\\bfoo\\('\n"
            "    reason: 'r'\n"
            "    canonical_violation: 'foo()'\n"
            "```\n"
        )
        findings = extract_negative_space(spec_text)
        self.assertEqual(findings.round_trip_errors, [])

    def test_drift_spec_yields_round_trip_error(self) -> None:
        """A spec with a regex that doesn't match its
        canonical_violation surfaces in round_trip_errors."""
        spec_text = (
            "## 4a. Negative Space\n\n"
            "```yaml\n"
            "forbidden_test_shapes:\n"
            "  - pattern: '\\bbaz\\('\n"
            "    reason: 'wrong dialect — claims to forbid foo() but won''t bite'\n"
            "    canonical_violation: 'foo()'\n"
            "```\n"
        )
        findings = extract_negative_space(spec_text)
        self.assertEqual(len(findings.round_trip_errors), 1)
        self.assertIn("does NOT match", findings.round_trip_errors[0])
        # Parse errors stay clean — round-trip is a separate surface.
        self.assertEqual(findings.parse_errors, [])

    def test_database_foundation_canonical_violation_pin(self) -> None:
        """Reconstruction of the database-foundation Phase 1 H2 case:
        a regex authored against SQL operators (=, ::, IS NULL) when
        the scenarios are TS (===, !==, toBe()). The spec's regex
        never bites because the scenarios use TS syntax. With B4's
        canonical_violation, the validator catches this at spec-
        authoring time instead of letting it ship a broken §4a."""
        spec_text = (
            "## 4a. Negative Space\n\n"
            "```yaml\n"
            "forbidden_test_shapes:\n"
            "  - pattern: 'current_setting\\([^)]*\\)\\s*=\\s*NULL'\n"
            "    reason: 'wrong-dialect SQL — TS uses === not ='\n"
            "    canonical_violation: |\n"
            "      const v = await client`SELECT current_setting('app.x', true)`;\n"
            "      expect(v[0]?.v).toBe(null);\n"
            "```\n"
        )
        findings = extract_negative_space(spec_text)
        self.assertEqual(
            len(findings.round_trip_errors), 1,
            "the SQL-dialect regex must NOT match the TS canonical "
            "violation — that's the H2 failure mode B4 exists to catch."
        )

    def test_round_trip_errors_are_fatal_at_precheck_level(self) -> None:
        """Linus B4 review: a regex that doesn't match its declared
        canonical_violation is a demonstrated bug — provably broken
        by the author's own hand. Precheck must veto, not advise.

        Pins the precheck-side gate behavior end-to-end so a future
        maintainer cannot weaken the severity to advisory without
        the test catching them.
        """
        from darkfactory.spec.precheck import run_precheck
        from tempfile import NamedTemporaryFile
        from pathlib import Path
        # Minimal spec body that satisfies the structural rubric well
        # enough to exercise the §4a path. We're not testing the
        # rubric here — we're testing that round_trip_errors flips
        # ``fatal`` to True regardless of how the rest of precheck
        # rates the spec.
        spec_text = (
            "# Test spec\n\n"
            "## 1. Problem Statement\nPin the H2 case.\n\n"
            "## 2. Functional Requirements\nNone.\n\n"
            "## 3. Non-Functional Requirements\nNone.\n\n"
            "## 4. Constraints\nNone.\n\n"
            "## 4a. Negative Space\n\n"
            "```yaml\n"
            "forbidden_test_shapes:\n"
            "  - pattern: '\\bbaz\\('\n"
            "    reason: 'wrong-dialect — claims to forbid foo() but won''t bite'\n"
            "    canonical_violation: 'foo()'\n"
            "```\n\n"
            "## 5. Interface Contract\nNone.\n\n"
            "## 6. Edge Cases\nNone.\n\n"
            "## 7. Error Handling\nNone.\n\n"
            "## 8. Acceptance Criteria\nNone.\n\n"
            "## 9. Examples\nNone.\n\n"
            "## 10. Substrate Verification\nNone.\n\n"
        )
        with NamedTemporaryFile(
            mode="w", suffix=".md", delete=False, encoding="utf-8",
        ) as f:
            f.write(spec_text)
            spec_path = Path(f.name)
        try:
            result = run_precheck(spec_path)
        finally:
            spec_path.unlink(missing_ok=True)
        # The round-trip-broken §4a entry MUST flip fatal to True.
        self.assertTrue(
            result.fatal,
            f"round_trip_errors must be fatal. round_trip_errors="
            f"{result.negative_space.round_trip_errors!r}",
        )
        self.assertEqual(len(result.negative_space.round_trip_errors), 1)

    def test_corrected_spec_passes(self) -> None:
        """Same scenario as above, but the spec author fixes the
        regex to match TS idiom before shipping."""
        spec_text = (
            "## 4a. Negative Space\n\n"
            "```yaml\n"
            "forbidden_test_shapes:\n"
            "  - pattern: 'current_setting\\([^)]*\\)[\\s\\S]{0,200}?(===|!==|toBe\\()\\s*null'\n"
            "    reason: 'TS idiom for unset-GUC check is ambiguous'\n"
            "    canonical_violation: |\n"
            "      const v = await client`SELECT current_setting('app.x', true)`;\n"
            "      expect(v[0]?.v).toBe(null);\n"
            "```\n"
        )
        findings = extract_negative_space(spec_text)
        self.assertEqual(
            findings.round_trip_errors, [],
            "the corrected TS regex must match the canonical violation. "
            "If this fails, the regex was insufficiently precise.",
        )


if __name__ == "__main__":
    unittest.main()
