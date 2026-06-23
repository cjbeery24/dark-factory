"""Tests for docker/barriers.py."""

import unittest

from darkfactory.docker.barriers import (
    configure_barriers,
    derive_compiled_output_dirs,
)
from darkfactory.models.agent import AgentRole


class TestBarriers(unittest.TestCase):
    # Phase 1 (B-bar-test) anchor: the assertions in
    # ``test_implementer_masks_holdouts_not_visible_scenarios`` below
    # are the validator-bridge invariants pinned by ``docs/ROADMAP.md``
    # Phase 1 (``_HOLDOUTS in implementer_masked`` and
    # ``_REPORTS in implementer_masked``). The third Phase 1 invariant
    # (``_SCENARIOS in qa_masked``) is pinned in
    # ``TestPhase1BarrierInvariants`` below. Together they are the
    # regression backstop for the audience-escalation failure mode
    # described in ``docs/AUTHORIZATION-MODEL.md`` (``d2fecbf``).
    def test_implementer_masks_holdouts_not_visible_scenarios(self) -> None:
        """Phase 4.6b.1: Implementer reads Test Engineer's visible tests
        under .dark-factory/scenarios/ but cannot see QA Holdout's
        adversarial tests under .dark-factory/holdouts/."""
        barriers = configure_barriers(["src"])
        impl = barriers[AgentRole.IMPLEMENTER]
        self.assertIn(".dark-factory/holdouts", impl.masked_dirs)
        self.assertIn(".dark-factory/reports", impl.masked_dirs)
        self.assertIn(".git", impl.masked_dirs)
        self.assertNotIn(".dark-factory/scenarios", impl.masked_dirs)

    def test_scenario_architect_masks_source(self) -> None:
        barriers = configure_barriers(["src", "lib"])
        sa = barriers[AgentRole.SCENARIO_ARCHITECT]
        self.assertIn("src", sa.masked_dirs)
        self.assertIn("lib", sa.masked_dirs)
        self.assertIn(".git", sa.masked_dirs)

    def test_oracle_no_masks(self) -> None:
        barriers = configure_barriers(["src"])
        oracle = barriers[AgentRole.ORACLE]
        self.assertEqual(oracle.masked_dirs, [])

    def test_all_roles_present(self) -> None:
        barriers = configure_barriers(["src"])
        for role in AgentRole:
            self.assertIn(role, barriers)

    def test_implementer_has_barrier_prompt(self) -> None:
        barriers = configure_barriers(["src"])
        self.assertIn("MUST NOT", barriers[AgentRole.IMPLEMENTER].barrier_prompt)

    def test_implementer_failures_dir_is_masked(self) -> None:
        barriers = configure_barriers(["src"])
        self.assertIn(".dark-factory/failures", barriers[AgentRole.IMPLEMENTER].masked_dirs)

    def test_failures_dir_masked_for_all_restricted_roles(self) -> None:
        # S0(a): the failure-dump full-stream sidecar lives UNDER failures/ and
        # relies on failures/ being a directory-level mask for EVERY barrier-
        # restricted role (the raw stream carries tool_use paths + source
        # excerpts). Only the no-mask roles (Oracle/Escalation) are exempt —
        # they see everything by design. Codex nit: prior tests covered 2 roles.
        barriers = configure_barriers(["src"])
        unrestricted = {AgentRole.ORACLE, AgentRole.ESCALATION}
        for role, cfg in barriers.items():
            if role in unrestricted:
                continue
            self.assertIn(
                ".dark-factory/failures", cfg.masked_dirs,
                f"failures/ must be masked for {role.value}",
            )

    def test_architect_masks_scenarios_not_source(self) -> None:
        barriers = configure_barriers(["src", "lib"])
        arch = barriers[AgentRole.ARCHITECT]
        self.assertIn(".dark-factory/scenarios", arch.masked_dirs)
        self.assertIn(".dark-factory/audit", arch.masked_dirs)
        self.assertIn(".dark-factory/state", arch.masked_dirs)
        self.assertIn(".dark-factory/failures", arch.masked_dirs)
        self.assertIn(".git", arch.masked_dirs)
        # Architect CAN read source code (unlike SA) — needs codebase conventions.
        self.assertNotIn("src", arch.masked_dirs)
        self.assertNotIn("lib", arch.masked_dirs)
        # Architect CAN read and write reports (generation report output).
        self.assertNotIn(".dark-factory/reports", arch.masked_dirs)

    def test_architect_denied_tools(self) -> None:
        barriers = configure_barriers(["src"])
        arch = barriers[AgentRole.ARCHITECT]
        self.assertIn("WebSearch", arch.denied_tools)
        self.assertIn("WebFetch", arch.denied_tools)

    def test_architect_write_dirs(self) -> None:
        barriers = configure_barriers(["src"])
        arch = barriers[AgentRole.ARCHITECT]
        self.assertIn(".dark-factory/architecture", arch.write_dirs)
        self.assertIn(".dark-factory/reports", arch.write_dirs)

    def test_architect_has_barrier_prompt(self) -> None:
        barriers = configure_barriers(["src"])
        self.assertIn("MUST NOT", barriers[AgentRole.ARCHITECT].barrier_prompt)


class TestWriteDirsContract(unittest.TestCase):
    """Phase 4.6b.1c: write_dirs regression guards.

    Run 8 crashed in the Defect Classifier branch because
    `DEFECT_CLASSIFIER` was missing `write_dirs=[".dark-factory/reports"]`.
    Without the declaration, the container got no rw overlay for
    reports/, the agent's Write silently failed, and `_step_classify`
    stamped the `classifier_no_output` error stub for every iteration,
    forcing NO-SHIP via safety rule 4.6b.0.iv.
    """

    def test_defect_classifier_can_write_reports(self) -> None:
        """Targeted regression for the Run 8 bug. Documents intent:
        DEFECT_CLASSIFIER must be able to write its defects.json sidecar
        to .dark-factory/reports/.
        """
        barriers = configure_barriers(["src"])
        classifier = barriers[AgentRole.DEFECT_CLASSIFIER]
        self.assertIn(".dark-factory/reports", classifier.write_dirs)

    def test_role_barriers_write_dirs_snapshot(self) -> None:
        """Golden snapshot of every role's declared write_dirs.

        Any silent edit to `configure_barriers` (adding a role, forgetting
        a write_dirs entry, accidentally dropping one) fails CI with a
        visible diff. Permanent guardrail against the Run 8 class of
        bug for every current and future role.

        When a legitimate barrier change lands, update this snapshot in
        the same commit so the diff is auditable.
        """
        barriers = configure_barriers(["src"])
        actual = {
            role.value: sorted(cfg.write_dirs)
            for role, cfg in barriers.items()
        }
        expected = {
            "architect": sorted([".dark-factory/architecture", ".dark-factory/reports"]),
            "defect-classifier": [".dark-factory/reports"],
            "escalation": [],
            "evaluator": [".dark-factory/reports"],
            "implementer": [],
            "oracle": [".dark-factory/reviews"],
            "proposal-rehearsal": [],
            "qa-holdout": sorted([".dark-factory/holdouts", ".dark-factory/reports"]),
            "reviewer": [".dark-factory/reviews"],
            "scenario-architect": sorted([".dark-factory/scenarios", ".dark-factory/reports"]),
            "spec-validator": [".dark-factory/reviews"],
            # Phase 5 Stage 3 (2026-04-25): substrate-owner writes its
            # JSON output under .dark-factory/architecture/substrate-prep/
            # (adjacent to architect manifest; not masked) and a human
            # report under .dark-factory/reports/.
            "substrate-owner": sorted([".dark-factory/architecture", ".dark-factory/reports"]),
            # Task #22: the Test Engineer no longer writes to reports/ (the
            # {spec}-scenarios.md write was dead — zero readers). reports/ is
            # now masked from the TE; its only output is scenarios/.
            "test-engineer": [".dark-factory/scenarios"],
        }
        self.assertEqual(actual, expected)


class TestTestEngineerReportsMask(unittest.TestCase):
    """Task #22: ``.dark-factory/reports/`` is masked from the TEST_ENGINEER.

    Prior-iteration gate reports + defect-classifier JSON under reports/
    have SEEN the implementation and gate verdict. Leaving the directory
    filesystem-reachable inside the blind TE container was a latent
    ``d2fecbf``-class audience-escalation surface. The TE has no
    legitimate reason to read or write reports/ (its only output is
    scenarios/; the dead {spec}-scenarios.md write was removed).
    """

    def test_te_masks_reports(self) -> None:
        te = configure_barriers(["src"])[AgentRole.TEST_ENGINEER]
        self.assertIn(".dark-factory/reports", te.masked_dirs)

    def test_te_does_not_write_reports(self) -> None:
        te = configure_barriers(["src"])[AgentRole.TEST_ENGINEER]
        self.assertNotIn(".dark-factory/reports", te.write_dirs)

    def test_qa_holdout_still_writes_reports(self) -> None:
        """The change is scoped to the TE. QA_HOLDOUT keeps its reports/
        write — its holdouts.md has a live Reviewer reader.
        """
        qa = configure_barriers(["src"])[AgentRole.QA_HOLDOUT]
        self.assertIn(".dark-factory/reports", qa.write_dirs)


class TestPhase1BarrierInvariants(unittest.TestCase):
    """Phase 1 (B-bar-test) anchor — third validator-bridge invariant
    not already covered by ``test_implementer_masks_holdouts_not_visible_scenarios``.

    PLAN.md originally proposed three assertions including
    ``_SCENARIOS in implementer_masked``. Verification per
    ``docs/AUTHORIZATION-MODEL.md``'s own rules found that would
    invert the Phase 4.6b.1 design: the Implementer reads the Test
    Engineer's visible AC-aligned scenarios as TDD targets
    (barriers.py:55-73). The corrected pinning is split: two of the
    three invariants are pinned by the existing implementer test
    above (anchor comment there); the third — the QA Holdout's mask
    of ``.dark-factory/scenarios`` — is pinned here.
    """

    def test_qa_holdout_cannot_read_visible_scenarios(self) -> None:
        """``_SCENARIOS in qa_masked``. The QA Holdout writes adversarial
        tests from the spec alone — reading the Test Engineer's visible
        scenarios would bias exploration toward already-covered anchors
        and defeat the split (barriers.py:123-128).
        """
        barriers = configure_barriers(["src"])
        qa = barriers[AgentRole.QA_HOLDOUT]
        self.assertIn(".dark-factory/scenarios", qa.masked_dirs)


class TestCodeBlindRolesMaskCode(unittest.TestCase):
    """The TE/QA (and the other spec-only roles) derive tests/analysis from the
    spec, not from any code — source OR compiled output. A missing mask silently
    removes a barrier with no test failure (docs/reviews/2026-03-03-v4-critical-
    evaluation.md), so each direction is pinned explicitly here.
    """

    # Roles whose epistemic premise is "from the spec alone" — they mask source
    # and must mask compiled output too.
    CODE_BLIND = (
        AgentRole.TEST_ENGINEER, AgentRole.QA_HOLDOUT, AgentRole.SPEC_VALIDATOR,
        AgentRole.EVALUATOR, AgentRole.PROPOSAL_REHEARSAL,
        AgentRole.SCENARIO_ARCHITECT,
    )
    # Roles that legitimately READ code — must NOT carry the compiled-output flag.
    CODE_READING = (
        AgentRole.IMPLEMENTER, AgentRole.ARCHITECT, AgentRole.REVIEWER,
        AgentRole.SUBSTRATE_OWNER,
    )

    def test_test_engineer_masks_source(self) -> None:
        """The missing invariant (Neo): the TE never had a source-mask test, so
        a refactor could silently widen the hole. The TE is spec-only."""
        te = configure_barriers(["apps/api/src", "lib"])[AgentRole.TEST_ENGINEER]
        self.assertIn("apps/api/src", te.masked_dirs)
        self.assertIn("lib", te.masked_dirs)

    def test_code_blind_roles_mask_source_and_flag_compiled(self) -> None:
        barriers = configure_barriers(["apps/api/src"])
        for role in self.CODE_BLIND:
            cfg = barriers[role]
            self.assertIn("apps/api/src", cfg.masked_dirs, role)
            self.assertTrue(cfg.mask_compiled_outputs, role)

    def test_code_reading_roles_do_not_flag_compiled(self) -> None:
        barriers = configure_barriers(["apps/api/src"])
        for role in self.CODE_READING:
            self.assertFalse(
                barriers[role].mask_compiled_outputs, role,
            )


class TestDeriveCompiledOutputDirs(unittest.TestCase):
    def test_derives_package_root_siblings(self) -> None:
        out = derive_compiled_output_dirs(["apps/api/src"])
        self.assertIn("apps/api/dist", out)
        self.assertIn("apps/api/build", out)
        self.assertIn("apps/api/target", out)
        # package-root relative, not nested under src/
        self.assertNotIn("apps/api/src/dist", out)

    def test_top_level_source_dir(self) -> None:
        out = derive_compiled_output_dirs(["src"])
        self.assertIn("dist", out)
        self.assertIn("__pycache__", out)

    def test_excludes_ambiguous_source_names(self) -> None:
        # `lib` / `bin` are commonly SOURCE — must not be derived as outputs.
        out = derive_compiled_output_dirs(["apps/api/src"])
        self.assertNotIn("apps/api/lib", out)
        self.assertNotIn("apps/api/bin", out)

    def test_dedup_across_source_dirs(self) -> None:
        out = derive_compiled_output_dirs(["src", "src"])
        self.assertEqual(len(out), len(set(out)))


if __name__ == "__main__":
    unittest.main()
