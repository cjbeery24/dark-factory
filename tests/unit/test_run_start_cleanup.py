"""Tests for pipeline/run_start_cleanup.py — per-run artifact sweep."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.pipeline.run_start_cleanup import (
    _PURGE_GLOBS,
    purge_prior_run_artifacts,
)


# Files the plan identifies as per-iter / per-run outputs that must
# be purged. Each entry corresponds to one of the globs in
# ``_PURGE_GLOBS`` expanded with spec=``myspec``.
PURGE_SAMPLES = (
    "reviews/myspec-feedback-iter1.md",
    "reviews/myspec-feedback-iter2.md",
    "reviews/myspec-oracle-output-iter1.md",
    "reviews/myspec-oracle-output-iter3.md",
    "reviews/myspec-review.md",
    "reviews/myspec-escalation.md",
    "reports/myspec-defects-iter1.json",
    "reports/myspec-defects-iter2.json",
    "reports/myspec-scenario-counts-iter1.env",
    "reports/myspec-scenario-counts-iter2.env",
    "reports/myspec-substrate-findings.json",
    "reports/myspec-quality-gate.md",
    "reports/myspec-quality-gate-iter1.md",
    "reports/myspec-smoke-test.md",
    "reports/myspec-pre-gate.md",
    "reports/myspec-pre-gate-iter1.md",
    "reports/myspec-review-prepass.md",
    "reports/myspec-review-prepass.json",
    "reports/myspec-scenario-validation.md",
    "reports/myspec-scenario-counts.env",
    "reports/myspec-holdouts.md",
    "reports/myspec-proposal-rehearsal.md",
    "reports/myspec-scenarios.md",
    "reports/myspec-architecture.md",
    "reports/myspec-evaluation-iter1.md",
    "reports/myspec-evaluation-iter3.md",
)

# Files that must survive the sweep.
PRESERVE_SAMPLES = (
    # Pre-flight spec-validation inputs read by dark-factory run itself.
    "reviews/myspec-validation.md",
    "reviews/myspec-validation.status",
    # Inputs — not outputs — of the pipeline.
    "specs/myspec.md",
    "plans/build-order.md",
    "architecture/myspec-manifest.yaml",
    "architecture/substrate-manifests/myspec.toml",
    "scenarios/myspec/test_smoke.py",
    "config.toml",
    "project-hints.toml",
    # Files that belong to a different spec — must NOT be touched even
    # when they sit in the same directory. Catches accidental broadening
    # of the glob (e.g. ``reviews/*-feedback-iter*.md``).
    "reviews/otherspec-feedback-iter1.md",
    "reviews/otherspec-review.md",
    "reports/otherspec-quality-gate.md",
    # State / audit / events are not pipeline feedback-loop inputs; a
    # resume flow relies on them surviving.
    "state/myspec.state",
    "audit/myspec-somerunid.log",
    "events.jsonl",
)


def _seed(project_dir: Path, rel_paths: tuple[str, ...]) -> None:
    dark = project_dir / ".dark-factory"
    for rel in rel_paths:
        target = dark / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"seed:{rel}")


class TestPurgePriorRunArtifacts(unittest.TestCase):
    def test_purges_every_listed_glob(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            _seed(project, PURGE_SAMPLES + PRESERVE_SAMPLES)

            count, files = purge_prior_run_artifacts(project, "myspec")

            self.assertEqual(count, len(PURGE_SAMPLES))
            self.assertEqual(len(files), count)
            # Sorted for stable event emission.
            self.assertEqual(files, sorted(files))

            dark = project / ".dark-factory"
            for rel in PURGE_SAMPLES:
                self.assertFalse(
                    (dark / rel).exists(),
                    f"expected {rel} to be purged",
                )
            for rel in PRESERVE_SAMPLES:
                self.assertTrue(
                    (dark / rel).exists(),
                    f"expected {rel} to be preserved",
                )

    def test_returns_relative_paths_under_dark_factory(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            _seed(project, ("reviews/myspec-feedback-iter1.md",))
            _, files = purge_prior_run_artifacts(project, "myspec")
            self.assertEqual(files, ["reviews/myspec-feedback-iter1.md"])

    def test_no_dark_factory_dir_is_noop(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            count, files = purge_prior_run_artifacts(project, "myspec")
            self.assertEqual(count, 0)
            self.assertEqual(files, [])

    def test_empty_dark_factory_is_noop(self) -> None:
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / ".dark-factory").mkdir()
            count, files = purge_prior_run_artifacts(project, "myspec")
            self.assertEqual(count, 0)
            self.assertEqual(files, [])

    def test_does_not_touch_archive_tree(self) -> None:
        """Cleanup reads only the live tree — ``archive/`` is sacred."""
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            _seed(
                project,
                (
                    "archive/20260101-myspec-aaaa0000/iter-1/reviews/myspec-feedback-iter1.md",
                    "archive/20260101-myspec-aaaa0000/final/events.jsonl",
                ),
            )
            count, _ = purge_prior_run_artifacts(project, "myspec")
            self.assertEqual(count, 0)
            dark = project / ".dark-factory"
            self.assertTrue(
                (dark / "archive/20260101-myspec-aaaa0000/iter-1/reviews/myspec-feedback-iter1.md").exists()
            )

    def test_spec_name_prefix_collision_does_not_affect_sibling_specs(self) -> None:
        """Purging spec ``database-foundation`` must not touch files
        belonging to sibling spec ``database-foundation-v2``."""
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            _seed(
                project,
                (
                    "reviews/database-foundation-feedback-iter1.md",
                    "reviews/database-foundation-review.md",
                    # Sibling spec whose name is a superstring of the
                    # target spec — must survive the sweep.
                    "reviews/database-foundation-v2-feedback-iter1.md",
                    "reviews/database-foundation-v2-review.md",
                    "reports/database-foundation-v2-quality-gate.md",
                ),
            )
            count, files = purge_prior_run_artifacts(
                project, "database-foundation",
            )
            self.assertEqual(count, 2)
            self.assertEqual(
                sorted(files),
                [
                    "reviews/database-foundation-feedback-iter1.md",
                    "reviews/database-foundation-review.md",
                ],
            )
            # v2 spec's files are untouched.
            dark = project / ".dark-factory"
            self.assertTrue(
                (dark / "reviews/database-foundation-v2-feedback-iter1.md").exists(),
                "sibling spec's file must not be touched by another spec's cleanup",
            )
            self.assertTrue(
                (dark / "reviews/database-foundation-v2-review.md").exists()
            )
            self.assertTrue(
                (dark / "reports/database-foundation-v2-quality-gate.md").exists()
            )

    def test_rejects_spec_name_with_path_traversal(self) -> None:
        """Refuse ``..`` / ``/`` / ``\\`` in spec names — belt-and-
        suspenders on a destructive filesystem operation."""
        with TemporaryDirectory() as tmp:
            project = Path(tmp)
            for bad in ("../other", "foo/bar", r"foo\bar", ".."):
                with self.assertRaises(ValueError):
                    purge_prior_run_artifacts(project, bad)

    def test_glob_list_covers_every_plan_pattern(self) -> None:
        """Guard against a future edit deleting a pattern silently."""
        # Plan lists 14 patterns + Linus review adds 4 more (the full
        # authored set under .dark-factory/{reports,reviews}/) + Task #18
        # adds substrate-setup-output.txt.
        self.assertEqual(len(_PURGE_GLOBS), 19)


if __name__ == "__main__":
    unittest.main()
