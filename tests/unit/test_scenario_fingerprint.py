"""Unit tests for Phase 6 I2 scenario fingerprint extraction."""

from __future__ import annotations

import unittest
from pathlib import Path

from darkfactory.adapters.base import ImportInfo
from darkfactory.scenarios.fingerprint import (
    compute_scenario_fingerprint,
    iter_scenario_files,
)
from tests.helpers import TempDirTestCase


class _StubAdapter:
    """Minimal stand-in that returns a fixed ImportInfo list."""

    def __init__(self, imports: list[ImportInfo]):
        self._imports = imports

    def parse_test_imports(self, file_path: Path) -> list[ImportInfo]:
        return list(self._imports)


class _NoopAdapter:
    def parse_test_imports(self, file_path: Path) -> list[ImportInfo]:
        return []


class TestComputeScenarioFingerprint(TempDirTestCase):

    def _write(self, name: str, body: str) -> Path:
        p = Path(self.tmp_dir) / name
        p.write_text(body, encoding="utf-8")
        return p

    def test_vitest_style_file_yields_stable_structure(self) -> None:
        path = self._write(
            "login.test.ts",
            """
import { describe, it, expect } from 'vitest';
import { login } from '@app/auth';

describe('login', () => {
  it('[AC-1] returns a token', async () => {
    const t = await login('a', 'b');
    expect(t).toBeDefined();
    expect(t.length).toBeGreaterThan(0);
  });
  it('[EC-2] rejects bad creds', async () => {
    await expect(login('x', 'y')).rejects.toThrow();
  });
});
""".strip(),
        )
        adapter = _StubAdapter([
            ImportInfo(specifier="vitest", symbols=["describe", "it", "expect"]),
            ImportInfo(specifier="@app/auth", symbols=["login"]),
        ])
        fp = compute_scenario_fingerprint(path, [adapter])
        self.assertEqual(fp["scenario_path"], str(path))
        self.assertEqual(fp["import_set"], ["@app/auth", "vitest"])
        self.assertIn("@app/auth::login", fp["symbol_set"])
        self.assertIn("vitest::expect", fp["symbol_set"])
        # 3 expect(...) + 1 describe + 2 it blocks.
        self.assertGreaterEqual(fp["assertion_count"], 3)
        self.assertEqual(
            fp["test_names"],
            sorted(["[AC-1] returns a token", "[EC-2] rejects bad creds", "login"]),
        )

    def test_prose_drift_does_not_change_fingerprint(self) -> None:
        """Comment/assertion-message drift must not affect the fingerprint."""
        base = """
import { it, expect } from 'vitest';
it('[AC-1] works', () => {
  expect(1).toBe(1);
});
""".strip()
        drift = """
import { it, expect } from 'vitest';
// Updated wording after review.
it('[AC-1] works', () => {
  // This is now clearer.
  expect(1).toBe(1);
});
""".strip()
        p_base = self._write("a.test.ts", base)
        p_drift = self._write("b.test.ts", drift)
        adapter = _StubAdapter(
            [ImportInfo(specifier="vitest", symbols=["it", "expect"])]
        )
        fp_base = compute_scenario_fingerprint(p_base, [adapter])
        fp_drift = compute_scenario_fingerprint(p_drift, [adapter])
        # Ignore scenario_path — they differ by filename.
        for key in ("import_set", "symbol_set", "assertion_count", "test_names"):
            self.assertEqual(fp_base[key], fp_drift[key], key)

    def test_added_assertion_changes_fingerprint(self) -> None:
        """A new expect(...) raises assertion_count — this is the signal."""
        a = """
import { it, expect } from 'vitest';
it('[AC-1] one', () => { expect(1).toBe(1); });
""".strip()
        b = """
import { it, expect } from 'vitest';
it('[AC-1] one', () => { expect(1).toBe(1); expect(2).toBe(2); });
""".strip()
        p_a = self._write("x.test.ts", a)
        p_b = self._write("y.test.ts", b)
        adapter = _StubAdapter(
            [ImportInfo(specifier="vitest", symbols=["it", "expect"])]
        )
        fp_a = compute_scenario_fingerprint(p_a, [adapter])
        fp_b = compute_scenario_fingerprint(p_b, [adapter])
        self.assertLess(fp_a["assertion_count"], fp_b["assertion_count"])

    def test_pytest_test_names_extracted(self) -> None:
        path = self._write(
            "test_login.py",
            """
import pytest

def test_returns_token():
    assert login('a', 'b') is not None

def test_rejects_bad_creds():
    assert login('x', 'y') is None
""".strip(),
        )
        fp = compute_scenario_fingerprint(path, [_NoopAdapter()])
        self.assertEqual(
            fp["test_names"],
            sorted(["test_returns_token", "test_rejects_bad_creds"]),
        )
        self.assertEqual(fp["assertion_count"], 2)

    def test_missing_file_returns_empty_fingerprint(self) -> None:
        missing = Path(self.tmp_dir) / "nope.test.ts"
        fp = compute_scenario_fingerprint(missing, [_NoopAdapter()])
        self.assertEqual(fp["scenario_path"], str(missing))
        self.assertEqual(fp["import_set"], [])
        self.assertEqual(fp["symbol_set"], [])
        self.assertEqual(fp["assertion_count"], 0)
        self.assertEqual(fp["test_names"], [])

    def test_first_adapter_with_imports_wins(self) -> None:
        path = self._write(
            "c.test.ts",
            """
import { it } from 'vitest';
it('x', () => {});
""".strip(),
        )
        first = _NoopAdapter()
        second = _StubAdapter(
            [ImportInfo(specifier="vitest", symbols=["it"])]
        )
        fp = compute_scenario_fingerprint(path, [first, second])
        self.assertEqual(fp["import_set"], ["vitest"])


class TestIterScenarioFiles(TempDirTestCase):

    def test_excludes_contracts_directory(self) -> None:
        d = Path(self.tmp_dir) / "scenarios"
        d.mkdir()
        (d / "a.test.ts").write_text("//", encoding="utf-8")
        (d / "_contracts").mkdir()
        (d / "_contracts" / "helper.test.ts").write_text("//", encoding="utf-8")
        files = iter_scenario_files(d)
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].name, "a.test.ts")

    def test_missing_dir_returns_empty(self) -> None:
        self.assertEqual(iter_scenario_files(Path(self.tmp_dir) / "nope"), [])

    def test_sorted_order(self) -> None:
        d = Path(self.tmp_dir) / "scenarios"
        d.mkdir()
        (d / "b.test.ts").write_text("//", encoding="utf-8")
        (d / "a.test.ts").write_text("//", encoding="utf-8")
        files = iter_scenario_files(d)
        names = [p.name for p in files]
        self.assertEqual(names, sorted(names))


if __name__ == "__main__":
    unittest.main()
