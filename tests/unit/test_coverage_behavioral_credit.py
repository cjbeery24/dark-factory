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
from darkfactory.adapters.typescript.adapter import TypescriptNodeAdapter
from darkfactory.scenarios.coverage import (
    compute_behavioral_files,
    compute_coverage,
    project_prefixes_from_manifest,
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


# ---- project_prefixes_from_manifest: the soundness gate (manifest, not tsconfig)
class TestPrefixDerivation(unittest.TestCase):
    _MANIFEST = (
        "language: node\n"
        "test_integration:\n"
        "  entry_points:\n"
        "    - path: apps/web/src/app.tsx\n"
        "      specifier: '@/app'\n"
        "      exports: [App]\n"
        "    - path: apps/api/src/server.ts\n"
        "      specifier: apps/api/src/server\n"
        "      exports: [createServer]\n"
    )

    def test_derives_specifier_and_dir_prefix(self) -> None:
        with TemporaryDirectory() as tmp:
            mpath = Path(tmp) / "spec-manifest.yaml"
            mpath.write_text(self._MANIFEST, encoding="utf-8")
            prefixes = project_prefixes_from_manifest(mpath)
        # exact declared specifiers AND their dir-prefixes. The dir-prefix carries a
        # DELIBERATE trailing slash (credit-gate tightening over the validator's bare
        # form) — locked in by test_trailing_slash_excludes_thirdparty below.
        self.assertIn("@/app", prefixes)
        self.assertIn("@/", prefixes)
        self.assertIn("apps/api/src/server", prefixes)
        self.assertIn("apps/api/src/", prefixes)

    def test_trailing_slash_excludes_thirdparty_scoped_pkg(self) -> None:
        """The reason the dir-prefix is ``@/`` not ``@``: a bare ``@`` would credit a
        test importing only a third-party scoped pkg (``@testing-library/react``) as
        behavioral project coverage. The trailing slash is load-bearing, not cosmetic."""
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "spec-manifest.yaml").write_text(self._MANIFEST, encoding="utf-8")
            prefixes = project_prefixes_from_manifest(d / "spec-manifest.yaml")
            # third-party scoped import must NOT be classified as a project import
            ad = _FakeAdapter({"e.test.ts": ["vitest", "@testing-library/react"]})
            (d / "e.test.ts").write_text("// x\n", encoding="utf-8")
            self.assertEqual(compute_behavioral_files(d, [ad], prefixes), set())

    def test_missing_manifest_returns_empty(self) -> None:
        prefixes = project_prefixes_from_manifest(Path("/no/such/manifest.yaml"))
        self.assertEqual(prefixes, ())

    def test_derived_prefixes_credit_alias_import(self) -> None:
        """End-to-end: manifest-derived `@/` prefix makes an `@/app` import behavioral."""
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "spec-manifest.yaml").write_text(self._MANIFEST, encoding="utf-8")
            prefixes = project_prefixes_from_manifest(d / "spec-manifest.yaml")
            ad = _FakeAdapter({"r.test.tsx": ["vitest", "@/app"]})
            (d / "r.test.tsx").write_text("// x\n", encoding="utf-8")
            b = compute_behavioral_files(d, [ad], prefixes)
            self.assertIn(str(d / "r.test.tsx"), b)


# ---- compute_coverage internal derivation (adapters, not injected set) ------
class TestInternalDerivation(unittest.TestCase):
    """Production path: caller passes adapters + prefixes, compute_coverage derives
    behavioral_files from its OWN scenarios_dir walk (Linus item 4 — no path-key drift)."""

    def test_adapters_path_denies_source_grep(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "b7-sources.test.ts").write_text(SOURCE_GREP, encoding="utf-8")
            ad = _FakeAdapter({"b7-sources.test.ts": ["vitest", "node:fs"]})
            r = compute_coverage(
                d, _m(ac=["AC-2"]),
                require_behavioral_credit=True, adapters=[ad], project_prefixes=(),
            )
            self.assertEqual(r.per_anchor_counts["AC-2"], 0)

    def test_adapters_path_credits_project_import(self) -> None:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            p = d / "routing.test.tsx"
            p.write_text("import {App} from '../app';\nit('[AC-2] x',()=>{});\n", encoding="utf-8")
            ad = _FakeAdapter({"routing.test.tsx": ["../app"]})
            r = compute_coverage(
                d, _m(ac=["AC-2"]),
                require_behavioral_credit=True, adapters=[ad], project_prefixes=(),
            )
            self.assertEqual(r.per_anchor_counts["AC-2"], 1)


# ---- REAL TS adapter: dynamic/side-effect/re-export now credited (prereq 1) --
class TestRealAdapterImportForms(unittest.TestCase):
    """parse_test_imports is regex/line-oriented. Prereq 1 extended it to also see
    dynamic import(), bare side-effect import, and re-export — a behavioral test
    reaching its unit via those forms is now correctly credited. `import type`
    stays DENIED (no runtime value imported); multi-line imports remain a known
    line-oriented limitation."""

    def _behavioral(self, body: str) -> bool:
        with TemporaryDirectory() as tmp:
            d = Path(tmp)
            p = d / "x.test.ts"
            p.write_text(body, encoding="utf-8")
            b = compute_behavioral_files(d, [TypescriptNodeAdapter()], ())
            return str(p) in b

    def test_static_import_is_behavioral(self) -> None:
        self.assertTrue(self._behavioral("import { App } from '../app';\n"))

    def test_cjs_require_is_behavioral(self) -> None:
        self.assertTrue(self._behavioral("const { App } = require('../app');\n"))

    def test_side_effect_import_is_behavioral(self) -> None:
        # `import '../app'` (no `from`) — now detected (kind=side_effect).
        self.assertTrue(self._behavioral("import '../app';\n"))

    def test_dynamic_import_is_behavioral(self) -> None:
        # `await import('../app')` — now detected (kind=dynamic).
        self.assertTrue(self._behavioral("const m = await import('../app');\n"))

    def test_reexport_is_behavioral(self) -> None:
        # `export { App } from '../app'` — now detected (kind=reexport).
        self.assertTrue(self._behavioral("export { App } from '../app';\n"))

    def test_reexport_star_is_behavioral(self) -> None:
        self.assertTrue(self._behavioral("export * from '../app';\n"))

    def test_import_type_still_denied(self) -> None:
        # Type-only import has no runtime value — must NOT earn behavioral credit.
        self.assertFalse(self._behavioral("import type { Props } from '../app';\n"))

    def test_reexport_type_still_denied(self) -> None:
        # Symmetric to import type: a type-only re-export must NOT earn credit.
        self.assertFalse(self._behavioral("export type { Props } from '../app';\n"))

    def test_reexport_default_is_behavioral(self) -> None:
        self.assertTrue(self._behavioral("export { default } from '../app';\n"))

    def test_reexport_star_as_ns_is_behavioral(self) -> None:
        self.assertTrue(self._behavioral("export * as ns from '../app';\n"))

    def test_dynamic_property_access_not_credited(self) -> None:
        # `foo.import('../app')` is a method call, not a dynamic import.
        self.assertFalse(self._behavioral("foo.import('../app');\n"))

    def test_dynamic_identifier_suffix_not_credited(self) -> None:
        # `myimport('../app')` is an identifier ending in "import", not a keyword.
        self.assertFalse(self._behavioral("myimport('../app');\n"))

    def test_bare_package_side_effect_not_credited(self) -> None:
        # `import 'reflect-metadata'` is detected (side_effect) but is third-party,
        # so _is_project_specifier denies it — not behavioral project coverage.
        self.assertFalse(self._behavioral("import 'reflect-metadata';\n"))

    def test_side_effect_does_not_misfire_on_static(self) -> None:
        # A normal static import must be kind=static (so the validator still sees it).
        ad = TypescriptNodeAdapter()
        with TemporaryDirectory() as tmp:
            p = Path(tmp) / "x.test.ts"
            p.write_text("import { App } from '../app';\n", encoding="utf-8")
            kinds = {i.kind for i in ad.parse_test_imports(p)}
        self.assertEqual(kinds, {"static"})


if __name__ == "__main__":
    unittest.main()
