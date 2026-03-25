"""Project structure detection — language, source dirs, test dirs, framework.

Scans a project root for language-specific signals and infers configuration
defaults. Used by `dark-factory init` to generate a working config without
manual input.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# Directories to skip when counting files (large or irrelevant).
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "__pycache__", "target", "vendor",
    ".venv", "venv", ".tox", ".mypy_cache", ".ruff_cache",
    "dist", "build", ".next", ".dark-factory",
})


@dataclass
class DetectionResult:
    """Detected project characteristics."""

    language: str = "unknown"
    source_dirs: list[str] = field(default_factory=list)
    test_dir: str = ""
    framework: str = ""
    build_cmd: str = ""
    lint_cmd: str = ""
    typecheck_cmd: str = ""
    test_cmd: str = ""
    needs_playwright: bool = False
    playwright_reason: str = ""


# Detection heuristics, ordered by priority (most specific first).

_LANGUAGE_SIGNALS: list[tuple[str, str, list[str]]] = [
    # (filename_or_glob, language, extensions_to_count)
    ("Cargo.toml", "rust", [".rs"]),
    ("go.mod", "go", [".go"]),
    ("pyproject.toml", "python", [".py"]),
    ("setup.py", "python", [".py"]),
    ("setup.cfg", "python", [".py"]),
    ("package.json", "node", [".ts", ".tsx", ".js", ".jsx"]),
]


def _count_extensions(project_dir: Path) -> dict[str, int]:
    """Walk the project tree once and count files by extension, skipping junk dirs."""
    counts: dict[str, int] = {}
    for dirpath, dirnames, filenames in os.walk(project_dir):
        # Prune junk directories in-place so os.walk skips them.
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for f in filenames:
            ext = os.path.splitext(f)[1]
            if ext:
                counts[ext] = counts.get(ext, 0) + 1
    return counts


# Packages that are direct evidence the project uses Playwright for browser testing.
_PLAYWRIGHT_DIRECT_DEPS: frozenset[str] = frozenset({
    "@playwright/test", "playwright",
    "playwright-chromium", "playwright-firefox", "playwright-webkit",
})

# UI framework deps are a weaker advisory signal — many such projects use jsdom/jest,
# not a real browser.  Only used as a fallback when no direct Playwright dep is found.
_BROWSER_UI_DEPS: frozenset[str] = frozenset({
    "next", "nuxt", "gatsby", "svelte",
    "@angular/core", "@remix-run/react", "@vue/cli-service",
})


def _detect_playwright_signals(project_dir: Path, result: DetectionResult) -> None:
    """Set result.needs_playwright if any browser-test signal is found.

    Signal priority (highest to lowest):
      1. Explicit playwright package in dependencies (direct, unambiguous).
      2. "playwright" string in Python requirements / pyproject.toml.
      3. .journey.md files in .dark-factory/ (framework-defined journey tests).
      4. UI-framework deps that strongly imply browser rendering (advisory only).
    """
    if result.language == "node":
        pkg_json = project_dir / "package.json"
        if pkg_json.exists():
            try:
                pkg = json.loads(pkg_json.read_text())
                all_deps = (
                    set(pkg.get("dependencies", {}).keys())
                    | set(pkg.get("devDependencies", {}).keys())
                )
                # Highest-confidence signal: Playwright itself is a dependency.
                if all_deps & _PLAYWRIGHT_DIRECT_DEPS:
                    result.needs_playwright = True
                    result.playwright_reason = "direct playwright dependency"
                    return
                # Lower-confidence signal: project uses a UI framework that
                # *may* require browser testing — flag advisory, stop here.
                if all_deps & _BROWSER_UI_DEPS:
                    result.needs_playwright = True
                    result.playwright_reason = "UI framework detected"
                    return
            except (ValueError, OSError):
                pass

    if result.language == "python":
        for req_file in ("requirements.txt", "requirements/base.txt", "requirements/dev.txt"):
            req_path = project_dir / req_file
            if req_path.exists():
                try:
                    if "playwright" in req_path.read_text().lower():
                        result.needs_playwright = True
                        result.playwright_reason = "direct playwright dependency"
                        return
                except OSError:
                    pass
        pyproject = project_dir / "pyproject.toml"
        if pyproject.exists():
            try:
                if "playwright" in pyproject.read_text().lower():
                    result.needs_playwright = True
                    result.playwright_reason = "direct playwright dependency"
                    return
            except OSError:
                pass

    # Language-agnostic: .journey.md files are an explicit framework-level signal.
    df_dir = project_dir / ".dark-factory"
    if df_dir.is_dir() and any(df_dir.rglob("*.journey.md")):
        result.needs_playwright = True
        result.playwright_reason = "existing journey scenario files"


def detect_project(project_dir: Path) -> DetectionResult:
    """Detect language, source dirs, test dir, and framework from project structure."""
    result = DetectionResult()

    # Walk the tree once to count extensions.
    ext_counts = _count_extensions(project_dir)

    # Phase 1: Identify language from marker files.
    candidates: list[tuple[str, int]] = []
    for marker_file, language, extensions in _LANGUAGE_SIGNALS:
        if (project_dir / marker_file).exists():
            count = sum(ext_counts.get(ext, 0) for ext in extensions)
            candidates.append((language, count))

    if not candidates:
        log.info("no language markers found in %s", project_dir)
        return result

    # When multiple signals exist, prefer the one with the most files.
    candidates.sort(key=lambda c: c[1], reverse=True)
    result.language = candidates[0][0]

    # Phase 2: Detect source and test directories.
    _detect_dirs(project_dir, result)

    # Phase 3: Detect framework and tooling.
    _detect_framework(project_dir, result)

    # Phase 4: Infer commands.
    _detect_commands(project_dir, result)

    _detect_playwright_signals(project_dir, result)

    return result


def _detect_dirs(project_dir: Path, result: DetectionResult) -> None:
    """Detect source and test directories for the detected language."""
    if result.language == "python":
        _detect_python_dirs(project_dir, result)
    elif result.language == "node":
        _detect_node_dirs(project_dir, result)
    elif result.language == "go":
        result.source_dirs = ["."]
        result.test_dir = "."  # Go co-locates tests.
    elif result.language == "rust":
        if (project_dir / "src").is_dir():
            result.source_dirs = ["src"]
        if (project_dir / "tests").is_dir():
            result.test_dir = "tests"


def _detect_python_dirs(project_dir: Path, result: DetectionResult) -> None:
    """Detect Python source and test directories."""
    # Check for src layout.
    src = project_dir / "src"
    if src.is_dir():
        # Find the package dir inside src/.
        packages = [
            d.name
            for d in src.iterdir()
            if d.is_dir() and (d / "__init__.py").exists()
        ]
        if packages:
            result.source_dirs = [f"src/{p}" for p in packages]
        else:
            result.source_dirs = ["src"]
    else:
        # Flat layout — look for top-level package directories.
        packages = [
            d.name
            for d in project_dir.iterdir()
            if d.is_dir()
            and (d / "__init__.py").exists()
            and d.name not in ("tests", "test", "docs", ".dark-factory", "__pycache__")
        ]
        if packages:
            result.source_dirs = packages

    # Test dir.
    for candidate in ("tests", "test"):
        if (project_dir / candidate).is_dir():
            result.test_dir = candidate
            break


def _detect_node_dirs(project_dir: Path, result: DetectionResult) -> None:
    """Detect Node.js/TypeScript source and test directories."""
    for candidate in ("src", "lib"):
        if (project_dir / candidate).is_dir():
            result.source_dirs = [candidate]
            break

    for candidate in ("tests", "test", "__tests__"):
        if (project_dir / candidate).is_dir():
            result.test_dir = candidate
            break


def _detect_framework(project_dir: Path, result: DetectionResult) -> None:
    """Detect framework from dependency files."""
    if result.language == "python":
        _detect_python_framework(project_dir, result)
    elif result.language == "node":
        _detect_node_framework(project_dir, result)


def _detect_python_framework(project_dir: Path, result: DetectionResult) -> None:
    """Detect Python framework from dependency declarations."""
    deps: set[str] = set()

    # Parse pyproject.toml dependencies.
    pyproject = project_dir / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        # Extract dependency names from lines like: "fastapi>=0.100",
        # Handles both [project] dependencies and [tool.poetry.dependencies].
        for match in re.finditer(r'["\']([a-zA-Z0-9_-]+)', content):
            deps.add(match.group(1).lower())

    # Parse requirements files.
    for req_file in ("requirements.txt", "requirements/base.txt"):
        req_path = project_dir / req_file
        if req_path.exists():
            for line in req_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("-"):
                    continue
                # Extract package name before any version specifier.
                m = re.match(r'([a-zA-Z0-9_-]+)', line)
                if m:
                    deps.add(m.group(1).lower())

    frameworks = [
        ("fastapi", "FastAPI"),
        ("django", "Django"),
        ("flask", "Flask"),
        ("starlette", "Starlette"),
        ("aiohttp", "aiohttp"),
    ]
    for keyword, name in frameworks:
        if keyword in deps:
            result.framework = name
            return


def _detect_node_framework(project_dir: Path, result: DetectionResult) -> None:
    """Detect Node.js framework from package.json dependency keys."""
    pkg_json = project_dir / "package.json"
    if not pkg_json.exists():
        return

    try:
        pkg = json.loads(pkg_json.read_text())
    except (json.JSONDecodeError, OSError):
        return

    all_deps = set(pkg.get("dependencies", {}).keys()) | set(pkg.get("devDependencies", {}).keys())

    frameworks = [
        ("next", "Next.js"),
        ("express", "Express"),
        ("fastify", "Fastify"),
        ("hono", "Hono"),
        ("koa", "Koa"),
        ("@nestjs/core", "NestJS"),
    ]
    for dep_name, name in frameworks:
        if dep_name in all_deps:
            result.framework = name
            return


def _detect_commands(project_dir: Path, result: DetectionResult) -> None:
    """Infer build, lint, typecheck, and test commands."""
    if result.language == "python":
        _detect_python_commands(project_dir, result)
    elif result.language == "node":
        _detect_node_commands(project_dir, result)
    elif result.language == "go":
        result.build_cmd = "go build ./..."
        result.lint_cmd = "golangci-lint run"
        result.test_cmd = "go test ./..."
    elif result.language == "rust":
        result.build_cmd = "cargo build"
        result.lint_cmd = "cargo clippy -- -D warnings"
        result.test_cmd = "cargo test"


def _detect_python_commands(project_dir: Path, result: DetectionResult) -> None:
    """Infer Python tooling commands from config files."""
    pyproject = project_dir / "pyproject.toml"
    content = pyproject.read_text().lower() if pyproject.exists() else ""

    # Lint.
    if "ruff" in content or (project_dir / "ruff.toml").exists():
        result.lint_cmd = "ruff check ."
    elif "flake8" in content or (project_dir / ".flake8").exists():
        result.lint_cmd = "flake8"

    # Type check.
    if "mypy" in content or (project_dir / "mypy.ini").exists():
        result.typecheck_cmd = "mypy ."
    elif "pyright" in content:
        result.typecheck_cmd = "pyright"

    # Test.
    if "pytest" in content or (project_dir / "pytest.ini").exists() or (project_dir / "conftest.py").exists():
        result.test_cmd = "pytest"
    else:
        result.test_cmd = "python -m pytest"

    # Build — Python doesn't typically have a separate build step.
    result.build_cmd = ""


def _detect_node_commands(project_dir: Path, result: DetectionResult) -> None:
    """Infer Node.js tooling commands from package.json scripts."""
    pkg_json = project_dir / "package.json"
    if not pkg_json.exists():
        return

    try:
        pkg = json.loads(pkg_json.read_text())
    except (json.JSONDecodeError, OSError):
        return

    scripts = pkg.get("scripts", {})

    if "build" in scripts:
        result.build_cmd = "npm run build"
    if "lint" in scripts:
        result.lint_cmd = "npm run lint"
    if "typecheck" in scripts or "type-check" in scripts:
        result.typecheck_cmd = "npm run typecheck" if "typecheck" in scripts else "npm run type-check"
    if "test" in scripts:
        result.test_cmd = "npm test"

    # Check for TypeScript.
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    if "typescript" in deps and not result.typecheck_cmd:
        result.typecheck_cmd = "npx tsc --noEmit"
