"""Anchor manifest — neutral home for AnchorManifest dataclass and IO.

Lives outside ``spec/precheck.py`` so ``scenarios/coverage.py`` and
``pipeline/orchestrator.py`` can depend on the manifest without pulling
in the precheck module. JSON is the canonical on-disk format for both
the manifest and the post-gen coverage gaps file; YAML appears only as
outbound prompt formatting (in ``pipeline/iteration.py``).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Scenario-Intent layers that carry an observable behavioral-rejection contract.
# An UNMAPPED (ac_id = —) intent in one of these layers is a coverage obligation
# the floor must demand a scenario for (the run20 whitespace→401 mis-ship class).
# Other layers (contract, edge_cases, unknown/missing) are NON-blocking — they
# legitimately roll up to a parent-anchor / substrate / DDL vehicle, so demanding
# a per-intent scenario for them would 7-blocker existing specs (Neo 2nd-pass
# ruling, docs/dev/2026-06-25-scenario-intent-promotion-design.md). Fail-safe
# default for an unrecognized layer is NON-blocking (membership test below).
_BLOCKING_ORPHAN_LAYERS = frozenset({"security", "error", "adversarial"})

# An orphan-intent coverage handle is anchor-shaped (FR-N#M) so it rides the
# SAME credit / substrate-covers / @untestable / remediation spine as a real
# anchor. Only FR-parented intents can form one — a Cross-Cutting / free-text /
# ungrouped parent has no FR id, so its orphans are advisory-only (documented
# limitation; the demonstrated FR-2#3 mis-ship has an FR parent).
_FR_PARENT_RE = re.compile(r"^FR-\d+$")


@dataclass(frozen=True)
class ScenarioIntent:
    """One row of the spec's §9 Scenario Intent table.

    ``parent`` is the ``### `` sub-heading the row sits under — usually an FR id
    (``"FR-2"``) but possibly a non-anchor label (``"Cross-Cutting"``,
    ``"(ungrouped)"``, an HTTP-verb group). ``index`` is the row's ``#`` column.
    ``mapped_anchor`` is the ``ac_id`` column resolved to a canonical anchor id,
    or ``None`` when the column is ``—`` / empty / absent (an *orphan* intent).
    """

    parent: str
    index: int
    text: str
    layer: str
    mapped_anchor: str | None = None


@dataclass
class AnchorManifest:
    """Enumerates every FR/AC/EC anchor declared in a spec."""

    fr_ids: list[str] = field(default_factory=list)
    ac_ids: list[str] = field(default_factory=list)
    ec_ids: list[str] = field(default_factory=list)
    # anchor_id -> intent count from the Scenario Intent section
    declared_intents: dict[str, int] = field(default_factory=dict)
    # Structured §9 Scenario-Intent rows (additive — superset of declared_intents,
    # which stays a count for back-compat with weakly_covered). The floor and the
    # future discriminating-power check both read layer/text/parent from here.
    intents: list[ScenarioIntent] = field(default_factory=list)
    # ACs annotated with vehicle: subprocess in the spec's AC table.
    # These are excluded from ac_ids before the manifest is persisted,
    # so the test-engineer and coverage floor never see them.
    subprocess_ac_ids: list[str] = field(default_factory=list)
    # ACs the AUTHOR declared @untestable (anchor_id -> reason) in the spec's
    # AC table. Like subprocess_ac_ids, these are excluded from ac_ids before
    # persist, so the coverage floor never demands a Tier-2 scenario for an
    # anchor the author declared un-scenario-testable. Author signal in the
    # spec (visible to everyone); removing a floor demand is de-escalation, so
    # this crosses no information barrier (see docs/AUTHORIZATION-MODEL.md).
    untestable_ac_ids: dict[str, str] = field(default_factory=dict)
    # Discrimination classification (2026-06-25) for behavioral-rejection ACs
    # (the security/error/adversarial slice). Parsed from §8 AC-table markers:
    #   `disc:differential`        -> input-attributable rejection; the holdout
    #                                 must carry a control+rejection pair.
    #   `disc:invariant: <reason>` -> content/log invariant (no valid sibling);
    #                                 routes to forbidden-pattern/content checks.
    # The differential-classification gate (check_differential_intent_blockers)
    # REVISEs any slice AC carrying NEITHER marker. invariant_ac_ids feeds
    # scenarios.differential.derive_differential_anchors as the invariant opt-out
    # (single source of truth — the gate and the gen-lint read the same §8 set).
    differential_ac_ids: list[str] = field(default_factory=list)
    invariant_ac_ids: dict[str, str] = field(default_factory=dict)

    # Note: forbidden_patterns is NOT tracked here. The architect writes
    # it directly to the architecture YAML manifest, and the
    # deterministic preflight reads from that YAML as the single source
    # of truth. A previous iteration mirrored forbidden_patterns into
    # this JSON too, creating two sources of truth — Linus flagged that
    # and the sync was dropped.

    def to_json_path(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def from_json_path(cls, path: Path) -> AnchorManifest | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        # Reconstruct ScenarioIntent rows per-entry fail-soft: a malformed entry
        # (wrong keys / type) is dropped, never crashes the read — same discipline
        # as the outer ``return None`` on a corrupt file.
        intents: list[ScenarioIntent] = []
        for d in data.get("intents", []):
            try:
                intents.append(ScenarioIntent(
                    parent=str(d["parent"]),
                    index=int(d["index"]),
                    text=str(d.get("text", "")),
                    layer=str(d.get("layer", "")),
                    mapped_anchor=(
                        str(d["mapped_anchor"])
                        if d.get("mapped_anchor") is not None
                        else None
                    ),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(
            fr_ids=list(data.get("fr_ids", [])),
            ac_ids=list(data.get("ac_ids", [])),
            ec_ids=list(data.get("ec_ids", [])),
            declared_intents=dict(data.get("declared_intents", {})),
            subprocess_ac_ids=list(data.get("subprocess_ac_ids", [])),
            untestable_ac_ids=dict(data.get("untestable_ac_ids", {})),
            differential_ac_ids=list(data.get("differential_ac_ids", [])),
            invariant_ac_ids=dict(data.get("invariant_ac_ids", {})),
            intents=intents,
        )

    def orphan_obligation_handles(self) -> list[str]:
        """Deterministic ``FR-N#M`` handles for blocking-layer ORPHAN intents.

        An orphan (``mapped_anchor is None``) intent in a blocking layer
        (:data:`_BLOCKING_ORPHAN_LAYERS`) under an ``FR-N`` parent becomes a
        first-class coverage obligation keyed ``<parent>#<index>``. The handle is
        anchor-shaped so it rides the existing ``compute_coverage`` /
        ``architect_credited_anchors`` (substrate ``covers``) / ``@untestable`` /
        ``render_floor_remediation`` spine unchanged. Non-FR parents and
        non-blocking / mapped intents yield no handle. Order-preserving + deduped.
        """
        seen: dict[str, None] = {}
        for it in self.intents:
            if it.mapped_anchor is not None:
                continue
            if it.layer not in _BLOCKING_ORPHAN_LAYERS:
                continue
            if not _FR_PARENT_RE.match(it.parent):
                continue
            seen.setdefault(f"{it.parent}#{it.index}", None)
        return list(seen.keys())


def manifest_path(project_dir: Path, spec_name: str) -> Path:
    return (
        project_dir / ".dark-factory" / "state" / spec_name / "anchor_manifest.json"
    )


def coverage_gaps_path(project_dir: Path, spec_name: str) -> Path:
    return (
        project_dir / ".dark-factory" / "state" / spec_name / "coverage_gaps.json"
    )
