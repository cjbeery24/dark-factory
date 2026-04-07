"""Phase 4.0 — backfill ``spec_type`` into existing spec frontmatter.

The dark-factory-metadata block inside a spec is an HTML comment
(``<!-- dark-factory-metadata ... -->``) containing ``key: value`` lines.
Before Phase 4, no existing spec had a ``spec_type`` line — the
orchestrator applied a transitional warn-and-default-to-``feature``.
Phase 4 makes ``spec_type`` load-bearing and will eventually hard-fail
on specs without it; this module is the one-shot backfill pass.

Classification heuristic is deliberately narrow. Anything that doesn't
hit a strong keyword defaults to ``feature`` — the same behavior the
orchestrator already applies — but makes the frontmatter explicit.

The tool never silently overwrites an existing ``spec_type``; ``--force``
is required to re-classify. ``--apply`` is required to write; without
it the tool runs in dry-run mode.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Match the opening of the metadata block tolerantly: optional trailing
# whitespace and any newline form.
_METADATA_START_RE = re.compile(r"<!--\s*dark-factory-metadata\s*\n")
_METADATA_END_RE = re.compile(r"-->")
_SPEC_TYPE_LINE_RE = re.compile(r"^\s*spec[_-]type\s*:", re.IGNORECASE)


# Heuristic token buckets. Order is precedence: bugfix beats bootstrap
# beats refactor beats default.
#
# Intentionally narrow — tokens that frequently appear in unrelated
# feature specs (migrate, patch, init) are excluded because they
# false-positive more often than they classify. The transitional default
# is ``feature``, which is what the orchestrator already does, so
# conservative here means "make the explicit tag match the implicit
# behavior" — aggressive classification is not needed.
_BUGFIX_TOKENS = ("fix", "bug", "regression", "hotfix")
_BOOTSTRAP_TOKENS = ("bootstrap", "setup", "scaffold", "foundation")
_REFACTOR_TOKENS = ("refactor", "cleanup", "restructure")


@dataclass
class MigrationResult:
    path: Path
    previous: str | None   # existing spec_type if any
    inferred: str          # classification — never None; defaults to "feature"
    applied: bool          # whether the file was written
    reason: str            # heuristic explanation


def _first_h1(content: str) -> str:
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _filename_tokens(stem: str) -> set[str]:
    """Tokenize a filename stem on any non-letter character."""
    return set(re.findall(r"[a-z]+", stem.lower()))


def _classify(filename_stem: str, h1: str) -> tuple[str, str]:
    """Return (spec_type, reason). Always returns a concrete type."""
    # Match filename tokens strictly (high signal) and H1 tokens loosely
    # (low signal — titles have lots of filler words). Requiring a
    # keyword in the *filename* for bootstrap/bugfix/refactor prevents
    # titles like "Fix typos in the docs" from hijacking the classifier
    # on a feature spec named "2026-04-07-docs-polish.md".
    name_tokens = _filename_tokens(filename_stem)
    h1_tokens = set(re.findall(r"[a-z]+", h1.lower()))

    def hit(bucket: tuple[str, ...]) -> list[str]:
        # Require the token in the filename; H1 alone is not enough.
        return [t for t in bucket if t in name_tokens]

    bug = hit(_BUGFIX_TOKENS)
    if bug:
        return "bugfix", f"filename matched bugfix tokens {bug}"
    boot = hit(_BOOTSTRAP_TOKENS)
    if boot:
        return "bootstrap", f"filename matched bootstrap tokens {boot}"
    ref = hit(_REFACTOR_TOKENS)
    if ref:
        return "refactor", f"filename matched refactor tokens {ref}"
    # Fall back to H1 only for the common "Fix <thing>" / "Bootstrap <thing>"
    # title pattern where the filename has a date prefix and no keyword.
    h1_bug = [t for t in _BUGFIX_TOKENS if t in h1_tokens]
    if h1_bug:
        return "bugfix", f"H1 matched bugfix tokens {h1_bug}"
    h1_boot = [t for t in _BOOTSTRAP_TOKENS if t in h1_tokens]
    if h1_boot:
        return "bootstrap", f"H1 matched bootstrap tokens {h1_boot}"
    return "feature", "no classifying tokens; defaulted to feature"


def _normalize_newlines(content: str) -> str:
    """Collapse CRLF/CR to LF. Specs are written by humans on any OS."""
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _extract_spec_type(block: str) -> str | None:
    for line in block.splitlines():
        if _SPEC_TYPE_LINE_RE.match(line):
            return line.split(":", 1)[1].strip() or None
    return None


def _read_metadata_block(content: str) -> str:
    start = _METADATA_START_RE.search(content)
    if not start:
        return ""
    end_match = _METADATA_END_RE.search(content, start.end())
    if not end_match:
        return ""
    return content[start.end():end_match.start()]


def _inject_spec_type(content: str, spec_type: str) -> str:
    """Append ``spec_type: <value>`` as the last field in the metadata
    block (just before the closing ``-->``).

    Appending — rather than prepending — preserves the ordering humans
    use (feature-id first, plan last, spec_type slotted in at the end)
    and keeps re-runs stable.
    """
    start = _METADATA_START_RE.search(content)
    if not start:
        raise ValueError("spec has no <!-- dark-factory-metadata --> block")
    end_match = _METADATA_END_RE.search(content, start.end())
    if not end_match:
        raise ValueError("metadata block not terminated")
    insertion_point = end_match.start()
    # Ensure the line we insert has its own newline, and that we don't
    # double-up on existing whitespace before the closing marker.
    prefix = content[:insertion_point]
    if not prefix.endswith("\n"):
        prefix += "\n"
    return prefix + f"spec_type: {spec_type}\n" + content[insertion_point:]


def migrate_one(
    spec_file: Path,
    *,
    apply: bool,
    force: bool,
) -> MigrationResult:
    """Classify a single spec and (optionally) rewrite its frontmatter."""
    raw = spec_file.read_text(encoding="utf-8")
    content = _normalize_newlines(raw)
    block = _read_metadata_block(content)
    if not block and _METADATA_START_RE.search(content) is None:
        raise ValueError("spec has no <!-- dark-factory-metadata --> block")
    existing = _extract_spec_type(block)

    if existing and not force:
        return MigrationResult(
            spec_file, existing, existing, False,
            f"already has spec_type={existing!r}; skipped (use --force to override)",
        )

    inferred, reason = _classify(spec_file.stem, _first_h1(content))

    if not apply:
        return MigrationResult(
            spec_file, existing, inferred, False,
            f"would set spec_type={inferred!r}: {reason}",
        )

    # If the file already had a spec_type line, remove it first so we
    # don't end up with duplicates.
    if existing:
        content = re.sub(
            r"^\s*spec[_-]type\s*:.*\n",
            "",
            content,
            count=1,
            flags=re.MULTILINE | re.IGNORECASE,
        )
    new_content = _inject_spec_type(content, inferred)
    spec_file.write_text(new_content, encoding="utf-8")
    return MigrationResult(
        spec_file, existing, inferred, True,
        f"set spec_type={inferred!r}: {reason}",
    )


def migrate_all(
    specs_dir: Path,
    *,
    apply: bool,
    force: bool,
) -> list[MigrationResult]:
    """Migrate every ``.md`` file directly under ``specs_dir``.

    Non-recursive. If a project nests specs by phase or subproject,
    invoke the tool once per directory.
    """
    if not specs_dir.is_dir():
        raise FileNotFoundError(f"specs directory not found: {specs_dir}")
    results: list[MigrationResult] = []
    for spec_file in sorted(specs_dir.glob("*.md")):
        try:
            results.append(migrate_one(spec_file, apply=apply, force=force))
        except ValueError as exc:
            results.append(MigrationResult(
                spec_file, None, "feature", False, f"error: {exc}",
            ))
    return results
