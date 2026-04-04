#!/usr/bin/env python3
"""
http-scenario-runner.py — HTTP behavioral scenario runner for Dark Factory.

Parses *.scenarios.md files (Format A) and executes them against a running
HTTP server. Writes scenario-counts-http.env and category-breakdown-http.env
to the reports directory.

Exit codes:
  0 — all scenarios passed
  1 — one or more scenarios failed (including connection refused)
  2 — setup / infrastructure error (missing --base-url, total parse failure)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Assertion:
    kind: str  # status | body_contains_field | body_field_equals |
               # body_field_contains | header_contains | capture
    args: dict[str, str]


@dataclasses.dataclass
class Step:
    method: str
    path: str
    headers: dict[str, str]
    body: Optional[str]
    assertions: list[Assertion]


@dataclasses.dataclass
class Scenario:
    name: str
    requirement: str
    steps: list[Step]
    source_file: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

_ASSERTION_PATTERNS: list[tuple[str, str, list[str]]] = [
    # kind, pattern, group names
    ("status",              r"^Status:\s*(\d+)$",                       ["code"]),
    ("body_contains_field", r'^Body contains field "(.+)"$',            ["field"]),
    ("body_field_equals",   r'^Body field "(.+)" equals "(.+)"$',       ["field", "value"]),
    ("body_field_contains", r'^Body field "(.+)" contains "(.+)"$',     ["field", "value"]),
    ("header_contains",     r'^Header "(.+)" contains "(.+)"$',         ["header", "value"]),
    ("capture",             r"^Capture:\s*(\w+)\s*=\s*body\.(.+)$",     ["var", "path"]),
]


def _parse_assertion(line: str) -> Optional[Assertion]:
    """Parse a single assertion line from an ## Expect block."""
    line = line.lstrip("- ").strip()
    for kind, pattern, group_names in _ASSERTION_PATTERNS:
        m = re.match(pattern, line)
        if m:
            return Assertion(kind=kind, args=dict(zip(group_names, m.groups())))
    return None


def _parse_request_block(lines: list[str]) -> tuple[str, str, dict[str, str], Optional[str]]:
    """
    Parse a request block: METHOD /path\nHeader: Value\n\nbody.

    Returns (method, path, headers, body).
    Raises ValueError on malformed input.
    """
    if not lines:
        raise ValueError("Empty request block")

    request_line = lines[0].strip()
    parts = request_line.split(None, 1)
    if len(parts) != 2:
        raise ValueError(f"Bad request line: {request_line!r}")
    method, path = parts[0].upper(), parts[1]

    headers: dict[str, str] = {}
    body_lines: list[str] = []
    in_body = False

    for line in lines[1:]:
        if in_body:
            body_lines.append(line)
        elif line.strip() == "":
            in_body = True
        else:
            if ":" in line:
                key, _, val = line.partition(":")
                headers[key.strip()] = val.strip()

    body = "\n".join(body_lines).strip() or None
    return method, path, headers, body


def _parse_expect_block(lines: list[str]) -> list[Assertion]:
    """Parse an ## Expect block, returning a list of Assertions."""
    assertions: list[Assertion] = []
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        a = _parse_assertion(line)
        if a is not None:
            assertions.append(a)
    return assertions


def _split_sections(block: str) -> dict[str, list[str]]:
    """
    Split a scenario block into sections keyed by heading text.

    Recognises:
      ## Request
      ## Expect
      ## Step N
    """
    sections: dict[str, list[str]] = {}
    current_key: Optional[str] = None
    current_lines: list[str] = []

    for raw_line in block.splitlines():
        if raw_line.startswith("## "):
            if current_key is not None:
                sections[current_key] = current_lines
            current_key = raw_line[3:].strip()
            current_lines = []
        else:
            if current_key is not None:
                current_lines.append(raw_line)

    if current_key is not None:
        sections[current_key] = current_lines

    return sections


def parse_scenario_block(block: str, source_file: str) -> Scenario:
    """
    Parse one scenario block (text between --- separators).

    Raises ValueError on unrecoverable parse failure.
    """
    name = ""
    requirement = ""
    steps: list[Step] = []

    # Extract header lines (# Scenario: ..., # Requirement: ...)
    header_lines: list[str] = []
    body_lines: list[str] = []
    past_header = False

    for line in block.splitlines():
        stripped = line.strip()
        if not past_header and stripped.startswith("# "):
            header_lines.append(stripped)
        elif not past_header and stripped == "":
            # Blank lines between header lines are skipped; only non-blank,
            # non-header lines mark the end of the header section.
            pass
        else:
            past_header = True
            body_lines.append(line)

    for h in header_lines:
        m = re.match(r"^# Scenario:\s*(.+)$", h)
        if m:
            name = m.group(1).strip()
        m = re.match(r"^# Requirement:\s*(.+)$", h)
        if m:
            requirement = m.group(1).strip()

    if not name:
        raise ValueError("Scenario block has no '# Scenario:' header")

    body = "\n".join(body_lines)
    sections = _split_sections(body)

    if not sections:
        raise ValueError(f"Scenario {name!r}: no sections found")

    # Single-step: ## Request + ## Expect
    if "Request" in sections and "Expect" in sections:
        method, path, headers, req_body = _parse_request_block(sections["Request"])
        assertions = _parse_expect_block(sections["Expect"])
        steps.append(Step(method=method, path=path, headers=headers,
                          body=req_body, assertions=assertions))
    else:
        # Multi-step: ## Step N + ## Expect pairs
        step_nums = sorted(
            int(k.split()[-1]) for k in sections if re.match(r"^Step \d+$", k)
        )
        if not step_nums:
            raise ValueError(f"Scenario {name!r}: no ## Request and no ## Step N sections")

        for n in step_nums:
            step_key = f"Step {n}"
            if step_key not in sections:
                raise ValueError(f"Scenario {name!r}: missing {step_key!r}")

            method, path, headers, req_body = _parse_request_block(sections[step_key])

            # For multi-step, duplicate ## Expect keys in the sections dict mean
            # only the last one survives. Walk the raw body text instead to find
            # the ## Expect block that immediately follows ## Step N.
            expect_lines = _find_step_expect(body, n)
            assertions = _parse_expect_block(expect_lines)
            steps.append(Step(method=method, path=path, headers=headers,
                               body=req_body, assertions=assertions))

    if not steps:
        raise ValueError(f"Scenario {name!r}: parsed zero steps")

    return Scenario(name=name, requirement=requirement, steps=steps,
                    source_file=source_file)


def _find_step_expect(body: str, step_num: int) -> list[str]:
    """
    Return the lines in the ## Expect block immediately following ## Step N.

    Stops at the next ## heading.
    """
    lines = body.splitlines()
    in_expect = False
    past_step = False
    result: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped == f"## Step {step_num}":
            past_step = True
            in_expect = False
            continue
        if past_step and stripped == "## Expect":
            in_expect = True
            continue
        if in_expect:
            if stripped.startswith("## "):
                break
            result.append(line)

    return result


def parse_scenarios_file(path: pathlib.Path) -> list[Scenario]:
    """
    Parse a *.scenarios.md file into a list of Scenario objects.

    Blocks are separated by lines that are exactly `---`.
    Skips empty blocks and warns on malformed blocks.
    Returns an empty list if no blocks could be parsed.
    """
    text = path.read_text(encoding="utf-8")
    # Split on --- separators (line that is exactly ---)
    raw_blocks = re.split(r"^---$", text, flags=re.MULTILINE)

    scenarios: list[Scenario] = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        # Skip blocks with no # Scenario header at all
        if not re.search(r"^# Scenario:", block, re.MULTILINE):
            continue
        try:
            scenario = parse_scenario_block(block, source_file=str(path))
            scenarios.append(scenario)
        except ValueError as exc:
            print(f"WARNING: Skipping malformed block in {path}: {exc}", file=sys.stderr)

    return scenarios


def load_all_scenarios(scenarios_dir: pathlib.Path) -> list[Scenario]:
    """Load all *.scenarios.md files from a directory."""
    all_scenarios: list[Scenario] = []
    for md_file in sorted(scenarios_dir.rglob("*.scenarios.md")):
        all_scenarios.extend(parse_scenarios_file(md_file))
    return all_scenarios


# ---------------------------------------------------------------------------
# HTTP execution
# ---------------------------------------------------------------------------

def _resolve_body_path(data: object, dot_path: str) -> object:
    """
    Resolve dot-notation path into a parsed JSON value.

    Dot-notation only (v1) — no array indexing.
    """
    parts = dot_path.split(".")
    current = data
    for part in parts:
        if not isinstance(current, dict):
            raise KeyError(f"Expected dict at {part!r}, got {type(current).__name__}")
        if part not in current:
            raise KeyError(f"Key {part!r} not found")
        current = current[part]
    return current


def _interpolate(s: str, captures: dict[str, str]) -> str:
    """
    Replace {var_name} placeholders from the captures dict.

    Warns if unresolved {placeholder} patterns remain after substitution — this
    typically means a Capture assertion in a prior step failed and the variable
    was never populated.
    """
    for k, v in captures.items():
        s = s.replace(f"{{{k}}}", v)
    if re.search(r"\{[A-Za-z_]\w*\}", s):
        print(f"WARNING: unresolved placeholder(s) in {s!r} — "
              "a prior Capture assertion may have failed", file=sys.stderr)
    return s


def run_scenario(
    scenario: Scenario,
    base_url: str,
    timeout: int,
) -> tuple[bool, str]:
    """
    Execute a Scenario against base_url.

    Returns (passed: bool, detail: str).
    """
    captures: dict[str, str] = {}

    for step_idx, step in enumerate(scenario.steps, 1):
        path = _interpolate(step.path, captures)
        # Percent-encode non-ASCII characters in the path so http.client
        # doesn't choke on unicode (e.g. /api/测试/пользователь).
        path = urllib.parse.quote(path, safe="/:@!$&'()*+,;=._~-%")
        url = base_url.rstrip("/") + path

        req_body_bytes = None
        req_headers = dict(step.headers)

        if step.body is not None:
            # JSON-only in v1. Extension point: add form-encoded support here.
            # NOTE: request body interpolation is not supported in v1 — {var}
            # placeholders in the body are NOT substituted from captures.
            req_body_bytes = step.body.encode("utf-8")
            req_headers.setdefault("Content-Type", "application/json")

        request = urllib.request.Request(
            url,
            data=req_body_bytes,
            headers=req_headers,
            method=step.method,
        )

        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status_code = response.status
                resp_headers = {k.lower(): v for k, v in response.headers.items()}
                resp_body_raw = response.read()
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            resp_headers = {k.lower(): v for k, v in exc.headers.items()}
            resp_body_raw = exc.read()
        except OSError as exc:
            # Connection refused, timeout, etc. → scenario failure
            return False, f"Step {step_idx}: connection error — {exc}"

        # Try to parse JSON body
        resp_json: object = None
        try:
            resp_json = json.loads(resp_body_raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        for assertion in step.assertions:
            ok, msg = _check_assertion(assertion, status_code, resp_headers,
                                       resp_json, captures)
            if not ok:
                return False, f"Step {step_idx}: {msg}"

    return True, "all steps passed"


def _check_assertion(
    assertion: Assertion,
    status_code: int,
    resp_headers: dict[str, str],
    resp_json: object,
    captures: dict[str, str],
) -> tuple[bool, str]:
    """
    Evaluate a single assertion. Updates captures dict in place for 'capture' kind.

    Returns (ok: bool, failure_message: str).
    """
    kind = assertion.kind
    args = assertion.args

    if kind == "status":
        expected = int(args["code"])
        if status_code != expected:
            return False, f"expected status {expected}, got {status_code}"
        return True, ""

    if kind == "body_contains_field":
        try:
            _resolve_body_path(resp_json, args["field"])
        except (KeyError, TypeError):
            return False, f"body missing field {args['field']!r}"
        return True, ""

    if kind == "body_field_equals":
        try:
            val = _resolve_body_path(resp_json, args["field"])
        except KeyError as exc:
            return False, f"body field lookup failed: {exc}"
        expected = args["value"]
        if str(val) != expected:
            return False, f"body field {args['field']!r}: expected {expected!r}, got {val!r}"
        return True, ""

    if kind == "body_field_contains":
        try:
            val = _resolve_body_path(resp_json, args["field"])
        except KeyError as exc:
            return False, f"body field lookup failed: {exc}"
        if args["value"] not in str(val):
            return False, (f"body field {args['field']!r}: {args['value']!r} "
                           f"not found in {val!r}")
        return True, ""

    if kind == "header_contains":
        header_key = args["header"].lower()
        header_val = resp_headers.get(header_key, "")
        if args["value"] not in header_val:
            return False, (f"header {args['header']!r}: {args['value']!r} "
                           f"not found in {header_val!r}")
        return True, ""

    if kind == "capture":
        try:
            val = _resolve_body_path(resp_json, args["path"])
        except KeyError as exc:
            return False, f"capture {args['var']!r}: body path lookup failed: {exc}"
        captures[args["var"]] = str(val)
        return True, ""

    return False, f"unknown assertion kind {kind!r}"


# ---------------------------------------------------------------------------
# Env file output
# ---------------------------------------------------------------------------

def _category_prefix(source_file: str) -> str:
    """
    Extract the NN- prefix from a scenario filename.

    E.g. '01-contract.scenarios.md' → '01'.
    Falls back to '99' for filenames without a numeric prefix.
    """
    name = pathlib.Path(source_file).name
    m = re.match(r"^(\d{2})-", name)
    return m.group(1) if m else "99"


def write_env_files(
    results: list[tuple[Scenario, bool, str]],
    reports_dir: pathlib.Path,
    spec_name: str,
) -> None:
    """Write scenario-counts-http.env and category-breakdown-http.env."""
    reports_dir.mkdir(parents=True, exist_ok=True)

    passed = sum(1 for _, ok, _ in results if ok)
    failed = sum(1 for _, ok, _ in results if not ok)
    total = len(results)
    rate = (passed * 100 // total) if total else 0

    counts_path = reports_dir / f"{spec_name}-scenario-counts-http.env"
    counts_path.write_text(
        f"SCENARIO_PASSED={passed}\n"
        f"SCENARIO_FAILED={failed}\n"
        f"SCENARIO_TOTAL={total}\n"
        f"SCENARIO_RATE={rate}\n",
        encoding="utf-8",
    )

    # Category breakdown keyed by NN- prefix
    cat_passed: dict[str, int] = {}
    cat_total: dict[str, int] = {}

    for scenario, ok, _ in results:
        prefix = _category_prefix(scenario.source_file)
        cat_total[prefix] = cat_total.get(prefix, 0) + 1
        if ok:
            cat_passed[prefix] = cat_passed.get(prefix, 0) + 1

    cat_path = reports_dir / f"{spec_name}-category-breakdown-http.env"
    lines: list[str] = []
    for prefix in sorted(cat_total):
        p = cat_passed.get(prefix, 0)
        t = cat_total[prefix]
        lines.append(f"CAT_{prefix}_PASSED={p}")
        lines.append(f"CAT_{prefix}_TOTAL={t}")
    cat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Format A (*.scenarios.md) HTTP scenarios against a live server."
    )
    parser.add_argument("scenarios_dir", help="Directory containing *.scenarios.md files")
    parser.add_argument("--base-url", default=os.environ.get("TEST_BASE_URL", ""),
                        help="Base URL of the server under test (or $TEST_BASE_URL)")
    parser.add_argument("--reports-dir", default=".dark-factory/reports",
                        help="Directory to write .env report files")
    parser.add_argument("--spec-name", default="spec",
                        help="Spec name prefix for report filenames")
    parser.add_argument("--timeout", type=int, default=10,
                        help="Per-request timeout in seconds (default: 10)")
    args = parser.parse_args()

    if not args.base_url:
        print("ERROR: --base-url is required (or set $TEST_BASE_URL)", file=sys.stderr)
        return 2

    scenarios_dir = pathlib.Path(args.scenarios_dir)
    if not scenarios_dir.is_dir():
        print(f"ERROR: scenarios directory not found: {scenarios_dir}", file=sys.stderr)
        return 2

    reports_dir = pathlib.Path(args.reports_dir)

    scenarios = load_all_scenarios(scenarios_dir)
    if not scenarios:
        print("ERROR: No scenarios could be parsed from any *.scenarios.md file",
              file=sys.stderr)
        return 2

    print(f"Running {len(scenarios)} HTTP scenario(s) against {args.base_url}")

    results: list[tuple[Scenario, bool, str]] = []
    for scenario in scenarios:
        try:
            ok, detail = run_scenario(scenario, args.base_url, args.timeout)
        except Exception as exc:
            ok, detail = False, f"CRASHED: {type(exc).__name__}: {exc}"
        status = "PASSED" if ok else "FAILED"
        print(f"DF-SCENARIO: {scenario.name} {status}")
        if not ok:
            print(f"  Detail: {detail}")
        results.append((scenario, ok, detail))

    write_env_files(results, reports_dir, args.spec_name)

    failed_count = sum(1 for _, ok, _ in results if not ok)
    if failed_count:
        print(f"\n{failed_count}/{len(results)} scenario(s) FAILED")
        return 1

    print(f"\nAll {len(results)} scenario(s) PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
