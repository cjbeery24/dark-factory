"""Tests for C1 structured remediation events.

Covers the core emit helpers (``emit_remediation_target`` /
``emit_remediation_outcome``), the novel-rule detector, and the
outcome-classifier. Orchestrator-level wiring is exercised via direct
assertions against events.jsonl — no live pipeline runs.
"""

from __future__ import annotations

import json
import unittest

from darkfactory.events.remediation import (
    SCHEMA_VERSION,
    classify_outcome,
    emit_remediation_outcome,
    emit_remediation_target,
    is_novel_rule,
    most_recent_target_for_spec,
    most_recent_unpaired_target,
)
from darkfactory.models.events import EventType
from darkfactory.utils.json_helpers import read_jsonl
from tests.helpers import TempDirTestCase


class TestEmitRemediationTarget(TempDirTestCase):
    def test_writes_full_schema(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_remediation_target(
            ef,
            spec="my-spec",
            run_id="run-001",
            failure_class="consistency_violation",
            rule_or_check="migrate-mode-vs-push-mode",
            remediation_type="add_project_hint",
            remediation_target_path=".dark-factory/project-hints.toml",
            auto_applicable=True,
            suggested_diff="[[policy.consistency_rules]]\n",
            rationale="Spec contains db:migrate but env emits drizzle-kit push",
        )
        records = read_jsonl(ef)
        self.assertEqual(len(records), 1)
        rec = records[0]
        self.assertEqual(rec["event"], "REMEDIATION_TARGET")
        self.assertEqual(rec["spec"], "my-spec")
        self.assertEqual(rec["run_id"], "run-001")
        data = rec["data"]
        self.assertEqual(data["schema_version"], SCHEMA_VERSION)
        self.assertEqual(data["failure_class"], "consistency_violation")
        self.assertEqual(data["rule_or_check"], "migrate-mode-vs-push-mode")
        self.assertEqual(data["remediation_type"], "add_project_hint")
        self.assertEqual(data["auto_applicable"], True)
        self.assertIn("policy.consistency_rules", data["suggested_diff"])
        self.assertIn("db:migrate", data["rationale"])
        # next_command defaults to remediate <run-id>
        self.assertEqual(data["next_command"], "dark-factory remediate run-001")
        # First emission of this rule → novel
        self.assertTrue(data["novel_rule_name"])

    def test_novel_rule_false_on_second_emission(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_remediation_target(
            ef, spec="a", run_id="r1",
            failure_class="consistency_violation",
            rule_or_check="same-rule",
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="first",
        )
        emit_remediation_target(
            ef, spec="b", run_id="r2",
            failure_class="consistency_violation",
            rule_or_check="same-rule",
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="second",
        )
        records = read_jsonl(ef)
        self.assertTrue(records[0]["data"]["novel_rule_name"])
        # Second emission of same rule (even from different spec) → not novel.
        self.assertFalse(records[1]["data"]["novel_rule_name"])

    def test_novel_rule_true_for_different_rule(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_remediation_target(
            ef, spec="a", run_id="r1",
            failure_class="consistency_violation",
            rule_or_check="rule-a",
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="a",
        )
        emit_remediation_target(
            ef, spec="a", run_id="r2",
            failure_class="consistency_violation",
            rule_or_check="rule-b",
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="b",
        )
        records = read_jsonl(ef)
        self.assertTrue(records[0]["data"]["novel_rule_name"])
        self.assertTrue(records[1]["data"]["novel_rule_name"])

    def test_custom_next_command_preserved(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_remediation_target(
            ef, spec="s", run_id="r",
            failure_class="spec_quality",
            rule_or_check="spec_validator",
            remediation_type="edit_spec",
            remediation_target_path=".dark-factory/specs/s.md",
            auto_applicable=False,
            rationale="score=60",
            next_command="/df-write-spec s",
        )
        rec = read_jsonl(ef)[0]
        self.assertEqual(rec["data"]["next_command"], "/df-write-spec s")

    def test_auto_applicable_false_for_validator(self) -> None:
        """Plan acceptance: validator score<100 → auto_applicable=false."""
        ef = self.tmp_dir / "events.jsonl"
        emit_remediation_target(
            ef, spec="s", run_id="r",
            failure_class="spec_quality",
            rule_or_check="spec_validator",
            remediation_type="edit_spec",
            remediation_target_path=".dark-factory/specs/s.md",
            auto_applicable=False,
            rationale="verdict=FAIL score=60 blockers=2",
        )
        rec = read_jsonl(ef)[0]
        self.assertFalse(rec["data"]["auto_applicable"])
        self.assertIn("score=60", rec["data"]["rationale"])


class TestIsNovelRule(TempDirTestCase):
    def test_missing_file_returns_true(self) -> None:
        ef = self.tmp_dir / "does-not-exist.jsonl"
        self.assertTrue(is_novel_rule(ef, "any-rule"))

    def test_empty_file_returns_true(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        ef.touch()
        self.assertTrue(is_novel_rule(ef, "any-rule"))

    def test_ignores_non_target_events(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        # Write a SPEC_START record — should not match REMEDIATION_TARGET scan.
        with ef.open("w") as f:
            f.write(json.dumps({
                "ts": "2026-04-14T00:00:00Z",
                "event": "SPEC_START",
                "spec": "s",
                "data": {"rule_or_check": "should-be-ignored"},
            }) + "\n")
        self.assertTrue(is_novel_rule(ef, "should-be-ignored"))

    def test_malformed_jsonl_fails_safe_to_novel(self) -> None:
        """Unreadable history shouldn't silently mark repeat rules familiar."""
        ef = self.tmp_dir / "events.jsonl"
        ef.write_text("not valid json\n")
        # read_jsonl raises ValueError on malformed JSON; is_novel_rule
        # must catch and return True (fail-safe to novel).
        self.assertTrue(is_novel_rule(ef, "any-rule"))


class TestMostRecentTargetForSpec(TempDirTestCase):
    def test_returns_none_for_no_history(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        self.assertIsNone(most_recent_target_for_spec(ef, "s"))

    def test_returns_most_recent_for_matching_spec(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_remediation_target(
            ef, spec="spec-a", run_id="r1",
            failure_class="consistency_violation",
            rule_or_check="rule-1",
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="first",
        )
        emit_remediation_target(
            ef, spec="spec-a", run_id="r2",
            failure_class="consistency_violation",
            rule_or_check="rule-2",
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="second",
        )
        rec = most_recent_target_for_spec(ef, "spec-a")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["data"]["rule_or_check"], "rule-2")
        self.assertEqual(rec["run_id"], "r2")

    def test_filters_by_spec(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_remediation_target(
            ef, spec="spec-a", run_id="r1",
            failure_class="consistency_violation",
            rule_or_check="rule-a",
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="a",
        )
        emit_remediation_target(
            ef, spec="spec-b", run_id="r2",
            failure_class="consistency_violation",
            rule_or_check="rule-b",
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="b",
        )
        rec = most_recent_target_for_spec(ef, "spec-a")
        self.assertEqual(rec["data"]["rule_or_check"], "rule-a")


class TestClassifyOutcome(TempDirTestCase):
    def _seed_target(
        self, ef, spec: str = "s", run_id: str = "r1",
        rule: str = "rule-1",
    ) -> dict:
        emit_remediation_target(
            ef, spec=spec, run_id=run_id,
            failure_class="consistency_violation",
            rule_or_check=rule,
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="r",
        )
        rec = most_recent_target_for_spec(ef, spec)
        assert rec is not None
        return rec

    def test_shipped_wins(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        target = self._seed_target(ef)
        outcome = classify_outcome(
            ef, prior_target=target, this_run_id="r2",
            terminal_status="shipped",
        )
        self.assertEqual(outcome, "shipped")

    def test_same_failure_again(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        target = self._seed_target(ef, rule="same-rule")
        # Current run (r2) emits same rule
        emit_remediation_target(
            ef, spec="s", run_id="r2",
            failure_class="consistency_violation",
            rule_or_check="same-rule",
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="r",
        )
        outcome = classify_outcome(
            ef, prior_target=target, this_run_id="r2",
            terminal_status="failed",
        )
        self.assertEqual(outcome, "same_failure_again")

    def test_different_failure(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        target = self._seed_target(ef, rule="rule-a")
        emit_remediation_target(
            ef, spec="s", run_id="r2",
            failure_class="consistency_violation",
            rule_or_check="rule-b",
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="r",
        )
        outcome = classify_outcome(
            ef, prior_target=target, this_run_id="r2",
            terminal_status="failed",
        )
        self.assertEqual(outcome, "different_failure")

    def test_fixed_when_no_new_target_and_gates_ran(self) -> None:
        """When the rerun emits no new target AND pre-implementer gates
        executed, credit the remediation as ``fixed`` even if the run
        aborted later (implementer, oracle, deploy). Treating this as
        "unresolved" would penalize genuine fixes just because the
        pipeline failed elsewhere.
        """
        ef = self.tmp_dir / "events.jsonl"
        target = self._seed_target(ef)
        outcome = classify_outcome(
            ef, prior_target=target, this_run_id="r2",
            terminal_status="escalated", gates_executed=True,
        )
        self.assertEqual(outcome, "fixed")

        outcome_failed = classify_outcome(
            ef, prior_target=target, this_run_id="r2",
            terminal_status="failed", gates_executed=True,
        )
        self.assertEqual(outcome_failed, "fixed")

    def test_unresolved_when_gates_never_ran(self) -> None:
        """Abort-during-startup (budget exhausted before step 1,
        bootstrap exception, etc.) leaves the prior rule unverified.
        Crediting "fixed" here would inflate C5's auto-graduation
        counter against a rule that was never actually re-tested.
        """
        ef = self.tmp_dir / "events.jsonl"
        target = self._seed_target(ef)
        outcome = classify_outcome(
            ef, prior_target=target, this_run_id="r2",
            terminal_status="failed", gates_executed=False,
        )
        self.assertEqual(outcome, "unresolved")

        outcome_escalated = classify_outcome(
            ef, prior_target=target, this_run_id="r2",
            terminal_status="escalated", gates_executed=False,
        )
        self.assertEqual(outcome_escalated, "unresolved")


class TestMostRecentUnpairedTarget(TempDirTestCase):
    """Guards the chained-failure case: a run_N+1 that also emits its own
    target must NOT shadow run_N's still-unpaired target. Without
    exclude_run_id semantics, the terminal pairing logic would orphan
    run_N's target forever, breaking the 1:1 target↔outcome invariant
    that C5's auto-graduation depends on.
    """

    def _emit_target(self, ef, spec: str, run_id: str, rule: str) -> None:
        emit_remediation_target(
            ef, spec=spec, run_id=run_id,
            failure_class="consistency_violation",
            rule_or_check=rule,
            remediation_type="add_project_hint",
            remediation_target_path="x",
            auto_applicable=True,
            rationale="r",
        )

    def test_returns_none_for_no_history(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        self.assertIsNone(most_recent_unpaired_target(ef, "s"))

    def test_chained_failure_pairs_older_target(self) -> None:
        """T1 (r1, failed) → human edit → T2 (r2, also failed). At r2's
        terminal, the pairing lookup must return T1 (older, unpaired),
        not T2 (belongs to the current run).
        """
        ef = self.tmp_dir / "events.jsonl"
        self._emit_target(ef, spec="s", run_id="r1", rule="rule-a")
        self._emit_target(ef, spec="s", run_id="r2", rule="rule-b")

        rec = most_recent_unpaired_target(ef, "s", exclude_run_id="r2")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["run_id"], "r1")
        self.assertEqual(rec["data"]["rule_or_check"], "rule-a")

    def test_skips_already_paired_targets(self) -> None:
        """Once r1's target has an outcome event, it must not be paired
        twice — enforces 'exactly one outcome per target' across resumes.
        """
        ef = self.tmp_dir / "events.jsonl"
        self._emit_target(ef, spec="s", run_id="r1", rule="rule-a")
        emit_remediation_outcome(
            ef, spec="s", run_id="r2",
            remediation_run_id="r1",
            rule_or_check="rule-a",
            remediation_type="add_project_hint",
            outcome="fixed",
        )
        # After r1 is paired, no unpaired targets remain (current run r3
        # has nothing to pair — r2 is the one that closed r1).
        rec = most_recent_unpaired_target(ef, "s", exclude_run_id="r3")
        self.assertIsNone(rec)

    def test_excludes_current_run_targets(self) -> None:
        """When only the current run has emitted targets, nothing to pair."""
        ef = self.tmp_dir / "events.jsonl"
        self._emit_target(ef, spec="s", run_id="r1", rule="rule-a")
        rec = most_recent_unpaired_target(ef, "s", exclude_run_id="r1")
        self.assertIsNone(rec)

    def test_filters_by_spec(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        self._emit_target(ef, spec="spec-a", run_id="r1", rule="rule-a")
        self._emit_target(ef, spec="spec-b", run_id="r2", rule="rule-b")
        rec = most_recent_unpaired_target(ef, "spec-a", exclude_run_id="r99")
        self.assertIsNotNone(rec)
        self.assertEqual(rec["data"]["rule_or_check"], "rule-a")


class TestEmitRemediationOutcome(TempDirTestCase):
    def test_outcome_envelope(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        emit_remediation_outcome(
            ef,
            spec="s",
            run_id="r2",
            remediation_run_id="r1",
            rule_or_check="same-rule",
            remediation_type="add_project_hint",
            outcome="fixed",
            applied_by="auto",
        )
        rec = read_jsonl(ef)[0]
        self.assertEqual(rec["event"], "REMEDIATION_OUTCOME")
        self.assertEqual(rec["spec"], "s")
        self.assertEqual(rec["run_id"], "r2")
        self.assertEqual(rec["data"]["schema_version"], SCHEMA_VERSION)
        self.assertEqual(rec["data"]["remediation_run_id"], "r1")
        self.assertEqual(rec["data"]["rule_or_check"], "same-rule")
        self.assertEqual(rec["data"]["outcome"], "fixed")
        self.assertEqual(rec["data"]["applied_by"], "auto")
        # applied_at defaulted to now
        self.assertTrue(rec["data"]["applied_at"].endswith("Z"))


class TestBootstrappingCase(TempDirTestCase):
    """Plan acceptance: projects with no project-hints.toml still emit
    valid remediation_target events. Emission must not depend on policy
    lookup."""

    def test_emit_works_with_no_project_hints(self) -> None:
        # No project directory, no project-hints.toml, just an events file.
        ef = self.tmp_dir / "events.jsonl"
        emit_remediation_target(
            ef, spec="bootstrap-spec", run_id="r1",
            failure_class="precheck_finding",
            rule_or_check="precheck_structural",
            remediation_type="edit_spec",
            remediation_target_path=".dark-factory/specs/bootstrap-spec.md",
            auto_applicable=False,
            rationale="spec file is empty",
        )
        records = read_jsonl(ef)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["event"], "REMEDIATION_TARGET")
        # novel_rule_name is true because this is the very first remediation
        # event in the project
        self.assertTrue(records[0]["data"]["novel_rule_name"])


class TestA1SuggestedDiff(TempDirTestCase):
    """Plan acceptance: A1 fires → suggested_diff contains a TOML block
    a user could paste into project-hints.toml."""

    def test_consistency_diff_shape(self) -> None:
        from darkfactory.pipeline.orchestrator import _consistency_suggested_diff
        from darkfactory.architecture.consistency import ConsistencyError

        cerr = ConsistencyError(
            rule_name="migrate-mode-vs-push-mode",
            env_path="commands.setup",
            env_value="drizzle-kit push",
            spec_match="db:migrate",
            env_match="drizzle-kit push",
            description="Spec mandates CLI-driven migrations...",
        )
        diff = _consistency_suggested_diff(cerr)
        self.assertIn("[[policy.consistency_rules]]", diff)
        self.assertIn('name = "migrate-mode-vs-push-mode"', diff)
        self.assertIn("description =", diff)


class TestReadJsonlMalformed(TempDirTestCase):
    """Guard: is_novel_rule must fail safely when events.jsonl is unreadable."""

    def test_permission_error_returns_true(self) -> None:
        # Simulate unreadable file via a path that cannot be a file.
        ef = self.tmp_dir  # directory, not file → OSError on open
        self.assertTrue(is_novel_rule(ef, "rule"))


if __name__ == "__main__":
    unittest.main()
