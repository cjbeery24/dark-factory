"""Integration tests for Docker argument construction.

No Docker daemon required — tests call arg-builder functions directly
and assert on the produced argument lists.
"""

from __future__ import annotations

import pytest

from darkfactory.docker.args import build_agent_args, build_base_args, build_claude_args
from darkfactory.docker.barriers import configure_barriers
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig


def _extract_flag_values(args: list[str], flag: str) -> list[str]:
    """Extract all values following a flag in an arg list.

    For flags like --tmpfs, --label, -v where the value is the next element.
    """
    return [args[i + 1] for i, a in enumerate(args) if a == flag and i + 1 < len(args)]


@pytest.fixture
def config() -> PipelineConfig:
    return PipelineConfig(
        source_dirs=["src"],
        agent_memory="4g",
        agent_cpus="4",
        agent_max_turns=200,
    )


@pytest.fixture
def barriers():
    return configure_barriers(["src"])


@pytest.mark.integration
class TestBaseArgs:
    """build_base_args produces correct security and resource flags."""

    def test_security_flags(self, config: PipelineConfig):
        args = build_base_args(config, "run123")
        assert "--cap-drop=ALL" in args
        assert "--security-opt=no-new-privileges" in args
        assert "--pids-limit=256" in args
        assert "--user=1000:1000" in args

    def test_resource_limits(self, config: PipelineConfig):
        args = build_base_args(config, "run123")
        assert "--memory=4g" in args
        assert "--cpus=4" in args

    def test_tmpfs_mounts(self, config: PipelineConfig):
        args = build_base_args(config, "run123")
        tmpfs_targets = _extract_flag_values(args, "--tmpfs")
        assert any("/tmp:" in t for t in tmpfs_targets)
        assert any("/home/agent:" in t for t in tmpfs_targets)

    def test_io_limits_absent_by_default(self, config: PipelineConfig):
        args = build_base_args(config, "run123")
        assert not any("device-read-bps" in a for a in args)
        assert not any("device-write-bps" in a for a in args)

    def test_io_limits_present_when_configured(self):
        config = PipelineConfig(
            agent_io_device="/dev/sda",
            agent_io_read_bps="50mb",
            agent_io_write_bps="30mb",
        )
        args = build_base_args(config, "run123")
        assert "--device-read-bps=/dev/sda:50mb" in args
        assert "--device-write-bps=/dev/sda:30mb" in args

    def test_io_read_only(self):
        config = PipelineConfig(
            agent_io_device="/dev/sda",
            agent_io_read_bps="50mb",
        )
        args = build_base_args(config, "run123")
        assert "--device-read-bps=/dev/sda:50mb" in args
        assert not any("device-write-bps" in a for a in args)

    def test_io_write_only(self):
        config = PipelineConfig(
            agent_io_device="/dev/sda",
            agent_io_write_bps="30mb",
        )
        args = build_base_args(config, "run123")
        assert not any("device-read-bps" in a for a in args)
        assert "--device-write-bps=/dev/sda:30mb" in args


@pytest.mark.integration
class TestAgentArgs:
    """build_agent_args produces correct mount, mask, network, and label flags."""

    def test_implementer_rw_mount(self, config, barriers):
        args = build_agent_args(
            AgentRole.IMPLEMENTER, config, barriers, "run123", "/project"
        )
        volumes = _extract_flag_values(args, "-v")
        project_mounts = [v for v in volumes if "/project:" in v]
        assert len(project_mounts) == 1
        assert project_mounts[0].endswith(":rw")

    def test_reviewer_ro_mount(self, config, barriers):
        args = build_agent_args(
            AgentRole.REVIEWER, config, barriers, "run123", "/project"
        )
        volumes = _extract_flag_values(args, "-v")
        project_mounts = [v for v in volumes if "/project:" in v]
        assert len(project_mounts) == 1
        assert project_mounts[0].endswith(":ro")

    def test_all_non_implementer_roles_get_ro(self, config, barriers):
        ro_roles = [r for r in AgentRole if r != AgentRole.IMPLEMENTER]
        for role in ro_roles:
            args = build_agent_args(role, config, barriers, "run123", "/project")
            volumes = _extract_flag_values(args, "-v")
            project_mounts = [v for v in volumes if "/project:" in v]
            assert project_mounts[0].endswith(":ro"), f"{role.value} should get :ro"

    def test_tmpfs_overlays_for_implementer(self, config, barriers):
        args = build_agent_args(
            AgentRole.IMPLEMENTER, config, barriers, "run123", "/project"
        )
        tmpfs_targets = _extract_flag_values(args, "--tmpfs")
        masked_paths = [t for t in tmpfs_targets if "/project/" in t]
        # Implementer masks: scenarios, reports, audit, state, .git
        assert any("scenarios" in t for t in masked_paths)
        assert any("reports" in t for t in masked_paths)
        assert any(".git" in t for t in masked_paths)

    def test_tmpfs_overlays_for_scenario_architect(self, config, barriers):
        args = build_agent_args(
            AgentRole.SCENARIO_ARCHITECT, config, barriers, "run123", "/project"
        )
        tmpfs_targets = _extract_flag_values(args, "--tmpfs")
        masked_paths = [t for t in tmpfs_targets if "/project/" in t]
        assert any("src" in t for t in masked_paths)
        assert any(".git" in t for t in masked_paths)

    def test_network_assignment(self, config, barriers):
        args = build_agent_args(
            AgentRole.IMPLEMENTER, config, barriers, "run123", "/project",
            network="df-test-net",
        )
        networks = _extract_flag_values(args, "--network")
        assert networks == ["df-test-net"]

    def test_no_network_when_empty(self, config, barriers):
        args = build_agent_args(
            AgentRole.IMPLEMENTER, config, barriers, "run123", "/project"
        )
        assert "--network" not in args

    def test_labels_include_run_metadata(self, config, barriers):
        args = build_agent_args(
            AgentRole.IMPLEMENTER, config, barriers, "run-abc", "/project"
        )
        labels = _extract_flag_values(args, "--label")
        assert "df-run-id=run-abc" in labels
        assert "df-role=implementer" in labels

    def test_oracle_no_project_masks(self, config, barriers):
        args = build_agent_args(
            AgentRole.ORACLE, config, barriers, "run123", "/project"
        )
        tmpfs_targets = _extract_flag_values(args, "--tmpfs")
        project_masks = [t for t in tmpfs_targets if "/project/" in t]
        assert project_masks == []


@pytest.mark.integration
class TestClaudeArgs:
    """build_claude_args produces correct Claude CLI flags."""

    def test_base_flags(self, config, barriers):
        args = build_claude_args(AgentRole.IMPLEMENTER, config, barriers)
        assert "claude" in args
        assert "-p" in args
        assert "--dangerously-skip-permissions" in args
        assert "--output-format=json" in args
        assert f"--max-turns={config.agent_max_turns}" in args

    def test_disallowed_tools_for_implementer(self, config, barriers):
        args = build_claude_args(AgentRole.IMPLEMENTER, config, barriers)
        disallowed = _extract_flag_values(args, "--disallowedTools")
        assert "WebSearch" in disallowed
        assert "WebFetch" in disallowed

    def test_disallowed_tools_for_scenario_architect(self, config, barriers):
        args = build_claude_args(AgentRole.SCENARIO_ARCHITECT, config, barriers)
        disallowed = _extract_flag_values(args, "--disallowedTools")
        assert "Read" in disallowed
        assert "Glob" in disallowed
        assert "Grep" in disallowed

    def test_no_disallowed_tools_for_oracle(self, config, barriers):
        args = build_claude_args(AgentRole.ORACLE, config, barriers)
        assert "--disallowedTools" not in args

    def test_barrier_prompt_appended(self, config, barriers):
        args = build_claude_args(AgentRole.IMPLEMENTER, config, barriers)
        prompts = _extract_flag_values(args, "--append-system-prompt")
        assert len(prompts) == 1
        assert "INFORMATION BARRIER" in prompts[0]

    def test_no_barrier_prompt_for_oracle(self, config, barriers):
        args = build_claude_args(AgentRole.ORACLE, config, barriers)
        assert "--append-system-prompt" not in args

    def test_model_override(self, barriers):
        config = PipelineConfig(
            model_id="claude-sonnet-4-20250514",
            model_ids={"implementer": "claude-opus-4-20250514"},
        )
        args = build_claude_args(AgentRole.IMPLEMENTER, config, barriers)
        assert "--model=claude-opus-4-20250514" in args

    def test_output_file(self, config, barriers):
        args = build_claude_args(
            AgentRole.IMPLEMENTER, config, barriers, output_file="/tmp/out.json"
        )
        outputs = _extract_flag_values(args, "--output")
        assert outputs == ["/tmp/out.json"]
