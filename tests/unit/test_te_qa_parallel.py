"""F Move 2b — Test Engineer / QA Holdout scenario-generation parallelization.

These tests exercise the REAL concurrency machinery, not source inspection and
not a mocked-away path:

1. The shared host-write lock (``locked_append_text``) is hammered from many
   threads through all THREE real writers (``append_jsonl``, ``_emit_telemetry``,
   ``audit_log``) with >8 KB records; we assert no torn/interleaved lines.

2. The real ``_step_scenario_generation`` fan-out is driven with the two agent
   bodies (``_invoke_qa_holdout`` / ``_invoke_test_engineer_generation``) replaced
   by lightweight stubs — but the ThreadPoolExecutor, the join-on-with-exit, the
   ``qa_future.result()`` error surfacing, and the serial/parallel gating are the
   REAL orchestrator code. Stubs only stand in for the Docker agent calls (no
   Docker in unit tests); the concurrency path itself is genuine.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

import darkfactory.pipeline.orchestrator as orch
from darkfactory.audit.logger import audit_log
from darkfactory.errors import DarkFactoryError
from darkfactory.models.config import PipelineConfig
from darkfactory.models.pipeline import SpecRun
from darkfactory.pipeline.agents import _emit_telemetry
from darkfactory.utils.json_helpers import append_jsonl, locked_append_text


# --------------------------------------------------------------------------- #
# 1. Concurrent host-write atomicity (the real lock, the real writers)
# --------------------------------------------------------------------------- #

# A payload bigger than the TextIOWrapper buffer (~8 KB) so that WITHOUT the
# lock a single logical record would be split across multiple write() syscalls
# and could interleave with another thread's record.
_BIG = "x" * 20_000


def _hammer(target, n_threads: int, n_each: int) -> None:
    barrier = threading.Barrier(n_threads)

    def worker(tid: int) -> None:
        barrier.wait()  # maximize contention: all threads start together
        for i in range(n_each):
            target(tid, i)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_append_jsonl_no_torn_lines_under_concurrency(tmp_path: Path):
    path = tmp_path / "events.jsonl"
    n_threads, n_each = 12, 40

    def emit(tid: int, i: int) -> None:
        append_jsonl(path, {"tid": tid, "i": i, "blob": _BIG})

    _hammer(emit, n_threads, n_each)

    lines = path.read_text().splitlines()
    assert len(lines) == n_threads * n_each
    seen = set()
    for line in lines:
        rec = json.loads(line)  # raises if any line is torn/interleaved
        assert rec["blob"] == _BIG
        seen.add((rec["tid"], rec["i"]))
    assert len(seen) == n_threads * n_each  # no record lost or duplicated


def test_emit_telemetry_no_torn_lines_under_concurrency(tmp_path: Path):
    n_threads, n_each = 12, 30

    def emit(tid: int, i: int) -> None:
        _emit_telemetry(
            tmp_path,
            run_id="r",
            spec_name=_BIG,  # oversized field forces a multi-syscall write
            role="test_engineer" if tid % 2 else "qa_holdout",
            iteration=i,
            phase="generate",
            wall_clock_ms=1,
            usage={
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            exit_code=0,
            model_id="m",
        )

    _hammer(emit, n_threads, n_each)

    path = tmp_path / ".dark-factory" / "reports" / "run-telemetry.jsonl"
    lines = path.read_text().splitlines()
    assert len(lines) == n_threads * n_each
    for line in lines:
        rec = json.loads(line)
        assert rec["spec_name"] == _BIG


def test_audit_log_no_torn_lines_under_concurrency(tmp_path: Path):
    path = tmp_path / "audit.log"
    n_threads, n_each = 12, 40

    def emit(tid: int, i: int) -> None:
        audit_log(path, f"CONTAINER t{tid} i{i} {_BIG}")

    _hammer(emit, n_threads, n_each)

    lines = path.read_text().splitlines()
    assert len(lines) == n_threads * n_each
    # Every line is a complete record: starts with a timestamp bracket and ends
    # with the full big payload (a torn line would be missing one or the other).
    for line in lines:
        assert line.startswith("[")
        assert line.endswith(_BIG)


def test_all_three_writers_interleaved_stay_intact(tmp_path: Path):
    """All three writers hammered together against the SAME shared lock —
    proves cross-writer serialization (one global lock, not one per file)."""
    events = tmp_path / "events.jsonl"
    audit = tmp_path / "audit.log"

    def mixed(tid: int, i: int) -> None:
        if tid % 3 == 0:
            append_jsonl(events, {"tid": tid, "i": i, "blob": _BIG})
        elif tid % 3 == 1:
            audit_log(audit, f"X t{tid} i{i} {_BIG}")
        else:
            _emit_telemetry(
                tmp_path, run_id="r", spec_name=_BIG, role="qa_holdout",
                iteration=i, phase="generate", wall_clock_ms=1,
                usage={"input_tokens": 0, "output_tokens": 0,
                       "cache_read_input_tokens": 0,
                       "cache_creation_input_tokens": 0},
                exit_code=0, model_id="m",
            )

    _hammer(mixed, 12, 30)

    for line in events.read_text().splitlines():
        json.loads(line)
    for line in audit.read_text().splitlines():
        assert line.startswith("[") and line.endswith(_BIG)
    telem = tmp_path / ".dark-factory" / "reports" / "run-telemetry.jsonl"
    for line in telem.read_text().splitlines():
        json.loads(line)


def _make_overlap_probe():
    """A file-object wrapper whose ``write`` widens its own window and records
    the max number of threads simultaneously inside it. Returns (state, factory)
    where factory(real_file) -> probe."""
    import time as _t
    state = {"inside": 0, "max": 0, "lock": threading.Lock()}

    class _Probe:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return self.f.__exit__(*a)

        def write(self, s):
            with state["lock"]:
                state["inside"] += 1
                state["max"] = max(state["max"], state["inside"])
            _t.sleep(0.003)  # widen the critical window so overlap is observable
            r = self.f.write(s)
            with state["lock"]:
                state["inside"] -= 1
            return r

    return state, _Probe


def test_locked_append_text_serializes_writes(tmp_path: Path, monkeypatch):
    """DETERMINISTIC proof the shared lock provides mutual exclusion (not just a
    timing-dependent smoke test). With the write window artificially widened, no
    two threads are ever inside ``locked_append_text``'s critical section at once.
    The control (a bare unlocked append) DOES overlap — proving the probe really
    detects the failure the lock prevents, so this test cannot silently pass
    against a broken (lock-removed) implementation."""
    import darkfactory.utils.json_helpers as jh
    # INVARIANT: capture the un-patched Path.open BEFORE any monkeypatch below,
    # so BOTH branches wrap the real file object (not a probe). Moving this
    # capture below a setattr would make the control branch wrap a probe and
    # render the test vacuous.
    real_open = Path.open

    # (1) the REAL locked writer — must NEVER overlap.
    state, Probe = _make_overlap_probe()
    monkeypatch.setattr(
        Path, "open", lambda self, *a, **k: Probe(real_open(self, *a, **k))
    )
    _hammer(
        lambda tid, i: jh.locked_append_text(tmp_path / "locked.log", f"{tid}-{i}\n"),
        8, 8,
    )
    assert state["max"] == 1, f"lock failed: {state['max']} writers were concurrent"

    # (2) control — an UNLOCKED append MUST overlap, else the probe proves nothing.
    state2, Probe2 = _make_overlap_probe()
    monkeypatch.setattr(
        Path, "open", lambda self, *a, **k: Probe2(real_open(self, *a, **k))
    )

    def unlocked(tid: int, i: int) -> None:
        with (tmp_path / "unlocked.log").open("a") as f:
            f.write(f"{tid}-{i}\n")

    _hammer(unlocked, 8, 8)
    assert state2["max"] > 1, "control never overlapped — the probe can't detect the bug"


def test_leaf_writers_share_one_lock_no_deadlock(tmp_path: Path):
    """The three public writers all bottom out in the single shared lock and
    never nest acquisition (a sequential call through all three must not hang)."""
    p = tmp_path / "x.log"
    locked_append_text(p, "a\n")
    append_jsonl(tmp_path / "y.jsonl", {"k": 1})
    audit_log(tmp_path / "z.log", "msg")
    assert p.read_text() == "a\n"


# --------------------------------------------------------------------------- #
# 2. Real fan-out orchestration in _step_scenario_generation
# --------------------------------------------------------------------------- #


def _mk_spec_run() -> SpecRun:
    return SpecRun(spec_name="demo", run_id="run123")


@pytest.fixture
def fanout_env(tmp_path: Path, monkeypatch):
    """Drive the REAL _step_scenario_generation fan-out with the two agent
    bodies and step_begin stubbed. Returns a recorder the test inspects."""
    rec: dict = {
        "order": [],
        "qa_started": threading.Event(),
        "qa_done": threading.Event(),
        "te_observed_qa_started": None,
        "te_observed_qa_done": None,
        "qa_thread": None,
        "te_thread": None,
        "step_begin_keys": [],
        "qa_raises": None,
        "te_raises": None,
    }

    def fake_step_begin(spec_id, step_id, config, project_dir):
        rec["step_begin_keys"].append(step_id)
        return config

    def fake_qa(spec_run, config, barriers, project_dir, *, holdouts_dir,
                events_file, audit_path, dry_run):
        rec["order"].append("qa")
        rec["qa_thread"] = threading.current_thread().name
        rec["qa_started"].set()
        # Hold long enough that the TE stub can observe QA is mid-flight.
        import time as _t
        _t.sleep(0.4)
        rec["qa_done"].set()
        if rec["qa_raises"] is not None:
            raise rec["qa_raises"]

    def fake_te(spec_run, config, barriers, project_dir, *, scenarios_dir,
                events_file, audit_path, dry_run, active_adapters):
        rec["order"].append("te")
        rec["te_thread"] = threading.current_thread().name
        # In the parallel path, observe whether QA is concurrently in-flight.
        if config.scenario_parallel_generation and not dry_run:
            assert rec["qa_started"].wait(timeout=3.0), "QA never started"
            rec["te_observed_qa_started"] = True
            rec["te_observed_qa_done"] = rec["qa_done"].is_set()
        if rec["te_raises"] is not None:
            raise rec["te_raises"]

    monkeypatch.setattr(orch, "step_begin", fake_step_begin)
    monkeypatch.setattr(orch, "_invoke_qa_holdout", fake_qa)
    monkeypatch.setattr(orch, "_invoke_test_engineer_generation", fake_te)
    # Force the fan-out branch: no sentinel, no pre-existing scenario files.
    monkeypatch.setattr(orch, "has_sentinel_file", lambda d: False)
    monkeypatch.setattr(orch, "has_scenario_files", lambda d: False)

    def run(config: PipelineConfig):
        orch._step_scenario_generation(
            _mk_spec_run(), config, {}, tmp_path,
            regen=False, events_file=None, audit_path=None, dry_run=False,
            active_adapters=[],
        )

    return rec, run


def test_parallel_branch_genuinely_overlaps(fanout_env):
    rec, run = fanout_env
    config = PipelineConfig()
    config.scenario_parallel_generation = True
    run(config)
    # The TE branch observed QA already running AND not yet finished → real
    # overlap, not serialization.
    assert rec["te_observed_qa_started"] is True
    assert rec["te_observed_qa_done"] is False
    # QA ran on a worker thread, TE on the main thread.
    assert rec["qa_thread"] != "MainThread"
    assert rec["te_thread"] == "MainThread"
    # Both step_begin keys freshened serially, before any agent body.
    assert rec["step_begin_keys"] == ["test_engineer", "qa_holdout"]


def test_parallel_te_failure_still_joins_qa_no_orphan(fanout_env):
    rec, run = fanout_env
    config = PipelineConfig()
    config.scenario_parallel_generation = True
    rec["te_raises"] = DarkFactoryError("te boom")
    with pytest.raises(DarkFactoryError, match="te boom"):
        run(config)
    # The with-exit joined QA before the TE exception propagated: QA completed.
    assert rec["qa_done"].is_set()


def test_parallel_qa_unexpected_error_surfaces_after_te_success(fanout_env):
    rec, run = fanout_env
    config = PipelineConfig()
    config.scenario_parallel_generation = True
    rec["qa_raises"] = ValueError("qa boom")
    with pytest.raises(ValueError, match="qa boom"):
        run(config)


def test_parallel_te_error_not_masked_by_concurrent_qa_error(fanout_env):
    rec, run = fanout_env
    config = PipelineConfig()
    config.scenario_parallel_generation = True
    rec["te_raises"] = DarkFactoryError("te wins")
    rec["qa_raises"] = ValueError("qa loses")
    # TE is the gating role: its exception must propagate, not QA's.
    with pytest.raises(DarkFactoryError, match="te wins"):
        run(config)
    assert rec["qa_done"].is_set()  # QA still joined


def test_serial_branch_when_flag_off(fanout_env):
    rec, run = fanout_env
    config = PipelineConfig()
    config.scenario_parallel_generation = False
    run(config)
    # No worker thread: both bodies ran on the main thread, QA before TE.
    assert rec["order"] == ["qa", "te"]
    assert rec["qa_thread"] == "MainThread"
    assert rec["te_thread"] == "MainThread"
    assert rec["step_begin_keys"] == ["test_engineer", "qa_holdout"]


def test_dry_run_forces_serial_even_with_flag_on(tmp_path, monkeypatch):
    order = []
    threads = {}

    def fake_step_begin(spec_id, step_id, config, project_dir):
        return config

    def fake_qa(*a, **k):
        order.append("qa")
        threads["qa"] = threading.current_thread().name

    def fake_te(*a, **k):
        order.append("te")
        threads["te"] = threading.current_thread().name

    monkeypatch.setattr(orch, "step_begin", fake_step_begin)
    monkeypatch.setattr(orch, "_invoke_qa_holdout", fake_qa)
    monkeypatch.setattr(orch, "_invoke_test_engineer_generation", fake_te)
    monkeypatch.setattr(orch, "has_sentinel_file", lambda d: False)
    monkeypatch.setattr(orch, "has_scenario_files", lambda d: False)

    config = PipelineConfig()
    config.scenario_parallel_generation = True
    orch._step_scenario_generation(
        _mk_spec_run(), config, {}, tmp_path,
        regen=False, events_file=None, audit_path=None, dry_run=True,
        active_adapters=[],
    )
    assert order == ["qa", "te"]
    assert threads == {"qa": "MainThread", "te": "MainThread"}


def test_resume_te_files_existed_runs_qa_only(tmp_path, monkeypatch):
    """On resume (TE output already present) the TE body must NOT re-run; the
    QA Holdout still runs (serially), for both flag states."""
    for flag in (False, True):
        order = []

        monkeypatch.setattr(orch, "step_begin",
                            lambda s, k, c, p: c)
        monkeypatch.setattr(orch, "_invoke_qa_holdout",
                            lambda *a, **k: order.append("qa"))
        monkeypatch.setattr(orch, "_invoke_test_engineer_generation",
                            lambda *a, **k: order.append("te"))
        monkeypatch.setattr(orch, "has_sentinel_file", lambda d: False)
        monkeypatch.setattr(orch, "has_scenario_files", lambda d: True)

        config = PipelineConfig()
        config.scenario_parallel_generation = flag
        orch._step_scenario_generation(
            _mk_spec_run(), config, {}, tmp_path,
            regen=False, events_file=None, audit_path=None, dry_run=False,
            active_adapters=[],
        )
        assert order == ["qa"], f"flag={flag}: TE body must not re-run on resume"


def test_sentinel_short_circuits_before_fanout(tmp_path, monkeypatch):
    """A sentinel spec returns before any agent body or the QA step_begin."""
    order = []
    keys = []
    monkeypatch.setattr(orch, "step_begin",
                        lambda s, k, c, p: (keys.append(k), c)[1])
    monkeypatch.setattr(orch, "_invoke_qa_holdout",
                        lambda *a, **k: order.append("qa"))
    monkeypatch.setattr(orch, "_invoke_test_engineer_generation",
                        lambda *a, **k: order.append("te"))
    monkeypatch.setattr(orch, "has_sentinel_file", lambda d: True)
    # A valid sentinel so the step accepts it and short-circuits.
    monkeypatch.setattr(orch, "validate_sentinel", lambda p: (True, ""))
    monkeypatch.setattr(orch, "get_sentinel_path", lambda d: d)

    config = PipelineConfig()
    config.scenario_parallel_generation = True
    orch._step_scenario_generation(
        _mk_spec_run(), config, {}, tmp_path,
        regen=False, events_file=None, audit_path=None, dry_run=False,
        active_adapters=[],
    )
    assert order == []  # neither agent ran
    assert keys == ["test_engineer"]  # QA step_begin never reached
