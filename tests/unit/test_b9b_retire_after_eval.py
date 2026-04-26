"""Tests for the retire_after auto-eval pattern-match harness.

ROADMAP.md Phase 3 B9b — second commit (foundation in commit
``e107479``). Pin the harness's verdict logic + helper-import
detection edge cases so the future LLM-invocation hook commit
ships against a verified-correct comparison surface.
"""
from __future__ import annotations

import json
import unittest
from dataclasses import asdict

from darkfactory.adapters.postgres.retire_after_eval import (
    EVAL_RESULT_SCHEMA_VERSION,
    EvalResult,
    RoundResult,
    _imports_helper,
    evaluate_fixture,
)
from darkfactory.adapters.postgres.retire_after_fixtures import (
    FIXTURES,
    Fixture,
)


# Synthetic "vanilla model output" per fixture: TS test snippets that
# (a) do NOT import the helper and (b) do NOT exhibit the foot-gun
# pattern. These represent the production-runner's success case — a
# new model produces safe code without the helper's guidance, so the
# helper is retire-able.
_VANILLA_OUTPUT_BY_HELPER: dict[str, str] = {
    # No `.toBeNull()` near `current_setting` — uses the explicit
    # postgres.js wire form (empty string).
    "assertUnsetGucIsNullish": '''\
import { describe, it, expect } from 'vitest';

describe('tenant context', () => {
  it('starts as the empty string before the app issues SET LOCAL', async () => {
    const rows = await client`SELECT current_setting('app.tenant_id', true) AS v`;
    expect(rows[0]?.v).toBe('');
  });
});
''',
    # Uses `SELECT set_config(name, value, true)` (bindable) instead
    # of `tx`SET LOCAL app.x = '${val}'`` (parameterized SET LOCAL —
    # foot-gun).
    "assertTxGucRoundTrips": '''\
import { describe, it, expect } from 'vitest';

describe('tenant context', () => {
  it('round-trips a tenant id via set_config', async () => {
    const tenantId = 'acme';
    await client.begin(async (tx) => {
      await tx`SELECT set_config('app.tenant_id', ${tenantId}, true)`;
      const rows = await tx`SELECT current_setting('app.tenant_id', true) AS v`;
      expect(rows[0]?.v).toBe(tenantId);
    });
  });
});
''',
    # RLS fixture has no synthetic-vanilla because the harness
    # always returns "inconclusive" for foot_gun_pattern=None — see
    # TestPatternNoneFixturesAlwaysInconclusive below.
}


def _fixture(name: str) -> Fixture:
    """Look up a Fixture by helper name; raises if missing."""
    for f in FIXTURES:
        if f.helper_name == name:
            return f
    raise AssertionError(f"no Fixture for helper {name!r}")


class TestEmptyRoundsInconclusive(unittest.TestCase):
    """Zero rounds = zero evidence either way."""

    def test_empty_input_for_every_fixture(self) -> None:
        for fixture in FIXTURES:
            with self.subTest(helper=fixture.helper_name):
                result = evaluate_fixture(fixture, [])
                self.assertEqual(
                    result.verdict, "inconclusive",
                    f"empty rounds for {fixture.helper_name!r} must "
                    "yield 'inconclusive' — zero evidence is not "
                    "evidence to retire"
                )
                self.assertEqual(result.rounds, 0)
                self.assertEqual(result.foot_gun_hits, 0)
                self.assertEqual(result.helper_import_count, 0)
                self.assertEqual(result.per_round, ())


class TestAllWrongCodeKeeps(unittest.TestCase):
    """Every round exhibits the foot-gun → ``keep``. Helper coverage
    is load-bearing; do not retire.
    """

    def test_all_wrong_for_pattern_bearing_fixtures(self) -> None:
        for fixture in FIXTURES:
            if fixture.foot_gun_pattern is None:
                continue
            with self.subTest(helper=fixture.helper_name):
                result = evaluate_fixture(
                    fixture, [fixture.wrong_code] * 5,
                )
                self.assertEqual(
                    result.verdict, "keep",
                    f"5× wrong_code for {fixture.helper_name!r} must "
                    f"yield 'keep' — foot-gun appears in every "
                    f"round, helper coverage is load-bearing"
                )
                self.assertEqual(
                    result.foot_gun_hits, 5,
                    "every wrong-code round must hit the foot-gun "
                    "pattern (TestRegexDistinguisher in "
                    "test_b9b_retire_after_pins.py pins this for "
                    "rounds=1; this test extends to N=5)"
                )
                self.assertEqual(result.helper_import_count, 0)


class TestAllCorrectCodeKeeps(unittest.TestCase):
    """Every round imports the helper → ``keep``. Production-runner
    workflow strips the helper from the staging tree, so imports
    SHOULD be 0 in vanilla output; a non-zero count is either
    runner-setup-buggy (helper still findable) OR a test-input
    contrivance. Either way, the verdict ``keep`` is the
    conservative response.
    """

    def test_all_correct_for_every_fixture(self) -> None:
        for fixture in FIXTURES:
            with self.subTest(helper=fixture.helper_name):
                result = evaluate_fixture(
                    fixture, [fixture.correct_code] * 5,
                )
                # Pattern-bearing fixtures: verdict is 'keep' because
                # helper_imports > 0. Pattern-None fixture: verdict
                # is 'inconclusive' regardless (covered separately).
                if fixture.foot_gun_pattern is not None:
                    self.assertEqual(
                        result.verdict, "keep",
                        f"5× correct_code for {fixture.helper_name!r} "
                        "must yield 'keep' — helper is being imported, "
                        "do not retire (the runner's vanilla prompt "
                        "should strip the helper from the staging "
                        "tree; if it doesn't, this verdict is the "
                        "conservative response)"
                    )
                else:
                    self.assertEqual(result.verdict, "inconclusive")
                self.assertEqual(result.foot_gun_hits, 0)
                self.assertEqual(result.helper_import_count, 5)


class TestSyntheticVanillaRetires(unittest.TestCase):
    """The retire path: model produces safe code WITHOUT importing
    the helper. This is the production-runner success case.
    """

    def test_5_rounds_synthetic_vanilla_yields_retire(self) -> None:
        for helper_name, vanilla in _VANILLA_OUTPUT_BY_HELPER.items():
            with self.subTest(helper=helper_name):
                fixture = _fixture(helper_name)
                result = evaluate_fixture(fixture, [vanilla] * 5)
                self.assertEqual(
                    result.verdict, "retire",
                    f"5× synthetic-vanilla for {helper_name!r} must "
                    "yield 'retire' — output has no foot-gun and no "
                    "helper import, helper coverage is redundant"
                )
                self.assertEqual(result.foot_gun_hits, 0)
                self.assertEqual(result.helper_import_count, 0)
                self.assertEqual(result.rounds, 5)
                # Per-round details: all foot_gun_hit=False (pattern
                # bearing fixture), all imports_helper=False.
                self.assertEqual(len(result.per_round), 5)
                for round_result in result.per_round:
                    self.assertEqual(round_result.foot_gun_hit, False)
                    self.assertEqual(round_result.imports_helper, False)


class TestMixedRoundsKeep(unittest.TestCase):
    """Strict N/N pass: a single foot-gun hit OR helper import in
    any round flips the verdict away from 'retire'.
    """

    def test_4_vanilla_plus_1_wrong_yields_keep(self) -> None:
        helper_name = "assertUnsetGucIsNullish"
        fixture = _fixture(helper_name)
        vanilla = _VANILLA_OUTPUT_BY_HELPER[helper_name]
        rounds = [vanilla] * 4 + [fixture.wrong_code]
        result = evaluate_fixture(fixture, rounds)
        self.assertEqual(
            result.verdict, "keep",
            "4× vanilla + 1× wrong_code must yield 'keep' — strict "
            "N/N is the verdict policy; one foot-gun is decisive"
        )
        self.assertEqual(result.foot_gun_hits, 1)
        self.assertEqual(result.helper_import_count, 0)

    def test_4_vanilla_plus_1_correct_yields_keep(self) -> None:
        helper_name = "assertUnsetGucIsNullish"
        fixture = _fixture(helper_name)
        vanilla = _VANILLA_OUTPUT_BY_HELPER[helper_name]
        rounds = [vanilla] * 4 + [fixture.correct_code]
        result = evaluate_fixture(fixture, rounds)
        self.assertEqual(
            result.verdict, "keep",
            "4× vanilla + 1× correct_code must yield 'keep' — even "
            "one helper import in a vanilla-prompt run is evidence "
            "the helper is still in use"
        )
        self.assertEqual(result.foot_gun_hits, 0)
        self.assertEqual(result.helper_import_count, 1)


class TestPatternNoneFixturesAlwaysInconclusive(unittest.TestCase):
    """Fixtures with ``foot_gun_pattern=None`` (RLS) always yield
    'inconclusive'. The harness can count helper imports but cannot
    tell a "safe inline check" from a "row-count foot-gun" without
    adapter-specific structural analysis (per the honest design-limit
    documented in retire_after_fixtures.py).
    """

    def test_rls_fixture_inconclusive_regardless_of_input(self) -> None:
        rls = _fixture("assertRlsEnabled")
        self.assertIsNone(
            rls.foot_gun_pattern,
            "TestPatternNoneFixturesAlwaysInconclusive presupposes "
            "the RLS fixture has foot_gun_pattern=None; if that "
            "changes, this test must be reconsidered"
        )
        # All-wrong input — no helper imports, foot-gun pattern is
        # None so no hits counted.
        result = evaluate_fixture(rls, [rls.wrong_code] * 5)
        self.assertEqual(result.verdict, "inconclusive")
        self.assertEqual(result.foot_gun_hits, 0)
        self.assertEqual(result.helper_import_count, 0)
        # All-correct input — helper imported in every round.
        result = evaluate_fixture(rls, [rls.correct_code] * 5)
        self.assertEqual(result.verdict, "inconclusive")
        self.assertEqual(result.helper_import_count, 5)
        # Per-round details: foot_gun_hit must be None (not False)
        # for every round — distinguishes "pattern checked, missed"
        # from "pattern not present."
        for r in result.per_round:
            self.assertIsNone(
                r.foot_gun_hit,
                "RLS round_result.foot_gun_hit must be None "
                "(distinguishes 'no pattern to check' from "
                "'pattern checked, missed')"
            )


class TestImportsHelperEdgeCases(unittest.TestCase):
    """Pin the helper-import detection's tightening over the
    foundation commit's bare-name check (which would false-positive
    on TS comments mentioning the helper).
    """

    _HELPER = "assertUnsetGucIsNullish"

    def test_comment_mentioning_helper_does_not_count(self) -> None:
        code = "// assertUnsetGucIsNullish foot-gun-prone shape\n"
        self.assertFalse(
            _imports_helper(code, self._HELPER),
            "TS comment mentioning the helper must NOT count as an "
            "import — bare-name match would false-positive here, "
            "the import-statement regex tightens detection"
        )

    def test_aliased_import_counts(self) -> None:
        code = (
            "import { assertUnsetGucIsNullish as assertNullish } "
            "from '../_contracts/postgres';"
        )
        self.assertTrue(
            _imports_helper(code, self._HELPER),
            "import { X as alias } must count as importing X "
            "(the imported name is in the body; the alias is the "
            "local binding)"
        )

    def test_import_from_non_staged_path_does_not_count(self) -> None:
        code = (
            "import { assertUnsetGucIsNullish } "
            "from './local-helpers';"
        )
        self.assertFalse(
            _imports_helper(code, self._HELPER),
            "import from a non-staged path must NOT count — the "
            "helper-import-discipline contract is specifically about "
            "imports from `_contracts/postgres` (or "
            "`_contracts/postgres/project`)"
        )

    def test_import_from_project_subpath_counts(self) -> None:
        code = (
            "import { assertUnsetGucIsNullish } "
            "from '../_contracts/postgres/project';"
        )
        self.assertTrue(
            _imports_helper(code, self._HELPER),
            "imports from the path-namespaced project subpath must "
            "count (B9d's project-staging path); the runner's "
            "vanilla prompt strips the helper from BOTH framework "
            "and project staging trees"
        )

    def test_multiline_import_counts(self) -> None:
        code = (
            "import {\n"
            "  assertUnsetGucIsNullish,\n"
            "  assertTxGucRoundTrips,\n"
            "} from '../_contracts/postgres';\n"
        )
        self.assertTrue(
            _imports_helper(code, self._HELPER),
            "multi-line TS imports (common in formatter-driven code) "
            "must count — the body regex is non-greedy across newlines"
        )

    def test_other_helper_in_same_import_does_not_match(self) -> None:
        code = (
            "import { assertTxGucRoundTrips } "
            "from '../_contracts/postgres';"
        )
        self.assertFalse(
            _imports_helper(code, self._HELPER),
            "an import-statement importing a DIFFERENT helper must "
            "not match — the helper-name search is whole-word"
        )

    def test_substring_collision_does_not_match(self) -> None:
        """A helper named ``X`` and an unrelated symbol named
        ``Xfoo`` must NOT match — the chunk match is whole-name
        (after `as` strip), not substring. Catches regressions where
        a maintainer relaxes the match to ``\\b...\\b`` regex (which
        would have false-matched against ``XfooBar`` if there's a
        word-boundary break in the longer name).
        """
        code = (
            "import { assertUnsetGucIsNullishOrEmpty } "
            "from '../_contracts/postgres';"
        )
        self.assertFalse(
            _imports_helper(code, "assertUnsetGucIsNullish"),
            "a helper-name that is a strict prefix of the imported "
            "symbol must NOT match — chunks compare by whole-name"
        )

    def test_comment_inside_named_imports_does_not_count(self) -> None:
        """Comments INSIDE the named-imports body (between ``{`` and
        ``}``) must not false-positive. Both line and block forms.
        """
        line_form = (
            "import {\n"
            "  assertTxGucRoundTrips, // also see "
            "assertUnsetGucIsNullish\n"
            "} from '../_contracts/postgres';\n"
        )
        self.assertFalse(
            _imports_helper(line_form, self._HELPER),
            "TS line comment INSIDE named-imports body mentioning "
            "the helper must NOT count — comments are stripped "
            "before the chunk match"
        )
        block_form = (
            "import { assertTxGucRoundTrips /* a la "
            "assertUnsetGucIsNullish */ } "
            "from '../_contracts/postgres';"
        )
        self.assertFalse(
            _imports_helper(block_form, self._HELPER),
            "TS block comment INSIDE named-imports body mentioning "
            "the helper must NOT count"
        )

    def test_type_only_import_does_not_count(self) -> None:
        """``import type { X } from '...'`` is a type-only import.
        It compiles to a no-op and doesn't represent runtime use of
        the helper. Pinned per Linus B9b commit-2 review — the
        decision is "type-only doesn't count," not "type-only
        counts."
        """
        code = (
            "import type { assertUnsetGucIsNullish } "
            "from '../_contracts/postgres';"
        )
        self.assertFalse(
            _imports_helper(code, self._HELPER),
            "`import type { X }` must NOT count — type-only imports "
            "compile to no-op"
        )

    def test_inline_type_modifier_does_not_count(self) -> None:
        """``import { type X, Y } from '...'`` mixes a type-only
        named-import (``type X``) with a runtime named-import (``Y``).
        The type-only one must not match; a separate runtime one
        does.
        """
        code = (
            "import { type assertUnsetGucIsNullish, "
            "assertTxGucRoundTrips } "
            "from '../_contracts/postgres';"
        )
        self.assertFalse(
            _imports_helper(code, self._HELPER),
            "inline `type X` named-import must NOT count for X"
        )
        # Sanity: the runtime import on the same line is still
        # detected for its own helper name.
        self.assertTrue(
            _imports_helper(code, "assertTxGucRoundTrips"),
            "runtime named-import on the same line as a `type X` "
            "must still be detected"
        )

    def test_reexport_does_not_count(self) -> None:
        """``export { X } from '...'`` is a forwarding declaration,
        not a runtime use. Pinned per Linus B9b commit-2 review.
        """
        code = (
            "export { assertUnsetGucIsNullish } "
            "from '../_contracts/postgres';"
        )
        self.assertFalse(
            _imports_helper(code, self._HELPER),
            "re-export must NOT count — it's a forwarding "
            "declaration, not a use"
        )

    def test_subpath_under_contracts_postgres_counts(self) -> None:
        """Subpaths under ``_contracts/postgres/`` (beyond the
        explicit ``project`` subdir) must match. Pin so a future
        regex tightening doesn't accidentally narrow to only the
        flat-and-project-subdir forms.
        """
        for subpath in ("project", "project/extras", "framework"):
            code = (
                "import { assertUnsetGucIsNullish } "
                f"from '../_contracts/postgres/{subpath}';"
            )
            with self.subTest(subpath=subpath):
                self.assertTrue(
                    _imports_helper(code, self._HELPER),
                    f"import from `_contracts/postgres/{subpath}` "
                    "must match — the regex accepts any subpath"
                )


class TestEvalResultShape(unittest.TestCase):
    """The audit artifact a future commit will persist must be JSON-
    serializable and frozen.
    """

    def test_eval_result_is_json_serializable(self) -> None:
        """The future audit-artifact writer (B9b commit 3) will
        persist EvalResult to ``.dark-factory/audit/auto-eval/...``
        via ``json.dumps(asdict(result), ...)``. Pin that
        ``asdict()`` produces a JSON-serializable structure with the
        schema_version field and the documented per-round shape.
        """
        fixture = _fixture("assertUnsetGucIsNullish")
        result = evaluate_fixture(fixture, [fixture.wrong_code] * 3)
        text = json.dumps(asdict(result), sort_keys=True)
        parsed = json.loads(text)
        # Schema version pinned — bumping requires deliberate edit.
        self.assertEqual(
            parsed["schema_version"], EVAL_RESULT_SCHEMA_VERSION,
        )
        # Per-round structure pinned: list of dicts with the
        # documented keys, regardless of tuple→list conversion.
        self.assertIsInstance(parsed["per_round"], list)
        self.assertEqual(len(parsed["per_round"]), 3)
        for entry in parsed["per_round"]:
            self.assertIn("foot_gun_hit", entry)
            self.assertIn("imports_helper", entry)

    def test_eval_result_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError
        fixture = _fixture("assertUnsetGucIsNullish")
        result = evaluate_fixture(fixture, [])
        with self.assertRaises(FrozenInstanceError):
            result.verdict = "mutated"  # type: ignore[misc]

    def test_round_result_is_frozen(self) -> None:
        from dataclasses import FrozenInstanceError
        r = RoundResult(foot_gun_hit=True, imports_helper=False)
        with self.assertRaises(FrozenInstanceError):
            r.foot_gun_hit = False  # type: ignore[misc]

    def test_verdict_is_in_closed_vocabulary(self) -> None:
        valid = {"retire", "keep", "inconclusive"}
        for fixture in FIXTURES:
            for inputs in (
                [],
                [fixture.wrong_code] * 3,
                [fixture.correct_code] * 3,
            ):
                with self.subTest(
                    helper=fixture.helper_name, n=len(inputs),
                ):
                    result = evaluate_fixture(fixture, inputs)
                    self.assertIn(result.verdict, valid)


class TestCrossFixtureSweep(unittest.TestCase):
    """Drift-catcher: when a fourth universal helper lands and the
    foundation tests catch it (`test_b9b_retire_after_pins.py::
    TestRetireAfterPinsExistForUniversalHelpers`), this sweep
    extends the runner's coverage automatically.
    """

    def test_every_fixture_evaluates_consistently(self) -> None:
        # Every fixture, when fed its own wrong_code N times, must
        # produce a non-retire verdict. Pin the universal property
        # rather than the specific verdict (pattern-None case is
        # 'inconclusive', pattern-bearing is 'keep'; both are
        # non-retire).
        for fixture in FIXTURES:
            with self.subTest(helper=fixture.helper_name):
                result = evaluate_fixture(
                    fixture, [fixture.wrong_code] * 3,
                )
                self.assertNotEqual(
                    result.verdict, "retire",
                    f"5× wrong_code for {fixture.helper_name!r} must "
                    "never yield 'retire' — that would mean the "
                    "harness is silently retiring a helper whose "
                    "fixture demonstrates a still-active foot-gun"
                )


if __name__ == "__main__":
    unittest.main()
