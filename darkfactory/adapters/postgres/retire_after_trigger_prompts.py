"""Vanilla trigger prompts for the postgres adapter's retire_after auto-
eval workflow. ROADMAP.md Phase 3 B9b — third commit (foundation in
``e107479``, harness in ``8306fbc``).

Each :class:`TriggerPrompt` is the spec/prompt body that would normally
trigger a Test Engineer to write a scenario exercising the foot-gun the
matching :class:`Fixture` exists to catch — but with the helper-mention
scrubbed. The runner (this commit, ``retire_after_runner.py``) feeds
the prompt body to the model with the helper REMOVED from both the
addendum and the staging tree, then captures N rounds of model output
and hands them to ``evaluate_fixture`` from the harness.

What we are measuring across N rounds:

* Does the model fall into the foot-gun shape unprompted? (foot-gun
  hits in ``EvalResult``)
* Does the model find the helper through some out-of-band path even
  with the helper removed from staging? (helper imports in ``EvalResult``)

A retire verdict requires zero of both across all N rounds — see
``retire_after_eval.py``'s verdict-semantics docstring for the full
decision table.

**Bidirectional invariant** — every :class:`Fixture` has a matching
:class:`TriggerPrompt` keyed by ``helper_name`` and vice versa.
Enforced by ``tests/unit/test_b9b_trigger_prompts.py``. Adding or
removing a universal helper requires updating BOTH registries (plus
``retire-after.toml``); the test suite catches any of the three
slipping out of sync.

**Authorization-model triggers (AUTHORIZATION-MODEL.md):**

Trigger #1 (audience escalation): NO. Prompt bodies are LLM-prompt
material consumed only by the host-side ``claude -p`` subprocess in
the auto-eval runner. They are NOT staged into ``_contracts/`` and NOT
read by any agent container at runtime. No string crosses into a
barrier-restricted reader's view.

Trigger #2 (changed-surfaces declaration): NO. ``barriers.py``
untouched, ``quality-gate.sh`` report rows untouched,
``scenario_lint.py`` untouched, no ``.dark-factory/`` artifact written
by this module (the runner emits ``EvalResult`` JSON to stdout with
optional ``--out PATH`` redirection — see ``retire_after_runner.py``
and ``cli.py``'s ``contracts auto-eval`` subcommand for the
deliberately-non-``.dark-factory/`` persistence shape, decided per
neo's review; alternative was Path A (mask ``_AUDIT`` from TE/SA/QA),
rejected on the architectural-fit ground that auto-eval is a
maintainer-only artifact with no per-project semantics).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TriggerPrompt:
    """One vanilla prompt body per universal helper.

    ``prompt_body`` is the prose the runner wraps with a "Write a
    vitest test for the following acceptance criterion:" preamble
    before sending to the model. The body MUST NOT mention the
    helper-name — the test suite enforces this with a substring check
    (``test_prompt_body_does_not_mention_helper``). A body that names
    the helper biases the model toward importing it and defeats the
    auto-eval signal.

    Frozen so a maintainer cannot mutate prompt content at runtime
    (mirrors :class:`Fixture` from ``retire_after_fixtures.py``).
    """

    helper_name: str
    prompt_body: str


# ``assertUnsetGucIsNullish`` trigger: a "the GUC is unset before the
# app sets it" scenario. Models reaching for ``.toBeNull()`` against
# ``current_setting(name, true)`` exhibit the foot-gun the helper
# catches (postgres.js wire surface returns ``''``, not NULL — the
# 2026-04-16 database-foundation escalation).
_UNSET_GUC_TRIGGER = TriggerPrompt(
    helper_name="assertUnsetGucIsNullish",
    prompt_body=(
        "Before the application has issued any SET LOCAL on the current "
        "session, querying the postgres GUC `app.tenant_id` should return "
        "no value. Write a vitest test that opens a query against "
        "`current_setting('app.tenant_id', true)` (the second argument "
        "`true` makes the call missing-ok rather than raising on an unset "
        "GUC) and asserts that the result represents an unset GUC. The "
        "`client` variable in scope is a postgres.js `Sql` instance "
        "already connected to the test database."
    ),
)


# ``assertTxGucRoundTrips`` trigger: a "round-trip a tenant id via tx-
# local SET LOCAL" scenario. Models reaching for ``tx`SET LOCAL app.x =
# '${val}'`` `` tagged-template literals exhibit the foot-gun (Postgres
# rejects parameterized GUC values at the SET LOCAL position with
# "could not determine data type of parameter $1"). Calibration note:
# the prompt body MUST NOT name the bindable correct primitive
# (``set_config(name, value, true)``) — naming it gives the model the
# answer and guarantees zero foot-gun hits for reasons unrelated to
# whether the helper is needed (Linus 3a review).
_TX_GUC_TRIGGER = TriggerPrompt(
    helper_name="assertTxGucRoundTrips",
    prompt_body=(
        "Write a vitest test that verifies the postgres GUC "
        "`app.tenant_id` round-trips through a transaction. Open a "
        "transaction on the `client` (a postgres.js `Sql` instance), "
        "pin the GUC to the value `'acme'` so it stays set only within "
        "that transaction's scope, then read the GUC back on the same "
        "transaction and assert it reads as `'acme'`. The transaction "
        "must be rolled back at the end so the GUC state does not leak "
        "across tests; this is a plumbing-only verification of GUC "
        "round-trip behavior at the SQL layer, not an assertion about "
        "application code paths."
    ),
)


# ``assertRlsEnabled`` trigger: a "verify RLS is enabled on the users
# table" scenario. Models reaching for application-layer row-count
# assertions (``SELECT * FROM users; expect(rows.length > 0)``) without
# checking the postgres system catalogs exhibit the foot-gun — a stub
# implementation that returns the expected rows trivially passes
# (2026-04-15 substrate-thin baseline escalation). Calibration note:
# the prompt body MUST NOT name ``pg_tables`` / ``pg_policies`` /
# ``rowsecurity`` or enumerate the three RLS failure modes. Naming
# any of those steers the model toward the correct catalog-check
# shape and collapses the helper-import signal that's the only signal
# the harness has for this fixture (``foot_gun_pattern=None``; Linus
# 3a review).
_RLS_TRIGGER = TriggerPrompt(
    helper_name="assertRlsEnabled",
    prompt_body=(
        "Write a vitest test that confirms row-level security is "
        "properly configured on the `public.users` table — RLS must "
        "actually filter access, not just appear to. The `client` "
        "variable in scope is a postgres.js `Sql` instance connected "
        "to the test database."
    ),
)


# Stable ordering by helper-name keeps test reports reviewer-stable
# and matches the ordering of ``retire_after_fixtures.FIXTURES`` so
# the bidirectional pin invariant has a deterministic comparison
# surface. The ordering pairing is pinned by
# ``test_b9b_trigger_prompts.test_ordering_matches_fixtures``.
TRIGGER_PROMPTS: tuple[TriggerPrompt, ...] = (
    _RLS_TRIGGER,
    _TX_GUC_TRIGGER,
    _UNSET_GUC_TRIGGER,
)


# Lookup-by-helper-name index, derived from ``TRIGGER_PROMPTS``. The
# runner (``retire_after_runner.py``, B9b commit 3b) needs this; ship
# it from this module rather than letting every caller derive its own
# inline ``{tp.helper_name: tp for tp in TRIGGER_PROMPTS}`` (Linus
# 3a review). The ``tuple`` is the source of truth; ordering and
# membership flow from there into the dict.
BY_HELPER_NAME: dict[str, TriggerPrompt] = {
    tp.helper_name: tp for tp in TRIGGER_PROMPTS
}
