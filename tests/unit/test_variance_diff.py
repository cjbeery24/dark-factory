"""Unit tests for scripts/variance-diff.py (Phase 6 I3)."""

from __future__ import annotations

import importlib.util
import json
import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase

_SCRIPT_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "scripts" / "variance-diff.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("variance_diff", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


variance_diff = _load_module()


def _mk_event(run_id: str, ev_type: str, **data) -> str:
    return json.dumps({
        "type": ev_type,
        "run_id": run_id,
        "spec_name": "demo",
        "data": data,
    })


class TestPureFunctions(unittest.TestCase):
    def test_jaccard_empty_both_returns_one(self) -> None:
        self.assertEqual(variance_diff._jaccard([], []), 1.0)

    def test_jaccard_disjoint_returns_zero(self) -> None:
        self.assertEqual(variance_diff._jaccard(["a"], ["b"]), 0.0)

    def test_jaccard_partial_overlap(self) -> None:
        self.assertAlmostEqual(
            variance_diff._jaccard(["a", "b"], ["b", "c"]), 1 / 3,
        )

    def test_abs_over_max_zero_zero(self) -> None:
        self.assertEqual(variance_diff._abs_over_max(0, 0), 0.0)

    def test_abs_over_max_partial(self) -> None:
        self.assertAlmostEqual(variance_diff._abs_over_max(2, 4), 0.5)

    def test_fingerprint_distance_identical_is_zero(self) -> None:
        fp = {
            "import_set": ["a"], "symbol_set": ["a::x"],
            "test_names": ["t"], "assertion_count": 3,
        }
        self.assertEqual(variance_diff._fingerprint_distance(fp, fp), 0.0)

    def test_fingerprint_distance_fully_disjoint_is_one(self) -> None:
        a = {
            "import_set": ["a"], "symbol_set": ["a::x"],
            "test_names": ["t1"], "assertion_count": 0,
        }
        b = {
            "import_set": ["b"], "symbol_set": ["b::y"],
            "test_names": ["t2"], "assertion_count": 0,
        }
        # assertion_count 0/0 is 0, others are 1; mean = 0.75.
        self.assertAlmostEqual(variance_diff._fingerprint_distance(a, b), 0.75)

    def test_normalize_path_strips_project_prefix(self) -> None:
        result = variance_diff._normalize_path(
            "/tmp/run-abc/.dark-factory/scenarios/foo/a.test.ts", "a",
        )
        self.assertEqual(result, ".dark-factory/scenarios/foo/a.test.ts")

    def test_normalize_path_passthrough_without_marker(self) -> None:
        result = variance_diff._normalize_path("/nowhere/a.test.ts", "a")
        self.assertEqual(result, "/nowhere/a.test.ts")


class TestEndToEnd(TempDirTestCase):
    def _write_events(self, lines: list[str]) -> Path:
        root = self.tmp_dir / ".dark-factory" / "audit"
        root.mkdir(parents=True)
        ef = root / "events.jsonl"
        ef.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return ef

    def test_identical_runs_yield_zero_distance(self) -> None:
        lines = []
        for run in ("a", "b"):
            lines.append(_mk_event(
                run, "ARCHITECT_MANIFEST_HASH",
                manifest_hash="xyz", canonical_json="{}", cache_hit=False,
            ))
            lines.append(_mk_event(
                run, "SCENARIO_FINGERPRINT",
                scenario_path=(
                    f"/tmp/{run}/.dark-factory/scenarios/demo/a.test.ts"
                ),
                phase="generate",
                import_set=["vitest"], symbol_set=["vitest::it"],
                assertion_count=2, test_names=["x"],
            ))
        self._write_events(lines)
        rc = variance_diff.main([
            "--project-dir", str(self.tmp_dir),
            "--run-a", "a", "--run-b", "b",
            "--json",
        ])
        self.assertEqual(rc, 0)

    def test_manifest_drift_produces_distance_one(self) -> None:
        lines = [
            _mk_event(
                "a", "ARCHITECT_MANIFEST_HASH",
                manifest_hash="aaa", canonical_json='{"x":1}', cache_hit=False,
            ),
            _mk_event(
                "b", "ARCHITECT_MANIFEST_HASH",
                manifest_hash="bbb", canonical_json='{"x":2}', cache_hit=False,
            ),
        ]
        ef = self._write_events(lines)
        events_a = variance_diff._load_events(ef, "a")
        events_b = variance_diff._load_events(ef, "b")
        m = variance_diff._manifest_diff(events_a, events_b)
        self.assertTrue(m["present"])
        self.assertEqual(m["distance"], 1.0)
        self.assertLess(m["canonical_jaccard"], 1.0)

    def test_classifier_determinism_detects_mismatch(self) -> None:
        # Same input_hash across runs a and b; different output_hash → mismatch.
        lines = [
            _mk_event(
                "a", "CLASSIFIER_IO_HASH",
                input_hash="INP", output_hash="OUT1",
                iteration=1, role="classifier", attempt=1,
                defects_count=0, failing_test_count=0,
            ),
            _mk_event(
                "b", "CLASSIFIER_IO_HASH",
                input_hash="INP", output_hash="OUT2",
                iteration=1, role="classifier", attempt=1,
                defects_count=0, failing_test_count=0,
            ),
        ]
        ef = self._write_events(lines)
        events_a = variance_diff._load_events(ef, "a")
        events_b = variance_diff._load_events(ef, "b")
        c = variance_diff._classifier_determinism(events_a, events_b)
        self.assertTrue(c["present"])
        self.assertEqual(c["total_pairs"], 1)
        self.assertEqual(c["mismatch_pairs"], 1)
        self.assertEqual(c["determinism_pct"], 100.0)

    def test_scenario_missing_file_counts_as_distance_one(self) -> None:
        lines = [
            _mk_event(
                "a", "SCENARIO_FINGERPRINT",
                scenario_path=(
                    "/x/.dark-factory/scenarios/demo/a.test.ts"
                ),
                phase="generate",
                import_set=[], symbol_set=[],
                assertion_count=0, test_names=[],
            ),
            _mk_event(
                "b", "SCENARIO_FINGERPRINT",
                scenario_path=(
                    "/y/.dark-factory/scenarios/demo/b.test.ts"
                ),
                phase="generate",
                import_set=[], symbol_set=[],
                assertion_count=0, test_names=[],
            ),
        ]
        ef = self._write_events(lines)
        events_a = variance_diff._load_events(ef, "a")
        events_b = variance_diff._load_events(ef, "b")
        s = variance_diff._scenario_diff(events_a, events_b)
        self.assertTrue(s["present"])
        self.assertEqual(s["missing_files"], 2)
        self.assertEqual(s["shared_files"], 0)
        self.assertEqual(s["mean_distance"], 1.0)

    def test_last_phase_fingerprint_wins(self) -> None:
        """generate → coverage_regen on the same path — the last event
        is what we compare against."""
        path = "/x/.dark-factory/scenarios/demo/a.test.ts"
        lines = [
            _mk_event(
                "a", "SCENARIO_FINGERPRINT",
                scenario_path=path, phase="generate",
                import_set=["old"], symbol_set=[],
                assertion_count=1, test_names=["old"],
            ),
            _mk_event(
                "a", "SCENARIO_FINGERPRINT",
                scenario_path=path, phase="coverage_regen",
                import_set=["new"], symbol_set=[],
                assertion_count=5, test_names=["new"],
            ),
        ]
        ef = self._write_events(lines)
        events_a = variance_diff._load_events(ef, "a")
        latest = variance_diff._pick_last_phase_fingerprints(events_a)
        self.assertEqual(len(latest), 1)
        self.assertEqual(latest[path]["phase"], "coverage_regen")
        self.assertEqual(latest[path]["import_set"], ["new"])

    def test_zero_gain_iterations_counted(self) -> None:
        lines = [
            _mk_event(
                "a", "ITERATION_TEMPORAL_SIGNATURE",
                iteration=2,
                prior_failure_set_hash="h1", current_failure_set_hash="h1",
                tests_moved_to_pass=0, tests_moved_to_fail=0,
                tests_unchanged=5,
                classification_distribution_delta={},
            ),
            _mk_event(
                "a", "ITERATION_TEMPORAL_SIGNATURE",
                iteration=3,
                prior_failure_set_hash="h1", current_failure_set_hash="h2",
                tests_moved_to_pass=2, tests_moved_to_fail=1,
                tests_unchanged=3,
                classification_distribution_delta={},
            ),
        ]
        ef = self._write_events(lines)
        events_a = variance_diff._load_events(ef, "a")
        self.assertEqual(variance_diff._iteration_zero_gain(events_a), 1)


if __name__ == "__main__":
    unittest.main()
