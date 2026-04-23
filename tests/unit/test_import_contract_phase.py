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


class TestNormalizeSpecifierExtension(unittest.TestCase):
    """Regression for the "manifest specifies .js but agent writes .ts" drift.

    NodeNext + ``import_extension: .js`` cannot resolve ``.ts`` at runtime
    (tsc emits .js; imports must spell the emitted extension). Agents
    sometimes copy the source path into ``specifier`` verbatim, ending
    in ``.ts``. The synth path normalizes the extension so the gate
    tests the same specifier Node will actually see.
    """

    def test_ts_rewritten_to_js(self) -> None:
        self.assertEqual(
            rp._normalize_specifier_extension("../../../src/db.ts", ".js"),
            "../../../src/db.js",
        )

    def test_tsx_rewritten_to_js(self) -> None:
        self.assertEqual(
            rp._normalize_specifier_extension("../../../src/App.tsx", ".js"),
            "../../../src/App.js",
        )

    def test_mts_rewritten_to_mjs(self) -> None:
        self.assertEqual(
            rp._normalize_specifier_extension("../../../src/x.mts", ".mjs"),
            "../../../src/x.mjs",
        )

    def test_already_matching_unchanged(self) -> None:
        self.assertEqual(
            rp._normalize_specifier_extension("../../../src/db.js", ".js"),
            "../../../src/db.js",
        )

    def test_empty_import_extension_noop(self) -> None:
        # Non-Node toolchain / unset → return unchanged.
        self.assertEqual(
            rp._normalize_specifier_extension("../../../src/db.ts", ""),
            "../../../src/db.ts",
        )

    def test_no_extension_untouched(self) -> None:
        # Bare package imports like '@scope/name' must not get .js glued on.
        self.assertEqual(
            rp._normalize_specifier_extension("@scope/name", ".js"),
            "@scope/name",
        )
        self.assertEqual(
            rp._normalize_specifier_extension("drizzle-orm/postgres-js", ".js"),
            "drizzle-orm/postgres-js",
        )

    def test_unknown_extension_untouched(self) -> None:
        # .json / .css / etc. are legitimate bare imports — leave alone.
        self.assertEqual(
            rp._normalize_specifier_extension("../data.json", ".js"),
            "../data.json",
        )

    def test_dot_in_path_not_extension(self) -> None:
        # '../some.dir/file' — no extension on 'file', and '.dir' is
        # in the path component before the separator. Must not rewrite.
        self.assertEqual(
            rp._normalize_specifier_extension("../some.dir/file", ".js"),
            "../some.dir/file",
        )


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

    def test_synth_applies_extension_rewrite(self) -> None:
        """Manifest import_extension must win over the agent-authored extension.

        Synthesizer reads entry.specifier with ``.ts`` and ``import_extension=.js``;
        emitted file must import the ``.js`` form, matching what Node ESM
        resolves to after tsc emits.
        """
        ti = rp.TestIntegration(
            module_system="NodeNext", import_extension=".js",
            vitest_config="vitest.config.ts", tsconfig="tsconfig.json",
            entry_points=[],
        )
        entry = rp.EntryPoint(
            path="apps/api/src/db.ts",
            specifier="../../../apps/api/src/db.ts",
            exports=["withTenantDb"], note="",
        )
        out = rp._synth_scratch_file(0, entry, ti)
        self.assertIn(
            'import * as _mod from "../../../apps/api/src/db.js"', out,
        )
        # Comment header must also reflect the normalized specifier so
        # operators reading the rendered file aren't confused.
        self.assertIn("//   specifier: ../../../apps/api/src/db.js", out)
        # And the failure-message text embeds the normalized form.
        self.assertIn("../../../apps/api/src/db.js (entry_points[0])", out)


class TestSynthWrapperConfig(TempDirTestCase):
    """Regression for run 3b1d2bc7: wrapper config emitted a bare-specifier
    import of the user's vitest.config, which Node ESM resolves as a
    package name → ERR_MODULE_NOT_FOUND. The workspace-subdir path must
    prefix ./ and escape the string as a JSON/TS literal.
    """

    def test_workspace_subdir_emits_relative_import(self) -> None:
        """vitest.config.ts in the workspace root must import as ./vitest.config.ts."""
        scratch = self.tmp_dir / ".dark-factory" / "resolution-check" / "demo"
        scratch.mkdir(parents=True)
        _wrapper_path, _flag, body = rp._synth_wrapper_config(
            scratch, "apps/api/vitest.config.ts", "apps/api",
        )
        self.assertIn('import config from "./vitest.config.ts";', body)
        self.assertNotIn("import config from 'vitest.config.ts'", body)
        self.assertNotIn('import config from "vitest.config.ts"', body)

    def test_non_workspace_path_preserves_existing_depth(self) -> None:
        """Without workspace_dir, base_rel already starts with ../ — no mutation.

        Scratch dir ``.dark-factory/resolution-check/<spec>/`` is three
        levels below the project root, so the wrapper must walk back up
        with three ``../`` segments to reach a project-root-relative
        vitest.config.ts.
        """
        scratch = self.tmp_dir / ".dark-factory" / "resolution-check" / "demo"
        scratch.mkdir(parents=True)
        _wrapper_path, _flag, body = rp._synth_wrapper_config(
            scratch, "vitest.config.ts", None,
        )
        self.assertIn('import config from "../../../vitest.config.ts";', body)

    def test_config_path_with_quote_is_json_escaped(self) -> None:
        """Single-quote in the path would have broken the f-string template."""
        scratch = self.tmp_dir / ".dark-factory" / "resolution-check" / "demo"
        scratch.mkdir(parents=True)
        _wrapper_path, _flag, body = rp._synth_wrapper_config(
            scratch, "apps/api/weird'name.config.ts", "apps/api",
        )
        # json.dumps → JSON-escaped double-quoted string (valid TS too).
        self.assertIn('import config from "./weird\'name.config.ts";', body)

    def test_include_glob_is_json_escaped(self) -> None:
        """The include glob is also passed through json.dumps."""
        scratch = self.tmp_dir / ".dark-factory" / "resolution-check" / "demo"
        scratch.mkdir(parents=True)
        _wrapper_path, _flag, body = rp._synth_wrapper_config(
            scratch, "apps/api/vitest.config.ts", "apps/api",
        )
        # Workspace path: include glob walks back up to project root
        # (depth of "apps/api" is 2, so "../../").
        self.assertIn(
            'merged.test.include = ["../../.dark-factory/resolution-check/*/**/*.test.ts"];',
            body,
        )


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
