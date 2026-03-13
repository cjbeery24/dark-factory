"""Tests for state/store.py — file-based KV store."""

import threading
import unittest

from tests.helpers import TempDirTestCase
from darkfactory.state.store import read_state, write_state, write_state_or_warn


class TestStateStore(TempDirTestCase):
    def test_write_and_read(self) -> None:
        state_file = self.tmp_dir / "test.state"
        write_state(state_file, "status", "running")
        self.assertEqual(read_state(state_file, "status"), "running")

    def test_read_missing_file(self) -> None:
        self.assertEqual(read_state(self.tmp_dir / "nope.state", "key"), "")

    def test_read_missing_key(self) -> None:
        state_file = self.tmp_dir / "test.state"
        write_state(state_file, "status", "running")
        self.assertEqual(read_state(state_file, "other"), "")

    def test_overwrite_key(self) -> None:
        state_file = self.tmp_dir / "test.state"
        write_state(state_file, "step", "1")
        write_state(state_file, "step", "2")
        self.assertEqual(read_state(state_file, "step"), "2")
        # Verify only one line with this key.
        lines = [line for line in state_file.read_text().splitlines() if line.startswith("step=")]
        self.assertEqual(len(lines), 1)

    def test_multiple_keys(self) -> None:
        state_file = self.tmp_dir / "test.state"
        write_state(state_file, "a", "1")
        write_state(state_file, "b", "2")
        write_state(state_file, "c", "3")
        self.assertEqual(read_state(state_file, "a"), "1")
        self.assertEqual(read_state(state_file, "b"), "2")
        self.assertEqual(read_state(state_file, "c"), "3")

    def test_concurrent_writes(self) -> None:
        state_file = self.tmp_dir / "concurrent.state"
        errors: list[Exception] = []

        def writer(key: str, value: str) -> None:
            try:
                write_state(state_file, key, value)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=writer, args=(f"key{i}", f"val{i}"))
            for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0)
        for i in range(10):
            self.assertEqual(read_state(state_file, f"key{i}"), f"val{i}")

    def test_value_containing_equals(self) -> None:
        state_file = self.tmp_dir / "eq.state"
        write_state(state_file, "cmd", "FOO=bar=baz")
        self.assertEqual(read_state(state_file, "cmd"), "FOO=bar=baz")

    def test_write_state_or_warn(self) -> None:
        state_file = self.tmp_dir / "warn.state"
        write_state_or_warn(state_file, "ok", "yes")
        self.assertEqual(read_state(state_file, "ok"), "yes")

    def test_write_state_or_warn_on_failure(self) -> None:
        """write_state_or_warn should log warning, not raise, on failure."""
        # Use a path that can't be written to.
        bad_path = self.tmp_dir / "readonly" / "nested" / "test.state"
        (self.tmp_dir / "readonly").mkdir()
        (self.tmp_dir / "readonly").chmod(0o444)
        try:
            # Should not raise.
            write_state_or_warn(bad_path, "key", "val")
        finally:
            (self.tmp_dir / "readonly").chmod(0o755)


if __name__ == "__main__":
    unittest.main()
