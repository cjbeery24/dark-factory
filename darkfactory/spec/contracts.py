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

    # Option B guard: a `command` contract MAY carry a post-command content
    # assertion in `then` (run the cmd, then assert a file's content). Reject a
    # VACUOUS one — `then.file` with no selector compiles to an always-true
    # assertion (empty regex / no key check) that passes for any file, a silent
    # false positive (Codex review). A meaningful assertion needs a non-empty
    # `pattern` (text mode) or a `path` (json/yaml mode).
    if ctype == "command":
        then = raw["then"]
        if then.get("file"):
            parse_mode = then.get("parse", "text")
            if parse_mode in ("json", "yaml"):
                if not then.get("path"):
                    return None, (
                        f"Contract {raw['id']} command post-assertion with "
                        f"parse={parse_mode} requires a non-empty then.path"
                    )
            elif not then.get("pattern"):
                return None, (
                    f"Contract {raw['id']} command post-assertion (then.file) "
                    f"requires a non-empty then.pattern, or then.parse=json|yaml "
                    f"with then.path"
                )

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
        "import { execSync } from 'node:child_process';\n"
        "import path from 'node:path';\n"
        "import { pathToFileURL } from 'node:url';\n\n"
        "// PROJECT_ROOT is anchored to this test file's own location so that\n"
        "// tier-1 contracts run identically regardless of the CWD the test\n"
        "// runner is invoked from (project root vs. a workspace dir like\n"
        "// apps/api/). Tests live at .dark-factory/scenarios/<spec>/, so\n"
        "// three levels up is the project root.\n"
        "const PROJECT_ROOT = path.resolve(import.meta.dirname, '../../..');\n\n"
        "describe('Tier 1 Contracts', () => {\n"
        + "\n".join(test_cases)
        + "});\n"
    )

    return [("00-tier1-contracts.test.ts", content)]


# Bound every tier-1 ``command`` contract's synchronous execSync so a hung
# tool (``pnpm install``/``pnpm lint`` in a not-yet-wired repo) cannot block
# the vitest worker's event loop to the gate/agent wall — vitest's testTimeout
# cannot interrupt a sync execSync (subprocess-stall sprint, 2026-06-16). 120s
# matches the scenarios' testTimeout default and the scenario-lint fix_hint;
# it is generous for a real command yet well inside the gate wall. Tier-1 is
# framework-rendered and preserved across TE retries, so it MUST comply by
# construction (the scenario-lint ``unbounded-exec-in-scenario`` rule would
# otherwise flag it on every monorepo run with no agent able to fix it).
_TIER1_EXEC_TIMEOUT_MS = 120000
# Python's subprocess.run takes a timeout in SECONDS (vitest execSync in ms).
_TIER1_EXEC_TIMEOUT_S = _TIER1_EXEC_TIMEOUT_MS // 1000


def _vitest_body(c: Contract) -> str:
    """Generate the test body for a single contract (vitest)."""
    if c.type == "file_exists":
        check_path = c.when.get("check", "")
        exists = c.then.get("exists", True)
        op = "" if exists else "!"
        return (
            f"    expect({op}existsSync(path.join(PROJECT_ROOT, "
            f"'{_escape_js(check_path)}'))).toBe(true);"
        )

    if c.type == "file_contains":
        return _vitest_file_contains(c)

    if c.type == "command":
        cmd = c.when.get("cmd", "")
        exit_code = c.then.get("exit_code", 0)
        if exit_code == 0:
            lines = [
                f"    execSync('{_escape_js(cmd)}', "
                f"{{ stdio: 'pipe', cwd: PROJECT_ROOT, "
                f"timeout: {_TIER1_EXEC_TIMEOUT_MS} }});"
            ]
        else:
            lines = [
                "    let exitCode = 0;",
                "    try {",
                f"      execSync('{_escape_js(cmd)}', "
                f"{{ stdio: 'pipe', cwd: PROJECT_ROOT, "
                f"timeout: {_TIER1_EXEC_TIMEOUT_MS} }});",
                "    } catch (e: any) {",
                # A timeout kill (e.killed / ETIMEDOUT) must FAIL, not be coerced
                # to exit 1 — otherwise a hung command on a non-zero-exit
                # contract masquerades as the expected failure and silently
                # passes (Linus review). A genuine non-zero exit sets e.status
                # and is NOT killed, so it falls through to the assertion.
                "      if (e.killed || e.code === 'ETIMEDOUT') throw e;",
                "      exitCode = e.status ?? 1;",
                "    }",
                f"    expect(exitCode).toBe({int(exit_code)});",
            ]
        # Optional post-command SIDE-EFFECT assertion: run the command, then
        # assert a file's content changed (e.g. `pnpm format` rewrote a file).
        # The selectors+outcome live in `then` (file/parse/path/pattern +
        # exists/matches); `when` holds the cmd. This keeps behavioral CLI ACs
        # in the bounded, framework-rendered Tier-1 layer rather than as
        # TE-authored unbounded subprocess tests (Neo Option B, 2026-06-16).
        if c.then.get("file"):
            lines.append(_vitest_file_contains(c, when=c.then, then=c.then))
        return "\n".join(lines)

    if c.type == "http":
        return _vitest_http_body(c)

    if c.type == "import":
        module_path = c.when.get("module", "")
        symbols = c.when.get("symbols", [])
        # Path-like specifiers (e.g. "packages/shared/src/index.ts") are
        # CWD-brittle — they don't resolve as bare specifiers and they
        # definitely don't resolve when vitest runs from a workspace dir.
        # Anchor them to PROJECT_ROOT via pathToFileURL so dynamic import
        # sees an absolute file:// URL. Package names (@scope/name, bare
        # package names without slashes, node: built-ins) and explicit
        # relative paths (./x, ../x) and absolute paths (/x) are left alone.
        if _is_path_like_import_specifier(module_path):
            import_expr = (
                f"await import(pathToFileURL(path.join(PROJECT_ROOT, "
                f"'{_escape_js(module_path)}')).href)"
            )
        else:
            import_expr = f"await import('{_escape_js(module_path)}')"
        lines = [
            f"    const mod = {import_expr};",
        ]
        for sym in symbols:
            lines.append(f"    expect(mod).toHaveProperty('{_escape_js(sym)}');")
        return "\n".join(lines)

    return f"    // Unsupported contract type: {_escape_js(c.type)}"


def _is_path_like_import_specifier(spec: str) -> bool:
    """Return True if *spec* is a CWD-brittle filesystem path specifier.

    A specifier is "path-like" when it contains a slash AND is not already
    unambiguously resolvable from anywhere:
    - ``@scope/name`` is a scoped package name, not a path.
    - ``./foo``, ``../bar``, ``/abs`` are explicit relative/absolute.
    - ``node:fs`` and similar built-in schemes are built-ins.
    - Bare package names like ``vitest`` or ``drizzle-orm`` are NOT
      path-like — they have no slash and resolve via node_modules.
    Everything else (``packages/shared/src/index.ts``, ``apps/api/dist/x.js``)
    is a project-relative filesystem path that must be anchored to
    PROJECT_ROOT before dynamic import will resolve it.
    """
    if not spec:
        return False
    if spec.startswith(("@", "./", "../", "/", "node:")):
        return False
    return "/" in spec


def _vitest_file_contains(
    c: Contract, *, when: dict | None = None, then: dict | None = None,
) -> str:
    """Generate file_contains test body for vitest.

    ``when``/``then`` default to ``c.when``/``c.then`` (the ``file_contains``
    contract shape: selectors in ``when``, outcome in ``then``). A ``command``
    contract with a post-command content assertion passes ``when=c.then,
    then=c.then`` — its file/parse/path/pattern AND its exists/matches all live
    in ``then`` (``when`` holds the ``cmd``). Defaulting keeps existing
    ``file_contains`` output byte-identical."""
    when = c.when if when is None else when
    then = c.then if then is None else then
    file_path = when.get("file", "")
    parse_mode = when.get("parse", "text")
    # Every readFileSync is anchored to PROJECT_ROOT so Tier 1 contracts are
    # CWD-invariant — the gate may run vitest from project root or from a
    # workspace dir like apps/api/ depending on the manifest.
    read_expr = (
        f"readFileSync(path.join(PROJECT_ROOT, '{_escape_js(file_path)}'), 'utf-8')"
    )

    if parse_mode == "json":
        dot_path = when.get("path", "")
        exists = then.get("exists", True)
        lines = [
            f"    const raw = {read_expr};",
            f"    const data = JSON.parse(raw);",
        ]
        if exists:
            lines.append(f"    expect(data).toHaveProperty('{_escape_js(dot_path)}');")
        else:
            lines.append(f"    expect(data).not.toHaveProperty('{_escape_js(dot_path)}');")
        return "\n".join(lines)

    if parse_mode == "yaml":
        dot_path = when.get("path", "")
        exists = then.get("exists", True)
        # YAML files can't be parsed with JSON.parse.  Use a regex to check
        # for top-level keys (dot_path depth 1) or fall back to substring
        # matching for deeper paths.  This avoids requiring js-yaml at runtime.
        key = dot_path.split(".")[0] if dot_path else ""
        escaped_key = _escape_regex_js(key)
        lines = [
            f"    const content = {read_expr};",
        ]
        if exists:
            lines.append(f"    expect(content).toMatch(/^{escaped_key}\\s*:/m);")
        else:
            lines.append(f"    expect(content).not.toMatch(/^{escaped_key}\\s*:/m);")
        return "\n".join(lines)

    # text mode — pattern match
    pattern = when.get("pattern", "")
    _validate_regex(pattern)
    escaped = _escape_regex_js(pattern)
    matches = then.get("matches", True)
    lines = [
        f"    const content = {read_expr};",
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
        "from pathlib import Path\n\n"
        "# _PROJECT_ROOT is anchored to this test file's own location so that\n"
        "# tier-1 contracts run identically regardless of the CWD pytest is\n"
        "# invoked from. Tests live at .dark-factory/scenarios/<spec>/, so\n"
        "# parents[3] resolves to the project root.\n"
        "_PROJECT_ROOT = Path(__file__).resolve().parents[3]\n\n\n"
        + "\n\n".join(test_funcs)
    )

    return [("00_tier1_contracts.py", content)]


def _pytest_body(c: Contract) -> str:
    """Generate the test body for a single contract (pytest)."""
    indent = "    "

    if c.type == "file_exists":
        check_path = c.when.get("check", "")
        exists = c.then.get("exists", True)
        if exists:
            return f"{indent}assert (_PROJECT_ROOT / {repr(check_path)}).exists()"
        return f"{indent}assert not (_PROJECT_ROOT / {repr(check_path)}).exists()"

    if c.type == "file_contains":
        return _pytest_file_contains(c, indent)

    if c.type == "command":
        cmd = c.when.get("cmd", "")
        exit_code = c.then.get("exit_code", 0)
        # timeout= bounds a hung command (parity with the vitest execSync
        # bound). subprocess.run raises TimeoutExpired on a hang → the test
        # fails fast instead of stalling the gate to the wall.
        lines = [
            f"{indent}result = subprocess.run({repr(cmd)}, shell=True, "
            f"capture_output=True, cwd=str(_PROJECT_ROOT), "
            f"timeout={_TIER1_EXEC_TIMEOUT_S})",
            f"{indent}assert result.returncode == {int(exit_code)}",
        ]
        # Optional post-command SIDE-EFFECT assertion (e.g. `pnpm format`
        # rewrote a file). Selectors+outcome live in `then`; `when` holds cmd.
        if c.then.get("file"):
            lines.append(_pytest_file_contains(c, indent, when=c.then, then=c.then))
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


def _pytest_file_contains(
    c: Contract, indent: str, *, when: dict | None = None,
    then: dict | None = None,
) -> str:
    """Generate file_contains test body for pytest.

    ``when``/``then`` default to ``c.when``/``c.then``; a ``command`` contract
    with a post-command content assertion passes ``when=c.then, then=c.then``
    (selectors + outcome both in ``then``). Defaulting keeps existing
    ``file_contains`` output byte-identical."""
    when = c.when if when is None else when
    then = c.then if then is None else then
    file_path = when.get("file", "")
    parse_mode = when.get("parse", "text")
    # Every Path() read is anchored to _PROJECT_ROOT so Tier 1 contracts are
    # CWD-invariant regardless of where pytest is invoked from.
    path_expr = f"(_PROJECT_ROOT / {repr(file_path)})"

    if parse_mode == "json":
        dot_path = when.get("path", "")
        exists = then.get("exists", True)
        parts = dot_path.split(".")
        accessor = "data"
        lines = [
            f"{indent}data = json.loads({path_expr}.read_text())",
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
        dot_path = when.get("path", "")
        exists = then.get("exists", True)
        parts = dot_path.split(".")
        accessor = "data"
        lines = [
            f"{indent}import yaml as _yaml",
            f"{indent}data = _yaml.safe_load({path_expr}.read_text())",
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
    pattern = when.get("pattern", "")
    _validate_regex(pattern)
    matches = then.get("matches", True)
    lines = [
        f"{indent}content = {path_expr}.read_text()",
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
