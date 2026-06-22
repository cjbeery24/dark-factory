"""Tests for ``check_substrate_deferral_consistency`` (F + E2).

The asymmetric-deferral guard: a ``verifiable=false`` substrate primitive
suppresses gate verification but, without a ``covers`` list, never defers
its anchors at the coverage floor — so a Blocking AC asserting that
capability gets an unsatisfiable scenario (the bb spec-4 FR-2 escalation).

F  (INERT_DEFERRAL)        — verifiable=false primitive missing `covers`.
E2 (BLOCKING_OVER_DEFERRED) — a covered anchor that is also Blocking.

The FIRST test is the ACTIVATION test (Neo directive): the *verbatim*
spec-4 substrate manifest that escalated MUST trip F. If it doesn't, the
whole guard is void.
"""

from pathlib import Path

from darkfactory.scenarios.coverage import (
    DeferralConsistencyFinding,
    check_substrate_deferral_consistency,
)


# The verbatim spec-4 substrate manifest (run 746b040324184abb) that
# escalated: both primitives verifiable=false, NEITHER declares `covers`.
SPEC4_MANIFEST = """\
spec_id = "2026-03-28-database-foundation"
postgres_version = "16"

[[guc]]
name = "app.tenant_id"
default = ""
registration_check = "SELECT current_setting('app.tenant_id', true)"

[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false

[[rls_policy]]
name = "f1c_baseline_no_policies"
expected_policies = []
verifiable = false

[[bootstrap_check]]
name = "drizzle-migrations-tracking-table"
check_sql = "SELECT 1 FROM information_schema.tables WHERE table_name='__drizzle_migrations'"
expect = "row_present"
"""


def _write(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "substrate.toml"
    p.write_text(body, encoding="utf-8")
    return p


# --- ACTIVATION (must fire on the spec that motivated the guard) ----------

def test_activation_spec4_manifest_fires_F(tmp_path):
    """The verbatim escalating spec-4 manifest MUST produce F findings for
    BOTH verifiable=false primitives (neither declares `covers`)."""
    path = _write(tmp_path, SPEC4_MANIFEST)
    findings = check_substrate_deferral_consistency(path, blocking_anchors=set())
    inert = [f for f in findings if f.kind == "INERT_DEFERRAL"]
    primitives = {f.primitive for f in inert}
    assert primitives == {"tenant_isolation", "rls_policy"}, (
        f"activation failed — F did not fire on the spec-4 manifest: {findings}"
    )
    assert all(f.anchor is None for f in inert)
    # The guc / bootstrap_check entries have no verifiable=false → untouched.
    assert len(inert) == 2


# --- F: covers presence ---------------------------------------------------

def test_covers_empty_list_satisfies_F(tmp_path):
    """`covers = []` is a positive 'governs no anchors' assertion — it must
    NOT trip F (Neo Q1/Q2: `[]` satisfies the invariant)."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = []
"""
    findings = check_substrate_deferral_consistency(_write(tmp_path, body), set())
    assert findings == []


def test_covers_present_nonempty_satisfies_F(tmp_path):
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = ["AC-14", "AC-16"]
"""
    # No Blocking set → covers present → neither F nor E2.
    findings = check_substrate_deferral_consistency(_write(tmp_path, body), set())
    assert findings == []


def test_missing_covers_fires_F_with_actionable_message(tmp_path):
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
"""
    findings = check_substrate_deferral_consistency(_write(tmp_path, body), set())
    assert len(findings) == 1
    assert findings[0].kind == "INERT_DEFERRAL"
    assert "covers" in findings[0].message
    assert "tenant_isolation" in findings[0].message


# --- E2: covers ∩ Blocking ------------------------------------------------

def test_E2_fires_when_covered_anchor_is_blocking(tmp_path):
    """The resolution case: once `covers` is declared, a covered anchor that
    is also Blocking is the cross-section contradiction."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = ["AC-12", "AC-14", "AC-16", "AC-18"]
"""
    blocking = {"AC-13", "AC-14", "AC-16", "AC-18", "AC-19", "AC-20", "EC-5"}
    findings = check_substrate_deferral_consistency(_write(tmp_path, body), blocking)
    offenders = {f.anchor for f in findings if f.kind == "BLOCKING_OVER_DEFERRED"}
    # AC-14/16/18 are covered AND blocking; AC-12 is covered but NOT blocking.
    assert offenders == {"AC-14", "AC-16", "AC-18"}
    assert all(f.kind == "BLOCKING_OVER_DEFERRED" for f in findings)


def test_lowercase_covers_entry_is_malformed_not_silently_passed(tmp_path):
    """Linus/Codex: the Blocking set is canonical-uppercase and crediting is
    case-sensitive, so a lowercase covers entry would silently under-fire E2
    AND fail to credit. Flag it as MALFORMED_COVERS rather than pass it."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = ["ac-14"]
"""
    findings = check_substrate_deferral_consistency(
        _write(tmp_path, body), {"AC-14"}
    )
    assert len(findings) == 1
    assert findings[0].kind == "MALFORMED_COVERS"
    assert "ac-14" in findings[0].message


def test_noncanonical_and_nonstring_covers_entries_are_malformed(tmp_path):
    """Codex MAJOR: `covers` being a list is not enough — entries that don't
    match the canonical anchor pattern (`14` int, `"not-an-anchor"`, lowercase
    `"ac-14"`) won't be credited by ``architect_credited_anchors``, recreating
    the inert asymmetry. Each must be flagged MALFORMED_COVERS."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = [14, "not-an-anchor", "ac-14"]
"""
    findings = check_substrate_deferral_consistency(_write(tmp_path, body), set())
    assert len(findings) == 3
    assert all(f.kind == "MALFORMED_COVERS" for f in findings)


def test_zero_padded_anchor_passes_guard_handled_downstream(tmp_path):
    """Boundary: `"AC-014"` MATCHES the canonical regex (same one
    ``architect_credited_anchors`` uses), so it is credited as key `AC-014`.
    The real `AC-14` then stays uncovered and is caught by the floor's
    existing uncovered-anchor check — not this guard. Deliberate: this guard
    stays consistent with what actually gets credited."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = ["AC-014"]
"""
    # AC-014 is not in the blocking set (which holds AC-14), so no E2 either.
    findings = check_substrate_deferral_consistency(
        _write(tmp_path, body), {"AC-14"}
    )
    assert findings == []


def test_E2_silent_when_covered_anchors_not_blocking(tmp_path):
    """Covered anchors that are non-blocking are the CORRECT deferral — no
    finding (this is exactly what re-scoping AC-14/16/18 produces)."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = ["AC-12", "AC-15", "AC-17"]
"""
    blocking = {"AC-13", "AC-19", "AC-20"}  # mechanism ACs, not the outcome ones
    findings = check_substrate_deferral_consistency(_write(tmp_path, body), blocking)
    assert findings == []


# --- E3: covers ∩ live scenario targets (non-silent on non-blocking) ------

def test_E3_fires_on_nonblocking_covered_live_anchor(tmp_path):
    """The integrity fix: a covered-deferred anchor that is NOT blocking but is
    STILL a live TE target (e.g. AC-12/15/17) must be surfaced — it would get
    an unsatisfiable scenario. E2 is silent on it; E3 is not."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = ["AC-12", "AC-15", "AC-17"]
"""
    # None blocking; all three are live TE targets.
    blocking = {"AC-13", "AC-19", "AC-20"}
    live = {"AC-12", "AC-15", "AC-17", "AC-13", "AC-19", "AC-20"}
    findings = check_substrate_deferral_consistency(
        _write(tmp_path, body), blocking, live_scenario_anchors=live
    )
    flagged = {f.anchor for f in findings if f.kind == "LIVE_SCENARIO_OVER_DEFERRED"}
    assert flagged == {"AC-12", "AC-15", "AC-17"}
    assert all(f.kind == "LIVE_SCENARIO_OVER_DEFERRED" for f in findings)


def test_E2_and_E3_both_fire_for_blocking_and_live_anchor(tmp_path):
    """A covered anchor that is BOTH Blocking and live gets both findings —
    they point at different edits (de-block vs exclude-from-scenarios)."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = ["AC-14"]
"""
    findings = check_substrate_deferral_consistency(
        _write(tmp_path, body), {"AC-14"}, live_scenario_anchors={"AC-14"}
    )
    kinds = {f.kind for f in findings}
    assert kinds == {"BLOCKING_OVER_DEFERRED", "LIVE_SCENARIO_OVER_DEFERRED"}


def test_E3_silent_when_covered_anchor_excluded_from_scenarios(tmp_path):
    """The correct end-state: a covered anchor that is @untestable /
    forward-reference is NOT in the live set, so E3 is silent — this is a
    properly-deferred forward-reference (the re-scope target state)."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = ["AC-12", "AC-14"]
"""
    # AC-12/AC-14 marked @untestable upstream → excluded from the live set.
    findings = check_substrate_deferral_consistency(
        _write(tmp_path, body), blocking_anchors=set(), live_scenario_anchors=set()
    )
    assert findings == []


def test_E3_skipped_when_live_set_not_provided(tmp_path):
    """Backwards-compatible: omitting live_scenario_anchors disables E3."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = false
covers = ["AC-12"]
"""
    findings = check_substrate_deferral_consistency(_write(tmp_path, body), set())
    assert findings == []


# --- Scope: verifiable=false only -----------------------------------------

def test_verifiable_true_untouched(tmp_path):
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = true
covers = ["AC-14"]
"""
    blocking = {"AC-14"}
    findings = check_substrate_deferral_consistency(_write(tmp_path, body), blocking)
    assert findings == [], "verifiable=true is gate-verified — no asymmetry, no check"


def test_verifiable_absent_untouched(tmp_path):
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
"""
    findings = check_substrate_deferral_consistency(
        _write(tmp_path, body), {"AC-14"}
    )
    assert findings == [], "verifiable absent → defaults to verify → untouched"


def test_verifiable_string_sentinel_not_treated_as_false(tmp_path):
    """Boolean identity, not truthiness: verifiable='false' (a string) is a
    malformed value other gates own — it is NOT `is False`, so this guard
    leaves it alone rather than mis-routing it."""
    body = """\
[[tenant_isolation]]
tenant_table = "tenants"
verifiable = "false"
"""
    findings = check_substrate_deferral_consistency(
        _write(tmp_path, body), {"AC-14"}
    )
    assert findings == []


# --- Robustness -----------------------------------------------------------

def test_missing_file_yields_no_findings(tmp_path):
    findings = check_substrate_deferral_consistency(
        tmp_path / "does-not-exist.toml", {"AC-14"}
    )
    assert findings == []


def test_malformed_toml_yields_no_findings(tmp_path):
    findings = check_substrate_deferral_consistency(
        _write(tmp_path, "this is not = valid toml ]["), {"AC-14"}
    )
    assert findings == []


def test_finding_is_frozen_dataclass():
    f = DeferralConsistencyFinding(
        kind="INERT_DEFERRAL", primitive="x", anchor=None, message="m"
    )
    assert f.kind == "INERT_DEFERRAL"
