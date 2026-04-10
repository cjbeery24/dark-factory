"""Auto-detect delta writer — Phase 2 of config unification.

Detection results are recorded as an append-only delta in the
environment delta log, making them visible, traceable, and subject to
the same hash-chain integrity as Architect-authored amendments.

The auto-detect delta fills gaps only: if a gate command field already
has a value (from config.toml, a prior delta, or the Architect), it
is NOT overwritten. This preserves the safety invariant that human and
Architect values take precedence over detection heuristics.
"""

from __future__ import annotations

import logging
from pathlib import Path

from darkfactory.architecture.amend import _next_delta_number, _render_delta, _slugify
from darkfactory.architecture.deltas import (
    fold_deltas,
    hash_view,
    list_deltas,
    materialize,
)
from darkfactory.architecture.environment import (
    BOOTSTRAP_ONLY_PATHS,
    EnvironmentContract,
)
from darkfactory.architecture.runtime import (
    load_environment,
    refresh_config_from_environment,
)
from darkfactory.models.config import PipelineConfig
from darkfactory.setup.detect import detect_project

log = logging.getLogger(__name__)

# Mapping: (DetectionResult field, environment contract path, PipelineConfig field)
# Phase 2 covers gate commands only. Phase 3 will expand detection to
# cover package_manager, server_cmd, setup_cmd, etc.
_DETECT_TO_ENV: tuple[tuple[str, str, str], ...] = (
    ("build_cmd", "commands.build", "build_cmd"),
    ("lint_cmd", "commands.lint", "lint_cmd"),
    ("typecheck_cmd", "commands.typecheck", "typecheck_cmd"),
    ("test_cmd", "commands.test", "test_cmd"),
)


def write_autodetect_delta(
    project_dir: Path,
    config: PipelineConfig,
) -> list[tuple[str, str]]:
    """Detect project commands and write gaps as an environment delta.

    Only fills fields where config has an empty value and detection found
    a non-empty one. Bootstrap-only paths (runtime.language,
    layout.source_dirs) are never written by auto-detect.

    Returns list of (config_field_name, value) pairs that were filled.
    Empty list if nothing needed filling or detection failed.
    """
    try:
        detected = detect_project(project_dir)
    except Exception as exc:
        log.warning("auto-detect delta: detection failed: %s", exc)
        return []

    # Build ops for empty config fields where detection found a value.
    ops: list[tuple[str, object]] = []
    filled: list[tuple[str, str]] = []
    for detect_field, env_path_str, config_field in _DETECT_TO_ENV:
        if env_path_str in BOOTSTRAP_ONLY_PATHS:
            continue
        config_value = getattr(config, config_field, "")
        detected_value = getattr(detected, detect_field, "")
        if not config_value and detected_value:
            ops.append((env_path_str, detected_value))
            filled.append((config_field, detected_value))

    if not ops:
        return []

    arch_dir = project_dir / ".dark-factory" / "architecture"
    deltas_dir = arch_dir / "environment.deltas"
    env_path = arch_dir / "environment.toml"

    # Compute parent hash from current fold.
    if deltas_dir.exists() and list_deltas(deltas_dir):
        parent_view = fold_deltas(deltas_dir)
    else:
        parent_view = EnvironmentContract()
    parent_hash = hash_view(parent_view)

    # Idempotency: if every proposed op already matches the current
    # materialized view, skip delta write to prevent log growth.
    def _current(path: str) -> object:
        parts = path.split(".")
        if len(parts) == 2:
            section = getattr(parent_view, parts[0], None)
            if section is None:
                return object()  # sentinel: never equal
            return getattr(section, parts[1], object())
        return object()

    if all(_current(path) == value for path, value in ops):
        log.info(
            "auto-detect delta: all %d op(s) already match current view; "
            "skipping delta write (idempotent)",
            len(ops),
        )
        # Still overlay onto config in case it's out of sync with env.
        env = load_environment(project_dir)
        refresh_config_from_environment(config, env)
        for config_field, _ in filled:
            config.set_provenance(config_field, "auto-detect")
        return filled

    _SPEC_ID = "auto-detect"

    deltas_dir.mkdir(parents=True, exist_ok=True)
    n = _next_delta_number(deltas_dir)
    filename = f"{n:04d}-{_slugify(_SPEC_ID)}.toml"
    delta_path = deltas_dir / filename

    # Snapshot environment.toml BEFORE we touch anything so we can
    # restore it byte-for-byte if materialize fails.
    env_snapshot: bytes | None
    if env_path.exists():
        env_snapshot = env_path.read_bytes()
    else:
        env_snapshot = None

    for config_field, value in filled:
        log.warning(
            "auto-detect: filled %s = %r — Architect should declare this "
            "in runtime: block",
            config_field, value,
        )

    delta_path.write_text(
        _render_delta(_SPEC_ID, parent_hash, ops), encoding="utf-8",
    )
    log.info("auto-detect delta: wrote %s with %d op(s)", delta_path.name, len(ops))

    try:
        materialize(deltas_dir, env_path)
    except Exception:
        # Rollback: restore env snapshot and remove delta file.
        if env_snapshot is not None:
            env_path.write_bytes(env_snapshot)
        elif env_path.exists():
            env_path.unlink()
        delta_path.unlink(missing_ok=True)
        raise

    # Overlay materialized values onto config via the standard path.
    env = load_environment(project_dir)
    refresh_config_from_environment(config, env)
    # Override provenance to "auto-detect" for fields we filled.
    for config_field, _ in filled:
        config.set_provenance(config_field, "auto-detect")

    return filled
