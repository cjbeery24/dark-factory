"""Unit tests for COVERAGE-GAPS.md parsing and bucket classification.

Covers the resilient coverage-gap routing flow described in
``docs/dev/2026-04-15-coverage-gap-architect-amend.md``. The orchestrator
consumes the outputs of these helpers; the helpers themselves are pure
and tested in isolation here.
"""

from __future__ import annotations

import textwrap
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.scenarios.coverage import (
    GAP_CATEGORY_ARCHITECT,
    GAP_CATEGORY_MISSING,
    GAP_CATEGORY_RUNTIME,
    GAP_CATEGORY_SPEC_VEHICLE,
    CoverageGap,
    classify_gaps,
    parse_coverage_gaps_markdown,
)


def _write_gaps(tmp: Path, content: str) -> Path:
    p = tmp / "COVERAGE-GAPS.md"
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


class TestParseCoverageGapsMarkdown(unittest.TestCase):
    def test_missing_file_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            self.assertEqual(
                parse_coverage_gaps_markdown(Path(tmp) / "nope.md"), [],
            )

    def test_three_anchor_types_parsed(self) -> None:
        with TemporaryDirectory() as tmp:
            p = _write_gaps(Path(tmp), """\
                # Coverage Gaps

                ## AC-9: Something
                - **Category:** architect-entry-point
                - **Reason:** missing entry point
                - **Needs:** apps/api/src/db/health-check.ts

                ## FR-4: Another
                - **Category:** architect-entry-point
                - **Reason:** same reason
                - **Needs:** apps/api/src/db/health-check.ts

                ## EC-2: Third
                - **Category:** spec-vehicle-mismatch
                - **Reason:** requires Fastify
            """)
            gaps = parse_coverage_gaps_markdown(p)
            ids = sorted(g.anchor_id for g in gaps)
            self.assertEqual(ids, ["AC-9", "EC-2", "FR-4"])

    def test_heading_without_colon(self) -> None:
        with TemporaryDirectory() as tmp:
            p = _write_gaps(Path(tmp), """\
                ## AC-9
                - **Category:** architect-entry-point
                - **Reason:** something
            """)
            gaps = parse_coverage_gaps_markdown(p)
            self.assertEqual(len(gaps), 1)
            self.assertEqual(gaps[0].anchor_id, "AC-9")
            self.assertEqual(gaps[0].category, GAP_CATEGORY_ARCHITECT)

    def test_category_tolerant_to_missing_bold(self) -> None:
        with TemporaryDirectory() as tmp:
            p = _write_gaps(Path(tmp), """\
                ## AC-1: X
                - Category: architect-entry-point
                - Reason: no bold on fields
                - Needs: src/foo.ts
            """)
            gaps = parse_coverage_gaps_markdown(p)
            self.assertEqual(len(gaps), 1)
            self.assertEqual(gaps[0].category, GAP_CATEGORY_ARCHITECT)
            self.assertEqual(gaps[0].needs, "src/foo.ts")

    def test_missing_category_leaves_field_none(self) -> None:
        with TemporaryDirectory() as tmp:
            p = _write_gaps(Path(tmp), """\
                ## AC-1: X
                - **Reason:** category absent
            """)
            gaps = parse_coverage_gaps_markdown(p)
            self.assertEqual(len(gaps), 1)
            self.assertIsNone(gaps[0].category)
            self.assertEqual(gaps[0].reason, "category absent")

    def test_trailing_bold_on_value_stripped(self) -> None:
        """TE sometimes emits ``**Category:** architect-entry-point**`` with a
        trailing bold pair; the parser strips the trailing ``**`` so the
        category still classifies."""
        with TemporaryDirectory() as tmp:
            p = _write_gaps(Path(tmp), """\
                ## AC-1: X
                - **Category:** architect-entry-point**
                - **Reason:** trailing bold**
                - **Needs:** src/foo.ts**
            """)
            gaps = parse_coverage_gaps_markdown(p)
            self.assertEqual(gaps[0].category, GAP_CATEGORY_ARCHITECT)
            self.assertEqual(gaps[0].reason, "trailing bold")
            self.assertEqual(gaps[0].needs, "src/foo.ts")

    def test_needs_module_synonym(self) -> None:
        """TE drift: ``Needs module:`` is accepted as a synonym for
        ``Needs:`` — the architect prompt thinks in module terms."""
        with TemporaryDirectory() as tmp:
            p = _write_gaps(Path(tmp), """\
                ## AC-1: X
                - **Category:** architect-entry-point
                - **Reason:** missing module
                - **Needs module:** src/foo.ts
            """)
            gaps = parse_coverage_gaps_markdown(p)
            self.assertEqual(gaps[0].needs, "src/foo.ts")

    def test_invalid_category_becomes_none(self) -> None:
        with TemporaryDirectory() as tmp:
            p = _write_gaps(Path(tmp), """\
                ## AC-1: X
                - **Category:** not-a-real-bucket
                - **Reason:** bogus
            """)
            gaps = parse_coverage_gaps_markdown(p)
            self.assertIsNone(gaps[0].category)

    def test_needs_optional(self) -> None:
        with TemporaryDirectory() as tmp:
            p = _write_gaps(Path(tmp), """\
                ## AC-1: X
                - **Category:** spec-vehicle-mismatch
                - **Reason:** requires Fastify
            """)
            gaps = parse_coverage_gaps_markdown(p)
            self.assertIsNone(gaps[0].needs)

    def test_body_before_first_heading_ignored(self) -> None:
        with TemporaryDirectory() as tmp:
            p = _write_gaps(Path(tmp), """\
                The test-engineer could not cover the following anchors:

                ## AC-1: First
                - **Category:** architect-entry-point
                - **Reason:** missing
                - **Needs:** src/x.ts
            """)
            gaps = parse_coverage_gaps_markdown(p)
            self.assertEqual(len(gaps), 1)

    def test_no_headings_returns_empty(self) -> None:
        with TemporaryDirectory() as tmp:
            p = _write_gaps(Path(tmp), "just prose, no headings\n")
            self.assertEqual(parse_coverage_gaps_markdown(p), [])


class TestClassifyGaps(unittest.TestCase):
    def test_restricts_to_uncovered_anchors(self) -> None:
        gaps = [
            CoverageGap("AC-1", "r", GAP_CATEGORY_ARCHITECT, "src/a.ts"),
            CoverageGap("AC-2", "r", GAP_CATEGORY_ARCHITECT, "src/b.ts"),
        ]
        buckets = classify_gaps(gaps, uncovered_anchors=["AC-1"])
        self.assertEqual(len(buckets[GAP_CATEGORY_ARCHITECT]), 1)
        self.assertEqual(buckets[GAP_CATEGORY_ARCHITECT][0].anchor_id, "AC-1")

    def test_routes_each_category(self) -> None:
        gaps = [
            CoverageGap("AC-1", "r", GAP_CATEGORY_ARCHITECT, "src/a.ts"),
            CoverageGap("AC-2", "r", GAP_CATEGORY_SPEC_VEHICLE, None),
            CoverageGap("AC-3", "r", GAP_CATEGORY_RUNTIME, None),
            CoverageGap("AC-4", "r", None, None),
        ]
        buckets = classify_gaps(
            gaps, uncovered_anchors=["AC-1", "AC-2", "AC-3", "AC-4"],
        )
        self.assertEqual(len(buckets[GAP_CATEGORY_ARCHITECT]), 1)
        self.assertEqual(len(buckets[GAP_CATEGORY_SPEC_VEHICLE]), 1)
        self.assertEqual(len(buckets[GAP_CATEGORY_RUNTIME]), 1)
        self.assertEqual(len(buckets[GAP_CATEGORY_MISSING]), 1)

    def test_empty_gaps_yields_empty_buckets(self) -> None:
        buckets = classify_gaps([], uncovered_anchors=["AC-1"])
        self.assertEqual(buckets[GAP_CATEGORY_ARCHITECT], [])
        self.assertEqual(buckets[GAP_CATEGORY_SPEC_VEHICLE], [])
        self.assertEqual(buckets[GAP_CATEGORY_RUNTIME], [])
        self.assertEqual(buckets[GAP_CATEGORY_MISSING], [])

    def test_anchor_ids_case_insensitive(self) -> None:
        gaps = [CoverageGap("AC-1", "r", GAP_CATEGORY_ARCHITECT, "src/x.ts")]
        buckets = classify_gaps(gaps, uncovered_anchors=["ac-1"])
        self.assertEqual(len(buckets[GAP_CATEGORY_ARCHITECT]), 1)


if __name__ == "__main__":
    unittest.main()
