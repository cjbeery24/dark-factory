#!/usr/bin/env python3
"""
journey-runner.py — Browser journey scenario runner for Dark Factory.

Parses *.journey.md files (Format B) and executes them using Playwright.
Requires the `dark-factory-playwright` Docker image or a local Playwright install.
Writes scenario-counts-journey.env and category-breakdown-journey.env.

Exit codes:
  0 — all scenarios passed
  1 — one or more scenarios failed
  2 — setup / infrastructure error (Playwright not installed, missing --base-url,
      total parse failure)
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import pathlib
import re
import sys
from typing import Optional

# Regex metacharacter detector — used by assert_url to distinguish plain paths
# from regex patterns. Compiled once at module scope.
_REGEX_META = re.compile(r"[.*+?^${}()|[\]\\]")

try:
    from playwright.sync_api import sync_playwright, expect, Page, Error as PlaywrightError
except ImportError:
    sys.stderr.write(
        "ERROR: Playwright not installed. "
        "Run: pip install playwright && playwright install chromium\n"
    )
    sys.exit(2)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class StepAction:
    action: str   # navigate | click | fill | assert_visible | assert_url |
                  # assert_not_visible | wait
    args: list[str]


@dataclasses.dataclass
class Scenario:
    name: str
    requirement: str
    steps: list[StepAction]
    source_file: str


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# Patterns for each step action
_STEP_PATTERNS: list[tuple[str, str]] = [
    # (action, regex) — groups become args
    ("navigate",           r"^navigate:\s*(.+)$"),
    ("click",              r"^click:\s*(.+)$"),
    ("fill",               r'^fill:\s*"(.+?)"\s+with\s+"(.+)"$'),
    ("assert_visible",     r'^assert visible:\s*"(.+)"$'),
    ("assert_url",         r"^assert url:\s*(.+)$"),
    ("assert_not_visible", r'^assert not visible:\s*"(.+)"$'),
    ("wait",               r"^wait:\s*(\d+)\s*ms$"),
]


def _parse_step_line(line: str) -> Optional[StepAction]:
    """Parse a numbered step line into a StepAction."""
    # Strip leading "N. " prefix
    line = re.sub(r"^\d+\.\s*", "", line.strip())
    for action, pattern in _STEP_PATTERNS:
        m = re.match(pattern, line, re.IGNORECASE)
        if m:
            return StepAction(action=action, args=list(m.groups()))
    return None


def parse_journey_block(block: str, source_file: str) -> Scenario:
    """
    Parse one scenario block from a *.journey.md file.

    Raises ValueError on unrecoverable parse failure.
    """
    name = ""
    requirement = ""
    steps: list[StepAction] = []

    header_lines: list[str] = []
    body_lines: list[str] = []
    past_header = False

    for line in block.splitlines():
        stripped = line.strip()
        if not past_header and stripped.startswith("# "):
            header_lines.append(stripped)
        elif not past_header and stripped == "":
            # Skip blank lines before first ## heading
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
        raise ValueError("Journey block has no '# Scenario:' header")

    # Find ## Steps section
    in_steps = False
    for line in body_lines:
        stripped = line.strip()
        if stripped == "## Steps":
            in_steps = True
            continue
        if in_steps:
            if stripped.startswith("## "):
                break
            if not stripped or stripped.startswith("#"):
                continue
            action = _parse_step_line(stripped)
            if action is not None:
                steps.append(action)
            else:
                print(f"WARNING: Unrecognised step line in {source_file}: {stripped!r}",
                      file=sys.stderr)

    if not steps:
        raise ValueError(f"Scenario {name!r}: no steps found in ## Steps section")

    return Scenario(name=name, requirement=requirement, steps=steps,
                    source_file=source_file)


def parse_journey_file(path: pathlib.Path) -> list[Scenario]:
    """Parse a *.journey.md file into a list of Scenario objects."""
    text = path.read_text(encoding="utf-8")
    raw_blocks = re.split(r"^---$", text, flags=re.MULTILINE)

    scenarios: list[Scenario] = []
    for block in raw_blocks:
        block = block.strip()
        if not block:
            continue
        if not re.search(r"^# Scenario:", block, re.MULTILINE):
            continue
        try:
            scenario = parse_journey_block(block, source_file=str(path))
            scenarios.append(scenario)
        except ValueError as exc:
            print(f"WARNING: Skipping malformed block in {path}: {exc}", file=sys.stderr)

    return scenarios


def load_all_scenarios(scenarios_dir: pathlib.Path) -> list[Scenario]:
    """Load all *.journey.md files from a directory."""
    all_scenarios: list[Scenario] = []
    for md_file in sorted(scenarios_dir.rglob("*.journey.md")):
        all_scenarios.extend(parse_journey_file(md_file))
    return all_scenarios


# ---------------------------------------------------------------------------
# Browser execution
# ---------------------------------------------------------------------------

def _execute_step(page: Page, step: StepAction, base_url: str) -> None:
    """
    Execute a single step action on the given page.

    Raises playwright.sync_api.Error or AssertionError on failure.
    """
    action = step.action
    args = step.args

    if action == "navigate":
        path = args[0].strip()
        url = base_url.rstrip("/") + path if path.startswith("/") else path
        page.goto(url)

    elif action == "click":
        target = args[0].strip()
        # Selector heuristic: CSS selectors start with . # or [
        if target.startswith((".", "#", "[")):
            page.locator(target).click()
        else:
            # Check if a role=button with this name exists before clicking;
            # fall back to get_by_text only when the button locator has no match.
            # Use .count() to avoid catching real failures (timeouts, etc.) in a
            # broad except clause.
            btn = page.get_by_role("button", name=target)
            if btn.count() > 0:
                btn.click()
            else:
                page.get_by_text(target).click()

    elif action == "fill":
        label, value = args[0].strip(), args[1]
        # Selector heuristic: CSS selectors start with . # or [
        if label.startswith((".", "#", "[")):
            page.locator(label).fill(value)
        else:
            # Use accessible label first, fall back to placeholder text
            lbl = page.get_by_label(label)
            if lbl.count() > 0:
                lbl.fill(value)
            else:
                page.get_by_placeholder(label).fill(value)

    elif action == "assert_visible":
        text = args[0]
        expect(page.get_by_text(text)).to_be_visible()

    elif action == "assert_url":
        pattern = args[0].strip()
        # If the arg is a path (starts with /), construct the full expected URL
        # and compare as a string to avoid regex false-positives (e.g., /register
        # matching /unregister). Only use re.compile for args that look like
        # explicit regex patterns (contain regex metacharacters).
        if pattern.startswith("/") and not _REGEX_META.search(pattern):
            full_url = base_url.rstrip("/") + pattern
            expect(page).to_have_url(full_url)
        else:
            expect(page).to_have_url(re.compile(pattern))

    elif action == "assert_not_visible":
        text = args[0]
        expect(page.get_by_text(text)).to_be_hidden()

    elif action == "wait":
        ms = int(args[0])
        page.wait_for_timeout(ms)

    else:
        raise ValueError(f"Unknown step action: {action!r}")


def run_scenario(
    scenario: Scenario,
    page: Page,
    base_url: str,
) -> tuple[bool, str]:
    """
    Execute all steps of a Scenario on the given page.

    Returns (passed: bool, detail: str).
    """
    for idx, step in enumerate(scenario.steps, 1):
        try:
            _execute_step(page, step, base_url)
        except (PlaywrightError, AssertionError, ValueError, OSError) as exc:
            return False, f"Step {idx} ({step.action}): {exc}"
    return True, "all steps passed"


# ---------------------------------------------------------------------------
# Env file output
# ---------------------------------------------------------------------------

def _category_prefix(source_file: str) -> str:
    """Extract the NN- prefix from a scenario filename, defaulting to '99'."""
    name = pathlib.Path(source_file).name
    m = re.match(r"^(\d{2})-", name)
    if not m:
        print(f"WARNING: scenario file {name!r} has no NN- prefix; "
              "using category '99'", file=sys.stderr)
    return m.group(1) if m else "99"


def write_env_files(
    results: list[tuple[Scenario, bool, str]],
    reports_dir: pathlib.Path,
    spec_name: str,
) -> None:
    """Write scenario-counts-journey.env and category-breakdown-journey.env."""
    reports_dir.mkdir(parents=True, exist_ok=True)

    passed = sum(1 for _, ok, _ in results if ok)
    failed = len(results) - passed
    total = len(results)
    rate = (passed * 100 // total) if total else 0

    counts_path = reports_dir / f"{spec_name}-scenario-counts-journey.env"
    counts_path.write_text(
        f"SCENARIO_PASSED={passed}\n"
        f"SCENARIO_FAILED={failed}\n"
        f"SCENARIO_TOTAL={total}\n"
        f"SCENARIO_RATE={rate}\n",
        encoding="utf-8",
    )

    cat_passed: dict[str, int] = {}
    cat_total: dict[str, int] = {}
    for scenario, ok, _ in results:
        prefix = _category_prefix(scenario.source_file)
        cat_total[prefix] = cat_total.get(prefix, 0) + 1
        if ok:
            cat_passed[prefix] = cat_passed.get(prefix, 0) + 1

    lines: list[str] = []
    for prefix in sorted(cat_total):
        p = cat_passed.get(prefix, 0)
        t = cat_total[prefix]
        lines.append(f"CAT_{prefix}_PASSED={p}")
        lines.append(f"CAT_{prefix}_TOTAL={t}")

    cat_path = reports_dir / f"{spec_name}-category-breakdown-journey.env"
    cat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_per_test_results(
    results: list[tuple[Scenario, bool, str]],
    reports_dir: pathlib.Path,
    spec_name: str,
    iteration: int,
) -> None:
    """Write per-scenario results JSON for stable failure detection.

    Same format as _write_per_test_results in quality-gate.sh.
    Merges with any existing entries (executable/HTTP tests may have written first).
    Skips when ``iteration < 1`` (standalone invocation without pipeline context).
    """
    if iteration < 1:
        return
    entries = []
    for scenario, passed, detail in results:
        entries.append({
            "name": scenario.name,
            "status": "passed" if passed else "failed",
            "error_type": "assertion",
        })
    path = reports_dir / f"{spec_name}-test-results-iter{iteration}.json"
    existing: list = []
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    path.write_text(json.dumps(existing + entries, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Format B (*.journey.md) browser journey scenarios."
    )
    parser.add_argument("scenarios_dir", help="Directory containing *.journey.md files")
    parser.add_argument("--base-url", default=os.environ.get("BROWSER_BASE_URL", ""),
                        help="Base URL of the app under test (or $BROWSER_BASE_URL)")
    parser.add_argument("--reports-dir", default=".dark-factory/reports",
                        help="Directory to write .env report files")
    parser.add_argument("--spec-name", default="spec",
                        help="Spec name prefix for report filenames")
    parser.add_argument("--browser", choices=["chromium", "firefox", "webkit"],
                        default="chromium", help="Browser engine (default: chromium)")
    parser.add_argument("--headed", action="store_true",
                        help="Show browser window (debug mode)")
    parser.add_argument("--timeout", type=int, default=5000,
                        help="Per-action timeout in milliseconds (default: 5000)")
    parser.add_argument("--iteration", type=int, default=0,
                        help="Pipeline iteration number (for per-test results JSON)")
    args = parser.parse_args()

    if not args.base_url:
        print("ERROR: --base-url is required (or set $BROWSER_BASE_URL)", file=sys.stderr)
        return 2

    scenarios_dir = pathlib.Path(args.scenarios_dir)
    if not scenarios_dir.is_dir():
        print(f"ERROR: scenarios directory not found: {scenarios_dir}", file=sys.stderr)
        return 2

    reports_dir = pathlib.Path(args.reports_dir)

    scenarios = load_all_scenarios(scenarios_dir)
    if not scenarios:
        print("ERROR: No scenarios could be parsed from any *.journey.md file",
              file=sys.stderr)
        return 2

    print(f"Running {len(scenarios)} journey scenario(s) against {args.base_url} "
          f"({args.browser})")

    results: list[tuple[Scenario, bool, str]] = []

    chromium_path = os.environ.get("PLAYWRIGHT_CHROMIUM_PATH")
    with sync_playwright() as playwright:
        browser_type = getattr(playwright, args.browser)
        launch_kwargs: dict = {"headless": not args.headed}
        if chromium_path:
            launch_kwargs["executable_path"] = chromium_path
        browser = browser_type.launch(**launch_kwargs)
        context = browser.new_context(base_url=args.base_url)
        context.set_default_timeout(args.timeout)
        context.set_default_navigation_timeout(args.timeout)

        for scenario in scenarios:
            page = context.new_page()
            try:
                ok, detail = run_scenario(scenario, page, args.base_url)
            finally:
                page.close()

            status = "PASSED" if ok else "FAILED"
            print(f"DF-SCENARIO: {scenario.name} {status}")
            if not ok:
                print(f"  Detail: {detail}")
            results.append((scenario, ok, detail))

        context.close()
        browser.close()

    write_env_files(results, reports_dir, args.spec_name)
    write_per_test_results(results, reports_dir, args.spec_name, args.iteration)

    failed_count = sum(1 for _, ok, _ in results if not ok)
    if failed_count:
        print(f"\n{failed_count}/{len(results)} scenario(s) FAILED")
        return 1

    print(f"\nAll {len(results)} scenario(s) PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
