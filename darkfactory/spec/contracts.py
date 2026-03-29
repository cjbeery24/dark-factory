"""Parse and compile Tier 1 test contracts from spec markdown.

Contracts are YAML blocks inside the Acceptance Criteria section of a spec.
They are compiled deterministically to test code — same input always produces
identical output.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import yaml

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

VALID_TYPES = frozenset({"file_exists", "file_contains", "command", "http", "import"})
REQUIRED_FIELDS = frozenset({"id", "fr", "type", "given", "when", "then"})
_ASYNC_TYPES = frozenset({"http", "import"})
_IDENTIFIER_RE = re.compile(r"[^a-zA-Z0-9_]")
_JS_PROPERTY_RE = re.compile(r"^[a-zA-Z_$][a-zA-Z0-9_$]*$")


@dataclass
class Contract:
    id: str
    fr: str
    type: str
    given: str
    when: dict
    then: dict
    invariant: bool = False


@dataclass
class ContractParseResult:
    contracts: list[Contract] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")
_YAML_FENCE_RE = re.compile(r"^(`{3,}|~{3,})\s*ya?ml", re.IGNORECASE)
_AC_HEADING_RE = re.compile(
    r"^##\s+(?:(?:[0-9]+|[A-Za-z])\.?\s+)?Acceptance\s+Criteria\s*$", re.IGNORECASE
)
_NEXT_H2_RE = re.compile(r"^##\s+")


def parse_contracts(spec_text: str) -> ContractParseResult:
    """Extract YAML contract blocks from the Acceptance Criteria section."""
    result = ContractParseResult()
    lines = spec_text.splitlines()

    # Find Acceptance Criteria section bounds.
    ac_start = None
    ac_end = len(lines)
    for i, line in enumerate(lines):
        if ac_start is None:
            if _AC_HEADING_RE.match(line):
                ac_start = i + 1
        else:
            if _NEXT_H2_RE.match(line) and not _AC_HEADING_RE.match(line):
                ac_end = i
                break

    if ac_start is None:
        return result  # No AC section — not an error, just no contracts.

    # Extract YAML code blocks within AC section.
    yaml_blocks: list[str] = []
    in_fence = False
    fence_len = 0
    fence_char = ""
    block_lines: list[str] = []

    for i in range(ac_start, ac_end):
        line = lines[i]
        if not in_fence:
            m = _YAML_FENCE_RE.match(line)
            if m:
                in_fence = True
                fence_char = m.group(1)[0]
                fence_len = len(m.group(1))
                block_lines = []
        else:
            close = _FENCE_RE.match(line)
            if (
                close
                and close.group(1)[0] == fence_char
                and len(close.group(1)) >= fence_len
            ):
                in_fence = False
                yaml_blocks.append("\n".join(block_lines))
            else:
                block_lines.append(line)

    if not yaml_blocks:
        return result

    # Parse each YAML block.
    for block in yaml_blocks:
        try:
            data = yaml.safe_load(block)
        except yaml.YAMLError as exc:
            result.errors.append(f"Malformed YAML in contract block: {exc}")
            continue

        if not isinstance(data, dict) or "contracts" not in data:
            continue  # Not a contracts block.

        raw_contracts = data["contracts"]
        if not isinstance(raw_contracts, list):
            result.errors.append("'contracts' must be a list")
            continue

        for idx, raw in enumerate(raw_contracts):
            contract, error = _validate_contract(raw, idx)
            if error:
                result.errors.append(error)
            if contract:
                result.contracts.append(contract)

    return result


def _validate_contract(raw: dict, idx: int) -> tuple[Contract | None, str | None]:
    """Validate a single contract dict and return (Contract, error)."""
    if not isinstance(raw, dict):
        return None, f"Contract at index {idx} is not a mapping"

    missing = REQUIRED_FIELDS - set(raw.keys())
    if missing:
        cid = raw.get("id", f"index {idx}")
        return None, f"Contract {cid} missing required fields: {', '.join(sorted(missing))}"

    ctype = raw["type"]
    if ctype not in VALID_TYPES:
        return None, f"Contract {raw['id']} has unknown type '{ctype}' (valid: {', '.join(sorted(VALID_TYPES))})"

    if not isinstance(raw["when"], dict):
        return None, f"Contract {raw['id']} 'when' must be a mapping, got {type(raw['when']).__name__}"

    if not isinstance(raw["then"], dict):
        return None, f"Contract {raw['id']} 'then' must be a mapping, got {type(raw['then']).__name__}"

    return Contract(
        id=str(raw["id"]),
        fr=str(raw["fr"]),
        type=ctype,
        given=str(raw.get("given", "")),
        when=raw["when"],
        then=raw["then"],
        invariant=bool(raw.get("invariant", False)),
    ), None


# ---------------------------------------------------------------------------
# Compiler
# ---------------------------------------------------------------------------


def compile_contracts(
    contracts: list[Contract],
    language: str,
    test_framework: str = "",
) -> list[tuple[str, str]]:
    """Compile contracts to deterministic test files.

    Returns [(filename, content), ...].
    Invariant contracts are skipped.
    Same input always produces identical output.
    """
    active = [c for c in contracts if not c.invariant]
    if not active:
        return []

    if language in ("node", "typescript"):
        return _compile_vitest(active)
    if language == "python":
        return _compile_pytest(active)

    log.warning("No compiler for language '%s', falling back to vitest", language)
    return _compile_vitest(active)


# ---------------------------------------------------------------------------
# Vitest compiler
# ---------------------------------------------------------------------------


def _compile_vitest(contracts: list[Contract]) -> list[tuple[str, str]]:
    """Compile contracts to a vitest test file."""
    test_cases: list[str] = []

    for c in contracts:
        body = _vitest_body(c)
        is_async = c.type in _ASYNC_TYPES
        arrow = "async () =>" if is_async else "() =>"
        test_cases.append(
            f"  it('{_escape_js(c.id)}: {_escape_js(c.given)}', {arrow} {{\n{body}\n  }});\n"
        )

    content = (
        "// Tier 1 Contracts — compiled deterministically from spec.\n"
        "// DO NOT EDIT. Regenerate by re-running the pipeline.\n\n"
        "import { describe, it, expect } from 'vitest';\n"
        "import { readFileSync, existsSync } from 'node:fs';\n"
        "import { execSync } from 'node:child_process';\n\n"
        "describe('Tier 1 Contracts', () => {\n"
        + "\n".join(test_cases)
        + "});\n"
    )

    return [("00-tier1-contracts.test.ts", content)]


def _vitest_body(c: Contract) -> str:
    """Generate the test body for a single contract (vitest)."""
    if c.type == "file_exists":
        path = c.when.get("check", "")
        exists = c.then.get("exists", True)
        op = "" if exists else "!"
        return f"    expect({op}existsSync('{_escape_js(path)}')).toBe(true);"

    if c.type == "file_contains":
        return _vitest_file_contains(c)

    if c.type == "command":
        cmd = c.when.get("cmd", "")
        exit_code = c.then.get("exit_code", 0)
        if exit_code == 0:
            return f"    execSync('{_escape_js(cmd)}', {{ stdio: 'pipe' }});"
        lines = [
            "    let exitCode = 0;",
            "    try {",
            f"      execSync('{_escape_js(cmd)}', {{ stdio: 'pipe' }});",
            "    } catch (e: any) {",
            "      exitCode = e.status ?? 1;",
            "    }",
            f"    expect(exitCode).toBe({int(exit_code)});",
        ]
        return "\n".join(lines)

    if c.type == "http":
        return _vitest_http_body(c)

    if c.type == "import":
        module_path = c.when.get("module", "")
        symbols = c.when.get("symbols", [])
        lines = [
            f"    const mod = await import('{_escape_js(module_path)}');",
        ]
        for sym in symbols:
            lines.append(f"    expect(mod).toHaveProperty('{_escape_js(sym)}');")
        return "\n".join(lines)

    return f"    // Unsupported contract type: {_escape_js(c.type)}"


def _vitest_file_contains(c: Contract) -> str:
    """Generate file_contains test body for vitest."""
    file_path = c.when.get("file", "")
    parse_mode = c.when.get("parse", "text")

    if parse_mode in ("json", "yaml"):
        dot_path = c.when.get("path", "")
        exists = c.then.get("exists", True)
        # YAML mode note: JSON.parse handles JSON and most simple YAML values.
        # Full YAML support would require a runtime dependency.
        lines = [
            f"    const raw = readFileSync('{_escape_js(file_path)}', 'utf-8');",
            f"    const data = JSON.parse(raw);",
        ]
        if exists:
            lines.append(f"    expect(data).toHaveProperty('{_escape_js(dot_path)}');")
        else:
            lines.append(f"    expect(data).not.toHaveProperty('{_escape_js(dot_path)}');")
        return "\n".join(lines)

    # text mode — pattern match
    pattern = c.when.get("pattern", "")
    _validate_regex(pattern)
    escaped = _escape_regex_js(pattern)
    matches = c.then.get("matches", True)
    lines = [
        f"    const content = readFileSync('{_escape_js(file_path)}', 'utf-8');",
    ]
    if matches:
        lines.append(f"    expect(content).toMatch(/{escaped}/m);")
    else:
        lines.append(f"    expect(content).not.toMatch(/{escaped}/m);")
    return "\n".join(lines)


def _vitest_http_body(c: Contract) -> str:
    """Generate HTTP contract test body for vitest."""
    method = str(c.when.get("method", "GET")).upper()
    path = c.when.get("path", "/")
    body = c.when.get("body")
    status = c.then.get("status")
    resp_body = c.then.get("body")

    # Use string concatenation instead of template literal to avoid injection.
    lines = [
        f"    const baseUrl = process.env.TEST_BASE_URL ?? 'http://localhost:3000';",
        f"    const res = await fetch(baseUrl + '{_escape_js(path)}', {{",
        f"      method: '{_escape_js(method)}',",
    ]
    if body is not None:
        body_str = json.dumps(body, sort_keys=True)
        lines.append(f"      headers: {{ 'Content-Type': 'application/json' }},")
        lines.append(f"      body: JSON.stringify({body_str}),")
    lines.append("    });")

    if status is not None:
        lines.append(f"    expect(res.status).toBe({int(status)});")

    if resp_body and isinstance(resp_body, dict):
        lines.append("    const data = await res.json();")
        for key, assertion in resp_body.items():
            _emit_http_assertion(lines, key, assertion)

    return "\n".join(lines)


def _emit_http_assertion(lines: list[str], key: str, assertion: object) -> None:
    """Emit a single HTTP response body assertion (safe property access)."""
    # Use bracket notation for safety — no raw key interpolation.
    accessor = f"data['{_escape_js(key)}']"
    if isinstance(assertion, dict):
        if "equals" in assertion:
            val = json.dumps(assertion["equals"], sort_keys=True)
            lines.append(f"    expect({accessor}).toBe({val});")
        elif "type" in assertion:
            lines.append(f"    expect(typeof {accessor}).toBe('{_escape_js(assertion['type'])}');")
        elif "contains" in assertion:
            val = json.dumps(assertion["contains"], sort_keys=True)
            lines.append(f"    expect({accessor}).toContain({val});")
    else:
        val = json.dumps(assertion, sort_keys=True) if isinstance(assertion, (dict, list)) else json.dumps(assertion)
        lines.append(f"    expect({accessor}).toBe({val});")


# ---------------------------------------------------------------------------
# Pytest compiler
# ---------------------------------------------------------------------------


def _compile_pytest(contracts: list[Contract]) -> list[tuple[str, str]]:
    """Compile contracts to a pytest test file."""
    test_funcs: list[str] = []
    seen_names: set[str] = set()

    for c in contracts:
        func_name = _sanitize_identifier(f"test_{c.id}")
        # Avoid collisions.
        if func_name in seen_names:
            func_name = f"{func_name}_{len(seen_names)}"
        seen_names.add(func_name)

        body = _pytest_body(c)
        docstring = f"{c.id}: {c.given}".replace('"', '\\"')
        test_funcs.append(
            f"def {func_name}():\n"
            f'    """{docstring}"""\n'
            f"{body}\n"
        )

    content = (
        "# Tier 1 Contracts — compiled deterministically from spec.\n"
        "# DO NOT EDIT. Regenerate by re-running the pipeline.\n\n"
        "import json\n"
        "import re\n"
        "import subprocess\n"
        "from pathlib import Path\n\n\n"
        + "\n\n".join(test_funcs)
    )

    return [("00_tier1_contracts.py", content)]


def _pytest_body(c: Contract) -> str:
    """Generate the test body for a single contract (pytest)."""
    indent = "    "

    if c.type == "file_exists":
        path = c.when.get("check", "")
        exists = c.then.get("exists", True)
        if exists:
            return f"{indent}assert Path({repr(path)}).exists()"
        return f"{indent}assert not Path({repr(path)}).exists()"

    if c.type == "file_contains":
        return _pytest_file_contains(c, indent)

    if c.type == "command":
        cmd = c.when.get("cmd", "")
        exit_code = c.then.get("exit_code", 0)
        lines = [
            f"{indent}result = subprocess.run({repr(cmd)}, shell=True, capture_output=True)",
            f"{indent}assert result.returncode == {int(exit_code)}",
        ]
        return "\n".join(lines)

    if c.type == "http":
        return _pytest_http_body(c, indent)

    if c.type == "import":
        module_path = c.when.get("module", "")
        symbols = c.when.get("symbols", [])
        lines = [
            f"{indent}import importlib",
            f"{indent}mod = importlib.import_module({repr(module_path)})",
        ]
        for sym in symbols:
            lines.append(f"{indent}assert hasattr(mod, {repr(sym)})")
        return "\n".join(lines)

    return f"{indent}pass  # Unsupported contract type: {c.type}"


def _pytest_file_contains(c: Contract, indent: str) -> str:
    """Generate file_contains test body for pytest."""
    file_path = c.when.get("file", "")
    parse_mode = c.when.get("parse", "text")

    if parse_mode == "json":
        dot_path = c.when.get("path", "")
        exists = c.then.get("exists", True)
        parts = dot_path.split(".")
        accessor = "data"
        lines = [
            f"{indent}data = json.loads(Path({repr(file_path)}).read_text())",
        ]
        if exists:
            for part in parts:
                lines.append(f"{indent}assert {repr(part)} in {accessor}")
                accessor = f"{accessor}[{repr(part)}]"
        else:
            # Navigate to parent, assert key absent.
            for part in parts[:-1]:
                lines.append(f"{indent}assert {repr(part)} in {accessor}")
                accessor = f"{accessor}[{repr(part)}]"
            if parts:
                lines.append(f"{indent}assert {repr(parts[-1])} not in {accessor}")
        return "\n".join(lines)

    if parse_mode == "yaml":
        dot_path = c.when.get("path", "")
        exists = c.then.get("exists", True)
        parts = dot_path.split(".")
        accessor = "data"
        lines = [
            f"{indent}import yaml as _yaml",
            f"{indent}data = _yaml.safe_load(Path({repr(file_path)}).read_text())",
        ]
        if exists:
            for part in parts:
                lines.append(f"{indent}assert {repr(part)} in {accessor}")
                accessor = f"{accessor}[{repr(part)}]"
        else:
            for part in parts[:-1]:
                lines.append(f"{indent}assert {repr(part)} in {accessor}")
                accessor = f"{accessor}[{repr(part)}]"
            if parts:
                lines.append(f"{indent}assert {repr(parts[-1])} not in {accessor}")
        return "\n".join(lines)

    # text mode — pattern match
    pattern = c.when.get("pattern", "")
    _validate_regex(pattern)
    matches = c.then.get("matches", True)
    lines = [
        f"{indent}content = Path({repr(file_path)}).read_text()",
    ]
    if matches:
        lines.append(f"{indent}assert re.search({repr(pattern)}, content, re.MULTILINE)")
    else:
        lines.append(f"{indent}assert not re.search({repr(pattern)}, content, re.MULTILINE)")
    return "\n".join(lines)


def _pytest_http_body(c: Contract, indent: str) -> str:
    """Generate HTTP contract test body for pytest."""
    method = str(c.when.get("method", "GET")).upper()
    path = c.when.get("path", "/")
    body = c.when.get("body")
    status = c.then.get("status")
    resp_body = c.then.get("body")

    lines = [
        f"{indent}import urllib.request",
        f"{indent}import urllib.error",
        f"{indent}import os",
        f"{indent}base_url = os.environ.get('TEST_BASE_URL', 'http://localhost:3000')",
    ]

    if body is not None:
        lines.append(f"{indent}data = json.dumps({repr(body)}).encode('utf-8')")
        lines.append(
            f"{indent}req = urllib.request.Request("
            f"base_url + {repr(path)}, data=data, method={repr(method)}, "
            f"headers={{'Content-Type': 'application/json'}})"
        )
    else:
        lines.append(
            f"{indent}req = urllib.request.Request(base_url + {repr(path)}, method={repr(method)})"
        )

    lines.append(f"{indent}try:")
    lines.append(f"{indent}    resp = urllib.request.urlopen(req)")
    lines.append(f"{indent}    status_code = resp.status")
    lines.append(f"{indent}    body_bytes = resp.read()")
    lines.append(f"{indent}except urllib.error.HTTPError as e:")
    lines.append(f"{indent}    status_code = e.code")
    lines.append(f"{indent}    body_bytes = e.read()")

    if status is not None:
        lines.append(f"{indent}assert status_code == {int(status)}")

    if resp_body and isinstance(resp_body, dict):
        lines.append(f"{indent}resp_data = json.loads(body_bytes)")
        for key, assertion in resp_body.items():
            if isinstance(assertion, dict):
                if "equals" in assertion:
                    lines.append(f"{indent}assert resp_data[{repr(key)}] == {repr(assertion['equals'])}")
                elif "type" in assertion:
                    lines.append(f"{indent}assert isinstance(resp_data[{repr(key)}], {_py_type(assertion['type'])})")
                elif "contains" in assertion:
                    lines.append(f"{indent}assert {repr(assertion['contains'])} in resp_data[{repr(key)}]")
            else:
                lines.append(f"{indent}assert resp_data[{repr(key)}] == {repr(assertion)}")

    return "\n".join(lines)


def _py_type(type_name: str) -> str:
    """Map contract type names to Python type names."""
    return {"string": "str", "number": "int|float", "boolean": "bool", "object": "dict", "array": "list"}.get(
        type_name, "object"
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _escape_js(s: str) -> str:
    """Escape a string for use in single-quoted JavaScript."""
    return (
        str(s)
        .replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\n", "\\n")
        .replace("\r", "\\r")
        .replace("\0", "\\0")
        .replace("\t", "\\t")
        .replace("`", "\\`")
        .replace("${", "\\${")
    )


def _escape_regex_js(pattern: str) -> str:
    """Escape a regex pattern for use inside a /pattern/ JS regex literal.

    Forward slashes must be escaped; other regex metacharacters are intentional.
    """
    return str(pattern).replace("/", "\\/")


def _validate_regex(pattern: str) -> None:
    """Validate that a pattern is a valid regex. Raises ValueError if not."""
    try:
        re.compile(pattern)
    except re.error as exc:
        raise ValueError(f"Invalid regex pattern in contract: {pattern!r}: {exc}") from exc


def _sanitize_identifier(name: str) -> str:
    """Sanitize a string for use as a Python identifier."""
    result = _IDENTIFIER_RE.sub("_", name.lower())
    # Ensure it starts with a letter or underscore.
    if result and result[0].isdigit():
        result = f"_{result}"
    return result or "_unnamed"
