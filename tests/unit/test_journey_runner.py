"""Unit tests for scripts/lib/journey-runner.py.

Tests cover:
- Parsing *.journey.md files into Scenario dataclasses (no browser)
- Step action line parsing (_parse_step_line)
- Env file output format
- Error handling: empty files, missing headers, missing ## Steps section
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

# ---------------------------------------------------------------------------
# Dynamic import of the runner script (not a package, no __init__.py)
# ---------------------------------------------------------------------------

_RUNNER_PATH = (
    Path(__file__).parent.parent.parent
    / "scripts" / "lib" / "journey-runner.py"
)


def _load_runner() -> types.ModuleType:
    # Patch playwright import so the module loads without Playwright installed
    fake_playwright = types.ModuleType("playwright")
    fake_sync = types.ModuleType("playwright.sync_api")

    class _FakeError(Exception):
        pass

    fake_sync.sync_playwright = None  # type: ignore[attr-defined]
    fake_sync.expect = None           # type: ignore[attr-defined]
    fake_sync.Page = None             # type: ignore[attr-defined]
    fake_sync.Error = _FakeError      # type: ignore[attr-defined]
    fake_playwright.sync_api = fake_sync  # type: ignore[attr-defined]

    sys.modules.setdefault("playwright", fake_playwright)
    sys.modules.setdefault("playwright.sync_api", fake_sync)

    spec = importlib.util.spec_from_file_location("journey_runner", _RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["journey_runner"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_runner = _load_runner()

# Convenience aliases
parse_journey_file = _runner.parse_journey_file
parse_journey_block = _runner.parse_journey_block
_parse_step_line = _runner._parse_step_line
write_env_files = _runner.write_env_files
write_per_test_results = _runner.write_per_test_results
Scenario = _runner.Scenario
StepAction = _runner.StepAction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_journey_file(tmp_path: Path, content: str) -> Path:
    f = tmp_path / "01-login.journey.md"
    f.write_text(content, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Step action line parsing
# ---------------------------------------------------------------------------

class TestParseStepLine(unittest.TestCase):
    def test_navigate(self) -> None:
        step = _parse_step_line("1. navigate: /login")
        assert step is not None
        self.assertEqual(step.action, "navigate")
        self.assertEqual(step.args[0], "/login")

    def test_click_css_selector(self) -> None:
        step = _parse_step_line("2. click: .btn-submit")
        assert step is not None
        self.assertEqual(step.action, "click")
        self.assertEqual(step.args[0], ".btn-submit")

    def test_click_label(self) -> None:
        step = _parse_step_line("3. click: Sign In")
        assert step is not None
        self.assertEqual(step.action, "click")
        self.assertEqual(step.args[0], "Sign In")

    def test_fill(self) -> None:
        step = _parse_step_line('4. fill: "Email" with "user@example.com"')
        assert step is not None
        self.assertEqual(step.action, "fill")
        self.assertEqual(step.args[0], "Email")
        self.assertEqual(step.args[1], "user@example.com")

    def test_assert_visible(self) -> None:
        step = _parse_step_line('5. assert visible: "Welcome"')
        assert step is not None
        self.assertEqual(step.action, "assert_visible")
        self.assertEqual(step.args[0], "Welcome")

    def test_assert_url(self) -> None:
        step = _parse_step_line("6. assert url: /dashboard")
        assert step is not None
        self.assertEqual(step.action, "assert_url")
        self.assertEqual(step.args[0], "/dashboard")

    def test_assert_not_visible(self) -> None:
        step = _parse_step_line('7. assert not visible: "Error"')
        assert step is not None
        self.assertEqual(step.action, "assert_not_visible")
        self.assertEqual(step.args[0], "Error")

    def test_wait(self) -> None:
        step = _parse_step_line("8. wait: 500 ms")
        assert step is not None
        self.assertEqual(step.action, "wait")
        self.assertEqual(step.args[0], "500")

    def test_unrecognised_returns_none(self) -> None:
        result = _parse_step_line("something completely unknown")
        self.assertIsNone(result)

    def test_step_without_number_prefix(self) -> None:
        # Lines without "N. " prefix should still parse
        step = _parse_step_line("navigate: /home")
        assert step is not None
        self.assertEqual(step.action, "navigate")

    def test_case_insensitive_navigate(self) -> None:
        step = _parse_step_line("1. Navigate: /home")
        assert step is not None
        self.assertEqual(step.action, "navigate")


# ---------------------------------------------------------------------------
# Parsing — single scenario block
# ---------------------------------------------------------------------------

class TestParseSingleScenario(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _parse(self, content: str) -> list:
        f = _make_journey_file(self.tmp, content)
        return parse_journey_file(f)

    def test_parses_name_and_requirement(self) -> None:
        content = """\
# Scenario: user can log in
# Requirement: AUTH-1

## Steps
1. navigate: /login
2. fill: "Email" with "user@example.com"
3. fill: "Password" with "secret"
4. click: Sign In
5. assert url: /dashboard
"""
        scenarios = self._parse(content)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].name, "user can log in")
        self.assertEqual(scenarios[0].requirement, "AUTH-1")

    def test_parses_all_steps(self) -> None:
        content = """\
# Scenario: multi step
# Requirement: FR-1

## Steps
1. navigate: /home
2. click: About
3. assert visible: "About Us"
"""
        scenarios = self._parse(content)
        self.assertEqual(len(scenarios[0].steps), 3)

    def test_step_types_parsed(self) -> None:
        content = """\
# Scenario: all types
# Requirement: FR-2

## Steps
1. navigate: /form
2. fill: "Name" with "Alice"
3. click: .submit-btn
4. assert visible: "Success"
5. assert url: /done
6. assert not visible: "Error"
7. wait: 200 ms
"""
        scenarios = self._parse(content)
        steps = scenarios[0].steps
        actions = [s.action for s in steps]
        self.assertIn("navigate", actions)
        self.assertIn("fill", actions)
        self.assertIn("click", actions)
        self.assertIn("assert_visible", actions)
        self.assertIn("assert_url", actions)
        self.assertIn("assert_not_visible", actions)
        self.assertIn("wait", actions)

    def test_blank_lines_before_steps_are_ignored(self) -> None:
        content = """\
# Scenario: whitespace test
# Requirement: EC-1

## Steps

1. navigate: /ping
2. assert visible: "pong"
"""
        scenarios = self._parse(content)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(len(scenarios[0].steps), 2)

    def test_multiple_scenarios_separated_by_dashes(self) -> None:
        content = """\
# Scenario: first
# Requirement: FR-1

## Steps
1. navigate: /a
2. assert visible: "A"

---

# Scenario: second
# Requirement: FR-2

## Steps
1. navigate: /b
2. assert visible: "B"
"""
        scenarios = self._parse(content)
        self.assertEqual(len(scenarios), 2)
        self.assertEqual(scenarios[0].name, "first")
        self.assertEqual(scenarios[1].name, "second")

    def test_malformed_block_missing_scenario_header_is_skipped(self) -> None:
        content = """\
# Requirement: EC-1

## Steps
1. navigate: /x
"""
        scenarios = self._parse(content)
        self.assertEqual(len(scenarios), 0)

    def test_empty_file_returns_empty_list(self) -> None:
        f = self.tmp / "empty.journey.md"
        f.write_text("", encoding="utf-8")
        result = parse_journey_file(f)
        self.assertEqual(result, [])

    def test_block_with_no_steps_is_skipped(self) -> None:
        content = """\
# Scenario: no steps
# Requirement: FR-1

## Steps
"""
        scenarios = self._parse(content)
        self.assertEqual(len(scenarios), 0)


# ---------------------------------------------------------------------------
# parse_journey_block — direct block parsing
# ---------------------------------------------------------------------------

class TestParseJourneyBlock(unittest.TestCase):
    def test_raises_on_missing_scenario_header(self) -> None:
        block = """\
## Steps
1. navigate: /x
"""
        with self.assertRaises(ValueError):
            parse_journey_block(block, "test.journey.md")

    def test_raises_on_empty_steps(self) -> None:
        block = """\
# Scenario: empty steps
# Requirement: FR-1

## Steps
"""
        with self.assertRaises(ValueError):
            parse_journey_block(block, "test.journey.md")

    def test_steps_section_stops_at_next_h2(self) -> None:
        block = """\
# Scenario: section boundary
# Requirement: FR-1

## Steps
1. navigate: /a

## Notes
Some notes here that look like steps: navigate: /b
"""
        scenario = parse_journey_block(block, "test.journey.md")
        # Only the step before ## Notes should be parsed
        self.assertEqual(len(scenario.steps), 1)
        self.assertEqual(scenario.steps[0].action, "navigate")
        self.assertEqual(scenario.steps[0].args[0], "/a")

    def test_unrecognised_step_lines_warn_and_are_skipped(self) -> None:
        import io
        buf = io.StringIO()
        block = """\
# Scenario: unknown steps
# Requirement: FR-1

## Steps
1. navigate: /x
2. this is not a valid step at all
3. assert visible: "OK"
"""
        with patch("sys.stderr", buf):
            scenario = parse_journey_block(block, "test.journey.md")
        # Only the two valid steps should survive
        self.assertEqual(len(scenario.steps), 2)
        self.assertIn("WARNING", buf.getvalue())


# ---------------------------------------------------------------------------
# Env file output
# ---------------------------------------------------------------------------

class TestWriteEnvFiles(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_result(self, passed: bool, source: str = "01-login.journey.md"):
        scenario = Scenario(name="test", requirement="FR-1",
                            steps=[], source_file=source)
        return (scenario, passed, "detail")

    def test_counts_file_written(self) -> None:
        results = [self._make_result(True), self._make_result(False)]
        write_env_files(results, self.tmp, "my-spec")
        counts = self.tmp / "my-spec-scenario-counts-journey.env"
        self.assertTrue(counts.exists())
        content = counts.read_text()
        self.assertIn("SCENARIO_PASSED=1", content)
        self.assertIn("SCENARIO_FAILED=1", content)
        self.assertIn("SCENARIO_TOTAL=2", content)

    def test_category_breakdown_written(self) -> None:
        results = [
            self._make_result(True, "01-login.journey.md"),
            self._make_result(False, "02-checkout.journey.md"),
        ]
        write_env_files(results, self.tmp, "my-spec")
        cat = self.tmp / "my-spec-category-breakdown-journey.env"
        self.assertTrue(cat.exists())
        content = cat.read_text()
        self.assertIn("CAT_01_PASSED=1", content)
        self.assertIn("CAT_02_PASSED=0", content)

    def test_all_passed(self) -> None:
        results = [self._make_result(True), self._make_result(True)]
        write_env_files(results, self.tmp, "spec")
        counts = (self.tmp / "spec-scenario-counts-journey.env").read_text()
        self.assertIn("SCENARIO_FAILED=0", counts)
        self.assertIn("SCENARIO_TOTAL=2", counts)

    def test_no_prefix_file_goes_to_99(self) -> None:
        results = [self._make_result(True, "login.journey.md")]
        write_env_files(results, self.tmp, "spec")
        cat = (self.tmp / "spec-category-breakdown-journey.env").read_text()
        self.assertIn("CAT_99_PASSED=1", cat)

    def test_pass_rate_computed(self) -> None:
        results = [self._make_result(True)] * 3 + [self._make_result(False)]
        write_env_files(results, self.tmp, "spec")
        counts = (self.tmp / "spec-scenario-counts-journey.env").read_text()
        self.assertIn("SCENARIO_RATE=75", counts)

    def test_empty_results_zero_rate(self) -> None:
        write_env_files([], self.tmp, "spec")
        counts = (self.tmp / "spec-scenario-counts-journey.env").read_text()
        self.assertIn("SCENARIO_RATE=0", counts)
        self.assertIn("SCENARIO_TOTAL=0", counts)


# ---------------------------------------------------------------------------
# Per-test results JSON
# ---------------------------------------------------------------------------

class TestWritePerTestResults(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _make_result(self, name: str, passed: bool, detail: str = "ok"):
        scenario = Scenario(name=name, requirement="FR-1",
                            steps=[], source_file="01-test.journey.md")
        return (scenario, passed, detail)

    def test_writes_json_with_correct_fields(self) -> None:
        results = [
            self._make_result("login-flow", True),
            self._make_result("signup-flow", False, "Element not found"),
        ]
        write_per_test_results(results, self.tmp, "my-spec", 3)
        import json
        path = self.tmp / "my-spec-test-results-iter3.json"
        self.assertTrue(path.exists())
        entries = json.loads(path.read_text())
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["name"], "login-flow")
        self.assertEqual(entries[0]["status"], "passed")
        self.assertEqual(entries[1]["status"], "failed")
        self.assertEqual(entries[1]["error_type"], "assertion")

    def test_iteration_zero_skips(self) -> None:
        results = [self._make_result("test", True)]
        write_per_test_results(results, self.tmp, "spec", 0)
        self.assertFalse((self.tmp / "spec-test-results-iter0.json").exists())

    def test_merges_with_existing(self) -> None:
        import json
        existing = [{"name": "http-scenario-1", "status": "passed", "error_type": "assertion"}]
        path = self.tmp / "spec-test-results-iter1.json"
        path.write_text(json.dumps(existing))
        results = [self._make_result("journey-1", True)]
        write_per_test_results(results, self.tmp, "spec", 1)
        entries = json.loads(path.read_text())
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["name"], "http-scenario-1")
        self.assertEqual(entries[1]["name"], "journey-1")


if __name__ == "__main__":
    unittest.main()
