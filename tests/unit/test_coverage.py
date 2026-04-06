"""Tests for darkfactory.scenarios.coverage.compute_coverage."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.scenarios.coverage import (
    _extract_untestable_markers,
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

    def test_extract_untestable_markers(self) -> None:
        text = (
            "# stub\n"
            "@untestable FR-3: spec lacks observable side-effect\n"
            "@untestable EC-2: structural invariant only\n"
        )
        markers = _extract_untestable_markers(text)
        self.assertEqual(markers["FR-3"], "spec lacks observable side-effect")
        self.assertEqual(markers["EC-2"], "structural invariant only")


if __name__ == "__main__":
    unittest.main()
