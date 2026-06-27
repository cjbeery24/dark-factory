"""Behavioral-credit rule — close the lexical-credit hole (Fix #1, step 2).

Credit is gated on a GROUND-TRUTH import classification (the adapter's
parse_test_imports), not a regex: a Tier-2 scenario credits only if it imports
>=1 PROJECT module. Source-greps / empty tests credit nothing. Tier-1 contracts
(``00-tier1-`` prefix) + markdown exempt. Default OFF = unchanged.

Covers the cases Linus/Codex flagged: `@/` alias + workspace-scope project
imports are NOT false-denied; a Tier-2 file naming itself ``*-contracts`` does
NOT bypass (prefix-anchored exemption).
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.adapters.base import ImportInfo
from darkfactory.scenarios.coverage import (
    compute_behavioral_files,
    compute_coverage,
)
from darkfactory.spec.precheck import AnchorManifest

SOURCE_GREP = (
    "import { describe, it, expect } from 'vitest';\n"
    "import { readFileSync } from 'node:fs';\n"
    "it('[AC-2] app.tsx redirects', () => {\n"
    "  expect(readFileSync('app.tsx','utf-8')).toContain('Navigate');\n"
    "});\n"
)


def _m(ac=None, fr=None):
    return AnchorManifest(fr_ids=fr or [], ac_ids=ac or [], ec_ids=[], declared_intents={})


class _FakeAdapter:
    """Stands in for an ecosystem adapter — returns the imports we assign per file."""

    def __init__(self, imports_by_name: dict[str, list[str]]):
        self._m = imports_by_name

    def parse_test_imports(self, path: Path) -> list[ImportInfo]:
        return [ImportInfo(specifier=s) for s in self._m.get(path.name, [])]


# ---- compute_coverage consumes the behavioral_files set --------------------
class TestCreditGate(unittest.TestCase):
    def test_default_off_unchanged(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "b7-routing-sources.test.ts").write_text(SOURCE_GREP, encoding="utf-8")
            r = compute_coverage(d, _m(ac=["AC-2"]))  # flag OFF
            self.assertEqual(r.per_anchor_counts["AC-2"], 1)

    def test_non_behavioral_credits_nothing(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "b7-routing-sources.test.ts").write_text(SOURCE_GREP, encoding="utf-8")
            r = compute_coverage(
                d, _m(ac=["AC-2"]),
                require_behavioral_credit=True,
                behavioral_files=set(),  # classifier found no project import
            )
            self.assertEqual(r.per_anchor_counts["AC-2"], 0)
            self.assertEqual(r.uncovered_anchors, ["AC-2"])

    def test_behavioral_file_credits(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            p = d / "routing.test.tsx"
            p.write_text("it('[AC-2] x', () => {});\n", encoding="utf-8")
            r = compute_coverage(
                d, _m(ac=["AC-2"]),
                require_behavioral_credit=True,
                behavioral_files={str(p)},
            )
            self.assertEqual(r.per_anchor_counts["AC-2"], 1)

    def test_tier1_prefix_is_exempt(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "00-tier1-contracts.test.ts").write_text(
                "it('[AC-1] config', () => {});\n", encoding="utf-8")
            r = compute_coverage(
                d, _m(ac=["AC-1"]),
                require_behavioral_credit=True, behavioral_files=set())
            self.assertEqual(r.per_anchor_counts["AC-1"], 1)  # exempt → credits

    def test_fake_named_contracts_does_NOT_bypass(self) -> None:
        """A Tier-2 file naming itself *-contracts must NOT get the Tier-1 exemption."""
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "routing-contracts.test.ts").write_text(SOURCE_GREP, encoding="utf-8")
            r = compute_coverage(
                d, _m(ac=["AC-2"]),
                require_behavioral_credit=True, behavioral_files=set())
            self.assertEqual(r.per_anchor_counts["AC-2"], 0)  # no bypass

    def test_markdown_exempt(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "flow.scenarios.md").write_text(
                "# Routing\n\n## AC-2\nGIVEN / WHEN visited THEN redirects\n", encoding="utf-8")
            r = compute_coverage(
                d, _m(ac=["AC-2"]),
                require_behavioral_credit=True, behavioral_files=set())
            self.assertGreaterEqual(r.per_anchor_counts["AC-2"], 1)


# ---- compute_behavioral_files: the ground-truth classifier -----------------
class TestClassifier(unittest.TestCase):
    def _run(self, imports_by_name: dict[str, list[str]], prefixes=()):
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            for name in imports_by_name:
                (d / name).write_text("// scenario\n", encoding="utf-8")
            ad = _FakeAdapter(imports_by_name)
            return d, compute_behavioral_files(d, [ad], prefixes)

    def test_relative_import_is_project(self) -> None:
        d, b = self._run({"a.test.ts": ["vitest", "../../../apps/api/src/server.ts"]})
        self.assertIn(str(d / "a.test.ts"), b)

    def test_source_grep_is_not_project(self) -> None:
        d, b = self._run({"b7.test.ts": ["vitest", "node:fs", "node:url", "node:path"]})
        self.assertEqual(b, set())

    def test_alias_import_not_false_denied(self) -> None:
        """`@/app` is a PROJECT import given the project's alias prefix — must credit."""
        d, b = self._run({"r.test.tsx": ["vitest", "@/app"]}, prefixes=("@/",))
        self.assertIn(str(d / "r.test.tsx"), b)

    def test_workspace_scope_import_not_false_denied(self) -> None:
        d, b = self._run(
            {"s.test.ts": ["vitest", "@budget-brain/shared"]},
            prefixes=("@budget-brain/",))
        self.assertIn(str(d / "s.test.ts"), b)

    def test_path_mapped_absolute_import_not_false_denied(self) -> None:
        d, b = self._run({"p.test.ts": ["apps/api/src/server"]}, prefixes=("apps/",))
        self.assertIn(str(d / "p.test.ts"), b)

    def test_external_only_is_not_project(self) -> None:
        """A test importing only third-party (no project) is not behavioral here."""
        d, b = self._run({"e.test.ts": ["vitest", "@testing-library/react", "jose"]})
        self.assertEqual(b, set())

    def test_import_and_grep_passes_floor_known_limitation(self) -> None:
        """Necessary-not-sufficient: import-the-unit-AND-grep passes the FLOOR.
        The blind-holdout gate is the sufficient layer (documented, not a regression)."""
        d, b = self._run({"x.test.ts": ["../app"]})  # imports project + (would also grep)
        self.assertIn(str(d / "x.test.ts"), b)


if __name__ == "__main__":
    unittest.main()
