"""ROADMAP.md Phase 3 B9b — third-commit tests for the trigger-prompt
registry.

Pins the bidirectional invariant between
``retire_after_fixtures.FIXTURES`` and
``retire_after_trigger_prompts.TRIGGER_PROMPTS``: every universal
helper has matching entries in BOTH registries (plus
``retire-after.toml``). A helper added to one without the others
fails CI.

Also pins the scrubbed-mention property: a TriggerPrompt's body must
NOT contain the helper-name as a substring. Naming the helper in the
prompt biases the model toward importing it and defeats the auto-eval
signal (the runner is measuring "would the model find the helper on
its own"; a prompt that names it pre-empts the question).
"""
from __future__ import annotations

import unittest
from dataclasses import FrozenInstanceError

from darkfactory.adapters.postgres.retire_after_fixtures import FIXTURES
from darkfactory.adapters.postgres.retire_after_trigger_prompts import (
    BY_HELPER_NAME,
    TRIGGER_PROMPTS,
    TriggerPrompt,
)


class TestTriggerPromptShape(unittest.TestCase):
    """TriggerPrompt invariants — frozen dataclass + non-empty fields."""

    def test_trigger_prompt_is_frozen(self) -> None:
        prompt = TRIGGER_PROMPTS[0]
        with self.assertRaises(FrozenInstanceError):
            prompt.helper_name = "different"  # type: ignore[misc]

    def test_helper_names_are_unique(self) -> None:
        names = [tp.helper_name for tp in TRIGGER_PROMPTS]
        self.assertEqual(len(names), len(set(names)))

    def test_prompt_bodies_are_non_empty(self) -> None:
        for tp in TRIGGER_PROMPTS:
            with self.subTest(helper=tp.helper_name):
                self.assertGreater(len(tp.prompt_body.strip()), 0)

    def test_trigger_prompts_ordering_is_stable(self) -> None:
        """Stable alphabetical-by-helper-name ordering matches the
        FIXTURES tuple ordering — two registries with parallel
        ordering surface a maintainer-mistake in test reports
        deterministically (same row across both invariant tables)."""
        names = [tp.helper_name for tp in TRIGGER_PROMPTS]
        self.assertEqual(names, sorted(names))


class TestBidirectionalFixtureInvariant(unittest.TestCase):
    """Every Fixture has a matching TriggerPrompt and vice versa."""

    def test_every_fixture_has_a_trigger_prompt(self) -> None:
        fixture_names = {f.helper_name for f in FIXTURES}
        prompt_names = {tp.helper_name for tp in TRIGGER_PROMPTS}
        missing = fixture_names - prompt_names
        self.assertEqual(
            missing,
            set(),
            msg=(
                f"helpers with a Fixture but no TriggerPrompt: {missing}. "
                "Add an entry to retire_after_trigger_prompts.TRIGGER_PROMPTS "
                "or remove the fixture."
            ),
        )

    def test_every_trigger_prompt_has_a_fixture(self) -> None:
        fixture_names = {f.helper_name for f in FIXTURES}
        prompt_names = {tp.helper_name for tp in TRIGGER_PROMPTS}
        extra = prompt_names - fixture_names
        self.assertEqual(
            extra,
            set(),
            msg=(
                f"helpers with a TriggerPrompt but no Fixture: {extra}. "
                "Add an entry to retire_after_fixtures.FIXTURES or remove "
                "the trigger prompt."
            ),
        )

    def test_registries_have_same_count(self) -> None:
        # Same-count is implied by the two set-equality tests above
        # but adds a fast-path guard if the bidirectional sets match
        # while a duplicate slips into one tuple. Catches the case
        # where len(TRIGGER_PROMPTS) != len(FIXTURES) but the
        # helper_name SETS coincide.
        self.assertEqual(len(TRIGGER_PROMPTS), len(FIXTURES))

    def test_ordering_matches_fixtures(self) -> None:
        """``TRIGGER_PROMPTS`` and ``FIXTURES`` are documented as
        parallel-ordered (alphabetical-by-helper-name). Without an
        explicit pairing test, a maintainer reordering one tuple
        without the other would pass set-equality and length checks
        while silently breaking the documented pairing."""
        self.assertEqual(
            [tp.helper_name for tp in TRIGGER_PROMPTS],
            [f.helper_name for f in FIXTURES],
        )


class TestByHelperNameIndex(unittest.TestCase):
    """The lookup-by-helper-name dict mirrors the tuple's contents
    one-to-one. Pinned here so a maintainer adding to the tuple
    without re-deriving the dict (e.g., shipping a hand-written
    dict that drifted) fails CI."""

    def test_index_keys_match_tuple_helper_names(self) -> None:
        self.assertEqual(
            set(BY_HELPER_NAME.keys()),
            {tp.helper_name for tp in TRIGGER_PROMPTS},
        )

    def test_index_values_are_tuple_entries(self) -> None:
        for tp in TRIGGER_PROMPTS:
            with self.subTest(helper=tp.helper_name):
                self.assertIs(BY_HELPER_NAME[tp.helper_name], tp)


class TestPromptBodyDoesNotMentionHelper(unittest.TestCase):
    """The scrubbed-mention property: prompt body must not name the
    helper. A model that sees the helper name in its prompt is biased
    toward importing it; the auto-eval signal (would the model find
    the helper unprompted?) collapses."""

    def test_prompt_body_does_not_contain_helper_name(self) -> None:
        for tp in TRIGGER_PROMPTS:
            with self.subTest(helper=tp.helper_name):
                self.assertNotIn(
                    tp.helper_name,
                    tp.prompt_body,
                    msg=(
                        f"TriggerPrompt for {tp.helper_name!r} mentions the "
                        "helper-name in its body — scrub the mention. The "
                        "auto-eval signal measures whether the model finds "
                        "the helper on its own; a prompt that names the "
                        "helper pre-empts the question."
                    ),
                )


class TestTriggerPromptType(unittest.TestCase):
    """Closed-vocabulary type: TRIGGER_PROMPTS contains only TriggerPrompt
    instances. Catches a maintainer mistake of mixing in a different
    type accidentally (e.g., copy-pasting a Fixture)."""

    def test_all_entries_are_trigger_prompts(self) -> None:
        for entry in TRIGGER_PROMPTS:
            with self.subTest(entry=entry):
                self.assertIsInstance(entry, TriggerPrompt)


if __name__ == "__main__":
    unittest.main()
