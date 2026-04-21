"""Deterministic forbidden-pattern preflight.

Runs after the Implementer exits and before the pre-gate Docker launch.
Scans *added* source lines in the implementer's working-tree diff for
patterns the architect declared in the architecture manifest's
``forbidden_patterns`` block and returns a violation list the iteration
loop surfaces to the implementer via the existing pre-gate retry
feedback path.

Why: spec-anchored MUST-NOT / "no X" constraints are routinely violated
by the implementer even when the spec repeats them (see the
``2026-04-08-shared-parse-amount`` run that shipped
``Math.round(numValue * 100)`` against an FR-3 that said "no rounding"
four times). A per-anchor literal-substring / regex scan is the
smallest mechanism that closes the loop without adding new agent cost.

Design notes:

- Added lines only. Implementers who don't touch a line don't own its
  contents; the preflight only evaluates new authorship.
- Pattern matching: patterns with an explicit ``re:`` prefix are treated
  as regex. Everything else is a literal substring match. This removes
  the "which metachars imply regex" guesswork — the architect says
  explicitly what they mean.
- Comments are NOT stripped. If the implementer types ``Math.round(``
  inside a comment, that is a deliberate choice and the block is
  warranted. Revisit only if we see false positives in practice.
- No container, no barrier crossing. This is pure host-side work that
  runs in the iteration loop at the same layer as ``preflight.py``.
- Source of truth: this module consumes the ``forbidden_patterns`` dict
  directly (no AnchorManifest JSON involvement). The caller loads it
  from the architect YAML manifest — there is exactly one source.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from darkfactory.events.types import emit_preflight_fail_open
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)

# Explicit regex marker. Patterns starting with ``re:`` are compiled as
# Python regex (search semantics); the rest of the pattern is everything
# after the prefix. Every other pattern is a literal substring match.
# Architects who want a regex opt in explicitly; the old "auto-detect
# metachars" heuristic silently misclassified things like ``.toFixed``.
_REGEX_PREFIX = "re:"

# Unified header lines (``+++ b/<path>``) we must skip when parsing the
# diff. Without this we'd match patterns inside the header itself.
_DIFF_HEADER_PREFIXES = ("+++ ", "--- ", "+++", "---")

# Paths the scanner must never attribute to the implementer. Pipeline
# tooling (architect manifest, events, audit logs, scenarios) and
# standard build/VCS/cache directories live here — none of them are
# implementer authorship, and scanning them produces self-matches
# (e.g. the architect's ``forbidden_patterns`` YAML declaration matches
# its own declared pattern at 100% reliability).
_EXCLUDED_PATH_PREFIXES: tuple[str, ...] = (
    ".dark-factory/",
    ".git/",
    "node_modules/",
    ".venv/",
    "dist/",
    "build/",
    ".pytest_cache/",
    "__pycache__/",
)

_ANCHOR_ID_RE = re.compile(r"^(?:FR|AC|EC)-\d+$")


def _is_excluded_path(rel: str) -> bool:
    """Return True for tool-generated paths the scanner must ignore."""
    return any(rel == p.rstrip("/") or rel.startswith(p) for p in _EXCLUDED_PATH_PREFIXES)


@dataclass(frozen=True)
class ForbiddenPatternViolation:
    """A single forbidden-pattern hit in the implementer's diff."""

    anchor_id: str      # e.g. "FR-3"
    pattern: str        # the raw pattern string from the manifest
    file: str           # project-relative path (e.g. "src/parse-amount.ts")
    line_number: int    # 1-based line number in the post-image
    matched_text: str   # the offending line (trimmed for readability)


def check_forbidden_patterns(
    forbidden_patterns: dict[str, list[str]],
    project_dir: Path,
    *,
    diff_base: str = "HEAD",
    baseline_untracked: frozenset[str] = frozenset(),
    known_anchors: set[str] | None = None,
    events_file: Path | None = None,
    spec_name: str = "",
    run_id: str = "",
    iteration: int = 0,
) -> list[ForbiddenPatternViolation]:
    """Scan added lines in the implementer diff for manifest forbidden patterns.

    Args:
        forbidden_patterns: The architect-declared mapping of
            ``anchor_id -> [pattern, ...]``. Loaded by the caller from
            the architecture YAML manifest. Empty dict => fast no-op.
        project_dir: Project working-tree root. Git is invoked against
            this path.
        diff_base: Git revision to diff against. Defaults to ``HEAD``;
            the iteration loop passes ``spec_run.preflight_base_ref``
            (captured via ``git stash create`` at run start) so
            pre-existing uncommitted changes are not attributed to the
            implementer and results remain stable across iterations.
        baseline_untracked: Set of paths that were untracked at baseline
            capture time (``git ls-files --others``). Any currently-
            untracked path in this set is excluded from the scan —
            it existed before the implementer ran and its contents
            are not implementer authorship. Default empty set => no
            pre-existing-untracked filter (the ``diff_base='HEAD'``
            fallback path).
        known_anchors: If provided, anchor IDs in ``forbidden_patterns``
            are cross-checked against this set for warning purposes.
            Typically the union of ``AnchorManifest.fr_ids/ac_ids/ec_ids``.
        events_file: Optional events.jsonl path. When set, fail-open
            branches emit ``PREFLIGHT_FAIL_OPEN`` so operators can see
            silent skips.
        spec_name / run_id / iteration: Event-correlation fields. Only
            consulted when ``events_file`` is set.

    Returns:
        A list of ForbiddenPatternViolation. Empty list = pass.
        Never raises; git or parse failures emit a fail-open event and
        return an empty list (no false positive violations on
        infrastructure errors).
    """
    if not forbidden_patterns:
        return []

    # Warn on any anchor_id that isn't in the declared anchors set. The
    # check still runs — enforcement doesn't hinge on cross-referencing —
    # but a mis-typed anchor_id is worth surfacing.
    for anchor_id in forbidden_patterns:
        if not _ANCHOR_ID_RE.match(anchor_id):
            log.warning(
                "forbidden_patterns: anchor_id %r is not in FR-N/AC-N/EC-N form",
                anchor_id,
            )
        elif known_anchors and anchor_id not in known_anchors:
            log.warning(
                "forbidden_patterns: anchor_id %r not declared in anchor manifest "
                "— check still runs",
                anchor_id,
            )

    diff_text = _collect_added_lines_diff(
        project_dir,
        diff_base,
        baseline_untracked=baseline_untracked,
        events_file=events_file,
        spec_name=spec_name,
        run_id=run_id,
        iteration=iteration,
    )
    if not diff_text:
        return []

    violations: list[ForbiddenPatternViolation] = []
    for current_file, line_number, line_text in _iter_added_lines(diff_text):
        for anchor_id, patterns in forbidden_patterns.items():
            for pattern in patterns:
                if not _pattern_matches(pattern, line_text):
                    continue
                violations.append(
                    ForbiddenPatternViolation(
                        anchor_id=anchor_id,
                        pattern=pattern,
                        file=current_file,
                        line_number=line_number,
                        matched_text=line_text.strip()[:200],
                    )
                )

    return violations


def format_violations_feedback(
    violations: list[ForbiddenPatternViolation],
) -> str:
    """Render violations as an implementer-facing feedback block.

    Groups by anchor_id for readability. Shape mirrors the pre-gate
    feedback the implementer already knows how to consume.
    """
    if not violations:
        return ""

    # Group by anchor_id preserving first-seen order.
    grouped: dict[str, list[ForbiddenPatternViolation]] = {}
    for v in violations:
        grouped.setdefault(v.anchor_id, []).append(v)

    lines = [
        "The following forbidden patterns were found in your implementation:",
        "",
    ]
    for anchor_id, items in grouped.items():
        for v in items:
            lines.append(f"  - {anchor_id} forbids `{v.pattern}`")
            lines.append(f"      {v.file}:{v.line_number}")
            lines.append(f"      matched: `{v.matched_text}`")
    lines.extend(
        [
            "",
            "These patterns are declared in the architecture manifest as "
            "spec-level MUST-NOT constraints tied to the cited anchor(s). "
            "Remove them before the pre-gate will accept this iteration.",
        ]
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


def _pattern_matches(pattern: str, line_text: str) -> bool:
    """Match a single pattern against a single line.

    Regex path when the pattern starts with ``re:``; literal substring
    otherwise. Regex compile failures fall back to literal substring
    (including the ``re:`` prefix) so a malformed pattern never throws
    and is unambiguously visible in feedback.
    """
    if pattern.startswith(_REGEX_PREFIX):
        regex_body = pattern[len(_REGEX_PREFIX):]
        try:
            return re.search(regex_body, line_text) is not None
        except re.error:
            log.warning(
                "forbidden_patterns: pattern %r failed to compile as regex; "
                "falling back to literal substring match against the full "
                "pattern including the %r prefix",
                pattern,
                _REGEX_PREFIX,
            )
            return pattern in line_text
    return pattern in line_text


def _collect_added_lines_diff(
    project_dir: Path,
    diff_base: str,
    *,
    baseline_untracked: frozenset[str],
    events_file: Path | None,
    spec_name: str,
    run_id: str,
    iteration: int,
) -> str:
    """Return unified-diff output covering tracked + untracked changes.

    The implementer runs inside a container with ``.git/`` masked, so
    new files arrive as untracked in the host working tree — plain
    ``git diff`` skips them. We stitch the diff from two sources:

      1. ``git diff --unified=0 <diff_base>`` for tracked modifications.
      2. ``git diff --no-index --unified=0 /dev/null <path>`` for every
         untracked (not gitignored) file, producing add-only diff blocks
         with the same unified-diff shape the parser already understands.

    Untracked paths are discovered via ``git ls-files -z`` so filenames
    with spaces / newlines survive round-tripping.

    Empty string on any top-level git failure. Individual untracked-file
    diff failures are logged and skipped (a missing file between list
    and diff shouldn't fabricate violations).
    """
    parts: list[str] = []

    tracked = run(
        [
            "git",
            "-C",
            str(project_dir),
            "diff",
            "--unified=0",
            "--no-color",
            diff_base,
        ],
        timeout=30,
    )
    if tracked.exit_code != 0:
        stderr_snip = tracked.stderr.strip()[:500]
        log.warning(
            "forbidden_patterns: git diff exit %d; treating as empty. stderr=%s",
            tracked.exit_code,
            stderr_snip,
        )
        if events_file is not None:
            emit_preflight_fail_open(
                events_file,
                spec=spec_name,
                run_id=run_id,
                iteration=iteration,
                check="forbidden_patterns",
                reason="git_diff_exit_nonzero",
                detail=f"base={diff_base!r} exit={tracked.exit_code} stderr={stderr_snip}",
            )
        return ""
    if tracked.stdout:
        parts.append(tracked.stdout)

    untracked = run(
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
    if untracked.exit_code != 0:
        # Non-fatal — we have the tracked diff. Log, emit, and proceed.
        stderr_snip = untracked.stderr.strip()[:500]
        log.warning(
            "forbidden_patterns: git ls-files exit %d; untracked changes "
            "omitted from scan. stderr=%s",
            untracked.exit_code,
            stderr_snip,
        )
        if events_file is not None:
            emit_preflight_fail_open(
                events_file,
                spec=spec_name,
                run_id=run_id,
                iteration=iteration,
                check="forbidden_patterns",
                reason="git_ls_files_exit_nonzero",
                detail=f"exit={untracked.exit_code} stderr={stderr_snip}",
            )
        return "".join(parts)

    # Pre-existing untracked files (already present at baseline capture)
    # are NOT implementer authorship. ``git ls-files --others`` reports
    # *currently* untracked paths without regard for ``diff_base``, so
    # we subtract the captured ``baseline_untracked`` set. We cannot
    # walk the stash-sha tree for this because ``git stash create -u``
    # does not reliably encode untracked files into the commit on all
    # git versions — the caller must have captured the list separately
    # at baseline time.
    # NUL-separated output; trailing NUL is an empty entry we must drop.
    for rel in untracked.stdout.split("\x00"):
        if not rel:
            continue
        if rel in baseline_untracked:
            # Existed at baseline — not implementer's authorship.
            continue
        if _is_excluded_path(rel):
            # Tool-generated path (architect manifest, events, audit,
            # scenarios, VCS, build output). Never attributable to the
            # implementer; scanning would produce self-matches.
            continue
        # --no-index returns exit 1 when files differ, which is exactly
        # the case we care about (the file is "different" from /dev/null
        # whenever it has any content). It returns 0 only for empty
        # files, which legitimately produce no added lines.
        diff_block = run(
            [
                "git",
                "-C",
                str(project_dir),
                "diff",
                "--unified=0",
                "--no-color",
                "--no-index",
                "/dev/null",
                rel,
            ],
            timeout=30,
        )
        if diff_block.stdout:
            parts.append(diff_block.stdout)
        elif diff_block.exit_code not in (0, 1):
            log.debug(
                "forbidden_patterns: untracked diff for %r returned exit %d",
                rel,
                diff_block.exit_code,
            )

    return "".join(parts)


def _iter_added_lines(diff_text: str):
    """Yield ``(file, line_number, line_text)`` for every added line.

    Parses unified-diff output directly rather than depending on a
    third-party library — the subset we need is small and stable:

      - ``+++ b/<path>`` sets the current file (``/dev/null`` for deletes).
      - ``@@ -x,y +a,b @@`` resets the post-image line counter to ``a``.
      - Subsequent ``+`` lines are added lines at the current counter;
        increment after each.
      - ``-`` lines do not advance the post-image counter; space lines
        would (but ``--unified=0`` produces none).
    """
    current_file: str | None = None
    post_line: int = 0

    for raw in diff_text.splitlines():
        # New file block. ``+++ b/path`` or ``+++ /dev/null``.
        if raw.startswith("+++ "):
            path = raw[4:].strip()
            if path == "/dev/null":
                current_file = None
                continue
            # Strip the standard ``a/`` / ``b/`` prefix; fall through
            # verbatim if the diff was generated without prefixes.
            if path.startswith("b/"):
                path = path[2:]
            if _is_excluded_path(path):
                # Tool-generated path slipped into the tracked diff
                # (e.g. target repo has committed ``.dark-factory/``
                # artifacts). Skip its entire added-line block.
                current_file = None
                continue
            current_file = path
            continue
        if raw.startswith(_DIFF_HEADER_PREFIXES):
            # ``--- a/path`` and bare ``+++``/``---`` — skip without
            # interpreting as added content.
            continue

        # Hunk header: ``@@ -x,y +a,b @@ optional trailing`` (y and b
        # default to 1 when omitted). Reset the post-image counter.
        if raw.startswith("@@"):
            match = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
            if match:
                post_line = int(match.group(1))
            continue

        if current_file is None:
            continue

        if raw.startswith("+"):
            # Added line. ``raw[1:]`` is the post-image content.
            yield current_file, post_line, raw[1:]
            post_line += 1
        elif raw.startswith("-"):
            # Removed line — post-image counter does not advance.
            continue
        else:
            # Context line (only present when unified>0). Post-image
            # counter advances for each context line.
            post_line += 1
