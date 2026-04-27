"""Phase 4 B6.5: validate the `/df-densify-spec` skill's emitted §4a
example against B4's round-trip validator.

The skill's Step 3.5 emits a `forbidden_test_shapes:` YAML block that
spec authors copy verbatim into their specs. After B6.5, each entry
carries `canonical_violation:` (required by B4) and `canonical_clean:`
(optional defense-in-depth). If the skill's example is self-
inconsistent — regex doesn't match its declared canonical_violation,
or matches its declared canonical_clean — every spec author copying
the example would inherit the inconsistency. The spec's precheck
would then fail (B4 fatal-on-mismatch) instead of catching the foot-
gun the rule was meant to forbid.

This test is the regression backstop. It extracts the YAML block
from SKILL.md and round-trips it through `extract_negative_space` +
B4's validator. Empty `round_trip_errors` means the skill's example
is consistent.

A second test pins behavior parity with
``darkfactory.contracts.foot_gun_patterns.BY_NAME_AND_DIALECT`` —
B5's single-source-of-truth library — for three of four entries:
the skill's regex and the library's regex must produce the SAME
match decision against the skill's own canonical_violation +
canonical_clean snippets. Byte-equality is too tight (the library's
Python r-string and YAML-parsed string differ in escape
representation while compiling to the same regex). Behavior
equivalence catches drift that actually changes match decisions.

The fourth pattern (``owner-bypass-missing-force``) uses a gate-
friendly negative-lookahead form that differs from the library's
``ENABLE ROW LEVEL SECURITY`` shape (different consumer; documented
in the skill's prose). That entry's regex is NOT expected to behave
identically to the library's.
"""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from darkfactory.contracts.foot_gun_patterns import BY_NAME_AND_DIALECT
from darkfactory.spec.negative_space import (
    extract_negative_space,
)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_SKILL_PATH = (
    _REPO_ROOT / ".claude" / "skills" / "df-densify-spec" / "SKILL.md"
)


# Semantic anchor (HTML comment in markdown) that immediately
# precedes the YAML block we want to extract. The skill's prose
# emits this anchor above the canonical_violation-bearing example
# block. Renaming this test file does NOT silently break the
# self-pin (Linus B6.5 review #4 — coupling the skill to the test
# filename was fragile).
_BLOCK_MARKER = "<!-- B6.5-FORBIDDEN-TEST-SHAPES-EXAMPLE -->"


def _extract_forbidden_test_shapes_yaml() -> str:
    """Return the raw YAML body of SKILL.md's
    ``forbidden_test_shapes:`` example block. Locates the FIRST
    ```yaml fence after the B6.5 marker text the skill's prose
    contains; everything inside that fence is the example.
    """
    text = _SKILL_PATH.read_text(encoding="utf-8")
    marker_idx = text.find(_BLOCK_MARKER)
    assert marker_idx != -1, (
        f"B6.5 self-pin marker {_BLOCK_MARKER!r} missing from "
        f"{_SKILL_PATH}. Either restore the marker in the skill's "
        "prose or update this test to use a different anchor."
    )
    fence_start = text.find("```yaml", marker_idx)
    assert fence_start != -1, (
        "no ```yaml fence found after the B6.5 marker in SKILL.md"
    )
    body_start = text.index("\n", fence_start) + 1
    body_end = text.find("```", body_start)
    assert body_end != -1, "unterminated ```yaml fence in SKILL.md"
    return text[body_start:body_end]


def _spec_with_yaml_block(yaml_body: str) -> str:
    """Wrap the YAML body in a minimal §4a-bearing spec text the
    ``extract_negative_space`` parser can ingest."""
    return (
        "## 4a. Negative Space\n\n"
        f"```yaml\n{yaml_body}```\n"
    )


class TestSkillExampleParsesAndRoundTripsCleanly(unittest.TestCase):
    """The skill's `forbidden_test_shapes:` example, fed verbatim
    through ``extract_negative_space`` + B4 round-trip validation,
    must produce zero parse_errors AND zero round_trip_errors. If
    EITHER list is non-empty, every spec author copying the example
    inherits the inconsistency.
    """

    def test_skill_yaml_extractable(self) -> None:
        body = _extract_forbidden_test_shapes_yaml()
        self.assertIn("forbidden_test_shapes:", body)

    def test_skill_yaml_parses(self) -> None:
        body = _extract_forbidden_test_shapes_yaml()
        # Sanity: the block parses as YAML.
        parsed = yaml.safe_load(body)
        self.assertIsInstance(parsed, dict)
        self.assertIn("forbidden_test_shapes", parsed)
        self.assertEqual(len(parsed["forbidden_test_shapes"]), 4)

    def test_skill_yaml_has_no_parse_errors(self) -> None:
        body = _extract_forbidden_test_shapes_yaml()
        spec = _spec_with_yaml_block(body)
        findings = extract_negative_space(spec)
        self.assertEqual(
            findings.parse_errors, [],
            f"skill YAML produces parse errors: {findings.parse_errors!r}",
        )

    def test_skill_yaml_has_no_round_trip_errors(self) -> None:
        body = _extract_forbidden_test_shapes_yaml()
        spec = _spec_with_yaml_block(body)
        findings = extract_negative_space(spec)
        self.assertEqual(
            findings.round_trip_errors, [],
            "B6.5 skill example is B4-inconsistent. Errors: "
            f"{findings.round_trip_errors!r}. Either the regex "
            "doesn't match its declared canonical_violation, or it "
            "matches its declared canonical_clean. Spec authors "
            "copying the broken example would have specs rejected "
            "at precheck (B4 fatal). Fix the skill's SKILL.md.",
        )


class TestEveryEntryHasCanonicalViolation(unittest.TestCase):
    """B6.5's whole point is that the skill emits B4 round-trip
    pins. Every entry MUST carry ``canonical_violation`` (otherwise
    B4 doesn't pin anything for that pattern). ``canonical_clean``
    is optional (defense-in-depth) but the skill ships it for all
    four entries today; pin that as a positive expectation."""

    def test_every_entry_has_canonical_violation(self) -> None:
        body = _extract_forbidden_test_shapes_yaml()
        parsed = yaml.safe_load(body)
        for i, entry in enumerate(parsed["forbidden_test_shapes"]):
            with self.subTest(entry_index=i, pattern=entry.get("pattern")):
                self.assertIn(
                    "canonical_violation", entry,
                    f"entry {i} missing `canonical_violation` — the "
                    "skill's purpose is to emit B4 round-trip pins. "
                    "Without it, B4 doesn't validate this regex.",
                )
                self.assertIsInstance(entry["canonical_violation"], str)
                self.assertGreater(
                    len(entry["canonical_violation"].strip()), 0,
                )

    def test_every_entry_has_canonical_clean(self) -> None:
        body = _extract_forbidden_test_shapes_yaml()
        parsed = yaml.safe_load(body)
        for i, entry in enumerate(parsed["forbidden_test_shapes"]):
            with self.subTest(entry_index=i, pattern=entry.get("pattern")):
                self.assertIn(
                    "canonical_clean", entry,
                    f"entry {i} missing `canonical_clean` — B6.5 "
                    "ships defense-in-depth false-positive pins on "
                    "all four entries. If you intentionally dropped "
                    "`canonical_clean` from one, update this test.",
                )


class TestThreeOfFourPatternsBehaveAsB5Library(unittest.TestCase):
    """Three of the skill's four patterns target the same foot-gun
    shapes as B5's canonical library at
    ``darkfactory.contracts.foot_gun_patterns``
    (peer-client-guc-read, parameterized-set-local,
    strict-null-empty-guc-check). Byte-equality is too tight (the
    library's Python r-string and YAML-parsed string differ in
    escape representation while compiling to the same regex);
    behavior parity is what matters: both regexes must agree on
    the skill's own canonical snippets (match the violation, miss
    the clean).

    The fourth (owner-bypass-missing-force) uses a gate-friendly
    negative-lookahead form documented in the skill's prose."""

    EXPECTED_LIBRARY_PARITY: tuple[tuple[str, str], ...] = (
        ("peer-client-guc-read", "ts"),
        ("parameterized-set-local", "ts"),
        ("strict-null-empty-guc-check", "ts"),
    )

    def test_three_patterns_behave_as_library(self) -> None:
        import re as _re
        body = _extract_forbidden_test_shapes_yaml()
        parsed = yaml.safe_load(body)
        entries_by_name: dict[str, dict] = {}
        for entry in parsed["forbidden_test_shapes"]:
            reason = entry.get("reason", "")
            for name, _ in self.EXPECTED_LIBRARY_PARITY:
                if reason.startswith(f"{name}:"):
                    entries_by_name[name] = entry
                    break

        for name, dialect in self.EXPECTED_LIBRARY_PARITY:
            with self.subTest(name=name):
                self.assertIn(name, entries_by_name)
                entry = entries_by_name[name]
                skill_re = _re.compile(entry["pattern"])
                lib_re = BY_NAME_AND_DIALECT[(name, dialect)].pattern
                # Both regexes MUST match the canonical violation and
                # MUST NOT match the canonical clean. If they disagree
                # on either, the skill's regex has drifted in behavior
                # from the library — the H2 failure mode B6.5 exists
                # to prevent.
                violation = entry["canonical_violation"]
                clean = entry["canonical_clean"]
                self.assertTrue(
                    skill_re.search(violation),
                    f"skill regex for {name!r} doesn't match its "
                    "own canonical_violation — would fail B4 precheck.",
                )
                self.assertTrue(
                    lib_re.search(violation),
                    f"library regex for {name!r} doesn't match the "
                    "skill's canonical_violation — drift between B5 "
                    "and the skill on what counts as a violation.",
                )
                self.assertFalse(
                    skill_re.search(clean),
                    f"skill regex for {name!r} matches its own "
                    "canonical_clean — false-positive on innocent "
                    "code.",
                )
                self.assertFalse(
                    lib_re.search(clean),
                    f"library regex for {name!r} matches the "
                    "skill's canonical_clean — drift between B5 and "
                    "the skill on what counts as innocent code.",
                )


class TestOwnerBypassUsesNegativeLookaheadForm(unittest.TestCase):
    """The fourth entry (`owner-bypass-missing-force`) deliberately
    uses a negative-lookahead regex that differs from B5's library
    pattern (which is just `ENABLE\\s+ROW\\s+LEVEL\\s+SECURITY`).
    Reason: the spec's regex runs at gate time under scenario_lint
    without post-match logic, so it needs the negative-lookahead to
    avoid over-firing on legitimate ENABLE+FORCE migrations. Pin
    the gate-friendly shape so a future maintainer doesn't
    "tidy-align" it back to the library form and break gate-time
    behavior."""

    def test_owner_bypass_uses_negative_lookahead(self) -> None:
        body = _extract_forbidden_test_shapes_yaml()
        parsed = yaml.safe_load(body)
        for entry in parsed["forbidden_test_shapes"]:
            reason = entry.get("reason", "")
            if reason.startswith("owner-bypass-missing-force:"):
                pattern = entry["pattern"]
                # Negative lookahead syntax `(?!...)` must be present.
                self.assertIn(
                    "(?!", pattern,
                    "owner-bypass-missing-force regex must use a "
                    "negative lookahead — the gate-friendly form. "
                    "If the library's shape is being adopted here, "
                    "scenario_lint also needs a post-match "
                    "absence-of-FORCE consumer or it'll over-fire.",
                )
                self.assertIn("FORCE", pattern)
                return
        self.fail("no owner-bypass-missing-force entry found")


if __name__ == "__main__":
    unittest.main()
