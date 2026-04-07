"""Shared fixtures for integration tests."""

from __future__ import annotations

import json
import uuid
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from darkfactory.config.loader import load_config
from darkfactory.models.agent import AgentResult, AgentRole
from darkfactory.models.events import EventType
from darkfactory.models.pipeline import PipelineStatus, SpecRun
from darkfactory.pipeline.quality_gate import GateResult
from darkfactory.utils.subprocess_runner import RunResult

_FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Project directory fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal Dark Factory project with valid structure.

    Includes .dark-factory dirs, a Python source file, pyproject.toml,
    and a valid config.toml with language=python.
    """
    # Dark Factory runtime dirs
    for d in ("specs", "scenarios", "reports", "state", "audit", "plans"):
        (tmp_path / ".dark-factory" / d).mkdir(parents=True)

    # Minimal Python source
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").touch()
    (src / "calculator.py").write_text(
        'def add(a: int, b: int) -> int:\n    return a + b\n\n'
        'def subtract(a: int, b: int) -> int:\n    return a - b\n\n'
        'def multiply(a: int, b: int) -> int:\n    return a * b\n'
    )

    # pyproject.toml
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test-project"\nversion = "0.1.0"\n'
    )

    # config.toml
    (tmp_path / ".dark-factory" / "config.toml").write_text(
        '[core]\nlanguage = "python"\nsource_dirs = ["src"]\n'
        'test_dir = "tests"\ntest_cmd = "python3 -m pytest"\n'
        '\n[agents]\nmax_turns = 5\ntimeout = 60\n'
        '\n[autonomy]\nlevel = 3\nmax_iterations = 3\n'
        '\n[gates]\ntimeout = 30\n'
    )

    return tmp_path


@pytest.fixture
def project_dir_no_config(tmp_path: Path) -> Path:
    """Create project structure without config.toml (for config resolution tests)."""
    for d in ("specs", "scenarios", "reports", "state", "audit", "plans"):
        (tmp_path / ".dark-factory" / d).mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "test-project"\nversion = "0.1.0"\n'
    )
    return tmp_path


# ---------------------------------------------------------------------------
# Config and state fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline_config(project_dir: Path):
    """Return a PipelineConfig loaded from the test project's config.toml."""
    return load_config(project_dir / ".dark-factory" / "config.toml")


# ---------------------------------------------------------------------------
# Spec fixtures — canonical content lives in fixtures/specs/*.md
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_spec(project_dir: Path) -> str:
    """Write a valid spec file that passes completeness and return its name."""
    name = "add-multiply"
    src = _FIXTURES_DIR / "specs" / "complete-spec.md"
    (project_dir / ".dark-factory" / "specs" / f"{name}.md").write_text(src.read_text())
    return name


@pytest.fixture
def failing_spec(project_dir: Path) -> str:
    """Write an incomplete spec file and return its name."""
    name = "incomplete"
    src = _FIXTURES_DIR / "specs" / "incomplete-spec.md"
    (project_dir / ".dark-factory" / "specs" / f"{name}.md").write_text(src.read_text())
    return name


# ---------------------------------------------------------------------------
# Events fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def events_file(project_dir: Path) -> Path:
    """Return path to events JSONL file, create parent dirs."""
    return project_dir / ".dark-factory" / "events.jsonl"


@pytest.fixture
def populated_events(events_file: Path) -> Path:
    """Return events file pre-populated with a realistic event sequence."""
    run_id = uuid.uuid4().hex[:16]
    spec_name = "add-multiply"
    base = datetime(2026, 3, 11, 12, 0, 0, tzinfo=timezone.utc)

    def _ts(offset_s: int) -> str:
        return (base + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Use the actual JSONL field names from darkfactory.events.emitter:
    # "event" (not "event_type"), "spec" (not "spec_name"), "ts" (ISO 8601), "iter"
    events = [
        {"ts": _ts(0), "event": EventType.SPEC_START.value,
         "spec": spec_name, "run_id": run_id},
        {"ts": _ts(1), "event": EventType.AGENT_START.value,
         "spec": spec_name, "run_id": run_id,
         "data": {"role": "spec-validator"}},
        {"ts": _ts(5), "event": EventType.AGENT_COMPLETED.value,
         "spec": spec_name, "run_id": run_id,
         "data": {"role": "spec-validator", "exit_code": 0, "turns": 3}},
        {"ts": _ts(6), "event": EventType.AGENT_START.value,
         "spec": spec_name, "run_id": run_id,
         "data": {"role": "scenario-architect"}},
        {"ts": _ts(15), "event": EventType.AGENT_COMPLETED.value,
         "spec": spec_name, "run_id": run_id,
         "data": {"role": "scenario-architect", "exit_code": 0, "turns": 5}},
        {"ts": _ts(16), "event": EventType.AGENT_START.value,
         "spec": spec_name, "run_id": run_id, "iter": 1,
         "data": {"role": "implementer"}},
        {"ts": _ts(30), "event": EventType.AGENT_COMPLETED.value,
         "spec": spec_name, "run_id": run_id, "iter": 1,
         "data": {"role": "implementer", "exit_code": 0, "turns": 8}},
        {"ts": _ts(35), "event": EventType.GATE_COMPLETED.value,
         "spec": spec_name, "run_id": run_id, "iter": 1,
         "data": {"passed": 10, "failed": 0, "total": 10, "pass_rate": 100}},
        {"ts": _ts(36), "event": EventType.SATISFACTION_SCORED.value,
         "spec": spec_name, "run_id": run_id, "iter": 1,
         "data": {"score": 95, "threshold": 80}},
        {"ts": _ts(40), "event": EventType.SPEC_SHIPPED.value,
         "spec": spec_name, "run_id": run_id,
         "data": {"iterations": 1, "total_turns": 16}},
    ]

    with events_file.open("w") as f:
        for event in events:
            f.write(json.dumps(event) + "\n")

    return events_file


# ---------------------------------------------------------------------------
# Factory helpers — build dataclass instances with sensible defaults
# ---------------------------------------------------------------------------

_ROLE_OUTPUTS: dict[AgentRole, str] = {
    AgentRole.SPEC_VALIDATOR: "VALIDATED — all sections present, score 100/100",
    AgentRole.SCENARIO_ARCHITECT: "Generated 10 scenarios across 4 categories",
    AgentRole.IMPLEMENTER: "Implemented FR-1, FR-2, FR-3. Files: src/calculator.py",
    AgentRole.REVIEWER: "APPROVE — implementation meets spec requirements",
    AgentRole.ORACLE: "SHIP\nAll functional requirements satisfied.",
    AgentRole.ESCALATION: "Escalation analysis: 3 iterations exhausted",
    AgentRole.EVALUATOR: "PASS — all health checks pass",
}


def agent_result_factory(
    role: AgentRole = AgentRole.IMPLEMENTER,
    exit_code: int = 0,
    **overrides: Any,
) -> AgentResult:
    """Build an AgentResult with sensible defaults for the given role."""
    defaults: dict[str, Any] = {
        "role": role,
        "exit_code": exit_code,
        "turns_used": 5,
        "output": _ROLE_OUTPUTS.get(role, ""),
        "wall_seconds": 10.0,
    }
    defaults.update(overrides)
    return AgentResult(**defaults)


def spec_run_factory(
    spec_name: str = "add-multiply",
    **overrides: Any,
) -> SpecRun:
    """Build a SpecRun with sensible defaults."""
    defaults: dict[str, Any] = {
        "spec_name": spec_name,
        "run_id": uuid.uuid4().hex[:16],
        "status": PipelineStatus.RUNNING,
    }
    defaults.update(overrides)
    return SpecRun(**defaults)


def gate_result_factory(
    exit_code: int = 0,
    passed: int = 10,
    failed: int = 0,
    **overrides: Any,
) -> GateResult:
    """Build a GateResult with sensible defaults."""
    total = passed + failed
    defaults: dict[str, Any] = {
        "exit_code": exit_code,
        "output": "",
        "scenario_passed": passed,
        "scenario_failed": failed,
        "scenario_total": total,
        "pass_rate": (passed * 100 // total) if total else 0,
        "satisfaction_score": 90 if exit_code == 0 else 0,
    }
    defaults.update(overrides)
    return GateResult(**defaults)


# ---------------------------------------------------------------------------
# Shared helpers — used across multiple integration test files
# ---------------------------------------------------------------------------

def plan_config(project_dir: Path):
    """Load config with plan-test-friendly defaults."""
    config = load_config(project_dir / ".dark-factory" / "config.toml")
    config.max_concurrent_specs = 1
    config.plan_token_budget = 10000
    config.plan_max_wall_time_s = 0
    config.worktree_cleanup_on_ship = False
    config.conflict_strategy = "warn"
    return config


def read_events(events_file: Path) -> list[dict]:
    """Read all events from a JSONL file."""
    if not events_file.exists():
        return []
    result = []
    with events_file.open() as f:
        for line in f:
            s = line.strip()
            if s:
                result.append(json.loads(s))
    return result


# ---------------------------------------------------------------------------
# Sequencing mock — pops results one at a time, records calls
# ---------------------------------------------------------------------------

class _SequenceMock:
    """A mock that returns pre-configured results in order.

    Raises IndexError if called more times than results were provided.
    Records each call's (args, kwargs) in self.calls.
    """

    def __init__(self, results: list[Any]) -> None:
        self._results = deque(results)
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append((args, kwargs))
        if not self._results:
            raise IndexError(
                f"SequenceMock exhausted after {len(self.calls) - 1} calls — "
                f"unexpected call #{len(self.calls)}"
            )
        return self._results.popleft()

    @property
    def call_count(self) -> int:
        return len(self.calls)

    @property
    def remaining(self) -> int:
        return len(self._results)


# ---------------------------------------------------------------------------
# Mock fixtures — patch at the right import path, return SequenceMock
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_invoke_agent():
    """Patch invoke_agent with a SequenceMock.

    Usage:
        def test_something(mock_invoke_agent):
            seq = mock_invoke_agent([
                agent_result_factory(role=AgentRole.SPEC_VALIDATOR),
                agent_result_factory(role=AgentRole.SCENARIO_ARCHITECT),
                agent_result_factory(role=AgentRole.IMPLEMENTER),
            ])
            # ... run pipeline ...
            assert len(seq.calls) == 3
            assert seq.calls[0][0][0] == AgentRole.SPEC_VALIDATOR  # 1st positional arg
    """
    def _setup(results: list[AgentResult]) -> _SequenceMock:
        seq = _SequenceMock(results)
        patcher = patch("darkfactory.pipeline.agents.invoke_agent", side_effect=seq)
        mock = patcher.start()
        # Attach seq so callers can inspect calls and remaining count.
        mock._seq = seq
        _setup._patcher = patcher
        return seq

    yield _setup

    if hasattr(_setup, "_patcher"):
        _setup._patcher.stop()


@pytest.fixture
def mock_execute_container():
    """Patch execute_container with a SequenceMock.

    Usage:
        def test_gate(mock_execute_container):
            seq = mock_execute_container([
                RunResult(exit_code=0, stdout="all passed"),
            ])
    """
    def _setup(results: list[RunResult]) -> _SequenceMock:
        seq = _SequenceMock(results)
        patcher = patch(
            "darkfactory.docker.containers.execute_container",
            side_effect=seq,
        )
        patcher.start()
        _setup._patcher = patcher
        return seq

    yield _setup

    if hasattr(_setup, "_patcher"):
        _setup._patcher.stop()


@pytest.fixture
def mock_run_pipeline():
    """Patch run_pipeline with a SequenceMock.

    Usage:
        def test_plan(mock_run_pipeline):
            seq = mock_run_pipeline([0, 0, 1])  # 2 shipped, 1 failed
    """
    def _setup(exit_codes: list[int]) -> _SequenceMock:
        seq = _SequenceMock(exit_codes)
        patcher = patch(
            "darkfactory.pipeline.orchestrator.run_pipeline",
            side_effect=seq,
        )
        patcher.start()
        _setup._patcher = patcher
        return seq

    yield _setup

    if hasattr(_setup, "_patcher"):
        _setup._patcher.stop()


# ---------------------------------------------------------------------------
# Registry / plan / regression fixtures (Phase 0 — integration test expansion)
# ---------------------------------------------------------------------------

@pytest.fixture
def registry_fixture(project_dir: Path) -> dict[str, list[str]]:
    """Pre-populate shipped-scenarios registry with 3 specs.

    Each spec has scenario files and .registry-meta containing target_files.
    Returns dict mapping spec_name -> target_files list.
    """
    specs = {
        "add-auth": ["src/auth.py", "src/middleware.py"],
        "add-api": ["src/api.py", "src/routes.py"],
        "add-ui": ["src/ui.py"],
    }
    registry = project_dir / ".dark-factory" / "shipped-scenarios"
    registry.mkdir(parents=True, exist_ok=True)

    for spec_name, target_files in specs.items():
        spec_dir = registry / spec_name
        spec_dir.mkdir()
        # Write scenario files.
        for i in range(3):
            (spec_dir / f"0{i+1}-scenario.test.ts").write_text(
                f"// scenario {i+1} for {spec_name}\n"
            )
        # Write registry meta.
        (spec_dir / ".registry-meta").write_text(
            f"spec={spec_name}\n"
            f"registered=2026-03-11T12:00:00Z\n"
            f"scenario_count=3\n"
            f"target_files={', '.join(target_files)}\n"
        )
        # Also write a spec file so coverage.affected_specs can find target_files.
        (project_dir / ".dark-factory" / "specs" / f"{spec_name}.md").write_text(
            f"# {spec_name}\n\n"
            "<!-- dark-factory-metadata\n"
            "feature-id: F-1\n"
            "spec_type: feature\n"
            "-->\n\n"
            f"target-files: {', '.join(target_files)}\n"
        )
    return specs


@pytest.fixture
def multi_spec_plan_fixture(project_dir: Path) -> tuple[str, list[str]]:
    """Write a plan with 3 specs in a dependency chain and return (plan_name, spec_names).

    S1 is independent, S2 depends on S1, S3 depends on S2.
    Uses the markdown table format that parse_plan() expects, with
    depends-on metadata in individual spec files.
    """
    plan_name = "test-plan"
    spec_names = ["chain-s1", "chain-s2", "chain-s3"]

    plan_table = (
        "| Spec | Dependencies | Priority |\n"
        "| --- | --- | --- |\n"
        "| chain-s1 | — | 1 |\n"
        "| chain-s2 | chain-s1 | 2 |\n"
        "| chain-s3 | chain-s2 | 3 |\n"
    )
    plans_dir = project_dir / ".dark-factory" / "plans"
    plans_dir.mkdir(parents=True, exist_ok=True)
    (plans_dir / f"{plan_name}.md").write_text(f"# {plan_name}\n\n{plan_table}\n")

    # Write matching spec files with depends-on metadata.
    specs_dir = project_dir / ".dark-factory" / "specs"
    deps_map = {"chain-s1": [], "chain-s2": ["chain-s1"], "chain-s3": ["chain-s2"]}
    for name in spec_names:
        deps = deps_map[name]
        deps_line = f"depends-on: [{', '.join(deps)}]\n" if deps else ""
        (specs_dir / f"{name}.md").write_text(
            f"# {name}\n\n"
            "<!-- dark-factory-metadata\n"
            "feature-id: F-1\n"
            "spec_type: feature\n"
            "-->\n\n"
            f"{deps_line}"
            f"## Functional Requirements\n\n- FR-1: placeholder\n"
        )

    return (plan_name, spec_names)


@pytest.fixture
def mock_run_scenarios():
    """Patch run_scenarios with a SequenceMock.

    Usage:
        def test_regression(mock_run_scenarios):
            from darkfactory.utils.subprocess_runner import RunResult
            seq = mock_run_scenarios([
                RunResult(exit_code=0, stdout="all passed"),
                RunResult(exit_code=1, stdout="failures"),
            ])
    """
    def _setup(results: list[Any]) -> _SequenceMock:
        seq = _SequenceMock(results)
        patcher = patch(
            "darkfactory.scenarios.runner.run_scenarios",
            side_effect=seq,
        )
        patcher.start()
        _setup._patcher = patcher
        return seq

    yield _setup

    if hasattr(_setup, "_patcher"):
        _setup._patcher.stop()


@pytest.fixture
def mock_run_command():
    """Patch deployment run_command with a SequenceMock.

    Usage:
        def test_deploy(mock_run_command):
            from subprocess import CompletedProcess
            seq = mock_run_command([
                CompletedProcess(args=[], returncode=0),
            ])
    """
    def _setup(results: list[Any]) -> _SequenceMock:
        seq = _SequenceMock(results)
        patcher = patch(
            "darkfactory.deployment.command.run_command",
            side_effect=seq,
        )
        patcher.start()
        _setup._patcher = patcher
        return seq

    yield _setup

    if hasattr(_setup, "_patcher"):
        _setup._patcher.stop()


# ---------------------------------------------------------------------------
# Pipeline environment fixture — shared by test_pipeline_flow and others
# ---------------------------------------------------------------------------

@pytest.fixture
def pipeline_env(project_dir, sample_spec):
    """Set up infrastructure mocks for pipeline tests.

    Patches Docker pre-flight, networks, proxies, git ops, and
    CleanupManager so tests run without Docker or git.

    Yields a dict with project_dir, spec_name, events_file, state_file,
    and setup_agents() to install a shared invoke_agent mock.
    """
    events_file = project_dir / ".dark-factory" / "events.jsonl"

    # Build a mock PreCheckResult that passes.
    mock_precheck_result = MagicMock()
    mock_precheck_result.passed = True
    mock_precheck_result.ambiguities = []

    patches = [
        patch("darkfactory.pipeline.orchestrator.require_docker"),
        patch("darkfactory.pipeline.orchestrator.setup_networks"),
        patch("darkfactory.pipeline.orchestrator.setup_proxies"),
        patch("darkfactory.pipeline.orchestrator.git_ops"),
        patch("darkfactory.pipeline.orchestrator.registry_register"),
        patch("darkfactory.pipeline.orchestrator.run_regression", return_value=(0, 0, 0, [])),
        patch("darkfactory.pipeline.orchestrator.record_start"),
        patch("darkfactory.pipeline.orchestrator.record_end"),
        patch("darkfactory.pipeline.cleanup.CleanupManager.register"),
        patch("darkfactory.pipeline.cleanup.CleanupManager.cleanup"),
        patch(
            "darkfactory.pipeline.orchestrator.validate_spec_result",
            return_value=(True, ""),
        ),
        patch(
            "darkfactory.pipeline.orchestrator.has_scenario_files",
            return_value=True,
        ),
        patch(
            "darkfactory.pipeline.iteration.count_files_changed",
            return_value=1,
        ),
        patch(
            "darkfactory.spec.precheck.run_precheck",
            return_value=mock_precheck_result,
        ),
    ]

    agent_patches: list[Any] = []

    def setup_agents(
        results: list[AgentResult],
        gate_result=None,
        has_scenarios: bool = True,
        validation_result: tuple[bool, str] = (True, ""),
    ) -> _SequenceMock:
        """Install a shared invoke_agent mock for both orchestrator and iteration."""
        seq = _SequenceMock(results)
        p1 = patch("darkfactory.pipeline.orchestrator.invoke_agent", new=seq)
        p2 = patch("darkfactory.pipeline.iteration.invoke_agent", new=seq)
        p3 = patch(
            "darkfactory.pipeline.iteration.run_quality_gate",
            return_value=gate_result or gate_result_factory(),
        )
        p1.start()
        p2.start()
        p3.start()
        agent_patches.extend([p1, p2, p3])

        if not has_scenarios:
            p4 = patch(
                "darkfactory.pipeline.orchestrator.has_scenario_files",
                side_effect=[False, True],  # False → skip early return, True → pass post-generation check
            )
            p4.start()
            agent_patches.append(p4)

        if validation_result != (True, ""):
            p5 = patch(
                "darkfactory.pipeline.orchestrator.validate_spec_result",
                return_value=validation_result,
            )
            p5.start()
            agent_patches.append(p5)

        return seq

    for p in patches:
        p.start()

    yield {
        "project_dir": project_dir,
        "spec_name": sample_spec,
        "events_file": events_file,
        "state_file": project_dir / ".dark-factory" / "state" / f"{sample_spec}.state",
        "setup_agents": setup_agents,
    }

    for p in agent_patches:
        p.stop()
    for p in patches:
        p.stop()
