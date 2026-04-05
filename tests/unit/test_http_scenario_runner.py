"""Unit tests for scripts/lib/http-scenario-runner.py.

Tests cover:
- Parsing *.scenarios.md files into Scenario dataclasses (no network)
- Assertion evaluation with mock response data
- Env file output format
- Error handling: empty files, missing headers, connection errors
"""

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Dynamic import of the runner script (not a package, no __init__.py)
# ---------------------------------------------------------------------------

_RUNNER_PATH = (
    Path(__file__).parent.parent.parent
    / "scripts" / "lib" / "http-scenario-runner.py"
)


def _load_runner() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("http_scenario_runner", _RUNNER_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["http_scenario_runner"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


_runner = _load_runner()

# Convenience aliases
parse_scenarios_file = _runner.parse_scenarios_file
parse_scenario_block = _runner.parse_scenario_block
_parse_assertion = _runner._parse_assertion
_check_assertion = _runner._check_assertion
_interpolate = _runner._interpolate
write_env_files = _runner.write_env_files
write_per_test_results = _runner.write_per_test_results
Scenario = _runner.Scenario
Step = _runner.Step
Assertion = _runner.Assertion


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scenarios_file(tmp_path: Path, content: str) -> Path:
    f = tmp_path / "01-contract.scenarios.md"
    f.write_text(content, encoding="utf-8")
    return f


# ---------------------------------------------------------------------------
# Parsing — single-step scenario
# ---------------------------------------------------------------------------

class TestParseSingleStep(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _parse_file(self, content: str) -> list:
        f = _make_scenarios_file(self.tmp, content)
        return parse_scenarios_file(f)

    def test_single_step_parses_name(self) -> None:
        content = """\
# Scenario: reject invalid email
# Requirement: EC-1

## Request
POST /api/users
Content-Type: application/json
{"email": "bad"}

## Expect
- Status: 422
"""
        scenarios = self._parse_file(content)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].name, "reject invalid email")
        self.assertEqual(scenarios[0].requirement, "EC-1")

    def test_single_step_parses_method_and_path(self) -> None:
        content = """\
# Scenario: create item
# Requirement: FR-1

## Request
POST /api/items
Content-Type: application/json
{"name": "widget"}

## Expect
- Status: 201
"""
        scenarios = self._parse_file(content)
        self.assertEqual(len(scenarios), 1)
        step = scenarios[0].steps[0]
        self.assertEqual(step.method, "POST")
        self.assertEqual(step.path, "/api/items")

    def test_single_step_parses_status_assertion(self) -> None:
        content = """\
# Scenario: check status
# Requirement: FR-2

## Request
GET /api/status

## Expect
- Status: 200
"""
        scenarios = self._parse_file(content)
        step = scenarios[0].steps[0]
        self.assertEqual(len(step.assertions), 1)
        self.assertEqual(step.assertions[0].kind, "status")
        self.assertEqual(step.assertions[0].args["code"], "200")

    def test_single_step_parses_all_assertion_types(self) -> None:
        content = """\
# Scenario: full assertion set
# Requirement: FR-3

## Request
POST /api/auth
Content-Type: application/json
{"user": "alice"}

## Expect
- Status: 200
- Body contains field "token"
- Body field "user" equals "alice"
- Body field "user" contains "ali"
- Body field "token" is type "string"
- Header "Content-Type" contains "json"
"""
        scenarios = self._parse_file(content)
        step = scenarios[0].steps[0]
        kinds = [a.kind for a in step.assertions]
        self.assertIn("status", kinds)
        self.assertIn("body_contains_field", kinds)
        self.assertIn("body_field_equals", kinds)
        self.assertIn("body_field_contains", kinds)
        self.assertIn("body_field_type", kinds)
        self.assertIn("header_contains", kinds)

    def test_blank_line_before_heading_does_not_break_parser(self) -> None:
        # Regression: blank lines between # header lines and ## sections
        content = """\
# Scenario: whitespace test
# Requirement: EC-2

## Request
GET /api/ping

## Expect
- Status: 200
"""
        scenarios = self._parse_file(content)
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0].name, "whitespace test")

    def test_multiple_scenarios_separated_by_dashes(self) -> None:
        content = """\
# Scenario: first
# Requirement: FR-1

## Request
GET /api/a

## Expect
- Status: 200

---

# Scenario: second
# Requirement: FR-2

## Request
GET /api/b

## Expect
- Status: 404
"""
        scenarios = self._parse_file(content)
        self.assertEqual(len(scenarios), 2)
        self.assertEqual(scenarios[0].name, "first")
        self.assertEqual(scenarios[1].name, "second")

    def test_malformed_block_is_skipped(self) -> None:
        # Block missing # Scenario: header — should be skipped
        content = """\
# Requirement: EC-1

## Request
GET /api/x

## Expect
- Status: 200
"""
        scenarios = self._parse_file(content)
        self.assertEqual(len(scenarios), 0)

    def test_empty_file_returns_empty_list(self) -> None:
        f = self.tmp / "empty.scenarios.md"
        f.write_text("", encoding="utf-8")
        result = parse_scenarios_file(f)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Parsing — multi-step scenario
# ---------------------------------------------------------------------------

class TestParseMultiStep(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_multi_step_parses_both_steps(self) -> None:
        content = """\
# Scenario: create then retrieve
# Requirement: FR-2

## Step 1
POST /api/items
Content-Type: application/json
{"name": "widget"}

## Expect
- Status: 201
- Capture: item_id = body.id

## Step 2
GET /api/items/{item_id}

## Expect
- Status: 200
- Body field "name" equals "widget"
"""
        f = _make_scenarios_file(self.tmp, content)
        scenarios = parse_scenarios_file(f)
        self.assertEqual(len(scenarios), 1)
        scenario = scenarios[0]
        self.assertEqual(len(scenario.steps), 2)

    def test_multi_step_step1_has_capture(self) -> None:
        content = """\
# Scenario: capture test
# Requirement: FR-3

## Step 1
POST /api/items
Content-Type: application/json
{"x": 1}

## Expect
- Status: 201
- Capture: my_id = body.id

## Step 2
GET /api/items/{my_id}

## Expect
- Status: 200
"""
        f = _make_scenarios_file(self.tmp, content)
        scenarios = parse_scenarios_file(f)
        step1_assertions = scenarios[0].steps[0].assertions
        capture = next(a for a in step1_assertions if a.kind == "capture")
        self.assertEqual(capture.args["var"], "my_id")
        # The parser strips the mandatory "body." prefix; path is the remainder
        self.assertEqual(capture.args["path"], "id")

    def test_multi_step_step2_assertions_correct(self) -> None:
        content = """\
# Scenario: step2 assertions
# Requirement: FR-4

## Step 1
POST /api/things
Content-Type: application/json
{}

## Expect
- Status: 200

## Step 2
GET /api/things/1

## Expect
- Status: 200
- Body field "id" equals "1"
"""
        f = _make_scenarios_file(self.tmp, content)
        scenarios = parse_scenarios_file(f)
        step2 = scenarios[0].steps[1]
        self.assertEqual(len(step2.assertions), 2)
        kinds = {a.kind for a in step2.assertions}
        self.assertIn("status", kinds)
        self.assertIn("body_field_equals", kinds)


# ---------------------------------------------------------------------------
# Assertion evaluation
# ---------------------------------------------------------------------------

class TestCheckAssertion(unittest.TestCase):
    def _check(self, kind: str, args: dict, status: int = 200,
               headers: dict = None, json_body: object = None,
               captures: dict = None) -> tuple:
        a = Assertion(kind=kind, args=args)
        return _check_assertion(a, status, headers or {}, json_body, captures or {})

    def test_status_pass(self) -> None:
        ok, _ = self._check("status", {"code": "200"}, status=200)
        self.assertTrue(ok)

    def test_status_fail(self) -> None:
        ok, msg = self._check("status", {"code": "201"}, status=200)
        self.assertFalse(ok)
        self.assertIn("200", msg)
        self.assertIn("201", msg)

    def test_body_contains_field_pass(self) -> None:
        ok, _ = self._check("body_contains_field", {"field": "id"},
                             json_body={"id": 42})
        self.assertTrue(ok)

    def test_body_contains_field_nested_dot_path(self) -> None:
        ok, _ = self._check("body_contains_field", {"field": "data.uptime"},
                             json_body={"data": {"uptime": 123}})
        self.assertTrue(ok)

    def test_body_contains_field_nested_dot_path_missing(self) -> None:
        ok, msg = self._check("body_contains_field", {"field": "data.uptime"},
                              json_body={"data": {"status": "ok"}})
        self.assertFalse(ok)
        self.assertIn("data.uptime", msg)

    def test_body_contains_field_fail_missing(self) -> None:
        ok, msg = self._check("body_contains_field", {"field": "token"},
                              json_body={"id": 42})
        self.assertFalse(ok)
        self.assertIn("token", msg)

    def test_body_contains_field_fail_not_dict(self) -> None:
        ok, _ = self._check("body_contains_field", {"field": "id"},
                             json_body=[1, 2, 3])
        self.assertFalse(ok)

    def test_body_field_equals_pass(self) -> None:
        ok, _ = self._check("body_field_equals",
                             {"field": "name", "value": "alice"},
                             json_body={"name": "alice"})
        self.assertTrue(ok)

    def test_body_field_equals_fail(self) -> None:
        ok, _ = self._check("body_field_equals",
                             {"field": "name", "value": "alice"},
                             json_body={"name": "bob"})
        self.assertFalse(ok)

    def test_body_field_contains_pass(self) -> None:
        ok, _ = self._check("body_field_contains",
                             {"field": "error", "value": "email"},
                             json_body={"error": "invalid email format"})
        self.assertTrue(ok)

    def test_body_field_contains_fail(self) -> None:
        ok, _ = self._check("body_field_contains",
                             {"field": "error", "value": "phone"},
                             json_body={"error": "invalid email format"})
        self.assertFalse(ok)

    def test_header_contains_pass(self) -> None:
        ok, _ = self._check("header_contains",
                             {"header": "content-type", "value": "json"},
                             headers={"content-type": "application/json"})
        self.assertTrue(ok)

    def test_header_contains_fail(self) -> None:
        ok, _ = self._check("header_contains",
                             {"header": "content-type", "value": "xml"},
                             headers={"content-type": "application/json"})
        self.assertFalse(ok)

    def test_capture_stores_value(self) -> None:
        captures: dict = {}
        a = Assertion(kind="capture", args={"var": "item_id", "path": "id"})
        ok, _ = _check_assertion(a, 201, {}, {"id": 99}, captures)
        self.assertTrue(ok)
        self.assertEqual(captures["item_id"], "99")

    def test_capture_nested_path(self) -> None:
        captures: dict = {}
        a = Assertion(kind="capture", args={"var": "uid", "path": "user.id"})
        ok, _ = _check_assertion(a, 200, {}, {"user": {"id": 7}}, captures)
        self.assertTrue(ok)
        self.assertEqual(captures["uid"], "7")

    def test_capture_missing_path_fails(self) -> None:
        captures: dict = {}
        # Use a key name that has no overlap with error-message structural words
        a = Assertion(kind="capture", args={"var": "x", "path": "absent.nested"})
        ok, msg = _check_assertion(a, 200, {}, {"other": 1}, captures)
        self.assertFalse(ok)
        # Path fragment should appear in the diagnostic message
        self.assertIn("absent", msg)

    # --- body_field_type assertions ---

    def test_body_field_type_number_float_pass(self) -> None:
        ok, _ = self._check("body_field_type",
                             {"field": "uptime", "expected_type": "number"},
                             json_body={"uptime": 42.5})
        self.assertTrue(ok)

    def test_body_field_type_number_int_pass(self) -> None:
        ok, _ = self._check("body_field_type",
                             {"field": "count", "expected_type": "number"},
                             json_body={"count": 42})
        self.assertTrue(ok)

    def test_body_field_type_number_fail(self) -> None:
        ok, msg = self._check("body_field_type",
                               {"field": "count", "expected_type": "number"},
                               json_body={"count": "42"})
        self.assertFalse(ok)
        self.assertIn("expected type number", msg)
        self.assertIn("str", msg)

    def test_body_field_type_string_pass(self) -> None:
        ok, _ = self._check("body_field_type",
                             {"field": "name", "expected_type": "string"},
                             json_body={"name": "hello"})
        self.assertTrue(ok)

    def test_body_field_type_boolean_pass(self) -> None:
        ok, _ = self._check("body_field_type",
                             {"field": "active", "expected_type": "boolean"},
                             json_body={"active": True})
        self.assertTrue(ok)

    def test_body_field_type_null_pass(self) -> None:
        ok, _ = self._check("body_field_type",
                             {"field": "deleted", "expected_type": "null"},
                             json_body={"deleted": None})
        self.assertTrue(ok)

    def test_body_field_type_array_pass(self) -> None:
        ok, _ = self._check("body_field_type",
                             {"field": "items", "expected_type": "array"},
                             json_body={"items": [1, 2, 3]})
        self.assertTrue(ok)

    def test_body_field_type_object_pass(self) -> None:
        ok, _ = self._check("body_field_type",
                             {"field": "meta", "expected_type": "object"},
                             json_body={"meta": {"a": 1}})
        self.assertTrue(ok)

    def test_body_field_type_unknown_type(self) -> None:
        ok, msg = self._check("body_field_type",
                               {"field": "x", "expected_type": "integer"},
                               json_body={"x": 42})
        self.assertFalse(ok)
        self.assertIn("unknown type", msg)
        self.assertIn("valid:", msg)

    def test_body_field_type_number_rejects_bool(self) -> None:
        """bool is a subclass of int — must NOT pass as 'number'."""
        ok, msg = self._check("body_field_type",
                               {"field": "flag", "expected_type": "number"},
                               json_body={"flag": True})
        self.assertFalse(ok)
        self.assertIn("boolean", msg)

    def test_body_field_type_nested_path(self) -> None:
        """The exact bug case: data.uptime is 4.954 (a number)."""
        ok, _ = self._check("body_field_type",
                             {"field": "data.uptime", "expected_type": "number"},
                             json_body={"data": {"uptime": 4.954}})
        self.assertTrue(ok)


# ---------------------------------------------------------------------------
# Interpolation
# ---------------------------------------------------------------------------

class TestInterpolate(unittest.TestCase):
    def test_substitutes_captured_var(self) -> None:
        result = _interpolate("/api/items/{item_id}", {"item_id": "42"})
        self.assertEqual(result, "/api/items/42")

    def test_no_placeholders_unchanged(self) -> None:
        result = _interpolate("/api/status", {})
        self.assertEqual(result, "/api/status")

    def test_unresolved_placeholder_warns(self) -> None:
        import io
        buf = io.StringIO()
        with patch("sys.stderr", buf):
            result = _interpolate("/api/items/{missing_var}", {})
        # String is returned unchanged when placeholder has no capture binding
        self.assertEqual(result, "/api/items/{missing_var}")
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

    def _make_result(self, passed: bool, source: str = "01-contract.scenarios.md"):
        scenario = Scenario(name="test", requirement="FR-1",
                            steps=[], source_file=source)
        return (scenario, passed, "detail")

    def test_counts_file_written(self) -> None:
        results = [self._make_result(True), self._make_result(False)]
        write_env_files(results, self.tmp, "my-spec")
        counts = self.tmp / "my-spec-scenario-counts-http.env"
        self.assertTrue(counts.exists())
        content = counts.read_text()
        self.assertIn("SCENARIO_PASSED=1", content)
        self.assertIn("SCENARIO_FAILED=1", content)
        self.assertIn("SCENARIO_TOTAL=2", content)

    def test_category_breakdown_written(self) -> None:
        results = [self._make_result(True, "01-contract.scenarios.md"),
                   self._make_result(False, "02-happy-path.scenarios.md")]
        write_env_files(results, self.tmp, "my-spec")
        cat = self.tmp / "my-spec-category-breakdown-http.env"
        self.assertTrue(cat.exists())
        content = cat.read_text()
        self.assertIn("CAT_01_PASSED=1", content)
        self.assertIn("CAT_02_PASSED=0", content)

    def test_all_passed(self) -> None:
        results = [self._make_result(True), self._make_result(True)]
        write_env_files(results, self.tmp, "spec")
        counts = (self.tmp / "spec-scenario-counts-http.env").read_text()
        self.assertIn("SCENARIO_FAILED=0", counts)
        self.assertIn("SCENARIO_TOTAL=2", counts)

    def test_no_prefix_file_goes_to_99(self) -> None:
        results = [self._make_result(True, "contract.scenarios.md")]
        write_env_files(results, self.tmp, "spec")
        cat = (self.tmp / "spec-category-breakdown-http.env").read_text()
        self.assertIn("CAT_99_PASSED=1", cat)

    def test_pass_rate_computed(self) -> None:
        results = [self._make_result(True)] * 3 + [self._make_result(False)]
        write_env_files(results, self.tmp, "spec")
        counts = (self.tmp / "spec-scenario-counts-http.env").read_text()
        self.assertIn("SCENARIO_RATE=75", counts)

    def test_empty_results_zero_rate(self) -> None:
        write_env_files([], self.tmp, "spec")
        counts = (self.tmp / "spec-scenario-counts-http.env").read_text()
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
                            steps=[], source_file="01-test.scenarios.md")
        return (scenario, passed, detail)

    def test_writes_json_with_correct_fields(self) -> None:
        results = [
            self._make_result("test-pass", True),
            self._make_result("test-fail", False, "expected 200 got 500"),
        ]
        write_per_test_results(results, self.tmp, "my-spec", 2)
        import json
        path = self.tmp / "my-spec-test-results-iter2.json"
        self.assertTrue(path.exists())
        entries = json.loads(path.read_text())
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["name"], "test-pass")
        self.assertEqual(entries[0]["status"], "passed")
        self.assertEqual(entries[0]["error_type"], "assertion")
        self.assertEqual(entries[1]["name"], "test-fail")
        self.assertEqual(entries[1]["status"], "failed")

    def test_crash_gets_crash_error_type(self) -> None:
        results = [
            self._make_result("crash-test", False, "CRASHED: TypeError: foo"),
        ]
        write_per_test_results(results, self.tmp, "spec", 1)
        import json
        entries = json.loads((self.tmp / "spec-test-results-iter1.json").read_text())
        self.assertEqual(entries[0]["error_type"], "crash")

    def test_iteration_zero_skips(self) -> None:
        results = [self._make_result("test", True)]
        write_per_test_results(results, self.tmp, "spec", 0)
        self.assertFalse((self.tmp / "spec-test-results-iter0.json").exists())

    def test_merges_with_existing(self) -> None:
        import json
        existing = [{"name": "unit-test-1", "status": "passed", "error_type": "assertion"}]
        path = self.tmp / "spec-test-results-iter1.json"
        path.write_text(json.dumps(existing))
        results = [self._make_result("http-scenario-1", True)]
        write_per_test_results(results, self.tmp, "spec", 1)
        entries = json.loads(path.read_text())
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["name"], "unit-test-1")
        self.assertEqual(entries[1]["name"], "http-scenario-1")


# ---------------------------------------------------------------------------
# run_scenario — execution with mock HTTP
# ---------------------------------------------------------------------------

run_scenario = _runner.run_scenario


class TestRunScenario(unittest.TestCase):
    """Exercise run_scenario() using urllib.request.urlopen mocks."""

    def _mock_response(self, status: int, body: bytes = b"{}",
                       headers: dict = None):
        """Build a minimal mock response compatible with run_scenario's read pattern."""
        resp = MagicMock()
        resp.status = status
        resp.read.return_value = body
        resp.headers = MagicMock()
        resp.headers.items.return_value = list((headers or {}).items())
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    def _make_single_step_scenario(
        self, method: str, path: str, assertions: list,
    ):
        step = Step(method=method, path=path, headers={}, body=None,
                    assertions=assertions)
        return Scenario(name="test", requirement="FR-1",
                        steps=[step], source_file="01-test.scenarios.md")

    def test_single_step_status_pass(self) -> None:
        scenario = self._make_single_step_scenario(
            "GET", "/api/status",
            [Assertion(kind="status", args={"code": "200"})],
        )
        mock_resp = self._mock_response(200)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            ok, detail = run_scenario(scenario, "http://localhost:8000", 5)
        self.assertTrue(ok)

    def test_single_step_status_fail(self) -> None:
        scenario = self._make_single_step_scenario(
            "GET", "/api/status",
            [Assertion(kind="status", args={"code": "200"})],
        )
        mock_resp = self._mock_response(500)
        with patch("urllib.request.urlopen", return_value=mock_resp):
            ok, detail = run_scenario(scenario, "http://localhost:8000", 5)
        self.assertFalse(ok)
        self.assertIn("500", detail)

    def test_connection_error_returns_failure(self) -> None:
        scenario = self._make_single_step_scenario(
            "GET", "/api/status",
            [Assertion(kind="status", args={"code": "200"})],
        )
        with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
            ok, detail = run_scenario(scenario, "http://localhost:8000", 5)
        self.assertFalse(ok)
        self.assertIn("connection error", detail.lower())

    def test_capture_propagates_to_next_step(self) -> None:
        """Step 1 captures item_id; step 2 uses {item_id} in path — core behavior."""
        step1 = Step(
            method="POST", path="/api/items", headers={}, body='{"name": "x"}',
            assertions=[
                Assertion(kind="status", args={"code": "201"}),
                Assertion(kind="capture", args={"var": "item_id", "path": "id"}),
            ],
        )
        step2 = Step(
            method="GET", path="/api/items/{item_id}", headers={}, body=None,
            assertions=[Assertion(kind="status", args={"code": "200"})],
        )
        scenario = Scenario(name="create then retrieve", requirement="FR-1",
                            steps=[step1, step2], source_file="01-test.scenarios.md")

        resp1 = self._mock_response(201, b'{"id": 42}')
        resp2 = self._mock_response(200, b'{"id": 42, "name": "x"}')

        call_count = 0
        real_urls: list[str] = []

        def fake_urlopen(req, timeout=None):
            nonlocal call_count
            call_count += 1
            real_urls.append(req.full_url)
            return resp1 if call_count == 1 else resp2

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            ok, detail = run_scenario(scenario, "http://localhost:8000", 5)

        self.assertTrue(ok, detail)
        self.assertEqual(call_count, 2)
        # Step 2 URL must contain the captured value, not the raw placeholder
        self.assertIn("/api/items/42", real_urls[1])
        self.assertNotIn("{item_id}", real_urls[1])

    def test_capture_failure_stops_scenario(self) -> None:
        """If capture fails (path not in response), the scenario should fail at that step."""
        step1 = Step(
            method="POST", path="/api/items", headers={}, body=None,
            assertions=[
                Assertion(kind="status", args={"code": "201"}),
                Assertion(kind="capture", args={"var": "item_id", "path": "id"}),
            ],
        )
        step2 = Step(
            method="GET", path="/api/items/{item_id}", headers={}, body=None,
            assertions=[Assertion(kind="status", args={"code": "200"})],
        )
        scenario = Scenario(name="capture fail test", requirement="FR-1",
                            steps=[step1, step2], source_file="01-test.scenarios.md")

        # Response does not contain "id" — capture will fail
        resp1 = self._mock_response(201, b'{"other": "nope"}')

        with patch("urllib.request.urlopen", return_value=resp1):
            ok, detail = run_scenario(scenario, "http://localhost:8000", 5)

        self.assertFalse(ok)
        self.assertIn("Step 1", detail)


if __name__ == "__main__":
    unittest.main()
