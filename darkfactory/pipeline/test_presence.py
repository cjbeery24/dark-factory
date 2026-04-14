"""Deterministic test-presence preflight.

Runs after the Implementer exits and before the pre-gate Docker launch.
Fails when the implementer's working-tree diff adds source files with
no accompanying test file change. Feeds the existing pre-gate retry
feedback path so violations surface to the implementer identically to
build/lint/typecheck failures.

Why: the ``2026-04-08-shared-parse-amount`` run failed iter 1 because
``packages/shared/src/parse-amount.ts`` was created with no companion
``parse-amount.test.ts`` even though the spec listed both paths and
three visible TDD scenarios existed. The self-tests gate caught it,
but an iteration was burned. Catching it here — before the $-cost
Docker gate launches — is free.

Design notes:

- No greenfield bypass. The first spec that adds source without tests
  is exactly where the convention gets set. Bypassing encodes the wrong
  precedent. The ``source_dirs_preflight`` pattern only bypasses when
  there's nothing to check, not when the project is new.
- Diff scope: added AND modified source files trigger the requirement.
  An "added function to existing file" case must also carry a test
  change.
- Empty-test-file guard: stub test files that contain no invocation
  tokens (``it(``, ``test(``, ``describe(``, ``def test_``, ``#[test]``)
  are treated as the same defect class as a missing test file. A
  warning-only mode would let an implementer write ``// TODO`` as a
  presence-check bypass — exactly what the gate must prevent.
- Source outside ``source_dirs`` emits a warning, not a failure —
  that's a config-drift signal for the architect, not implementer work.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.events.types import emit_preflight_fail_open
from darkfactory.models.config import PipelineConfig
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)


# Test-file detection heuristics, indexed by language. Falls back to
# the Node heuristics for languages we haven't yet seen.
_TEST_FILENAME_SUFFIXES: dict[str, tuple[str, ...]] = {
    "node": (".test.", ".spec."),
    "python": (),  # python uses prefix/suffix rather than dot-segments
    "go": ("_test.",),
    "rust": (),  # rust embeds tests in source modules — not detectable by filename
}

# Path segments that must appear as a *directory* component (not merely
# as a substring of a filename) to classify the path as a test file.
# Matches e.g. ``src/__tests__/a.ts``, ``packages/x/tests/b.py``; does
# NOT match ``src/helpers/tests.ts`` (filename) nor ``src/testament/x.ts``
# (prefix of a non-test segment). Without the directory-component
# requirement, ``tests.py`` next to source was mis-classified as a test,
# which could mask real presence failures.
_TEST_DIR_SEGMENTS: frozenset[str] = frozenset({"__tests__", "tests", "test"})

_PY_TEST_FILENAME_RE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.py$")

# Invocation tokens checked inside test files to detect empty stubs,
# by language. Presence of ANY token in ANY test file => non-empty.
_INVOCATION_TOKENS: dict[str, tuple[str, ...]] = {
    "node": ("it(", "test(", "describe("),
    "python": ("def test_",),
    "go": ("func Test",),
    "rust": ("#[test]",),
}


@dataclass
class TestPresenceResult:
    """Outcome of the test-presence preflight.

    ``violations`` is the list of added source files that lack a
    companion test change. Non-empty => hard failure.

    ``warnings`` is free-form diagnostic strings that do not fail the
    check (e.g. source added outside ``source_dirs``). Surfaced in
    feedback and logs but not treated as blocking.
    """

    violations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def failed(self) -> bool:
        return bool(self.violations)


def check_test_presence(
    config: PipelineConfig,
    project_dir: Path,
    *,
    diff_base: str = "HEAD",
    baseline_untracked: frozenset[str] = frozenset(),
    events_file: Path | None = None,
    spec_name: str = "",
    run_id: str = "",
    iteration: int = 0,
) -> TestPresenceResult:
    """Fail when added source files have no accompanying test change.

    Args:
        config: Pipeline config. ``source_dirs`` and ``language`` are
            consumed; other fields are ignored.
        project_dir: Project working-tree root. Git is invoked against
            this path.
        diff_base: Git revision to diff against. Defaults to ``HEAD``;
            the iteration loop passes ``spec_run.preflight_base_ref``.
        baseline_untracked: Set of paths that were untracked at baseline
            capture time. Subtracted from the currently-untracked set
            so a pre-existing untracked source file is not flagged as
            an implementer-authored add.
        events_file: Optional events.jsonl path. When set, fail-open
            branches emit ``PREFLIGHT_FAIL_OPEN``.
        spec_name / run_id / iteration: Event-correlation fields.

    Returns:
        TestPresenceResult. Caller decides how to surface. Never raises.
    """
    result = TestPresenceResult()

    changes = _collect_diff_changes(
        project_dir,
        diff_base,
        baseline_untracked=baseline_untracked,
        events_file=events_file,
        spec_name=spec_name,
        run_id=run_id,
        iteration=iteration,
    )
    if changes is None:
        # git failure — log and return empty result. A missing diff
        # must not fabricate violations.
        return result

    added_files, modified_files = changes
    language = config.language or "node"
    source_dirs = list(config.source_dirs) if config.source_dirs else []

    # Partition every touched path into test vs source, in- vs out-of-scope.
    added_sources_in_scope: list[str] = []
    added_sources_out_of_scope: list[str] = []
    added_tests: list[str] = []
    modified_tests: list[str] = []

    for path in added_files:
        if _is_test_path(path, language):
            added_tests.append(path)
            continue
        if _in_source_dirs(path, source_dirs):
            added_sources_in_scope.append(path)
        else:
            # Source-shaped file (by elimination) that landed outside
            # any configured source_dir. This is config drift — flag
            # it but don't fail the implementer for it.
            added_sources_out_of_scope.append(path)

    for path in modified_files:
        if _is_test_path(path, language):
            modified_tests.append(path)
        # Modified source files alone don't trigger presence failure;
        # they only "satisfy" the presence condition for added sources
        # via the test-touch check below.

    # Emit source-outside-source_dirs warnings first — they're
    # orthogonal to the presence pass/fail decision and apply whether
    # or not there's anything to guard.
    for p in added_sources_out_of_scope:
        result.warnings.append(
            f"SOURCE_DIRS_COVERAGE: added source {p!r} is outside "
            f"configured source_dirs {source_dirs!r} — no test "
            f"presence requirement enforced, but the architect "
            f"should verify source_dirs covers this path."
        )

    if not added_sources_in_scope:
        # Nothing to guard — deletes, test-only changes, modify-only
        # changes all pass through.
        return result

    # Presence condition is satisfied when any existing test is modified
    # OR any newly-added test file carries real invocation tokens.
    # Rewritten flat for readability: one branch per outcome.
    if modified_tests:
        return result  # existing test touched — presence satisfied
    if not added_tests:
        # No test files added or modified — hard fail, list every
        # added source.
        result.violations.extend(added_sources_in_scope)
        return result

    # Added-only test files exist. If ANY of them carries invocation
    # tokens, presence is satisfied. If ALL are empty stubs, treat as
    # "missing test" (an empty stub must not satisfy the check — that
    # would let an implementer write ``// TODO`` as a bypass).
    empty_added_tests = _find_empty_test_files(
        added_tests, project_dir, language
    )
    all_added_are_empty = len(empty_added_tests) == len(added_tests)
    if all_added_are_empty:
        result.violations.extend(added_sources_in_scope)
        result.warnings.append(
            "EMPTY_TEST_FILES: added test file(s) contain no "
            f"invocation tokens ({_INVOCATION_TOKENS.get(language, _INVOCATION_TOKENS['node'])}): "
            + ", ".join(empty_added_tests)
        )

    return result


def format_violations_feedback(result: TestPresenceResult) -> str:
    """Render violations as an implementer-facing feedback block."""
    if not result.failed:
        return ""

    lines = [
        "The following source files were added without any accompanying "
        "test file change:",
        "",
    ]
    for src in result.violations:
        lines.append(f"  - {src}")
    lines.extend(
        [
            "",
            "Every added source file must be accompanied by either a new "
            "test file or a modification to an existing test file. Empty "
            "stub test files (no `it(`, `test(`, `describe(`, `def test_`, "
            "or `#[test]` tokens) do not satisfy this check — they are "
            "treated identically to a missing test file.",
        ]
    )
    # Include warnings in the feedback if any are present — they're
    # diagnostic context the implementer can act on.
    if result.warnings:
        lines.append("")
        lines.append("Additional notes:")
        for w in result.warnings:
            lines.append(f"  - {w}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _is_test_path(path: str, language: str) -> bool:
    """True if ``path`` looks like a test file in ``language``.

    Combines filename heuristics and directory-segment heuristics. Test
    segment classification requires the segment to be a full directory
    component (not a substring of a filename) to avoid false positives
    like ``src/helpers/tests.py`` or ``src/testament/engine.ts``.
    """
    segments = path.split("/")

    # Directory segment anywhere in the path (EXCLUDING the filename
    # itself) => test. Exact match, not substring.
    for seg in segments[:-1]:
        if seg in _TEST_DIR_SEGMENTS:
            return True

    filename = segments[-1] if segments else path

    if language == "python":
        if _PY_TEST_FILENAME_RE.search(path):
            return True
        return False

    suffixes = _TEST_FILENAME_SUFFIXES.get(
        language, _TEST_FILENAME_SUFFIXES["node"]
    )
    for suffix in suffixes:
        if suffix in filename:
            return True
    return False


def _in_source_dirs(path: str, source_dirs: list[str]) -> bool:
    """True if ``path`` lies under any configured source directory.

    ``source_dirs`` entries are treated as directory prefixes (trailing
    slash optional). Empty list => never in scope (no source_dirs
    configured means the test-presence check has nothing to guard).
    """
    if not source_dirs:
        return False
    for raw in source_dirs:
        prefix = raw.rstrip("/") + "/"
        if path == raw.rstrip("/") or path.startswith(prefix):
            return True
    return False


def _find_empty_test_files(
    test_paths: list[str],
    project_dir: Path,
    language: str,
) -> list[str]:
    """Return test files whose content contains no invocation tokens.

    Files that don't exist on disk are NOT flagged — they may have been
    staged-then-reverted or the git diff contained deletes. We only
    evaluate real content.
    """
    tokens = _INVOCATION_TOKENS.get(language, _INVOCATION_TOKENS["node"])
    empty: list[str] = []
    for rel in test_paths:
        abs_path = project_dir / rel
        try:
            text = abs_path.read_text(encoding="utf-8")
        except OSError:
            continue
        if not any(tok in text for tok in tokens):
            empty.append(rel)
    return empty


def _collect_diff_changes(
    project_dir: Path,
    diff_base: str,
    *,
    baseline_untracked: frozenset[str],
    events_file: Path | None,
    spec_name: str,
    run_id: str,
    iteration: int,
) -> tuple[list[str], list[str]] | None:
    """Return ``(added_paths, modified_paths)`` or None on git failure.

    The implementer runs inside a container with ``.git/`` masked, so
    brand-new files land as *untracked* rather than staged or committed.
    ``git diff --diff-filter=A`` won't see them. We union three sources:

      - ``git diff --diff-filter=A <base>`` (staged/committed adds)
      - ``git diff --diff-filter=M <base>`` (modifications)
      - ``git ls-files -z --others --exclude-standard`` (untracked adds)

    Untracked files are classified as "added". Paths that appear in
    both the added and modified sets are coerced to "added" — the
    presence check treats both identically for the main condition.
    """
    added = _git_name_filtered(
        project_dir,
        diff_base,
        "A",
        events_file=events_file,
        spec_name=spec_name,
        run_id=run_id,
        iteration=iteration,
    )
    if added is None:
        return None
    modified = _git_name_filtered(
        project_dir,
        diff_base,
        "M",
        events_file=events_file,
        spec_name=spec_name,
        run_id=run_id,
        iteration=iteration,
    )
    if modified is None:
        return None
    untracked = _git_untracked_files(
        project_dir,
        events_file=events_file,
        spec_name=spec_name,
        run_id=run_id,
        iteration=iteration,
    )
    if untracked is None:
        # Non-fatal — fall back to tracked-only view. A git ls-files
        # failure shouldn't swallow the check.
        untracked = []

    # Pre-existing untracked files (already present at baseline capture)
    # are not implementer authorship. ``git ls-files --others`` reports
    # *currently* untracked paths regardless of ``diff_base``, so we
    # subtract the captured ``baseline_untracked`` set. Walking the
    # stash-sha tree would miss these because ``git stash create -u``
    # does not reliably commit untracked content into the tree on all
    # git versions.
    implementer_untracked = [p for p in untracked if p not in baseline_untracked]

    added_set = set(added) | set(implementer_untracked)
    modified_set = set(modified) - added_set
    return sorted(added_set), sorted(modified_set)


def _git_name_filtered(
    project_dir: Path,
    diff_base: str,
    filter_letter: str,
    *,
    events_file: Path | None,
    spec_name: str,
    run_id: str,
    iteration: int,
) -> list[str] | None:
    """Run ``git diff -z --name-only --diff-filter=<letter>``.

    Returns the list of paths or None on git failure. Uses ``-z`` so
    filenames with spaces / newlines survive round-tripping.
    """
    result = run(
        [
            "git",
            "-C",
            str(project_dir),
            "diff",
            "-z",
            "--name-only",
            f"--diff-filter={filter_letter}",
            diff_base,
        ],
        timeout=30,
    )
    if result.exit_code != 0:
        stderr_snip = result.stderr.strip()[:500]
        log.warning(
            "test_presence: git diff --diff-filter=%s exit %d; treating "
            "as no changes. stderr=%s",
            filter_letter,
            result.exit_code,
            stderr_snip,
        )
        if events_file is not None:
            emit_preflight_fail_open(
                events_file,
                spec=spec_name,
                run_id=run_id,
                iteration=iteration,
                check="test_presence",
                reason="git_diff_exit_nonzero",
                detail=(
                    f"base={diff_base!r} filter={filter_letter} "
                    f"exit={result.exit_code} stderr={stderr_snip}"
                ),
            )
        return None
    return [p for p in result.stdout.split("\x00") if p]


def _git_untracked_files(
    project_dir: Path,
    *,
    events_file: Path | None,
    spec_name: str,
    run_id: str,
    iteration: int,
) -> list[str] | None:
    """Return untracked files (honoring .gitignore) or None on failure."""
    result = run(
        [
            "git",
            "-C",
            str(project_dir),
            "ls-files",
            "-z",
            "--others",
            "--exclude-standard",
        ],
        timeout=30,
    )
    if result.exit_code != 0:
        stderr_snip = result.stderr.strip()[:500]
        log.warning(
            "test_presence: git ls-files exit %d; treating as no "
            "untracked files. stderr=%s",
            result.exit_code,
            stderr_snip,
        )
        if events_file is not None:
            emit_preflight_fail_open(
                events_file,
                spec=spec_name,
                run_id=run_id,
                iteration=iteration,
                check="test_presence",
                reason="git_ls_files_exit_nonzero",
                detail=f"exit={result.exit_code} stderr={stderr_snip}",
            )
        return None
    return [p for p in result.stdout.split("\x00") if p]
