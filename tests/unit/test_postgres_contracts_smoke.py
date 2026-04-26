"""Smoke tests for the Postgres wire-contract helpers module.

These tests guard the static shape of
``darkfactory/adapters/postgres/contracts/ts/`` — the directory the
orchestrator stages into ``.dark-factory/scenarios/<spec>/_contracts/postgres/``
before the scenario-architect runs. No ``.test.ts`` files ship alongside
the helpers (would inflate vitest runs in every pipeline); instead we
assert structural invariants from Python:

1. Required files exist.
2. ``package.json`` is valid JSON and declares ``type: "module"``.
3. ``index.ts`` re-exports every helper name the plan pins.
4. When ``tsc`` is available locally, ``tsc --noEmit`` exits 0.

The ``tsc`` check is skipped when the binary is not on ``PATH`` — dark
factory pipeline containers install it; developer laptops may not.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import unittest
from pathlib import Path

CONTRACTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "darkfactory"
    / "adapters"
    / "postgres"
    / "contracts"
    / "ts"
)

# Universal wire-contract helpers shipped by the framework. The three
# convention helpers (assertAppSetsTxGuc, assertTenantSeesOwnRows,
# assertTenantCannotSeeOtherRows) were deleted from the framework in
# B9a v(N+1); they now ship as project starters under
# `darkfactory/contracts/templates/postgres/project-starters/` and are
# bootstrapped into projects via `dark-factory contracts init postgres`.
# `TestFrameworkDoesNotShipConventionHelpers` below pins their absence.
EXPECTED_HELPERS = (
    "assertUnsetGucIsNullish",
    "assertTxGucRoundTrips",
    "assertRlsEnabled",
)

# Helper names that v(N+1) deleted from the framework. Pinned by the
# absence-test class so a regression that re-introduces any of them
# fails CI immediately.
DELETED_CONVENTION_HELPERS = (
    "assertAppSetsTxGuc",
    "assertTenantSeesOwnRows",
    "assertTenantCannotSeeOtherRows",
)


class TestPostgresContractsLayout(unittest.TestCase):
    def test_required_files_exist(self) -> None:
        for name in (
            "package.json",
            "tsconfig.json",
            "index.ts",
            "guc.ts",
            "tx_guc.ts",
            "rls.ts",
            "README.md",
        ):
            with self.subTest(file=name):
                self.assertTrue(
                    (CONTRACTS_DIR / name).is_file(),
                    f"{name} missing from {CONTRACTS_DIR}",
                )

    def test_isolation_ts_was_deleted(self) -> None:
        """B9a v(N+1) deleted ``isolation.ts`` (both helpers in it
        were convention-only). The starter copy lives at
        ``darkfactory/contracts/templates/postgres/project-starters/
        isolation.ts``."""
        self.assertFalse(
            (CONTRACTS_DIR / "isolation.ts").exists(),
            f"{CONTRACTS_DIR / 'isolation.ts'} should NOT exist after "
            "B9a v(N+1) — the file's helpers (assertTenantSeesOwnRows, "
            "assertTenantCannotSeeOtherRows) are project-convention "
            "and ship via `dark-factory contracts init postgres`.",
        )

    def test_package_json_is_valid_and_esm(self) -> None:
        data = json.loads((CONTRACTS_DIR / "package.json").read_text())
        self.assertEqual(data.get("type"), "module")
        self.assertEqual(data.get("name"), "@df/contracts-postgres")
        self.assertTrue(data.get("private", False), "contracts must not publish to npm")

    def test_tsconfig_is_valid_json(self) -> None:
        text = (CONTRACTS_DIR / "tsconfig.json").read_text()
        data = json.loads(text)
        compiler_options = data.get("compilerOptions", {})
        self.assertTrue(compiler_options.get("strict"))
        self.assertTrue(compiler_options.get("noEmit"))

    def test_index_reexports_expected_helpers(self) -> None:
        src = (CONTRACTS_DIR / "index.ts").read_text()
        for name in EXPECTED_HELPERS:
            with self.subTest(helper=name):
                self.assertIn(
                    name, src,
                    f"index.ts must re-export {name}; got: {src!r}",
                )

    def test_guc_helper_defined_and_named(self) -> None:
        src = (CONTRACTS_DIR / "guc.ts").read_text()
        self.assertIn("export async function assertUnsetGucIsNullish", src)
        self.assertIn("current_setting", src)

    def test_tx_guc_helpers_defined_and_cite_wire_truth(self) -> None:
        r"""``tx_guc.ts`` ships ONE universal helper after B9a v(N+1):
        ``assertTxGucRoundTrips`` (plumbing round-trip, where the
        helper itself issues ``set_config`` so scenarios cannot
        foot-gun the SQL shape). The convention sibling
        ``assertAppSetsTxGuc`` (app-callback form) was deleted from
        the framework and now ships under
        ``darkfactory/contracts/templates/postgres/project-starters/
        app_tx_guc.ts``.

        Pins:

        1. Only ``assertTxGucRoundTrips`` is exported.
        2. The helper's body runs an ``await client.begin(...)`` —
           a non-tx read is the bug it exists to prevent.
        3. ``assertTxGucRoundTrips`` issues ``set_config`` internally,
           which is the only bindable form of SET LOCAL — hiding the
           SQL shape prevents the ``tx`SET LOCAL app.x = '${val}'``
           foot-gun.
        4. Guards against ``value === ''`` for the ambiguity reason
           documented in the helper (empty string is the postgres.js
           wire form of an unset GUC).
        """
        src = (CONTRACTS_DIR / "tx_guc.ts").read_text()
        # (1)
        self.assertIn("export async function assertTxGucRoundTrips", src)
        # (2)
        self.assertEqual(src.count("await client.begin(async (tx) =>"), 1)
        self.assertIn("current_setting", src)
        # (3) — plumbing primitive uses set_config with bind params.
        self.assertIn("set_config(${gucName}, ${value}, true)", src)
        # (4)
        self.assertIn("value === ''", src)

        # B9a v(N+1) pin: the convention helper has been deleted from
        # the framework; project-staged copy lives at
        # ``darkfactory/contracts/templates/postgres/project-starters/
        # app_tx_guc.ts`` and bootstraps via
        # ``dark-factory contracts init postgres``.
        self.assertNotIn("export async function assertAppSetsTxGuc", src)

    def test_rls_helper_defined_and_cites_wire_truth(self) -> None:
        """rls.ts must query both pg_tables AND pg_policies, and its
        error messages must cite the wire truth being asserted so
        defect-classifier output is specific.
        """
        src = (CONTRACTS_DIR / "rls.ts").read_text()
        self.assertIn("export async function assertRlsEnabled", src)
        self.assertIn("pg_tables", src)
        self.assertIn("pg_policies", src)
        # Error messages must name each distinguishable failure mode so
        # scenario output tells the defect-classifier what is wrong.
        self.assertIn("not found in pg_tables", src)
        self.assertIn("rowsecurity", src)
        self.assertIn("no policies attached", src)


class TestFrameworkDoesNotShipConventionHelpers(unittest.TestCase):
    """B9a v(N+1) regression backstop: the three convention helpers
    were deleted from the framework's live contracts/ts/ tree. A
    future commit that re-adds any of them (e.g., reverts the
    deprecation cycle, or accidentally pastes one back during a
    refactor) fails CI immediately.

    The helpers still exist as project starters under
    ``darkfactory/contracts/templates/postgres/project-starters/``
    and are bootstrapped into projects via
    ``dark-factory contracts init postgres`` — the absence pinned
    here is specifically about the FRAMEWORK's live tree.
    """

    def test_no_convention_helper_exported_in_framework_ts_tree(self) -> None:
        """No `.ts` file in the framework's contracts/ts/ tree may
        export any of the three deleted convention helpers. Walks
        every `.ts` file (not just index.ts) so a future maintainer
        adding the helper to a new file is also caught."""
        for ts_file in CONTRACTS_DIR.glob("*.ts"):
            with self.subTest(file=ts_file.name):
                src = ts_file.read_text()
                for helper in DELETED_CONVENTION_HELPERS:
                    self.assertNotIn(
                        f"export async function {helper}", src,
                        msg=(
                            f"{helper!r} must NOT be exported from the "
                            f"framework after B9a v(N+1). Found in "
                            f"{ts_file.name}. Move it to "
                            "darkfactory/contracts/templates/postgres/"
                            "project-starters/ if it was re-introduced "
                            "intentionally, or delete the export if not."
                        ),
                    )

    def test_index_does_not_re_export_convention_helpers(self) -> None:
        """``index.ts`` must not RE-EXPORT the convention helpers;
        B9d staging routes scenarios to ``_contracts/postgres/project/``
        for them, so a re-export here would shadow the staging path.

        Strips comment lines before checking — the live index.ts
        deliberately mentions the deleted helpers in a header comment
        documenting the v(N+1) migration trail (audit value > literal
        absence)."""
        src = (CONTRACTS_DIR / "index.ts").read_text()
        # Strip ``//`` line comments so prose mentions in headers do
        # not false-positive. Block comments (/* ... */) aren't used
        # in the current index.ts; if they appear, this regex keeps
        # them as code, which is the conservative direction.
        import re as _re
        executable = "\n".join(
            _re.sub(r"//.*$", "", line) for line in src.splitlines()
        )
        for helper in DELETED_CONVENTION_HELPERS:
            with self.subTest(helper=helper):
                self.assertNotIn(
                    helper, executable,
                    msg=(
                        f"{helper!r} must NOT be re-exported from the "
                        "framework's index.ts after B9a v(N+1). "
                        "Project-staged helpers reach scenarios via "
                        "`./_contracts/postgres/project`, not via "
                        "the framework's index re-export. (Header "
                        "comments mentioning the migration trail are "
                        "stripped before this check.)"
                    ),
                )


class TestPostgresContractsTypecheck(unittest.TestCase):
    """Runs ``tsc --noEmit`` against the contracts dir when tsc is on PATH.

    Skipped in minimal environments (e.g. laptops without node). The
    pipeline's Docker images install typescript; dev rig may not.
    """

    def test_tsc_noemit_passes(self) -> None:
        tsc = shutil.which("tsc")
        if tsc is None:
            self.skipTest("tsc not available on PATH")
        result = subprocess.run(
            [tsc, "--noEmit", "-p", str(CONTRACTS_DIR)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode, 0,
            f"tsc --noEmit failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
