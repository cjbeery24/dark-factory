"""WHAT-only structure acceptance (spec-overburden campaign).

§5 Interface Contract / §9 Scenario Intent / §10 Examples are derived-HOW and no
longer required. These tests lock the POSITIVE structure-pass path (previously
untested — which let a `passed = score == 100` regression slip when the required
set shrank from 10 to 8) and the §9-absent anchor-intent no-op.
"""

from __future__ import annotations

import unittest
from textwrap import dedent

from darkfactory.spec.precheck import (
    _REQUIRED_SECTIONS,
    check_structure,
    check_anchor_intent_coverage,
    extract_anchor_manifest,
)


def _required_sections_body() -> str:
    """A spec carrying exactly the 8 required sections (no §5/§9/§10)."""
    return dedent(
        """
        # Spec

        ## 1. Problem Statement
        A problem exists.

        ## 2. Functional Requirements
        ### FR-1: Do the thing
        - Input: a request. Output: a response.

        ## 3. Non-Functional Requirements
        - NFR-1: fast enough.

        ## 4. Constraints
        - Stack: TypeScript.

        ## 6. Edge Cases
        | ID | Condition | Expected |
        |----|-----------|----------|
        | EC-1 | empty input | error |

        ## 7. Error Handling
        - Errors surface as rejections.

        ## 8. Acceptance Criteria
        | ID | Criterion | Blocking |
        |----|-----------|----------|
        | AC-1 | GIVEN a request WHEN handled THEN a response is returned | no |

        ## 12. Out of Scope
        Styling.
        """
    )


class TestWhatOnlyStructure(unittest.TestCase):
    def test_max_score_tracks_required_section_count(self) -> None:
        # Guards against re-introducing a hardcoded /100 when the list changes.
        self.assertEqual(len(_REQUIRED_SECTIONS), 8)

    def test_complete_whatonly_spec_passes_structure(self) -> None:
        r = check_structure(_required_sections_body())
        self.assertEqual(r.score, 10 * len(_REQUIRED_SECTIONS))
        self.assertTrue(r.passed)

    def test_spec_with_optional_sections_still_passes(self) -> None:
        # Backward-compat: a spec that ALSO carries §5/§10 still passes (the
        # optional sections don't subtract; required set is fully present).
        body = _required_sections_body() + dedent(
            """
            ## 5. Interface Contract
            ```
            doThing(req): Response
            ```

            ## 10. Examples
            1. req -> response
            """
        )
        r = check_structure(body)
        self.assertTrue(r.passed)

    def test_missing_required_section_fails_structure(self) -> None:
        # Drop a required section (Error Handling) → structure must fail.
        full = _required_sections_body()
        self.assertIn("## 7. Error Handling", full)
        body = full.replace(
            "## 7. Error Handling\n- Errors surface as rejections.\n", ""
        )
        self.assertNotIn("## 7. Error Handling", body)
        r = check_structure(body)
        self.assertFalse(r.passed)
        self.assertLess(r.score, 10 * len(_REQUIRED_SECTIONS))


class TestAnchorIntentWhatOnly(unittest.TestCase):
    def test_absent_scenario_intent_no_coverage_issues(self) -> None:
        # WHAT-only spec has no §9 → no declared intents → no-op (no false flags).
        m = extract_anchor_manifest(_required_sections_body())
        self.assertEqual(m.declared_intents, {})
        self.assertEqual(check_anchor_intent_coverage(m), [])

    def test_partial_scenario_intent_still_flags(self) -> None:
        # When §9 IS declared (declared_intents non-empty), an FR/AC absent from
        # it is still flagged — the no-op only triggers on a truly-absent §9.
        # Test the function directly with a hand-built manifest (robust to the
        # §9 table parser's quirks).
        from darkfactory.spec.anchors import AnchorManifest
        m = AnchorManifest(
            fr_ids=["FR-1", "FR-2"],
            ac_ids=["AC-1"],
            ec_ids=[],
            declared_intents={"FR-1": ["do the thing"], "AC-1": ["..."]},
            intents=[],
        )
        issues = check_anchor_intent_coverage(m)
        self.assertTrue(any("FR-2" in i for i in issues),
                        f"FR-2 should be flagged as missing from §9; got {issues}")

    def test_truly_absent_scenario_intent_is_noop(self) -> None:
        from darkfactory.spec.anchors import AnchorManifest
        m = AnchorManifest(fr_ids=["FR-1"], ac_ids=["AC-1"], ec_ids=[],
                           declared_intents={}, intents=[])
        self.assertEqual(check_anchor_intent_coverage(m), [])


class TestMinimumBehaviorFloor(unittest.TestCase):
    """Deterministic anti-vacuous floor (REVISE-class) — the variance-free
    backstop to the LLM Derivability Bar."""

    def _mk(self, frs, acs):
        from darkfactory.spec.anchors import AnchorManifest
        return AnchorManifest(fr_ids=frs, ac_ids=acs, ec_ids=[],
                              declared_intents={}, intents=[])

    def test_sufficient_behavior_passes(self) -> None:
        from darkfactory.spec.precheck import check_minimum_behavior
        m = self._mk(["FR-1"], ["AC-1", "AC-2", "AC-3"])
        self.assertEqual(check_minimum_behavior(m), [])

    def test_no_fr_flagged(self) -> None:
        from darkfactory.spec.precheck import check_minimum_behavior
        issues = check_minimum_behavior(self._mk([], ["AC-1", "AC-2", "AC-3"]))
        self.assertTrue(any("Functional Requirements" in i for i in issues))

    def test_too_few_acs_flagged(self) -> None:
        from darkfactory.spec.precheck import check_minimum_behavior
        issues = check_minimum_behavior(self._mk(["FR-1"], ["AC-1"]))
        self.assertTrue(any("Acceptance Criteria" in i for i in issues))

    def test_whatonly_spec_clears_the_floor(self) -> None:
        # A WHAT-only manifest (8 behavioral ACs, no exclusions) must clear it.
        from darkfactory.spec.precheck import check_minimum_behavior
        m = self._mk(["FR-1", "FR-2", "FR-3"],
                     [f"AC-{i}" for i in range(1, 9)])
        self.assertEqual(check_minimum_behavior(m), [])

    def test_subprocess_excluded_acs_dont_count_toward_floor(self) -> None:
        # 3 declared ACs but 2 are subprocess-excluded → only 1 behavioral →
        # floor fires (the count is post-exclusion, Linus 3a).
        from darkfactory.spec.anchors import AnchorManifest
        from darkfactory.spec.precheck import check_minimum_behavior
        m = AnchorManifest(fr_ids=["FR-1"], ac_ids=["AC-1", "AC-2", "AC-3"],
                           ec_ids=[], declared_intents={}, intents=[])
        m.subprocess_ac_ids = ["AC-2", "AC-3"]
        issues = check_minimum_behavior(m)
        self.assertTrue(any("Acceptance Criteria" in i for i in issues))


if __name__ == "__main__":
    unittest.main()
