"""Tests for project structure detection."""

from __future__ import annotations

from pathlib import Path

from darkfactory.setup.detect import detect_project


def _make_project(tmp_path: Path, files: dict[str, str]) -> Path:
    """Create a minimal project with the given files."""
    for name, content in files.items():
        p = tmp_path / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
    return tmp_path


class TestLanguageDetection:
    def test_python_pyproject(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "pyproject.toml": "[project]\nname = 'myapp'\n",
            "src/myapp/__init__.py": "",
            "src/myapp/main.py": "print('hello')",
            "tests/test_main.py": "def test(): pass",
        })
        result = detect_project(tmp_path)
        assert result.language == "python"

    def test_python_setup_py(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "setup.py": "from setuptools import setup; setup()",
            "myapp/__init__.py": "",
            "myapp/core.py": "x = 1",
        })
        result = detect_project(tmp_path)
        assert result.language == "python"

    def test_node_package_json(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"name": "myapp"}',
            "src/index.ts": "export default {}",
            "src/app.ts": "const x = 1",
        })
        result = detect_project(tmp_path)
        assert result.language == "node"

    def test_go_mod(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "go.mod": "module example.com/myapp\ngo 1.21\n",
            "main.go": "package main",
            "handler.go": "package main",
        })
        result = detect_project(tmp_path)
        assert result.language == "go"

    def test_rust_cargo(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "Cargo.toml": "[package]\nname = 'myapp'\n",
            "src/main.rs": "fn main() {}",
        })
        result = detect_project(tmp_path)
        assert result.language == "rust"

    def test_no_markers(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {"README.md": "# Hello"})
        result = detect_project(tmp_path)
        assert result.language == "unknown"

    def test_multiple_markers_prefers_more_files(self, tmp_path: Path) -> None:
        """When both pyproject.toml and package.json exist, prefer the one with more files."""
        _make_project(tmp_path, {
            "pyproject.toml": "[project]\nname = 'myapp'\n",
            "package.json": '{"name": "myapp"}',
            "app.py": "x = 1",
            "lib.py": "y = 2",
            "util.py": "z = 3",
            "src/index.js": "module.exports = {}",
        })
        result = detect_project(tmp_path)
        assert result.language == "python"


class TestSourceDirDetection:
    def test_python_src_layout(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "pyproject.toml": "",
            "src/myapp/__init__.py": "",
            "src/myapp/main.py": "",
        })
        result = detect_project(tmp_path)
        assert result.source_dirs == ["src/myapp"]

    def test_python_flat_layout(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "pyproject.toml": "",
            "myapp/__init__.py": "",
            "myapp/core.py": "",
        })
        result = detect_project(tmp_path)
        assert result.source_dirs == ["myapp"]

    def test_node_src_dir(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"name": "x"}',
            "src/index.ts": "",
        })
        result = detect_project(tmp_path)
        assert result.source_dirs == ["src"]

    def test_node_monorepo_source_dirs(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"name": "root"}',
            "pnpm-workspace.yaml": "packages:\n  - apps/*\n  - packages/*\n",
            "apps/api/src/index.ts": "",
            "apps/web/src/index.ts": "",
            "packages/shared/src/index.ts": "",
        })
        result = detect_project(tmp_path)
        assert sorted(result.source_dirs) == [
            "apps/api/src", "apps/web/src", "packages/shared/src",
        ]

    def test_node_monorepo_with_root_src_prefers_root(self, tmp_path: Path) -> None:
        """If root src/ exists, it wins over monorepo detection."""
        _make_project(tmp_path, {
            "package.json": '{"name": "root"}',
            "pnpm-workspace.yaml": "",
            "src/index.ts": "",
            "apps/api/src/index.ts": "",
        })
        result = detect_project(tmp_path)
        assert result.source_dirs == ["src"]

    def test_go_root(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {"go.mod": "", "main.go": ""})
        result = detect_project(tmp_path)
        assert result.source_dirs == ["."]


class TestTestDirDetection:
    def test_python_tests_dir(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "pyproject.toml": "",
            "tests/test_foo.py": "",
            "src/app/__init__.py": "",
        })
        result = detect_project(tmp_path)
        assert result.test_dir == "tests"

    def test_node_test_dir(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"name": "x"}',
            "test/app.test.ts": "",
            "src/index.ts": "",
        })
        result = detect_project(tmp_path)
        assert result.test_dir == "test"

    def test_node_dunder_tests_dir(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"name": "x"}',
            "__tests__/app.test.js": "",
            "src/index.js": "",
        })
        result = detect_project(tmp_path)
        assert result.test_dir == "__tests__"


class TestFrameworkDetection:
    def test_fastapi(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "pyproject.toml": '[project]\ndependencies = ["fastapi"]\n',
            "src/app/__init__.py": "",
        })
        result = detect_project(tmp_path)
        assert result.framework == "FastAPI"

    def test_express(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"dependencies": {"express": "^4.0"}}',
            "src/index.js": "",
        })
        result = detect_project(tmp_path)
        assert result.framework == "Express"

    def test_no_false_positive_on_description(self, tmp_path: Path) -> None:
        """A package with 'next' in description should not detect as Next.js."""
        _make_project(tmp_path, {
            "package.json": '{"description": "the next big thing", "dependencies": {}}',
            "src/index.js": "",
        })
        result = detect_project(tmp_path)
        assert result.framework == ""

    def test_no_framework(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "pyproject.toml": "[project]\nname = 'mylib'\n",
            "src/mylib/__init__.py": "",
        })
        result = detect_project(tmp_path)
        assert result.framework == ""


class TestCommandDetection:
    def test_python_ruff(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "pyproject.toml": "[tool.ruff]\nline-length = 100\n",
            "src/app/__init__.py": "",
        })
        result = detect_project(tmp_path)
        assert result.lint_cmd == "ruff check ."

    def test_node_scripts(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"scripts": {"build": "tsc", "lint": "eslint .", "test": "jest"}}',
            "src/index.ts": "",
        })
        result = detect_project(tmp_path)
        assert result.build_cmd == "npm run build"
        assert result.lint_cmd == "npm run lint"
        assert result.test_cmd == "npm test"

    def test_go_commands(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {"go.mod": "", "main.go": ""})
        result = detect_project(tmp_path)
        assert result.build_cmd == "go build ./..."
        assert result.test_cmd == "go test ./..."

    def test_rust_commands(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {"Cargo.toml": "", "src/main.rs": ""})
        result = detect_project(tmp_path)
        assert result.build_cmd == "cargo build"
        assert result.test_cmd == "cargo test"

    def test_python_build_cmd_carries_break_system_packages(self, tmp_path: Path) -> None:
        # The dark-factory-python image's base marks system python PEP 668
        # externally-managed, so the generated build_cmd MUST carry
        # --break-system-packages or `pip install` is refused in the gate
        # container (the T2.0 escalation root cause). Pins the pyproject/editable
        # shape (the parse-amount-py target); the requirements.txt and
        # requirements/base.txt branches carry the same suffix (detect.py).
        _make_project(tmp_path, {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1"\n',
            "src/app/__init__.py": "",
        })
        assert detect_project(tmp_path).build_cmd == "pip install -e . -q --break-system-packages"

    def test_python_build_cmd_installs_test_extras_group(self, tmp_path: Path) -> None:
        # Tier-B: a bare `pip install -e .` installs MAIN deps only, so test deps
        # in an extras group (httpx — FastAPI TestClient's transport) never
        # install and tests error at collection (notes-crud: 0%). The build_cmd
        # must install the detected test-extras group; the target is quoted so
        # bash doesn't glob `[dev]`.
        _make_project(tmp_path, {
            "pyproject.toml": (
                '[project]\nname = "x"\nversion = "0.1"\n'
                "[project.optional-dependencies]\n"
                'dev = ["pytest>=7.0", "httpx>=0.24"]\n'
            ),
            "src/app/__init__.py": "",
        })
        assert detect_project(tmp_path).build_cmd == (
            "pip install -e '.[dev]' -q --break-system-packages"
        )

    def test_python_build_cmd_extras_name_agnostic_fallback(self, tmp_path: Path) -> None:
        # A non-conventional group name (not dev/test/testing) that declares a
        # known test dep is still detected via the fallback.
        _make_project(tmp_path, {
            "pyproject.toml": (
                '[project]\nname = "x"\nversion = "0.1"\n'
                "[project.optional-dependencies]\n"
                'qa = ["httpx>=0.24"]\n'
            ),
            "src/app/__init__.py": "",
        })
        assert detect_project(tmp_path).build_cmd == (
            "pip install -e '.[qa]' -q --break-system-packages"
        )

    def test_python_build_cmd_rejects_unsafe_extras_name(self, tmp_path: Path) -> None:
        # An extras GROUP NAME with shell-unsafe chars (TOML permits quoted keys
        # with spaces/quotes/metachars) must NOT be spliced into the shell
        # `pip install -e '.[...]'` — degrade to bare (such a name is also not a
        # valid installable PEP 508 extra). Guards against a broken/injectable
        # build_cmd derived from an arbitrary user project's pyproject.
        for bad_key in (
            '"my tests"',   # space — invalid/unsafe extra name
            '"dev\\n"',     # trailing newline — must use fullmatch, not `$`+match
            '"qa-"',        # trailing hyphen — not a valid PEP 508 extra ending
            '"d;rm"',       # shell metachar
        ):
            _make_project(tmp_path, {
                "pyproject.toml": (
                    '[project]\nname = "x"\nversion = "0.1"\n'
                    "[project.optional-dependencies]\n"
                    f'{bad_key} = ["httpx"]\n'
                ),
                "src/app/__init__.py": "",
            })
            assert detect_project(tmp_path).build_cmd == (
                "pip install -e . -q --break-system-packages"
            ), bad_key

    def test_python_build_cmd_malformed_pyproject_degrades_to_bare(self, tmp_path: Path) -> None:
        # A parseable-but-malformed pyproject (project not a table; deps not a
        # list) must DEGRADE to the bare install, never crash detect_project.
        for pyproject in (
            'project = "x"\n',                                   # [project] is a string
            '[project]\nname = "x"\n'                            # optional-dependencies
            "[project.optional-dependencies]\nqa = 1\n",         # group deps not a list
            '[project]\nname = "x"\noptional-dependencies = 5\n',  # extras not a table
            '[project]\nname = "x"\n'                            # PRIORITY group (dev)
            "[project.optional-dependencies]\ndev = 1\n",        # with non-list deps
            '[project]\nname = "x"\n'                            # priority group as a
            "[project.optional-dependencies]\ndev = \"httpx\"\n",  # bare string, not list
        ):
            _make_project(tmp_path, {"pyproject.toml": pyproject, "src/app/__init__.py": ""})
            assert detect_project(tmp_path).build_cmd == (
                "pip install -e . -q --break-system-packages"
            ), pyproject

    def test_python_mypy_disables_import_untyped(self, tmp_path: Path) -> None:
        # The image ships mypy but no types-* stubs; a third-party import without
        # stubs (e.g. setuptools in a generated setup.py) must NOT hard-fail the
        # Type Check gate. --disable-error-code=import-untyped suppresses ONLY
        # that class; [import-not-found] + in-project type errors stay enforced.
        _make_project(tmp_path, {
            "pyproject.toml": '[project]\nname = "x"\nversion = "0.1"\n'
                              "[project.optional-dependencies]\ndev = [\"mypy\"]\n",
            "src/app/__init__.py": "",
        })
        assert detect_project(tmp_path).typecheck_cmd == (
            "mypy . --disable-error-code=import-untyped"
        )


# --- Phase 3 detection expansion ---


class TestPackageManagerDetection:
    def test_detects_pnpm_package_manager(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{}',
            "pnpm-lock.yaml": "",
            "src/index.ts": "",
        })
        result = detect_project(tmp_path)
        assert result.package_manager == "pnpm"

    def test_detects_npm_package_manager(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{}',
            "package-lock.json": '{}',
            "src/index.js": "",
        })
        result = detect_project(tmp_path)
        assert result.package_manager == "npm"

    def test_detects_yarn_package_manager(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{}',
            "yarn.lock": "",
            "src/index.js": "",
        })
        result = detect_project(tmp_path)
        assert result.package_manager == "yarn"

    def test_detects_pip_package_manager(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "pyproject.toml": "[project]\nname='x'\n",
            "src/x/__init__.py": "",
        })
        result = detect_project(tmp_path)
        assert result.package_manager == "pip"


class TestServerCmdDetection:
    def test_detects_server_cmd_from_dev_script(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"scripts": {"dev": "next dev", "build": "next build", "test": "jest"}}',
            "src/index.ts": "",
        })
        result = detect_project(tmp_path)
        assert result.server_cmd == "npm run dev"

    def test_detects_server_cmd_pnpm(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"scripts": {"dev": "next dev", "build": "next build"}}',
            "pnpm-lock.yaml": "",
            "src/index.ts": "",
        })
        result = detect_project(tmp_path)
        assert result.server_cmd == "pnpm run dev"


class TestSetupCmdDetection:
    def test_detects_prisma_setup(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"devDependencies": {"prisma": "^5.0"}, "scripts": {"build": "tsc"}}',
            "pnpm-lock.yaml": "",
            "src/index.ts": "",
        })
        result = detect_project(tmp_path)
        assert result.setup_cmd == "pnpm exec prisma db push"

    def test_detects_drizzle_setup(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "package.json": '{"devDependencies": {"drizzle-kit": "^0.20"}, "scripts": {"build": "tsc"}}',
            "src/index.ts": "",
        })
        result = detect_project(tmp_path)
        assert result.setup_cmd == "npx drizzle-kit push"

    def test_detects_alembic_setup(self, tmp_path: Path) -> None:
        _make_project(tmp_path, {
            "pyproject.toml": "[project]\nname='x'\ndependencies=['alembic']\n",
            "alembic.ini": "[alembic]\nscript_location = alembic\n",
            "src/x/__init__.py": "",
        })
        result = detect_project(tmp_path)
        assert result.setup_cmd == "alembic upgrade head"
