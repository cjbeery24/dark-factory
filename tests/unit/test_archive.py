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
    diff_sha_pair_for_completed,
    init_archive_root,
    snapshot_final,
    snapshot_iteration,
    snapshot_iteration_diff,
    snapshot_iteration_diff_for_completed,
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


class TestSnapshotFinalAbortReason(TempDirTestCase):
    """ROADMAP.md Phase 2 B1-cls-audit surface (d).

    Pin the four-branch behavior of the conditional ``meta["abort_reason"]``
    write: only present when both ``status == "aborted"`` AND
    ``abort_reason`` is truthy. Pre-B1 baseline omitted the field
    entirely (no consumer could distinguish abort taxonomies); B1 makes
    the field present-when-meaningful, absent-otherwise.
    """

    def test_aborted_with_reason_writes_field(self) -> None:
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")
        snapshot_final(
            archive_root, root, "demo", "run1",
            status="aborted", iteration=1,
            abort_reason="scenario_author_retry_exhausted: TE pre-acceptance §4a hit",
        )
        meta = json.loads((archive_root / "final" / "meta.json").read_text())
        self.assertEqual(meta["status"], "aborted")
        self.assertEqual(
            meta["abort_reason"],
            "scenario_author_retry_exhausted: TE pre-acceptance §4a hit",
        )

    def test_aborted_with_empty_reason_omits_field(self) -> None:
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")
        snapshot_final(
            archive_root, root, "demo", "run1",
            status="aborted", iteration=1,
            abort_reason="",
        )
        meta = json.loads((archive_root / "final" / "meta.json").read_text())
        self.assertEqual(meta["status"], "aborted")
        self.assertNotIn("abort_reason", meta)

    def test_shipped_with_reason_omits_field(self) -> None:
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")
        # Defensive: even if a future caller passes abort_reason on a
        # ship path (shouldn't happen, but the field is gated on status
        # so we pin the gate, not the call-site discipline).
        snapshot_final(
            archive_root, root, "demo", "run1",
            status="shipped", iteration=2,
            abort_reason="this should be ignored on a ship",
        )
        meta = json.loads((archive_root / "final" / "meta.json").read_text())
        self.assertEqual(meta["status"], "shipped")
        self.assertNotIn("abort_reason", meta)

    def test_escalated_with_reason_omits_field(self) -> None:
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")
        snapshot_final(
            archive_root, root, "demo", "run1",
            status="escalated", iteration=3,
            abort_reason="this should be ignored on escalation",
        )
        meta = json.loads((archive_root / "final" / "meta.json").read_text())
        self.assertEqual(meta["status"], "escalated")
        self.assertNotIn("abort_reason", meta)

    def test_default_param_preserves_legacy_callers(self) -> None:
        # Calling without the ``abort_reason`` kwarg (the pre-B1 form)
        # must still produce a valid meta.json with no abort_reason key.
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")
        snapshot_final(
            archive_root, root, "demo", "run1",
            status="aborted", iteration=1,
        )
        meta = json.loads((archive_root / "final" / "meta.json").read_text())
        self.assertEqual(meta["status"], "aborted")
        self.assertNotIn("abort_reason", meta)


class _FakeIter:
    def __init__(self, end_sha: str = "") -> None:
        self.iteration_end_sha = end_sha


class _FakeSpecRun:
    def __init__(self, end_shas: list[str], preflight: str = "") -> None:
        self.iterations = [_FakeIter(s) for s in end_shas]
        self.preflight_base_ref = preflight


class TestDiffShaPairForCompleted(unittest.TestCase):
    """Pin the SHA-pair fallback chain.

    Mirrors ``_format_prior_iter_oracle_block``: prefer the prior iter's
    captured end SHA, else the run-start preflight baseline. Out-of-range
    or fully-empty pairs return ``("", "")`` so the caller skips the
    diff write rather than emit a misleading empty patch.
    """

    def test_iter_one_uses_preflight_baseline(self) -> None:
        spec_run = _FakeSpecRun(end_shas=["end_iter1"], preflight="baseline_sha")
        start, end = diff_sha_pair_for_completed(spec_run, 1)
        self.assertEqual(start, "baseline_sha")
        self.assertEqual(end, "end_iter1")

    def test_iter_two_uses_prior_end_sha(self) -> None:
        spec_run = _FakeSpecRun(
            end_shas=["end_iter1", "end_iter2"], preflight="baseline",
        )
        start, end = diff_sha_pair_for_completed(spec_run, 2)
        self.assertEqual(start, "end_iter1")
        self.assertEqual(end, "end_iter2")

    def test_iter_one_with_no_preflight_returns_fully_empty_pair(self) -> None:
        """Single-source-of-truth contract: when EITHER SHA is missing,
        return (\"\", \"\") not (\"\", end_sha). Footgun-removal: a future
        caller forgetting the empty-start guard would otherwise pass
        ``..end_sha`` to git, which means \"diff working-tree against
        end_sha\" — silently capturing every uncommitted file as if the
        implementer had touched it."""
        spec_run = _FakeSpecRun(end_shas=["end_iter1"], preflight="")
        start, end = diff_sha_pair_for_completed(spec_run, 1)
        self.assertEqual(start, "")
        self.assertEqual(end, "")

    def test_out_of_range_returns_empty_pair(self) -> None:
        spec_run = _FakeSpecRun(end_shas=["end_iter1"], preflight="b")
        self.assertEqual(diff_sha_pair_for_completed(spec_run, 0), ("", ""))
        self.assertEqual(diff_sha_pair_for_completed(spec_run, 2), ("", ""))
        self.assertEqual(diff_sha_pair_for_completed(spec_run, -1), ("", ""))

    def test_missing_end_sha_returns_fully_empty_pair(self) -> None:
        """Capture failed mid-run (e.g., git unavailable on iter 2).
        Same single-source-of-truth contract as the preflight-missing
        case: missing end SHA → ``(\"\", \"\")`` so the caller has one
        guard to write, not two."""
        spec_run = _FakeSpecRun(end_shas=["end_iter1", ""], preflight="b")
        start, end = diff_sha_pair_for_completed(spec_run, 2)
        self.assertEqual(start, "")
        self.assertEqual(end, "")


class TestSnapshotIterationDiff(TempDirTestCase):
    """End-to-end: iter-end archival writes implementer-changes.patch.

    Verifies the wiring between snapshot_iteration_diff and
    capture_iteration_diff: the patch lands in
    ``<archive_root>/iter-N/implementer-changes.patch`` with the diff
    bytes the git helper produced.
    """

    def test_writes_patch_into_iter_dir(self) -> None:
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")

        from unittest.mock import patch as _patch
        with _patch("darkfactory.git.ops.run") as mock_run:
            from darkfactory.utils.subprocess_runner import RunResult
            mock_run.return_value = RunResult(
                exit_code=0,
                stdout="diff --git a/src/foo.py b/src/foo.py\n+x\n",
                stderr="",
            )
            snapshot_iteration_diff(
                archive_root, root, 1,
                "abc", "def", ["src"], "tests",
            )
        patch_path = archive_root / "iter-1" / "implementer-changes.patch"
        self.assertTrue(patch_path.exists())
        self.assertIn("diff --git", patch_path.read_text())

    def test_none_archive_root_is_noop(self) -> None:
        # Should not raise and should not even attempt git invocation.
        from unittest.mock import patch as _patch
        with _patch("darkfactory.git.ops.run") as mock_run:
            snapshot_iteration_diff(
                None, self.tmp_dir, 1, "abc", "def", ["src"], "tests",
            )
            mock_run.assert_not_called()

    def test_completed_iteration_zero_is_noop(self) -> None:
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")
        from unittest.mock import patch as _patch
        with _patch("darkfactory.git.ops.run") as mock_run:
            snapshot_iteration_diff(
                archive_root, root, 0, "abc", "def", ["src"], "tests",
            )
            mock_run.assert_not_called()
        self.assertFalse((archive_root / "iter-0").exists())

    def test_empty_shas_skip_capture(self) -> None:
        """Empty SHA pair = upstream capture failed. Skip the write
        rather than emit a misleading patch (or worse, treat empty SHA
        as 'compare to nothing' inside git)."""
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")
        from unittest.mock import patch as _patch
        with _patch("darkfactory.git.ops.run") as mock_run:
            snapshot_iteration_diff(
                archive_root, root, 1, "", "def", ["src"], "tests",
            )
            snapshot_iteration_diff(
                archive_root, root, 1, "abc", "", ["src"], "tests",
            )
            mock_run.assert_not_called()
        self.assertFalse((archive_root / "iter-1" / "implementer-changes.patch").exists())

    def test_capture_failure_does_not_raise(self) -> None:
        """capture_iteration_diff returning False (git failure / write
        failure) must be a logged-and-continue path, not an exception."""
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")
        from unittest.mock import patch as _patch
        with _patch("darkfactory.git.ops.run") as mock_run:
            from darkfactory.utils.subprocess_runner import RunResult
            mock_run.return_value = RunResult(
                exit_code=1, stdout="", stderr="bad object",
            )
            # Must not raise.
            snapshot_iteration_diff(
                archive_root, root, 1,
                "abc", "def", ["src"], "tests",
            )
        # No patch file because git failed.
        self.assertFalse((archive_root / "iter-1" / "implementer-changes.patch").exists())


class TestSnapshotIterationDiffForCompleted(TempDirTestCase):
    """Convenience wrapper test — folds SHA resolution + diff capture.

    Pins the four production call sites (iter-loop top + ship/escalate/
    abort handlers) to one helper. If this wrapper drifts from the
    underlying contract, all four sites drift together rather than
    diverging silently.
    """

    def _spec_run_with_archive(self, end_shas: list[str], preflight: str = ""):
        root = make_project(self.tmp_dir)
        archive_root = init_archive_root(root, "demo", "run1")

        class _SR:
            pass

        sr = _SR()
        sr.archive_root = archive_root
        sr.iterations = [_FakeIter(s) for s in end_shas]
        sr.preflight_base_ref = preflight
        return sr, root, archive_root

    def test_writes_patch_for_iter_one_via_preflight_baseline(self) -> None:
        sr, root, archive_root = self._spec_run_with_archive(
            end_shas=["end_iter1"], preflight="baseline_sha",
        )
        from unittest.mock import patch as _patch
        with _patch("darkfactory.git.ops.run") as mock_run:
            from darkfactory.utils.subprocess_runner import RunResult
            mock_run.return_value = RunResult(
                exit_code=0, stdout="diff --git a/x b/x\n", stderr="",
            )
            snapshot_iteration_diff_for_completed(
                sr, root, 1, ["src"], "tests",
            )
        self.assertTrue(
            (archive_root / "iter-1" / "implementer-changes.patch").exists(),
        )

    def test_no_op_when_archive_root_none(self) -> None:
        # Archive init failed — wrapper must early-return cleanly.
        class _SR:
            pass

        sr = _SR()
        sr.archive_root = None
        sr.iterations = [_FakeIter("end_iter1")]
        sr.preflight_base_ref = "baseline"
        from unittest.mock import patch as _patch
        with _patch("darkfactory.git.ops.run") as mock_run:
            snapshot_iteration_diff_for_completed(
                sr, self.tmp_dir, 1, ["src"], "tests",
            )
            mock_run.assert_not_called()

    def test_no_op_when_sha_pair_unresolvable(self) -> None:
        # Iter 1 with no preflight baseline → diff_sha_pair returns ("","")
        # → wrapper skips git invocation entirely.
        # Phase 1b note: end_sha is populated ("end_iter1") here, so the
        # fallback trigger (`if not end_sha`) does NOT fire. The empty
        # path-spec returned by diff_sha_pair is purely the start_sha
        # being empty (no preflight). Pinned as documentation that
        # the fallback only fires for missing END sha, not start.
        sr, root, archive_root = self._spec_run_with_archive(
            end_shas=["end_iter1"], preflight="",
        )
        from unittest.mock import patch as _patch
        with _patch("darkfactory.git.ops.run") as mock_run:
            snapshot_iteration_diff_for_completed(
                sr, root, 1, ["src"], "tests",
            )
            mock_run.assert_not_called()
        self.assertFalse(
            (archive_root / "iter-1" / "implementer-changes.patch").exists(),
        )

    def test_fallback_captures_when_end_sha_empty(self) -> None:
        """Phase 1b (Gap 3 follow-up, 2026-04-29): when an iteration's
        ``iteration_end_sha`` is empty (escape-hatch path skipped
        ``_step_oracle``), the wrapper attempts a fresh capture against
        the live working tree. If the recapture succeeds, the patch is
        produced as if the original capture had fired.

        Closes Gap 3's failure mode #2: in the 13:50 run, iter-2 and
        iter-3 fired escape-hatch and produced no patch because end_sha
        was never captured. With this fallback, those iterations
        produce patches.

        Pins the iter_state mutation: when the fallback succeeds, the
        captured SHA is written back to
        ``spec_run.iterations[N-1].iteration_end_sha`` so downstream
        consumers (B1 idle check, prior-iter Oracle diff stat) see the
        populated value rather than degrading silently."""
        sr, root, archive_root = self._spec_run_with_archive(
            end_shas=["", ""],  # iter-1 and iter-2 both empty initially
            preflight="baseline_sha",
        )
        # Set iter-1's end_sha so iter-2 has a usable start_sha after
        # the fallback populates iter-2's end_sha (mirrors the actual
        # escape-hatch scenario where iter-1 ran the oracle and iter-2
        # didn't).
        sr.iterations[0].iteration_end_sha = "end_iter1"
        sr.run_id = "abc123def456"

        from unittest.mock import patch as _patch
        from darkfactory.utils.subprocess_runner import RunResult

        with _patch(
            "darkfactory.pipeline.iteration._capture_iteration_end_sha",
            return_value="recaptured_sha",
        ) as mock_recapture, _patch("darkfactory.git.ops.run") as mock_run:
            mock_run.return_value = RunResult(
                exit_code=0,
                stdout="diff --git a/src/x b/src/x\n+ok\n",
                stderr="",
            )
            snapshot_iteration_diff_for_completed(
                sr, root, 2, ["src"], "tests",
            )

            mock_recapture.assert_called_once()
            kwargs = mock_recapture.call_args.kwargs
            self.assertIn("ref_name", kwargs)
            self.assertEqual(
                kwargs["ref_name"],
                "refs/dark-factory/iter-end/abc123def456-2",
            )

        # Patch file produced.
        self.assertTrue(
            (archive_root / "iter-2" / "implementer-changes.patch").exists(),
        )
        # iter_state mutation: end_sha now reflects the recapture.
        self.assertEqual(sr.iterations[1].iteration_end_sha, "recaptured_sha")

    def test_fallback_failure_does_not_break_pipeline(self) -> None:
        """Best-effort: if the fallback recapture itself raises, the
        archive must still not break the pipeline."""
        sr, root, archive_root = self._spec_run_with_archive(
            end_shas=[""],
            preflight="baseline_sha",
        )
        sr.run_id = "abc"
        from unittest.mock import patch as _patch
        with _patch(
            "darkfactory.pipeline.iteration._capture_iteration_end_sha",
            side_effect=RuntimeError("git unavailable"),
        ), _patch("darkfactory.git.ops.run"):
            # Must not raise.
            snapshot_iteration_diff_for_completed(
                sr, root, 1, ["src"], "tests",
            )
        # No patch file (recapture failed).
        self.assertFalse(
            (archive_root / "iter-1" / "implementer-changes.patch").exists(),
        )
        # iter_state.iteration_end_sha unchanged (still empty).
        self.assertEqual(sr.iterations[0].iteration_end_sha, "")


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
