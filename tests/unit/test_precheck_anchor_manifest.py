"""Tests for AnchorManifest extraction and FR/AC scenario-intent enforcement."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.spec.precheck import (
    check_anchor_intent_coverage,
    extract_anchor_manifest,
    run_precheck,
)


_FULL_SPEC_TEMPLATE = """# Test Spec

## Problem Statement
Pain.

## Functional Requirements
### FR-1: Login
Users log in.
### FR-2: Logout
Users log out.

## Non-Functional Requirements
- p99 < 200ms

## Constraints
- python 3.11

## Interface Contract
- POST /login

## Edge Cases
- EC-1: empty body
- EC-2: malformed JSON

## Error Handling
- 400 on invalid

## Acceptance Criteria
- AC-1: GIVEN valid creds WHEN POST THEN 200
- AC-2: GIVEN invalid creds WHEN POST THEN 401

## Examples
- example

## Dependencies & Out of Scope
- none

## Scenario Intent

### FR-1: Login
| # | Intent | Layer |
|---|--------|-------|
| 1 | login happy [AC-1] | happy_path |
| 2 | login bad creds [AC-2] | error |
| 3 | login third | happy_path |
| 4 | login fourth | happy_path |
| 5 | login fifth | happy_path |

### FR-2: Logout
| # | Intent | Layer |
|---|--------|-------|
| 1 | logout one | happy_path |
| 2 | logout two | happy_path |
| 3 | logout three | happy_path |
| 4 | logout four | happy_path |
| 5 | logout five | happy_path |
"""


class TestExtractAnchorManifest(unittest.TestCase):
    def test_collects_fr_ac_ec_ids(self) -> None:
        manifest = extract_anchor_manifest(_FULL_SPEC_TEMPLATE)
        self.assertEqual(manifest.fr_ids, ["FR-1", "FR-2"])
        self.assertEqual(manifest.ac_ids, ["AC-1", "AC-2"])
        self.assertEqual(manifest.ec_ids, ["EC-1", "EC-2"])

    def test_declared_intents_counts_per_anchor(self) -> None:
        manifest = extract_anchor_manifest(_FULL_SPEC_TEMPLATE)
        # FR-1 appears as the section heading (5 rows context) plus AC-1/AC-2
        # are inline in the FR-1 group's rows.
        self.assertGreaterEqual(manifest.declared_intents.get("FR-1", 0), 5)
        self.assertGreaterEqual(manifest.declared_intents.get("FR-2", 0), 5)
        self.assertGreaterEqual(manifest.declared_intents.get("AC-1", 0), 1)
        self.assertGreaterEqual(manifest.declared_intents.get("AC-2", 0), 1)

    def test_empty_spec(self) -> None:
        manifest = extract_anchor_manifest("# Empty\n")
        self.assertEqual(manifest.fr_ids, [])
        self.assertEqual(manifest.ac_ids, [])
        self.assertEqual(manifest.ec_ids, [])
        self.assertEqual(manifest.declared_intents, {})


class TestAnchorIntentEnforcement(unittest.TestCase):
    def test_full_spec_passes(self) -> None:
        manifest = extract_anchor_manifest(_FULL_SPEC_TEMPLATE)
        issues = check_anchor_intent_coverage(manifest)
        self.assertEqual(issues, [])

    def test_missing_fr_in_intent_fails(self) -> None:
        # Replace ONLY the Scenario Intent sub-heading, leaving the FR section
        # heading intact so FR-2 is still declared.
        spec = _FULL_SPEC_TEMPLATE.replace(
            "### FR-2: Logout\n| # | Intent | Layer |",
            "### Other group\n| # | Intent | Layer |",
        )
        manifest = extract_anchor_manifest(spec)
        self.assertIn("FR-2", manifest.fr_ids)
        issues = check_anchor_intent_coverage(manifest)
        self.assertTrue(any("FR-2" in i for i in issues))

    def test_missing_ac_in_intent_fails(self) -> None:
        # Strip AC-1 and AC-2 inline anchors from the intent rows.
        spec = _FULL_SPEC_TEMPLATE.replace("[AC-1]", "").replace("[AC-2]", "")
        manifest = extract_anchor_manifest(spec)
        issues = check_anchor_intent_coverage(manifest)
        self.assertTrue(any("AC-1" in i for i in issues))
        self.assertTrue(any("AC-2" in i for i in issues))

    def test_missing_ec_NOT_enforced_at_precheck(self) -> None:
        # ECs are declared but never referenced in Scenario Intent — must NOT
        # generate an issue (post-gen gate handles ECs).
        manifest = extract_anchor_manifest(_FULL_SPEC_TEMPLATE)
        issues = check_anchor_intent_coverage(manifest)
        self.assertFalse(any("EC-" in i for i in issues))


class TestRunPrecheckAnchorWiring(unittest.TestCase):
    def test_run_precheck_persists_manifest_field(self) -> None:
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "spec.md"
            p.write_text(_FULL_SPEC_TEMPLATE, encoding="utf-8")
            result = run_precheck(p)
            self.assertIsNotNone(result.anchor_manifest)
            assert result.anchor_manifest is not None
            self.assertIn("FR-1", result.anchor_manifest.fr_ids)
            self.assertEqual(result.anchor_intent_issues, [])

    def test_run_precheck_fails_when_fr_missing_from_intent(self) -> None:
        spec = _FULL_SPEC_TEMPLATE.replace(
            "### FR-2: Logout\n| # | Intent | Layer |",
            "### Other group\n| # | Intent | Layer |",
        )
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "spec.md"
            p.write_text(spec, encoding="utf-8")
            result = run_precheck(p)
            self.assertFalse(result.passed)
            self.assertTrue(any("FR-2" in i for i in result.anchor_intent_issues))


if __name__ == "__main__":
    unittest.main()
