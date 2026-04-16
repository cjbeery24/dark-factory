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

EXPECTED_HELPERS_STEP2 = ("assertUnsetGucIsNullish",)


class TestPostgresContractsLayout(unittest.TestCase):
    def test_required_files_exist(self) -> None:
        for name in ("package.json", "tsconfig.json", "index.ts", "guc.ts", "README.md"):
            with self.subTest(file=name):
                self.assertTrue(
                    (CONTRACTS_DIR / name).is_file(),
                    f"{name} missing from {CONTRACTS_DIR}",
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
        for name in EXPECTED_HELPERS_STEP2:
            with self.subTest(helper=name):
                self.assertIn(
                    name, src,
                    f"index.ts must re-export {name}; got: {src!r}",
                )

    def test_guc_helper_defined_and_named(self) -> None:
        src = (CONTRACTS_DIR / "guc.ts").read_text()
        self.assertIn("export async function assertUnsetGucIsNullish", src)
        self.assertIn("current_setting", src)


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
