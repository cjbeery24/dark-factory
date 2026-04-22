"""Tests for the ``ClassificationHint`` dataclass and the
``apply_classification_hints`` pipeline helper.

The hint dataclass is a validated, frozen record; the pipeline helper
mutates ``defects.json`` in place when a scenario file matches any
adapter-contributed pattern. These tests cover:

* Hint construction validation (regex compiles, category in vocab,
  internal reason code shape).
* Barrier discipline: the vocab in ``adapters.base`` tracks
  ``pipeline.iteration._VALID_CLASSIFICATIONS`` exactly — a drift
  between the two is a blocker.
* Apply-hints happy path, no-ops, and edge cases (missing file, empty
  defects, classifier error, containment violations).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from darkfactory.adapters.base import (
    HINT_CATEGORY_VOCAB,
    ClassificationHint,
    EcosystemAdapter,
)
from darkfactory.pipeline.classification_hints import apply_classification_hints


# ---------------------------------------------------------------------------
# ClassificationHint construction
# ---------------------------------------------------------------------------


def test_classification_hint_accepts_valid_construction():
    h = ClassificationHint(
        pattern=r"postgres\(",
        category_override="SPEC_AMBIGUOUS",
        internal_reason_code="POSTGRES_EXAMPLE",
    )
    assert h.pattern == r"postgres\("
    assert h.category_override == "SPEC_AMBIGUOUS"
    assert h.internal_reason_code == "POSTGRES_EXAMPLE"


def test_classification_hint_rejects_empty_pattern():
    with pytest.raises(ValueError, match="non-empty"):
        ClassificationHint(
            pattern="",
            category_override="IMPL_BUG",
            internal_reason_code="FOO_BAR",
        )


def test_classification_hint_rejects_invalid_regex():
    with pytest.raises(ValueError, match="valid regex"):
        ClassificationHint(
            pattern="(unbalanced",
            category_override="IMPL_BUG",
            internal_reason_code="FOO_BAR",
        )


def test_classification_hint_rejects_unknown_category():
    with pytest.raises(ValueError, match="category_override"):
        ClassificationHint(
            pattern="x",
            category_override="PERFORMANCE",  # not in vocab
            internal_reason_code="FOO_BAR",
        )


@pytest.mark.parametrize(
    "bad_code",
    [
        "",
        "lowercase",
        "AB",  # too short
        "A" * 65,  # too long
        "1_LEADING_DIGIT",
        "TRAILING_SPACE ",
        "HAS-DASH",
        "HAS DOT.",
    ],
)
def test_classification_hint_rejects_bad_reason_code(bad_code: str):
    with pytest.raises(ValueError, match="internal_reason_code"):
        ClassificationHint(
            pattern="x",
            category_override="IMPL_BUG",
            internal_reason_code=bad_code,
        )


def test_classification_hint_is_frozen():
    h = ClassificationHint(
        pattern="x",
        category_override="IMPL_BUG",
        internal_reason_code="FOO_BAR",
    )
    with pytest.raises((AttributeError, Exception)):
        h.pattern = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Barrier-discipline contract: hint vocab mirrors classifier vocab.
# ---------------------------------------------------------------------------


def test_hint_category_vocab_matches_classifier_vocab():
    """If the classifier vocabulary changes in ``pipeline.iteration`` and
    the ``adapters.base`` mirror doesn't, adapter-contributed hints
    could reclassify defects into a bucket the Oracle doesn't know
    about. This tripwire catches that drift at unit-test time.
    """
    from darkfactory.pipeline.iteration import _VALID_CLASSIFICATIONS

    assert HINT_CATEGORY_VOCAB == _VALID_CLASSIFICATIONS, (
        "HINT_CATEGORY_VOCAB drift from _VALID_CLASSIFICATIONS. "
        "Update darkfactory/adapters/base.py to match."
    )


# ---------------------------------------------------------------------------
# apply_classification_hints — fixture helpers
# ---------------------------------------------------------------------------


@dataclass
class _MockHintAdapter:
    """Minimal object with ``classification_hints()`` — the only method
    the helper calls. Intentionally not a full ``EcosystemAdapter``;
    duck-typing is sufficient for this unit under test.
    """
    hints: list[ClassificationHint]

    def classification_hints(self) -> list[ClassificationHint]:
        return list(self.hints)


def _write_defects(path: Path, defects: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            "iteration": 1,
            "schema_version": 1,
            "error": "",
            "defects": defects,
        }, indent=2),
        encoding="utf-8",
    )


def _write_scenario(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


# Test-side hint shapes match the post-Linus production regexes in
# ``PostgresAdapter``: peer-client narrows to the ``app.`` GUC
# namespace, owner-BYPASSRLS uses a ``\A``-anchored whole-scope
# negative lookahead for FORCE.
_HINT_PEER = ClassificationHint(
    pattern=(
        r"postgres\(process\.env\.[A-Z_]+"
        r"[\s\S]*?current_setting\(\s*['\"`]app\."
    ),
    category_override="SPEC_AMBIGUOUS",
    internal_reason_code="POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION",
)
_HINT_OWNER = ClassificationHint(
    pattern=(
        r"\A(?![\s\S]*FORCE\s+ROW\s+LEVEL)"
        r"[\s\S]*ENABLE\s+ROW\s+LEVEL\s+SECURITY"
    ),
    category_override="SPEC_AMBIGUOUS",
    internal_reason_code="POSTGRES_OWNER_BYPASSES_RLS",
)


# ---------------------------------------------------------------------------
# apply_classification_hints — happy paths
# ---------------------------------------------------------------------------


def test_apply_hints_reclassifies_matching_defect(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "tenant-context.test.ts",
        """import postgres from 'postgres';
describe('Tenant Context Middleware [FR-2]', () => {
  const client = postgres(process.env.DATABASE_URL);
  it('[AC-5] GIVEN tenant WHEN withDb THEN guc set', async () => {
    const r = await client`SELECT current_setting('app.tenant_id', true)`;
    expect(r).toBe('uuid-123');
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": (
            "Tenant Context Middleware [FR-2] > [AC-5] GIVEN tenant "
            "WHEN withDb THEN guc set"
        ),
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-5", "FR-2"],
        "scope_filter_eligible": False,
        "rationale": "Implementation fails to set app.tenant_id GUC.",
    }])

    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    updated = apply_classification_hints(defects_path, scenarios_dir, [adapter])
    assert updated == 1

    doc = json.loads(defects_path.read_text(encoding="utf-8"))
    assert doc["defects"][0]["category"] == "SPEC_AMBIGUOUS"
    assert doc["defects"][0]["internal_reason_code"] == (
        "POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"
    )
    # Other fields preserved.
    assert doc["defects"][0]["cited_anchor_ids"] == ["AC-5", "FR-2"]


def test_apply_hints_non_matching_scenario_is_no_op(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "plain.test.ts",
        """describe('Unrelated [FR-1]', () => {
  it('[AC-1] does a thing', () => { expect(1).toBe(1); });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "Unrelated [FR-1] > [AC-1] does a thing",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "something",
    }])

    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    updated = apply_classification_hints(defects_path, scenarios_dir, [adapter])
    assert updated == 0

    doc = json.loads(defects_path.read_text(encoding="utf-8"))
    assert doc["defects"][0]["category"] == "IMPL_BUG"
    assert "internal_reason_code" not in doc["defects"][0]


def test_apply_hints_multiple_files_first_match_wins(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "rls.test.ts",
        """describe('RLS Isolation [AC-7]', () => {
  await client`CREATE TABLE x (id int)`;
  await client`ALTER TABLE x ENABLE ROW LEVEL SECURITY`;
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "RLS Isolation [AC-7] > assertion",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-7"],
        "rationale": "",
    }])

    adapter = _MockHintAdapter(hints=[_HINT_OWNER])
    updated = apply_classification_hints(defects_path, scenarios_dir, [adapter])
    assert updated == 1

    doc = json.loads(defects_path.read_text(encoding="utf-8"))
    assert doc["defects"][0]["internal_reason_code"] == "POSTGRES_OWNER_BYPASSES_RLS"


def test_force_row_level_suppresses_owner_hint(tmp_path: Path):
    """The owner-BYPASSRLS pattern has a negative-lookahead for
    ``FORCE ROW LEVEL`` — files that correctly add FORCE must not
    trigger a false-positive reclassification."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "rls-forced.test.ts",
        """describe('RLS Forced [AC-7]', () => {
  await client`ALTER TABLE x ENABLE ROW LEVEL SECURITY`;
  await client`ALTER TABLE x FORCE ROW LEVEL SECURITY`;
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "RLS Forced [AC-7] > assertion",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-7"],
        "rationale": "",
    }])

    adapter = _MockHintAdapter(hints=[_HINT_OWNER])
    updated = apply_classification_hints(defects_path, scenarios_dir, [adapter])
    assert updated == 0


# ---------------------------------------------------------------------------
# apply_classification_hints — no-op edge cases
# ---------------------------------------------------------------------------


def test_missing_defects_file_returns_zero(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    assert apply_classification_hints(
        tmp_path / "missing.json", scenarios_dir, [
            _MockHintAdapter(hints=[_HINT_PEER]),
        ],
    ) == 0


def test_classifier_error_short_circuits(tmp_path: Path):
    """A defects.json with non-empty ``error`` must not be mutated —
    the Oracle will NO-SHIP on the error anyway, and rewriting would
    mask the classifier pipeline failure mode."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "t.test.ts",
        "describe('T [AC-1]', () => {}); postgres(process.env.X) current_setting(",
    )
    defects_path = tmp_path / "defects.json"
    defects_path.write_text(
        json.dumps({
            "iteration": 1,
            "schema_version": 1,
            "error": "parse_failed",
            "defects": [],
        }),
        encoding="utf-8",
    )
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    updated = apply_classification_hints(defects_path, scenarios_dir, [adapter])
    assert updated == 0


def test_empty_defects_array_returns_zero(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_no_adapter_hints_returns_zero(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "t.test.ts",
        "describe('T [AC-1]', () => {});",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "T [AC-1] > x",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_missing_scenarios_dir_returns_zero(tmp_path: Path):
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "T [AC-1] > x",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, tmp_path / "does-not-exist", [adapter],
    ) == 0


def test_unparseable_defects_json_returns_zero(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    defects_path = tmp_path / "defects.json"
    defects_path.write_text("{not: json", encoding="utf-8")
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


# ---------------------------------------------------------------------------
# Barrier / containment
# ---------------------------------------------------------------------------


def test_contracts_dir_excluded_from_scan(tmp_path: Path):
    """Scenario-staged adapter helpers under ``_contracts/`` carry
    library-wire assertions that often include patterns matching our
    hints (e.g. a ``current_setting`` example) — scanning them would
    trigger hint false-positives on every defect in the spec.
    """
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "_contracts" / "postgres" / "tx.ts",
        "postgres(process.env.DATABASE_URL) current_setting(",
    )
    _write_scenario(
        scenarios_dir / "t.test.ts",
        "describe('T [AC-1]', () => { it('[AC-1] x', () => {}); });",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "T [AC-1] > x",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_symlink_out_of_tree_is_skipped(tmp_path: Path):
    """A symlink from ``scenarios/`` to an out-of-tree file must not be
    read — matches the containment check used in ``type_only_exports``.
    """
    scenarios_dir = tmp_path / "scenarios"
    scenarios_dir.mkdir()
    outside = tmp_path / "outside.test.ts"
    outside.write_text(
        "postgres(process.env.DATABASE_URL) current_setting(",
        encoding="utf-8",
    )
    try:
        (scenarios_dir / "link.test.ts").symlink_to(outside)
    except OSError:
        pytest.skip("symlink creation not available")
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "X > Y",
        "category": "IMPL_BUG",
        "cited_anchor_ids": [],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    # Symlinked scenario is excluded by the containment check; no match
    # on it, but the test has no matching describe prefix anyway. The
    # key assertion: no exception, no mutation based on out-of-tree read.
    updated = apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    )
    assert updated == 0


def test_first_adapter_hint_wins(tmp_path: Path):
    """Adapter iteration order then per-adapter list order are the tie
    breakers. Pin it so adapter authors can rely on priority."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "both.test.ts",
        """describe('Both [AC-9]', () => {
  await client`ALTER TABLE x ENABLE ROW LEVEL SECURITY`;
  const c = postgres(process.env.DATABASE_URL);
  await c`SELECT current_setting('app.x', true)`;
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "Both [AC-9] > x",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-9"],
        "rationale": "",
    }])
    # PEER hint listed first; both match, but PEER wins.
    adapter = _MockHintAdapter(hints=[_HINT_PEER, _HINT_OWNER])
    updated = apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    )
    assert updated == 1
    doc = json.loads(defects_path.read_text(encoding="utf-8"))
    assert doc["defects"][0]["internal_reason_code"] == (
        "POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"
    )


def test_defect_without_test_name_is_skipped(tmp_path: Path):
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "t.test.ts",
        """describe('Foo [AC-1]', () => {
  const c = postgres(process.env.X);
  it('x', () => { c`SELECT current_setting('app.tenant_id')`; });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [
        {
            "test_name": "",  # malformed
            "category": "IMPL_BUG",
            "cited_anchor_ids": [],
            "rationale": "",
        },
        {
            "test_name": "Foo [AC-1] > x",
            "category": "IMPL_BUG",
            "cited_anchor_ids": ["AC-1"],
            "rationale": "",
        },
    ])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    updated = apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    )
    assert updated == 1


# ---------------------------------------------------------------------------
# Post-Linus hardening — body-scoped linkage, idempotency, narrowed vocab
# ---------------------------------------------------------------------------


def test_body_scoped_linkage_does_not_fan_out_across_describes(tmp_path: Path):
    """Linus #1: a file with multiple describe blocks where only ONE
    contains the footgun must reclassify only that block's defects. The
    first-pass implementation unioned every describe name in a matching
    file into the hint's prefix set — so a 20-describe file with one
    footgun block would fan the reclassification out to all 20. Body-
    scoped matching (post-Linus) pairs each describe body with its own
    regex scan; a non-matching sibling describe stays untouched."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "mixed.test.ts",
        """
describe('Clean CRUD [FR-1]', () => {
  it('[AC-1] selects a row', async () => {
    const r = await db.users.findOne();
    expect(r.id).toBeDefined();
  });
});

describe('Footgun [FR-2]', () => {
  const c = postgres(process.env.DATABASE_URL);
  it('[AC-5] reads tx guc', async () => {
    const r = await c`SELECT current_setting('app.tenant_id')`;
    expect(r).toBe('x');
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [
        {
            "test_name": "Clean CRUD [FR-1] > [AC-1] selects a row",
            "category": "IMPL_BUG",
            "cited_anchor_ids": ["AC-1"],
            "rationale": "",
        },
        {
            "test_name": "Footgun [FR-2] > [AC-5] reads tx guc",
            "category": "IMPL_BUG",
            "cited_anchor_ids": ["AC-5"],
            "rationale": "",
        },
    ])

    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    updated = apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    )
    assert updated == 1  # only the footgun describe — not the clean CRUD one

    doc = json.loads(defects_path.read_text(encoding="utf-8"))
    # Clean CRUD defect stays as IMPL_BUG — not reclassified.
    assert doc["defects"][0]["category"] == "IMPL_BUG"
    assert "internal_reason_code" not in doc["defects"][0]
    # Footgun defect is reclassified.
    assert doc["defects"][1]["category"] == "SPEC_AMBIGUOUS"
    assert doc["defects"][1]["internal_reason_code"] == (
        "POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"
    )


def test_nested_describes_use_exclusive_body_no_parent_fanout(tmp_path: Path):
    """Linus pass 2 #1: an outer describe's *exclusive* body (its text
    minus nested sub-describes) is what the hint regex runs against. A
    footgun inside a nested describe must NOT register the outer's
    chain path — otherwise a clean sibling ``describe`` under the same
    outer would get reclassified.

    File layout:
        describe('Outer [FR-3]', () => {
          describe('Clean Inner', () => { it(); })   // no footgun
          describe('Dirty Inner', () => { /* footgun */ it(); })
        });

    Expected: only the Dirty Inner's defect is reclassified. The Clean
    Inner's defect stays IMPL_BUG. This is the bug Linus found in
    pass 2: the first-pass fix unioned both inner-describe chains.
    """
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "nested.test.ts",
        """
describe('Outer [FR-3]', () => {
  describe('Clean Inner', () => {
    it('reads a row', async () => {
      const r = await db.users.findOne();
    });
  });
  describe('Dirty Inner', () => {
    const c = postgres(process.env.DATABASE_URL);
    it('reads app guc', async () => {
      await c`SELECT current_setting('app.x')`;
    });
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [
        {
            "test_name": "Outer [FR-3] > Clean Inner > reads a row",
            "category": "IMPL_BUG",
            "cited_anchor_ids": ["FR-3"],
            "rationale": "",
        },
        {
            "test_name": "Outer [FR-3] > Dirty Inner > reads app guc",
            "category": "IMPL_BUG",
            "cited_anchor_ids": ["FR-3"],
            "rationale": "",
        },
    ])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    updated = apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    )
    assert updated == 1, "only the Dirty Inner's defect should be reclassified"

    doc = json.loads(defects_path.read_text(encoding="utf-8"))
    assert doc["defects"][0]["category"] == "IMPL_BUG"
    assert "internal_reason_code" not in doc["defects"][0]
    assert doc["defects"][1]["category"] == "SPEC_AMBIGUOUS"
    assert doc["defects"][1]["internal_reason_code"] == (
        "POSTGRES_PEER_CLIENT_TX_GUC_ASSERTION"
    )


def test_deeply_nested_describe_path_separator_prevents_sibling_collision(
    tmp_path: Path,
):
    """Chain paths are joined with ``" > "`` and matched with a
    ``startswith(path + " > ")`` guard so an outer's prefix can't
    accidentally match a descendant that doesn't share the chain.
    E.g. ``Outer > Inner`` must not match a defect under
    ``Outer2 > Inner`` even though both end in ``Inner``."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "collide.test.ts",
        """
describe('Outer', () => {
  describe('Inner', () => {
    const c = postgres(process.env.URL);
    it('x', async () => { await c`SELECT current_setting('app.t')`; });
  });
});
describe('Outer2', () => {
  describe('Inner', () => {
    it('x', async () => { /* clean */ });
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [
        {
            "test_name": "Outer2 > Inner > x",
            "category": "IMPL_BUG",
            "cited_anchor_ids": [],
            "rationale": "",
        },
    ])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    # Outer2 > Inner is clean — must not match Outer > Inner's hint.
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_describe_in_line_comment_is_ignored(tmp_path: Path):
    """Linus pass 2 #2: ``// describe('bad', ...)`` inside a line
    comment must NOT register 'bad' as a prefix. The first-pass
    ``_DESCRIBE_START_RE.finditer`` ran against the whole file with
    zero string/comment awareness — injecting describe-in-comment
    would let a Scenario Architect hallucination mass-reclassify
    any defect starting with the injected prefix."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "comment-inject.test.ts",
        """
// describe('FakePrefix', () => {
//   const c = postgres(process.env.URL);
//   await c`SELECT current_setting('app.x')`;
// });

describe('RealDescribe [FR-1]', () => {
  it('[AC-1] clean test', () => { expect(1).toBe(1); });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [
        {
            "test_name": "FakePrefix > some test",
            "category": "IMPL_BUG",
            "cited_anchor_ids": [],
            "rationale": "",
        },
        {
            "test_name": "RealDescribe [FR-1] > [AC-1] clean test",
            "category": "IMPL_BUG",
            "cited_anchor_ids": ["AC-1"],
            "rationale": "",
        },
    ])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_describe_in_block_comment_is_ignored(tmp_path: Path):
    """Same as line-comment case, for ``/* ... */`` blocks."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "block-inject.test.ts",
        """
/* describe('FakePrefix', () => {
     const c = postgres(process.env.URL);
     await c`SELECT current_setting('app.x')`;
   }); */

describe('Real [FR-1]', () => {
  it('[AC-1] x', () => {});
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "FakePrefix > something",
        "category": "IMPL_BUG",
        "cited_anchor_ids": [],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_describe_in_string_literal_is_ignored(tmp_path: Path):
    """Same as comment-injection: a describe call inside a string
    literal must NOT be harvested as a real describe."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "string-inject.test.ts",
        """
const example = "describe('FakePrefix', () => { postgres(process.env.URL); current_setting('app.x'); });";

describe('Real [FR-1]', () => {
  it('[AC-1] x', () => {});
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "FakePrefix > something",
        "category": "IMPL_BUG",
        "cited_anchor_ids": [],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_identifier_named_describeSomething_is_not_harvested(tmp_path: Path):
    """``describeTable()`` or ``fooDescribe()`` should not be treated
    as a ``describe(...)`` call. The tokenizer checks the preceding
    and following characters for identifier-continuation to enforce
    a word boundary."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "ident-boundary.test.ts",
        """
const r = describeTable('users');
const d = fooDescribe('bar');
describe('Real [FR-1]', () => {
  it('[AC-1] x', () => {});
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "describeTable > x",
        "category": "IMPL_BUG",
        "cited_anchor_ids": [],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_regex_literal_with_balanced_braces_works_accidentally(tmp_path: Path):
    """A regex literal with balanced braces like ``/^[0-9]{36}$/`` does
    NOT break brace matching — the ``{`` and ``}`` inside the regex
    balance each other, so depth returns to the describe-entry level
    only at the real closing brace. This happens to work correctly
    WITHOUT regex-literal awareness because the tokenizer treats ``/``
    as a division operator and ``{``/``}`` as normal brace tokens that
    happen to balance.

    Pins the accidental-correctness so a future "proper regex literal
    handling" refactor doesn't think this was broken."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "regex-balanced.test.ts",
        """
describe('RegexBalanced [FR-1]', () => {
  const UUID_RE = /^[0-9a-f-]{36}$/;
  const c = postgres(process.env.URL);
  it('[AC-1] x', async () => {
    await c`SELECT current_setting('app.t')`;
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "RegexBalanced [FR-1] > [AC-1] x",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 1


def test_regex_literal_with_unbalanced_brace_is_known_limitation(tmp_path: Path):
    """Documented limitation: a JS regex literal with an *unbalanced*
    ``}`` (e.g. ``/foo}bar/``) inside a describe body closes the
    describe prematurely for the brace-matcher. The tokenizer treats
    ``/`` as division, not a regex-literal opener — disambiguating
    regex-vs-division in JS requires tracking the preceding token
    context (regex follows ``=``, ``(``, ``return``, etc.; division
    follows identifiers and literals), which is non-trivial.

    Silent miss (hint fails to fire) is the safe direction versus
    silent leak: a classification-hint bug should reduce
    reclassification, never produce false positives. If a future
    refactor adds regex-literal awareness, this test flips from
    ``== 0`` to ``== 1`` — deliberate, not accidental."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "regex-unbalanced.test.ts",
        """
describe('RegexUnbalanced [FR-1]', () => {
  const RE = /a}b/;
  const c = postgres(process.env.URL);
  it('[AC-1] x', async () => {
    await c`SELECT current_setting('app.t')`;
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "RegexUnbalanced [FR-1] > [AC-1] x",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    # The `}` inside `/a}b/` closes the describe at that point. Body
    # is truncated before the postgres() call and current_setting
    # string, so the hint does not fire.
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_idempotent_across_repeat_calls(tmp_path: Path):
    """Repeated invocations against the same defects.json must not
    re-reclassify defects that already carry an ``internal_reason_code``.
    Retry / resume scenarios otherwise produce undefined order between
    hint A (pass 1) and hint B (pass 2). Linus #5."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "t.test.ts",
        """describe('T [FR-1]', () => {
  const c = postgres(process.env.DATABASE_URL);
  it('[AC-1] x', async () => {
    await c`SELECT current_setting('app.t')`;
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "T [FR-1] > [AC-1] x",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])

    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 1
    # Second call is a no-op: reason code already set.
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_existing_reason_code_is_preserved(tmp_path: Path):
    """A defect carrying an upstream ``internal_reason_code`` (e.g. from
    a future adapter that stamps codes at classification time) is left
    alone — we don't silently overwrite another agent's metadata."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "t.test.ts",
        """describe('T [FR-1]', () => {
  const c = postgres(process.env.X);
  it('[AC-1] x', async () => {
    await c`SELECT current_setting('app.t')`;
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "T [FR-1] > [AC-1] x",
        "category": "ENV_ISSUE",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
        "internal_reason_code": "UPSTREAM_CODE_X",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0
    doc = json.loads(defects_path.read_text(encoding="utf-8"))
    assert doc["defects"][0]["category"] == "ENV_ISSUE"
    assert doc["defects"][0]["internal_reason_code"] == "UPSTREAM_CODE_X"


def test_peer_client_does_not_fire_on_session_level_gucs(tmp_path: Path):
    """Linus #2: the peer-client hint must not fire on ``current_setting``
    reads of session-level GUCs like ``TimeZone`` or ``server_version``.
    A peer client reading those is perfectly legitimate; only tx-local
    ``app.*`` reads off a peer connection are the documented footgun.

    The production pattern narrows to ``current_setting('app.`` — this
    test pins that narrowing so it isn't silently loosened."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "session.test.ts",
        """describe('Session GUCs [FR-9]', () => {
  const c = postgres(process.env.DATABASE_URL);
  it('[AC-1] reads TimeZone', async () => {
    const r = await c`SELECT current_setting('TimeZone')`;
    expect(r).toBeDefined();
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "Session GUCs [FR-9] > [AC-1] reads TimeZone",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_force_before_enable_also_suppresses_owner_hint(tmp_path: Path):
    """Linus #3: FORCE appearing *before* ENABLE in the scan scope must
    also suppress the hint. The original regex used a post-match-tail
    negative lookahead, producing a false-positive when FORCE preceded
    ENABLE. The post-Linus pattern anchors with ``\\A`` and negative-
    looks-ahead for FORCE anywhere in the scope."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "rls-reorder.test.ts",
        """describe('RLS Reordered [AC-7]', () => {
  await client`ALTER TABLE t FORCE ROW LEVEL SECURITY`;
  await client`ALTER TABLE t ENABLE ROW LEVEL SECURITY`;
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "RLS Reordered [AC-7] > assertion",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-7"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_OWNER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_malformed_describe_with_unbalanced_braces_skips_silently(tmp_path: Path):
    """A scenario file whose brace-matching walks off the end of the
    file is dropped from the prefix set — returning a partial body
    could produce over-matches (a pattern in post-describe module code
    bleeds into the describe's body). Safer to skip.
    """
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "unbalanced.test.ts",
        """describe('Unbalanced [FR-1]', () => {
  const c = postgres(process.env.X);
  it('[AC-1] x', async () => {
    await c`SELECT current_setting('app.t')`;
  // missing closing braces
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "Unbalanced [FR-1] > [AC-1] x",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    # No matching body → no reclassification. This is the safe direction.
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_string_containing_brace_does_not_break_matching(tmp_path: Path):
    """The brace-matcher honors string literals so a ``}`` inside a
    quoted string doesn't close the describe prematurely. Previously a
    describe body containing ``const s = '}';`` would be truncated at
    that point and the rest of the pattern would be missed."""
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "str-brace.test.ts",
        r"""describe('Braces [FR-1]', () => {
  const s = '}';
  const c = postgres(process.env.X);
  it('[AC-1] x', async () => {
    await c`SELECT current_setting('app.t')`;
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "Braces [FR-1] > [AC-1] x",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 1


def test_dollar_prefixed_describe_is_not_harvested(tmp_path: Path):
    """``$describe`` is a valid JS identifier (``$`` is legal in both
    start and continue positions, per ECMAScript). The tokenizer's
    word-boundary guards must reject both ``$describe(...)`` (prefix-
    matched ``$`` before) and ``describe$foo(...)`` (suffix ``$`` after)
    the keyword. Without ``$`` in the guards, a user-defined helper
    like ``const $describe = (n, f) => describe('FAKE'+n, f);`` would
    be misread as ``describe('FAKE...')`` and pollute the describe map.

    This pins Linus pass 3 Critical #2 — the fix is adding ``"$"`` to
    both the pre- and post-keyword identifier-continuation checks in
    ``_tokenize_describe_tree``.
    """
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "dollar-ident.test.ts",
        """
const $describe = (n, f) => describe('Real [FR-1]', f);
const describe$foo = (n, f) => describe('RealToo [FR-2]', f);
$describe('Injected [AC-X]', () => {
  const c = postgres(process.env.X);
  it('[AC-X] leak', async () => {
    await c`SELECT current_setting('app.t')`;
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        # If $describe was mis-harvested, this test_name would match
        # its fake "Injected [AC-X]" describe path.
        "test_name": "Injected [AC-X] > [AC-X] leak",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-X"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    # $describe must not appear in the harvested describe map. The only
    # real describes are 'Real [FR-1]' and 'RealToo [FR-2]' (both
    # bodyless arrow-headers here, no real it() blocks), so the
    # defect's test_name "Injected [AC-X] > ..." matches no prefix and
    # no reclassification happens.
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_unbalanced_regex_drops_entire_file_not_just_its_describe(
    tmp_path: Path,
):
    """Linus pass 3 Critical #1: a single describe whose body contains
    an unbalanced regex literal (``/a}b/``) makes the brace-matcher
    miscount at the file level. Any ``results`` entry already pushed
    before that point has a ``body_end`` computed against the wrong
    brace depth, so sibling describes may be silently attributed to
    earlier ones.

    The tokenizer's defense is: if ``stack`` is non-empty at EOF,
    return ``[]`` (drop the entire file's describe map). Dropping
    everything is a silent miss; publishing partial corrupted results
    would be a silent leak that misclassifies real defects. Pin the
    drop-everything behavior so a future refactor doesn't "optimize"
    it to return the partially-good entries.
    """
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "corrupted.test.ts",
        """
describe('CleanDescribe [FR-1]', () => {
  const c = postgres(process.env.URL);
  it('[AC-1] should reclassify', async () => {
    await c`SELECT current_setting('app.t')`;
  });
});
describe('PoisonDescribe [FR-2]', () => {
  const RE = /a}b/;
  it('[AC-2] ok', () => {});
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        # This defect would match the CleanDescribe hint in isolation.
        # The poison file causes the entire file's describe map to be
        # dropped, so even this clean describe gets no hint applied.
        "test_name": "CleanDescribe [FR-1] > [AC-1] should reclassify",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    # 0 means the whole file was dropped. That's the safe direction:
    # silent miss, not wrong attribution.
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 0


def test_hint_pattern_inside_template_literal_body_is_not_attributed(
    tmp_path: Path,
):
    """Template literals (backtick strings) are opaque to the
    tokenizer — the lexer enters string state on `` ` `` and stays
    there until the closing backtick, so any ``describe(...)`` inside
    a template literal body is ignored. But what about a hint-pattern
    payload appearing inside a template literal INSIDE a real
    describe's body? It DOES match, because the regex runs against
    the raw body text (which includes the template-literal contents
    verbatim).

    This is intentional: template-literal *content* is visible to the
    regex, so both the tagged shape ``postgres`` \\`SELECT
    current_setting('app.t')\\` `` AND the untagged shape
    ``c.unsafe(\\`SELECT current_setting('app.t')\\`)`` — which is
    what the fixture below uses — match the peer-client regex.
    Both are production patterns, both are real footguns.

    The test pins this behavior so a future "strip template literals
    before regex-matching" optimization doesn't silently kill
    production hint firing. Per Linus pass 3 SHOULD #6.
    """
    scenarios_dir = tmp_path / "scenarios"
    _write_scenario(
        scenarios_dir / "template-hint.test.ts",
        """describe('TemplateHit [FR-1]', () => {
  const c = postgres(process.env.URL);
  it('[AC-1] runs via tagged template', async () => {
    const q = `SELECT current_setting('app.tenant_id')`;
    const result = await c.unsafe(q);
    expect(result).toBeDefined();
  });
});
""",
    )
    defects_path = tmp_path / "defects.json"
    _write_defects(defects_path, [{
        "test_name": "TemplateHit [FR-1] > [AC-1] runs via tagged template",
        "category": "IMPL_BUG",
        "cited_anchor_ids": ["AC-1"],
        "rationale": "",
    }])
    adapter = _MockHintAdapter(hints=[_HINT_PEER])
    # Hint fires because the body-scoped regex sees the raw text of
    # the describe body, including the template-literal content.
    assert apply_classification_hints(
        defects_path, scenarios_dir, [adapter],
    ) == 1


# ---------------------------------------------------------------------------
# ABC default — adapters without an override return empty
# ---------------------------------------------------------------------------


class _MinimalAdapter(EcosystemAdapter):
    """Bare-minimum concrete adapter for testing the ABC default
    ``classification_hints`` behavior. Only implements the abstract
    methods; no override of the concrete hint method."""

    @property
    def name(self) -> str:
        return "minimal"

    @property
    def detection_signals(self) -> tuple[str, ...]:
        return ("marker",)

    def detect(self) -> bool:
        return False

    def precheck(self, spec):
        return []

    def consistency_rules(self):
        return []

    def architect_addendum(self) -> str:
        return ""

    def spec_validator_addendum(self) -> str:
        return ""

    def scenario_architect_addendum(self) -> str:
        return ""

    def quality_gate_dispatch(self, manifest):
        from darkfactory.adapters.base import GateCommands
        return GateCommands()

    def proposal_rehearsal_typecheck(self, scratch_dir: Path):
        return None

    def verify_substrate(self, sidecar_url, manifest_path, spec_text):
        return None


def test_abc_default_classification_hints_is_empty():
    assert _MinimalAdapter().classification_hints() == []
