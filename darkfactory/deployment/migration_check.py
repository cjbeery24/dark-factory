"""Enforce expand-contract migration safety.

Scans migration files for mixed destructive+additive operations in the same
file, which violates the expand-contract pattern. Supports SQL, Python ORM
(Django/Alembic/SQLAlchemy), and JavaScript ORM (Sequelize/Knex/TypeORM)
patterns. User-extensible via migration_extra_patterns config.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from darkfactory.deployment.models import MigrationCheckResult
from darkfactory.utils.subprocess_runner import run

log = logging.getLogger(__name__)

# --- Built-in pattern sets (case-insensitive) ---

SQL_DESTRUCTIVE = [
    r"\bDROP\s+TABLE\b",
    r"\bDROP\s+COLUMN\b",
    r"\bDROP\s+INDEX\b",
    r"\bDROP\s+CONSTRAINT\b",
    r"\bDROP\s+DATABASE\b",
    r"\bDELETE\s+FROM\b(?![^;]*\bWHERE\b)",
    r"\bTRUNCATE\s+TABLE\b",
    r"\bALTER\s+TABLE\b.*\bDROP\b",
]

SQL_ADDITIVE = [
    r"\bCREATE\s+TABLE\b",
    r"\bCREATE\s+INDEX\b",
    r"\bADD\s+COLUMN\b",
    r"\bADD\s+CONSTRAINT\b",
    r"\bALTER\s+TABLE\b.*\bADD\b",
]

PYTHON_ORM_DESTRUCTIVE = [
    r"\bRemoveField\b",
    r"\bDeleteModel\b",
    r"\bdrop_column\b",
    r"\bdrop_table\b",
    r"\bdrop_index\b",
    r"\bdrop_constraint\b",
    r"\bop\.drop_column\b",
    r"\bop\.drop_table\b",
    r"\bop\.drop_index\b",
    r"\bop\.drop_constraint\b",
]

PYTHON_ORM_ADDITIVE = [
    r"\bAddField\b",
    r"\bCreateModel\b",
    r"\badd_column\b",
    r"\bcreate_table\b",
    r"\bcreate_index\b",
    r"\bop\.add_column\b",
    r"\bop\.create_table\b",
    r"\bop\.create_index\b",
]

JS_ORM_DESTRUCTIVE = [
    r"\bdropTable\b",
    r"\bremoveColumn\b",
    r"\bdropIndex\b",
    r"\bdropColumn\b",
]

JS_ORM_ADDITIVE = [
    r"\bcreateTable\b",
    r"\baddColumn\b",
    r"\baddIndex\b",
    r"\bcreateIndex\b",
]

ALL_DESTRUCTIVE = SQL_DESTRUCTIVE + PYTHON_ORM_DESTRUCTIVE + JS_ORM_DESTRUCTIVE
ALL_ADDITIVE = SQL_ADDITIVE + PYTHON_ORM_ADDITIVE + JS_ORM_ADDITIVE


def _compile_patterns(
    patterns: list[str],
    extra: list[str] | None = None,
) -> list[re.Pattern[str]]:
    combined = patterns + (extra or [])
    return [re.compile(p, re.IGNORECASE) for p in combined]


def _get_changed_files(project_dir: str, base_branch: str) -> list[str]:
    """Get files changed vs base branch using git diff."""
    result = run(
        ["git", "diff", "--name-only", f"{base_branch}...HEAD"],
        cwd=project_dir,
        timeout=30,
    )
    if result.exit_code != 0:
        log.warning("git diff failed: %s", result.stderr)
        return []
    return [f for f in result.stdout.strip().split("\n") if f]


def _is_migration_file(filepath: str, migration_dirs: list[str]) -> bool:
    """Check if a file path falls within any configured migration directory."""
    parts = filepath.split("/")
    for d in migration_dirs:
        d_stripped = d.rstrip("/")
        d_parts = d_stripped.split("/")
        # Check if the migration dir appears as a proper path prefix.
        for i in range(len(parts) - len(d_parts) + 1):
            if parts[i:i + len(d_parts)] == d_parts:
                return True
    return False


def _scan_file(
    filepath: Path,
    destructive_patterns: list[re.Pattern[str]],
    additive_patterns: list[re.Pattern[str]],
) -> tuple[list[str], list[str]]:
    """Scan a single file for destructive and additive operations.

    Returns (destructive_ops, additive_ops) found.
    """
    try:
        content = filepath.read_text(errors="replace")
    except OSError as e:
        log.warning("cannot read migration file %s: %s", filepath, e)
        return [], []

    destructive_ops: list[str] = []
    additive_ops: list[str] = []

    for pattern in destructive_patterns:
        for match in pattern.finditer(content):
            destructive_ops.append(match.group())

    for pattern in additive_patterns:
        for match in pattern.finditer(content):
            additive_ops.append(match.group())

    return destructive_ops, additive_ops


def check_migrations(
    project_dir: str,
    migration_dirs: list[str],
    base_branch: str = "main",
    extra_patterns: dict[str, list[str]] | None = None,
) -> MigrationCheckResult:
    """Scan migration files for expand-contract safety violations.

    A migration is UNSAFE if it contains both destructive AND additive
    operations in the same file.
    """
    extra_destructive = (extra_patterns or {}).get("destructive", [])
    extra_additive = (extra_patterns or {}).get("additive", [])

    destructive_compiled = _compile_patterns(ALL_DESTRUCTIVE, extra_destructive)
    additive_compiled = _compile_patterns(ALL_ADDITIVE, extra_additive)

    changed = _get_changed_files(project_dir, base_branch)
    migration_files = [f for f in changed if _is_migration_file(f, migration_dirs)]

    if not migration_files:
        return MigrationCheckResult(safe=True)

    all_destructive: list[str] = []
    all_additive: list[str] = []
    files_checked: list[str] = []
    mixed = False

    for rel_path in migration_files:
        filepath = Path(project_dir) / rel_path
        destructive_ops, additive_ops = _scan_file(
            filepath, destructive_compiled, additive_compiled,
        )

        files_checked.append(rel_path)
        all_destructive.extend(destructive_ops)
        all_additive.extend(additive_ops)

        if destructive_ops and additive_ops:
            mixed = True
            log.warning(
                "mixed migration detected in %s: destructive=%s additive=%s",
                rel_path, destructive_ops, additive_ops,
            )

    return MigrationCheckResult(
        safe=not mixed,
        destructive_ops=all_destructive,
        additive_ops=all_additive,
        mixed=mixed,
        files_checked=files_checked,
    )


def suggest_split(
    migration_file: str,
    destructive_ops: list[str],
    additive_ops: list[str],
) -> str:
    """Generate a human-readable suggestion for splitting a mixed migration."""
    lines = [f"Split {migration_file} into two migrations:"]
    if additive_ops:
        ops_str = ", ".join(sorted(set(additive_ops)))
        lines.append(f"  1. Expand: {ops_str} (backward-compatible, deploy first)")
    if destructive_ops:
        ops_str = ", ".join(sorted(set(destructive_ops)))
        lines.append(f"  2. Contract: {ops_str} (after rollback window)")
    return "\n".join(lines)
