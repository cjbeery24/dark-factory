"""Tests for ``dark-factory contracts verify <adapter>`` (Phase 4
Contracts CLI v1).

Three surfaces:

1. **Registry shape** (``silent_wrong_registry`` invariants):
   frozen, kebab-case-or-camelCase as appropriate, every guard
   pattern compiles, every foot-gun ban references an existing B5
   library entry, ``HELPER_DECL_RE`` accepts substitution.

2. **Verify behavior** (``verify_adapter_helpers`` happy path +
   regression detection): the as-shipped postgres starters pass
   verify; a synthetic helper with a removed guard fails; a
   synthetic helper containing a foot-gun fails; missing helpers are
   silently skipped (logged, not violated); contracts dir absence
   raises ValueError.

3. **CLI** (end-to-end): postgres init + verify passes; tampered
   helper file fails verify with exit 1 and a readable headline;
   unknown adapter exits 1 with stderr.
"""
from __future__ import annotations

import io
import re
import shutil
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.helpers import TempDirTestCase

from darkfactory.cli import main
from darkfactory.contracts.foot_gun_patterns import BY_NAME_AND_DIALECT
from darkfactory.contracts.silent_wrong_registry import (
    FOOT_GUN_BANS,
    GUARDS,
    HELPER_DECL_RE,
    HelperFootGunBan,
    HelperGuard,
    adapters as registered_adapters,
)
from darkfactory.contracts.verify import (
    Violation,
    VerifyResult,
    verify_adapter_helpers,
)


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------
# Registry shape tests
# ---------------------------------------------------------------------


class TestRegistryShape(unittest.TestCase):
    """Invariants on GUARDS + FOOT_GUN_BANS + HELPER_DECL_RE."""

    def test_helper_decl_re_substitutes_and_compiles(self) -> None:
        # The constant carries a {name} sentinel; .format(name=...)
        # produces a regex that matches `export async function NAME`.
        rendered = HELPER_DECL_RE.format(name=re.escape("assertFoo"))
        compiled = re.compile(rendered)
        self.assertTrue(compiled.search(
            "export async function assertFoo(client: Sql) {"
        ))
        self.assertFalse(compiled.search(
            "// note: assertFoo is exported elsewhere"
        ))

    def test_helper_decl_re_escapes_special_chars(self) -> None:
        # If a helper name ever contains regex metacharacters (it
        # shouldn't — TS identifiers are alphanumeric — but defense
        # in depth), the .format(name=re.escape(...)) shape handles
        # them.
        rendered = HELPER_DECL_RE.format(name=re.escape("assert.Foo"))
        # Period gets escaped, so the regex doesn't match arbitrary chars.
        compiled = re.compile(rendered)
        self.assertFalse(compiled.search(
            "export async function assertXFoo() {}"
        ))

    def test_every_guard_is_frozen_helper_guard(self) -> None:
        for adapter, guards in GUARDS.items():
            for guard in guards:
                with self.subTest(adapter=adapter, helper=guard.helper_name):
                    self.assertIsInstance(guard, HelperGuard)
                    self.assertIsInstance(guard.pattern, re.Pattern)
                    self.assertGreater(len(guard.helper_name), 0)
                    self.assertGreater(len(guard.reason.strip()), 0)

    def test_every_ban_is_frozen_helper_foot_gun_ban(self) -> None:
        for adapter, bans in FOOT_GUN_BANS.items():
            for ban in bans:
                with self.subTest(adapter=adapter, ban=ban.b5_name):
                    self.assertIsInstance(ban, HelperFootGunBan)
                    self.assertIn(ban.b5_dialect, ("ts", "sql", "py"))
                    self.assertGreater(len(ban.b5_name), 0)
                    self.assertGreater(len(ban.reason.strip()), 0)

    def test_every_ban_references_existing_b5_pattern(self) -> None:
        """Each FOOT_GUN_BANS entry must point at a real entry in
        the B5 library — otherwise the verifier raises ValueError at
        runtime when consulting the missing pattern. Pin here so the
        registry-runtime drift is caught at PR time."""
        for adapter, bans in FOOT_GUN_BANS.items():
            for ban in bans:
                with self.subTest(adapter=adapter, ban=ban.b5_name):
                    self.assertIn(
                        (ban.b5_name, ban.b5_dialect),
                        BY_NAME_AND_DIALECT,
                        f"FOOT_GUN_BANS[{adapter!r}] references missing "
                        f"B5 pattern ({ban.b5_name!r}, {ban.b5_dialect!r}).",
                    )

    def test_postgres_adapter_is_registered(self) -> None:
        self.assertIn("postgres", registered_adapters())


# ---------------------------------------------------------------------
# verify_adapter_helpers behavior tests
# ---------------------------------------------------------------------


def _seed_postgres_contracts(project_dir: Path) -> Path:
    """Copy the postgres starter helpers into a fresh project's
    contracts dir. Returns the contracts dir path."""
    src = (_REPO_ROOT / "darkfactory" / "contracts" / "templates"
           / "postgres" / "project-starters")
    dst = project_dir / ".dark-factory" / "contracts" / "postgres"
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*"):
        if f.is_file():
            shutil.copy(f, dst / f.name)
    return dst


class TestVerifyHappyPath(TempDirTestCase):
    """The as-shipped postgres starters pass verify."""

    def test_clean_starters_pass(self) -> None:
        _seed_postgres_contracts(self.tmp_dir)
        result = verify_adapter_helpers("postgres", self.tmp_dir)
        self.assertEqual(
            result.violations, [],
            f"unexpected violations: {result.violations!r}",
        )
        self.assertGreater(len(result.helpers_checked), 0)
        self.assertEqual(result.helpers_skipped, [])

    def test_result_carries_correct_paths_and_counts(self) -> None:
        contracts_dir = _seed_postgres_contracts(self.tmp_dir)
        result = verify_adapter_helpers("postgres", self.tmp_dir)
        self.assertEqual(result.adapter, "postgres")
        self.assertEqual(result.contracts_dir, contracts_dir)
        self.assertEqual(len(result.files_scanned), 3)


class TestVerifyDetectsRemovedGuard(TempDirTestCase):
    """A synthetic helper with the safety guard removed must produce
    a missing_guard violation."""

    def test_removing_expected_empty_guard_fails(self) -> None:
        contracts_dir = _seed_postgres_contracts(self.tmp_dir)
        # Read the starter, strip the guard, write it back.
        app_tx_guc = contracts_dir / "app_tx_guc.ts"
        src = app_tx_guc.read_text()
        # Remove the if-block carrying the `expected === ''` guard.
        new_src = re.sub(
            r"if\s*\(\s*expected\s*===\s*''\s*\)\s*\{[^}]*\}",
            "// guard removed by malicious developer",
            src,
            count=1,
        )
        self.assertNotEqual(new_src, src, "regex didn't strip the guard")
        app_tx_guc.write_text(new_src)

        result = verify_adapter_helpers("postgres", self.tmp_dir)
        self.assertEqual(len(result.violations), 1)
        v = result.violations[0]
        self.assertEqual(v.kind, "missing_guard")
        self.assertEqual(v.helper_or_file, "assertAppSetsTxGuc")
        self.assertIn("expected", v.reason)

    def test_removing_client_begin_fails(self) -> None:
        contracts_dir = _seed_postgres_contracts(self.tmp_dir)
        isolation = contracts_dir / "isolation.ts"
        src = isolation.read_text()
        # Replace `await client.begin(async (` with a peer-client form.
        new_src = src.replace(
            "await client.begin(async (tx) => {",
            "// converted to peer client (BAD): {",
        )
        self.assertNotEqual(new_src, src)
        isolation.write_text(new_src)

        result = verify_adapter_helpers("postgres", self.tmp_dir)
        # Both isolation helpers share the file; both lose the guard
        # → 2 missing_guard violations.
        missing = [v for v in result.violations if v.kind == "missing_guard"]
        self.assertEqual(len(missing), 2)


class TestVerifyDetectsFootGunInHelper(TempDirTestCase):
    """A helper file that contains a B5 foot-gun pattern produces a
    foot_gun_in_helper violation."""

    def test_parameterized_set_local_in_helper_fails(self) -> None:
        contracts_dir = _seed_postgres_contracts(self.tmp_dir)
        # Write a fresh file containing the foot-gun.
        bad_helper = contracts_dir / "bad_helper.ts"
        bad_helper.write_text("""\
import type { Sql } from 'postgres';

export async function assertSomething(client: Sql, gucName: string, value: string) {
  await client.begin(async (tx) => {
    await tx`SET LOCAL ${gucName} = '${value}'`;
  });
}
""")

        result = verify_adapter_helpers("postgres", self.tmp_dir)
        foot_guns = [
            v for v in result.violations if v.kind == "foot_gun_in_helper"
        ]
        self.assertGreaterEqual(len(foot_guns), 1)
        self.assertTrue(any(
            "parameterized-set-local" in v.helper_or_file
            for v in foot_guns
        ))


class TestVerifySkipsAbsentHelpers(TempDirTestCase):
    """Helpers in the registry that the project doesn't declare are
    skipped silently (logged, not violated)."""

    def test_empty_contracts_dir_skips_all(self) -> None:
        # Just create the contracts dir; no helpers inside.
        contracts_dir = self.tmp_dir / ".dark-factory" / "contracts" / "postgres"
        contracts_dir.mkdir(parents=True)

        result = verify_adapter_helpers("postgres", self.tmp_dir)
        self.assertEqual(
            len(result.helpers_skipped),
            len(GUARDS["postgres"]),
        )
        # No violations from missing helpers.
        missing = [v for v in result.violations if v.kind == "missing_guard"]
        self.assertEqual(missing, [])


class TestVerifyErrorPaths(TempDirTestCase):
    def test_unknown_adapter_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown adapter"):
            verify_adapter_helpers("not-a-real-adapter", self.tmp_dir)

    def test_missing_contracts_dir_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "contracts dir does not exist"):
            verify_adapter_helpers("postgres", self.tmp_dir)


# ---------------------------------------------------------------------
# CLI end-to-end tests
# ---------------------------------------------------------------------


class ContractsVerifyCLITestCase(TempDirTestCase):
    def run_cli(self, *args: str) -> tuple[int, str, str]:
        full = ["--project-root", str(self.tmp_dir), *args]
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with redirect_stdout(out), redirect_stderr(err):
                main(full)
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
        return code, out.getvalue(), err.getvalue()


class TestVerifyCLI(ContractsVerifyCLITestCase):
    def test_verify_postgres_clean_passes_exit_0(self) -> None:
        _seed_postgres_contracts(self.tmp_dir)
        code, out, err = self.run_cli("contracts", "verify", "postgres")
        self.assertEqual(code, 0, msg=f"stderr={err!r}")
        self.assertIn("Verify PASSED", out)

    def test_verify_tampered_helper_fails_exit_1(self) -> None:
        contracts_dir = _seed_postgres_contracts(self.tmp_dir)
        # Strip the guard.
        app = contracts_dir / "app_tx_guc.ts"
        app.write_text(re.sub(
            r"if\s*\(\s*expected\s*===\s*''\s*\)\s*\{[^}]*\}",
            "// removed",
            app.read_text(),
            count=1,
        ))
        code, out, _ = self.run_cli("contracts", "verify", "postgres")
        self.assertEqual(code, 1)
        self.assertIn("Verify FAILED", out)
        self.assertIn("assertAppSetsTxGuc", out)

    def test_verify_unknown_adapter_exit_1(self) -> None:
        code, _, err = self.run_cli("contracts", "verify", "no-such-adapter")
        self.assertEqual(code, 1)
        self.assertIn("contracts verify failed", err)
        self.assertIn("unknown adapter", err)

    def test_verify_no_contracts_dir_exit_1(self) -> None:
        code, _, err = self.run_cli("contracts", "verify", "postgres")
        self.assertEqual(code, 1)
        self.assertIn("contracts dir does not exist", err)

    def test_contracts_no_subcommand_lists_verify(self) -> None:
        code, _, err = self.run_cli("contracts")
        self.assertEqual(code, 1)
        self.assertIn("verify", err)


if __name__ == "__main__":
    unittest.main()
