"""Tests for check_fr_breadth in spec/precheck.py (task #30).

Deterministic per-FR breadth check — the replacement for the validator agent's
old LLM-driven per-FR claim cap, which flip-flopped on a non-deterministic
33→50 claim-extraction spread. It counts §9 Scenario Intent rows per FR (the
structure that carries per-FR grouping in EVERY spec), flagging FRs over the
template's documented "Target 5-15 intents per FR" budget. These tests pin the
behavior on both synthetic fixtures and the real spec corpus (the corpus test
guards against the check going structurally dead — the failure Linus caught in
the first, §8-based design).
"""

import pathlib
import tempfile
import unittest

from darkfactory.spec.precheck import (
    ScenarioIntentResult,
    check_fr_breadth,
    check_scenario_intents,
    run_precheck,
    _FR_BREADTH_INTENT_THRESHOLD,
)

_REPO = pathlib.Path(__file__).resolve().parents[2]


def _full_spec_with_intents(n_intents: int) -> str:
    """A structurally-complete spec whose §9 FR-1 declares ``n_intents`` rows."""
    intent_rows = "".join(
        f"| {i} | intent number {i} | happy_path |\n" for i in range(1, n_intents + 1)
    )
    return f"""\
# Test Spec

## Problem Statement
Pain.

## Functional Requirements
### FR-1: Create server
- Processing: validate env, create server
- Side effects: none

## Non-Functional Requirements
- p99 < 200ms

## Constraints
- node 22

## Interface Contract
- POST /api

## Edge Cases
- EC-1: bad input

## Error Handling
- 400 on invalid

## Acceptance Criteria
| ID | Criterion | Blocking |
|----|-----------|:-:|
| AC-1 | GIVEN valid env WHEN server starts THEN it returns ok | no |

## Examples
- example

## Dependencies & Out of Scope
- none

## Scenario Intent
### FR-1: Create server
| # | Intent | Layer |
|---|--------|-------|
{intent_rows}"""


def _intents(**counts: int) -> ScenarioIntentResult:
    return ScenarioIntentResult(present=True, fr_intent_counts=dict(counts))


class TestCheckFrBreadth(unittest.TestCase):
    def test_fr_within_budget_no_finding(self) -> None:
        si = _intents(**{"FR-1: Conn": 9, "FR-2: Binding": _FR_BREADTH_INTENT_THRESHOLD})
        self.assertEqual(check_fr_breadth(si), [])

    def test_fr_over_budget_fires(self) -> None:
        n = _FR_BREADTH_INTENT_THRESHOLD + 1
        si = _intents(**{"FR-2: Binding": n})
        findings = check_fr_breadth(si)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].fr_id, "FR-2")
        self.assertEqual(findings[0].intent_count, n)
        self.assertEqual(findings[0].threshold, _FR_BREADTH_INTENT_THRESHOLD)

    def test_threshold_is_strict_greater_than(self) -> None:
        # Exactly at the budget does NOT fire (only > threshold).
        si = _intents(**{"FR-1: X": _FR_BREADTH_INTENT_THRESHOLD})
        self.assertEqual(check_fr_breadth(si), [])

    def test_numbered_fr_heading_matched(self) -> None:
        # A §9 sub-heading carrying a section number ("### 9.2 FR-2: ...") must
        # still attribute to FR-2 (Linus round-2: anchored optional-prefix).
        over = _FR_BREADTH_INTENT_THRESHOLD + 3
        si = _intents(**{"9.2 FR-2: Tenant binding": over})
        findings = check_fr_breadth(si)
        self.assertEqual([f.fr_id for f in findings], ["FR-2"])
        self.assertEqual(findings[0].intent_count, over)

    def test_pseudo_group_mentioning_fr_not_matched(self) -> None:
        # A pseudo-group label that merely MENTIONS an FR mid-string must NOT be
        # mistaken for an FR group (anchored, not a bare search).
        big = _FR_BREADTH_INTENT_THRESHOLD + 20
        si = _intents(**{"Forbidden patterns (see FR-2 exclusions)": big})
        self.assertEqual(check_fr_breadth(si), [])

    def test_pseudo_groups_skipped(self) -> None:
        # Cross-Cutting / Forbidden / (ungrouped) are not FRs — never flagged,
        # even when over budget.
        big = _FR_BREADTH_INTENT_THRESHOLD + 20
        si = _intents(**{
            "Cross-Cutting (optional, 0-10 intents)": big,
            "Forbidden patterns (exclusion scenarios)": big,
            "(ungrouped)": big,
        })
        self.assertEqual(check_fr_breadth(si), [])

    def test_multiple_frs_independent(self) -> None:
        over = _FR_BREADTH_INTENT_THRESHOLD + 1
        si = _intents(**{"FR-1: Small": 6, "FR-2: Big": over, "FR-3: Small": 8})
        findings = check_fr_breadth(si)
        self.assertEqual([f.fr_id for f in findings], ["FR-2"])
        self.assertEqual(findings[0].intent_count, over)

    def test_no_section_is_noop(self) -> None:
        self.assertEqual(check_fr_breadth(ScenarioIntentResult(present=False)), [])

    def test_custom_threshold(self) -> None:
        si = _intents(**{"FR-1: X": 7})
        self.assertEqual(check_fr_breadth(si, threshold=10), [])
        findings = check_fr_breadth(si, threshold=5)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].intent_count, 7)
        self.assertEqual(findings[0].threshold, 5)

    # --- corpus tests: guard against the check going structurally dead -------

    def test_canonical_template_within_budget(self) -> None:
        # The feature-spec template's §9 has small placeholder FR groups — well
        # within budget. Asserts the check parses the real structure and finds
        # nothing to flag (an INTENTIONAL no-op, not a structural-dead no-op).
        txt = (_REPO / "templates" / "feature-spec.md").read_text()
        si = check_scenario_intents(txt)
        self.assertTrue(si.present, "template §9 must parse (structure guard)")
        self.assertTrue(
            any(lbl.lower().startswith("fr-") for lbl in si.fr_intent_counts),
            "template §9 must expose real FR groups (structure guard)",
        )
        self.assertEqual(check_fr_breadth(si), [])

    def test_real_spec_intents_counted(self) -> None:
        # A real spec that DOES exercise §9 FR grouping (bb spec-4). Confirms the
        # check sees real per-FR counts (FR-2 has 13 intents — within the 15
        # budget, so no finding; the point is the structure is non-empty, not
        # dead). If this file is absent (clean checkout), skip rather than fail.
        spec = (
            pathlib.Path("~/sites/budget-brain-v2/.dark-factory"
                         "/specs/2026-03-28-database-foundation.md")
        )
        if not spec.exists():
            self.skipTest("bb spec-4 not present in this checkout")
        si = check_scenario_intents(spec.read_text())
        fr_groups = {k: v for k, v in si.fr_intent_counts.items()
                     if k.lower().startswith("fr-")}
        self.assertTrue(fr_groups, "spec-4 §9 must expose FR groups")
        self.assertTrue(max(fr_groups.values()) >= 8,
                        "spec-4 FR groups should carry real intent counts")
        # FR-2 = 13 intents, under the 15 budget → no finding (honest: the
        # flip-flop fix is the LLM blocker removal, not this warning firing).
        self.assertEqual(check_fr_breadth(si), [])


class TestFrBreadthIntegration(unittest.TestCase):
    """End-to-end through run_precheck — the advisory-to-gate safety guard.

    This codebase has a documented failure mode where an advisory finding
    accidentally becomes a hard gate (d2fecbf). These tests pin that an
    over-budget FR breadth finding NEVER sets passed=False / fatal=True."""

    def _run(self, n_intents: int):
        with tempfile.TemporaryDirectory() as td:
            spec = pathlib.Path(td) / "spec.md"
            spec.write_text(_full_spec_with_intents(n_intents))
            # mechanism gate off = pipeline default (Phase 4); isolates breadth.
            return run_precheck(spec, p4_mechanism_gate_enabled=False)

    def test_breadth_finding_surfaces_through_run_precheck(self) -> None:
        over = _FR_BREADTH_INTENT_THRESHOLD + 5
        r = self._run(over)
        self.assertEqual(len(r.fr_breadth), 1)
        self.assertEqual(r.fr_breadth[0].fr_id, "FR-1")
        self.assertEqual(r.fr_breadth[0].intent_count, over)
        self.assertFalse(r.fatal, "breadth finding must never be fatal")

    def test_breadth_does_not_move_the_gate(self) -> None:
        # The advisory-to-gate safety guard: crossing the breadth threshold must
        # NOT change passed/fatal. Asserted as a differential so it holds
        # regardless of whatever else the minimal scaffold trips.
        within = self._run(_FR_BREADTH_INTENT_THRESHOLD)
        over = self._run(_FR_BREADTH_INTENT_THRESHOLD + 5)
        self.assertEqual(within.fr_breadth, [], "within budget → no finding")
        self.assertEqual(len(over.fr_breadth), 1, "over budget → finding")
        # Same passed/fatal both ways → breadth has zero gating effect.
        self.assertEqual(within.passed, over.passed)
        self.assertEqual(within.fatal, over.fatal)
        self.assertFalse(over.fatal)


if __name__ == "__main__":
    unittest.main()
