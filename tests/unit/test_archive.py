"""Tests for darkfactory.pipeline.archive.

The archive is an observational, best-effort subsystem — it must
preserve per-iteration artifacts before they get overwritten, and must
never raise an exception that reaches the pipeline.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path

from darkfactory.pipeline.archive import (
    init_archive_root,
    snapshot_final,
    snapshot_iteration,
)
from tests.helpers import TempDirTestCase, make_project


class TestInitArchiveRoot(TempDirTestCase):
    def test_creates_directory_and_manifest(self) -> None:
        make_project(self.tmp_dir)
        root = init_archive_root(self.tmp_dir, "demo", "abc123def456")

        self.assertTrue(root.exists())
        self.assertTrue(root.is_dir())
        self.assertEqual(root.parent.name, "archive")
        self.assertEqual(root.parent.parent, self.tmp_dir / ".dark-factory")

        manifest = json.loads((root / "run.json").read_text())
        self.assertEqual(manifest["spec"], "demo")
        self.assertEqual(manifest["run_id"], "abc123def456")
        self.assertIn("started_at", manifest)

    def test_directory_name_includes_spec_and_short_run(self) -> None:
        make_project(self.tmp_dir)
        root = init_archive_root(self.tmp_dir, "myspec", "deadbeefcafef00d")
        # Format: <YYYYMMDD-HHMMSS>-<spec>-<short-run>
        parts = root.name.split("-")
        self.assertGreaterEqual(len(parts), 4)
        self.assertIn("myspec", root.name)
        self.assertIn("deadbeef", root.name)

    def test_empty_run_id_fallback(self) -> None:
        make_project(self.tmp_dir)
        root = init_archive_root(self.tmp_dir, "demo", "")
        self.assertIn("norunid", root.name)


class TestSnapshotIteration(TempDirTestCase):
    def _setup_iter_artifacts(self) -> Path:
        root = make_project(self.tmp_dir)
        df = root / ".dark-factory"
        (df / "reviews" / "demo-feedback-iter1.md").write_text("fb1")
        (df / "reviews" / "other-feedback-iter1.md").write_text("unrelated")
        (df / "reports" / "demo-quality-gate.md").write_text("gate")
        (df / "audit" / "demo-implementer.jsonl").write_text("{}")
        (df / "architecture" / "substrate-manifests").mkdir(parents=True)
        (df / "architecture" / "demo-manifest.yaml").write_text("m")
        (df / "architecture" / "substrate-manifests" / "demo.toml").write_text("s")
        return root

    def test_copies_spec_scoped_files_and_skips_unrelated(self) -> None:
        root = self._setup_iter_artifacts()
        archive_root = init_archive_root(root, "demo", "run1")

        snapshot_iteration(archive_root, root, "demo", 1)

        iter_dir = archive_root / "iter-1"
        self.assertTrue((iter_dir / "reviews" / "demo-feedback-iter1.md").exists())
        self.assertFalse((iter_dir / "reviews" / "other-feedback-iter1.md").exists())
        self.assertTrue((iter_dir / "reports" / "demo-quality-gate.md").exists())
        self.assertTrue((iter_dir / "audit" / "demo-implementer.jsonl").exists())

    def test_architecture_recursion_captures_substrate_manifest(self) -> None:
        root = self._setup_iter_artifacts()
        archive_root = init_archive_root(root, "demo", "run1")

        snapshot_iteration(archive_root, root, "demo", 2)

        iter_dir = archive_root / "iter-2"
        self.assertTrue((iter_dir / "architecture" / "demo-manifest.yaml").exists())
        self.assertTrue(
            (iter_dir / "architecture" / "substrate-manifests" / "demo.toml").exists(),
        )

    def test_scenarios_recursion_captures_spec_scoped_tree(self) -> None:
        """Scenarios live at ``.dark-factory/scenarios/<spec>/...`` and
        must land under ``iter-N/scenarios/<spec>/...`` in the archive.
        Added 2026-04-22 Task 3 so ``--anchor-scenarios`` V-dimension
        runs can point at the archive instead of the legacy
        ``archived-runs/`` tree."""
        root = self._setup_iter_artifacts()
        df = root / ".dark-factory"
        (df / "scenarios" / "demo").mkdir(parents=True)
        (df / "scenarios" / "demo" / "test_smoke.py").write_text("t")
        (df / "scenarios" / "demo" / "_contracts" / "postgres").mkdir(parents=True)
        (df / "scenarios" / "demo" / "_contracts" / "postgres" / "helper.py").write_text("h")
        # Sibling spec's scenarios must NOT land in demo's archive.
        (df / "scenarios" / "other-spec").mkdir(parents=True)
        (df / "scenarios" / "other-spec" / "test_foo.py").write_text("x")

        archive_root = init_archive_root(root, "demo", "run1")
        snapshot_iteration(archive_root, root, "demo", 1)

        iter_dir = archive_root / "iter-1"
        self.assertTrue(
            (iter_dir / "scenarios" / "demo" / "test_smoke.py").exists(),
        )
        self.assertTrue(
            (iter_dir / "scenarios" / "demo" / "_contracts" / "postgres" / "helper.py").exists(),
        )
        self.assertFalse(
            (iter_dir / "scenarios" / "other-spec" / "test_foo.py").exists(),
        )

    def test_none_archive_root_is_noop(self) -> None:
        root = self._setup_iter_artifacts()
        # Should not raise when archive_root is None (init failed).
        snapshot_iteration(None, root, "demo", 1)

    def test_completed_iteration_zero_is_noop(self) -> None:
        """Guard against off-by-one callers: completed_iteration<1 creates no dir.

        Regression for run 3b1d2bc7: handlers were passing
        ``spec_run.iteration`` (already incremented past the last real
        iteration), producing an empty ``iter-<max+1>/`` archive entry
        while the real final iteration was skipped. Callers now pass
        ``len(spec_run.iterations)``, which is 0 before the first
        iteration ever starts — and this must NOT create ``iter-0/``.
        """
        root = self._setup_iter_artifacts()
        archive_root = init_archive_root(root, "demo", "run1")
        snapshot_iteration(archive_root, root, "demo", 0)
        self.assertFalse((archive_root / "iter-0").exists())
        snapshot_iteration(archive_root, root, "demo", -1)
        self.assertFalse((archive_root / "iter--1").exists())

    def test_missing_source_dirs_tolerated(self) -> None:
        root = self.tmp_dir  # no .dark-factory created
        (root / ".dark-factory").mkdir()
        archive_root = init_archive_root(root, "demo", "run1")
        # Should not raise even though every source dir is missing.
        snapshot_iteration(archive_root, root, "demo", 1)
        # iter-1/ is still created (best-effort mkdir).
        self.assertTrue((archive_root / "iter-1").exists())

    def test_iteration_snapshot_is_independent(self) -> None:
        """Overwriting iter 1's feedback should not affect iter 1's archive."""
        root = self._setup_iter_artifacts()
        archive_root = init_archive_root(root, "demo", "run1")
        snapshot_iteration(archive_root, root, "demo", 1)

        # Simulate iter 2 overwriting iter 1's feedback file.
        (root / ".dark-factory" / "reviews" / "demo-feedback-iter1.md").write_text(
            "overwritten",
        )
        snapshot_iteration(archive_root, root, "demo", 2)

        # Iter 1 archive keeps original content; iter 2 archive has new.
        iter1 = (archive_root / "iter-1" / "reviews" / "demo-feedback-iter1.md")
        iter2 = (archive_root / "iter-2" / "reviews" / "demo-feedback-iter1.md")
        self.assertEqual(iter1.read_text(), "fb1")
        self.assertEqual(iter2.read_text(), "overwritten")


class TestSnapshotFinal(TempDirTestCase):
    def test_captures_run_level_files_and_meta(self) -> None:
        root = make_project(self.tmp_dir)
        df = root / ".dark-factory"
        (df / "events.jsonl").write_text('{"event":"start"}\n')
        (df / "metrics.jsonl").write_text('{"m":1}\n')
        (df / "failures").mkdir()
        (df / "failures" / "demo-fail.json").write_text("{}")

        archive_root = init_archive_root(root, "demo", "run1")
        snapshot_final(archive_root, root, "demo", "run1",
                       status="shipped", iteration=2)

        final = archive_root / "final"
        self.assertTrue((final / "events.jsonl").exists())
        self.assertTrue((final / "metrics.jsonl").exists())
        self.assertTrue((final / "failures" / "demo-fail.json").exists())

        meta = json.loads((final / "meta.json").read_text())
        self.assertEqual(meta["status"], "shipped")
        self.assertEqual(meta["final_iteration"], 2)
        self.assertEqual(meta["spec"], "demo")
        self.assertEqual(meta["run_id"], "run1")

    def test_none_archive_root_is_noop(self) -> None:
        root = make_project(self.tmp_dir)
        snapshot_final(None, root, "demo", "run1",
                       status="failed", iteration=1)

    def test_missing_run_level_files_tolerated(self) -> None:
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")
        # events.jsonl / metrics.jsonl don't exist — should still write meta.
        snapshot_final(archive_root, root, "demo", "run1",
                       status="escalated", iteration=3)
        meta = json.loads((archive_root / "final" / "meta.json").read_text())
        self.assertEqual(meta["status"], "escalated")


class TestArchiveBestEffort(TempDirTestCase):
    def test_unreadable_source_dir_does_not_raise(self) -> None:
        """Archive failures must never propagate — they're diagnostic only."""
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")
        reviews = root / ".dark-factory" / "reviews"
        (reviews / "demo-feedback-iter1.md").write_text("fb")
        # Remove read permission so iterdir fails. POSIX-only; skip on
        # platforms where chmod can't strip read.
        if os.name != "posix":
            self.skipTest("POSIX-only permission test")
        try:
            os.chmod(reviews, 0o000)
            # Must not raise.
            snapshot_iteration(archive_root, root, "demo", 1)
        finally:
            os.chmod(reviews, 0o755)


if __name__ == "__main__":
    unittest.main()
