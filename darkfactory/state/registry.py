"""Shipped scenario registry — persistent storage for blind test scenarios.

Replaces lib/registry.sh — registry_register, registry_deregister,
registry_list, registry_scenario_dir, registry_run_regression.

When a spec ships, its scenarios are copied to the registry so they
persist across runs and can be used for regression testing.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from darkfactory.config.schema import validate_spec_name
from darkfactory.errors import DarkFactoryError
from darkfactory.state.lock import FileLock
from darkfactory.utils.hash import sha256_file

log = logging.getLogger(__name__)

REGISTRY_DIR_NAME = "shipped-scenarios"
META_FILE = ".registry-meta"


def _registry_dir(project_dir: Path) -> Path:
    return project_dir / ".dark-factory" / REGISTRY_DIR_NAME


def _validate_spec_name(spec_name: str) -> None:
    """Validate spec name using the canonical schema validator."""
    validate_spec_name(spec_name)


def register(
    project_dir: Path,
    spec_name: str,
    *,
    source_root: Path | None = None,
) -> bool:
    """Copy scenarios for a shipped spec into the persistent registry.

    Copies from .dark-factory/scenarios/{spec}/ to the registry.
    Creates a .registry-meta file with metadata.
    Returns True if registered successfully.
    """
    _validate_spec_name(spec_name)

    scenarios_dir = (source_root or project_dir) / ".dark-factory" / "scenarios" / spec_name
    if not scenarios_dir.exists():
        log.warning("no scenarios to register for %s", spec_name)
        return False

    scenario_files = [f for f in scenarios_dir.iterdir() if f.is_file() and not f.name.startswith(".")]
    if not scenario_files:
        log.warning("empty scenarios directory for %s", spec_name)
        return False

    registry = _registry_dir(project_dir)
    target = registry / spec_name
    registry.mkdir(parents=True, exist_ok=True)
    lock_path = registry / ".registry.lock"
    lock = FileLock(lock_path, timeout=30.0)

    with lock:
        # Idempotent: remove existing before copying.
        if target.exists():
            shutil.rmtree(target)

        target.mkdir(parents=True, exist_ok=True)

        count = 0
        for f in scenario_files:
            shutil.copy2(f, target / f.name)
            count += 1

        # Collect target_files from spec if available.
        target_files_str = ""
        spec_file = project_dir / ".dark-factory" / "specs" / f"{spec_name}.md"
        if spec_file.exists():
            target_files_str = _extract_target_files(spec_file)

        # Write metadata.
        meta = target / META_FILE
        meta.write_text(
            f"spec={spec_name}\n"
            f"registered={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"scenario_count={count}\n"
            f"target_files={target_files_str}\n"
        )

    log.info("registered %d scenarios for %s", count, spec_name)
    return True


def deregister(project_dir: Path, spec_name: str) -> bool:
    """Remove a spec's scenarios from the registry.

    Returns True if removed, False if not found.
    """
    _validate_spec_name(spec_name)

    registry = _registry_dir(project_dir)
    target = registry / spec_name

    if not target.exists():
        return False

    registry.mkdir(parents=True, exist_ok=True)
    lock_path = registry / ".registry.lock"
    lock = FileLock(lock_path, timeout=30.0)

    with lock:
        shutil.rmtree(target)

    log.info("deregistered scenarios for %s", spec_name)
    return True


def list_specs(project_dir: Path) -> list[str]:
    """List all registered spec names."""
    registry = _registry_dir(project_dir)
    if not registry.exists():
        return []

    return sorted(
        d.name
        for d in registry.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def scenario_dir(project_dir: Path, spec_name: str) -> Path | None:
    """Return the path to a registered spec's scenarios, or None."""
    _validate_spec_name(spec_name)
    registry = _registry_dir(project_dir)
    target = registry / spec_name
    if target.exists():
        return target
    return None


def read_meta(project_dir: Path, spec_name: str) -> dict[str, str]:
    """Read the registry metadata for a spec.

    Returns empty dict if not registered or meta file missing.
    """
    _validate_spec_name(spec_name)
    registry = _registry_dir(project_dir)
    meta_path = registry / spec_name / META_FILE

    if not meta_path.exists():
        return {}

    result: dict[str, str] = {}
    with meta_path.open() as f:
        for line in f:
            stripped = line.strip()
            if "=" in stripped:
                key, _, value = stripped.partition("=")
                result[key] = value
    return result


def read_target_files(project_dir: Path, spec_name: str) -> set[str]:
    """Read the target_files set for a registered spec."""
    meta = read_meta(project_dir, spec_name)
    raw = meta.get("target_files", "")
    if not raw:
        return set()
    return {f.strip() for f in raw.split(",") if f.strip()}


def _extract_target_files(spec_file: Path) -> str:
    """Extract target-files from a spec's markdown frontmatter or body.

    Looks for a line like: target-files: src/foo.py, src/bar.py
    """
    text = spec_file.read_text()
    m = re.search(r"^target[_-]files:\s*(.+)$", text, re.MULTILINE | re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def migrate_from_state(project_dir: Path) -> int:
    """One-time migration: populate registry from legacy state files.

    Only migrates specs marked status=shipped in .dark-factory/state/*.state.
    Returns count of specs migrated.
    """
    registry = _registry_dir(project_dir)
    if registry.exists() and list_specs(project_dir):
        log.info("registry already populated, skipping migration")
        return 0

    state_dir = project_dir / ".dark-factory" / "state"
    if not state_dir.exists():
        return 0

    count = 0
    for state_file in state_dir.glob("*.state"):
        spec_name = state_file.stem
        # Read status from state file.
        status = ""
        with state_file.open() as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("status="):
                    status = stripped[7:]
        if status == "shipped":
            if register(project_dir, spec_name):
                count += 1

    if count:
        log.info("migrated %d specs from legacy state files", count)
    return count
