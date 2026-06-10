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
    """Detected project characteristics.

    ``typescript`` and ``test_runner`` are optional enrichments consumed by
    the project-hints writer (B2). They are deliberately populated from
    package.json / pyproject.toml dependency inspection — NOT from
    substring-sniffing the derived command strings — so they survive
    command overrides without false-positives.
    """

    language: str = "unknown"
    package_manager: str = ""
    source_dirs: list[str] = field(default_factory=list)
    test_dir: str = ""
    framework: str = ""
    build_cmd: str = ""
    lint_cmd: str = ""
    typecheck_cmd: str = ""
    test_cmd: str = ""
    setup_cmd: str = ""
    server_cmd: str = ""
    typescript: bool = False
    test_runner: str = ""
    workspace_layout: str = ""
    lockfiles: list[str] = field(default_factory=list)


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


def _detect_package_manager(project_dir: Path, result: DetectionResult) -> None:
    """Detect package manager + workspace layout + lockfiles, reusing
    hints.py logic.

    ``hints.detect_hints`` is the single authoritative detector for these
    three fields. We copy its output into ``DetectionResult`` here so
    downstream callers (``_write_project_hints`` in B2) don't have to
    re-walk the filesystem to get the same answer.
    """
    from darkfactory.architecture.hints import detect_hints
    hints = detect_hints(project_dir)
    if hints.package_manager:
        result.package_manager = hints.package_manager
    result.workspace_layout = hints.workspace_layout
    result.lockfiles = list(hints.lockfiles)


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

    # Phase 1b: Detect package manager (reuses hints.py logic).
    _detect_package_manager(project_dir, result)

    # Phase 2: Detect source and test directories.
    _detect_dirs(project_dir, result)

    # Phase 3: Detect framework and tooling.
    _detect_framework(project_dir, result)

    # Phase 4: Infer commands (includes server_cmd and setup_cmd).
    _detect_commands(project_dir, result)

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

    # Monorepo fallback: if no root src/ or lib/, delegate to the shared
    # layout detector which handles apps/*/src, packages/*/src, cmd/.
    if not result.source_dirs:
        from darkfactory.layout.detect import detect_candidate_source_dirs
        detected = detect_candidate_source_dirs(project_dir)
        if detected:
            result.source_dirs = detected

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
        # --disable-error-code=import-untyped suppresses ONLY the
        # "[import-untyped]" error (a third-party lib is installed but ships no
        # type stubs — e.g. `import setuptools` in a generated setup.py; the
        # dark-factory-python image ships mypy but no `types-*` stub packages,
        # and the implementer cannot fix a stub gap in a dependency). It leaves
        # "[import-not-found]" (a genuinely-missing module) and ALL in-project
        # type errors ([arg-type]/[return-value]/...) fully enforced — verified.
        # Do NOT use --ignore-missing-imports: it also swallows
        # [import-not-found], letting real missing-module bugs ship. Mirrors the
        # --break-system-packages PEP 668 belt-and-suspenders pattern below.
        result.typecheck_cmd = "mypy . --disable-error-code=import-untyped"
    elif "pyright" in content:
        result.typecheck_cmd = "pyright"

    # Test.
    has_pytest_signal = (
        "pytest" in content
        or (project_dir / "pytest.ini").exists()
        or (project_dir / "conftest.py").exists()
    )
    if has_pytest_signal:
        result.test_cmd = "pytest"
        result.test_runner = "pytest"
    else:
        # No concrete signal — default to pytest via python -m for zero-config
        # runs, but do NOT claim "pytest" as the test_runner: project-hints
        # consumers treat the field as ground truth, not aspiration.
        result.test_cmd = "python -m pytest"

    # Setup command — from ORM/migration tools.
    if (project_dir / "alembic.ini").exists() or (project_dir / "alembic").is_dir():
        result.setup_cmd = "alembic upgrade head"

    # Build — install project dependencies so mypy and other type-checkers
    # can resolve imports. Prefer editable install when a package is present;
    # editable install makes the project itself importable, not just its deps.
    # requirements-dev.txt and other split-file patterns are not auto-detected;
    # users should set build_cmd manually for those.
    # `--break-system-packages`: the dark-factory-python image's base (alpine)
    # marks its system python PEP 668 externally-managed, so a bare `pip install`
    # is refused inside the gate container (the image also sets
    # PIP_BREAK_SYSTEM_PACKAGES=1, but the explicit flag keeps the generated
    # config self-documenting and portable; it's a harmless no-op on
    # non-externally-managed pythons, pip>=23.0.1).
    if (
        (project_dir / "pyproject.toml").exists()
        or (project_dir / "setup.py").exists()
        or (project_dir / "setup.cfg").exists()
    ):
        result.build_cmd = "pip install -e . -q --break-system-packages"
    elif (project_dir / "requirements.txt").exists():
        result.build_cmd = "pip install -r requirements.txt -q --break-system-packages"
    elif (project_dir / "requirements" / "base.txt").exists():
        result.build_cmd = "pip install -r requirements/base.txt -q --break-system-packages"
    # else: leave build_cmd = "" (no installable package detected)


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

    # Detect package manager.
    is_pnpm = (project_dir / "pnpm-lock.yaml").exists()
    is_pnpm_workspace = (project_dir / "pnpm-workspace.yaml").exists()
    runner = "pnpm" if is_pnpm else "npm"

    # Detect test framework — vitest requires explicit `run` to avoid watch mode.
    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
    has_vitest = "vitest" in deps

    # Project-hints enrichment — record *which* test runner and whether
    # TypeScript is declared, so B2's migrator writes the correct
    # [detected] entries without re-parsing package.json. Order matters:
    # a package with both vitest and jest is almost always in the middle
    # of a migration and `vitest` is the newer choice.
    if has_vitest:
        result.test_runner = "vitest"
    elif "jest" in deps:
        result.test_runner = "jest"
    elif "mocha" in deps:
        result.test_runner = "mocha"
    result.typescript = "typescript" in deps

    if is_pnpm_workspace:
        # pnpm workspaces: use -r (recursive) flags and include install in build_cmd
        # so the container always has fresh dependencies.
        if "build" in scripts:
            result.build_cmd = "pnpm install && pnpm run build"
        if "lint" in scripts:
            result.lint_cmd = "pnpm -r lint"
        if "typecheck" in scripts or "type-check" in scripts:
            result.typecheck_cmd = (
                "pnpm -r typecheck" if "typecheck" in scripts else "pnpm -r type-check"
            )
        if "test" in scripts:
            # pnpm -r run --if-present test: runs each workspace package's "test"
            # script but skips packages that have no "test" script at all, preventing
            # vitest v4+ from exiting 1 when a package has no test files.
            # --if-present must come BEFORE the script name (pnpm run flag, not script arg).
            result.test_cmd = "pnpm -r run --if-present test"
    else:
        if "build" in scripts:
            result.build_cmd = f"{runner} run build"
        if "lint" in scripts:
            result.lint_cmd = f"{runner} run lint"
        if "typecheck" in scripts or "type-check" in scripts:
            result.typecheck_cmd = (
                f"{runner} run typecheck" if "typecheck" in scripts
                else f"{runner} run type-check"
            )
        if "test" in scripts:
            if has_vitest:
                # vitest watch mode guard: `npm test -- --run` or `pnpm exec vitest run`
                result.test_cmd = (
                    "pnpm exec vitest run" if is_pnpm else "npx vitest run"
                )
            else:
                result.test_cmd = f"{runner} test"

    # Check for TypeScript — add typecheck fallback if not already detected.
    if "typescript" in deps and not result.typecheck_cmd:
        result.typecheck_cmd = "tsc --noEmit"

    # Server command — from package.json scripts.
    for script_name in ("dev", "start", "serve"):
        if script_name in scripts:
            result.server_cmd = f"{runner} run {script_name}"
            break

    # Setup command — from ORM/migration tools.
    if "prisma" in deps or "@prisma/client" in deps:
        prefix = "pnpm exec" if is_pnpm else "npx"
        result.setup_cmd = f"{prefix} prisma db push"
    elif "drizzle-kit" in deps:
        prefix = "pnpm exec" if is_pnpm else "npx"
        result.setup_cmd = f"{prefix} drizzle-kit push"


