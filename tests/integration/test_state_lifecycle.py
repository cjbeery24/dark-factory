"""Integration tests for state persistence, locking, and registry."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from darkfactory.state.store import read_state, write_state
from darkfactory.state.lock import FileLock, LockError
from darkfactory.state import registry


@pytest.mark.integration
class TestStateRoundTrip:
    """State write/read with real filesystem."""

    def test_write_read_roundtrip(self, project_dir: Path):
        state_path = project_dir / ".dark-factory" / "state" / "test.state"
        write_state(state_path, "status", "running")
        write_state(state_path, "iteration", "2")
        write_state(state_path, "run_id", "abc123")

        assert read_state(state_path, "status") == "running"
        assert read_state(state_path, "iteration") == "2"
        assert read_state(state_path, "run_id") == "abc123"

    def test_overwrite_existing_key(self, project_dir: Path):
        state_path = project_dir / ".dark-factory" / "state" / "test.state"
        write_state(state_path, "status", "running")
        write_state(state_path, "status", "shipped")
        assert read_state(state_path, "status") == "shipped"

    def test_read_missing_key_returns_empty(self, project_dir: Path):
        state_path = project_dir / ".dark-factory" / "state" / "test.state"
        write_state(state_path, "status", "running")
        assert read_state(state_path, "nonexistent") == ""

    def test_read_missing_file_returns_empty(self, project_dir: Path):
        state_path = project_dir / ".dark-factory" / "state" / "nonexistent.state"
        assert read_state(state_path, "status") == ""

    def test_multiple_keys_preserved(self, project_dir: Path):
        state_path = project_dir / ".dark-factory" / "state" / "test.state"
        write_state(state_path, "a", "1")
        write_state(state_path, "b", "2")
        write_state(state_path, "c", "3")
        # Updating one key preserves the others
        write_state(state_path, "b", "updated")
        assert read_state(state_path, "a") == "1"
        assert read_state(state_path, "b") == "updated"
        assert read_state(state_path, "c") == "3"


@pytest.mark.integration
class TestFileLock:
    """FileLock with real filesystem locking."""

    def test_lock_acquire_release(self, tmp_path: Path):
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path, timeout=5.0)
        lock.acquire()
        assert lock_path.exists()
        lock.release()
        assert not lock_path.exists()

    def test_lock_context_manager(self, tmp_path: Path):
        lock_path = tmp_path / "test.lock"
        with FileLock(lock_path, timeout=5.0):
            assert lock_path.exists()
        assert not lock_path.exists()

    def test_concurrent_threads_serialize(self, tmp_path: Path):
        lock_path = tmp_path / "test.lock"
        results: list[str] = []
        barrier = threading.Barrier(2)

        def writer(name: str):
            barrier.wait()
            with FileLock(lock_path, timeout=10.0):
                results.append(f"{name}-start")
                time.sleep(0.05)
                results.append(f"{name}-end")

        t1 = threading.Thread(target=writer, args=("t1",))
        t2 = threading.Thread(target=writer, args=("t2",))
        t1.start()
        t2.start()
        t1.join(timeout=15)
        t2.join(timeout=15)

        assert len(results) == 4
        # Verify serialization: one thread's start-end pair is contiguous
        if results[0] == "t1-start":
            assert results[1] == "t1-end"
        else:
            assert results[0] == "t2-start"
            assert results[1] == "t2-end"

    def test_lock_timeout(self, tmp_path: Path):
        lock_path = tmp_path / "test.lock"
        lock1 = FileLock(lock_path, timeout=5.0)
        lock1.acquire()

        lock2 = FileLock(lock_path, timeout=0.2, poll_interval=0.05)
        with pytest.raises(LockError, match="timeout"):
            lock2.acquire()

        lock1.release()

    def test_stale_lock_cleanup(self, tmp_path: Path):
        lock_path = tmp_path / "test.lock"
        # Create a stale lock with a dead PID. The lock is detected as stale
        # because PID 999999999 does not exist (ProcessLookupError), not because
        # of any timeout — _is_stale checks PID liveness only.
        lock_path.mkdir()
        (lock_path / "pid").write_text("999999999")

        lock = FileLock(lock_path, timeout=5.0)
        lock.acquire()
        assert lock._held
        lock.release()

    def test_release_when_not_held(self, tmp_path: Path):
        lock_path = tmp_path / "test.lock"
        lock = FileLock(lock_path, timeout=5.0)
        # release() when not held is a no-op, not an error
        lock.release()


@pytest.mark.integration
class TestRegistry:
    """Scenario registry with real filesystem operations."""

    def _setup_scenarios(self, project_dir: Path, spec_name: str, count: int = 3):
        """Create dummy scenario files for a spec."""
        scenario_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
        scenario_dir.mkdir(parents=True, exist_ok=True)
        for i in range(count):
            (scenario_dir / f"scenario_{i}.yaml").write_text(
                f"name: test_{i}\ncategory: happy_path\n"
            )

    def test_register_and_list(self, project_dir: Path):
        self._setup_scenarios(project_dir, "add-multiply")
        assert registry.register(project_dir, "add-multiply")
        specs = registry.list_specs(project_dir)
        assert "add-multiply" in specs

    def test_register_copies_files(self, project_dir: Path):
        self._setup_scenarios(project_dir, "add-multiply", count=3)
        registry.register(project_dir, "add-multiply")
        reg_dir = registry.scenario_dir(project_dir, "add-multiply")
        assert reg_dir is not None
        files = [f for f in reg_dir.iterdir() if not f.name.startswith(".")]
        assert len(files) == 3

    def test_register_writes_metadata(self, project_dir: Path):
        self._setup_scenarios(project_dir, "add-multiply")
        registry.register(project_dir, "add-multiply")
        meta = registry.read_meta(project_dir, "add-multiply")
        assert meta["spec"] == "add-multiply"
        assert "registered" in meta
        assert meta["scenario_count"] == "3"

    def test_deregister(self, project_dir: Path):
        self._setup_scenarios(project_dir, "add-multiply")
        registry.register(project_dir, "add-multiply")
        assert registry.deregister(project_dir, "add-multiply")
        assert "add-multiply" not in registry.list_specs(project_dir)

    def test_deregister_nonexistent_returns_false(self, project_dir: Path):
        assert not registry.deregister(project_dir, "nonexistent")

    def test_list_empty(self, project_dir: Path):
        assert registry.list_specs(project_dir) == []

    def test_register_idempotent(self, project_dir: Path):
        self._setup_scenarios(project_dir, "add-multiply")
        registry.register(project_dir, "add-multiply")
        registry.register(project_dir, "add-multiply")
        assert registry.list_specs(project_dir).count("add-multiply") == 1

    def test_register_no_scenarios_returns_false(self, project_dir: Path):
        assert not registry.register(project_dir, "empty-spec")
