"""Tests for darkfactory.spec.precheck_runs."""

import json
import tempfile
import unittest
from pathlib import Path

from darkfactory.spec.precheck_runs import record_precheck_run, spec_hash


class TestPrecheckRuns(unittest.TestCase):
    def test_initial_run_records_count_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            count = record_precheck_run(project, "spec-a", "hash1")
            self.assertEqual(count, 1)
            data = json.loads(
                (project / ".dark-factory" / "state" / "spec-a" / "precheck_runs.json").read_text()
            )
            self.assertEqual(data["spec_hash"], "hash1")
            self.assertEqual(data["count"], 1)
            self.assertIn("first_run", data)
            self.assertIn("last_run", data)

    def test_same_hash_increments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            record_precheck_run(project, "s", "h")
            record_precheck_run(project, "s", "h")
            count = record_precheck_run(project, "s", "h")
            self.assertEqual(count, 3)

    def test_hash_change_resets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            record_precheck_run(project, "s", "h1")
            record_precheck_run(project, "s", "h1")
            count = record_precheck_run(project, "s", "h2")
            self.assertEqual(count, 1)

    def test_spec_hash_stable(self) -> None:
        self.assertEqual(spec_hash(b"abc"), spec_hash(b"abc"))
        self.assertNotEqual(spec_hash(b"abc"), spec_hash(b"abcd"))


if __name__ == "__main__":
    unittest.main()
