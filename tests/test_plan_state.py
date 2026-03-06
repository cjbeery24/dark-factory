"""Tests for state/plan_checkpoint.py — plan checkpoint JSONL."""

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.state import plan_checkpoint


class TestCheckpointPath(unittest.TestCase):
    def test_format(self) -> None:
        path = plan_checkpoint.checkpoint_path(
            Path("/tmp/state"), "my-plan", "abcdef1234567890extra"
        )
        self.assertEqual(path.name, "plan-my-plan-abcdef1234567890.jsonl")


class TestWrite(TempDirTestCase):
    def test_creates_file(self) -> None:
        state_dir = self.tmp_dir / "state"
        state_dir.mkdir()
        path = plan_checkpoint.write(
            state_dir, "plan1", "run123", "spec-a", "shipped", 0,
        )
        self.assertTrue(path.exists())
        records = plan_checkpoint.all_records(path)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["spec"], "spec-a")
        self.assertEqual(records[0]["status"], "shipped")
        self.assertEqual(records[0]["exit_code"], 0)
        self.assertIn("ts", records[0])
        self.assertIn("run_id", records[0])

    def test_appends_records(self) -> None:
        state_dir = self.tmp_dir / "state"
        state_dir.mkdir()
        plan_checkpoint.write(state_dir, "p", "r1", "a", "shipped", 0)
        plan_checkpoint.write(state_dir, "p", "r1", "b", "failed", 1)
        path = plan_checkpoint.checkpoint_path(state_dir, "p", "r1")
        records = plan_checkpoint.all_records(path)
        self.assertEqual(len(records), 2)

    def test_extra_fields(self) -> None:
        state_dir = self.tmp_dir / "state"
        state_dir.mkdir()
        plan_checkpoint.write(
            state_dir, "p", "r1", "a", "shipped", 0,
            extra={"wall_s": 42, "pr_url": "https://example.com/pr/1"},
        )
        path = plan_checkpoint.checkpoint_path(state_dir, "p", "r1")
        records = plan_checkpoint.all_records(path)
        self.assertEqual(records[0]["wall_s"], 42)
        self.assertEqual(records[0]["pr_url"], "https://example.com/pr/1")

    def test_spec_hash(self) -> None:
        state_dir = self.tmp_dir / "state"
        state_dir.mkdir()
        spec_file = self.tmp_dir / "spec.md"
        spec_file.write_text("# My Spec\n")
        plan_checkpoint.write(
            state_dir, "p", "r1", "a", "shipped", 0,
            spec_path=spec_file,
        )
        path = plan_checkpoint.checkpoint_path(state_dir, "p", "r1")
        records = plan_checkpoint.all_records(path)
        self.assertTrue(len(records[0]["spec_hash"]) > 0)


class TestLoadLatest(TempDirTestCase):
    def test_no_checkpoints(self) -> None:
        state_dir = self.tmp_dir / "state"
        state_dir.mkdir()
        result = plan_checkpoint.load_latest(state_dir, "plan1")
        self.assertIsNone(result)

    def test_single_checkpoint(self) -> None:
        state_dir = self.tmp_dir / "state"
        state_dir.mkdir()
        plan_checkpoint.write(state_dir, "plan1", "run1", "a", "shipped", 0)
        result = plan_checkpoint.load_latest(state_dir, "plan1")
        self.assertIsNotNone(result)

    def test_picks_latest(self) -> None:
        state_dir = self.tmp_dir / "state"
        state_dir.mkdir()
        # Use the same run_id so both go to the same checkpoint file,
        # then verify both records are present.
        plan_checkpoint.write(state_dir, "plan1", "run1aaaaaaaaaaaaa", "a", "failed", 1)
        plan_checkpoint.write(state_dir, "plan1", "run1aaaaaaaaaaaaa", "b", "shipped", 0)
        result = plan_checkpoint.load_latest(state_dir, "plan1")
        self.assertIsNotNone(result)
        records = plan_checkpoint.all_records(result)
        self.assertEqual(len(records), 2)


class TestCompletedSpecs(TempDirTestCase):
    def test_shipped_and_budget_skipped(self) -> None:
        state_dir = self.tmp_dir / "state"
        state_dir.mkdir()
        plan_checkpoint.write(state_dir, "p", "r1", "a", "shipped", 0)
        plan_checkpoint.write(state_dir, "p", "r1", "b", "failed", 1)
        plan_checkpoint.write(state_dir, "p", "r1", "c", "budget_skipped", 0)
        plan_checkpoint.write(state_dir, "p", "r1", "d", "escalated", 1)
        path = plan_checkpoint.checkpoint_path(state_dir, "p", "r1")
        completed = plan_checkpoint.completed_specs(path)
        self.assertEqual(completed, {"a", "c"})


class TestSpecHash(TempDirTestCase):
    def test_returns_hash(self) -> None:
        state_dir = self.tmp_dir / "state"
        state_dir.mkdir()
        spec_file = self.tmp_dir / "spec.md"
        spec_file.write_text("content")
        plan_checkpoint.write(
            state_dir, "p", "r1", "a", "shipped", 0, spec_path=spec_file,
        )
        path = plan_checkpoint.checkpoint_path(state_dir, "p", "r1")
        h = plan_checkpoint.spec_hash(path, "a")
        self.assertTrue(len(h) == 64)  # SHA-256 hex

    def test_missing_spec(self) -> None:
        state_dir = self.tmp_dir / "state"
        state_dir.mkdir()
        plan_checkpoint.write(state_dir, "p", "r1", "a", "shipped", 0)
        path = plan_checkpoint.checkpoint_path(state_dir, "p", "r1")
        h = plan_checkpoint.spec_hash(path, "nonexistent")
        self.assertEqual(h, "")


if __name__ == "__main__":
    unittest.main()
