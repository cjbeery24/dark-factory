"""Phase 3.4 — lockfile-based environment hints."""

import json
from pathlib import Path

from darkfactory.architecture.hints import (
    EnvironmentHints,
    detect_and_write,
    detect_hints,
    write_hints,
)


def test_empty_project_returns_empty_hints(tmp_path):
    h = detect_hints(tmp_path)
    assert h.language == ""
    assert h.package_manager == ""
    assert h.lockfiles == []
    assert h.workspace_layout == "single"


def test_pnpm_lockfile_wins_over_yarn(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "yarn.lock").write_text("")
    (tmp_path / "package-lock.json").write_text("{}")
    h = detect_hints(tmp_path)
    assert h.language == "node"
    assert h.package_manager == "pnpm"
    assert h.lockfiles == ["pnpm-lock.yaml"]


def test_yarn_lockfile(tmp_path):
    (tmp_path / "yarn.lock").write_text("")
    h = detect_hints(tmp_path)
    assert h.package_manager == "yarn"
    assert h.language == "node"


def test_npm_lockfile(tmp_path):
    (tmp_path / "package-lock.json").write_text("{}")
    h = detect_hints(tmp_path)
    assert h.package_manager == "npm"


def test_python_uv_beats_poetry(tmp_path):
    (tmp_path / "uv.lock").write_text("")
    (tmp_path / "poetry.lock").write_text("")
    h = detect_hints(tmp_path)
    assert h.language == "python"
    assert h.package_manager == "uv"


def test_python_pip_requirements(tmp_path):
    (tmp_path / "requirements.txt").write_text("")
    h = detect_hints(tmp_path)
    assert h.language == "python"
    assert h.package_manager == "pip"


def test_rust_and_go(tmp_path):
    (tmp_path / "Cargo.lock").write_text("")
    h = detect_hints(tmp_path)
    assert h.language == "rust"
    assert h.package_manager == "cargo"


def test_node_lockfile_blocks_python_detection(tmp_path):
    """A polyglot repo with both Node and Python should report whichever
    lock the search order prefers (Node first). Don't claim both."""
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "requirements.txt").write_text("")
    h = detect_hints(tmp_path)
    assert h.language == "node"
    assert h.package_manager == "pnpm"


def test_monorepo_via_pnpm_workspace(tmp_path):
    (tmp_path / "pnpm-workspace.yaml").write_text("packages:\n  - 'apps/*'\n")
    h = detect_hints(tmp_path)
    assert h.workspace_layout == "monorepo"


def test_monorepo_via_apps_dir(tmp_path):
    (tmp_path / "apps").mkdir()
    h = detect_hints(tmp_path)
    assert h.workspace_layout == "monorepo"


def test_monorepo_via_package_json_workspaces(tmp_path):
    (tmp_path / "package.json").write_text('{"workspaces": ["apps/*"]}')
    h = detect_hints(tmp_path)
    assert h.workspace_layout == "monorepo"


def test_corrupt_package_json_falls_back_to_single(tmp_path):
    (tmp_path / "package.json").write_text("{not json")
    h = detect_hints(tmp_path)
    assert h.workspace_layout == "single"


def test_write_hints_round_trips(tmp_path):
    h = EnvironmentHints(
        package_manager="pnpm", language="node",
        lockfiles=["pnpm-lock.yaml"], workspace_layout="monorepo",
    )
    out = write_hints(tmp_path, h)
    assert out == tmp_path / ".dark-factory" / "architecture" / "hints.json"
    data = json.loads(out.read_text())
    assert data == {
        "package_manager": "pnpm",
        "language": "node",
        "lockfiles": ["pnpm-lock.yaml"],
        "workspace_layout": "monorepo",
    }


def test_cargo_toml_without_lock_detects_rust(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname = \"x\"\n")
    h = detect_hints(tmp_path)
    assert h.language == "rust"
    assert h.package_manager == "cargo"


def test_go_mod_without_sum_detects_go(tmp_path):
    (tmp_path / "go.mod").write_text("module x\ngo 1.21\n")
    h = detect_hints(tmp_path)
    assert h.language == "go"
    assert h.package_manager == "go-modules"


def test_gemfile_without_lock_detects_ruby(tmp_path):
    (tmp_path / "Gemfile").write_text("source 'https://rubygems.org'\n")
    h = detect_hints(tmp_path)
    assert h.language == "ruby"
    assert h.package_manager == "bundler"


def test_pyproject_with_uv_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = \"x\"\n[tool.uv]\nfoo = \"bar\"\n"
    )
    h = detect_hints(tmp_path)
    assert h.language == "python"
    assert h.package_manager == "uv"


def test_pyproject_with_poetry_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = \"x\"\n")
    h = detect_hints(tmp_path)
    assert h.language == "python"
    assert h.package_manager == "poetry"


def test_pyproject_without_known_table_falls_back_to_pip(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = \"x\"\n")
    h = detect_hints(tmp_path)
    assert h.language == "python"
    assert h.package_manager == "pip"


def test_uv_lock_beats_pyproject_introspection(tmp_path):
    (tmp_path / "uv.lock").write_text("")
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\n")
    h = detect_hints(tmp_path)
    assert h.package_manager == "uv"  # lockfile wins


def test_detect_and_write(tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "apps").mkdir()
    h = detect_and_write(tmp_path)
    assert h.language == "node"
    assert h.workspace_layout == "monorepo"
    out = tmp_path / ".dark-factory" / "architecture" / "hints.json"
    assert out.exists()
