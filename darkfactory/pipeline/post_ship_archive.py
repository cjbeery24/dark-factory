"""Phase 5.1 — post-ship archive: tag registered scenarios + append shipped row.

After a spec ships, ``registry_register`` copies its scenarios into
``.dark-factory/shipped-scenarios/<spec_id>/``. This module runs right
after that to:

1. Prepend a two-line header (``spec_origin`` + ``spec_type``) to every
   tagged scenario file in the archive. The comment syntax is chosen by
   file extension so the tag is valid source in the scenario's own
   language — the scenario runner in Phase 5.2 parses these tags to
   route regression runs and attribute failures.

2. Append a :class:`ShippedSpec` row to ``.dark-factory/plan/shipped.jsonl``
   with the ``env_hash_at_ship`` (``hash_view`` of the materialized
   ``EnvironmentContract`` at ship time). Phase 5 uses this to detect
   env drift between ship time and a later regression run; Phase 6 uses
   it as the "what was decided when" record for the plan runner.

Failures here are logged but do not unship the spec — the ship
decision has already been made. Best effort.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from darkfactory.architecture.deltas import hash_view
from darkfactory.architecture.runtime import load_environment
from darkfactory.plan.state import ShippedSpec, append_shipped

log = logging.getLogger(__name__)


def capture_env_hash(project_dir: Path) -> str | None:
    """Snapshot ``hash_view`` of the live env contract — call at SHIP decision.

    Captured *before* any post-ship side effect (registry copy, branch
    creation, regression run) so the recorded hash is the contract the
    Oracle actually saw, not whatever drift happened during archival.
    Returns None on failure; caller decides whether to skip the row.
    """
    try:
        env = load_environment(project_dir)
        return hash_view(env)
    except Exception:  # noqa: BLE001 — best-effort, never unship
        log.exception("post_ship_archive: env hash capture failed")
        return None


# Per-extension header-comment renderer. The tag must be valid source in
# the scenario's own language so (a) the runner can execute the file
# unchanged and (b) the tag survives any downstream formatter.
_EXT_COMMENT: dict[str, tuple[str, str, str]] = {
    # suffix → (line_prefix, block_open, block_close). Empty block markers
    # mean the tag is just two line-prefixed lines.
    ".md": ("<!-- ", "", " -->"),
    ".markdown": ("<!-- ", "", " -->"),
    ".ts": ("// ", "", ""),
    ".tsx": ("// ", "", ""),
    ".js": ("// ", "", ""),
    ".jsx": ("// ", "", ""),
    ".mjs": ("// ", "", ""),
    ".cjs": ("// ", "", ""),
    ".go": ("// ", "", ""),
    ".rs": ("// ", "", ""),
    ".java": ("// ", "", ""),
    ".py": ("# ", "", ""),
    ".rb": ("# ", "", ""),
    ".sh": ("# ", "", ""),
    ".yml": ("# ", "", ""),
    ".yaml": ("# ", "", ""),
    ".toml": ("# ", "", ""),
}

def _render_header(suffix: str, spec_id: str, spec_type: str) -> tuple[str, str] | None:
    """Return ``(first_line, full_block)`` for the extension, or None if unknown.

    ``first_line`` is used as the exact idempotency sentinel — scanning for a
    loose substring like ``"spec_origin:"`` would falsely match any scenario
    whose body mentions this field. The rendered first line is unambiguous.
    """
    tpl = _EXT_COMMENT.get(suffix.lower())
    if tpl is None:
        return None
    prefix, _open, close = tpl
    line1 = f"{prefix}spec_origin: {spec_id}{close}"
    line2 = f"{prefix}spec_type: {spec_type}{close}"
    return line1, f"{line1}\n{line2}\n"


def _split_shebang(body: str) -> tuple[str, str]:
    """Peel a leading shebang off ``body`` so headers land after it.

    Prepending a comment before ``#!/usr/bin/env python`` breaks
    executability — the kernel parses the shebang only on line 1.
    """
    if body.startswith("#!"):
        nl = body.find("\n")
        if nl == -1:
            return body + "\n", ""
        return body[: nl + 1], body[nl + 1 :]
    return "", body


def tag_archived_scenarios(
    archive_dir: Path,
    spec_id: str,
    spec_type: str,
) -> int:
    """Prepend origin/type header comments to every scenario in ``archive_dir``.

    Returns the number of files tagged. Files whose extension isn't in the
    comment map are skipped (logged as a warning). Already-tagged files are
    detected by an exact first-line match against the would-be header, so this
    is safe to call repeatedly and cannot false-positive on scenario bodies
    that happen to mention ``spec_origin``. Shebang lines are preserved.
    """
    if not archive_dir.is_dir():
        return 0
    tagged = 0
    for child in sorted(archive_dir.iterdir()):
        if not child.is_file() or child.name.startswith("."):
            continue
        suffix = child.suffix
        rendered = _render_header(suffix, spec_id, spec_type)
        if rendered is None:
            log.warning(
                "post_ship_archive: no comment syntax for %s; skipping tag",
                child.name,
            )
            continue
        first_line, header_block = rendered
        try:
            body = child.read_text(encoding="utf-8")
        except OSError:
            log.exception("post_ship_archive: read %s failed", child)
            continue
        except UnicodeDecodeError:
            log.warning(
                "post_ship_archive: %s is not utf-8; skipping tag", child,
            )
            continue
        shebang, rest = _split_shebang(body)
        # Exact first-line match: either the header is already at the top
        # (no shebang) or immediately after the shebang. Anything else — even
        # a scenario that mentions "spec_origin:" in its prose — gets tagged.
        if rest.startswith(first_line + "\n") or rest.startswith(first_line + "\r"):
            continue
        try:
            child.write_text(shebang + header_block + rest, encoding="utf-8")
            tagged += 1
        except OSError:
            log.exception("post_ship_archive: write %s failed", child)
    return tagged


def record_shipped_spec(
    project_dir: Path,
    spec_id: str,
    spec_type: str,
    spec_origin: str,
    *,
    delta_filename: str | None = None,
    env_hash: str | None = None,
    services_at_ship: tuple[str, ...] = (),
) -> ShippedSpec | None:
    """Append a row to ``shipped.jsonl``.

    ``env_hash`` should be captured at the SHIP decision via
    :func:`capture_env_hash` and threaded through. If omitted, this falls
    back to capturing at call time — convenient for tests, but in the
    real pipeline that introduces a race window between the Oracle's
    decision and the row being written. Returns None on any failure.

    ``services_at_ship`` records the service names from the environment
    contract at ship time. Used by Phase 4.3 dependency warnings.
    """
    archive_rel = f".dark-factory/shipped-scenarios/{spec_id}"
    if env_hash is None:
        env_hash = capture_env_hash(project_dir)
    if not env_hash:
        return None
    row = ShippedSpec(
        spec_id=spec_id,
        spec_type=spec_type,  # type: ignore[arg-type]
        spec_origin=spec_origin,
        shipped_at=datetime.now(timezone.utc),
        env_hash_at_ship=env_hash,
        delta_filename=delta_filename,
        scenario_archive_path=archive_rel,
        services_at_ship=services_at_ship,
    )
    try:
        append_shipped(project_dir, row)
    except (OSError, ValueError):
        log.exception(
            "post_ship_archive: append_shipped failed for %s", spec_id,
        )
        return None
    return row
