"""Anchor manifest — neutral home for AnchorManifest dataclass and IO.

Lives outside ``spec/precheck.py`` so ``scenarios/coverage.py`` and
``pipeline/orchestrator.py`` can depend on the manifest without pulling
in the precheck module. JSON is the canonical on-disk format for both
the manifest and the post-gen coverage gaps file; YAML appears only as
outbound prompt formatting (in ``pipeline/iteration.py``).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class AnchorManifest:
    """Enumerates every FR/AC/EC anchor declared in a spec."""

    fr_ids: list[str] = field(default_factory=list)
    ac_ids: list[str] = field(default_factory=list)
    ec_ids: list[str] = field(default_factory=list)
    # anchor_id -> intent count from the Scenario Intent section
    declared_intents: dict[str, int] = field(default_factory=dict)

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
        return cls(
            fr_ids=list(data.get("fr_ids", [])),
            ac_ids=list(data.get("ac_ids", [])),
            ec_ids=list(data.get("ec_ids", [])),
            declared_intents=dict(data.get("declared_intents", {})),
        )


def manifest_path(project_dir: Path, spec_name: str) -> Path:
    return (
        project_dir / ".dark-factory" / "state" / spec_name / "anchor_manifest.json"
    )


def coverage_gaps_path(project_dir: Path, spec_name: str) -> Path:
    return (
        project_dir / ".dark-factory" / "state" / spec_name / "coverage_gaps.json"
    )
