"""Unit tests for the durable cross-run agent-metrics corpus."""

from __future__ import annotations

import json

from darkfactory.observability.corpus import (
    CORPUS_SCHEMA_VERSION,
    append_to_corpus,
    read_corpus,
    resolve_corpus_path,
    spec_shape,
    summarize_corpus,
)


def _summary(**over):
    s = {
        "role": "test-engineer", "spec": "demo", "run_id": "abcd1234",
        "iteration": 0, "phase": "generate", "attempt": 1, "outcome": "incomplete",
        "truncated": True, "wall_seconds": 1364.0, "actual_turns": 56,
        "thinking_turns": 15, "thinking_seconds": 1332.0, "tool_seconds": 3.8,
        "idle_seconds": 28.0, "tool_calls": {"Read": 20, "Glob": 13},
        "files_written": 0,
        "tokens": {"input": 13073, "output": 1759,
                   "cache_read": 2753624, "cache_creation": 486345},
        "gaps": {"p50": 0.3, "p90": 4.9, "max": 906.4, "n_over_120s": 2},
        "tsc": None, "events_observed": 97,
    }
    s.update(over)
    return s


def _provenance(**over):
    p = {
        "project": "budget-brain-v2", "spec": "demo", "spec_type": "feature",
        "run_id": "abcd1234", "role": "test-engineer", "phase": "generate",
        "iteration": 0, "attempt": 1, "model": "claude-sonnet-4-6",
        "outcome": "timeout",
    }
    p.update(over)
    return p


# ---------------------------------------------------------------------------
# spec_shape
# ---------------------------------------------------------------------------


def test_spec_shape_counts_distinct_ids():
    text = "FR-1 FR-2 FR-2 AC-1 AC-3 AC-3 EC-1 EC-2 EC-7 and prose FR-1 again"
    assert spec_shape(text) == {"fr_count": 2, "ac_count": 2, "ec_count": 3}


def test_spec_shape_empty():
    assert spec_shape(None) == {"fr_count": None, "ac_count": None, "ec_count": None}
    assert spec_shape("") == {"fr_count": None, "ac_count": None, "ec_count": None}


# ---------------------------------------------------------------------------
# resolve_corpus_path
# ---------------------------------------------------------------------------


def test_resolve_corpus_path_default_and_override(tmp_path, monkeypatch):
    # Clear the test-isolation env override to exercise the true home default.
    monkeypatch.delenv("DARK_FACTORY_CORPUS_DIR", raising=False)
    default = resolve_corpus_path(None)
    assert default.name == "agent-metrics.jsonl"
    assert default.parent.name == "corpus"
    assert default.parent.parent.name == ".dark-factory"
    # corpus_path argument takes precedence over everything.
    custom = tmp_path / "x" / "mine.jsonl"
    assert resolve_corpus_path(str(custom)) == custom


def test_corpus_dir_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("DARK_FACTORY_CORPUS_DIR", str(tmp_path / "envcorpus"))
    # No corpus_path -> env dir is used.
    p = resolve_corpus_path(None)
    assert p == tmp_path / "envcorpus" / "agent-metrics.jsonl"
    # corpus_path still wins over the env override.
    explicit = tmp_path / "explicit.jsonl"
    assert resolve_corpus_path(str(explicit)) == explicit


# ---------------------------------------------------------------------------
# append_to_corpus
# ---------------------------------------------------------------------------


def test_append_writes_full_record(tmp_path):
    corpus = tmp_path / "corpus" / "agent-metrics.jsonl"
    append_to_corpus(
        _summary(), provenance=_provenance(),
        spec_shape={"fr_count": 3, "ac_count": 8, "ec_count": 7},
        corpus_path=str(corpus), ts="2026-06-23T00:00:00Z",
        archive_streams=False,
    )
    rows = [json.loads(ln) for ln in corpus.read_text().splitlines()]
    assert len(rows) == 1
    r = rows[0]
    # provenance
    assert r["project"] == "budget-brain-v2"
    assert r["spec_type"] == "feature"
    assert r["model"] == "claude-sonnet-4-6"
    assert r["outcome"] == "timeout"   # provenance overrides summary's "incomplete"
    # spec_shape + schema
    assert r["spec_shape"] == {"fr_count": 3, "ac_count": 8, "ec_count": 7}
    assert r["schema_version"] == CORPUS_SCHEMA_VERSION
    assert r["ts"] == "2026-06-23T00:00:00Z"
    # metrics carried through
    assert r["actual_turns"] == 56
    assert r["gaps"]["n_over_120s"] == 2
    assert r["tokens"]["cache_read"] == 2753624


def test_append_metrics_only_no_artifact_content(tmp_path):
    # Barrier-safety: only metrics/counts/histograms reach the corpus — assert no
    # field smells like artifact content (a guard against future schema creep).
    corpus = tmp_path / "agent-metrics.jsonl"
    append_to_corpus(_summary(), provenance=_provenance(),
                     spec_shape=spec_shape("FR-1"), corpus_path=str(corpus),
                     archive_streams=False)
    r = json.loads(corpus.read_text().splitlines()[0])
    forbidden = {"content", "source", "scenario", "test_text", "code", "diff", "output"}
    assert not (set(r) & forbidden)
    # tool_calls is a histogram (name -> int), not content
    assert all(isinstance(v, int) for v in r["tool_calls"].values())


def test_append_appends_not_overwrites(tmp_path):
    corpus = tmp_path / "agent-metrics.jsonl"
    append_to_corpus(_summary(role="test-engineer"), provenance=_provenance(role="test-engineer"),
                     corpus_path=str(corpus), archive_streams=False)
    append_to_corpus(_summary(role="qa-holdout"), provenance=_provenance(role="qa-holdout"),
                     corpus_path=str(corpus), archive_streams=False)
    rows = [json.loads(ln) for ln in corpus.read_text().splitlines()]
    assert [r["role"] for r in rows] == ["test-engineer", "qa-holdout"]


def test_append_never_raises_on_bad_input(tmp_path):
    # Unwritable path + junk values must not raise.
    append_to_corpus({"x": object()}, provenance={"run_id": None},
                     corpus_path=str(tmp_path / "a" / "b" / "c.jsonl"),
                     archive_streams=False)
    # object() isn't JSON-serializable -> swallowed; nothing written, no raise.


def test_append_archives_sidecar(tmp_path):
    corpus = tmp_path / "corpus" / "agent-metrics.jsonl"
    sidecar = tmp_path / "stream_test-engineer_demo_abcd1234_it0_generate_attempt1_p1s0.jsonl"
    sidecar.write_text('{"t":0.0,"line":"{}"}\n')
    append_to_corpus(_summary(), provenance=_provenance(run_id="abcd1234"),
                     corpus_path=str(corpus), archive_streams=True,
                     sidecar_path=sidecar)
    copied = corpus.parent / "abcd1234" / sidecar.name
    assert copied.is_file()
    assert copied.read_text() == sidecar.read_text()


# ---------------------------------------------------------------------------
# read_corpus
# ---------------------------------------------------------------------------


def test_read_corpus_tolerates_torn_lines(tmp_path):
    corpus = tmp_path / "agent-metrics.jsonl"
    corpus.write_text(
        json.dumps({"role": "a", "actual_turns": 1}) + "\n"
        + "this is not json{{{\n"
        + json.dumps({"role": "b", "actual_turns": 2}) + "\n"
    )
    rows = read_corpus(str(corpus))
    assert [r["role"] for r in rows] == ["a", "b"]


def test_read_corpus_missing_file(tmp_path):
    assert read_corpus(str(tmp_path / "nope.jsonl")) == []


# ---------------------------------------------------------------------------
# summarize_corpus
# ---------------------------------------------------------------------------


def test_summarize_corpus_aggregates():
    recs = [
        _summary_record(role="test-engineer", phase="generate", outcome="timeout",
                        truncated=True, turns=56, wall=1364, mega=2, cr=900, inp=100),
        _summary_record(role="test-engineer", phase="generate", outcome="completed",
                        truncated=False, turns=40, wall=600, mega=0, cr=800, inp=200),
        _summary_record(role="qa-holdout", phase="generate", outcome="completed",
                        truncated=False, turns=59, wall=1172, mega=0, cr=970, inp=30),
    ]
    rows = {(r["role"], r["phase"]): r for r in summarize_corpus(recs)}
    te = rows[("test-engineer", "generate")]
    assert te["n"] == 2
    assert te["timeouts"] == 1
    assert te["timeout_rate"] == 0.5
    assert te["truncated"] == 1
    assert te["median_turns"] == 48.0   # median(56,40)
    assert te["max_turns"] == 56
    assert te["mega_turns_total"] == 2
    # cache hit: run1 900/1000=90%, run2 800/1000=80% -> avg 85%
    assert te["avg_cache_hit_pct"] == 85.0
    qa = rows[("qa-holdout", "generate")]
    assert qa["n"] == 1
    assert qa["timeout_rate"] == 0.0


def test_summarize_corpus_empty():
    assert summarize_corpus([]) == []


def _summary_record(*, role, phase, outcome, truncated, turns, wall, mega, cr, inp):
    return {
        "role": role, "phase": phase, "outcome": outcome, "truncated": truncated,
        "actual_turns": turns, "wall_seconds": wall,
        "gaps": {"n_over_120s": mega},
        "tokens": {"cache_read": cr, "input": inp, "cache_creation": 0},
    }
