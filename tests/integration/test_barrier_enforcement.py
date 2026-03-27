"""Integration tests for information barrier enforcement.

Three layers:
(a) Mount/tmpfs configuration validation (no Docker needed)
(b) Symlink/hardlink safety checks with real filesystem
(c) Audit barrier detection with real file I/O (no Docker needed)

Docker-dependent container barrier tests are marked @pytest.mark.docker.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from darkfactory.audit.checker import check_output_for_barriers, validate_oracle_feedback
from darkfactory.docker.barriers import configure_barriers
from darkfactory.docker.mounts import (
    build_project_mount,
    build_tmpfs_masks,
    check_hardlink_safety,
    check_symlink_safety,
)
from darkfactory.errors import BarrierViolation
from darkfactory.models.agent import AgentRole


@pytest.mark.integration
class TestBarrierConfiguration:
    """Verify barrier configs match the documented spec for all 7 roles."""

    def test_all_roles_have_config(self):
        barriers = configure_barriers(["src"])
        for role in AgentRole:
            assert role in barriers, f"missing barrier config for {role.value}"

    def test_implementer_masks(self):
        barriers = configure_barriers(["src", "lib"])
        impl = barriers[AgentRole.IMPLEMENTER]
        assert ".dark-factory/scenarios" in impl.masked_dirs
        assert ".dark-factory/reports" in impl.masked_dirs
        assert ".dark-factory/audit" in impl.masked_dirs
        assert ".dark-factory/state" in impl.masked_dirs
        assert ".git" in impl.masked_dirs
        # Implementer does NOT mask source dirs
        assert "src" not in impl.masked_dirs

    def test_scenario_architect_masks_source(self):
        barriers = configure_barriers(["src", "lib"])
        sa = barriers[AgentRole.SCENARIO_ARCHITECT]
        assert "src" in sa.masked_dirs
        assert "lib" in sa.masked_dirs
        assert ".git" in sa.masked_dirs
        # SA does NOT mask scenarios
        assert ".dark-factory/scenarios" not in sa.masked_dirs

    def test_reviewer_masks(self):
        barriers = configure_barriers(["src"])
        rev = barriers[AgentRole.REVIEWER]
        assert ".dark-factory/scenarios" in rev.masked_dirs
        assert ".dark-factory/reports" in rev.masked_dirs
        assert ".git" in rev.masked_dirs

    def test_evaluator_masks_source_and_git(self):
        barriers = configure_barriers(["src", "lib"])
        ev = barriers[AgentRole.EVALUATOR]
        assert "src" in ev.masked_dirs
        assert "lib" in ev.masked_dirs
        assert ".git" in ev.masked_dirs
        # Evaluator blocks network tools (primary barrier is tmpfs, denied_tools is defense-in-depth)
        assert "WebSearch" in ev.denied_tools

    def test_oracle_no_barriers(self):
        barriers = configure_barriers(["src"])
        oracle = barriers[AgentRole.ORACLE]
        assert oracle.masked_dirs == []
        assert oracle.denied_tools == []
        assert oracle.barrier_prompt == ""

    def test_spec_validator_no_barriers(self):
        barriers = configure_barriers(["src"])
        sv = barriers[AgentRole.SPEC_VALIDATOR]
        assert sv.masked_dirs == []
        assert sv.denied_tools == []

    def test_escalation_no_barriers(self):
        barriers = configure_barriers(["src"])
        esc = barriers[AgentRole.ESCALATION]
        assert esc.masked_dirs == []

    def test_multiple_source_dirs(self):
        barriers = configure_barriers(["src", "lib", "pkg"])
        sa = barriers[AgentRole.SCENARIO_ARCHITECT]
        assert "src" in sa.masked_dirs
        assert "lib" in sa.masked_dirs
        assert "pkg" in sa.masked_dirs


@pytest.mark.integration
class TestMountConfiguration:
    """Project mount mode per role."""

    def test_rw_mount(self):
        args = build_project_mount("/project", rw=True)
        assert args == ["-v", "/project:/project:rw"]

    def test_ro_mount(self):
        args = build_project_mount("/project", rw=False)
        assert args == ["-v", "/project:/project:ro"]

    def test_tmpfs_masks_produce_correct_args(self):
        args = build_tmpfs_masks("/project", [
            ".dark-factory/scenarios", ".dark-factory/reports", ".git",
        ])
        assert len(args) == 6  # 3 pairs of --tmpfs + path
        paths = [args[i + 1] for i in range(0, len(args), 2)]
        assert "/project/.dark-factory/scenarios:ro,size=0" in paths
        assert "/project/.dark-factory/reports:ro,size=0" in paths
        assert "/project/.git:ro,size=0" in paths

    def test_tmpfs_empty_list(self):
        args = build_tmpfs_masks("/project", [])
        assert args == []


@pytest.mark.integration
class TestSymlinkSafety:
    """Symlink escape detection with real filesystem."""

    def test_masked_dir_is_symlink_raises(self, tmp_path: Path):
        # Create a real target and a symlink to it at the masked path
        real_dir = tmp_path / "real_scenarios"
        real_dir.mkdir()
        (real_dir / "secret.py").write_text("secret")

        masked_dir = tmp_path / ".dark-factory" / "scenarios"
        masked_dir.parent.mkdir(parents=True)
        masked_dir.symlink_to(real_dir)

        with pytest.raises(BarrierViolation, match="symlink"):
            check_symlink_safety(
                tmp_path,
                [".dark-factory/scenarios"],
            )

    def test_symlink_in_visible_dir_to_masked_raises(self, tmp_path: Path):
        # Create masked dir with content
        masked = tmp_path / ".dark-factory" / "scenarios"
        masked.mkdir(parents=True)
        (masked / "test.py").write_text("secret")

        # Create visible dir with symlink pointing into masked
        src = tmp_path / "src"
        src.mkdir()
        (src / "leak").symlink_to(masked)

        with pytest.raises(BarrierViolation, match="points into masked"):
            check_symlink_safety(
                tmp_path,
                [".dark-factory/scenarios"],
                visible_dirs=["src"],
            )

    def test_no_symlinks_passes(self, tmp_path: Path):
        masked = tmp_path / ".dark-factory" / "scenarios"
        masked.mkdir(parents=True)
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("legit code")

        # Should not raise
        check_symlink_safety(
            tmp_path,
            [".dark-factory/scenarios"],
            visible_dirs=["src"],
        )

    def test_no_visible_dirs_skips_reverse_check(self, tmp_path: Path):
        masked = tmp_path / ".dark-factory" / "scenarios"
        masked.mkdir(parents=True)

        # Should not raise even without visible_dirs
        check_symlink_safety(tmp_path, [".dark-factory/scenarios"])

    def test_chained_symlinks_detected(self, tmp_path: Path):
        """Symlink -> symlink -> masked dir is caught by .resolve()."""
        masked = tmp_path / ".dark-factory" / "scenarios"
        masked.mkdir(parents=True)
        (masked / "secret.py").write_text("secret")

        # Chain: src/link1 -> intermediate -> masked
        intermediate = tmp_path / "intermediate"
        intermediate.symlink_to(masked)

        src = tmp_path / "src"
        src.mkdir()
        (src / "link1").symlink_to(intermediate)

        with pytest.raises(BarrierViolation, match="points into masked"):
            check_symlink_safety(
                tmp_path,
                [".dark-factory/scenarios"],
                visible_dirs=["src"],
            )


@pytest.mark.integration
class TestHardlinkSafety:
    """Hardlink escape detection with real filesystem."""

    def test_hardlink_from_source_to_masked_raises(self, tmp_path: Path):
        masked = tmp_path / ".dark-factory" / "scenarios"
        masked.mkdir(parents=True)
        secret = masked / "secret.py"
        secret.write_text("secret test content")

        src = tmp_path / "src"
        src.mkdir()
        # Create hardlink
        os.link(str(secret), str(src / "leak.py"))

        with pytest.raises(BarrierViolation, match="hardlink"):
            check_hardlink_safety(
                tmp_path,
                source_dirs=["src"],
                masked_dirs=[".dark-factory/scenarios"],
            )

    def test_no_hardlinks_passes(self, tmp_path: Path):
        masked = tmp_path / ".dark-factory" / "scenarios"
        masked.mkdir(parents=True)
        (masked / "secret.py").write_text("secret")

        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("different content")

        check_hardlink_safety(
            tmp_path,
            source_dirs=["src"],
            masked_dirs=[".dark-factory/scenarios"],
        )

    def test_nonexistent_masked_dir_passes(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "main.py").write_text("code")

        # Masked dir doesn't exist — should not raise
        check_hardlink_safety(
            tmp_path,
            source_dirs=["src"],
            masked_dirs=["nonexistent"],
        )


@pytest.mark.integration
class TestAuditBarrierDetection:
    """check_output_for_barriers with real file I/O."""

    def test_violation_detected(self, project_dir: Path):
        audit_path = project_dir / ".dark-factory" / "audit" / "test.log"
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        output = "I found scenarios at .dark-factory/scenarios/test.py"
        violations = check_output_for_barriers(
            output,
            [".dark-factory/scenarios"],
            "implementer",
            audit_path,
            events_file,
            spec_name="test-spec",
            run_id="run123",
        )
        assert len(violations) == 1
        assert ".dark-factory/scenarios" in violations[0]
        # Audit log should have been written
        assert audit_path.exists()

    def test_clean_output_no_violations(self, project_dir: Path):
        audit_path = project_dir / ".dark-factory" / "audit" / "test.log"
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        output = "Implemented the add function as specified in FR-1."
        violations = check_output_for_barriers(
            output,
            [".dark-factory/scenarios", ".dark-factory/reports"],
            "implementer",
            audit_path,
            events_file,
            spec_name="test-spec",
            run_id="run123",
        )
        assert violations == []

    def test_multiple_violations(self, project_dir: Path):
        audit_path = project_dir / ".dark-factory" / "audit" / "test.log"
        events_file = project_dir / ".dark-factory" / "events.jsonl"

        output = (
            "Reading .dark-factory/scenarios/test.py and "
            ".dark-factory/reports/gate.md for context"
        )
        violations = check_output_for_barriers(
            output,
            [".dark-factory/scenarios", ".dark-factory/reports"],
            "implementer",
            audit_path,
            events_file,
            spec_name="test-spec",
            run_id="run123",
        )
        assert len(violations) == 2


@pytest.mark.integration
class TestOracleFeedbackLeakage:
    """validate_oracle_feedback semantic leakage detection."""

    def test_clean_feedback_no_leaks(self, project_dir: Path):
        audit_path = project_dir / ".dark-factory" / "audit" / "test.log"
        feedback = (
            "NO-SHIP\n\n## Feedback\n"
            "- FR-1: multiply function not yet implemented\n"
            "- EC-1: zero handling missing"
        )
        result = validate_oracle_feedback(feedback, audit_path)
        assert result.is_compliant

    def test_scenario_filename_leakage(self, project_dir: Path):
        audit_path = project_dir / ".dark-factory" / "audit" / "test.log"
        feedback = "The test 01-contract_test.test.py is failing on line 42."
        result = validate_oracle_feedback(feedback, audit_path)
        assert not result.is_compliant

    def test_assertion_leakage(self, project_dir: Path):
        audit_path = project_dir / ".dark-factory" / "audit" / "test.log"
        feedback = "The expect(result).toBe(42) assertion is failing."
        result = validate_oracle_feedback(feedback, audit_path)
        assert not result.is_compliant

    def test_test_dsl_leakage(self, project_dir: Path):
        audit_path = project_dir / ".dark-factory" / "audit" / "test.log"
        feedback = "In describe('Calculator') the test is broken."
        result = validate_oracle_feedback(feedback, audit_path)
        assert not result.is_compliant
