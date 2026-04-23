"""Tests for scripts/lib/archive_audit_gate.py.

The gate is an offline maintainer tool that stops a destructive
contract refactor from landing if any archived scenario still
references the removed symbols. Its correctness matters because a
false PASS would let the refactor land and silently invalidate every
scenario that cited the old helper.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


def _load_module() -> Any:
    spec_path = (
        Path(__file__).resolve().parent.parent.parent
        / "scripts" / "lib" / "archive_audit_gate.py"
    )
    spec = importlib.util.spec_from_file_location("archive_audit_gate", spec_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["archive_audit_gate"] = mod
    spec.loader.exec_module(mod)
    return mod


gate = _load_module()


class TestRemovedSymbolParse(unittest.TestCase):
    def test_parses_simple(self) -> None:
        sym = gate.RemovedSymbol.parse("oldName:removed in 2026-04")
        self.assertEqual(sym.name, "oldName")
        self.assertEqual(sym.reason, "removed in 2026-04")

    def test_strips_surrounding_whitespace(self) -> None:
        sym = gate.RemovedSymbol.parse("  oldName  :   some reason   ")
        self.assertEqual(sym.name, "oldName")
        self.assertEqual(sym.reason, "some reason")

    def test_keeps_colons_in_reason(self) -> None:
        # Reasons often cite URLs or timestamps that themselves contain colons.
        sym = gate.RemovedSymbol.parse("X:see https://x.test:443/y at 10:30")
        self.assertEqual(sym.name, "X")
        self.assertEqual(sym.reason, "see https://x.test:443/y at 10:30")

    def test_rejects_missing_colon(self) -> None:
        import argparse
        with self.assertRaises(argparse.ArgumentTypeError):
            gate.RemovedSymbol.parse("oldName")

    def test_rejects_empty_name(self) -> None:
        import argparse
        with self.assertRaises(argparse.ArgumentTypeError):
            gate.RemovedSymbol.parse(":reason only")

    def test_rejects_empty_reason(self) -> None:
        import argparse
        with self.assertRaises(argparse.ArgumentTypeError):
            gate.RemovedSymbol.parse("name only:")


class TestScanFile(unittest.TestCase):
    def _write(self, content: str, suffix: str = ".test.ts") -> Path:
        # Use NamedTemporaryFile path but manage lifetime via the parent
        # tmpdir in each test method.
        path = Path(self._tmpdir.name) / f"scenario{suffix}"
        path.write_text(content)
        return path

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_matches_direct_import(self) -> None:
        sym = gate.RemovedSymbol(name="assertTxGucEquals", reason="split")
        path = self._write(
            "import { assertTxGucEquals } from './_contracts/postgres';\n"
            "assertTxGucEquals(client, 'app.x', 'v', cb);\n"
        )
        hits = gate.scan_file(path, [sym])
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].line, 1)
        self.assertEqual(hits[1].line, 2)

    def test_word_boundary_no_false_prefix(self) -> None:
        # A helper that *contains* the removed name as a substring is
        # not a hit. ``assertTxGucEqualsButDifferent`` is its own symbol.
        sym = gate.RemovedSymbol(name="assertTxGucEquals", reason="split")
        path = self._write("assertTxGucEqualsButDifferent(client, 'a');\n")
        self.assertEqual(gate.scan_file(path, [sym]), [])

    def test_word_boundary_no_false_suffix(self) -> None:
        sym = gate.RemovedSymbol(name="Equals", reason="test")
        path = self._write("xyEquals(1);\n")
        self.assertEqual(gate.scan_file(path, [sym]), [])

    def test_multiple_symbols_each_counted_once_per_line(self) -> None:
        # One hit per (symbol, line) — repeat occurrences on the same
        # line are deduped so the report stays skimmable. The operator
        # only needs the file:line pointer to go fix it; two separate
        # hits on the same line would not change the remediation path.
        syms = [
            gate.RemovedSymbol(name="oldA", reason="a"),
            gate.RemovedSymbol(name="oldB", reason="b"),
        ]
        path = self._write("oldA(); oldB(); oldA();\n")
        hits = gate.scan_file(path, syms)
        self.assertEqual(len(hits), 2)
        self.assertEqual({h.symbol.name for h in hits}, {"oldA", "oldB"})

    def test_unreadable_file_returns_empty(self) -> None:
        # Non-existent file (or permission denied) returns empty rather
        # than crashing the scan. The gate is best-effort; a file it
        # cannot read cannot break its caller.
        sym = gate.RemovedSymbol(name="x", reason="y")
        hits = gate.scan_file(Path("/nonexistent/path"), [sym])
        self.assertEqual(hits, [])


class TestScanArchive(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _touch(self, relpath: str, content: str) -> None:
        p = self.root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def test_nonexistent_root_returns_empty(self) -> None:
        hits = gate.scan_archive(
            Path("/nonexistent"),
            [gate.RemovedSymbol(name="x", reason="y")],
        )
        self.assertEqual(hits, [])

    def test_recursive_scan_across_trials(self) -> None:
        sym = gate.RemovedSymbol(name="oldName", reason="r")
        self._touch(
            "trial-a/iter-1/scenarios/foo/a.test.ts",
            "oldName(1);\n",
        )
        self._touch(
            "trial-b/iter-2/scenarios/bar/b.test.ts",
            "other(); oldName(2);\n",
        )
        hits = gate.scan_archive(self.root, [sym])
        self.assertEqual(len(hits), 2)
        # Stable ordering (symbol, file, line).
        self.assertEqual(
            [str(h.file.relative_to(self.root)) for h in hits],
            [
                "trial-a/iter-1/scenarios/foo/a.test.ts",
                "trial-b/iter-2/scenarios/bar/b.test.ts",
            ],
        )

    def test_non_scenario_files_skipped(self) -> None:
        sym = gate.RemovedSymbol(name="oldName", reason="r")
        # Not a .test.ts / .test.js / .spec.ts / .spec.js — skipped.
        self._touch("trial/iter/reports/run.md", "oldName appears here\n")
        self.assertEqual(gate.scan_archive(self.root, [sym]), [])

    def test_spec_glob_covered(self) -> None:
        sym = gate.RemovedSymbol(name="oldName", reason="r")
        self._touch("trial/iter/scenarios/x.spec.ts", "oldName(1);\n")
        self._touch("trial/iter/scenarios/y.spec.js", "oldName(2);\n")
        hits = gate.scan_archive(self.root, [sym])
        self.assertEqual(len(hits), 2)

    def test_duplicate_path_counted_once(self) -> None:
        # If a file matches multiple globs (e.g. two patterns that both
        # overlap), it must still be scanned exactly once. Multi-glob
        # iteration is the only place ``seen`` is actively used.
        sym = gate.RemovedSymbol(name="oldName", reason="r")
        self._touch("trial/iter/scenarios/x.test.ts", "oldName(1);\n")
        hits = gate.scan_archive(
            self.root, [sym],
            globs=("*.test.ts", "*.ts"),  # both will match the file
        )
        self.assertEqual(len(hits), 1)


class TestFormatReport(unittest.TestCase):
    def test_clean_path_names_symbols(self) -> None:
        syms = [
            gate.RemovedSymbol(name="A", reason="a"),
            gate.RemovedSymbol(name="B", reason="b"),
        ]
        out = gate.format_report([], syms, Path("/archives"))
        self.assertIn("PASS", out)
        self.assertIn("[A, B]", out)

    def test_report_groups_by_symbol(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        try:
            syms = [gate.RemovedSymbol(name="oldName", reason="has a reason")]
            hits = [
                gate.Hit(
                    file=tmp / "t" / "a.test.ts", line=3,
                    excerpt="oldName();", symbol=syms[0],
                ),
                gate.Hit(
                    file=tmp / "t" / "b.test.ts", line=9,
                    excerpt="oldName(x);", symbol=syms[0],
                ),
            ]
            out = gate.format_report(hits, syms, tmp)
            self.assertIn("FAIL", out)
            self.assertIn("2 reference(s) across 2 file(s)", out)
            self.assertIn("## oldName (2 reference(s))", out)
            self.assertIn("has a reason", out)
            self.assertIn("t/a.test.ts:3", out)
            self.assertIn("t/b.test.ts:9", out)
        finally:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)


class TestCLIMain(unittest.TestCase):
    """End-to-end CLI exercise.

    The gate is invoked by maintainers via a shell command; rendering
    argparse + exit codes wrong would make it unusable. These tests
    drive ``main()`` directly with an argv list.
    """

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def _touch(self, relpath: str, content: str) -> None:
        p = self.root / relpath
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    def test_exit_zero_when_no_matches(self) -> None:
        self._touch("trial/scenarios/a.test.ts", "freshCode();\n")
        rc = gate.main([
            "--archive-root", str(self.root),
            "--removed-symbol", "oldName:Track-A split",
        ])
        self.assertEqual(rc, 0)

    def test_exit_one_when_match_found(self) -> None:
        self._touch("trial/scenarios/a.test.ts", "oldName();\n")
        rc = gate.main([
            "--archive-root", str(self.root),
            "--removed-symbol", "oldName:Track-A split",
        ])
        self.assertEqual(rc, 1)

    def test_exit_two_when_root_missing(self) -> None:
        rc = gate.main([
            "--archive-root", "/definitely/not/here",
            "--removed-symbol", "oldName:reason",
        ])
        self.assertEqual(rc, 2)

    def test_supports_multiple_removed_symbols(self) -> None:
        self._touch("trial/scenarios/a.test.ts", "oldA(); oldB();\n")
        rc = gate.main([
            "--archive-root", str(self.root),
            "--removed-symbol", "oldA:r1",
            "--removed-symbol", "oldB:r2",
        ])
        self.assertEqual(rc, 1)

    def test_custom_file_glob(self) -> None:
        # The default globs do not include ``.py``; a Python scenario
        # archive needs --file-glob.
        self._touch("trial/test_x.py", "old_name()\n")
        rc_default = gate.main([
            "--archive-root", str(self.root),
            "--removed-symbol", "old_name:r",
        ])
        self.assertEqual(rc_default, 0)  # .py not in default globs
        rc_custom = gate.main([
            "--archive-root", str(self.root),
            "--removed-symbol", "old_name:r",
            "--file-glob", "test_*.py",
        ])
        self.assertEqual(rc_custom, 1)


if __name__ == "__main__":
    unittest.main()
