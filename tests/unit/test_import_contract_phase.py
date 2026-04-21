"""Tests for scripts/lib/render_import_contract_phase.py.

The runner (vitest invocation) is not tested here — it requires a real
Node project with vitest installed. These tests cover the manifest
parser, scratch-file synthesis, and skip/fail emission paths, which is
where the error-prone logic lives. Integration exercise of the vitest
invocation happens during real pipeline runs.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase


# Import the gate script as a module despite its location outside the
# ``darkfactory`` package tree. The script deliberately lives under
# ``scripts/lib/`` because it runs inside the gate container from a
# different mount point.
_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2]
    / "scripts" / "lib" / "render_import_contract_phase.py"
)
_spec = importlib.util.spec_from_file_location(
    "render_import_contract_phase", _SCRIPT_PATH,
)
assert _spec is not None and _spec.loader is not None
rp = importlib.util.module_from_spec(_spec)
sys.modules["render_import_contract_phase"] = rp
_spec.loader.exec_module(rp)


_SAMPLE_MANIFEST = """\
spec: demo
runtime: {}

test_integration:
  module_system: NodeNext
  import_extension: .js
  vitest_config: apps/api/vitest.config.ts
  tsconfig: apps/api/tsconfig.json
  entry_points:
    - path: apps/api/src/server.ts
      specifier: "../../../apps/api/src/server.js"
      exports: [startServer, AppConfig]
      note: "HTTP server entry"
    - path: apps/api/src/parser.ts
      specifier: "../../../apps/api/src/parser.js"
      exports: [parseRecord]
      note: "request parser"

forbidden_patterns: {}
"""


class TestParseTestIntegration(unittest.TestCase):
    def test_scalar_fields(self) -> None:
        ti = rp.parse_test_integration(_SAMPLE_MANIFEST)
        self.assertIsNotNone(ti)
        self.assertEqual(ti.module_system, "NodeNext")
        self.assertEqual(ti.import_extension, ".js")
        self.assertEqual(ti.vitest_config, "apps/api/vitest.config.ts")
        self.assertEqual(ti.tsconfig, "apps/api/tsconfig.json")

    def test_entry_points(self) -> None:
        ti = rp.parse_test_integration(_SAMPLE_MANIFEST)
        self.assertEqual(len(ti.entry_points), 2)
        ep0 = ti.entry_points[0]
        self.assertEqual(ep0.path, "apps/api/src/server.ts")
        self.assertEqual(ep0.specifier, "../../../apps/api/src/server.js")
        self.assertEqual(ep0.exports, ["startServer", "AppConfig"])
        self.assertEqual(ep0.note, "HTTP server entry")
        ep1 = ti.entry_points[1]
        self.assertEqual(ep1.exports, ["parseRecord"])

    def test_missing_block(self) -> None:
        text = "spec: demo\nruntime: {}\n"
        self.assertIsNone(rp.parse_test_integration(text))

    def test_empty_entry_points(self) -> None:
        text = (
            "test_integration:\n"
            "  module_system: NodeNext\n"
            "  import_extension: .js\n"
            "  vitest_config: vitest.config.ts\n"
            "  tsconfig: tsconfig.json\n"
            "  entry_points: []\n"
        )
        ti = rp.parse_test_integration(text)
        self.assertEqual(ti.entry_points, [])

    def test_quoted_specifier(self) -> None:
        text = (
            "test_integration:\n"
            "  vitest_config: vitest.config.ts\n"
            "  entry_points:\n"
            "    - path: src/x.ts\n"
            "      specifier: '../x.js'\n"
            "      exports: ['a', \"b\"]\n"
            "      note: quoted\n"
        )
        ti = rp.parse_test_integration(text)
        self.assertEqual(ti.entry_points[0].specifier, "../x.js")
        self.assertEqual(ti.entry_points[0].exports, ["a", "b"])


class TestBlockStyleExports(unittest.TestCase):
    """Regression for run dc53cf0d: block-style exports must parse.

    The hand-rolled parser mishandled block-style ``exports:`` lists
    (one item per line) and returned empty ``exports``, which
    propagated to the scratch test and emitted a false FAIL on every
    well-formed manifest. ``yaml.safe_load`` handles both shapes.
    """

    def test_block_style(self) -> None:
        text = (
            "test_integration:\n"
            "  module_system: NodeNext\n"
            "  import_extension: .js\n"
            "  vitest_config: apps/api/vitest.config.ts\n"
            "  tsconfig: apps/api/tsconfig.json\n"
            "  entry_points:\n"
            "    - path: apps/api/src/server.ts\n"
            "      specifier: ../../../apps/api/src/server.js\n"
            "      exports:\n"
            "        - startServer\n"
            "        - AppConfig\n"
            "      note: HTTP server entry\n"
        )
        ti = rp.parse_test_integration(text)
        self.assertIsNotNone(ti)
        self.assertEqual(len(ti.entry_points), 1)
        self.assertEqual(
            ti.entry_points[0].exports, ["startServer", "AppConfig"],
        )

    def test_malformed_yaml_returns_none(self) -> None:
        # A tab-damaged list (common paste hazard) must fail soft —
        # parse_test_integration returns None, which the gate maps to
        # SKIP rather than crashing the phase.
        text = "test_integration:\n\tentry_points: []\n"
        self.assertIsNone(rp.parse_test_integration(text))


class TestSynthScratchFile(unittest.TestCase):
    def test_uses_namespace_import(self) -> None:
        """Top-level namespace import guarantees the module is evaluated.

        A named-only import (``import { x } from '...'``) can be
        tree-shaken by esbuild/vitest if the binding is unused, which
        would silently turn a real resolution failure into a PASS.
        """
        ti = rp.TestIntegration(
            module_system="NodeNext", import_extension=".js",
            vitest_config="vitest.config.ts", tsconfig="tsconfig.json",
            entry_points=[],
        )
        entry = rp.EntryPoint(
            path="src/x.ts", specifier="../x.js",
            exports=["foo", "bar"], note="",
        )
        out = rp._synth_scratch_file(0, entry, ti)
        self.assertIn("import * as _mod from \"../x.js\"", out)
        self.assertIn("\"foo\"", out)
        self.assertIn("\"bar\"", out)
        # The assertion loop must be present or a failure would silently pass.
        self.assertIn("_keys.includes(name)", out)

    def test_specifier_with_quote_is_json_escaped(self) -> None:
        entry = rp.EntryPoint(
            path="src/x.ts",
            specifier="../weird\"name.js",
            exports=["a"], note="",
        )
        ti = rp.TestIntegration("", "", "", "", [])
        out = rp._synth_scratch_file(0, entry, ti)
        self.assertIn('"../weird\\"name.js"', out)


class TestSkipPaths(TempDirTestCase):
    def _prep(self) -> tuple[Path, Path]:
        report = self.tmp_dir / "report.md"
        summary = self.tmp_dir / "summary.md"
        report.touch()
        summary.touch()
        return report, summary

    def test_non_node_toolchain_skips(self) -> None:
        report, summary = self._prep()
        rc = rp.render(report, summary, "demo", "python")
        self.assertEqual(rc, 0)
        self.assertIn("SKIP", report.read_text())
        self.assertIn("toolchain=python", report.read_text())

    def test_missing_manifest_skips(self) -> None:
        report, summary = self._prep()
        import os
        old = os.getcwd()
        os.chdir(self.tmp_dir)
        try:
            rc = rp.render(report, summary, "no-such-spec", "node")
        finally:
            os.chdir(old)
        self.assertEqual(rc, 0)
        self.assertIn("SKIP", report.read_text())
        self.assertIn("no architect manifest", report.read_text())

    def test_manifest_without_test_integration_skips(self) -> None:
        import os
        arch_dir = self.tmp_dir / ".dark-factory" / "architecture"
        arch_dir.mkdir(parents=True)
        (arch_dir / "demo-manifest.yaml").write_text(
            "spec: demo\nruntime: {}\nforbidden_patterns: {}\n"
        )
        report, summary = self._prep()
        old = os.getcwd()
        os.chdir(self.tmp_dir)
        try:
            rc = rp.render(report, summary, "demo", "node")
        finally:
            os.chdir(old)
        self.assertEqual(rc, 0)
        self.assertIn("no test_integration.entry_points", report.read_text())


if __name__ == "__main__":
    unittest.main()
