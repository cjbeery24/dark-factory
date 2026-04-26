"""ROADMAP.md Phase 3 B9b — foundation-commit tests.

Pins the metadata + fixture invariants that the auto-eval runner
(B9b follow-up commit) will depend on:

1. ``retire-after.toml`` declares a pin for every universal helper
   that ships in the framework (no missing pins).
2. Pin values are well-formed model-version identifiers.
3. Every pin has a corresponding ``Fixture`` in
   ``retire_after_fixtures.FIXTURES`` (and vice versa) — bidirectional
   pin↔fixture invariant.
4. For each fixture with a ``foot_gun_pattern``, the pattern fires on
   ``wrong_code`` and misses ``correct_code`` (regex distinguisher).
5. For every fixture, ``wrong_code`` does NOT import the helper and
   ``correct_code`` DOES — helper-import discipline is the foundation
   the regex check stacks on top of.
6. ``Fixture.retire_after`` agrees with the TOML pin's
   ``retire_after`` for the same helper (cross-pin: catches drift if
   one is bumped without the other).

These five invariants are the regression backstop for the auto-eval
runner. Without them the runner ships on top of unverified inputs and
any drift in the regex/wrong-code coupling silently breaks the eval.
"""
from __future__ import annotations

import re
import tomllib
import unittest
from pathlib import Path

from darkfactory.adapters.postgres.retire_after_fixtures import (
    FIXTURES,
    Fixture,
)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_RETIRE_AFTER_TOML = (
    _REPO_ROOT / "darkfactory" / "adapters" / "postgres" / "retire-after.toml"
)


# Universal helpers per ROADMAP.md Phase 3 §"Exit criteria" — the
# three that stay in the framework after B9a v(N+1). Pinning the set
# explicitly here (rather than scanning ``contracts/ts/``) means a
# future addition of a fourth universal helper deliberately fails this
# test until the maintainer adds both the TOML pin AND the
# ``retire_after_fixtures.py`` Fixture — exactly the bidirectional
# discipline the foundation commit's contract demands.
_UNIVERSAL_HELPERS: frozenset[str] = frozenset({
    "assertUnsetGucIsNullish",
    "assertTxGucRoundTrips",
    "assertRlsEnabled",
})


# Closed model-version vocabulary (regex). The shape pinned here
# mirrors the canonical IDs in ``CLAUDE.md`` — ``claude-(opus|sonnet|
# haiku)-N-M`` with optional date suffix. Keeps test #2 cheap while
# rejecting obvious typos (``claude-opus-4`` without minor; or a
# free-text "current model" string).
_MODEL_VERSION_RE = re.compile(
    r"^claude-(?:opus|sonnet|haiku)-\d+-\d+(?:-\d{8})?$",
)


def _load_pins() -> dict[str, dict[str, str]]:
    """Return ``{helper_name: {retire_after: ..., rationale: ...}}``.

    Loaded from the packaged TOML at module load time. Tests subclass
    ``unittest.TestCase`` and call this helper rather than caching at
    module scope so a malformed TOML fails the relevant test cleanly
    rather than crashing import.
    """
    text = _RETIRE_AFTER_TOML.read_text(encoding="utf-8")
    data = tomllib.loads(text)
    out: dict[str, dict[str, str]] = {}
    for entry in data.get("pin", []):
        helper = entry.get("helper")
        if not isinstance(helper, str):
            raise AssertionError(
                f"retire-after.toml [[pin]] entry missing string "
                f"`helper` field: {entry!r}"
            )
        out[helper] = {
            "retire_after": str(entry.get("retire_after", "")),
            "rationale": str(entry.get("rationale", "")),
        }
    return out


class TestRetireAfterPinsExistForUniversalHelpers(unittest.TestCase):
    """The TOML must declare a pin for every universal helper, and
    nothing else. Missing pin = missing auto-eval coverage; extra pin
    = stale entry pointing at a removed helper.
    """

    def test_pinned_helpers_match_universal_set_exactly(self) -> None:
        pins = _load_pins()
        self.assertEqual(
            frozenset(pins.keys()), _UNIVERSAL_HELPERS,
            f"retire-after.toml pin set {sorted(pins.keys())!r} "
            f"does not match the universal-helper set "
            f"{sorted(_UNIVERSAL_HELPERS)!r}. Adding a universal "
            "helper requires adding a [[pin]] row; removing one "
            "requires removing the [[pin]]; this test catches both "
            "drift modes."
        )

    def test_schema_version_is_pinned(self) -> None:
        text = _RETIRE_AFTER_TOML.read_text(encoding="utf-8")
        data = tomllib.loads(text)
        self.assertEqual(
            data.get("schema_version"), 1,
            "retire-after.toml must declare `schema_version = 1` at "
            "top level. Schema bumps are the signal to update the "
            "auto-eval runner's parser."
        )


class TestRetireAfterPinValues(unittest.TestCase):
    """Pin values must be well-formed model identifiers."""

    def test_every_pin_has_a_well_formed_model_version(self) -> None:
        for helper, fields in _load_pins().items():
            with self.subTest(helper=helper):
                value = fields["retire_after"]
                self.assertRegex(
                    value, _MODEL_VERSION_RE,
                    f"retire_after value for {helper!r} is "
                    f"{value!r} — must match "
                    f"{_MODEL_VERSION_RE.pattern!r} "
                    "(claude-(opus|sonnet|haiku)-N-M[-YYYYMMDD]). "
                    "Free-text values defeat the auto-eval runner's "
                    "model-bump comparison."
                )

    def test_every_pin_has_a_non_empty_rationale(self) -> None:
        for helper, fields in _load_pins().items():
            with self.subTest(helper=helper):
                rationale = fields["rationale"]
                self.assertGreater(
                    len(rationale), 20,
                    f"retire_after rationale for {helper!r} is "
                    f"{rationale!r} — too short. The rationale tells "
                    "the auto-eval runner reviewer WHY the helper "
                    "exists and what the foot-gun is. A one-line "
                    "summary is the minimum useful artifact."
                )


class TestPinFixtureBidirectionalInvariant(unittest.TestCase):
    """Every TOML pin has a Fixture and every Fixture has a pin —
    no orphan metadata in either direction.
    """

    def test_every_pin_has_a_corresponding_fixture(self) -> None:
        pins = _load_pins()
        fixtures_by_name = {f.helper_name: f for f in FIXTURES}
        for helper in pins:
            with self.subTest(helper=helper):
                self.assertIn(
                    helper, fixtures_by_name,
                    f"retire-after.toml has a [[pin]] for {helper!r} "
                    "but `retire_after_fixtures.FIXTURES` has no "
                    "matching Fixture. Add the Fixture; the auto-eval "
                    "runner needs a canonical violation to compare "
                    "model output against."
                )

    def test_every_fixture_has_a_corresponding_pin(self) -> None:
        pins = _load_pins()
        for fixture in FIXTURES:
            with self.subTest(helper=fixture.helper_name):
                self.assertIn(
                    fixture.helper_name, pins,
                    f"retire_after_fixtures.FIXTURES has a Fixture "
                    f"for {fixture.helper_name!r} but retire-after."
                    "toml has no matching [[pin]]. Add the [[pin]] "
                    "with a retire_after model version."
                )

    def test_fixture_retire_after_matches_toml_pin(self) -> None:
        """Cross-pin: ``Fixture.retire_after`` must equal the TOML's
        ``retire_after`` for the same helper. Catches drift if one is
        bumped without the other (e.g., a maintainer rolls the TOML
        forward to a new model version but forgets to update the
        fixture, producing a runner-eval mismatch).
        """
        pins = _load_pins()
        for fixture in FIXTURES:
            with self.subTest(helper=fixture.helper_name):
                pin_value = pins.get(fixture.helper_name, {}).get(
                    "retire_after", ""
                )
                self.assertEqual(
                    fixture.retire_after, pin_value,
                    f"Fixture.retire_after for "
                    f"{fixture.helper_name!r} is "
                    f"{fixture.retire_after!r} but the TOML pin says "
                    f"{pin_value!r}. Drift between the two breaks "
                    "the auto-eval runner's model-bump comparison."
                )


class TestFootGunPatternDistinguishes(unittest.TestCase):
    """For every fixture with a ``foot_gun_pattern``, the regex must
    fire on ``wrong_code`` and miss ``correct_code``.

    The negative side (``correct_code`` doesn't match) is the
    over-greedy-regex safeguard: without it, a regex that happens to
    match both wrong and correct snippets would still produce a green
    test — silent regression.
    """

    def test_pattern_fires_on_wrong_misses_correct(self) -> None:
        for fixture in FIXTURES:
            if fixture.foot_gun_pattern is None:
                continue
            with self.subTest(helper=fixture.helper_name):
                self.assertRegex(
                    fixture.wrong_code, fixture.foot_gun_pattern,
                    f"foot_gun_pattern for {fixture.helper_name!r} "
                    "did not match `wrong_code` — the fixture's "
                    "wrong-code snippet must structurally exhibit "
                    "the foot-gun the helper exists to catch."
                )
                self.assertNotRegex(
                    fixture.correct_code, fixture.foot_gun_pattern,
                    f"foot_gun_pattern for {fixture.helper_name!r} "
                    "ALSO matched `correct_code` — the regex is "
                    "over-greedy. Tighten the pattern OR rewrite the "
                    "correct_code to avoid the spurious match."
                )


class TestHelperImportDiscipline(unittest.TestCase):
    """Every fixture's wrong_code must NOT import the helper, and
    correct_code MUST import it. This is the structural foundation
    the regex distinguisher stacks on top of — and the only
    distinguisher for fixtures whose foot-gun is the *absence* of a
    check (``foot_gun_pattern = None``).
    """

    def test_wrong_code_does_not_import_helper(self) -> None:
        for fixture in FIXTURES:
            with self.subTest(helper=fixture.helper_name):
                self.assertNotIn(
                    fixture.helper_name, fixture.wrong_code,
                    f"wrong_code for {fixture.helper_name!r} "
                    "imports / references the helper. The foot-gun "
                    "fixture must demonstrate the foot-gun *without* "
                    "the helper — else there's nothing for the "
                    "auto-eval runner to compare against."
                )

    def test_correct_code_imports_helper(self) -> None:
        for fixture in FIXTURES:
            with self.subTest(helper=fixture.helper_name):
                self.assertIn(
                    fixture.helper_name, fixture.correct_code,
                    f"correct_code for {fixture.helper_name!r} does "
                    "NOT reference the helper. The positive-control "
                    "snippet must use the helper, else it's not "
                    "actually showing the correct usage."
                )


class TestFixtureDataclassDiscipline(unittest.TestCase):
    """Pin lightweight invariants on the Fixture dataclass itself."""

    def test_fixture_is_frozen_dataclass(self) -> None:
        """Frozen so a maintainer can't mutate FIXTURES at runtime
        and produce a test pass that doesn't reflect the source.
        """
        from dataclasses import FrozenInstanceError
        fixture = FIXTURES[0]
        with self.assertRaises(FrozenInstanceError):
            fixture.helper_name = "mutated"  # type: ignore[misc]

    def test_fixture_unique_helper_names(self) -> None:
        names = [f.helper_name for f in FIXTURES]
        self.assertEqual(
            len(names), len(set(names)),
            f"FIXTURES has duplicate helper_name entries: {names!r}. "
            "Each helper must have exactly one Fixture."
        )


class TestPatternMatchesScenarioLint(unittest.TestCase):
    """Cross-module pin: the ``assertTxGucRoundTrips`` fixture's
    ``foot_gun_pattern`` must agree byte-for-byte with
    ``scripts/lib/scenario_lint.py:_PARAMETERIZED_SET_LOCAL_RE``.

    Why the test (not module-level importlib): a foundation data
    module should not take a runtime dep on a sibling script tree
    just to resolve its own static metadata. The pattern is duplicated
    inline in ``retire_after_fixtures.py`` and this test enforces the
    sync — a regex tightening in ``scenario_lint.py`` that doesn't
    propagate to the fixture fails this test, prompting the
    maintainer to update both together (or accept the divergence
    deliberately by editing the test).
    """

    def _load_scenario_lint_module(self):
        import importlib.util
        import sys

        spec_path = (
            _REPO_ROOT / "scripts" / "lib" / "scenario_lint.py"
        )
        spec = importlib.util.spec_from_file_location(
            "_scenario_lint_for_b9b_pin", spec_path,
        )
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("_scenario_lint_for_b9b_pin", mod)
        spec.loader.exec_module(mod)
        return mod

    def test_tx_guc_pattern_matches_scenario_lint(self) -> None:
        scenario_lint = self._load_scenario_lint_module()
        expected_re: re.Pattern[str] = (
            scenario_lint._PARAMETERIZED_SET_LOCAL_RE
        )
        tx_guc = next(
            f for f in FIXTURES
            if f.helper_name == "assertTxGucRoundTrips"
        )
        self.assertIsNotNone(
            tx_guc.foot_gun_pattern,
            "tx_guc fixture must have a foot_gun_pattern",
        )
        actual_re = tx_guc.foot_gun_pattern
        assert actual_re is not None  # for the type checker
        self.assertEqual(
            actual_re.pattern, expected_re.pattern,
            "tx_guc fixture's foot_gun_pattern has drifted from "
            "scripts/lib/scenario_lint.py:_PARAMETERIZED_SET_LOCAL_RE. "
            "Update both together — the fixture pattern duplicates "
            "scenario_lint's by design (single-source-of-truth via "
            "this test rather than module-import-time coupling)."
        )
        self.assertEqual(
            actual_re.flags, expected_re.flags,
            "tx_guc fixture's foot_gun_pattern flags have drifted "
            "from scripts/lib/scenario_lint.py:_PARAMETERIZED_SET_LOCAL_RE."
        )


if __name__ == "__main__":
    unittest.main()
