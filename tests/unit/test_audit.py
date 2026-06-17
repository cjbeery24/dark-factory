"""Tests for audit/logger.py and audit/checker.py."""

import unittest
from pathlib import Path

from tests.helpers import TempDirTestCase
from darkfactory.audit.logger import audit_log, audit_summary
from darkfactory.audit.checker import check_output_for_barriers, validate_oracle_feedback


class TestAuditLogger(TempDirTestCase):
    def test_log_creates_file(self) -> None:
        log_path = self.tmp_dir / "audit.log"
        audit_log(log_path, "test message")
        self.assertTrue(log_path.exists())
        content = log_path.read_text()
        self.assertIn("test message", content)
        self.assertIn("[20", content)  # Timestamp.

    def test_log_appends(self) -> None:
        log_path = self.tmp_dir / "audit.log"
        audit_log(log_path, "first")
        audit_log(log_path, "second")
        lines = log_path.read_text().strip().splitlines()
        self.assertEqual(len(lines), 2)

    def test_summary(self) -> None:
        log_path = self.tmp_dir / "audit.log"
        audit_log(log_path, "test")
        summary = audit_summary(log_path)
        self.assertIn("test", summary)

    def test_summary_missing(self) -> None:
        self.assertEqual(audit_summary(self.tmp_dir / "nope.log"), "")


class TestBarrierChecker(TempDirTestCase):
    def test_no_violations(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "clean output", [".dark-factory/scenarios"],
            "implementer", al, ef, "s", "r",
        )
        self.assertEqual(violations, [])

    def test_detects_violation(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "I read .dark-factory/scenarios/my-spec/01-contract.test.ts",
            [".dark-factory/scenarios", ".dark-factory/reports"],
            "implementer", al, ef, "s", "r",
        )
        self.assertEqual(violations, [".dark-factory/scenarios"])

    # --- Sprint 16 option (b): source-path echo subtraction ---

    def test_source_echo_suppressed_when_in_given_inputs(self) -> None:
        # A source dir echoed from the agent's given inputs (manifest specifier /
        # spec / agent-def) is not a read — suppress it.
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "import { createServer } from '../../../apps/api/src/server.js'",
            ["apps/api/src"], "test-engineer", al, ef, "s", "r",
            excluded_text="manifest specifier: ../../../apps/api/src/server.js",
            subtractable_paths=["apps/api/src"],
        )
        self.assertEqual(violations, [])

    def test_source_echo_fires_when_not_in_given_inputs(self) -> None:
        # Same path, but NOT present in the given inputs → real signal, fires.
        # (Locks Neo Finding 1: a caller that fails to include the true input set
        # — e.g. omits the agent-def — must still see the violation.)
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "leaked apps/api/src/secret.ts", ["apps/api/src"],
            "test-engineer", al, ef, "s", "r",
            excluded_text="spec text that does not name the source dir",
            subtractable_paths=["apps/api/src"],
        )
        self.assertEqual(violations, ["apps/api/src"])

    # --- P2: .dark-factory/scenarios subtractable (architect addendum echo) ---

    def test_scenarios_echo_suppressed_when_in_architect_inputs(self) -> None:
        # The architect's ecosystem addendum names .dark-factory/scenarios (where
        # the TE writes); echoing it is not a read (the dir is tmpfs-masked). With
        # scenarios subtractable AND present in excluded_text → suppressed.
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "entry_points resolve under .dark-factory/scenarios/my-spec/",
            [".dark-factory/scenarios"], "architect", al, ef, "s", "r",
            excluded_text="addendum: the Test Engineer writes .dark-factory/scenarios/<spec>/*.test.ts",
            subtractable_paths=["apps/api/src", ".dark-factory/scenarios"],
        )
        self.assertEqual(violations, [])

    def test_scenarios_echo_fires_when_not_in_inputs(self) -> None:
        # Self-limiting gate: a role whose inputs do NOT name scenarios (e.g.
        # qa-holdout) still fires on a scenarios echo, even though scenarios is
        # globally subtractable. This is the property that keeps the fix safe.
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "read .dark-factory/scenarios/my-spec/01-contract.test.ts",
            [".dark-factory/scenarios"], "qa-holdout", al, ef, "s", "r",
            excluded_text="holdout instructions that never name the scenarios dir",
            subtractable_paths=["apps/api/src", ".dark-factory/scenarios"],
        )
        self.assertEqual(violations, [".dark-factory/scenarios"])

    def test_holdouts_always_fires_even_when_subtractable_includes_scenarios(self) -> None:
        # Safety: holdouts is NOT subtractable; even echoed in inputs (and even
        # with scenarios added to subtractable), it always fires.
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "saw .dark-factory/holdouts/my-spec/adv.test.ts",
            [".dark-factory/holdouts"], "architect", al, ef, "s", "r",
            excluded_text=".dark-factory/holdouts mentioned in inputs",
            subtractable_paths=["apps/api/src", ".dark-factory/scenarios"],
        )
        self.assertEqual(violations, [".dark-factory/holdouts"])

    def test_qa_holdout_def_does_not_name_scenarios(self) -> None:
        # DEFENSE-IN-DEPTH hygiene (the PRIMARY safety is the call-site carve-out
        # in agents.py that keeps scenarios out of qa-holdout's subtractable set —
        # see test_agents.test_qa_holdout_scenarios_echo_STILL_fires...). Keeping
        # the adversarial-blind holdout's def free of the scenarios string is good
        # hygiene; this fails loudly if a future edit adds it.
        import darkfactory
        qa_def = (Path(darkfactory.__file__).parent.parent / "agents" / "qa-holdout.md")
        self.assertNotIn("scenarios", qa_def.read_text(encoding="utf-8"))

    def test_non_subtractable_path_always_fires(self) -> None:
        # Holdouts/.git are NOT subtractable: even if echoed in given inputs, an
        # echo is a real signal and must fire. This is the safety property.
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "referenced .dark-factory/holdouts/adversarial.test.ts",
            [".dark-factory/holdouts"], "test-engineer", al, ef, "s", "r",
            excluded_text="prompt mentions .dark-factory/holdouts somehow",
            subtractable_paths=["apps/api/src"],  # holdouts NOT in subtractable
        )
        self.assertEqual(violations, [".dark-factory/holdouts"])

    def test_no_subtraction_without_subtractable_paths(self) -> None:
        # Default (subtractable_paths omitted) → original blunt behavior, fires.
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "apps/api/src echoed", ["apps/api/src"],
            "test-engineer", al, ef, "s", "r",
            excluded_text="apps/api/src is in inputs",  # but no subtractable set
        )
        self.assertEqual(violations, ["apps/api/src"])

    def test_explicit_none_subtractable_paths_fires(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "apps/api/src echoed", ["apps/api/src"],
            "test-engineer", al, ef, "s", "r",
            excluded_text="apps/api/src", subtractable_paths=None,
        )
        self.assertEqual(violations, ["apps/api/src"])

    def test_git_echo_always_fires(self) -> None:
        # .git is never subtractable — an echo always fires (safety property).
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "ran git log in .git/", [".git"], "test-engineer", al, ef, "s", "r",
            excluded_text="prompt incidentally mentions .git",
            subtractable_paths=["apps/api/src"],  # .git NOT subtractable
        )
        self.assertEqual(violations, [".git"])

    def test_empty_excluded_text_fires(self) -> None:
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "apps/api/src echoed", ["apps/api/src"],
            "test-engineer", al, ef, "s", "r",
            excluded_text="", subtractable_paths=["apps/api/src"],
        )
        self.assertEqual(violations, ["apps/api/src"])

    def test_single_segment_source_dir_not_over_suppressed(self) -> None:
        # Codex BLOCKING: a bare single-segment source dir ('src') is too generic
        # to subtract — it must STILL fire even when 'src' appears in the inputs,
        # so a 'src'-rooted project can't silently swallow real violations.
        ef = self.tmp_dir / "events.jsonl"
        al = self.tmp_dir / "audit.log"
        violations = check_output_for_barriers(
            "leaked src/secret.ts", ["src"], "test-engineer", al, ef, "s", "r",
            excluded_text="the spec mentions src/ all over and import.meta",
            subtractable_paths=["src"],
        )
        self.assertEqual(violations, ["src"])


class TestCollectBarrierExcludedText(TempDirTestCase):
    """Sprint 16 option (b): excluded_text assembly includes the agent-def body."""

    def test_includes_agent_def_and_manifest_bodies(self) -> None:
        from darkfactory.models.agent import AgentRole
        from darkfactory.pipeline.agents import _collect_barrier_excluded_text
        df = self.tmp_dir / ".dark-factory"
        (df / "specs").mkdir(parents=True)
        (df / "architecture").mkdir(parents=True)
        (df / "agents").mkdir(parents=True)
        (df / "specs" / "demo.md").write_text("spec body SPEC_MARKER", encoding="utf-8")
        (df / "architecture" / "demo-manifest.yaml").write_text(
            "specifier: ../../../apps/api/src/server.js", encoding="utf-8")
        (df / "agents" / "test-engineer.md").write_text(
            "example: ../../../apps/api/src/server.js AGENTDEF_MARKER", encoding="utf-8")
        text = _collect_barrier_excluded_text(
            self.tmp_dir, "demo", AgentRole.TEST_ENGINEER, "PROMPT_MARKER",
        )
        self.assertIn("PROMPT_MARKER", text)
        self.assertIn("SPEC_MARKER", text)
        self.assertIn("AGENTDEF_MARKER", text)
        self.assertIn("apps/api/src", text)  # the load-bearing manifest specifier


class TestOracleFeedback(TempDirTestCase):
    def test_clean_feedback(self) -> None:
        al = self.tmp_dir / "audit.log"
        fb = 'Focus on FR-1 compliance\n<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'
        result = validate_oracle_feedback(fb, al)
        self.assertTrue(result.is_compliant)
        self.assertEqual(result.violations, [])

    def test_scenario_filename_leak(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "The test 01-contract.test.ts is failing", al,
        )
        self.assertFalse(result.is_compliant)
        self.assertGreater(len(result.violations), 0)

    def test_assertion_leak(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "expect(result).toBe(true) is not passing", al,
        )
        self.assertFalse(result.is_compliant)
        self.assertGreater(len(result.violations), 0)

    def test_count_leakage_fraction(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "Only 64/73 scenarios passed in this iteration.", al,
        )
        self.assertFalse(result.is_compliant)

    def test_count_leakage_percentage(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "The 87% pass rate is insufficient for shipping.", al,
        )
        self.assertFalse(result.is_compliant)

    def test_count_leakage_quantified(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "All 73 scenarios must pass before shipping.", al,
        )
        self.assertFalse(result.is_compliant)

    def test_count_leakage_verb_based_no_count(self) -> None:
        """4.6b.1g: 'no scenarios passed' leaks distribution even without a count."""
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "Unfortunately no scenarios passed in this iteration.", al,
        )
        self.assertFalse(result.is_compliant)

    def test_count_leakage_verb_based_all_failed(self) -> None:
        """4.6b.1g: 'all scenarios failed' leaks distribution."""
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "Essentially all scenarios failed due to missing handler.", al,
        )
        self.assertFalse(result.is_compliant)

    def test_bare_all_scenarios_no_false_positive(self) -> None:
        """4.6b.1g regression (Run 11): bare 'all scenarios' with no count and
        no verb is generic English, not a leak."""
        al = self.tmp_dir / "audit.log"
        fb = (
            "Focus on AC-1: all scenarios covering the startup path "
            "exercise structured logging. FR-2 remains the bottleneck.\n"
            '<!-- CLASSIFICATIONS: {"AC-1": "IMPL_BUG"} -->'
        )
        result = validate_oracle_feedback(fb, al)
        self.assertTrue(result.is_compliant)

    def test_no_false_positive_on_spec_refs(self) -> None:
        """Spec section references like FR-1/FR-2 should not trigger."""
        al = self.tmp_dir / "audit.log"
        fb = (
            "Focus on FR-1 and EC-3. The deployment config needs attention.\n"
            '<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG", "EC-3": "SPEC_AMBIGUOUS"} -->'
        )
        result = validate_oracle_feedback(fb, al)
        self.assertTrue(result.is_compliant)

    def test_violation_summary(self) -> None:
        al = self.tmp_dir / "audit.log"
        result = validate_oracle_feedback(
            "64/73 tests passed in 01-contract.test.ts", al,
        )
        self.assertFalse(result.is_compliant)
        self.assertIn("barrier rule", result.violation_summary)

    def test_classifications_block_present_no_warning(self) -> None:
        al = self.tmp_dir / "audit.log"
        fb = 'Focus on FR-1.\n<!-- CLASSIFICATIONS: {"FR-1": "IMPL_BUG"} -->'
        validate_oracle_feedback(fb, al)
        log_text = al.read_text() if al.exists() else ""
        self.assertNotIn("CLASSIFICATIONS block absent", log_text)

    def test_classifications_block_absent_is_violation(self) -> None:
        al = self.tmp_dir / "audit.log"
        fb = "Focus on FR-1 compliance. Re-read the spec."
        result = validate_oracle_feedback(fb, al)
        self.assertFalse(result.is_compliant)
        self.assertTrue(any("CLASSIFICATIONS" in v for v in result.violations))
        log_text = al.read_text()
        self.assertIn("CLASSIFICATIONS block absent", log_text)


if __name__ == "__main__":
    unittest.main()
