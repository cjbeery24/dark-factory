"""Tests for the deterministic TypeScript export extractor."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from darkfactory.extract.exports import (
    ContractViolation,
    ExportResult,
    extract_exports,
    format_violations,
    verify_export_contract,
)
from darkfactory.spec.manifest import EntryPoint


class TestExtractExports(unittest.TestCase):
    """Test regex-based export extraction from TypeScript source."""

    def setUp(self):
        self._tmpfiles: list[Path] = []

    def tearDown(self):
        for p in self._tmpfiles:
            p.unlink(missing_ok=True)

    def _write(self, content: str) -> Path:
        f = tempfile.NamedTemporaryFile(suffix=".ts", mode="w", delete=False)
        f.write(dedent(content))
        f.close()
        p = Path(f.name)
        self._tmpfiles.append(p)
        return p

    def test_named_function_export(self):
        p = self._write("""\
            export function createServer() {}
            export function startServer() {}
        """)
        result = extract_exports(p)
        self.assertEqual(result.exports, ["createServer", "startServer"])

    def test_async_function_export(self):
        p = self._write("""\
            export async function fetchData() {}
        """)
        result = extract_exports(p)
        self.assertEqual(result.exports, ["fetchData"])

    def test_const_let_var_exports(self):
        p = self._write("""\
            export const PORT = 3000
            export let counter = 0
            export var legacy = true
        """)
        result = extract_exports(p)
        self.assertIn("PORT", result.exports)
        self.assertIn("counter", result.exports)
        self.assertIn("legacy", result.exports)

    def test_class_export(self):
        p = self._write("""\
            export class UserService {
              async getUser(id: string) {}
            }
        """)
        result = extract_exports(p)
        self.assertEqual(result.exports, ["UserService"])

    def test_type_interface_enum_exports(self):
        p = self._write("""\
            export type UserId = string
            export interface UserConfig { name: string }
            export enum Status { Active, Inactive }
        """)
        result = extract_exports(p)
        self.assertIn("UserId", result.exports)
        self.assertIn("UserConfig", result.exports)
        self.assertIn("Status", result.exports)

    def test_brace_export(self):
        p = self._write("""\
            const a = 1
            const b = 2
            export { a, b }
        """)
        result = extract_exports(p)
        self.assertIn("a", result.exports)
        self.assertIn("b", result.exports)

    def test_brace_reexport_from_module(self):
        p = self._write("""\
            export { createUser, deleteUser } from './users'
        """)
        result = extract_exports(p)
        self.assertIn("createUser", result.exports)
        self.assertIn("deleteUser", result.exports)

    def test_brace_export_with_alias(self):
        p = self._write("""\
            export { internalFn as publicFn } from './internal'
        """)
        result = extract_exports(p)
        self.assertIn("publicFn", result.exports)
        self.assertNotIn("internalFn", result.exports)

    def test_type_reexport(self):
        p = self._write("""\
            export { type UserType } from './types'
        """)
        result = extract_exports(p)
        self.assertIn("UserType", result.exports)

    def test_default_export(self):
        p = self._write("""\
            export default function main() {}
        """)
        result = extract_exports(p)
        self.assertTrue(result.has_default)
        self.assertIn("default", result.exports)

    def test_star_reexport_flagged(self):
        p = self._write("""\
            export * from './models'
            export * from './utils'
        """)
        result = extract_exports(p)
        self.assertEqual(result.star_reexports, ["./models", "./utils"])
        # Star re-exports don't produce named exports.
        self.assertEqual([e for e in result.exports if e != "default"], [])

    def test_star_as_namespace_reexport(self):
        p = self._write("""\
            export * as schema from './schema'
        """)
        result = extract_exports(p)
        self.assertIn("schema", result.exports)
        self.assertEqual(result.star_reexports, [])

    def test_mixed_exports(self):
        """Real-world barrel file with a mix of export styles."""
        p = self._write("""\
            export { db, connection } from './connection'
            export { usersTable } from './schema/users'
            export type { DrizzleTx } from './types'
            export const DB_VERSION = 1
        """)
        result = extract_exports(p)
        self.assertIn("db", result.exports)
        self.assertIn("connection", result.exports)
        self.assertIn("usersTable", result.exports)
        self.assertIn("DrizzleTx", result.exports)
        self.assertIn("DB_VERSION", result.exports)

    def test_comments_ignored(self):
        p = self._write("""\
            // export function notReal() {}
            /* export const alsoNot = 1 */
            export function real() {}
        """)
        result = extract_exports(p)
        self.assertEqual(result.exports, ["real"])

    def test_nonexistent_file(self):
        result = extract_exports(Path("/does/not/exist.ts"))
        self.assertEqual(result.exports, [])

    def test_empty_file(self):
        p = self._write("")
        result = extract_exports(p)
        self.assertEqual(result.exports, [])

    def test_no_duplicates(self):
        p = self._write("""\
            export function foo() {}
            export { foo }
        """)
        result = extract_exports(p)
        self.assertEqual(result.exports.count("foo"), 1)

    def test_generator_function(self):
        p = self._write("""\
            export function* generateIds() { yield 1 }
        """)
        result = extract_exports(p)
        self.assertIn("generateIds", result.exports)


class TestVerifyExportContract(unittest.TestCase):
    """Test contract verification between manifest and actual code."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.project = Path(self.tmpdir)

    def _write_file(self, rel_path: str, content: str) -> None:
        p = self.project / rel_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(dedent(content))

    def test_no_violations_when_exports_match(self):
        self._write_file("src/server.ts", """\
            export function createServer() {}
            export function startServer() {}
        """)
        eps = [EntryPoint(
            path="src/server.ts",
            specifier="../../../src/server.js",
            exports=["createServer", "startServer"],
        )]
        violations = verify_export_contract(eps, self.project)
        self.assertEqual(violations, [])

    def test_violation_when_export_missing(self):
        self._write_file("src/server.ts", """\
            export function startServer() {}
        """)
        eps = [EntryPoint(
            path="src/server.ts",
            specifier="../../../src/server.js",
            exports=["createServer", "startServer"],
        )]
        violations = verify_export_contract(eps, self.project)
        self.assertEqual(len(violations), 1)
        self.assertIn("createServer", violations[0].missing)

    def test_violation_when_export_renamed(self):
        self._write_file("src/server.ts", """\
            export function buildServer() {}
        """)
        eps = [EntryPoint(
            path="src/server.ts",
            specifier="../../../src/server.js",
            exports=["createServer"],
        )]
        violations = verify_export_contract(eps, self.project)
        self.assertEqual(len(violations), 1)
        self.assertIn("createServer", violations[0].missing)
        self.assertIn("buildServer", violations[0].unexpected)

    def test_empty_declared_exports_skipped(self):
        """Entry points with empty declared exports are skipped (Architect didn't predict)."""
        self._write_file("src/index.ts", """\
            export { foo } from './foo'
        """)
        eps = [EntryPoint(path="src/index.ts", exports=[])]
        violations = verify_export_contract(eps, self.project)
        self.assertEqual(violations, [])

    def test_star_reexport_with_unexpected_exports_flagged(self):
        """Star re-exports don't suppress violations from unexpected exports."""
        self._write_file("src/index.ts", """\
            export * from './models'
            export function extraHelper() {}
        """)
        eps = [EntryPoint(
            path="src/index.ts",
            exports=["User"],
        )]
        violations = verify_export_contract(eps, self.project)
        self.assertEqual(len(violations), 1)
        self.assertIn("User", violations[0].missing)
        self.assertIn("extraHelper", violations[0].unexpected)
        self.assertIn("./models", violations[0].star_reexports)

    def test_missing_file_reports_violation(self):
        eps = [EntryPoint(
            path="src/nonexistent.ts",
            exports=["foo"],
        )]
        violations = verify_export_contract(eps, self.project)
        self.assertEqual(len(violations), 1)
        self.assertIn("foo", violations[0].missing)

    def test_star_reexport_advisory(self):
        """Files with export * have star_reexports noted on the violation."""
        self._write_file("src/index.ts", """\
            export * from './models'
        """)
        eps = [EntryPoint(
            path="src/index.ts",
            exports=["User", "Post"],
        )]
        violations = verify_export_contract(eps, self.project)
        self.assertEqual(len(violations), 1)
        self.assertIn("./models", violations[0].star_reexports)

    def test_multiple_entry_points(self):
        self._write_file("src/a.ts", "export function foo() {}")
        self._write_file("src/b.ts", "export function bar() {}")
        eps = [
            EntryPoint(path="src/a.ts", exports=["foo"]),
            EntryPoint(path="src/b.ts", exports=["baz"]),  # mismatch
        ]
        violations = verify_export_contract(eps, self.project)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].path, "src/b.ts")


class TestFormatViolations(unittest.TestCase):

    def test_empty_violations(self):
        self.assertEqual(format_violations([]), "")

    def test_formats_missing_exports(self):
        v = ContractViolation(
            entry_point_idx=0,
            path="src/server.ts",
            declared=["createServer"],
            actual=["buildServer"],
            missing=["createServer"],
            unexpected=["buildServer"],
            star_reexports=[],
        )
        text = format_violations([v])
        self.assertIn("src/server.ts", text)
        self.assertIn("createServer", text)
        self.assertIn("buildServer", text)
        self.assertIn("Missing exports", text)
        self.assertIn("Unexpected exports", text)

    def test_formats_star_reexport_note(self):
        v = ContractViolation(
            entry_point_idx=0,
            path="src/index.ts",
            declared=["User"],
            actual=[],
            missing=["User"],
            unexpected=[],
            star_reexports=["./models"],
        )
        text = format_violations([v])
        self.assertIn("export *", text)
        self.assertIn("./models", text)


if __name__ == "__main__":
    unittest.main()
