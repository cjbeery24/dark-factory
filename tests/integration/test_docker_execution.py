"""Integration tests for Docker container execution and barrier enforcement.

Mock boundary: None — these are real Docker tests.
Requires: Docker daemon running, df-test-alpine image built.

Build image before running:
    docker build -t df-test-alpine -f tests/integration/Dockerfile.test .
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from darkfactory.docker.barriers import configure_barriers
from darkfactory.docker.containers import cleanup_containers, execute_container
from darkfactory.docker.args import build_agent_args, build_base_args
from darkfactory.models.agent import AgentRole
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import RunResult

_IMAGE = "df-test-alpine"


def _docker_available() -> bool:
    try:
        r = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _image_exists(image: str) -> bool:
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True, timeout=5,
        )
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


_skip_no_docker = pytest.mark.skipif(
    not _docker_available(), reason="Docker daemon not available"
)
_skip_no_image = pytest.mark.skipif(
    not _image_exists(_IMAGE),
    reason=f"Docker image {_IMAGE} not built",
)

pytestmark = [pytest.mark.integration, pytest.mark.docker, _skip_no_docker, _skip_no_image]


# --- Suite 7: Container Lifecycle ---

class TestContainerExecution:
    """Real Docker container lifecycle tests."""

    def test_container_runs_returns_output(self):
        """Minimal args — validates execute_container basic path."""
        result = execute_container([], _IMAGE, ["-c", "echo hello"])
        assert result.exit_code == 0
        assert result.stdout.strip() == "hello"

    def test_container_nonzero_exit(self):
        result = execute_container([], _IMAGE, ["-c", "exit 42"])
        assert result.exit_code == 42

    def test_container_timeout_kills(self):
        result = execute_container([], _IMAGE, ["-c", "sleep 999"], timeout=2)
        assert result.timed_out

    def test_container_cleanup_on_success(self):
        """--rm flag ensures container is removed after success."""
        run_id = f"test-cleanup-{uuid.uuid4().hex[:8]}"
        result = execute_container(
            ["--label", f"df-run-id={run_id}"],
            _IMAGE, ["-c", "echo done"],
        )
        assert result.exit_code == 0

        ps = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=df-run-id={run_id}"],
            capture_output=True, text=True, timeout=5,
        )
        assert ps.stdout.strip() == ""

    def test_container_cleanup_on_failure(self):
        """--rm flag ensures container is removed after failure."""
        run_id = f"test-cleanup-{uuid.uuid4().hex[:8]}"
        result = execute_container(
            ["--label", f"df-run-id={run_id}"],
            _IMAGE, ["-c", "exit 1"],
        )
        assert result.exit_code == 1

        ps = subprocess.run(
            ["docker", "ps", "-aq", "--filter", f"label=df-run-id={run_id}"],
            capture_output=True, text=True, timeout=5,
        )
        assert ps.stdout.strip() == ""

    def test_container_resource_limits_via_build_args(self):
        """Verify build_base_args resource limits reach the container.

        Uses execute_container with args from build_base_args to test the
        full code path. Container does not use --rm so we can inspect,
        but we override --rm by adding --name (execute_container adds --rm
        by default, we remove it and manage lifecycle manually).
        """
        run_id = f"test-res-{uuid.uuid4().hex[:8]}"
        name = f"df-res-test-{run_id}"
        config = PipelineConfig(agent_memory="128m", agent_cpus="0.5")
        base_args = build_base_args(config, run_id)
        # Add --name for inspection; run manually without --rm
        full_cmd = ["docker", "run", "--name", name, *base_args, _IMAGE, "-c", "echo ok"]
        proc = subprocess.run(full_cmd, capture_output=True, text=True, timeout=10)
        assert proc.returncode == 0

        try:
            inspect = subprocess.run(
                ["docker", "inspect", "--format",
                 "{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}}", name],
                capture_output=True, text=True, timeout=5,
            )
            parts = inspect.stdout.strip().split()
            memory = int(parts[0])
            nano_cpus = int(parts[1])
            assert memory == 128 * 1024 * 1024  # 128MB in bytes
            assert nano_cpus == 500_000_000  # 0.5 CPU in nanocpus
        finally:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True, timeout=5,
            )

    def test_container_label_filtering(self):
        """cleanup_containers finds and removes containers by label."""
        run_id = f"test-label-{uuid.uuid4().hex[:8]}"
        name = f"df-label-test-{run_id}"
        subprocess.run(
            [
                "docker", "run", "-d", "--name", name,
                "--label", f"df-run-id={run_id}",
                _IMAGE, "-c", "sleep 300",
            ],
            capture_output=True, text=True, timeout=10,
        )

        try:
            ps = subprocess.run(
                ["docker", "ps", "-q", "--filter", f"label=df-run-id={run_id}"],
                capture_output=True, text=True, timeout=5,
            )
            assert ps.stdout.strip() != ""

            cleanup_containers(run_id)

            ps2 = subprocess.run(
                ["docker", "ps", "-aq", "--filter", f"label=df-run-id={run_id}"],
                capture_output=True, text=True, timeout=5,
            )
            assert ps2.stdout.strip() == ""
        finally:
            subprocess.run(
                ["docker", "rm", "-f", name],
                capture_output=True, timeout=5,
            )


# --- Suite 6: Docker Barrier Enforcement ---

class TestDockerBarriers:
    """Real Docker barrier enforcement tests.

    These tests build agent args with real barrier configs and run
    containers that verify the filesystem isolation.
    """

    @pytest.fixture
    def barrier_project(self, tmp_path: Path) -> Path:
        """Create a project with barrier-sensitive directories."""
        project = tmp_path / "project"
        project.mkdir()

        # Source code
        src = project / "src"
        src.mkdir()
        (src / "app.py").write_text("print('hello')\n")

        # Dark Factory dirs with content
        df = project / ".dark-factory"
        for d in ("scenarios", "reports", "audit", "state"):
            (df / d).mkdir(parents=True)
        scen_dir = df / "scenarios" / "test-spec"
        scen_dir.mkdir(parents=True)
        (scen_dir / "test_one.py").write_text("assert True\n")
        (df / "reports" / "gate-report.md").write_text("# Report\n")

        # Spec (should always be visible)
        (df / "specs").mkdir(parents=True)
        (df / "specs" / "test-spec.md").write_text("# Test Spec\n")

        # Git dir
        (project / ".git").mkdir()
        (project / ".git" / "HEAD").write_text("ref: refs/heads/main\n")

        return project

    def _run_barrier_cmd(
        self, role: AgentRole, project: Path, cmd: str,
    ) -> RunResult:
        """Run a command in a container with barrier args for the given role."""
        config = PipelineConfig(
            source_dirs=["src"],
            agent_memory="128m",
            agent_cpus="0.5",
        )
        barriers = configure_barriers(config.source_dirs)
        run_id = f"test-barrier-{uuid.uuid4().hex[:8]}"
        docker_args = build_agent_args(
            role, config, barriers, run_id, str(project),
        )
        return execute_container(docker_args, _IMAGE, ["-c", cmd], timeout=10)

    def test_implementer_cannot_read_scenarios(self, barrier_project: Path):
        result = self._run_barrier_cmd(
            AgentRole.IMPLEMENTER, barrier_project,
            "ls /project/.dark-factory/scenarios/",
        )
        # Container ran successfully — directory exists but is empty (tmpfs mask)
        assert result.exit_code == 0
        assert "test_one.py" not in result.stdout

    def test_implementer_cannot_read_reports(self, barrier_project: Path):
        result = self._run_barrier_cmd(
            AgentRole.IMPLEMENTER, barrier_project,
            "ls /project/.dark-factory/reports/",
        )
        assert result.exit_code == 0
        assert "gate-report.md" not in result.stdout

    def test_scenario_architect_cannot_read_source(self, barrier_project: Path):
        result = self._run_barrier_cmd(
            AgentRole.SCENARIO_ARCHITECT, barrier_project,
            "ls /project/src/",
        )
        assert result.exit_code == 0
        assert "app.py" not in result.stdout

    def test_implementer_git_masked(self, barrier_project: Path):
        """.git is masked for implementer — prevents git log/show leaking history."""
        result = self._run_barrier_cmd(
            AgentRole.IMPLEMENTER, barrier_project,
            "ls /project/.git/",
        )
        assert result.exit_code == 0
        assert "HEAD" not in result.stdout

    def test_reviewer_read_only_mount(self, barrier_project: Path):
        result = self._run_barrier_cmd(
            AgentRole.REVIEWER, barrier_project,
            "touch /project/test-rw-check 2>&1; echo EXIT=$?",
        )
        # touch fails on read-only mount
        assert "EXIT=1" in result.stdout or "Read-only" in result.stdout

    def test_implementer_can_write_source(self, barrier_project: Path):
        result = self._run_barrier_cmd(
            AgentRole.IMPLEMENTER, barrier_project,
            "echo 'new code' > /project/src/new.py && cat /project/src/new.py",
        )
        assert result.exit_code == 0
        assert "new code" in result.stdout
