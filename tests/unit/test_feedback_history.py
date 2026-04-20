"""Unit tests for Priority 1 (2026-04-20) feedback-history builder."""

from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path

from darkfactory.pipeline.feedback_history import (
    ROOT_CAUSE_PREAMBLE_TEMPLATE,
    audit_history_for_barrier_leak,
    build_cumulative_history,
    build_iter_diff,
)
from tests.helpers import TempDirTestCase


def _write_feedback(reviews_dir: Path, spec: str, iter_n: int, text: str) -> None:
    reviews_dir.mkdir(parents=True, exist_ok=True)
    (reviews_dir / f"{spec}-feedback-iter{iter_n}.md").write_text(
        text, encoding="utf-8"
    )


def _write_defects(
    reports_dir: Path, spec: str, iter_n: int, defects: list[dict]
) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)
    (reports_dir / f"{spec}-defects-iter{iter_n}.json").write_text(
        json.dumps({"iteration": iter_n, "defects": defects}),
        encoding="utf-8",
    )


class TestBuildCumulativeHistory(TempDirTestCase):
    def test_iter_1_returns_empty(self) -> None:
        reviews = self.tmp_dir / "reviews"
        reports = self.tmp_dir / "reports"
        result = build_cumulative_history(reviews, reports, "demo", 1)
        self.assertEqual(result.text, "")
        self.assertEqual(result.iterations_included, [])

    def test_iter_2_contains_iter_1_feedback_only(self) -> None:
        reviews = self.tmp_dir / "reviews"
        reports = self.tmp_dir / "reports"
        _write_feedback(reviews, "demo", 1, "# Iter 1\nFR-7 broken.\n")
        _write_defects(
            reports, "demo", 1,
            [{"cited_anchor_ids": ["FR-7"], "category": "IMPL_BUG",
              "rationale": "FR-7 test broken"}],
        )
        result = build_cumulative_history(reviews, reports, "demo", 2)
        self.assertIn("### Iteration 1 feedback", result.text)
        self.assertIn("FR-7 broken", result.text)
        self.assertIn("[FR-7] IMPL_BUG", result.text)
        self.assertEqual(result.iterations_included, [1])
        self.assertNotIn("Iteration 2 feedback", result.text)

    def test_iter_3_chronological_iter1_then_iter2(self) -> None:
        """Chronological ordering is required \u2014 the implementer must
        read iter 1 before iter 2 to see the progression."""
        reviews = self.tmp_dir / "reviews"
        reports = self.tmp_dir / "reports"
        _write_feedback(reviews, "demo", 1, "FR-7 was broken in iter 1")
        _write_feedback(reviews, "demo", 2, "FR-7 still broken in iter 2")
        result = build_cumulative_history(reviews, reports, "demo", 3)
        idx_1 = result.text.find("### Iteration 1 feedback")
        idx_2 = result.text.find("### Iteration 2 feedback")
        self.assertGreater(idx_1, -1)
        self.assertGreater(idx_2, idx_1,
                           "iter 2 block must appear AFTER iter 1 block")
        self.assertEqual(result.iterations_included, [1, 2])

    def test_missing_feedback_iteration_is_skipped_without_error(self) -> None:
        reviews = self.tmp_dir / "reviews"
        reports = self.tmp_dir / "reports"
        # Only iter 1 present, iter 2 missing (shouldn't happen but resilient)
        _write_feedback(reviews, "demo", 1, "iter1 present")
        result = build_cumulative_history(reviews, reports, "demo", 3)
        self.assertIn("iter1 present", result.text)
        self.assertEqual(result.iterations_included, [1])

    def test_cadence_signal_preserved_no_dedupe(self) -> None:
        """Same anchor ID appearing across iterations must be preserved \u2014
        the cadence ('FR-7 failed every iteration') is the signal."""
        reviews = self.tmp_dir / "reviews"
        reports = self.tmp_dir / "reports"
        for n in (1, 2):
            _write_defects(reports, "demo", n, [
                {"cited_anchor_ids": ["FR-7"], "category": "IMPL_BUG",
                 "rationale": f"iter{n} attempt"},
            ])
            _write_feedback(reviews, "demo", n, f"iter{n} content")
        result = build_cumulative_history(reviews, reports, "demo", 3)
        # FR-7 appears twice (iter 1 and iter 2 both had FR-7 fail).
        self.assertEqual(result.text.count("[FR-7] IMPL_BUG"), 2)

    def test_truncation_preserves_most_recent_iteration(self) -> None:
        """When cumulative history would exceed the cap, the most
        recent iteration block must survive intact."""
        reviews = self.tmp_dir / "reviews"
        reports = self.tmp_dir / "reports"
        # Stuff iter 1 with 50k chars of padding; iter 2 content must
        # still be present.
        _write_feedback(reviews, "demo", 1, "iter1 " + ("A" * 50_000))
        _write_feedback(reviews, "demo", 2, "iter2 concise content")
        result = build_cumulative_history(reviews, reports, "demo", 3)
        self.assertIn("iter2 concise content", result.text)
        self.assertIn("truncated", result.text.lower())


class TestRootCausePreambleTemplate(unittest.TestCase):
    def test_contains_fix_scope_tokens(self) -> None:
        """Must contain each of the three fix_scope labels, the example
        section heading, and the required-preamble fenced block."""
        t = ROOT_CAUSE_PREAMBLE_TEMPLATE
        for label in ("local-patch", "signature-change", "module-rework"):
            self.assertIn(label, t, f"fix-scope label '{label}' missing")
        self.assertIn("## Root cause analysis", t)
        self.assertIn("V2-R3", t)  # worked example must be embedded


class TestBuildIterDiff(TempDirTestCase):
    def _init_repo(self) -> Path:
        project = self.tmp_dir / "proj"
        (project / "src").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=project, check=True)
        subprocess.run(
            ["git", "config", "user.email", "test@test"],
            cwd=project, check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "test"],
            cwd=project, check=True,
        )
        (project / "src" / "a.ts").write_text("// iter1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=project, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "iter1"],
            cwd=project, check=True,
        )
        return project

    def test_iter_1_returns_empty(self) -> None:
        project = self._init_repo()
        self.assertEqual(build_iter_diff(project, ["src"], iteration=1), "")

    def test_iter_2_returns_diff_of_iter_1_changes(self) -> None:
        project = self._init_repo()
        # Simulate iter-2 commit that changed src/a.ts.
        (project / "src" / "a.ts").write_text(
            "// iter1\n// iter2 added this\n", encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=project, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "iter2"],
            cwd=project, check=True,
        )
        out = build_iter_diff(project, ["src"], iteration=2)
        self.assertIn("iter2 added this", out)
        self.assertIn("```diff", out)

    def test_no_changes_returns_empty(self) -> None:
        """If iter-1 commit == HEAD~1 and HEAD is an empty (no-change)
        second commit, return empty (no-signal case)."""
        project = self._init_repo()
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "empty"],
            cwd=project, check=True,
        )
        self.assertEqual(build_iter_diff(project, ["src"], iteration=2), "")

    def test_diff_scoped_to_source_dirs_excludes_test_changes(self) -> None:
        """Diff must exclude changes outside source_dirs \u2014 feedback
        files and scenarios should not show up in the implementer's
        prior-changes view."""
        project = self._init_repo()
        (project / "tests").mkdir()
        (project / "tests" / "t.ts").write_text("// test\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=project, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "iter2"],
            cwd=project, check=True,
        )
        out = build_iter_diff(project, ["src"], iteration=2)
        # No src change happened, so no diff \u2014 verifies scoping.
        self.assertEqual(out, "")


class TestBarrierAudit(TempDirTestCase):
    def test_clean_history_passes(self) -> None:
        holdouts = self.tmp_dir / "holdouts"
        holdouts.mkdir()
        (holdouts / "secret-holdout.test.ts").write_text("", encoding="utf-8")
        scenarios = self.tmp_dir / "scenarios"
        scenarios.mkdir()
        history = "iter 1 feedback: FR-7 failed. iter 2 feedback: FR-7 still failed."
        self.assertEqual(
            audit_history_for_barrier_leak(
                history, holdouts_dir=holdouts, scenarios_dir=scenarios,
            ),
            [],
        )

    def test_holdout_filename_in_history_is_violation(self) -> None:
        holdouts = self.tmp_dir / "holdouts"
        holdouts.mkdir()
        (holdouts / "secret-holdout.test.ts").write_text("", encoding="utf-8")
        scenarios = self.tmp_dir / "scenarios"
        scenarios.mkdir()
        history = "FR-7 failed in secret-holdout.test.ts"
        violations = audit_history_for_barrier_leak(
            history, holdouts_dir=holdouts, scenarios_dir=scenarios,
        )
        self.assertTrue(
            any("secret-holdout.test.ts" in v for v in violations),
            f"expected holdout name violation, got {violations!r}",
        )

    def test_forbidden_path_prefix_is_violation(self) -> None:
        holdouts = self.tmp_dir / "holdouts"
        holdouts.mkdir()
        scenarios = self.tmp_dir / "scenarios"
        scenarios.mkdir()
        history = "see /project/.dark-factory/reports/gate.md for details"
        violations = audit_history_for_barrier_leak(
            history, holdouts_dir=holdouts, scenarios_dir=scenarios,
        )
        self.assertTrue(
            any("reports" in v for v in violations),
            f"expected reports-path violation, got {violations!r}",
        )

    def test_cadence_and_classifier_rationales_not_flagged(self) -> None:
        """Cadence signal ('FR-7 mentioned 3 times') and classifier
        rationales are legitimate signal, NOT leaks \u2014 the scenario
        directory is already visible, and classifier rationales derive
        from tests the implementer can already read."""
        holdouts = self.tmp_dir / "holdouts"
        holdouts.mkdir()
        scenarios = self.tmp_dir / "scenarios"
        scenarios.mkdir()
        history = (
            "[FR-7] IMPL_BUG: test fails because implementation rejects"
            " valid input.\n[FR-7] IMPL_BUG: same failure in iter 2."
            " [FR-7] IMPL_BUG: same failure in iter 3."
        )
        self.assertEqual(
            audit_history_for_barrier_leak(
                history, holdouts_dir=holdouts, scenarios_dir=scenarios,
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
