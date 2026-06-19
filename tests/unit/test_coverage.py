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
    architect_credited_anchors,
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

    def test_te_vitest_example_from_prompt_credits_anchors(self) -> None:
        """Pin the contract between ``agents/test-engineer.md`` and the
        coverage scanner: the vitest example shown in the TE prompt must
        be parseable by the executable-file extractor with every
        bracket-tagged ``it(...)`` crediting its anchor.

        If this test starts failing after editing ``agents/test-engineer.md``
        or ``darkfactory/scenarios/coverage.py``, the prompt and the
        scanner have drifted — fix the one that is wrong, not this test.
        """
        vitest_example = (
            "import { describe, it, expect } from 'vitest';\n"
            "import { createServer } from '../../../apps/api/src/server';\n"
            "\n"
            "describe('Health endpoint', () => {\n"
            "  it('[AC-3] returns 200 with {status: ok} body shape', "
            "async () => {\n"
            "    expect(true).toBe(true);\n"
            "  });\n"
            "\n"
            "  it('[AC-4] includes uptime as a number', async () => {\n"
            "    expect(true).toBe(true);\n"
            "  });\n"
            "});\n"
        )
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "health-endpoint.test.ts").write_text(
                vitest_example, encoding="utf-8",
            )
            report = compute_coverage(
                d, _manifest(ac=["AC-3", "AC-4"]),
            )
            self.assertEqual(report.per_anchor_counts["AC-3"], 1)
            self.assertEqual(report.per_anchor_counts["AC-4"], 1)
            self.assertEqual(report.uncovered_anchors, [])

    def test_te_pytest_example_from_prompt_credits_anchors(self) -> None:
        """Pin the contract for the pytest example in ``agents/test-engineer.md``.

        Sprint 9 (breadth): the Python example places the anchor tag in the
        FIRST docstring line of a ``def test_*():`` function — the convention
        the TE prompt teaches. Before Sprint 9 the docstring form was NOT
        credited (``_extract_anchors_from_text`` only scanned JSDoc ``@covers``
        and describe/it/test literals), so pytest outputs survived the coverage
        floor only via the filename fallback (``_FILENAME_ANCHOR_RE``) — which
        broke the moment a TE emitted ``test_fr_scenarios.py`` (no anchor in the
        name). It now credits the docstring directly via
        ``_PYTEST_TEST_DOCSTRING_RE``. This pin asserts the docstring is the
        credit path even when the FILENAME carries no anchor.
        """
        pytest_example = (
            "import pytest\n"
            "\n"
            "def test_health_returns_200_with_status_ok():\n"
            '    """[AC-3] health returns 200 with {status: ok} body shape"""\n'
            "    assert True\n"
        )
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            # Filename deliberately carries NO anchor — credit must come from
            # the docstring, proving the Sprint-9 docstring credit path works.
            (d / "test_health_endpoint.py").write_text(
                pytest_example, encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(ac=["AC-3"]))
            self.assertEqual(report.per_anchor_counts["AC-3"], 1)
            self.assertEqual(report.uncovered_anchors, [])

    def test_pytest_mark_covers_marker_credits_anchor(self) -> None:
        """Sprint 9: the ``@pytest.mark.covers("FR-1")`` marker form is
        credited via the broadened _COVERS_TAG_RE (`.covers(` idiom)."""
        from darkfactory.scenarios.coverage import _extract_anchors_from_text
        text = (
            '@pytest.mark.covers("FR-1", "AC-2")\n'
            "def test_a():\n"
            "    assert True\n"
        )
        self.assertEqual(_extract_anchors_from_text(text), {"FR-1", "AC-2"})

    def test_jsdoc_covers_still_credited_after_broadening(self) -> None:
        """Sprint 9 regression: broadening _COVERS_TAG_RE to the `.covers(`
        idiom must NOT break the original JSDoc ``@covers FR-1`` form."""
        from darkfactory.scenarios.coverage import _extract_anchors_from_text
        text = "/** @covers FR-1, AC-7 */\ntest('x', () => {});\n"
        self.assertEqual(_extract_anchors_from_text(text), {"FR-1", "AC-7"})

    def test_pytest_docstring_marker_credits_anchor(self) -> None:
        """Sprint 9: ``[FR-2]`` in the first docstring line of a
        ``def test_*()`` function is credited."""
        from darkfactory.scenarios.coverage import _extract_anchors_from_text
        text = 'def test_b():\n    """[FR-2] reject malformed input."""\n    pass\n'
        self.assertEqual(_extract_anchors_from_text(text), {"FR-2"})

    def test_covers_lookalikes_do_not_false_credit(self) -> None:
        """Sprint 9 (ship-gate posture): the covers-marker idiom must NOT
        false-credit method calls, look-alike words, or prose. Crediting an
        anchor that isn't really tested is worse than a false abort. The
        load-bearing cases (caught by Linus + Codex) are real `.covers(` METHOD
        CALLS — only the leading `@` decorator anchor distinguishes a coverage
        marker from `query.covers(...)` / `poly.covers(...)` / dead code."""
        from darkfactory.scenarios.coverage import _extract_anchors_from_text
        for text in (
            # The dangerous case: a real method literally named `covers`.
            'def test_x():\n    assert query.covers("FR-1") == []',
            'region = poly.covers("AC-5")',          # shapely-style geo API
            '    # legacy: x.covers("FR-9") removed',  # commented-out dead code
            'x = thing.discovers("FR-2")',           # discovers, not @…covers
            'y = obj.recovers("FR-3")',              # recovers, not @…covers
            'msg = "this covers FR-4 in the docs"',  # prose, no @, no .covers(
        ):
            self.assertEqual(
                _extract_anchors_from_text(text), set(), text,
            )

    def test_markdown_scenario_covers_method_call_does_not_false_credit(self) -> None:
        """Sprint 9: the markdown extractor shares _COVERS_TAG_RE, so the
        decorator anchor must also protect it — a `.covers(` mention in markdown
        prose or a fenced code block must NOT credit (the decorator `@` is
        absent in prose). Only an explicit `@covers FR-N` tag credits."""
        from darkfactory.scenarios.coverage import (
            _extract_anchors_from_markdown_scenario,
        )
        for text in (
            "Some prose: the handler `obj.covers(\"FR-9\")` is called here.",
            "```python\nassert thing.covers(\"AC-2\")\n```",
        ):
            self.assertEqual(
                _extract_anchors_from_markdown_scenario(text), set(), text,
            )

    def test_pytest_docstring_scoping_rejects_non_test_boundaries(self) -> None:
        """Sprint 9 (ship-gate posture): [FR-N] credit is scoped to a
        ``def test_*()`` opening docstring — module docstrings, body
        comments, and non-test helper docstrings must NOT credit."""
        from darkfactory.scenarios.coverage import _extract_anchors_from_text
        for text in (
            '"""[FR-1] module-level docstring."""\nimport os\n',          # module
            "def test_x():\n    # [FR-2] a comment\n    assert True\n",     # comment
            'def helper():\n    """[FR-3] a helper, not a test."""\n    pass\n',  # non-test fn
        ):
            self.assertEqual(
                _extract_anchors_from_text(text), set(), text,
            )

    def test_te_must_not_emit_markdown_tests(self) -> None:
        """Regression pin: narrative Given/When/Then markdown files
        must not credit anchors as visible tests.

        In Run 4, TE inherited Scenario Architect format and wrote
        ``*.scenarios.md`` files with Given/When/Then prose. The quality
        gate's executable dispatch (``_DF_FORMAT_PATTERNS_EXECUTABLE``)
        does not match ``*.md`` files, so they never execute. The TE
        prompt now explicitly forbids markdown test files.

        We still want the coverage scanner to *parse* a .scenarios.md
        file (QA Holdout or legacy SA work) — but a .scenarios.md file
        full of narrative prose with no anchors in headers or @covers
        tags must credit nothing. This pin catches a regression where a
        prose-only markdown file would accidentally satisfy the coverage
        floor gate.
        """
        narrative_prose = (
            "# Scenario: Health endpoint returns successful response\n"
            "\n"
            "**Given:** The server is running\n"
            "**When:** A GET request is made to /health\n"
            "**Then:**\n"
            "- Response status is 200 OK\n"
            "- Body contains status: ok\n"
        )
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "health.scenarios.md").write_text(
                narrative_prose, encoding="utf-8",
            )
            report = compute_coverage(d, _manifest(ac=["AC-3"]))
            self.assertEqual(report.per_anchor_counts["AC-3"], 0)
            self.assertEqual(report.uncovered_anchors, ["AC-3"])


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


class TestArchitectCreditedAnchors(unittest.TestCase):
    """SX2: the coverage floor credits anchors covered by a NON-scenario vehicle
    the ARCHITECT declared — and ONLY from architect-produced files, never the
    barrier-restricted TE's COVERAGE-GAPS.md (which the TE controls)."""

    _MANIFEST = (
        "version: 1\nspec: s\nspec_hash: h\nlanguage: node\n"
        "file_map:\n  - {path: src/a.ts, action: create, purpose: x}\n"
        "data_models: []\napi_contracts: []\ndependencies: []\n"
        "environment: []\nruntime: {}\ntest_integration: {entry_points: []}\n"
        "forbidden_patterns:\n  AC-9:\n  - 'drizzle-orm/.*migrator'\n"
    )

    def _paths(self, d: Path, *, manifest: bool = True, substrate=None):
        mp = d / "m.yaml"
        if manifest:
            mp.write_text(self._MANIFEST, encoding="utf-8")
        sp = d / "s.toml"
        if substrate is not None:
            sp.write_text(substrate, encoding="utf-8")
        return mp, sp

    def test_forbidden_pattern_anchor_credited(self):
        with TemporaryDirectory() as t:
            mp, sp = self._paths(Path(t))
            credited = architect_credited_anchors(mp, sp)
            self.assertIn("AC-9", credited)
            self.assertIn("forbidden-pattern", credited["AC-9"])

    def test_substrate_covers_verifiable_false_credited_as_deferred(self):
        with TemporaryDirectory() as t:
            mp, sp = self._paths(Path(t), substrate=(
                '[[tenant_isolation]]\nverifiable = false\n'
                'covers = ["AC-12", "AC-14"]\n'
            ))
            credited = architect_credited_anchors(mp, sp)
            self.assertTrue({"AC-12", "AC-14"} <= set(credited))
            self.assertIn("deferred", credited["AC-12"])

    def test_substrate_covers_verifiable_true_credited(self):
        with TemporaryDirectory() as t:
            mp, sp = self._paths(Path(t), substrate=(
                '[[rls_table]]\nverifiable = true\ncovers = ["AC-20"]\n'
            ))
            credited = architect_credited_anchors(mp, sp)
            self.assertIn("AC-20", credited)
            self.assertNotIn("deferred", credited["AC-20"])

    def test_anchor_with_no_architect_signal_not_credited(self):
        with TemporaryDirectory() as t:
            mp, sp = self._paths(Path(t), substrate='[[guc]]\nname = "x"\n')
            credited = architect_credited_anchors(mp, sp)
            self.assertNotIn("AC-7", credited)
            self.assertNotIn("AC-99", credited)

    def test_te_coverage_gaps_md_never_credits(self):
        # SAFETY (load-bearing): an anchor the barrier-restricted TE labeled
        # `spec-vehicle-mismatch` in COVERAGE-GAPS.md (a file it controls) must
        # NOT be credited — only an ARCHITECT signal credits. The function takes
        # only architect paths; the TE file in the same dir is irrelevant.
        with TemporaryDirectory() as t:
            d = Path(t)
            mp, sp = self._paths(d, substrate="")  # no covers anywhere
            (d / "COVERAGE-GAPS.md").write_text(
                "## AC-12\n- Category: spec-vehicle-mismatch\n", encoding="utf-8"
            )
            credited = architect_credited_anchors(mp, sp)
            self.assertNotIn("AC-12", credited)

    def test_missing_files_credit_nothing(self):
        with TemporaryDirectory() as t:
            d = Path(t)
            self.assertEqual(
                architect_credited_anchors(d / "nope.yaml", d / "nope.toml"), {}
            )

    def test_floor_credit_combination_control_anchor_still_fails(self):
        # End-to-end of the floor's credit step (compute_coverage + credit):
        # AC-12 is credited by the architect's substrate `covers`; AC-99 has NO
        # architect signal and stays uncovered → would still fail the floor. This
        # is the control that proves the relaxation can't blanket-pass.
        with TemporaryDirectory() as t:
            d = Path(t)
            scen = d / "scenarios"
            scen.mkdir()
            mp, sp = self._paths(d, substrate=(
                '[[tenant_isolation]]\nverifiable = false\ncovers = ["AC-12"]\n'
            ))
            report = compute_coverage(scen, _manifest(ac=["AC-12", "AC-99"]))
            self.assertEqual(set(report.uncovered_anchors), {"AC-12", "AC-99"})
            credited = architect_credited_anchors(mp, sp)
            remaining = [a for a in report.uncovered_anchors if a not in credited]
            self.assertEqual(remaining, ["AC-99"])


if __name__ == "__main__":
    unittest.main()
