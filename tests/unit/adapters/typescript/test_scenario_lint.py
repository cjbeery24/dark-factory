"""Task #21 RULE A — pre-impl scenario-authoring lint (path-escape clamp).

The Test Engineer authors scenarios BLIND (source masked). A relative-path
literal whose ``../`` run climbs above the project root typechecks cleanly
but is a guaranteed ENOENT at gate time (the bb spec-4 AC-25 failure). The
TypeScript adapter's ``lint_scenarios`` clamps such a run to the max
in-bounds depth — deterministic and host-side (no agent, no manifest).
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.adapters.base import EcosystemAdapter
from darkfactory.adapters.typescript.adapter import TypescriptNodeAdapter


def _scenario_dir(root: Path, spec: str = "spec-x") -> Path:
    d = root / ".dark-factory" / "scenarios" / spec
    d.mkdir(parents=True)
    return d


class TestPathEscapeClamp(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.sdir = _scenario_dir(self.root)
        self.adapter = TypescriptNodeAdapter(project_dir=self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_clamps_overdeep_run_to_root_depth(self) -> None:
        # File is 3 levels below root (.dark-factory/scenarios/spec-x); a
        # 4-deep ``../`` run escapes the root and is clamped to 3.
        f = self.sdir / "fr3-journal.test.ts"
        f.write_text(
            "const j = new URL("
            "'../../../../apps/api/src/db/migrations/meta/_journal.json', "
            "import.meta.url);\n"
        )
        findings = self.adapter.lint_scenarios(self.sdir)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].code, "scenario-path-escape-clamped")
        self.assertEqual(findings[0].severity, "info")
        self.assertEqual(findings[0].line, 1)
        self.assertIn(
            "'../../../apps/api/src/db/migrations/meta/_journal.json'",
            f.read_text(),
        )
        self.assertNotIn("../../../../apps", f.read_text())

    def test_in_bounds_literal_untouched(self) -> None:
        # Exactly root-depth (3) is in-bounds — no rewrite, no finding.
        f = self.sdir / "ok.test.ts"
        original = "const u = new URL('../../../apps/x', import.meta.url);\n"
        f.write_text(original)
        findings = self.adapter.lint_scenarios(self.sdir)
        self.assertEqual(findings, [])
        self.assertEqual(f.read_text(), original)

    def test_escaping_relative_import_also_clamped(self) -> None:
        # Clamping is context-independent: an import specifier that escapes
        # the root can never resolve as a module either, so it is clamped
        # like any other literal (depth-aware — see the in-bounds test for
        # the non-escaping case that stays untouched).
        f = self.sdir / "imp.test.ts"
        f.write_text(
            "import { foo } from '../../../../pkg/foo';\n"
            "export { bar } from '../../../../pkg/bar';\n"
            "const x = require('../../../../pkg/baz');\n"
        )
        findings = self.adapter.lint_scenarios(self.sdir)
        self.assertEqual(len(findings), 3)
        text = f.read_text()
        self.assertIn("from '../../../pkg/foo'", text)
        self.assertIn("from '../../../pkg/bar'", text)
        self.assertIn("require('../../../pkg/baz')", text)
        self.assertNotIn("../../../../pkg", text)

    def test_in_bounds_import_untouched(self) -> None:
        # A relative import that is correct for the file's depth is NOT an
        # escape and must be left alone.
        f = self.sdir / "okimp.test.ts"
        original = "import { foo } from '../../../pkg/foo';\n"  # depth 3, in-bounds
        f.write_text(original)
        self.assertEqual(self.adapter.lint_scenarios(self.sdir), [])
        self.assertEqual(f.read_text(), original)

    def test_template_literal_path_clamped(self) -> None:
        # Backtick-delimited path literals fail the same way at runtime and
        # are clamped (when free of ${...} interpolation).
        f = self.sdir / "tmpl.test.ts"
        f.write_text("const p = new URL(`../../../../escape/x`, import.meta.url);\n")
        findings = self.adapter.lint_scenarios(self.sdir)
        self.assertEqual(len(findings), 1)
        self.assertIn("`../../../escape/x`", f.read_text())

    def test_template_literal_with_interpolation_untouched(self) -> None:
        # A ${...} interpolation makes the path dynamic — clamping a static
        # prefix could be wrong, so the literal is left untouched.
        f = self.sdir / "dyn.test.ts"
        original = "const p = new URL(`../../../../${dir}/x`, import.meta.url);\n"
        f.write_text(original)
        self.assertEqual(self.adapter.lint_scenarios(self.sdir), [])
        self.assertEqual(f.read_text(), original)

    def test_comment_line_skipped(self) -> None:
        # A ../ literal inside a leading-comment line is not executable —
        # no rewrite, no spurious finding.
        f = self.sdir / "cmt.test.ts"
        original = (
            "// const old = '../../../../legacy/path';\n"
            " * see '../../../../also/legacy'\n"
        )
        f.write_text(original)
        self.assertEqual(self.adapter.lint_scenarios(self.sdir), [])
        self.assertEqual(f.read_text(), original)

    def test_import_then_url_escape_on_separate_lines(self) -> None:
        # The headline robustness property: an import line never blocks
        # linting of a DIFFERENT line. Both the escaping import and the
        # escaping URL are clamped independently.
        f = self.sdir / "mixed.test.ts"
        f.write_text(
            "import { db } from '../../../../pkg/db';\n"
            "const p = new URL('../../../../apps/x', import.meta.url);\n"
        )
        findings = self.adapter.lint_scenarios(self.sdir)
        self.assertEqual(len(findings), 2)
        text = f.read_text()
        self.assertIn("from '../../../pkg/db'", text)
        self.assertIn("'../../../apps/x'", text)

    def test_non_utf8_file_skipped(self) -> None:
        # A non-UTF-8 scenario file must be skipped, never read-with-replace
        # then written back (which would corrupt it).
        f = self.sdir / "bin.test.ts"
        raw = b"const p = new URL('../../../../x', import.meta.url);\n\xff\xfe"
        f.write_bytes(raw)
        self.assertEqual(self.adapter.lint_scenarios(self.sdir), [])
        self.assertEqual(f.read_bytes(), raw)  # untouched

    def test_framework_helper_dirs_skipped(self) -> None:
        # _contracts / _df_setup are framework-staged (correct by
        # construction), not TE-authored — never rewritten.
        for helper in ("_contracts", "_df_setup"):
            hd = self.sdir / helper
            hd.mkdir()
            hf = hd / "helper.ts"
            original = "const p = '../../../../../escape';\n"
            hf.write_text(original)
            findings = self.adapter.lint_scenarios(self.sdir)
            self.assertEqual(findings, [])
            self.assertEqual(hf.read_text(), original)

    def test_depth_computed_per_file_for_nested_dirs(self) -> None:
        # A scenario in a nested subdir is 4 below root; a 4-deep run is
        # in-bounds there, but a 5-deep run is clamped to 4.
        nested = self.sdir / "sub"
        nested.mkdir()
        f = nested / "n.test.ts"
        f.write_text(
            "const a = new URL('../../../../in', import.meta.url);\n"
            "const b = new URL('../../../../../out', import.meta.url);\n"
        )
        findings = self.adapter.lint_scenarios(self.sdir)
        self.assertEqual(len(findings), 1)
        text = f.read_text()
        self.assertIn("'../../../../in'", text)          # 4-deep untouched
        self.assertIn("'../../../../out'", text)         # 5-deep clamped to 4
        self.assertNotIn("../../../../../out", text)

    def test_no_project_dir_is_noop(self) -> None:
        # The contract-harness instantiation (no project_dir) must no-op.
        f = self.sdir / "x.test.ts"
        f.write_text("const p = new URL('../../../../../x', import.meta.url);\n")
        adapter = TypescriptNodeAdapter()  # no project_dir
        self.assertEqual(adapter.lint_scenarios(self.sdir), [])

    def test_missing_scenario_dir_is_noop(self) -> None:
        self.assertEqual(
            self.adapter.lint_scenarios(self.root / "does-not-exist"), []
        )

    def test_multiple_literals_one_line(self) -> None:
        f = self.sdir / "multi.test.ts"
        f.write_text(
            "const pair = ["
            "new URL('../../../../a', import.meta.url), "
            "new URL('../../../../b', import.meta.url)];\n"
        )
        findings = self.adapter.lint_scenarios(self.sdir)
        self.assertEqual(len(findings), 2)
        text = f.read_text()
        self.assertIn("'../../../a'", text)
        self.assertIn("'../../../b'", text)


class TestBaseSeamNoop(unittest.TestCase):
    def test_base_lint_scenarios_is_noop(self) -> None:
        # The ABC default must return [] without touching anything — every
        # adapter that doesn't override inherits a safe no-op.
        with TemporaryDirectory() as tmp:
            sdir = _scenario_dir(Path(tmp))
            (sdir / "x.test.ts").write_text(
                "const p = new URL('../../../../../x', import.meta.url);\n"
            )
            # EcosystemAdapter is abstract; exercise the unbound default
            # directly to prove the seam contract is a pure no-op.
            self.assertEqual(
                EcosystemAdapter.lint_scenarios(object(), sdir), []  # type: ignore[arg-type]
            )


if __name__ == "__main__":
    unittest.main()
