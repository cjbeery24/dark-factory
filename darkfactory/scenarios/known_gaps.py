"""Known-gaps skip list for shipped-with-gaps scenarios.

When Oracle ships a spec with documented gaps — failures classified
``ENV_ISSUE`` / ``BRITTLE`` / ``FLAKE`` that the Implementer cannot fix
because they depend on host state, test brittleness, or timing — the
gap is persisted into the spec's shipped-scenarios archive as
``.known-gaps.json``. Future scenario runs (regression, smoke probe,
direct CLI) consult this file and mark those specific tests as skipped
so a documented gap doesn't re-break the pipeline every time the spec
is exercised.

**Skippable categories are deliberately narrow.** Only ``ENV_ISSUE``,
``BRITTLE``, and ``FLAKE`` flow through. ``IMPL_BUG`` and
``SPEC_AMBIGUOUS`` are *not* skippable — those are real bugs or real
spec gaps and require a follow-up spec (or a human) to fix, not a mask.
If the category is anything else, the gap is recorded in the markdown
defect registry for observability but is *not* added to the skip list.

The file schema is versioned so the format can evolve without breaking
older archives. Readers that see a mismatched ``schema_version``
silently ignore the file rather than crashing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
KNOWN_GAPS_FILE = ".known-gaps.json"

# Only these three classifier categories translate into a skip at
# future-run time. IMPL_BUG and SPEC_AMBIGUOUS are intentionally
# excluded — hiding those would let real regressions slip through.
SKIPPABLE_CATEGORIES: frozenset[str] = frozenset({"ENV_ISSUE", "BRITTLE", "FLAKE"})

# Test names are written by the Test-Engineer authoring contract with a
# bracketed anchor prefix like "[AC-2]" / "[EC-1]" / "[FR-3]". We lift
# that out for the gap's ``anchor`` field so the defect registry stays
# traceable back to the spec section even if the test name is edited.
_ANCHOR_PREFIX_RE = re.compile(r"^\[([A-Z]+-\d+)\]")


@dataclass
class KnownGap:
    """A single documented gap carried forward from a shipped-with-gaps run."""

    test_name: str
    category: str
    anchor: str = ""
    added_at: str = ""
    source: str = ""  # e.g. "oracle_iter2", "manual_backfill"
    rationale: str = ""


@dataclass
class KnownGapsFile:
    """On-disk representation of ``.known-gaps.json``."""

    spec: str
    shipped_at: str = ""
    gaps: list[KnownGap] = field(default_factory=list)
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "spec": self.spec,
            "shipped_at": self.shipped_at,
            "gaps": [asdict(g) for g in self.gaps],
        }


def extract_anchor(test_name: str) -> str:
    """Extract the spec anchor (e.g. ``AC-2``) from a bracketed test name."""
    m = _ANCHOR_PREFIX_RE.match(test_name)
    return m.group(1) if m else ""


def read_known_gaps(dir_path: Path) -> KnownGapsFile | None:
    """Load ``dir_path/.known-gaps.json``.

    Returns ``None`` when:

    - the file is missing,
    - the file can't be parsed as JSON,
    - the top-level isn't a dict,
    - the ``schema_version`` doesn't match :data:`SCHEMA_VERSION`.

    Schema mismatches log a warning but never raise — an older archive
    stays inert until a migration runs rather than crashing the runner.
    """
    path = dir_path / KNOWN_GAPS_FILE
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        log.warning("known-gaps: unreadable file %s", path)
        return None
    if not isinstance(raw, dict):
        log.warning("known-gaps: top-level is not a dict in %s", path)
        return None
    version = raw.get("schema_version", 0)
    if version != SCHEMA_VERSION:
        log.warning(
            "known-gaps: schema version mismatch in %s (got %s, want %s) — ignoring",
            path, version, SCHEMA_VERSION,
        )
        return None

    gaps: list[KnownGap] = []
    for g in raw.get("gaps", []) or []:
        if not isinstance(g, dict):
            continue
        name = g.get("test_name")
        cat = g.get("category")
        if not name or not cat:
            continue
        gaps.append(
            KnownGap(
                test_name=str(name),
                category=str(cat),
                anchor=str(g.get("anchor") or ""),
                added_at=str(g.get("added_at") or ""),
                source=str(g.get("source") or ""),
                rationale=str(g.get("rationale") or ""),
            )
        )
    return KnownGapsFile(
        spec=str(raw.get("spec") or ""),
        shipped_at=str(raw.get("shipped_at") or ""),
        gaps=gaps,
        schema_version=version,
    )


def write_known_gaps(dir_path: Path, gaps_file: KnownGapsFile) -> Path:
    """Write a :class:`KnownGapsFile` to ``dir_path/.known-gaps.json``."""
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / KNOWN_GAPS_FILE
    path.write_text(
        json.dumps(gaps_file.to_dict(), indent=2, sort_keys=True) + "\n"
    )
    return path


def collect_skippable_names(dirs: list[Path]) -> list[str]:
    """Gather skippable test names across one-or-more scenario dirs.

    Only gaps whose ``category`` is in :data:`SKIPPABLE_CATEGORIES` are
    returned. Duplicates are de-duped preserving first-seen order so the
    resulting list is stable across runs with the same input directories.
    """
    seen: set[str] = set()
    out: list[str] = []
    for d in dirs:
        gf = read_known_gaps(d)
        if gf is None:
            continue
        for g in gf.gaps:
            if g.category not in SKIPPABLE_CATEGORIES:
                continue
            if g.test_name in seen:
                continue
            seen.add(g.test_name)
            out.append(g.test_name)
    return out


def build_vitest_setup_source(skippable: list[str]) -> str:
    """Return the JS source of a vitest setup file that skips known gaps.

    The generated file is injected via ``--setupFiles`` on the vitest
    CLI. It installs a ``beforeEach`` hook that matches ``ctx.task.name``
    against an exact-string allowlist and calls ``ctx.skip()`` if a
    match is found. Skipped tests still appear in the report as
    ``skipped`` — not ``passed`` — so observability is preserved.

    Exact-name match (not substring) is intentional: a gap on
    ``"[EC-1] foo"`` must never mask ``"[EC-1] foo bar"``.
    """
    names_json = json.dumps(skippable)
    return (
        "// Auto-generated by Dark Factory — do not edit.\n"
        "// Known-gaps skip list. Tests named below are marked skipped\n"
        "// because they were documented as ENV_ISSUE/BRITTLE/FLAKE on\n"
        "// the originating ship and are carried forward as known gaps.\n"
        "import { beforeEach } from 'vitest'\n"
        f"const __DF_KNOWN_GAPS = new Set({names_json})\n"
        "beforeEach((ctx) => {\n"
        "  if (__DF_KNOWN_GAPS.has(ctx.task.name)) {\n"
        "    ctx.skip()\n"
        "  }\n"
        "})\n"
    )


def build_pytest_conftest_source(skippable: list[str]) -> str:
    """Return the Python source of a conftest.py that skips known gaps.

    Installs ``pytest_collection_modifyitems`` that adds ``pytest.mark.skip``
    to any item whose display name (``item.name``, i.e. the part after
    the last ``::`` in the nodeid) matches the skip list exactly.
    """
    names_json = json.dumps(skippable)
    return (
        "# Auto-generated by Dark Factory — do not edit.\n"
        "# Known-gaps skip list.\n"
        "import json\n"
        "\n"
        "import pytest\n"
        "\n"
        f"_DF_KNOWN_GAPS = set(json.loads({names_json!r}))\n"
        "\n"
        "\n"
        "def pytest_collection_modifyitems(config, items):\n"
        '    skip = pytest.mark.skip(reason="dark-factory known gap")\n'
        "    for item in items:\n"
        "        if item.name in _DF_KNOWN_GAPS:\n"
        "            item.add_marker(skip)\n"
    )
