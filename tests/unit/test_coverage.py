"""Tests for darkfactory.scenarios.coverage.compute_coverage."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.scenarios.coverage import (
    _extract_anchors_from_markdown_scenario,
    _extract_untestable_markers,
    _iter_scenario_files,
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


if __name__ == "__main__":
    unittest.main()
