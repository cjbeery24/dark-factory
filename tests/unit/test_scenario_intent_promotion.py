"""End-to-end coverage-floor regression for Scenario-Intent promotion.

Neo's highest-leverage guard (docs/dev/2026-06-25-scenario-intent-promotion-
design.md §7): the orphan-intent handle (``FR-N#M``) must be a first-class key
across the WHOLE floor spine — parsed from §9, demanded by ``compute_coverage``,
credited by a ``@covers FR-2#3`` scenario, credited by a substrate ``covers``
list, and waivable by ``@untestable`` — or the mechanism is wired-but-inert.

The fixture reproduces the spec-5 (run20) structure that mis-shipped:
  - FR-1: ALL intents orphan (RLS / DDL — substrate-vehicle, not HTTP scenarios)
  - FR-2: mixed; FR-2#3 is the lone orphan (whitespace→401 security intent that
    the impl's ``.trim()`` silently accepted)
  - FR-3: one orphan error intent (FR-3#5)
  - Cross-Cutting: a security orphan under a NON-FR parent (advisory-only — no
    handle, the documented limitation)
"""

from __future__ import annotations

from pathlib import Path

from darkfactory.scenarios.coverage import (
    _extract_untestable_markers,
    architect_credited_anchors,
    compute_coverage,
)
from darkfactory.spec.precheck import (
    check_orphan_intent_advisory,
    extract_anchor_manifest,
)

# A faithful-but-minimal spec carrying the §9 structure that matters. Only the
# sections the manifest extractor reads need to be well-formed.
_SPEC = """# Auth Schemas Middleware

## 2. Functional Requirements

### FR-1: Tenant/User schemas with RLS
### FR-2: JWT authentication middleware
### FR-3: User identity API endpoint

## 8. Acceptance Criteria

- AC-1: expired JWT → 401
- AC-2: malformed header → 401
- AC-3: failed-auth body has no secrets
- AC-4: unknown user → 401
- AC-5: unknown tenant → 401
- AC-6: /health → 200
- AC-7: missing config → startup fail
- AC-8: header-injection guarded

## 9. Scenario Intent

### FR-1: Tenant/User schemas with RLS
| # | Intent | Layer | ac_id |
|---|--------|-------|-------|
| 1 | tenant A context sees only tenant A rows | security | — |
| 2 | no tenant context returns zero rows | security | — |
| 3 | FK integrity rejects orphan tenantId | error | — |
| 4 | tables exist after migration | contract | — |
| 5 | duplicate clerkOrgId rejected | error | — |

### FR-2: JWT authentication middleware
| # | Intent | Layer | ac_id |
|---|--------|-------|-------|
| 1 | expired JWT → 401 | error | AC-1 |
| 2 | malformed header → 401 | error | AC-2 |
| 3 | whitespace-padded Bearer token returns 401 | security | — |
| 4 | failed-auth body has no secrets | security | AC-3 |
| 5 | missing config causes startup failure | edge_cases | AC-7 |

### FR-3: User identity API endpoint
| # | Intent | Layer | ac_id |
|---|--------|-------|-------|
| 1 | unknown user → 401 | error | AC-4 |
| 2 | unknown tenant → 401 | error | AC-5 |
| 3 | /health → 200 | contract | AC-6 |
| 4 | envelope wraps user+tenant | contract | — |
| 5 | unauthenticated /auth/me → 401 | error | — |

### Cross-Cutting
| # | Intent | Layer | ac_id |
|---|--------|-------|-------|
| 1 | secrets never appear in logs | security | — |
| 2 | header-injection guarded | adversarial | AC-8 |
"""

# The blocking-layer (security/error/adversarial) orphans under an FR parent.
# Cross-Cutting#1 is excluded (non-FR parent); the *contract* orphans
# (FR-1#4, FR-3#4) are excluded (non-blocking layer).
_EXPECTED_HANDLES = {"FR-1#1", "FR-1#2", "FR-1#3", "FR-1#5", "FR-2#3", "FR-3#5"}


def _manifest():
    return extract_anchor_manifest(_SPEC)


def test_orphan_handles_are_exactly_the_blocking_fr_orphans():
    handles = set(_manifest().orphan_obligation_handles())
    assert handles == _EXPECTED_HANDLES
    # The mis-ship intent is demanded.
    assert "FR-2#3" in handles
    # The contract orphan and the non-FR-parent orphan are NOT demanded.
    assert "FR-1#4" not in handles
    assert "Cross-Cutting#1" not in handles


def test_floor_demands_uncovered_handles(tmp_path: Path):
    # No scenarios at all → every handle is uncovered, alongside the anchors.
    report = compute_coverage(tmp_path, _manifest())
    for handle in _EXPECTED_HANDLES:
        assert handle in report.uncovered_anchors, f"{handle} should be demanded"


def test_scenario_credits_the_specific_handle(tmp_path: Path):
    # A test tagged [FR-2#3] credits the handle AND its parent FR-2; a *generic*
    # FR-2 test does NOT credit the orphan handle.
    (tmp_path / "whitespace.test.ts").write_text(
        "describe('mw', () => {\n"
        "  it('rejects whitespace-padded token [FR-2#3]', () => {});\n"
        "  it('expired jwt [FR-2]', () => {});\n"
        "});\n",
        encoding="utf-8",
    )
    report = compute_coverage(tmp_path, _manifest())
    assert "FR-2#3" not in report.uncovered_anchors
    assert "FR-2" not in report.uncovered_anchors
    # The other orphan handles are still demanded.
    assert "FR-1#1" in report.uncovered_anchors
    assert "FR-3#5" in report.uncovered_anchors


def test_covers_tag_does_not_swallow_next_line_comment(tmp_path: Path):
    """Linus regression: the @covers capture must NOT cross a newline. A `#` on
    the tag line previously let the class swallow the following line's prose,
    false-crediting a stray anchor (the run20 false-credit class)."""
    from darkfactory.scenarios.coverage import _extract_anchors_from_text

    text = (
        "// @covers FR-2#3\n"
        "// see also AC-99 in the other module\n"
        "it('whitespace token rejected', () => {});\n"
    )
    credited = _extract_anchors_from_text(text)
    assert "FR-2#3" in credited
    assert "FR-2" in credited
    assert "AC-99" not in credited  # the next-line comment must NOT be credited


def test_covers_tag_still_parses_multi_anchor_single_line():
    """The single-line bound must not break the normal comma-separated form."""
    from darkfactory.scenarios.coverage import _extract_anchors_from_text

    credited = _extract_anchors_from_text(
        "/** @covers FR-1, AC-7 */\nit('x', () => {});\n"
    )
    assert {"FR-1", "AC-7"} <= credited


def test_generic_fr_test_does_not_satisfy_the_orphan_handle(tmp_path: Path):
    # The whole point of the handle: a bare [FR-2] test must not silently close
    # the FR-2#3 obligation.
    (tmp_path / "generic.test.ts").write_text(
        "describe('mw', () => { it('some fr-2 thing [FR-2]', () => {}); });\n",
        encoding="utf-8",
    )
    report = compute_coverage(tmp_path, _manifest())
    assert "FR-2#3" in report.uncovered_anchors


def test_substrate_covers_credits_fr1_handles(tmp_path: Path):
    """The FR-1 chicken-and-egg: a substrate primitive declaring
    ``covers = ["FR-1#1", ...]`` MUST credit the orphan handles, so an
    RLS-vehicle orphan routes to substrate (not an impossible HTTP scenario)."""
    manifest_yaml = tmp_path / "spec-manifest.yaml"
    manifest_yaml.write_text("forbidden_patterns: {}\n", encoding="utf-8")
    substrate = tmp_path / "substrate.toml"
    substrate.write_text(
        "[[tenant_isolation]]\n"
        'name = "users_rls"\n'
        'covers = ["FR-1#1", "FR-1#2", "FR-1#3", "FR-1#5"]\n',
        encoding="utf-8",
    )
    credited = architect_credited_anchors(manifest_yaml, substrate)
    for handle in ("FR-1#1", "FR-1#2", "FR-1#3", "FR-1#5"):
        assert handle in credited, f"{handle} must be substrate-credited"

    # Simulate the orchestrator's _credit_report: after subtracting credited,
    # the FR-1 handles are gone but FR-2#3 (HTTP scenario vehicle) remains.
    report = compute_coverage(tmp_path, _manifest())
    remaining = [a for a in report.uncovered_anchors if a not in credited]
    assert "FR-2#3" in remaining
    assert "FR-3#5" in remaining
    for handle in ("FR-1#1", "FR-1#2", "FR-1#3", "FR-1#5"):
        assert handle not in remaining


def test_untestable_waives_a_handle():
    markers = _extract_untestable_markers(
        "// @untestable FR-3#5: covered by an integration smoke, not a unit scenario\n"
    )
    assert "FR-3#5" in markers
    assert markers["FR-3#5"].startswith("covered by an integration smoke")


# ---------------------------------------------------------------------------
# Parser robustness (Neo: column-count variants / non-FR parents / malformed /
# unknown layers must be load-bearing, never crash).
# ---------------------------------------------------------------------------

def _parse(si_body: str):
    spec = (
        "# S\n\n## 2. Functional Requirements\n### FR-1: x\n\n"
        "## 8. Acceptance Criteria\n- AC-1: x\n\n"
        "## 9. Scenario Intent\n" + si_body
    )
    return extract_anchor_manifest(spec)


def test_three_column_table_no_ac_id_is_orphan():
    # | # | Intent | Layer |  — no ac_id column at all → orphan.
    m = _parse(
        "### FR-1: x\n| # | Intent | Layer |\n|---|--------|-------|\n"
        "| 1 | reject bad token | security |\n"
    )
    assert m.intents[0].layer == "security"
    assert m.intents[0].mapped_anchor is None
    assert "FR-1#1" in m.orphan_obligation_handles()


def test_three_column_table_last_cell_is_anchor_is_mapped():
    # | # | Intent | ac_id |  — last cell an anchor id → mapped (no layer column).
    m = _parse(
        "### FR-1: x\n| # | Intent | ac_id |\n|---|--------|-------|\n"
        "| 1 | reject bad token | AC-1 |\n"
    )
    assert m.intents[0].mapped_anchor == "AC-1"
    assert m.orphan_obligation_handles() == []


def test_unknown_layer_is_non_blocking():
    m = _parse(
        "### FR-1: x\n| # | Intent | Layer | ac_id |\n|---|--------|-------|-------|\n"
        "| 1 | something | weird_layer | — |\n"
    )
    assert m.intents[0].layer == "weird_layer"
    assert m.orphan_obligation_handles() == []  # fail-safe: not demanded


def test_non_fr_parent_orphan_yields_no_handle():
    m = _parse(
        "### Cross-Cutting\n| # | Intent | Layer | ac_id |\n|---|--------|-------|-------|\n"
        "| 1 | secrets never logged | security | — |\n"
    )
    assert m.intents[0].parent == "Cross-Cutting"
    assert m.orphan_obligation_handles() == []


def test_malformed_rows_do_not_crash():
    # Rows that don't match the intent-row shape are simply skipped; a numeric
    # first cell with too few columns must not raise.
    m = _parse(
        "### FR-1: x\n| # | Intent | Layer | ac_id |\n|---|--------|-------|-------|\n"
        "| 1 | ok | security | — |\n"
        "| not-a-number | junk |\n"
    )
    # Only the well-formed row is captured.
    assert any(it.index == 1 and it.layer == "security" for it in m.intents)


def test_advisory_lists_blocking_orphans_only():
    advisories = check_orphan_intent_advisory(_manifest())
    joined = "\n".join(advisories)
    # FR-parented blocking orphans appear with their handle hint.
    assert "[FR-2#3]" in joined
    assert "[FR-1#1]" in joined
    # The non-FR-parent security orphan is surfaced with the distinct note.
    assert "Cross-Cutting" in joined
    # Mapped / contract orphans are NOT advised.
    assert "FR-2#1" not in joined  # mapped
    assert "[FR-1#4]" not in joined  # contract layer


def test_manifest_intents_json_roundtrip(tmp_path: Path):
    from darkfactory.spec.anchors import AnchorManifest

    m = _manifest()
    p = tmp_path / "anchor_manifest.json"
    m.to_json_path(p)
    back = AnchorManifest.from_json_path(p)
    assert back is not None
    assert {(i.parent, i.index, i.layer, i.mapped_anchor) for i in back.intents} == {
        (i.parent, i.index, i.layer, i.mapped_anchor) for i in m.intents
    }
    assert set(back.orphan_obligation_handles()) == _EXPECTED_HANDLES


def test_from_json_path_tolerates_malformed_intent_entries(tmp_path: Path):
    import json as _json
    from darkfactory.spec.anchors import AnchorManifest

    p = tmp_path / "anchor_manifest.json"
    p.write_text(
        _json.dumps({
            "fr_ids": ["FR-1"], "ac_ids": [], "ec_ids": [],
            "intents": [
                {"parent": "FR-1", "index": 1, "layer": "security", "mapped_anchor": None},
                {"parent": "FR-1"},                 # missing index → dropped
                {"index": "x", "parent": "FR-1"},   # bad index type → dropped
                "not-a-dict",                       # wrong type → dropped
            ],
        }),
        encoding="utf-8",
    )
    back = AnchorManifest.from_json_path(p)
    assert back is not None
    assert len(back.intents) == 1
    assert back.intents[0].parent == "FR-1"
