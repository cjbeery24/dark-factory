"""Tests for darkfactory.scenarios.coverage.compute_coverage."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.scenarios.coverage import (
    CoverageReport,
    _extract_anchors_from_markdown_scenario,
    _extract_untestable_markers,
    _iter_scenario_files,
    check_spec_type_floor,
    compute_coverage,
)
from darkfactory.spec.precheck import AnchorManifest


def _manifest(
    fr: list[str] | None = None,
    ac: list[str] | None = None,
    ec: list[str] | None = None,
    intents: dict[str, int] | None = None,
) -> AnchorManifest:
    return AnchorManifest(
        fr_ids=fr or [],
        ac_ids=ac or [],
        ec_ids=ec or [],
        declared_intents=intents or {},
    )


class TestComputeCoverage(unittest.TestCase):
    def test_explicit_covers_tag_credits_anchor(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.test.js").write_text(
                "/**\n * @covers FR-1, FR-3\n */\ndescribe('x', ()=>{});\n",
                encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(fr=["FR-1", "FR-2", "FR-3"]))
            self.assertEqual(report.per_anchor_counts["FR-1"], 1)
            self.assertEqual(report.per_anchor_counts["FR-3"], 1)
            self.assertEqual(report.per_anchor_counts["FR-2"], 0)
            self.assertEqual(report.uncovered_anchors, ["FR-2"])

    def test_inline_marker_credits_anchor(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "b.test.ts").write_text(
                "it('validates [FR-3, AC-7]', ()=>{});\n",
                encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(fr=["FR-3"], ac=["AC-7"]))
            self.assertEqual(report.per_anchor_counts["FR-3"], 1)
            self.assertEqual(report.per_anchor_counts["AC-7"], 1)
            self.assertEqual(report.uncovered_anchors, [])

    def test_filename_fallback_inference(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "FR-5_login.spec.js").write_text("// no tag\n", encoding="utf-8")
            report = compute_coverage(d, _manifest(fr=["FR-5"]))
            self.assertEqual(report.per_anchor_counts["FR-5"], 1)

    def test_body_comment_mention_does_NOT_credit(self) -> None:
        """Random prose mention of FR-3 in a comment must not credit FR-3.

        Only @covers tags, describe/it/test string literals, and filename
        matches are valid credit sources.
        """
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "x.test.js").write_text(
                "// some note about FR-3 behavior\n"
                "it('does a thing', () => {});\n",
                encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(fr=["FR-3"]))
            self.assertEqual(report.per_anchor_counts["FR-3"], 0)
            self.assertEqual(report.uncovered_anchors, ["FR-3"])

    def test_it_with_bracketed_marker_credits(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "x.test.js").write_text(
                'it("validates [FR-3]", () => {});\n',
                encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(fr=["FR-3"]))
            self.assertEqual(report.per_anchor_counts["FR-3"], 1)

    def test_describe_string_literal_credits(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "x.test.js").write_text(
                "describe('EC-2 malformed JSON', () => {});\n",
                encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(ec=["EC-2"]))
            self.assertEqual(report.per_anchor_counts["EC-2"], 1)

    def test_uncovered_detection(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.test.js").write_text("// @covers FR-1\n", encoding="utf-8")
            report = compute_coverage(
                d, _manifest(fr=["FR-1", "FR-2"], ec=["EC-1"]),
            )
            self.assertEqual(set(report.uncovered_anchors), {"FR-2", "EC-1"})

    def test_weakly_covered_detection(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.test.js").write_text("// @covers FR-1\n", encoding="utf-8")
            report = compute_coverage(
                d, _manifest(fr=["FR-1"], intents={"FR-1": 5}),
            )
            self.assertEqual(report.weakly_covered, ["FR-1"])
            self.assertEqual(report.uncovered_anchors, [])

    def test_weak_NOT_flagged_when_intents_lt_2(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.test.js").write_text("// @covers FR-1\n", encoding="utf-8")
            report = compute_coverage(
                d, _manifest(fr=["FR-1"], intents={"FR-1": 1}),
            )
            self.assertEqual(report.weakly_covered, [])

    def test_duplicate_tags_warning(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.test.js").write_text("// @covers FR-1\n", encoding="utf-8")
            (d / "b.test.js").write_text("// @covers FR-1\n", encoding="utf-8")
            (d / "c.test.js").write_text("// @covers FR-1\n", encoding="utf-8")
            report = compute_coverage(d, _manifest(fr=["FR-1"]))
            self.assertEqual(report.duplicate_tags_by_anchor.get("FR-1"), 3)

    def test_empty_scenarios_dir(self) -> None:
        with TemporaryDirectory() as tmp:
            report = compute_coverage(Path(tmp), _manifest(fr=["FR-1"]))
            self.assertEqual(report.uncovered_anchors, ["FR-1"])

    def test_compute_coverage_credits_markdown_scenarios_end_to_end(self) -> None:
        """Regression: budget-brain-v2 / 2026-03-28-api-bootstrap.

        Markdown scenario files (Format A) emitted by the architect MUST
        be credited via the `# Requirement:` header. Previously the scanner
        ignored .scenarios.md entirely and reported all anchors uncovered.
        """
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "01-health.scenarios.md").write_text(
                "# Scenario: health endpoint returns ok\n"
                "# Requirement: FR-1, AC-3\n\n"
                "## Request\nGET /health\n\n"
                "## Expect\n- Status: 200\n",
                encoding="utf-8",
            )
            report = compute_coverage(
                d, _manifest(fr=["FR-1", "FR-2"], ac=["AC-3"]),
            )
            self.assertEqual(report.per_anchor_counts["FR-1"], 1)
            self.assertEqual(report.per_anchor_counts["AC-3"], 1)
            self.assertEqual(report.per_anchor_counts["FR-2"], 0)
            self.assertEqual(report.uncovered_anchors, ["FR-2"])

    def test_extract_untestable_markers(self) -> None:
        text = (
            "# stub\n"
            "@untestable FR-3: spec lacks observable side-effect\n"
            "@untestable EC-2: structural invariant only\n"
        )
        markers = _extract_untestable_markers(text)
        self.assertEqual(markers["FR-3"], "spec lacks observable side-effect")
        self.assertEqual(markers["EC-2"], "structural invariant only")


class TestMarkdownScenarioCoverage(unittest.TestCase):
    """Tests for the .scenarios.md / .journey.md (Format A / Format B) path."""

    def test_markdown_scenario_file_is_iterated(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            f = d / "01-foo.scenarios.md"
            f.write_text("# Scenario: foo\n# Requirement: FR-1\n", encoding="utf-8")
            yielded = list(_iter_scenario_files(d))
            self.assertIn(f, yielded)

    def test_journey_md_file_is_iterated(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            f = d / "02-bar.journey.md"
            f.write_text("# Scenario: bar\n# Requirement: FR-2\n", encoding="utf-8")
            yielded = list(_iter_scenario_files(d))
            self.assertIn(f, yielded)

    def test_extract_anchors_from_markdown_requirement_header(self) -> None:
        text = "# Scenario: foo\n# Requirement: FR-1, AC-3\n\nbody\n"
        self.assertEqual(
            _extract_anchors_from_markdown_scenario(text),
            {"FR-1", "AC-3"},
        )

    def test_extract_anchors_handles_plural_requirements(self) -> None:
        text = "# Scenario: foo\n# Requirements: FR-1\n"
        self.assertEqual(
            _extract_anchors_from_markdown_scenario(text),
            {"FR-1"},
        )

    def test_extract_anchors_ignores_body_prose_mentions(self) -> None:
        text = (
            "# Scenario: foo\n"
            "# Requirement: FR-1\n\n"
            "## Notes\n"
            "This relates to FR-7 and AC-2 conceptually.\n"
        )
        self.assertEqual(
            _extract_anchors_from_markdown_scenario(text),
            {"FR-1"},
        )

    def test_extract_anchors_handles_multiple_scenario_blocks(self) -> None:
        text = (
            "# Scenario: first\n"
            "# Requirement: FR-1\n\n"
            "## Request\nGET /a\n\n"
            "# Scenario: second\n"
            "# Requirement: FR-2, AC-5\n\n"
            "## Request\nGET /b\n"
        )
        self.assertEqual(
            _extract_anchors_from_markdown_scenario(text),
            {"FR-1", "FR-2", "AC-5"},
        )

    def test_compute_coverage_does_not_credit_md_files_without_scenario_suffix(
        self,
    ) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "notes.md").write_text(
                "# Requirement: FR-1\n", encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(fr=["FR-1"]))
            self.assertEqual(report.per_anchor_counts["FR-1"], 0)
            self.assertEqual(report.uncovered_anchors, ["FR-1"])

    def test_extract_anchors_handles_leading_whitespace_on_requirement_header(
        self,
    ) -> None:
        text = "# Scenario: foo\n   # Requirement: FR-1\n"
        self.assertEqual(
            _extract_anchors_from_markdown_scenario(text),
            {"FR-1"},
        )

    def test_extract_anchors_header_is_case_insensitive(self) -> None:
        text = "# scenario: foo\n# requirement: fr-1\n"
        self.assertEqual(
            _extract_anchors_from_markdown_scenario(text),
            {"FR-1"},
        )

    def test_markdown_scenario_without_requirement_header_is_uncovered(
        self,
    ) -> None:
        """A malformed .scenarios.md file (no header) credits zero anchors."""
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "01-malformed.scenarios.md").write_text(
                "# Scenario: missing requirement\n## Request\nGET /x\n",
                encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(fr=["FR-1"]))
            self.assertEqual(report.per_anchor_counts["FR-1"], 0)
            self.assertEqual(report.uncovered_anchors, ["FR-1"])

    def test_te_bracket_tag_in_scenario_header_credits_anchor(self) -> None:
        """Phase 4.6b.1 TE format: ``# Scenario: ... [FR-1]`` credits FR-1.

        Regression for the 4.6b.1 coverage-scanner / TE-format mismatch
        that blocked Phase 4.6b.2. TE emits bracketed anchor tags inside
        ``# Scenario:`` and ``## Test:`` headers; the scanner must credit
        them without requiring a separate ``# Requirement:`` header.
        """
        text = (
            "# Scenario: CORS request from allowed origin succeeds [FR-1]\n"
            "\n"
            "## Test: Server starts with valid environment [AC-3]\n"
            "\n"
            "### Edge: rate limit boundary [EC-2]\n"
        )
        self.assertEqual(
            _extract_anchors_from_markdown_scenario(text),
            {"FR-1", "AC-3", "EC-2"},
        )

    def test_te_covers_tag_in_markdown_credits_anchor(self) -> None:
        """TE emits ``@covers AC-3`` tags under ``# Scenario:`` headers."""
        text = (
            "# Scenario: Health endpoint returns 200\n"
            "\n"
            "@covers AC-3\n"
            "\n"
            "**Given** the server is running\n"
        )
        self.assertEqual(
            _extract_anchors_from_markdown_scenario(text),
            {"AC-3"},
        )

    def test_body_prose_anchor_mention_does_not_credit(self) -> None:
        """Discipline: random prose mentions of FR-N outside headers
        or @covers tags must not credit the anchor."""
        text = (
            "# Scenario: unrelated test\n"
            "\n"
            "This scenario is similar to the one for FR-9 but is actually\n"
            "about something else entirely.\n"
        )
        self.assertEqual(
            _extract_anchors_from_markdown_scenario(text),
            set(),
        )

    def test_code_files_still_use_describe_extractor(self) -> None:
        """Sanity: dispatch did not break the original code-file path."""
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "x.test.ts").write_text(
                'describe("[FR-1]", () => {});\n',
                encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(fr=["FR-1"]))
            self.assertEqual(report.per_anchor_counts["FR-1"], 1)
            self.assertEqual(report.uncovered_anchors, [])


class TestRegressionDetection(unittest.TestCase):
    def test_at_regression_tag_sets_flag(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "bug.test.js").write_text(
                "/** @covers FR-1\n * @regression\n */\nit('x',()=>{});\n",
                encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(fr=["FR-1"]))
            self.assertTrue(report.regression_scenario_present)

    def test_filename_alone_does_not_credit(self) -> None:
        # Filename heuristic was rejected — only the @regression tag counts.
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "FR-1_regression.test.js").write_text(
                "/** @covers FR-1 */\nit('x',()=>{});\n", encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(fr=["FR-1"]))
            self.assertFalse(report.regression_scenario_present)

    def test_no_regression_marker(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "a.test.js").write_text(
                "/** @covers FR-1 */\nit('x',()=>{});\n", encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(fr=["FR-1"]))
            self.assertFalse(report.regression_scenario_present)


class TestCheckSpecTypeFloor(unittest.TestCase):
    def test_bugfix_with_regression_passes(self) -> None:
        r = CoverageReport(regression_scenario_present=True)
        ok, reason = check_spec_type_floor(r, "bugfix")
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_bugfix_without_regression_fails(self) -> None:
        r = CoverageReport(regression_scenario_present=False)
        ok, reason = check_spec_type_floor(r, "bugfix")
        self.assertFalse(ok)
        self.assertIn("regression", reason)

    def test_feature_passes_without_regression(self) -> None:
        r = CoverageReport(regression_scenario_present=False)
        ok, _ = check_spec_type_floor(r, "feature")
        self.assertTrue(ok)

    def test_bootstrap_passes_without_regression(self) -> None:
        r = CoverageReport(regression_scenario_present=False)
        ok, _ = check_spec_type_floor(r, "bootstrap")
        self.assertTrue(ok)

    def test_unknown_passes(self) -> None:
        r = CoverageReport(regression_scenario_present=False)
        ok, _ = check_spec_type_floor(r, "")
        self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
