"""Unit tests for the agent stream-summarizer (observability)."""

from __future__ import annotations

import json

import pytest

from darkfactory.observability.stream_summary import (
    _percentile,
    append_metrics_line,
    find_sidecars,
    summarize_stream,
)


# ---------------------------------------------------------------------------
# Synthetic sidecar builders
# ---------------------------------------------------------------------------


def _rec(t: float, event: dict) -> str:
    """One {"t","line"} sidecar record, line-encoded as the writer produces it."""
    return json.dumps({"t": round(t, 3), "line": json.dumps(event)})


def _assistant(*, thinking: bool = False, tool_uses=None, text: str | None = None,
               usage: dict | None = None) -> dict:
    content = []
    if thinking:
        content.append({"type": "thinking", "thinking": "..."})
    if text is not None:
        content.append({"type": "text", "text": text})
    for tu in (tool_uses or []):
        content.append({"type": "tool_use", **tu})
    msg: dict = {"content": content}
    if usage is not None:
        msg["usage"] = usage
    return {"type": "assistant", "message": msg}


def _user_tool_result(tool_use_id: str, content) -> dict:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": content},
    ]}}


def _result(usage: dict) -> dict:
    return {"type": "result", "subtype": "success", "usage": usage}


def _write_sidecar(tmp_path, lines, name="stream_test-engineer_myspec_8c5cfb67_it1_validate_attempt1_p123s0.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Percentile helper
# ---------------------------------------------------------------------------


def test_percentile_basic():
    vals = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert _percentile(vals, 0.0) == 0.0
    assert _percentile(vals, 1.0) == 4.0
    assert _percentile(vals, 0.5) == 2.0
    # linear interpolation between rank 3 (=3.0) and 4 (=4.0) at k=3.6
    assert _percentile(vals, 0.9) == pytest.approx(3.6)


def test_percentile_empty_and_singleton():
    assert _percentile([], 0.5) == 0.0
    assert _percentile([7.0], 0.9) == 7.0


# ---------------------------------------------------------------------------
# Clean, complete stream — exact metrics
# ---------------------------------------------------------------------------


def test_complete_stream_exact_metrics(tmp_path):
    # Timeline (t seconds):
    #  0.0 system init
    #  1.0 assistant (thinking) -> gap 1.0 thinking
    #  1.5 assistant (thinking + Bash tool_use) -> gap 0.5 thinking
    #  3.5 user tool_result for the Bash -> gap 2.0 tool
    #  4.0 assistant (Write scenario) -> gap 0.5 (no thinking) -> idle
    #  4.2 assistant (Read, no thinking) -> gap 0.2 -> idle
    #  4.3 result
    lines = [
        _rec(0.0, {"type": "system", "subtype": "init"}),
        _rec(1.0, _assistant(thinking=True, usage={"output_tokens": 10})),
        _rec(1.5, _assistant(thinking=True, tool_uses=[
            {"id": "t1", "name": "Bash", "input": {"command": "ls"}}],
            usage={"output_tokens": 20})),
        _rec(3.5, _user_tool_result("t1", "file listing")),
        _rec(4.0, _assistant(tool_uses=[
            {"id": "t2", "name": "Write",
             "input": {"file_path": "tests/foo.test.ts", "content": "x"}}])),
        _rec(4.2, _assistant(tool_uses=[
            {"id": "t3", "name": "Read", "input": {"file_path": "src/a.ts"}}])),
        _rec(4.3, _result({"input_tokens": 100, "output_tokens": 30,
                           "cache_read_input_tokens": 5000,
                           "cache_creation_input_tokens": 200})),
    ]
    p = _write_sidecar(tmp_path, lines)
    s = summarize_stream(p)

    assert s["outcome"] == "completed"
    assert s["truncated"] is False
    assert s["actual_turns"] == 4          # four assistant messages
    assert s["thinking_turns"] == 2
    # thinking_seconds = gap before each thinking turn = 1.0 + 0.5
    assert s["thinking_seconds"] == pytest.approx(1.5)
    # tool_seconds = Bash tool_use(1.5) -> tool_result(3.5) = 2.0
    assert s["tool_seconds"] == pytest.approx(2.0)
    # wall = last event t = 4.3; idle = 4.3 - 1.5 - 2.0 = 0.8
    assert s["wall_seconds"] == pytest.approx(4.3)
    assert s["idle_seconds"] == pytest.approx(0.8)
    assert s["tool_calls"] == {"Bash": 1, "Write": 1, "Read": 1}
    assert s["files_written"] == 1         # only the Write path (Read isn't a write)
    assert s["tokens"] == {"input": 100, "output": 30,
                           "cache_read": 5000, "cache_creation": 200}
    # tsc was never run (Bash command was "ls") -> null
    assert s["tsc"] is None
    # metadata recovered from filename
    assert s["role"] == "test-engineer"
    assert s["phase"] == "validate"
    assert s["iteration"] == 1
    assert s["attempt"] == 1
    assert s["spec"] == "myspec"


def test_gaps_distribution(tmp_path):
    # Gaps between consecutive events: build known set incl. one > 120s.
    lines = [
        _rec(0.0, {"type": "system"}),
        _rec(0.5, _assistant(thinking=True)),     # gap 0.5
        _rec(20.5, _assistant(thinking=True)),    # gap 20.0
        _rec(150.5, _assistant(thinking=True)),   # gap 130.0  (> 120)
        _rec(150.5, _result({"output_tokens": 1})),  # gap 0.0
    ]
    p = _write_sidecar(tmp_path, lines)
    s = summarize_stream(p)
    g = s["gaps"]
    assert g["max"] == pytest.approx(130.0)
    assert g["n_over_120s"] == 1
    # gaps sorted = [0.0, 0.5, 20.0, 130.0]
    assert g["p50"] == pytest.approx(10.25)


# ---------------------------------------------------------------------------
# tsc error sequence (the spin-vs-progress signal)
# ---------------------------------------------------------------------------


def test_tsc_error_counts_progress(tmp_path):
    # Two tsc invocations: 14 errors then 9 -> PROGRESS sequence.
    err_block_14 = "\n".join(
        f"src/f{i}.ts({i},1): error TS2339: Property x does not exist."
        for i in range(14)
    )
    lines = [
        _rec(0.0, {"type": "system"}),
        _rec(1.0, _assistant(tool_uses=[
            {"id": "tsc1", "name": "Bash",
             "input": {"command": "npx tsc --noEmit"}}])),
        _rec(2.0, _user_tool_result("tsc1", err_block_14)),
        _rec(3.0, _assistant(tool_uses=[
            {"id": "tsc2", "name": "Bash",
             "input": {"command": "npm run typecheck"}}])),
        # tsc summary line form
        _rec(4.0, _user_tool_result("tsc2", "Found 9 errors in 3 files.")),
        _rec(4.1, _result({"output_tokens": 5})),
    ]
    p = _write_sidecar(tmp_path, lines)
    s = summarize_stream(p)
    assert s["tsc"] == {"invocations": 2, "error_counts": [14, 9]}


def test_tsc_zero_errors_clean(tmp_path):
    lines = [
        _rec(0.0, {"type": "system"}),
        _rec(1.0, _assistant(tool_uses=[
            {"id": "tsc1", "name": "Bash", "input": {"command": "tsc --noEmit"}}])),
        _rec(2.0, _user_tool_result("tsc1", "")),  # no errors
        _rec(2.1, _result({"output_tokens": 1})),
    ]
    p = _write_sidecar(tmp_path, lines)
    s = summarize_stream(p)
    assert s["tsc"] == {"invocations": 1, "error_counts": [0]}


def test_tool_result_content_as_list(tmp_path):
    # claude-code sometimes wraps tool_result content as [{"type":"text",...}].
    lines = [
        _rec(0.0, {"type": "system"}),
        _rec(1.0, _assistant(tool_uses=[
            {"id": "tsc1", "name": "Bash", "input": {"command": "vue-tsc --noEmit"}}])),
        _rec(2.0, _user_tool_result(
            "tsc1", [{"type": "text", "text": "Found 3 errors in 1 file."}])),
        _rec(2.1, _result({"output_tokens": 1})),
    ]
    p = _write_sidecar(tmp_path, lines)
    s = summarize_stream(p)
    assert s["tsc"] == {"invocations": 1, "error_counts": [3]}


# ---------------------------------------------------------------------------
# Truncated / timeout stream — best effort, never raises
# ---------------------------------------------------------------------------


def test_truncated_stream_no_result(tmp_path):
    # No terminal result event (SIGKILL'd timeout) + a torn final line.
    lines = [
        _rec(0.0, {"type": "system"}),
        _rec(1.0, _assistant(thinking=True, usage={"output_tokens": 10,
                                                   "cache_read_input_tokens": 100})),
        _rec(2.0, _assistant(thinking=True, usage={"output_tokens": 15,
                                                   "cache_read_input_tokens": 150})),
        '{"t": 3.0, "line": "{\\"type\\":\\"assist',  # torn tail
    ]
    p = _write_sidecar(tmp_path, lines)
    s = summarize_stream(p)
    assert s["truncated"] is True
    assert s["outcome"] == "incomplete"
    assert s["actual_turns"] == 2
    # tokens summed from per-assistant usage (lower bound)
    assert s["tokens"]["output"] == 25
    assert s["tokens"]["cache_read"] == 250


def test_inline_wall_override_extends_idle(tmp_path):
    # Inline mode passes the real wall (incl. the post-last-event SIGKILL tail).
    lines = [
        _rec(0.0, {"type": "system"}),
        _rec(1.0, _assistant(thinking=True)),   # gap 1.0 thinking
    ]
    p = _write_sidecar(tmp_path, lines)
    s = summarize_stream(p, wall_seconds=901.0, outcome="timeout")
    assert s["wall_seconds"] == pytest.approx(901.0)
    # idle = 901 - 1.0 thinking - 0 tool
    assert s["idle_seconds"] == pytest.approx(900.0)
    assert s["outcome"] == "timeout"   # caller override wins


def test_empty_and_missing_files(tmp_path):
    # Empty file
    empty = tmp_path / "stream_architect_s_abc_it0_attempt1_p1s0.jsonl"
    empty.write_text("", encoding="utf-8")
    s = summarize_stream(empty)
    assert s["truncated"] is True
    assert s["actual_turns"] == 0
    assert s["tokens"] == {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0}

    # Missing file — never raises
    s2 = summarize_stream(tmp_path / "does_not_exist.jsonl")
    assert s2["actual_turns"] == 0
    assert s2["truncated"] is True


def test_unknown_event_types_ignored(tmp_path):
    # Schema-drift defense: a future event type must not break parsing.
    lines = [
        _rec(0.0, {"type": "system"}),
        _rec(0.5, {"type": "some_future_event", "payload": {"x": 1}}),
        _rec(1.0, _assistant(thinking=True)),
        _rec(1.1, _result({"output_tokens": 3})),
    ]
    p = _write_sidecar(tmp_path, lines)
    s = summarize_stream(p)
    assert s["outcome"] == "completed"
    assert s["actual_turns"] == 1
    assert s["truncated"] is False


def test_multiedit_counts_as_write(tmp_path):
    lines = [
        _rec(0.0, {"type": "system"}),
        _rec(1.0, _assistant(tool_uses=[
            {"id": "e1", "name": "MultiEdit", "input": {"file_path": "src/x.ts"}},
            {"id": "e2", "name": "Edit", "input": {"file_path": "src/y.ts"}},
            {"id": "e3", "name": "Edit", "input": {"file_path": "src/x.ts"}}])),
        _rec(1.1, _result({"output_tokens": 1})),
    ]
    p = _write_sidecar(tmp_path, lines)
    s = summarize_stream(p)
    # distinct paths: src/x.ts, src/y.ts
    assert s["files_written"] == 2
    assert s["tool_calls"] == {"MultiEdit": 1, "Edit": 2}


# ---------------------------------------------------------------------------
# Discovery + flat sink
# ---------------------------------------------------------------------------


def test_append_metrics_line(tmp_path):
    summary = {"role": "test-engineer", "actual_turns": 5}
    append_metrics_line(tmp_path, summary)
    append_metrics_line(tmp_path, {"role": "implementer", "actual_turns": 3})
    out = tmp_path / ".dark-factory" / "metrics" / "stream-summary.jsonl"
    rows = [json.loads(ln) for ln in out.read_text().splitlines()]
    assert rows[0]["role"] == "test-engineer"
    assert rows[1]["actual_turns"] == 3


def test_find_sidecars_filters(tmp_path):
    failures = tmp_path / ".dark-factory" / "failures"
    failures.mkdir(parents=True)
    names = [
        "stream_test-engineer_specA_11111111_it1_validate_attempt1_p1s0.jsonl",
        "stream_implementer_specA_11111111_it1_attempt1_p1s1.jsonl",
        "stream_test-engineer_specB_22222222_it0_generate_attempt1_p1s2.jsonl",
        "not-a-sidecar.jsonl",
    ]
    for n in names:
        (failures / n).write_text("{}\n")

    # spec filter
    a = find_sidecars(tmp_path, spec="specA")
    assert {p.name for p in a} == {names[0], names[1]}

    # run filter (8-char prefix)
    b = find_sidecars(tmp_path, run_id="22222222")
    assert {p.name for p in b} == {names[2]}

    # archive copy is de-duped against the live copy
    archive = tmp_path / ".dark-factory" / "archive" / "20260623-specA-11111111" / "failures"
    archive.mkdir(parents=True)
    (archive / names[0]).write_text("{}\n")
    c = find_sidecars(tmp_path, spec="specA")
    assert sum(1 for p in c if p.name == names[0]) == 1


def test_tsc_cmd_re_false_positives_excluded(tmp_path):
    # Commands that merely MENTION tsc/typecheck must NOT register as tsc runs —
    # a phantom tsc invocation corrupts the headline spin-vs-progress metric.
    for cmd in ("cat typecheck.log", "echo typecheck",
                "node scripts/typecheck.js", "ls /tsc",
                "grep error tsc.txt"):
        lines = [
            _rec(0.0, {"type": "system"}),
            _rec(1.0, _assistant(tool_uses=[
                {"id": "b1", "name": "Bash", "input": {"command": cmd}}])),
            _rec(2.0, _user_tool_result("b1", "Found 5 errors in 1 file.")),
            _rec(2.1, _result({"output_tokens": 1})),
        ]
        p = _write_sidecar(tmp_path, lines, name=f"stream_implementer_s_a_it0_attempt1_p1s0.jsonl")
        s = summarize_stream(p)
        assert s["tsc"] is None, f"command {cmd!r} wrongly detected as tsc"


def test_tsc_cmd_re_true_positives(tmp_path):
    for cmd in ("tsc --noEmit", "npx tsc --noEmit", "pnpm tsc",
                "./node_modules/.bin/tsc -p tsconfig.json",
                "vue-tsc --noEmit", "npm run typecheck",
                "pnpm run type-check", "cd app && tsc --noEmit"):
        lines = [
            _rec(0.0, {"type": "system"}),
            _rec(1.0, _assistant(tool_uses=[
                {"id": "b1", "name": "Bash", "input": {"command": cmd}}])),
            _rec(2.0, _user_tool_result("b1", "Found 2 errors in 1 file.")),
            _rec(2.1, _result({"output_tokens": 1})),
        ]
        p = _write_sidecar(tmp_path, lines, name="stream_test-engineer_s_a_it0_attempt1_p1s0.jsonl")
        s = summarize_stream(p)
        assert s["tsc"] == {"invocations": 1, "error_counts": [2]}, f"command {cmd!r} not detected"


def test_inner_decode_error_with_result_stays_clean(tmp_path):
    # A completed stream (terminal result) with one un-decodable inner event line
    # is schema drift, NOT a torn tail -> must remain truncated=False (Linus #2).
    lines = [
        _rec(0.0, {"type": "system"}),
        json.dumps({"t": 0.5, "line": "this-is-not-json{{{"}),  # inner garbage
        _rec(1.0, _assistant(thinking=True)),
        _rec(1.1, _result({"output_tokens": 3})),
    ]
    p = _write_sidecar(tmp_path, lines)
    s = summarize_stream(p)
    assert s["outcome"] == "completed"
    assert s["truncated"] is False
    assert s["actual_turns"] == 1


def test_emit_agent_stream_summary_lands_event(tmp_path):
    # Regression for the kwarg-collision blocker: the summary dict carries its own
    # spec/run_id/iteration keys, which must not collide with the envelope kwargs.
    from darkfactory.events.types import emit_agent_stream_summary

    lines = [
        _rec(0.0, {"type": "system"}),
        _rec(1.0, _assistant(thinking=True)),
        _rec(1.1, _result({"output_tokens": 3})),
    ]
    p = _write_sidecar(tmp_path, lines)
    summary = summarize_stream(p, role="test-engineer", phase="validate",
                               iteration=1, spec="myspec", run_id="8c5cfb67",
                               outcome="completed")
    events_file = tmp_path / "events.jsonl"
    # Must not raise (the collision would TypeError here).
    emit_agent_stream_summary(events_file, spec="myspec", run_id="8c5cfb67",
                              iteration=1, summary=summary)
    rows = [json.loads(ln) for ln in events_file.read_text().splitlines()]
    assert len(rows) == 1
    rec = rows[0]
    assert rec["event"] == "AGENT_STREAM_SUMMARY"
    assert rec["spec"] == "myspec"
    assert rec["data"]["actual_turns"] == 1
    assert rec["data"]["role"] == "test-engineer"
    # reserved envelope keys are not duplicated inside data
    assert "spec" not in rec["data"]
    assert "run_id" not in rec["data"]
    assert "iteration" not in rec["data"]


def test_metadata_without_phase(tmp_path):
    # A sidecar with no phase segment still parses role/iteration/attempt.
    name = "stream_implementer_my-spec_abc12345_it2_attempt3_p99s7.jsonl"
    p = _write_sidecar(tmp_path, [_rec(0.0, _result({"output_tokens": 1}))], name=name)
    s = summarize_stream(p)
    assert s["role"] == "implementer"
    assert s["spec"] == "my-spec"
    assert s["iteration"] == 2
    assert s["attempt"] == 3
    assert s["phase"] is None
