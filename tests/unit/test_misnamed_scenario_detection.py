"""F Move 2c (2026-06-16): find_misnamed_scenario_files detector.

Run 2f013c56's test-engineer wrote valid-content scenario files as plain `.js`
(`fr-1-workspace-install.js`) instead of `*.test.ts`; the runner won't collect
them and `has_scenario_files` (correctly) returns False, so the empty-retry
loop deleted+regenerated them 4× then aborted "no scenario files written"
despite real files on disk. This detector identifies exactly those misnamed
files so the warm-session retry can feed a deterministic rename corrective.

The detector MUST be false-positive-free (a false positive triggers spurious
corrective retries), so it is scoped to code extensions and excludes the
legitimate non-test artifacts the TE writes.
"""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.scenarios.detection import (
    find_misnamed_scenario_files,
    has_scenario_files,
)


class TestFindMisnamedScenarioFiles(unittest.TestCase):
    def _dir(self) -> Path:
        self._td = TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        d = Path(self._td.name) / "scen"
        d.mkdir()
        return d

    def test_empty_dir_returns_empty(self):
        self.assertEqual(find_misnamed_scenario_files(self._dir()), [])

    def test_missing_dir_returns_empty(self):
        self.assertEqual(
            find_misnamed_scenario_files(Path("/nonexistent/xyz")), []
        )

    def test_run_2f013c56_case_dot_js_flagged(self):
        d = self._dir()
        (d / "fr-1-workspace-install.js").write_text("x")
        (d / "ac-6-ci-duration.js").write_text("x")
        self.assertEqual(
            find_misnamed_scenario_files(d),
            ["ac-6-ci-duration.js", "fr-1-workspace-install.js"],
        )

    def test_recognized_test_files_not_flagged(self):
        """The complement invariant: anything has_scenario_files accepts is
        never flagged misnamed."""
        d = self._dir()
        (d / "good.test.ts").write_text("x")
        (d / "x.spec.js").write_text("x")
        (d / "y.scenarios.md").write_text("x")
        self.assertTrue(has_scenario_files(d))
        self.assertEqual(find_misnamed_scenario_files(d), [])

    def test_tier1_prefix_excluded(self):
        d = self._dir()
        (d / "00-tier1-contracts.test.ts").write_text("x")
        self.assertEqual(find_misnamed_scenario_files(d), [])

    def test_helper_dirs_and_artifacts_excluded(self):
        d = self._dir()
        (d / "_contracts").mkdir()
        (d / "_contracts" / "helper.ts").write_text("x")
        (d / "_df_setup").mkdir()
        (d / "_df_setup" / "reset.ts").write_text("x")
        (d / "COVERAGE-GAPS.md").write_text("x")
        (d / "SCENARIOS-NOT-APPLICABLE.md").write_text("x")
        (d / ".hidden.js").write_text("x")
        self.assertEqual(find_misnamed_scenario_files(d), [])

    def test_plain_md_doc_not_flagged_false_positive_guard(self):
        """A non-code artifact (plain .md) must NOT be flagged — that would
        trigger spurious corrective retries. Markdown scenarios are recognized
        only when correctly named (*.scenarios.md etc.)."""
        d = self._dir()
        (d / "design-notes.md").write_text("x")
        self.assertEqual(find_misnamed_scenario_files(d), [])

    def test_mixed_good_and_misnamed_returns_only_misnamed(self):
        d = self._dir()
        (d / "good.test.ts").write_text("x")
        (d / "bad.js").write_text("x")
        self.assertEqual(find_misnamed_scenario_files(d), ["bad.js"])

    def test_nested_misnamed_returns_relative_path(self):
        d = self._dir()
        (d / "acceptance").mkdir()
        (d / "acceptance" / "flow.js").write_text("x")
        self.assertEqual(
            find_misnamed_scenario_files(d), [str(Path("acceptance") / "flow.js")]
        )


if __name__ == "__main__":
    unittest.main()
