"""Integration tests for scenario runner — real pytest subprocess execution.

Mock boundary: None. Tests run real pytest subprocess against the
python-sample fixture project copied to tmp_path.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from darkfactory.config.loader import load_config
from darkfactory.scenarios.runner import run_scenarios


_FIXTURE_PROJECT = (
    Path(__file__).parent / "fixtures" / "projects" / "python-sample"
)

# Scenario files use the NN-category_test_name.py naming convention
# (e.g., 01-contract_test_multiply.py). Override python_files so pytest
# collects them alongside standard test_*.py files.
_SCENARIO_CMD = (
    'python3 -m pytest {SCENARIOS_DIR} -v'
    ' -o "python_files=test_*.py *_test_*.py"'
)


def _copy_fixture(tmp_path: Path, *, scenario_cmd: str = _SCENARIO_CMD) -> Path:
    """Copy the python-sample fixture to tmp_path and return project dir."""
    project = tmp_path / "project"
    shutil.copytree(_FIXTURE_PROJECT, project)
    # Write a config.toml for the scenario runner
    config_dir = project / ".dark-factory"
    config_dir.mkdir(exist_ok=True)
    # Use TOML single quotes to avoid escaping issues with inner double quotes
    (config_dir / "config.toml").write_text(
        "[core]\nlanguage = \"python\"\nsource_dirs = [\"src\"]\n"
        "test_dir = \"tests\"\n"
        f"scenario_test_cmd = '{scenario_cmd}'\n"
        "\n[agents]\nmax_turns = 5\ntimeout = 60\n"
        "\n[gates]\ntimeout = 30\n"
    )
    return project


def _config(project_dir: Path):
    return load_config(project_dir / ".dark-factory" / "config.toml")


@pytest.mark.integration
class TestRunScenariosRealPytest:
    """Run scenario tests with real pytest subprocess."""

    def test_run_scenarios_real_pytest(self, tmp_path: Path):
        """All scenarios pass — exit_code=0, stdout shows pass count."""
        project = _copy_fixture(tmp_path)
        config = _config(project)

        result = run_scenarios("add-multiply", config, project, timeout=30)

        assert result.exit_code == 0
        assert "passed" in result.stdout

    def test_run_scenarios_with_failures(self, tmp_path: Path):
        """Deliberately failing scenario — exit_code=1, stdout shows failure."""
        project = _copy_fixture(tmp_path)
        config = _config(project)

        # Add a failing test
        fail_dir = project / ".dark-factory" / "scenarios" / "add-multiply"
        (fail_dir / "05-regression_test_broken.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "sys.path.insert(0, str(Path(__file__).parents[3] / 'src'))\n"
            "from calculator import multiply\n\n"
            "def test_deliberately_wrong():\n"
            "    assert multiply(2, 3) == 999\n"
        )

        result = run_scenarios("add-multiply", config, project, timeout=30)

        assert result.exit_code == 1
        assert "failed" in result.stdout.lower()

    def test_missing_scenarios_dir(self, tmp_path: Path):
        """No scenarios dir — exit_code=2."""
        project = _copy_fixture(tmp_path)
        config = _config(project)

        result = run_scenarios("nonexistent-spec", config, project)

        assert result.exit_code == 2
        assert "no scenarios directory" in result.stderr


@pytest.mark.integration
class TestToolchainDetection:
    """Toolchain auto-detection for test command."""

    def test_toolchain_detection_python(self, tmp_path: Path):
        """Python project with pyproject.toml — auto-detect selects pytest."""
        project = _copy_fixture(tmp_path, scenario_cmd="")
        config = _config(project)
        assert config.scenario_test_cmd == ""

        # Auto-detect runs: python3 -m pytest <dir> -v
        # This only collects test_*.py files (default python_files).
        # Scenario files use NN-category_test_name.py convention,
        # so auto-detect finds 0 tests → exit_code=5 (no tests collected).
        # This validates that auto-detect kicks in and runs pytest.
        result = run_scenarios("add-multiply", config, project, timeout=30)

        # pytest ran (exit 5 = no tests collected, which proves pytest executed)
        assert result.exit_code == 5
        assert "no tests ran" in result.stdout


@pytest.mark.integration
class TestSatisfactionScoring:
    """Satisfaction scoring with scenario-like inputs."""

    def test_weighted_satisfaction_from_scenario_results(self):
        """compute_satisfaction with per-file pass/fail dict returns weighted score."""
        from darkfactory.models.config import PipelineConfig
        from darkfactory.scenarios.satisfaction import compute_satisfaction

        # Simulate scenario results: 3 files matching NN- naming convention
        results = {
            "01-contract_test_multiply.py": (3, 3),
            "02-happy_path_test_floats.py": (2, 2),
            "03-edge_cases_test_zero.py": (1, 3),
        }
        config = PipelineConfig()

        score = compute_satisfaction(results, config)

        assert score.total_score > 0
        assert score.total_passed == 6
        assert score.total_scenarios == 8
        assert len(score.categories) >= 1
        # Contract and happy_path should have 100% scores
        cat_by_name = {c.name: c for c in score.categories}
        if "contract" in cat_by_name:
            assert cat_by_name["contract"].score == 100
        if "happy_path" in cat_by_name:
            assert cat_by_name["happy_path"].score == 100
