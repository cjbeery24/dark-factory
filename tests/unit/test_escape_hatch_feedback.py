"""Tests for pipeline/escape_hatch_feedback.py — synthetic feedback
written at each of the four iteration-loop escape hatches."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from darkfactory.audit.checker import validate_oracle_feedback
from darkfactory.models.pipeline import IterationState, SpecRun
from darkfactory.models.satisfaction import TrajectoryType
from darkfactory.pipeline.escape_hatch_feedback import (
    REASON_B1_EARLY_ESCALATION,
    REASON_GATE_REGRESSION,
    REASON_SELF_TEST_REGRESSION,
    REASON_TRAJECTORY_PLATEAU,
    VALID_REASONS,
    write_escape_hatch_feedback,
)


def _project(tmp: str) -> Path:
    p = Path(tmp)
    (p / ".dark-factory" / "reviews").mkdir(parents=True)
    return p


def _spec_run(name: str = "myspec", iteration: int = 2) -> SpecRun:
    sr = SpecRun(spec_name=name)
    sr.iteration = iteration
    return sr


def _iter_state(
    number: int, *,
    gate_exit_code: int = 1,
    pass_rate: int = 60,
    scenario_total: int = 10,
    satisfaction_score: int = 55,
    failing_ac_ids: set[str] | None = None,
) -> IterationState:
    s = IterationState(number=number)
    s.gate_exit_code = gate_exit_code
    s.pass_rate = pass_rate
    s.scenario_total = scenario_total
    s.satisfaction_score = satisfaction_score
    s.failing_ac_ids = failing_ac_ids or set()
    return s


def _seed_prior_feedback(
    project: Path, spec_name: str, iter_number: int, text: str,
) -> Path:
    p = project / ".dark-factory" / "reviews" / f"{spec_name}-feedback-iter{iter_number}.md"
    p.write_text(text, encoding="utf-8")
    return p


class TestSelfTestRegression(unittest.TestCase):
    def test_writes_feedback_with_self_test_regression_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            prior = _iter_state(1, gate_exit_code=0, pass_rate=80)
            curr = _iter_state(2, gate_exit_code=1, pass_rate=30)
            sr = _spec_run(iteration=2)
            sr.iterations = [prior, curr]
            _seed_prior_feedback(
                project, sr.spec_name, 1,
                "# Prior feedback\n\nFocus on AC-4 invariant preservation.",
            )

            path, inlined = write_escape_hatch_feedback(
                sr, curr, REASON_SELF_TEST_REGRESSION, project,
                prev_iter_state=prior,
                rollback_ref=f"refs/dark-factory/iter-end/{sr.run_id}-1",
                rolled_back=True,
            )
            self.assertTrue(path.exists())
            self.assertTrue(inlined)
            body = path.read_text(encoding="utf-8")
            self.assertIn("SELF_TEST_REGRESSION", body)
            self.assertIn("current iter: 2", body)
            self.assertIn("prior iter: 1", body)
            self.assertIn("exit_code: 1", body)
            self.assertIn("prior exit_code: 0", body)
            self.assertIn("Focus on AC-4 invariant preservation.", body)
            self.assertIn("do NOT re-introduce", body)


class TestGateRegression(unittest.TestCase):
    def test_writes_feedback_with_gate_regression_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            prior = _iter_state(1, gate_exit_code=1, pass_rate=70)
            curr = _iter_state(2, gate_exit_code=1, pass_rate=50)
            sr = _spec_run(iteration=2)
            sr.iterations = [prior, curr]
            _seed_prior_feedback(
                project, sr.spec_name, 1,
                "# Prior feedback\n\nAC-1 and FR-2 need work.",
            )

            path, inlined = write_escape_hatch_feedback(
                sr, curr, REASON_GATE_REGRESSION, project,
                prev_iter_state=prior,
                rollback_ref=f"refs/dark-factory/iter-end/{sr.run_id}-1",
                rolled_back=True,
            )
            body = path.read_text(encoding="utf-8")
            self.assertTrue(inlined)
            self.assertIn("GATE_REGRESSION", body)
            self.assertIn("pass_rate: 50", body)
            self.assertIn("prior pass_rate: 70", body)
            self.assertIn("AC-1 and FR-2", body)


class TestB1EarlyEscalation(unittest.TestCase):
    def test_writes_feedback_with_b1_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            first = _iter_state(1, pass_rate=40, failing_ac_ids={"AC-3", "FR-2"})
            second = _iter_state(2, pass_rate=42, failing_ac_ids={"AC-3", "FR-2"})
            sr = _spec_run(iteration=2)
            sr.iterations = [first, second]
            sr.trajectory_class = TrajectoryType.PLATEAU
            sr.trajectory_strategy = "refactor_tests"
            sr.stable_failure_names = {
                "test_login[AC-3]", "test_logout[FR-2]",
            }

            path, inlined = write_escape_hatch_feedback(
                sr, second, REASON_B1_EARLY_ESCALATION, project,
            )
            body = path.read_text(encoding="utf-8")
            self.assertFalse(inlined)
            self.assertIn("B1_EARLY_ESCALATION", body)
            self.assertIn("stable failure count: 2", body)
            self.assertIn("AC-3", body)
            self.assertIn("FR-2", body)
            self.assertIn("trajectory: PLATEAU", body)
            self.assertIn("This run is escalating", body)
            # Directive must not claim more than the payload supports.
            self.assertIn("failed in every data-carrying iteration", body)
            # Test names MUST NOT appear.
            self.assertNotIn("test_login", body)
            self.assertNotIn("test_logout", body)

    def test_b1_intersects_rather_than_unions_ac_ids(self) -> None:
        """An AC that failed on iter-1 but was fixed on iter-2 must not
        appear in the "failed in every data-carrying iteration" list."""
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            # iter-1 fails AC-1 and AC-2; iter-2 fails AC-2 and AC-3.
            # Intersection = {AC-2}. Union = {AC-1, AC-2, AC-3}.
            first = _iter_state(1, failing_ac_ids={"AC-1", "AC-2"})
            second = _iter_state(2, failing_ac_ids={"AC-2", "AC-3"})
            sr = _spec_run(iteration=2)
            sr.iterations = [first, second]
            sr.trajectory_class = TrajectoryType.PLATEAU
            sr.stable_failure_names = {"t"}

            path, _ = write_escape_hatch_feedback(
                sr, second, REASON_B1_EARLY_ESCALATION, project,
            )
            body = path.read_text(encoding="utf-8")
            # AC-2 is in the intersection → must appear.
            self.assertIn("AC-2", body)
            # AC-1 (iter-1 only) and AC-3 (iter-2 only) must NOT
            # appear in the "intersection" line. They might still
            # appear elsewhere in inlined content, but since we
            # didn't seed a prior feedback file, this is clean.
            ac_line = next(
                line for line in body.splitlines()
                if "intersection across data-carrying iters" in line
            )
            self.assertIn("AC-2", ac_line)
            self.assertNotIn("AC-1", ac_line)
            self.assertNotIn("AC-3", ac_line)

    def test_b1_empty_intersection_is_a_named_diagnostic(self) -> None:
        """Disjoint failure sets across iterations — the helper must
        explicitly flag that as a distinct diagnostic state."""
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            first = _iter_state(1, failing_ac_ids={"AC-1"})
            second = _iter_state(2, failing_ac_ids={"AC-2"})
            sr = _spec_run(iteration=2)
            sr.iterations = [first, second]
            sr.trajectory_class = TrajectoryType.PLATEAU
            sr.stable_failure_names = {"t"}

            path, _ = write_escape_hatch_feedback(
                sr, second, REASON_B1_EARLY_ESCALATION, project,
            )
            body = path.read_text(encoding="utf-8")
            self.assertIn("disjoint AC/FR/EC sets", body)

    def test_b1_no_data_iters_is_distinct_from_empty_intersection(self) -> None:
        """Every iteration's failing_ac_ids is empty (e.g. all iters
        build-failed) — the "no data" branch must fire, not the
        "disjoint sets" branch."""
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            first = _iter_state(1, failing_ac_ids=set())
            second = _iter_state(2, failing_ac_ids=set())
            sr = _spec_run(iteration=2)
            sr.iterations = [first, second]
            sr.trajectory_class = TrajectoryType.PLATEAU
            sr.stable_failure_names = set()

            path, _ = write_escape_hatch_feedback(
                sr, second, REASON_B1_EARLY_ESCALATION, project,
            )
            body = path.read_text(encoding="utf-8")
            self.assertIn(
                "no iteration extracted AC/FR/EC tags", body,
            )
            self.assertNotIn("disjoint AC/FR/EC sets", body)


class TestTrajectoryPlateau(unittest.TestCase):
    def test_writes_feedback_with_trajectory_reason_and_history(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            sr = _spec_run(iteration=3)
            sr.iterations = [
                _iter_state(1, pass_rate=40, failing_ac_ids={"AC-1"}),
                _iter_state(2, pass_rate=55, failing_ac_ids={"AC-1", "AC-2"}),
                _iter_state(3, pass_rate=55, failing_ac_ids={"AC-1", "AC-2"}),
            ]
            sr.trajectory_class = TrajectoryType.PLATEAU
            sr.trajectory_strategy = "abandon"
            sr.stable_failure_names = {"t1", "t2"}

            path, _ = write_escape_hatch_feedback(
                sr, sr.iterations[-1], REASON_TRAJECTORY_PLATEAU, project,
            )
            body = path.read_text(encoding="utf-8")
            self.assertIn("TRAJECTORY_PLATEAU", body)
            self.assertIn("pass_rate history: [40, 55, 55]", body)
            self.assertIn("stable failure count: 2", body)
            self.assertIn("trajectory: PLATEAU", body)
            self.assertIn("strategy recommendation: abandon", body)


class TestEdgeCaseNoPriorFeedback(unittest.TestCase):
    def test_graceful_omission_when_prior_feedback_missing(self) -> None:
        """If iter-N-1 exited via the build-fail path, no feedback file
        exists — the helper must omit the inlined block gracefully."""
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            prior = _iter_state(1, gate_exit_code=2, pass_rate=0)  # build fail
            curr = _iter_state(2, gate_exit_code=1, pass_rate=40)
            sr = _spec_run(iteration=2)
            sr.iterations = [prior, curr]
            # Deliberately do NOT seed feedback-iter1.md.

            path, inlined = write_escape_hatch_feedback(
                sr, curr, REASON_GATE_REGRESSION, project,
                prev_iter_state=prior,
                rollback_ref=f"refs/dark-factory/iter-end/{sr.run_id}-1",
                rolled_back=True,
            )
            body = path.read_text(encoding="utf-8")
            self.assertFalse(inlined)
            self.assertIn("GATE_REGRESSION", body)
            self.assertIn("No feedback file from the prior iteration", body)
            # No crash, no empty inlined block header followed by nothing.
            self.assertNotIn("(inlined)", body)


class TestBarrierCompliance(unittest.TestCase):
    """Run every reason's output through the existing barrier scanner."""

    def _run_scanner(self, body: str) -> None:
        result = validate_oracle_feedback(body, audit_path=Path("/tmp/_test_audit"))
        # The CLASSIFICATIONS block is only required for Oracle NO-SHIP
        # feedback; synthetic escape-hatch feedback is not oracle
        # output. Filter that single check out before asserting
        # compliance on the actual leakage patterns.
        leaks = [
            v for v in result.violations
            if "CLASSIFICATIONS" not in v
        ]
        self.assertEqual(
            leaks, [],
            msg=f"synthetic feedback leaked barrier-violating content: {leaks}",
        )

    def test_self_test_regression_passes_scanner(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            sr = _spec_run(iteration=2)
            sr.iterations = [_iter_state(1), _iter_state(2)]
            path, _ = write_escape_hatch_feedback(
                sr, sr.iterations[-1], REASON_SELF_TEST_REGRESSION, project,
                prev_iter_state=sr.iterations[-2],
                rollback_ref="refs/abc",
                rolled_back=True,
            )
            self._run_scanner(path.read_text(encoding="utf-8"))

    def test_gate_regression_passes_scanner(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            sr = _spec_run(iteration=2)
            sr.iterations = [_iter_state(1), _iter_state(2)]
            path, _ = write_escape_hatch_feedback(
                sr, sr.iterations[-1], REASON_GATE_REGRESSION, project,
                prev_iter_state=sr.iterations[-2],
                rollback_ref="refs/abc",
                rolled_back=True,
            )
            self._run_scanner(path.read_text(encoding="utf-8"))

    def test_b1_early_escalation_passes_scanner(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            sr = _spec_run(iteration=2)
            sr.iterations = [
                _iter_state(1, failing_ac_ids={"AC-1", "FR-3"}),
                _iter_state(2, failing_ac_ids={"AC-1", "FR-3", "EC-2"}),
            ]
            sr.trajectory_class = TrajectoryType.PLATEAU
            sr.stable_failure_names = {"t1"}
            path, _ = write_escape_hatch_feedback(
                sr, sr.iterations[-1], REASON_B1_EARLY_ESCALATION, project,
            )
            self._run_scanner(path.read_text(encoding="utf-8"))

    def test_feedback_bodies_never_contain_barrier_smelling_substrings(
        self,
    ) -> None:
        """Assertion-based negative control paralleling the scanner.

        The `validate_oracle_feedback` scanner is a lexical regex
        check — it catches *known-bad* patterns. This test codifies
        the intent: the helper must never emit the substrings that
        reliably mark scenario-level leakage, regardless of whether
        a future format tweak happens to land inside one of the
        scanner's patterns.
        """
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            sr = _spec_run(iteration=3)
            sr.iterations = [
                _iter_state(1, failing_ac_ids={"AC-1"}),
                _iter_state(2, failing_ac_ids={"AC-1"}),
                _iter_state(3, failing_ac_ids={"AC-1"}),
            ]
            sr.trajectory_class = TrajectoryType.PLATEAU
            sr.stable_failure_names = {"test_login", "test_logout"}

            # Exercise every reason with the same sink.
            outputs: list[str] = []
            path_1, _ = write_escape_hatch_feedback(
                sr, sr.iterations[-1], REASON_TRAJECTORY_PLATEAU, project,
            )
            outputs.append(path_1.read_text(encoding="utf-8"))
            path_2, _ = write_escape_hatch_feedback(
                sr, sr.iterations[-1], REASON_B1_EARLY_ESCALATION, project,
            )
            outputs.append(path_2.read_text(encoding="utf-8"))
            path_3, _ = write_escape_hatch_feedback(
                sr, sr.iterations[-1], REASON_GATE_REGRESSION, project,
                prev_iter_state=sr.iterations[-2],
                rollback_ref="refs/abc",
                rolled_back=True,
            )
            outputs.append(path_3.read_text(encoding="utf-8"))
            path_4, _ = write_escape_hatch_feedback(
                sr, sr.iterations[-1], REASON_SELF_TEST_REGRESSION, project,
                prev_iter_state=sr.iterations[-2],
                rollback_ref="refs/abc",
                rolled_back=True,
            )
            outputs.append(path_4.read_text(encoding="utf-8"))

            # Substring patterns that reliably mark scenario-level
            # leakage. "failed" / "passed" alone are prose verbs and
            # NOT leakage (the directive uses "failed in every
            # iteration"); the leak patterns combine a count with the
            # verb, or carry test-name / assertion shape.
            import re as _re
            forbidden_patterns: tuple[tuple[str, _re.Pattern[str]], ...] = (
                ("percent pass/fail", _re.compile(r"\d+%\s*(?:pass|fail)", _re.IGNORECASE)),
                ("N/M passed|failed",
                 _re.compile(r"\d+\s*/\s*\d+\s*(?:passed|failed|scenarios?|tests?)",
                             _re.IGNORECASE)),
                ("N of M passed|failed",
                 _re.compile(r"\d+\s+of\s+\d+\s+(?:passed|failed|scenarios?|tests?)",
                             _re.IGNORECASE)),
                ("all N scenarios",
                 _re.compile(r"\b(?:all|every|none|no)\s+\d+\s+scenarios?\b", _re.IGNORECASE)),
                ("all scenarios pass/fail",
                 _re.compile(
                     r"\b(?:all|every|none|no)\s+scenarios?\s+(?:passed|failed|pass|fail)\b",
                     _re.IGNORECASE)),
                ("pytest test name", _re.compile(r"\btest_[a-z0-9_]+\b")),
                ("js/ts test filename",
                 _re.compile(r"\b\d{2}-[\w-]+\.(?:test|spec|scenario)\.[a-z]+\b")),
                ("assert call", _re.compile(r"\b(?:expect|assert|should)\s*[.(]")),
                ("test dsl block",
                 _re.compile(r"\b(?:describe|it|test)\s*\(\s*['\"]")),
                ("stack trace", _re.compile(r"at\s+\w+\s+\(.*:\d+:\d+\)")),
            )
            for idx, body in enumerate(outputs):
                # AC-IDs are safe vocabulary but test names are not —
                # the helper's payload is spec_run.stable_failure_names
                # (test names) by LENGTH only.
                self.assertNotIn("test_login", body, f"output {idx}")
                self.assertNotIn("test_logout", body, f"output {idx}")
                for label, pattern in forbidden_patterns:
                    self.assertIsNone(
                        pattern.search(body),
                        msg=f"output {idx} matches forbidden pattern '{label}'",
                    )

    def test_prior_feedback_is_truncated_at_cap(self) -> None:
        """Unbounded telescoping inlining would blow up prompt budget
        across 3-iter runs. The cap must fire."""
        from darkfactory.pipeline.escape_hatch_feedback import (
            _INLINE_MAX_CHARS,
        )
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            sr = _spec_run(iteration=2)
            sr.iterations = [_iter_state(1), _iter_state(2)]
            huge = "x" * (_INLINE_MAX_CHARS * 2)
            _seed_prior_feedback(project, sr.spec_name, 1, huge)

            path, inlined = write_escape_hatch_feedback(
                sr, sr.iterations[-1], REASON_GATE_REGRESSION, project,
                prev_iter_state=sr.iterations[-2],
            )
            self.assertTrue(inlined)
            body = path.read_text(encoding="utf-8")
            self.assertIn("truncated at", body)
            # Body must still be reasonably bounded.
            self.assertLess(len(body), _INLINE_MAX_CHARS * 1.5)

    def test_trajectory_plateau_passes_scanner(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            sr = _spec_run(iteration=3)
            sr.iterations = [
                _iter_state(1, failing_ac_ids={"AC-1"}),
                _iter_state(2, failing_ac_ids={"AC-1"}),
                _iter_state(3, failing_ac_ids={"AC-1"}),
            ]
            sr.trajectory_class = TrajectoryType.PLATEAU
            sr.stable_failure_names = {"t1"}
            path, _ = write_escape_hatch_feedback(
                sr, sr.iterations[-1], REASON_TRAJECTORY_PLATEAU, project,
            )
            self._run_scanner(path.read_text(encoding="utf-8"))


class TestContractEnforcement(unittest.TestCase):
    def test_rejects_unknown_reason(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            sr = _spec_run()
            sr.iterations = [_iter_state(1), _iter_state(2)]
            with self.assertRaises(ValueError):
                write_escape_hatch_feedback(
                    sr, sr.iterations[-1], "NOT_A_REAL_REASON", project,
                )

    def test_regression_reason_requires_prev_iter_state(self) -> None:
        with TemporaryDirectory() as tmp:
            project = _project(tmp)
            sr = _spec_run()
            sr.iterations = [_iter_state(1), _iter_state(2)]
            with self.assertRaises(ValueError):
                write_escape_hatch_feedback(
                    sr, sr.iterations[-1], REASON_SELF_TEST_REGRESSION, project,
                    prev_iter_state=None,
                )
            with self.assertRaises(ValueError):
                write_escape_hatch_feedback(
                    sr, sr.iterations[-1], REASON_GATE_REGRESSION, project,
                    prev_iter_state=None,
                )

    def test_valid_reasons_set_is_closed(self) -> None:
        self.assertEqual(
            VALID_REASONS,
            {
                REASON_SELF_TEST_REGRESSION,
                REASON_GATE_REGRESSION,
                REASON_B1_EARLY_ESCALATION,
                REASON_TRAJECTORY_PLATEAU,
            },
        )


if __name__ == "__main__":
    unittest.main()
