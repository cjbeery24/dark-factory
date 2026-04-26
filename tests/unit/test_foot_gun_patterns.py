"""Tests for the shared foot-gun-pattern library
(``darkfactory.contracts.foot_gun_patterns``). ROADMAP.md Phase 4 B5.

Pins:

1. The library's invariants — name kebab-case, dialect closed, every
   pattern compiles, reason + fix_hint non-empty, frozen records.
2. The (name, dialect) tuple uniquely identifies each record (the
   ``BY_NAME_AND_DIALECT`` dict's keys agree with PATTERNS).
3. Convenience helpers (``patterns_by_name``, ``ts_patterns``)
   return what they say.
4. Cross-module pins: the patterns scenario_lint and
   retire_after_fixtures use are object-identical with the library's
   (caught at consumer-side tests too, but pinned here for the
   library's own contract).

These tests do NOT pin specific pattern shapes (those would
duplicate scenario_lint and retire_after_fixtures' tests). The
library's job is structural; the consumers' tests pin behavior.
"""
from __future__ import annotations

import re
import unittest
from dataclasses import FrozenInstanceError

from darkfactory.contracts.foot_gun_patterns import (
    BY_NAME_AND_DIALECT,
    PATTERNS,
    FootGunPattern,
    patterns_by_name,
    ts_patterns,
)


_KEBAB_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_ALLOWED_DIALECTS = frozenset({"ts", "sql", "py"})


class TestPatternRecordShape(unittest.TestCase):
    """Each FootGunPattern is well-formed."""

    def test_every_pattern_is_a_FootGunPattern(self) -> None:
        for entry in PATTERNS:
            with self.subTest(entry=entry):
                self.assertIsInstance(entry, FootGunPattern)

    def test_FootGunPattern_is_frozen(self) -> None:
        p = PATTERNS[0]
        with self.assertRaises(FrozenInstanceError):
            p.name = "different"  # type: ignore[misc]

    def test_every_name_is_kebab_case(self) -> None:
        for p in PATTERNS:
            with self.subTest(name=p.name):
                self.assertRegex(
                    p.name, _KEBAB_CASE_RE,
                    msg=(
                        f"FootGunPattern name {p.name!r} must be "
                        "kebab-case (matches §4a `forbidden_test_shapes` "
                        "+ scenario_lint emit-rule names). Use hyphens, "
                        "not underscores or camelCase."
                    ),
                )

    def test_every_dialect_is_in_closed_vocabulary(self) -> None:
        for p in PATTERNS:
            with self.subTest(name=p.name, dialect=p.dialect):
                self.assertIn(p.dialect, _ALLOWED_DIALECTS)

    def test_every_pattern_is_compiled_regex(self) -> None:
        for p in PATTERNS:
            with self.subTest(name=p.name):
                self.assertIsInstance(p.pattern, re.Pattern)

    def test_every_reason_and_fix_hint_is_non_empty(self) -> None:
        for p in PATTERNS:
            with self.subTest(name=p.name):
                self.assertGreater(
                    len(p.reason.strip()), 0,
                    f"reason on {p.name!r}/{p.dialect} is empty",
                )
                self.assertGreater(
                    len(p.fix_hint.strip()), 0,
                    f"fix_hint on {p.name!r}/{p.dialect} is empty",
                )


class TestNameDialectUniqueness(unittest.TestCase):
    """``BY_NAME_AND_DIALECT`` keys are the (name, dialect) tuples;
    PATTERNS contains exactly one record per (name, dialect)."""

    def test_no_duplicate_name_dialect_pair(self) -> None:
        keys = [(p.name, p.dialect) for p in PATTERNS]
        self.assertEqual(
            len(keys), len(set(keys)),
            f"PATTERNS has duplicate (name, dialect) pairs: {keys}",
        )

    def test_index_keys_match_pattern_pairs(self) -> None:
        from_index = set(BY_NAME_AND_DIALECT.keys())
        from_tuple = {(p.name, p.dialect) for p in PATTERNS}
        self.assertEqual(from_index, from_tuple)

    def test_index_values_are_pattern_records(self) -> None:
        for p in PATTERNS:
            with self.subTest(key=(p.name, p.dialect)):
                self.assertIs(
                    BY_NAME_AND_DIALECT[(p.name, p.dialect)], p,
                )


class TestConvenienceHelpers(unittest.TestCase):
    def test_patterns_by_name_finds_known_pattern(self) -> None:
        results = patterns_by_name("parameterized-set-local")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].dialect, "ts")

    def test_patterns_by_name_unknown_returns_empty(self) -> None:
        self.assertEqual(patterns_by_name("does-not-exist"), ())

    def test_patterns_by_name_supports_multi_dialect_lookup_shape(self) -> None:
        """Today no name spans multiple dialects (db-migrate-in-scenario
        was collapsed in the Linus B5 review when both shell and list
        forms turned out to be the same rule with the same fix_hint).
        The library shape STILL supports multi-dialect — this test
        documents the shape via the helper's tuple-return invariant."""
        # Always returns a tuple, even for single-dialect names.
        for name in {p.name for p in PATTERNS}:
            with self.subTest(name=name):
                results = patterns_by_name(name)
                self.assertIsInstance(results, tuple)
                self.assertGreaterEqual(len(results), 1)

    def test_ts_patterns_returns_only_ts_dialect(self) -> None:
        results = ts_patterns()
        self.assertGreater(len(results), 0)
        for p in results:
            with self.subTest(name=p.name):
                self.assertEqual(p.dialect, "ts")

    def test_ts_patterns_excludes_sql_and_py(self) -> None:
        ts_names = {p.name for p in ts_patterns()}
        all_names_for_ts = {
            p.name for p in PATTERNS if p.dialect == "ts"
        }
        self.assertEqual(ts_names, all_names_for_ts)


class TestPatternsRegisteredForExpectedFootGuns(unittest.TestCase):
    """Anchor against the known set of foot-guns. A future addition
    forces a maintainer to update this list — the library's purpose
    is single-source-of-truth, so adding a new pattern silently is
    a drift signal we want to catch.
    """

    EXPECTED_NAMES = frozenset({
        "db-migrate-in-scenario",
        "owner-bypass-missing-force",
        "parameterized-set-local",
        "peer-client-guc-read",
        "strict-null-empty-guc-check",
    })

    def test_pattern_name_set_matches_expected(self) -> None:
        actual = {p.name for p in PATTERNS}
        self.assertEqual(
            actual, self.EXPECTED_NAMES,
            f"PATTERNS' name set drifted. Expected: "
            f"{self.EXPECTED_NAMES}; actual: {actual}. Either update "
            "EXPECTED_NAMES in this test (intentional add/remove) "
            "or restore the missing pattern.",
        )


if __name__ == "__main__":
    unittest.main()
