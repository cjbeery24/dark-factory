"""Pattern-match harness for the postgres adapter's retire_after auto-
eval workflow. ROADMAP.md Phase 3 B9b — second commit (foundation
landed in commit ``e107479``).

Pure host-side function: takes a :class:`Fixture` and N model
outputs (TS test snippets), returns an :class:`EvalResult` with
foot-gun hit count, helper-import count, and a verdict
(``retire`` | ``keep`` | ``inconclusive``).

What this module does NOT do:

* Invoke the LLM. The model-invocation hook ships in a follow-up
  commit (commit 3 of B9b's runner sequence) — host-side
  ``claude -p --output-format=json`` subprocess via the
  ``intelligence/calibrate.py`` pattern.
* Persist artifacts. No write to ``.dark-factory/audit/auto-eval/``;
  that lands with the LLM-invocation commit so output-format and
  barrier-mask declarations are co-located with the producer.
* Open PRs. PR automation lands in commit 4 (GitHub Actions
  workflow consuming the runner's eval-result.json artifact).

**Verdict semantics (from the production-runner workflow):**

The runner constructs a vanilla prompt where the helper is removed
from BOTH the addendum and the staging tree. Across N rounds, it
captures the model's TS test output and applies this harness to
each round's content.

* ``retire`` — every round has zero foot-gun hits AND zero helper
  imports. The model produced safe output without the helper's
  guidance and without finding the helper through other means.
  Helper coverage is now redundant.
* ``keep`` — at least one round exhibits the foot-gun pattern OR
  imports the helper. Helper is still load-bearing.
* ``inconclusive`` — zero rounds (no evidence either way), OR the
  fixture's ``foot_gun_pattern`` is ``None`` (the harness can only
  count helper imports; honest design limit per
  ``retire_after_fixtures.py``'s RLS-fixture documentation).

Helper-import detection requires an import STATEMENT from a
path-namespaced ``_contracts/postgres`` module — bare-name matches
in comments, prose, or imports from unrelated paths do not count.
A tightening over the foundation commit's
``test_b9b_retire_after_pins.py::TestHelperImportDiscipline``
inline check (which only verified bare-name absence/presence in
the canonical fixture content); declared explicitly in the commit
body so the tightening is visible.

**Authorization model (AUTHORIZATION-MODEL.md):**

Trigger #1 (audience escalation): NO. The harness is a pure Python
function. Inputs come from ``retire_after_fixtures.FIXTURES``
(harness-only) and synthetic test inputs. No string crosses into a
barrier-restricted reader's view.

Trigger #2 (changed-surfaces declaration): NO. ``barriers.py``
untouched, ``quality-gate.sh`` report rows untouched,
``scenario_lint.py`` untouched, no new ``.dark-factory/`` artifact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from darkfactory.adapters.postgres.retire_after_fixtures import Fixture


# Closed verdict vocabulary, enforced at the type level. Mypy refuses
# any other string at construction sites; the test
# ``test_verdict_is_in_closed_vocabulary`` is the runtime mirror.
Verdict = Literal["retire", "keep", "inconclusive"]


@dataclass(frozen=True)
class RoundResult:
    """Per-round detail.

    ``foot_gun_hit`` is ``None`` when the fixture's
    ``foot_gun_pattern`` is ``None`` (RLS case). Distinguished from
    ``False`` so a downstream consumer can tell "checked, didn't fire"
    apart from "did not check (no pattern)."

    Frozen so a maintainer can't mutate per-round content after the
    EvalResult is constructed (mirrors :class:`Fixture`).
    """

    foot_gun_hit: bool | None
    imports_helper: bool


@dataclass(frozen=True)
class EvalResult:
    """Harness output for one fixture across N rounds.

    JSON-serializable via ``dataclasses.asdict()`` — the future
    runner will persist this to
    ``.dark-factory/audit/auto-eval/<helper>/<model>/<utc>.json``.
    Persistence formatting (indent, key-order) is the writer's
    concern, not the data class's; callers use
    ``json.dumps(asdict(result), ...)`` directly. Stable
    schema_version pinned so a future field addition is loud, not
    silent.
    """

    schema_version: int
    helper_name: str
    rounds: int
    foot_gun_hits: int
    helper_import_count: int
    verdict: Verdict
    per_round: tuple[RoundResult, ...] = field(default_factory=tuple)


# Schema version for ``EvalResult``. Bump when adding/removing/
# renaming fields. The future audit-artifact reader pins this and
# refuses to parse a mismatched version (prevents silent drift on
# the artifact-consumer side).
EVAL_RESULT_SCHEMA_VERSION: int = 1


# Match a TS named-import statement from a path-namespaced
# ``_contracts/postgres`` (or any subpath thereof — e.g.,
# ``_contracts/postgres/project``) source. The named-imports body
# is captured; we then split on commas and check each chunk's
# leading identifier (after stripping comments, ``type`` prefix,
# and ``as <alias>`` suffix).
#
# Rationale for the tightening over the foundation's bare-name
# check (``helper_name in code``):
#
# * Bare-name match false-positives on TS comments mentioning the
#   helper (e.g., ``// assertUnsetGucIsNullish foot-gun``) AND on
#   substring collisions (``assertUnsetGucIsNullishOrEmpty`` would
#   match ``assertUnsetGucIsNullish``).
# * Bare-name match misses imports from non-staged paths (treats
#   them the same as staged paths).
# * Bare-name match cannot distinguish ``import { type X }`` (compiles
#   to no-op, doesn't represent runtime use of the helper) from
#   ``import { X }`` (real use).
#
# Behaviors pinned by the test suite:
#
# * ``import type { X } from '...'`` → does NOT match. Type-only
#   imports compile to a no-op; the model isn't using the helper at
#   runtime.
# * ``import { type X, Y } from '...'`` → does NOT match for X
#   (type-only inline form), DOES match for Y.
# * ``import { X as alias } from '...'`` → matches X (the imported
#   name is what we measure, not the local binding).
# * ``import { X /* comment */ } from '...'`` and
#   ``import { X, // comment\n  Y } from '...'`` → comments stripped
#   before matching; comment text mentioning the helper does not
#   false-positive.
# * ``export { X } from '...'`` → does NOT match. Re-exports are
#   forwarding declarations, not uses.
# * ``import * as helpers from '...'`` and ``import X from '...'``
#   (default) → do NOT match (no named-imports body).
# * ``import { Xfoo } from '...'`` for ``helper_name="X"`` → does NOT
#   match (whole-name match, not substring).
# * Subpaths under ``_contracts/postgres/`` (e.g., the project
#   subdir) → match. Computed paths (``from `${...}`...``) → do NOT.
_IMPORT_STATEMENT_RE = re.compile(
    r"""(?xs)
    \bimport\s*               # `import` keyword (no `type` between — see body for inline-type handling)
    \{                        # named-imports opening brace
        (?P<body>[^}]*?)      # imported-names body (non-greedy, no closing brace)
    \}                        # closing brace
    \s*from\s*                # `from` keyword
    ['"]                      # quoted path-spec opening
    (?P<source>               # path-spec body
        \.\.?\/[^'"]*?_contracts\/postgres(?:\/[^'"]*)?
    )
    ['"]                      # closing quote
    """,
)


def _imports_helper(code: str, helper_name: str) -> bool:
    """True iff ``code`` contains a TS named-import statement that
    imports ``helper_name`` from a path-namespaced
    ``_contracts/postgres`` (or any subpath) source as a runtime-use
    binding.

    See ``_IMPORT_STATEMENT_RE`` above for the full enumeration of
    matched / not-matched TS forms; tests pin every case.
    """
    helper_re = re.compile(rf"\A{re.escape(helper_name)}\Z")
    for match in _IMPORT_STATEMENT_RE.finditer(code):
        body = match.group("body")
        # Strip TS line and block comments from the body; without
        # this, a comment inside the named-imports list mentioning
        # the helper false-positives.
        body = re.sub(r"//[^\n]*", "", body)
        body = re.sub(r"/\*[\s\S]*?\*/", "", body)
        # Split on commas and inspect each named-import chunk
        # individually. Whole-chunk match (after trimming `type`
        # prefix and `as <alias>` suffix) avoids the bare-name's
        # substring-collision and inline-`type` false-positives.
        for raw_chunk in body.split(","):
            chunk = raw_chunk.strip()
            if not chunk:
                continue
            # `import { type X, Y }` form — drop type-only entries.
            if chunk.startswith("type "):
                continue
            # `import { X as alias }` form — alias is the local
            # binding; the import is of X.
            chunk = re.split(r"\s+as\s+", chunk, maxsplit=1)[0].strip()
            if helper_re.match(chunk):
                return True
    return False


def evaluate_fixture(
    fixture: Fixture,
    model_outputs: list[str],
) -> EvalResult:
    """Run the comparison logic across N model outputs.

    ``model_outputs`` is a list of TS test snippets (strings) —
    typically captured from N invocations of the model via the
    runner's vanilla prompt, but the harness accepts any string list
    so synthetic test inputs work without an LLM.

    Verdict logic (closed):

    * ``rounds == 0`` → ``inconclusive`` (zero rounds = zero
      evidence).
    * ``fixture.foot_gun_pattern is None`` → ``inconclusive``
      (RLS-style absence-of-check fixtures need adapter-specific
      structural analysis the generic harness can't provide; honest
      design limit).
    * ``foot_gun_hits == 0 AND helper_import_count == 0`` →
      ``retire``.
    * Otherwise → ``keep``.

    Note on the second branch (``helper_import_count > 0`` even
    when no foot-gun): the production runner constructs a vanilla
    prompt where the helper is REMOVED from both the addendum and
    the staging tree. Helper imports SHOULD be 0 in vanilla output;
    a non-zero count means the runner setup is buggy (helper still
    findable) — the verdict ``keep`` is conservative ("don't retire
    if the model is using the helper, regardless of how it found
    it").
    """
    rounds = len(model_outputs)
    if rounds == 0:
        return EvalResult(
            schema_version=EVAL_RESULT_SCHEMA_VERSION,
            helper_name=fixture.helper_name,
            rounds=0,
            foot_gun_hits=0,
            helper_import_count=0,
            verdict="inconclusive",
            per_round=(),
        )

    per_round: list[RoundResult] = []
    foot_gun_hits = 0
    helper_imports = 0
    for output in model_outputs:
        if fixture.foot_gun_pattern is not None:
            hit = bool(fixture.foot_gun_pattern.search(output))
        else:
            hit = None
        if hit:
            foot_gun_hits += 1
        imports = _imports_helper(output, fixture.helper_name)
        if imports:
            helper_imports += 1
        per_round.append(RoundResult(
            foot_gun_hit=hit, imports_helper=imports,
        ))

    if fixture.foot_gun_pattern is None:
        verdict: Verdict = "inconclusive"
    elif foot_gun_hits == 0 and helper_imports == 0:
        verdict = "retire"
    else:
        verdict = "keep"

    return EvalResult(
        schema_version=EVAL_RESULT_SCHEMA_VERSION,
        helper_name=fixture.helper_name,
        rounds=rounds,
        foot_gun_hits=foot_gun_hits,
        helper_import_count=helper_imports,
        verdict=verdict,
        per_round=tuple(per_round),
    )
