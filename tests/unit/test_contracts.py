"""Tests for darkfactory.spec.contracts — parser and deterministic compiler.

The Tier 1 contract compiler must emit CWD-invariant code: every filesystem
read and subprocess invocation must be anchored to a project-root constant
derived from the test file's own location. The workspace-wrapper gate may run
vitest from a workspace dir (e.g. apps/api/) rather than the project root, and
pytest may likewise be invoked from anywhere; tier 1 contracts are exempt
from the test-engineer authoring-contract linter (Check 17) because they
are compiler output rather than agent output, so CWD-safety is the compiler's
responsibility.

These tests lock in the PROJECT_ROOT / _PROJECT_ROOT anchoring and the
cwd=/cwd: options on subprocess/execSync invocations.
"""

from __future__ import annotations

import pytest

from darkfactory.spec.contracts import (
    Contract,
    compile_contracts,
    parse_contracts,
)


# ---------------------------------------------------------------------------
# Parser tests (lightweight — focus is the compiler; parser is already
# exercised indirectly by integration suites)
# ---------------------------------------------------------------------------


def test_parse_contracts_no_ac_section_returns_empty():
    spec = "# Title\n\nSome body.\n"
    result = parse_contracts(spec)
    assert result.contracts == []
    assert result.errors == []


def test_parse_contracts_happy_path_single_contract():
    spec = """# Feature

## Acceptance Criteria

```yaml
contracts:
  - id: TC-1
    fr: FR-1
    type: file_exists
    given: pnpm-workspace.yaml exists at project root
    when:
      check: pnpm-workspace.yaml
    then:
      exists: true
```
"""
    result = parse_contracts(spec)
    assert result.errors == []
    assert len(result.contracts) == 1
    c = result.contracts[0]
    assert c.id == "TC-1"
    assert c.fr == "FR-1"
    assert c.type == "file_exists"
    assert c.when == {"check": "pnpm-workspace.yaml"}
    assert c.then == {"exists": True}


def test_parse_contracts_rejects_unknown_type():
    spec = """## Acceptance Criteria

```yaml
contracts:
  - id: TC-1
    fr: FR-1
    type: bogus_type
    given: nothing
    when: {}
    then: {}
```
"""
    result = parse_contracts(spec)
    assert result.contracts == []
    assert any("unknown type" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Helpers for compiler tests — build Contract objects directly
# ---------------------------------------------------------------------------


def _file_exists(cid: str = "TC-1", check: str = "pnpm-workspace.yaml", exists: bool = True) -> Contract:
    return Contract(
        id=cid,
        fr="FR-1",
        type="file_exists",
        given=f"{check} exists" if exists else f"{check} absent",
        when={"check": check},
        then={"exists": exists},
    )


def _command(cid: str = "TC-2", cmd: str = "pnpm install", exit_code: int = 0) -> Contract:
    return Contract(
        id=cid,
        fr="FR-2",
        type="command",
        given=f"running `{cmd}`",
        when={"cmd": cmd},
        then={"exit_code": exit_code},
    )


def _file_contains_json(
    cid: str = "TC-3",
    file: str = "package.json",
    dot_path: str = "scripts.test",
    exists: bool = True,
) -> Contract:
    return Contract(
        id=cid,
        fr="FR-3",
        type="file_contains",
        given=f"{file} has {dot_path}",
        when={"file": file, "parse": "json", "path": dot_path},
        then={"exists": exists},
    )


def _file_contains_yaml(
    cid: str = "TC-4",
    file: str = "pnpm-workspace.yaml",
    dot_path: str = "packages",
    exists: bool = True,
) -> Contract:
    return Contract(
        id=cid,
        fr="FR-4",
        type="file_contains",
        given=f"{file} has {dot_path}",
        when={"file": file, "parse": "yaml", "path": dot_path},
        then={"exists": exists},
    )


def _file_contains_text(
    cid: str = "TC-5",
    file: str = ".github/workflows/ci.yml",
    pattern: str = "name: CI",
    matches: bool = True,
) -> Contract:
    return Contract(
        id=cid,
        fr="FR-5",
        type="file_contains",
        given=f"{file} matches /{pattern}/",
        when={"file": file, "parse": "text", "pattern": pattern},
        then={"matches": matches},
    )


# ---------------------------------------------------------------------------
# Vitest compiler — CWD-invariance guarantees
# ---------------------------------------------------------------------------


class TestVitestCwdInvariance:
    """Every path and subprocess invocation must be anchored to PROJECT_ROOT."""

    def test_header_declares_project_root_anchored_to_import_meta_dirname(self):
        files = compile_contracts([_file_exists()], language="node")
        assert len(files) == 1
        filename, content = files[0]
        assert filename == "00-tier1-contracts.test.ts"

        # Header must import path and declare PROJECT_ROOT.
        assert "import path from 'node:path';" in content
        assert (
            "const PROJECT_ROOT = path.resolve(import.meta.dirname, '../../..');"
            in content
        )

    def test_file_exists_uses_path_join_project_root(self):
        files = compile_contracts([_file_exists(check="apps/api/package.json")], language="node")
        content = files[0][1]

        assert "path.join(PROJECT_ROOT, 'apps/api/package.json')" in content
        assert "existsSync(path.join(PROJECT_ROOT," in content
        # The CWD-brittle form must not appear.
        assert "existsSync('apps/api/package.json')" not in content

    def test_file_exists_negated_uses_path_join(self):
        files = compile_contracts(
            [_file_exists(check="dist/index.js", exists=False)],
            language="node",
        )
        content = files[0][1]

        assert "!existsSync(path.join(PROJECT_ROOT, 'dist/index.js'))" in content

    def test_command_success_passes_cwd_project_root(self):
        files = compile_contracts([_command(cmd="pnpm typecheck")], language="node")
        content = files[0][1]

        assert (
            "execSync('pnpm typecheck', { stdio: 'pipe', cwd: PROJECT_ROOT, "
            "timeout: 120000 });" in content
        )
        # The CWD-brittle form must not appear.
        assert "execSync('pnpm typecheck', { stdio: 'pipe' })" not in content

    def test_command_execsync_is_timeout_bounded(self):
        """Subprocess-stall sprint: every tier-1 command contract's execSync
        must carry a `timeout:` so a hung tool cannot block the vitest event
        loop to the gate wall. Both the exit-0 and the non-zero-exit render
        paths must be bounded."""
        ok = compile_contracts([_command(cmd="pnpm lint")], language="node")[0][1]
        nonzero = compile_contracts(
            [_command(cmd="pnpm run missing", exit_code=1)], language="node",
        )[0][1]
        assert "timeout: 120000" in ok
        assert "timeout: 120000" in nonzero

    def test_command_nonzero_exit_passes_cwd_project_root(self):
        files = compile_contracts(
            [_command(cmd="pnpm run missing", exit_code=1)],
            language="node",
        )
        content = files[0][1]

        assert "cwd: PROJECT_ROOT" in content
        assert "expect(exitCode).toBe(1);" in content

    def test_file_contains_json_uses_path_join(self):
        files = compile_contracts(
            [_file_contains_json(file="package.json", dot_path="scripts.test")],
            language="node",
        )
        content = files[0][1]

        assert (
            "readFileSync(path.join(PROJECT_ROOT, 'package.json'), 'utf-8')"
            in content
        )
        # CWD-brittle form must not appear.
        assert "readFileSync('package.json'" not in content
        # Assertion is preserved.
        assert "toHaveProperty('scripts.test')" in content

    def test_file_contains_yaml_uses_path_join(self):
        files = compile_contracts(
            [_file_contains_yaml(file="pnpm-workspace.yaml")],
            language="node",
        )
        content = files[0][1]

        assert (
            "readFileSync(path.join(PROJECT_ROOT, 'pnpm-workspace.yaml'), 'utf-8')"
            in content
        )
        assert "readFileSync('pnpm-workspace.yaml'" not in content

    def test_file_contains_text_uses_path_join(self):
        files = compile_contracts(
            [_file_contains_text(file=".github/workflows/ci.yml", pattern="name: CI")],
            language="node",
        )
        content = files[0][1]

        assert (
            "readFileSync(path.join(PROJECT_ROOT, '.github/workflows/ci.yml'), 'utf-8')"
            in content
        )

    def test_import_path_like_specifier_anchored_via_path_to_file_url(self):
        c = Contract(
            id="TC-IMPORT-1",
            fr="FR-1",
            type="import",
            given="shared package barrel exports parseAmount",
            when={
                "module": "packages/shared/src/index.ts",
                "symbols": ["parseAmount"],
            },
            then={},
        )
        files = compile_contracts([c], language="node")
        content = files[0][1]

        # Header must import pathToFileURL.
        assert "import { pathToFileURL } from 'node:url';" in content
        # Path-like specifier anchored via PROJECT_ROOT.
        assert (
            "await import(pathToFileURL(path.join(PROJECT_ROOT, "
            "'packages/shared/src/index.ts')).href)"
            in content
        )
        # CWD-brittle bare-path form must not appear.
        assert "await import('packages/shared/src/index.ts')" not in content
        # Symbol assertion preserved.
        assert "expect(mod).toHaveProperty('parseAmount');" in content

    def test_import_scoped_package_name_kept_verbatim(self):
        c = Contract(
            id="TC-IMPORT-2",
            fr="FR-1",
            type="import",
            given="scoped package import",
            when={"module": "@budget-brain/shared", "symbols": ["parseAmount"]},
            then={},
        )
        files = compile_contracts([c], language="node")
        content = files[0][1]

        assert "await import('@budget-brain/shared')" in content
        assert "pathToFileURL" not in content.split("describe(")[1]

    def test_import_bare_package_name_kept_verbatim(self):
        c = Contract(
            id="TC-IMPORT-3",
            fr="FR-1",
            type="import",
            given="bare package import",
            when={"module": "drizzle-orm", "symbols": ["eq"]},
            then={},
        )
        files = compile_contracts([c], language="node")
        content = files[0][1]

        assert "await import('drizzle-orm')" in content
        # No anchoring for bare package names.
        assert "PROJECT_ROOT, 'drizzle-orm'" not in content

    def test_import_relative_path_kept_verbatim(self):
        c = Contract(
            id="TC-IMPORT-4",
            fr="FR-1",
            type="import",
            given="relative import",
            when={"module": "./helper.js", "symbols": ["x"]},
            then={},
        )
        files = compile_contracts([c], language="node")
        content = files[0][1]

        assert "await import('./helper.js')" in content

    def test_import_node_builtin_kept_verbatim(self):
        c = Contract(
            id="TC-IMPORT-5",
            fr="FR-1",
            type="import",
            given="node builtin import",
            when={"module": "node:fs", "symbols": ["readFileSync"]},
            then={},
        )
        files = compile_contracts([c], language="node")
        content = files[0][1]

        assert "await import('node:fs')" in content

    def test_multiple_contracts_share_one_file_with_one_project_root_decl(self):
        files = compile_contracts(
            [
                _file_exists(cid="TC-1", check="pnpm-workspace.yaml"),
                _command(cid="TC-2", cmd="pnpm install"),
                _file_contains_json(cid="TC-3"),
            ],
            language="node",
        )

        assert len(files) == 1
        content = files[0][1]
        # PROJECT_ROOT declared exactly once.
        assert content.count("const PROJECT_ROOT = path.resolve") == 1
        # All three anchored references land.
        assert "path.join(PROJECT_ROOT, 'pnpm-workspace.yaml')" in content
        assert "cwd: PROJECT_ROOT" in content
        assert "path.join(PROJECT_ROOT, 'package.json')" in content


# ---------------------------------------------------------------------------
# Pytest compiler — CWD-invariance guarantees
# ---------------------------------------------------------------------------


class TestPytestCwdInvariance:
    """Every path and subprocess invocation must be anchored to _PROJECT_ROOT."""

    def test_header_declares_project_root_anchored_to_file_parents(self):
        files = compile_contracts([_file_exists()], language="python")
        assert len(files) == 1
        filename, content = files[0]
        assert filename == "00_tier1_contracts.py"

        assert "_PROJECT_ROOT = Path(__file__).resolve().parents[3]" in content

    def test_file_exists_uses_project_root_divide(self):
        files = compile_contracts(
            [_file_exists(check="pyproject.toml")],
            language="python",
        )
        content = files[0][1]

        assert "(_PROJECT_ROOT / 'pyproject.toml').exists()" in content
        # CWD-brittle form must not appear.
        assert "Path('pyproject.toml').exists()" not in content

    def test_file_exists_negated_uses_project_root_divide(self):
        files = compile_contracts(
            [_file_exists(check="dist/", exists=False)],
            language="python",
        )
        content = files[0][1]

        assert "not (_PROJECT_ROOT / 'dist/').exists()" in content

    def test_command_passes_cwd_project_root(self):
        files = compile_contracts(
            [_command(cmd="pytest --collect-only")],
            language="python",
        )
        content = files[0][1]

        assert "cwd=str(_PROJECT_ROOT)" in content
        assert "subprocess.run('pytest --collect-only'" in content
        # CWD-brittle form must not appear.
        assert "subprocess.run('pytest --collect-only', shell=True, capture_output=True)\n" not in content

    def test_file_contains_json_uses_project_root_divide(self):
        files = compile_contracts(
            [_file_contains_json(file="package.json", dot_path="name")],
            language="python",
        )
        content = files[0][1]

        assert "json.loads((_PROJECT_ROOT / 'package.json').read_text())" in content
        assert "Path('package.json').read_text()" not in content
        assert "assert 'name' in data" in content

    def test_file_contains_yaml_uses_project_root_divide(self):
        files = compile_contracts(
            [_file_contains_yaml(file="pnpm-workspace.yaml", dot_path="packages")],
            language="python",
        )
        content = files[0][1]

        assert (
            "_yaml.safe_load((_PROJECT_ROOT / 'pnpm-workspace.yaml').read_text())"
            in content
        )
        assert "Path('pnpm-workspace.yaml').read_text()" not in content

    def test_file_contains_text_uses_project_root_divide(self):
        files = compile_contracts(
            [_file_contains_text(file=".github/workflows/ci.yml", pattern="name: CI")],
            language="python",
        )
        content = files[0][1]

        assert (
            "content = (_PROJECT_ROOT / '.github/workflows/ci.yml').read_text()"
            in content
        )
        assert "Path('.github/workflows/ci.yml').read_text()" not in content


# ---------------------------------------------------------------------------
# General compiler invariants
# ---------------------------------------------------------------------------


class TestCompilerInvariants:
    def test_invariant_contracts_are_excluded(self):
        active = _file_exists(cid="TC-A")
        invariant = Contract(
            id="TC-B",
            fr="FR-2",
            type="file_exists",
            given="only checked in invariant mode",
            when={"check": "README.md"},
            then={"exists": True},
            invariant=True,
        )
        files = compile_contracts([active, invariant], language="node")
        content = files[0][1]
        assert "TC-A" in content
        assert "TC-B" not in content

    def test_empty_active_contracts_returns_empty_list(self):
        invariant = Contract(
            id="TC-ONLY-INVARIANT",
            fr="FR-1",
            type="file_exists",
            given="only invariant",
            when={"check": "README.md"},
            then={"exists": True},
            invariant=True,
        )
        assert compile_contracts([invariant], language="node") == []

    def test_language_defaults_to_vitest_for_unknown_language(self):
        files = compile_contracts([_file_exists()], language="ruby")
        assert len(files) == 1
        # Falls back to vitest — filename is .test.ts, not .py.
        assert files[0][0].endswith(".test.ts")
        assert "PROJECT_ROOT" in files[0][1]

    def test_determinism_same_input_same_output(self):
        contracts = [_file_exists(), _command(), _file_contains_json()]
        out_a = compile_contracts(contracts, language="node")
        out_b = compile_contracts(contracts, language="node")
        assert out_a == out_b

        out_py_a = compile_contracts(contracts, language="python")
        out_py_b = compile_contracts(contracts, language="python")
        assert out_py_a == out_py_b
