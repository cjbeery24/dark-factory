"""Phase 3.4 — lockfile-based environment hints for the Architect.

Before the Architect runs, scan the project root for the usual
package-manager / language artifacts and write a small JSON file with
the detected hints. The Architect reads it (mounted RO via the project
mount) and uses the hints to avoid emitting npm commands against a pnpm
workspace, pip commands against a poetry project, etc.

Hints are *advisory*. The Architect's manifest is the source of truth;
this just nudges it toward not contradicting reality.

Detection is intentionally shallow — file existence at the project root
or one level deep. Anything more clever (parsing lockfiles, walking
workspaces) belongs in Phase 4 / a real detector subsystem.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class EnvironmentHints:
    """Best-guess project tooling hints for the Architect."""

    package_manager: str = ""   # pnpm | yarn | npm | pip | poetry | uv | cargo | go-modules | ""
    language: str = ""          # node | python | rust | go | ""
    lockfiles: list[str] = field(default_factory=list)  # repo-relative paths
    workspace_layout: str = ""  # "monorepo" | "single" | ""


# Search order matters: pnpm beats yarn beats npm because the lockfile
# is the strongest signal. First match wins per (language, manager).
_NODE_LOCKS: tuple[tuple[str, str], ...] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("package-lock.json", "npm"),
)
_PYTHON_LOCKS: tuple[tuple[str, str], ...] = (
    ("uv.lock", "uv"),
    ("poetry.lock", "poetry"),
    ("Pipfile.lock", "pipenv"),
    ("requirements.txt", "pip"),
)
_OTHER_LOCKS: tuple[tuple[str, str, str], ...] = (
    # (filename, language, manager)
    ("Cargo.lock", "rust", "cargo"),
    ("go.sum", "go", "go-modules"),
    ("Gemfile.lock", "ruby", "bundler"),
)
# Manifest files used as a fallback when no lockfile exists. Libraries
# (Rust, Go) commonly omit lockfiles, so the manifest is the strongest
# signal we get.
_MANIFEST_FALLBACKS: tuple[tuple[str, str, str], ...] = (
    ("Cargo.toml", "rust", "cargo"),
    ("go.mod", "go", "go-modules"),
    ("Gemfile", "ruby", "bundler"),
)


def _detect_python_manager_from_pyproject(project_dir: Path) -> str:
    """Read pyproject.toml's build-system / tool tables to guess the
    manager when no lockfile is committed. Returns "" on any failure."""
    pp = project_dir / "pyproject.toml"
    if not pp.is_file():
        return ""
    try:
        text = pp.read_text(encoding="utf-8")
    except OSError:
        return ""
    # Cheap substring matching — avoid pulling in a TOML parser for hints.
    if "[tool.uv]" in text or 'requires = ["uv-build' in text:
        return "uv"
    if "[tool.poetry]" in text:
        return "poetry"
    if "[tool.hatch" in text:
        return "hatch"
    if "[tool.pdm]" in text:
        return "pdm"
    return "pip"  # has pyproject but no recognizable tool table


def _exists_at_root(project_dir: Path, name: str) -> bool:
    return (project_dir / name).is_file()


def _detect_workspace_layout(project_dir: Path) -> str:
    """Cheap monorepo signal: pnpm-workspace.yaml, an apps/ or packages/
    directory at the root, or a workspaces field in package.json."""
    if (project_dir / "pnpm-workspace.yaml").is_file():
        return "monorepo"
    if (project_dir / "apps").is_dir() or (project_dir / "packages").is_dir():
        return "monorepo"
    pkg = project_dir / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return "single"
        if isinstance(data, dict) and data.get("workspaces"):
            return "monorepo"
    return "single"


def detect_hints(project_dir: Path) -> EnvironmentHints:
    """Scan project root for tooling signals.

    Returns an EnvironmentHints with whatever was detected. Empty fields
    mean "couldn't tell" — never guess. The Architect should treat empty
    fields as "use the spec / existing source code".
    """
    hints = EnvironmentHints()

    # Node: lockfile presence is the strongest package-manager signal.
    for filename, manager in _NODE_LOCKS:
        if _exists_at_root(project_dir, filename):
            hints.language = "node"
            hints.package_manager = manager
            hints.lockfiles.append(filename)
            break

    # Python: only consider if no Node lockfile already claimed the language.
    if not hints.language:
        for filename, manager in _PYTHON_LOCKS:
            if _exists_at_root(project_dir, filename):
                hints.language = "python"
                hints.package_manager = manager
                hints.lockfiles.append(filename)
                break

    # Python: pyproject.toml fallback when no lockfile is committed.
    # Libraries and pre-uv projects often have only pyproject.toml.
    if not hints.language and (project_dir / "pyproject.toml").is_file():
        manager = _detect_python_manager_from_pyproject(project_dir)
        if manager:
            hints.language = "python"
            hints.package_manager = manager
            # No lockfile to record — pyproject.toml is the manifest, not a lock.

    # Rust / Go / Ruby lockfiles.
    if not hints.language:
        for filename, lang, manager in _OTHER_LOCKS:
            if _exists_at_root(project_dir, filename):
                hints.language = lang
                hints.package_manager = manager
                hints.lockfiles.append(filename)
                break

    # Manifest fallback for languages that commonly omit lockfiles
    # (Rust libraries, Go modules, Ruby gems without a Gemfile.lock).
    if not hints.language:
        for filename, lang, manager in _MANIFEST_FALLBACKS:
            if _exists_at_root(project_dir, filename):
                hints.language = lang
                hints.package_manager = manager
                break

    hints.workspace_layout = _detect_workspace_layout(project_dir)
    return hints


def write_hints(project_dir: Path, hints: EnvironmentHints) -> Path:
    """Write hints to .dark-factory/architecture/hints.json. Idempotent.

    Single-writer assumption: the dark-factory pipeline runs one spec
    at a time per project, so we never have two architect steps
    competing to overwrite this file. No fsync — it's a regenerable
    cache, not durable state. If you ever ship a multi-spec runner,
    rework this.
    """
    arch_dir = project_dir / ".dark-factory" / "architecture"
    arch_dir.mkdir(parents=True, exist_ok=True)
    out = arch_dir / "hints.json"
    out.write_text(
        json.dumps(asdict(hints), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return out


def detect_and_write(project_dir: Path) -> EnvironmentHints:
    hints = detect_hints(project_dir)
    write_hints(project_dir, hints)
    log.info(
        "environment hints: lang=%s manager=%s layout=%s lockfiles=%s",
        hints.language or "?",
        hints.package_manager or "?",
        hints.workspace_layout or "?",
        hints.lockfiles,
    )
    return hints
