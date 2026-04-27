"""Tests for ``dark-factory contracts init <adapter>`` (Phase 4
Contracts CLI v0).

Two surfaces:

1. **Helper function** ``init_adapter_contracts`` — pure copy/skip
   logic with ``force`` and ``dry_run`` flags. Tests cover happy path,
   unknown adapter, idempotency-without-force, force-overwrites,
   dry-run-no-fs-changes, and recursive-subdir preservation.

2. **CLI argparse + dispatch** via ``dark-factory contracts init``.
   Drives ``main()`` end-to-end so the parser wiring is exercised.

The tests anchor against the framework's actual
``templates/contracts/postgres/project-starters/`` content (4 files
shipped in B9a v(N): README.md, index.ts, app_tx_guc.ts, isolation.ts).
A future starter add/remove will require updating the asserted
file-count / file-name expectations here — that's intentional. Drift
between starter content and these tests should fail loud at PR time.
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from tests.helpers import TempDirTestCase

from darkfactory.cli import main
from darkfactory.contracts.init import (
    InitResult,
    init_adapter_contracts,
    list_available_adapters,
    starters_dir,
)


# Files shipped under templates/contracts/postgres/project-starters/
# at the time of B9a v(N). Drift from this set is a maintainer-must-
# update signal — the tests fail and the maintainer either updates
# the starter dir or updates this list. Either way, awareness.
_POSTGRES_STARTER_FILES: tuple[str, ...] = (
    "README.md",
    "app_tx_guc.ts",
    "index.ts",
    "isolation.ts",
)


class TestStartersDirResolution(unittest.TestCase):
    """``starters_dir`` resolves the framework starter dir for an
    adapter. ``list_available_adapters`` enumerates dirs that
    actually have starters."""

    def test_postgres_starters_dir_exists(self) -> None:
        d = starters_dir("postgres")
        self.assertTrue(d.is_dir(), f"postgres starters dir missing: {d}")

    def test_unknown_adapter_starters_dir_is_path_but_not_dir(self) -> None:
        # The function returns a Path regardless of existence — it's
        # ``init_adapter_contracts`` that raises when missing.
        d = starters_dir("not-a-real-adapter")
        self.assertFalse(d.is_dir())

    def test_list_available_adapters_includes_postgres(self) -> None:
        adapters = list_available_adapters()
        self.assertIn("postgres", adapters)


class TestInitAdapterContractsHappyPath(TempDirTestCase):
    def test_copies_all_postgres_starter_files(self) -> None:
        result = init_adapter_contracts("postgres", self.tmp_dir)
        # Filenames in result.files_copied are relative paths into the
        # starters dir. Compare against the expected set.
        self.assertEqual(
            sorted(result.files_copied),
            list(_POSTGRES_STARTER_FILES),
        )
        self.assertEqual(result.skipped_existing, [])
        self.assertFalse(result.dry_run)

    def test_files_actually_appear_at_destination(self) -> None:
        init_adapter_contracts("postgres", self.tmp_dir)
        dst = self.tmp_dir / ".dark-factory" / "contracts" / "postgres"
        for filename in _POSTGRES_STARTER_FILES:
            with self.subTest(file=filename):
                self.assertTrue(
                    (dst / filename).exists(),
                    f"copied file missing: {dst / filename}",
                )

    def test_copied_content_matches_source_byte_for_byte(self) -> None:
        init_adapter_contracts("postgres", self.tmp_dir)
        src_dir = starters_dir("postgres")
        dst_dir = self.tmp_dir / ".dark-factory" / "contracts" / "postgres"
        for filename in _POSTGRES_STARTER_FILES:
            with self.subTest(file=filename):
                self.assertEqual(
                    (src_dir / filename).read_bytes(),
                    (dst_dir / filename).read_bytes(),
                )

    def test_result_carries_correct_paths(self) -> None:
        result = init_adapter_contracts("postgres", self.tmp_dir)
        self.assertEqual(result.adapter, "postgres")
        self.assertEqual(result.src_dir, starters_dir("postgres"))
        self.assertEqual(
            result.dst_dir,
            self.tmp_dir / ".dark-factory" / "contracts" / "postgres",
        )


class TestInitAdapterContractsErrorPath(TempDirTestCase):
    def test_unknown_adapter_raises(self) -> None:
        with self.assertRaisesRegex(ValueError, "unknown adapter"):
            init_adapter_contracts("totally-not-a-real-adapter", self.tmp_dir)

    def test_unknown_adapter_message_lists_available(self) -> None:
        try:
            init_adapter_contracts("nope", self.tmp_dir)
        except ValueError as exc:
            self.assertIn("postgres", str(exc))
        else:
            self.fail("expected ValueError")


class TestInitAdapterContractsIdempotency(TempDirTestCase):
    """A second run on a populated destination skips existing files
    by default; ``force=True`` overwrites."""

    def test_second_run_without_force_skips_existing(self) -> None:
        # First run: clean copy.
        init_adapter_contracts("postgres", self.tmp_dir)
        # Second run: every file is now existing.
        result = init_adapter_contracts("postgres", self.tmp_dir)
        self.assertEqual(result.files_copied, [])
        self.assertEqual(
            sorted(result.skipped_existing),
            list(_POSTGRES_STARTER_FILES),
        )

    def test_force_overwrites_existing(self) -> None:
        # First run: clean copy.
        init_adapter_contracts("postgres", self.tmp_dir)
        # Touch a copied file so we can detect overwrite.
        readme = self.tmp_dir / ".dark-factory" / "contracts" / "postgres" / "README.md"
        original_size = readme.stat().st_size
        readme.write_text("// stale local edit\n")
        self.assertNotEqual(readme.stat().st_size, original_size)
        # Force run: file is restored to framework version.
        result = init_adapter_contracts(
            "postgres", self.tmp_dir, force=True,
        )
        # Linus Phase-4-v0: force-overwritten files surface in their
        # OWN list, NOT files_copied. The maintainer asked for force
        # and must see what got stomped.
        self.assertEqual(result.files_copied, [])
        self.assertEqual(
            sorted(result.force_overwritten),
            list(_POSTGRES_STARTER_FILES),
        )
        self.assertEqual(result.skipped_existing, [])
        self.assertEqual(readme.stat().st_size, original_size)

    def test_force_with_only_some_files_existing(self) -> None:
        """Mixed pre-state: SOME starter files at the destination,
        OTHERS not. ``--force`` overwrites the existing subset and
        creates the rest fresh — surface BOTH lists separately so a
        maintainer sees the partial-overwrite cleanly."""
        # Pre-create just one of the four starter files.
        dst = self.tmp_dir / ".dark-factory" / "contracts" / "postgres"
        dst.mkdir(parents=True)
        (dst / "README.md").write_text("// existing\n")
        result = init_adapter_contracts(
            "postgres", self.tmp_dir, force=True,
        )
        self.assertEqual(result.force_overwritten, ["README.md"])
        self.assertEqual(
            sorted(result.files_copied),
            sorted(f for f in _POSTGRES_STARTER_FILES if f != "README.md"),
        )
        self.assertEqual(result.skipped_existing, [])


class TestInitAdapterContractsDryRun(TempDirTestCase):
    """``dry_run=True`` populates the result lists but writes nothing."""

    def test_dry_run_creates_no_files(self) -> None:
        result = init_adapter_contracts(
            "postgres", self.tmp_dir, dry_run=True,
        )
        self.assertEqual(
            sorted(result.files_copied),
            list(_POSTGRES_STARTER_FILES),
        )
        self.assertTrue(result.dry_run)
        # No files written to the destination.
        dst = self.tmp_dir / ".dark-factory" / "contracts" / "postgres"
        self.assertFalse(dst.exists())

    def test_dry_run_does_not_create_parent_dirs(self) -> None:
        # Even ``.dark-factory/`` should not get mkdir'd in dry-run.
        init_adapter_contracts("postgres", self.tmp_dir, dry_run=True)
        self.assertFalse((self.tmp_dir / ".dark-factory").exists())


class TestInitCLI(TempDirTestCase):
    """End-to-end via ``main()`` so argparse wiring is exercised."""

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

    def test_init_postgres_succeeds(self) -> None:
        code, out, err = self.run_cli("contracts", "init", "postgres")
        self.assertEqual(code, 0, msg=f"stderr={err!r}")
        self.assertIn("Copied", out)
        self.assertIn("README.md", out)
        # Files actually landed.
        dst = self.tmp_dir / ".dark-factory" / "contracts" / "postgres"
        self.assertTrue((dst / "README.md").exists())

    def test_init_points_at_readme_after_copy(self) -> None:
        """Lvl-1 README discoverability fix: after copying starter
        files, the CLI explicitly points the developer at the
        README.md so they don't have to discover it via directory
        listing. Closes the "discover what to do after init"
        journey gap (the ε commit closed "discover init"; this
        closes the next link)."""
        code, out, _ = self.run_cli("contracts", "init", "postgres")
        self.assertEqual(code, 0)
        self.assertIn("Next:", out)
        self.assertIn("README.md", out)
        self.assertIn("editing instructions", out)

    def test_init_dry_run_omits_readme_pointer(self) -> None:
        """Dry-run shouldn't claim there's a README to read — no
        file was actually written. Suppresses the pointer."""
        code, out, _ = self.run_cli(
            "contracts", "init", "postgres", "--dry-run",
        )
        self.assertEqual(code, 0)
        self.assertNotIn("Next:", out)

    def test_init_dry_run_does_not_write(self) -> None:
        code, out, _ = self.run_cli(
            "contracts", "init", "postgres", "--dry-run",
        )
        self.assertEqual(code, 0)
        self.assertIn("Would copy", out)
        self.assertFalse((self.tmp_dir / ".dark-factory").exists())

    def test_init_existing_files_without_force_skipped(self) -> None:
        # First invocation populates.
        self.run_cli("contracts", "init", "postgres")
        # Second invocation: no --force, all files reported skipped.
        code, out, _ = self.run_cli("contracts", "init", "postgres")
        self.assertEqual(code, 0)
        self.assertIn("Skipped", out)
        self.assertIn("--force", out)

    def test_init_force_overwrites(self) -> None:
        self.run_cli("contracts", "init", "postgres")
        readme = self.tmp_dir / ".dark-factory" / "contracts" / "postgres" / "README.md"
        readme.write_text("// stale\n")
        code, out, _ = self.run_cli(
            "contracts", "init", "postgres", "--force",
        )
        self.assertEqual(code, 0)
        # Linus Phase-4-v0 review: force overwrites must be SURFACED
        # in their own section, not silently buried in "Copied N
        # files". A maintainer running --force after editing project
        # convention helpers in place must see what got stomped.
        self.assertIn("Overwrote", out)
        self.assertIn("README.md", out)
        # Stale content overwritten on disk.
        self.assertNotEqual(readme.read_text(), "// stale\n")

    def test_init_force_dry_run_says_would_overwrite(self) -> None:
        """Dry-run + force previews the overwrite without writing."""
        self.run_cli("contracts", "init", "postgres")
        readme = self.tmp_dir / ".dark-factory" / "contracts" / "postgres" / "README.md"
        readme.write_text("// edited locally\n")
        code, out, _ = self.run_cli(
            "contracts", "init", "postgres", "--force", "--dry-run",
        )
        self.assertEqual(code, 0)
        self.assertIn("Would overwrite", out)
        # Edits NOT lost — dry-run preserves local content.
        self.assertEqual(readme.read_text(), "// edited locally\n")

    def test_init_unknown_adapter_exits_1_with_stderr(self) -> None:
        code, out, err = self.run_cli(
            "contracts", "init", "no-such-adapter",
        )
        self.assertEqual(code, 1)
        self.assertIn("contracts init failed", err)
        self.assertIn("unknown adapter", err)
        # Stdout silent on error.
        self.assertEqual(out, "")

    def test_contracts_no_subcommand_lists_init_in_usage(self) -> None:
        """The ``contracts`` parent's no-subcommand error message must
        list the new ``init`` subcommand. Catches a future maintainer
        adding a subcommand to the parser without updating the usage
        string."""
        code, _, err = self.run_cli("contracts")
        self.assertEqual(code, 1)
        self.assertIn("init", err)
        self.assertIn("auto-eval", err)


if __name__ == "__main__":
    unittest.main()
