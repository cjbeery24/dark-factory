"""Integration tests for scenario detection, validation, and satisfaction scoring."""

from __future__ import annotations

from pathlib import Path

import pytest

from darkfactory.models.config import PipelineConfig
from darkfactory.scenarios.detection import has_scenario_files, validate_spec_result
from darkfactory.scenarios.satisfaction import compute_satisfaction
from darkfactory.scenarios.validator import validate_scenarios


@pytest.mark.integration
class TestScenarioDetection:
    """Scenario file detection with real filesystem."""

    def test_detect_test_files(self, tmp_path: Path):
        (tmp_path / "test_auth.py").touch()
        assert has_scenario_files(tmp_path)

    def test_detect_spec_files(self, tmp_path: Path):
        (tmp_path / "auth.spec.ts").touch()
        assert has_scenario_files(tmp_path)

    def test_detect_scenario_files(self, tmp_path: Path):
        (tmp_path / "auth.scenario.yaml").touch()
        assert has_scenario_files(tmp_path)

    def test_no_files_returns_false(self, tmp_path: Path):
        assert not has_scenario_files(tmp_path)

    def test_nonexistent_dir_returns_false(self, tmp_path: Path):
        assert not has_scenario_files(tmp_path / "nonexistent")

    def test_fallback_any_file_present(self, tmp_path: Path):
        # has_scenario_files falls back to any(iterdir()) when no patterns match
        (tmp_path / "scenarios.yaml").touch()
        assert has_scenario_files(tmp_path)


@pytest.mark.integration
class TestValidateSpecResult:
    """Spec validation result parsing."""

    def test_pass_from_status_file(self, tmp_path: Path):
        (tmp_path / "myspec-validation.status").write_text(
            "verdict=PASS\nscore=100\ndivergence=LOW\nblockers=0\n"
        )
        passed, reason = validate_spec_result(tmp_path, "myspec")
        assert passed
        assert reason == "PASS"

    def test_pass_with_imperfect_score(self, tmp_path: Path):
        # Score below 100 is fine as long as there are no blockers.
        (tmp_path / "myspec-validation.status").write_text(
            "verdict=PASS\nscore=85\ndivergence=LOW\nblockers=0\n"
        )
        passed, reason = validate_spec_result(tmp_path, "myspec")
        assert passed

    def test_fail_verdict(self, tmp_path: Path):
        (tmp_path / "myspec-validation.status").write_text(
            "verdict=FAIL\nscore=80\ndivergence=LOW\nblockers=0\n"
        )
        passed, reason = validate_spec_result(tmp_path, "myspec")
        assert not passed
        assert "verdict=FAIL" in reason

    def test_blockers_skipped_when_verdict_pass(self, tmp_path: Path):
        # Pre-existing behavior: verdict=PASS skips blocker check (detection.py:166).
        (tmp_path / "myspec-validation.status").write_text(
            "verdict=PASS\nscore=90\ndivergence=LOW\nblockers=2\n"
        )
        passed, reason = validate_spec_result(tmp_path, "myspec")
        assert passed

    def test_high_divergence_warns_but_passes(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        (tmp_path / "myspec-validation.status").write_text(
            "verdict=PASS\nscore=100\ndivergence=HIGH\nblockers=0\n"
        )
        import logging
        with caplog.at_level(logging.WARNING):
            passed, reason = validate_spec_result(tmp_path, "myspec")
        assert passed
        assert reason == "PASS"
        assert any("divergence" in r.message.lower() for r in caplog.records)

    def test_no_output(self, tmp_path: Path):
        passed, reason = validate_spec_result(tmp_path, "myspec")
        assert not passed
        assert "no validation output" in reason

    def test_invalid_blockers_value(self, tmp_path: Path):
        (tmp_path / "myspec-validation.status").write_text(
            "verdict=PASS\nscore=90\ndivergence=LOW\nblockers=abc\n"
        )
        passed, reason = validate_spec_result(tmp_path, "myspec")
        assert not passed
        assert "invalid blockers" in reason

    def test_missing_blockers_field_defaults_to_zero(self, tmp_path: Path):
        # Old-format status files without blockers field should pass.
        (tmp_path / "myspec-validation.status").write_text(
            "verdict=PASS\nscore=100\ndivergence=LOW\n"
        )
        passed, reason = validate_spec_result(tmp_path, "myspec")
        assert passed

    def test_pass_from_markdown(self, tmp_path: Path):
        (tmp_path / "myspec-validation.md").write_text(
            "# Validation Report\n\nVerdict: PASS\nDivergence: LOW\n\n## Critical Issues (must fix)\n\nNone.\n"
        )
        passed, reason = validate_spec_result(tmp_path, "myspec")
        assert passed

    def test_fail_from_markdown_with_critical_issues(self, tmp_path: Path):
        content = (
            "# Validation Report\n\nVerdict: PASS\nDivergence: LOW\n\n"
            "## Critical Issues (must fix)\n\n"
            "1. Missing error handling for concurrent access\n"
        )
        (tmp_path / "myspec-validation.md").write_text(content)
        passed, reason = validate_spec_result(tmp_path, "myspec")
        assert not passed
        assert "critical" in reason.lower()


@pytest.mark.integration
class TestSatisfactionScoring:
    """Weighted satisfaction scoring with real config."""

    def test_perfect_score(self):
        config = PipelineConfig()
        results = {
            "01-contract_test.py": (5, 5),
            "02-happy_path_test.py": (10, 10),
            "03-edge_cases_test.py": (3, 3),
            "04-error_conditions_test.py": (4, 4),
        }
        score = compute_satisfaction(results, config)
        assert score.total_score == 100
        assert score.total_passed == 22
        assert score.total_failed == 0
        assert score.pass_rate == 100

    def test_partial_score(self):
        config = PipelineConfig()
        results = {
            "01-contract_test.py": (5, 5),
            "02-happy_path_test.py": (5, 10),
            "03-edge_cases_test.py": (0, 3),
        }
        score = compute_satisfaction(results, config)
        # contract: (5*100)//5=100, w=100; happy_path: (5*100)//10=50, w=100; edge: 0, w=80
        # weighted_sum = 100*100 + 100*50 + 80*0 = 15000; weight_sum = 280
        # total_score = 15000 // 280 = 53
        assert score.total_score == 53
        assert score.total_passed == 10
        assert score.total_failed == 8
        # pass_rate = (10*100) // 18 = 55
        assert score.pass_rate == 55

    def test_zero_score(self):
        config = PipelineConfig()
        results = {
            "01-contract_test.py": (0, 5),
            "02-happy_path_test.py": (0, 10),
        }
        score = compute_satisfaction(results, config)
        assert score.total_score == 0
        assert score.pass_rate == 0

    def test_empty_results(self):
        config = PipelineConfig()
        score = compute_satisfaction({}, config)
        assert score.total_score == 0
        assert score.total_scenarios == 0

    def test_category_breakdown(self):
        config = PipelineConfig()
        results = {
            "01-contract_test.py": (3, 3),
            "02-happy_path_test.py": (2, 4),
        }
        score = compute_satisfaction(results, config)
        cats = {c.name: c for c in score.categories if c.total > 0}
        assert "contract" in cats
        assert cats["contract"].score == 100
        assert "happy_path" in cats
        assert cats["happy_path"].score == 50


@pytest.mark.integration
class TestScenarioValidation:
    """Scenario file validation with real project structure."""

    def _make_scenarios(self, project_dir: Path, spec_name: str,
                        files: dict[str, str]) -> None:
        scen_dir = project_dir / ".dark-factory" / "scenarios" / spec_name
        scen_dir.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (scen_dir / name).write_text(content)

    def test_valid_python_scenarios(self, project_dir: Path):
        self._make_scenarios(project_dir, "test-spec", {
            "test_contract.py": "def test_one():\n    assert 1 + 1 == 2\n",
            "test_happy.py": "def test_two():\n    assert 2 + 2 == 4\n",
            "test_edge.py": "def test_three():\n    assert 0 + 0 == 0\n",
        })
        result = validate_scenarios("test-spec", "python", project_dir)
        assert result.passed

    def test_too_few_files(self, project_dir: Path):
        self._make_scenarios(project_dir, "test-spec", {
            "test_one.py": "def test_one():\n    assert True\n",
        })
        result = validate_scenarios("test-spec", "python", project_dir)
        assert not result.passed

    def test_writes_report(self, project_dir: Path):
        self._make_scenarios(project_dir, "test-spec", {
            "test_a.py": "def test_a():\n    assert 1 == 1\n",
            "test_b.py": "def test_b():\n    assert 2 == 2\n",
            "test_c.py": "def test_c():\n    assert 3 == 3\n",
        })
        validate_scenarios("test-spec", "python", project_dir)
        report = project_dir / ".dark-factory" / "reports" / "test-spec-scenario-validation.md"
        assert report.exists()
        assert report.stat().st_size > 0

    def test_trivial_assertions_fail(self, project_dir: Path):
        self._make_scenarios(project_dir, "test-spec", {
            "test_a.py": "def test_a():\n    assert True\n",
            "test_b.py": "def test_b():\n    assert True\n",
            "test_c.py": "def test_c():\n    assert True\n",
        })
        result = validate_scenarios("test-spec", "python", project_dir)
        assert not result.passed
        trivial_check = next(c for c in result.checks if c.name == "trivial")
        assert not trivial_check.passed

    def test_empty_body_fail(self, project_dir: Path):
        self._make_scenarios(project_dir, "test-spec", {
            "test_a.py": "def test_a():\n    assert 1 + 1 == 2\n",
            "test_b.py": "def test_b():\n    assert 2 + 2 == 4\n",
            "test_c.py": "def test_empty(x):\n    pass\n",
        })
        result = validate_scenarios("test-spec", "python", project_dir)
        assert not result.passed
        empty_check = next(c for c in result.checks if c.name == "empty_bodies")
        assert not empty_check.passed
        assert "test_c.py" in empty_check.failing_files

    def test_no_assertions_fail(self, project_dir: Path):
        self._make_scenarios(project_dir, "test-spec", {
            "test_a.py": "def test_a():\n    x = 1 + 1\n",
            "test_b.py": "def test_b():\n    assert 2 == 2\n",
            "test_c.py": "def test_c():\n    assert 3 == 3\n",
        })
        result = validate_scenarios("test-spec", "python", project_dir)
        assert not result.passed
        assertion_check = next(c for c in result.checks if c.name == "assertions")
        assert not assertion_check.passed
        assert "test_a.py" in assertion_check.failing_files
