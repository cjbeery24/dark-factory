"""ROADMAP.md Phase 3 B9e: layering-audit pin tests.

Companion to ``docs/audits/2026-04-25-b9e-layering-audit.md``. Pins
the B9e classification corrections (per Linus review) at four
surfaces:

1. ``mechanism-tokens.toml`` ``app_guc_identifier`` carries an inline
   ``# B9e:`` annotation declaring the ``app.*`` namespace
   hardcoding as an *accepted false-negative* (UNIVERSAL with
   accepted false-negative — not a deferred CONVENTION migration).
2. ``substrate-rules.toml`` ``custom_guc`` rule carries the same
   inline annotation.
3. ``postgres/scenario_architect_addendum.md`` annotates the three
   project-side convention helpers with ``*(project)*`` markers in
   the rendered prompt text.
4. ``postgres/architect_addendum.md`` substrate-manifest worked
   example uses placeholder names rather than the budget-brain-v2
   project-convention names ``app.tenant_id`` / ``users`` /
   ``users_tenant_isolation`` / ``2026-03-28-database-foundation``.

These tests live separate from the contract harness in
``tests/adapters/test_adapter_contract.py`` because they pin
B9e-specific marker text, not ABC-level contracts. The contract
harness already covers the governance line-cap regression backstop
(``test_architect_addendum_within_governance_line_cap``,
``test_scenario_architect_addendum_within_governance_line_cap``);
B9e adds no new cap test.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from darkfactory.adapters.postgres.adapter import PostgresAdapter


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_POSTGRES_DIR = (
    _REPO_ROOT / "darkfactory" / "adapters" / "postgres"
)
_MECHANISM_TOKENS = _POSTGRES_DIR / "mechanism-tokens.toml"
_SUBSTRATE_RULES = _POSTGRES_DIR / "substrate-rules.toml"


class TestMechanismTokensB9eAnnotation(unittest.TestCase):
    """Pin the inline B9e annotation on ``app_guc_identifier``.

    The annotation names the ``app.*`` namespace hardcoding as an
    *accepted false-negative* — i.e., a documented UNIVERSAL design
    limit, not a CONVENTION migration that's been deferred. A future
    maintainer who "tidies" the comment away would silently lose the
    layering classification rationale; this test catches that loss.
    """

    def test_annotation_marks_app_guc_as_accepted_false_negative(self) -> None:
        text = _MECHANISM_TOKENS.read_text(encoding="utf-8")
        # The B9e annotation must precede the [[token]] block whose
        # name is `app_guc_identifier`. Locate the block and verify
        # the annotation appears within the comment span immediately
        # above it.
        block_idx = text.find('name = "app_guc_identifier"')
        self.assertNotEqual(
            block_idx, -1,
            "app_guc_identifier [[token]] block missing — restore from "
            "git history if accidentally removed.",
        )
        # Walk backward to the previous [[token]] header (or file
        # start) and verify the B9e annotation lives in that span.
        # The current file has the annotation on the [[token]] block
        # that owns this name, so we slice from the most recent
        # [[token]] line backward to the previous one.
        tail = text[:block_idx]
        block_open = tail.rfind("[[token]]")
        self.assertNotEqual(
            block_open, -1,
            "could not locate the [[token]] header preceding "
            "app_guc_identifier",
        )
        # Look one block-span backward (the B9e annotation lives in
        # the inline-comment block of the [[token]] header that owns
        # `app_guc_identifier`).
        block_text = text[block_open:block_idx]
        self.assertIn(
            "B9e", block_text,
            "B9e: annotation missing on app_guc_identifier [[token]] "
            "block — see docs/audits/2026-04-25-b9e-layering-audit.md "
            "§1 for why this annotation must remain.",
        )
        self.assertIn(
            "accepted false-negative", block_text,
            "B9e annotation must name the `app.*` namespace as an "
            "*accepted false-negative* (not a deferred CONVENTION "
            "migration). The phrasing distinguishes UNIVERSAL with "
            "documented design-limit from deferred-migration debt.",
        )


class TestSubstrateRulesB9eAnnotation(unittest.TestCase):
    """Pin the inline B9e annotation on the ``custom_guc`` rule."""

    def test_annotation_marks_custom_guc_as_accepted_false_negative(self) -> None:
        text = _SUBSTRATE_RULES.read_text(encoding="utf-8")
        block_idx = text.find('trigger = "custom_guc"')
        self.assertNotEqual(
            block_idx, -1,
            "custom_guc [[rule]] missing — restore from git history "
            "if accidentally removed.",
        )
        tail = text[:block_idx]
        block_open = tail.rfind("[[rule]]")
        self.assertNotEqual(
            block_open, -1,
            "could not locate the [[rule]] header preceding custom_guc",
        )
        block_text = text[block_open:block_idx]
        self.assertIn(
            "B9e", block_text,
            "B9e: annotation missing on custom_guc [[rule]] — see "
            "docs/audits/2026-04-25-b9e-layering-audit.md §2 for why "
            "this annotation must remain.",
        )
        self.assertIn(
            "accepted false-negative", block_text,
            "B9e annotation must name the `app.*` match-pattern bias "
            "as an *accepted false-negative* (not a deferred "
            "CONVENTION migration).",
        )


class TestScenarioAddendumProjectMarkers(unittest.TestCase):
    """Pin the ``*(project)*`` markers on the three convention helpers
    in the rendered scenario-architect addendum text.

    Three convention helpers (``assertAppSetsTxGuc``,
    ``assertTenantSeesOwnRows``, ``assertTenantCannotSeeOtherRows``)
    are project-staged per ROADMAP.md Phase 3 B9a. The
    scenario-architect addendum's helper-list bullets must flag each
    one with a ``*(project)*`` marker so the LLM (Test Engineer)
    sees the universal/convention split. A regression that drops the
    markers re-blends the surfaces in the prompt and weakens the
    layering signal.

    Tests the *rendered* addendum string returned by the adapter
    method, not the raw .md file — because the line cap and import
    behavior happen at composition time.
    """

    def test_three_convention_helpers_carry_project_marker(self) -> None:
        adapter = PostgresAdapter()
        text = adapter.scenario_architect_addendum()
        for helper in (
            "assertAppSetsTxGuc",
            "assertTenantSeesOwnRows",
            "assertTenantCannotSeeOtherRows",
        ):
            # Every project-staged helper must appear at least once
            # in the rendered prompt with a *(project)* marker
            # somewhere in the same logical bullet. The simplest
            # invariant: each helper name appears, and the substring
            # `*(project)*` appears in the addendum at least three
            # times (once per convention helper). We assert both.
            self.assertIn(
                helper, text,
                f"convention helper {helper!r} missing from scenario "
                "addendum — the helper-list bullets must reference "
                "every helper the Test Engineer is allowed to import.",
            )
        marker_count = text.count("*(project)*")
        # Three markers (one per convention helper) plus one in the
        # explanation note immediately below the bullets = 4. Pin a
        # lower bound so a reformat that adds emphasis stays valid;
        # pin an upper bound so a sloppy edit that scatters markers
        # everywhere is also caught.
        self.assertGreaterEqual(
            marker_count, 4,
            f"expected ≥4 `*(project)*` markers in scenario "
            f"addendum (one per convention helper + the explanation "
            f"line); got {marker_count}.",
        )
        self.assertLessEqual(
            marker_count, 6,
            f"unexpectedly many `*(project)*` markers ({marker_count}) "
            "— the marker is for the three project-staged helpers "
            "only (3 helpers + 1 explanation = 4 expected; ceiling 6 "
            "leaves room for one emphasis reformat). If a fourth "
            "helper appears, update this bound and the audit doc.",
        )

    def test_universal_helpers_do_not_carry_project_marker(self) -> None:
        """Defense-in-depth against a regression that mis-tags a
        framework helper as project-staged. Pins the directionality
        of the marker convention.
        """
        adapter = PostgresAdapter()
        text = adapter.scenario_architect_addendum()
        # For each universal helper, find every occurrence and
        # confirm no `*(project)*` marker appears within ~80 chars
        # immediately after the helper name on the same line. A
        # `*(project)*` marker that close to a universal helper name
        # is a mis-tag.
        for helper in (
            "assertUnsetGucIsNullish",
            "assertTxGucRoundTrips",
            "assertRlsEnabled",
        ):
            idx = 0
            while True:
                idx = text.find(helper, idx)
                if idx == -1:
                    break
                # Bound the search to the same line (newline
                # terminates the bullet's first segment).
                line_end = text.find("\n", idx)
                if line_end == -1:
                    line_end = len(text)
                same_line_tail = text[idx + len(helper):line_end]
                self.assertNotIn(
                    "*(project)*", same_line_tail,
                    f"universal helper {helper!r} appears with a "
                    f"`*(project)*` marker on the same line — that "
                    "is a layering mis-tag. The marker is reserved "
                    "for project-staged convention helpers.",
                )
                idx += len(helper)


class TestArchitectAddendumExamplePlaceholders(unittest.TestCase):
    """Pin that the substrate-manifest worked example in the
    architect addendum uses placeholder names, not the budget-brain-v2
    project-convention names.

    Linus review (B9e): the original example hardcoded
    ``app.tenant_id``, ``users``, ``users_tenant_isolation``, and
    the spec id ``2026-03-28-database-foundation`` — all
    budget-brain-v2-specific. The Architect agent (LLM) reads this
    prompt fragment when emitting a manifest; concrete-name examples
    bias output toward the example's names regardless of the spec's
    actual primitives. The B9e fix replaces the values with explicit
    angle-bracket placeholders so the LLM sees "fill in from the
    spec" instead of pattern-matching the example's names.

    This test pins absence of the project names AND presence of the
    placeholder convention. A future maintainer reverting the
    placeholders to "more readable" concrete names re-introduces the
    layering violation; this test catches that revert.
    """

    _BUDGET_BRAIN_PROJECT_NAMES: tuple[str, ...] = (
        "app.tenant_id",
        '"users"',  # quoted to avoid matching the universal word "users"
        "users_tenant_isolation",
        "2026-03-28-database-foundation",
    )

    _PLACEHOLDER_TOKENS: tuple[str, ...] = (
        "<spec-filename-stem>",
        "<your_guc_name>",
        "<your_table>",
        "<your_policy_name>",
    )

    def test_example_does_not_hardcode_project_names(self) -> None:
        adapter = PostgresAdapter()
        text = adapter.architect_addendum()
        for name in self._BUDGET_BRAIN_PROJECT_NAMES:
            self.assertNotIn(
                name, text,
                f"architect-addendum substrate-manifest example "
                f"contains the budget-brain-v2 project-convention "
                f"name {name!r}. Replace with placeholder per B9e — "
                f"see docs/audits/2026-04-25-b9e-layering-audit.md §3a.",
            )

    def test_example_uses_placeholder_tokens(self) -> None:
        adapter = PostgresAdapter()
        text = adapter.architect_addendum()
        for placeholder in self._PLACEHOLDER_TOKENS:
            self.assertIn(
                placeholder, text,
                f"architect-addendum substrate-manifest example "
                f"missing the placeholder token {placeholder!r} — "
                "B9e requires the worked example use angle-bracket "
                "placeholders, not project-convention names.",
            )

    def test_example_preamble_directs_substitution_from_spec(self) -> None:
        """The preamble line above the example tells the LLM the
        shape of the substitution (`substitute the spec's actual
        primitive names for <placeholders>`). Without it, the
        placeholders alone could be read as "make up names
        appropriate to the project" rather than "copy from spec
        primitives." Pin the preamble's directive verb so a future
        "tidy" pass that drops the line fails the test.
        """
        adapter = PostgresAdapter()
        text = adapter.architect_addendum()
        self.assertIn(
            "substitute", text,
            "architect-addendum substrate-manifest preamble missing "
            "the `substitute the spec's actual primitive names ...` "
            "directive that tells the LLM placeholders come from the "
            "spec's `## Substrate Primitives` block — see "
            "docs/audits/2026-04-25-b9e-layering-audit.md §3a.",
        )


if __name__ == "__main__":
    unittest.main()
