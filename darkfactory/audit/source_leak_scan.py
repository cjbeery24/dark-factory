"""Phase 4.5.4 — defense-in-depth scan for source-dirs path echo.

The Docker filesystem barrier (Phase 4.5.1/.3) is the primary defense:
masked tmpfs overlays make ``source_dirs`` invisible inside analysis-agent
containers. This module is the *secondary* check: after an analysis agent
finishes, we walk the project's effective ``source_dirs`` and grep the
agent's transcript for any project-relative file path that matches a real
source file on disk. A hit means the agent named a source path it should
not have access to.

Why a separate scanner instead of folding into ``check_output_for_barriers``?
The existing barrier audit substring-matches against ``masked_dirs``, which
contains *top-level directory names* like ``"src"``. That fires on any
mention of the dir name (the agent saying "the source code in src/" trips
it). This scanner adds precision: we only fire on *real file paths* under
those dirs, AND we subtract paths that already appear in the spec text the
agent was given. The result is a high-precision signal worth actioning.

Limitations (intentional, documented):

- Path-string matching only. We do NOT scan for file *contents* — too noisy
  (a comment that says "import os" matches half the codebase).
- Best-effort enumeration. Walks ``source_dirs`` with depth and count caps
  so a giant monorepo cannot stall agent post-processing.
- False negatives by design: basename-only references, paraphrases, line
  citations, or reformatted slashes slip through. The event name is
  ``BARRIER_PATH_ECHO_DETECTED`` — it claims only what it detects.
- Spec-text subtraction is the only false-positive mitigation. Paths the
  spec itself names are the agent's legitimate input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Hard caps so a 100k-file monorepo cannot stall the post-agent step.
_MAX_FILES_SCANNED = 5000
_MAX_DEPTH = 8

# Directories to skip during enumeration: build artifacts, vendored deps,
# and version control. These do not contain code the architect could
# legitimately leak from a barrier-protection standpoint, and they bloat
# the file list.
_SKIP_DIR_NAMES = frozenset({
    ".git", "node_modules", "__pycache__", "dist", "build", "target",
    ".venv", "venv", ".tox", ".mypy_cache", ".pytest_cache", ".next",
    "out", "coverage", ".cache",
})

# File extensions worth tracking. Agent transcripts that name a config or
# lock file are not interesting; we care about source files the architect
# could plausibly quote, paraphrase, or list.
_SOURCE_EXTENSIONS = frozenset({
    ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs",
    ".java", ".kt", ".rb", ".php", ".cs", ".swift", ".scala", ".clj",
    ".ex", ".exs", ".erl", ".c", ".cc", ".cpp", ".h", ".hpp",
})


@dataclass
class LeakScanResult:
    """Outcome of a single post-agent leak scan.

    - matched_paths: project-relative file paths whose names appeared
      verbatim in the agent transcript. Empty when clean.
    - files_scanned: how many source files were enumerated under
      ``source_dirs`` (subject to ``_MAX_FILES_SCANNED``).
    - truncated: True if enumeration hit the file/depth cap. A truncated
      scan that returns no matches is *not* a clean bill of health.
    """
    matched_paths: list[str] = field(default_factory=list)
    files_scanned: int = 0
    truncated: bool = False


def _enumerate_source_files(
    project_dir: Path,
    source_dirs: list[str],
) -> tuple[list[str], bool]:
    """Walk ``source_dirs`` and return project-relative source file paths."""
    paths: list[str] = []
    truncated = False

    for entry in source_dirs:
        root = project_dir / entry
        if not root.is_dir():
            continue
        # Manual stack walk so we can enforce depth + skip dirs cleanly.
        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            if depth > _MAX_DEPTH:
                truncated = True
                continue
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            for child in children:
                if child.is_dir():
                    if child.name in _SKIP_DIR_NAMES:
                        continue
                    stack.append((child, depth + 1))
                    continue
                if child.suffix.lower() not in _SOURCE_EXTENSIONS:
                    continue
                try:
                    rel = child.relative_to(project_dir).as_posix()
                except ValueError:
                    continue
                paths.append(rel)
                if len(paths) >= _MAX_FILES_SCANNED:
                    return paths, True
    return paths, truncated


def scan_agent_output_for_source_leaks(
    output: str,
    project_dir: Path,
    source_dirs: list[str],
    excluded_text: str = "",
) -> LeakScanResult:
    """Return any source-dir file paths that appear verbatim in ``output``.

    ``excluded_text`` is text the agent was *given* as input (typically the
    spec body). Any source path that already appears in ``excluded_text`` is
    subtracted from the match list — the agent learning a path from its own
    prompt is not a barrier echo.

    Pure: no FS writes, no event emission. Caller decides what to do with
    the result (typically: emit ``BARRIER_PATH_ECHO_DETECTED`` and
    audit-log the matched paths).
    """
    if not output or not source_dirs:
        return LeakScanResult()

    paths, truncated = _enumerate_source_files(project_dir, source_dirs)
    matches: list[str] = []
    for path in paths:
        if path in output and path not in excluded_text:
            matches.append(path)
    return LeakScanResult(
        matched_paths=matches,
        files_scanned=len(paths),
        truncated=truncated,
    )
