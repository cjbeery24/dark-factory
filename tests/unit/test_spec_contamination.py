"""Tests for the spec pipeline-contamination guard (Sprint 32, precheck.py).

The guard flags normative spec clauses that encode dark-factory internals
(agent directives, .dark-factory/ paths, import-mandate mechanisms) instead of
framework-/stack-agnostic product behavior — the §5-import-mandate class that
twice invalidated breadth claims. ADVISORY ONLY: it must NEVER change
passed/fatal. The matcher is precise by design (Neo): no bare-word matches, only
dark-factory proper nouns / paths or generic roles under a directive
construction — so it must stay quiet on product-domain language (a product spec
using "import", "reviewer", "architect", "barrier").
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from darkfactory.spec.precheck import check_pipeline_contamination, run_precheck
from darkfactory.spec.spec_model import parse_spec_model


def _find(text: str):
    return check_pipeline_contamination(parse_spec_model(text))


def _cats(findings):
    return {f.category for f in findings}


class TestContaminationTruePositives(unittest.TestCase):
    def test_import_mandate_exemplar(self):
        # The §5-class incident: a product clause mandating an import mechanism.
        f = _find("# S\n## Functional Requirements\n"
                  "- FR-1: The module import must be done via dynamic import to satisfy ESM.\n")
        self.assertIn("import_mandate", _cats(f))

    def test_agent_directive_high_confidence(self):
        f = _find("# S\n## Functional Requirements\n"
                  "- FR-1: The Implementer MUST regenerate the import map before tests.\n")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].category, "agent_directive")
        self.assertEqual(f[0].confidence, "high")  # uppercase MUST

    def test_df_path_high(self):
        f = _find("# S\n## Acceptance Criteria\n| ID | Criterion |\n|--|--|\n"
                  "| AC-1 | The entry file is written under .dark-factory/ for the run. |\n")
        self.assertIn("df_path", _cats(f))
        self.assertTrue(all(x.confidence == "high" for x in f if x.category == "df_path"))

    def test_agent_directive_article_less(self):
        # bullet-style FR/AC prose often drops "the": "Implementer MUST …".
        f = _find("# S\n## Functional Requirements\n"
                  "- FR-1: Implementer MUST regenerate the import map before tests.\n")
        self.assertEqual([x.category for x in f], ["agent_directive"])
        self.assertEqual(f[0].confidence, "high")

    def test_df_proper_nouns(self):
        f = _find("# S\n## Functional Requirements\n"
                  "- FR-1: The scenario architect generates blind scenarios for coverage.\n")
        # both "scenario architect" and "blind scenarios" are dark-factory terms
        self.assertIn("df_term", _cats(f))
        self.assertGreaterEqual(len(f), 1)


class TestContaminationCleanAndFalsePositives(unittest.TestCase):
    def test_clean_product_spec_zero(self):
        self.assertEqual(_find(
            "# S\n## Functional Requirements\n"
            "- FR-1: POST /notes returns 201 with the created note's id.\n"
            "- FR-2: GET /notes/{id} returns 404 when the note does not exist.\n"), [])

    def test_fp_domain_roles(self):
        # product-domain "architect"/"reviewer"/"engineer" as capabilities, not directives
        self.assertEqual(_find(
            "# S\n## Functional Requirements\n"
            "- FR-1: The architect role can approve timesheets; the reviewer approves the request.\n"
            "- FR-2: A Software Engineer job posting lists the required skills.\n"), [])

    def test_fp_import_as_feature(self):
        self.assertEqual(_find(
            "# S\n## Functional Requirements\n"
            "- FR-1: The system can import a CSV file; users import contacts from a vCard.\n"), [])

    def test_fp_barrier_domain_word(self):
        self.assertEqual(_find(
            "# S\n## Functional Requirements\n"
            "- FR-1: The free tier lowers the barrier to entry for new users.\n"), [])

    def test_fp_import_mandate_with_domain_dynamic(self):
        # "import" + a mandate verb + a bare adjective "dynamic" (a PRODUCT noun,
        # not a module-loading mechanism) must NOT flag (Linus Q1 / Codex P2).
        for clause in (
            "Large dataset import must use the dynamic chunking algorithm.",
            "The system should import dynamic pricing data through the partner feed.",
            "Data import must be configured via the dynamic field mapping UI.",
        ):
            with self.subTest(clause=clause):
                self.assertEqual(_find(f"# S\n## Functional Requirements\n- FR-1: {clause}\n"), [])

    def test_subheading_under_non_normative_excluded(self):
        # ancestor-aware skip (Linus Q2): a subheading nested under Out of Scope
        # inherits the skip even though its own heading looks normative.
        self.assertEqual(_find(
            "# S\n## Functional Requirements\n- FR-1: Create a note.\n"
            "## Out of Scope\n### Notes\n"
            "- the Implementer MUST not touch auth; see .dark-factory/ for layout.\n"), [])

    def test_fence_excluded(self):
        # .dark-factory/ and dynamic import inside example code must NOT flag.
        self.assertEqual(_find(
            "# S\n## Functional Requirements\n- FR-1: Create a note.\n"
            "```ts\nconst m = await import('.dark-factory/x'); // dynamic import via path\n```\n"), [])

    def test_non_normative_sections_excluded(self):
        # the tenant_isolation-class trap: internals mentioned in Out-of-Scope/Examples.
        self.assertEqual(_find(
            "# S\n## Functional Requirements\n- FR-1: Create a note.\n"
            "## Dependencies & Out of Scope\n"
            "- The implementer must not handle auth; tenant isolation is out of scope.\n"
            "## Examples\n- e.g. write to .dark-factory/scenarios for the blind scenario.\n"), [])

    def test_lowercase_product_must_is_medium_not_high(self):
        # residual: a human "architect must" in a product spec → medium (dismissable),
        # never high. Confirms the uppercase-MUST discriminator.
        f = _find("# S\n## Functional Requirements\n"
                  "- FR-1: the architect must approve the floor plan before build.\n")
        self.assertTrue(all(x.confidence == "medium" for x in f))


class TestContaminationAdvisoryInvariant(unittest.TestCase):
    """The load-bearing safety tests: contamination is advisory — it NEVER moves
    passed/fatal, and it surfaces to the author."""

    def _run(self, text: str):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "spec.md"
            p.write_text(text, encoding="utf-8")
            return run_precheck(p)

    _CLEAN = ("# Spec\n## Functional Requirements\n"
              "- FR-1: POST /notes returns 201 with the created id.\n")
    _CONTAM = ("# Spec\n## Functional Requirements\n"
               "- FR-1: POST /notes returns 201; the Implementer MUST import via dynamic import.\n")

    def test_contamination_never_changes_passed_or_fatal(self):
        clean = self._run(self._CLEAN)
        contam = self._run(self._CONTAM)
        # contamination present in the contaminated spec...
        self.assertTrue(contam.contamination, "expected contamination findings")
        # ...but passed/fatal are identical to the clean spec — purely advisory.
        self.assertEqual(clean.passed, contam.passed)
        self.assertEqual(clean.fatal, contam.fatal)
        self.assertFalse(contam.fatal, "contamination must never be fatal")

    def test_contamination_surfaces_in_summary(self):
        contam = self._run(self._CONTAM)
        self.assertIn("Contamination advisories", contam.summary)


if __name__ == "__main__":
    unittest.main()
