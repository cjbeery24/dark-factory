"""Phase 2.4 — one-shot config.toml → environment.toml migration.

When the pipeline starts on a project that has no
``.dark-factory/architecture/environment.toml`` yet, this module synthesizes
a single bootstrap delta named ``0000-bootstrap-from-config-toml.toml`` from
the existing ``PipelineConfig`` runtime keys, applies it via ``materialize``,
and emits a deprecation warning. After this runs once, ``environment.toml``
exists and the runtime keys in ``config.toml`` become a HARD ERROR (enforced
in Phase 4 alongside ``spec_type``).

This is **migration scaffolding, not a permanent fallback.** It exists so
projects authored before the env contract continue to run. Projects that
have already shipped a real bootstrap delta will hit the
``environment.toml exists`` short-circuit on the first call and never run
through here.

Failures here are FATAL — the orchestrator must abort rather than continue
on a half-migrated project. ``BackcompatError`` is raised on any synthesis
or materialization failure.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

import tomli_w

from darkfactory.architecture.deltas import (
    EnvironmentConflict,
    SCHEMA_VERSION,
    hash_view,
    load_delta,
    materialize,
)
from darkfactory.architecture.environment import EnvironmentContract
from darkfactory.layout.detect import detect_candidate_source_dirs
from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)


_BOOTSTRAP_FILENAME = "0000-bootstrap-from-config-toml.toml"
_BOOTSTRAP_SPEC_ID = "bootstrap-from-config-toml"


class BackcompatError(RuntimeError):
    """Fatal failure synthesizing or materializing the bootstrap delta."""


# Map: PipelineConfig field -> (env section, env field). Inverse of
# darkfactory.architecture.runtime._ENV_TO_CONFIG. Listed here explicitly so
# the synthesis is auditable in one place — do NOT derive at runtime.
_CONFIG_TO_ENV: tuple[tuple[str, str, str], ...] = (
    ("build_cmd", "commands", "build"),
    ("lint_cmd", "commands", "lint"),
    ("typecheck_cmd", "commands", "typecheck"),
    ("test_cmd", "commands", "test"),
    ("test_server_cmd", "commands", "server_start"),
    ("server_setup_cmd", "commands", "install"),
    ("server_health_path", "server", "health_path"),
    ("server_startup_timeout", "server", "boot_timeout_s"),
    ("language", "runtime", "language"),
)


def _build_bootstrap_delta(
    config: PipelineConfig,
    parent_hash: str,
    spec_id: str,
    project_dir: Path | None = None,
) -> str:
    """Render a bootstrap delta TOML file as a string.

    Only fields the user actually set (i.e. differ from the PipelineConfig
    default) are migrated. We must NOT bake framework defaults into the
    append-only delta log — that would falsely record a "decision" the
    user never made.
    """
    defaults = PipelineConfig()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        f"schema_version = {SCHEMA_VERSION}",
        f'spec_id = "{spec_id}"',
        f'parent_hash = "{parent_hash}"',
        f'applied_at = "{now}"',
        "",
    ]
    for config_field, section, env_field in _CONFIG_TO_ENV:
        value = getattr(config, config_field, None)
        default = getattr(defaults, config_field, None)
        if value == default:
            continue  # user didn't set it; don't migrate framework default
        # tomli_w.dumps handles all escaping correctly. Wrap in a throwaway
        # table so we get just the value rendering.
        rendered = tomli_w.dumps({"v": value}).strip()
        # rendered looks like: v = "..."  → take everything after "v = "
        value_literal = rendered.split("=", 1)[1].strip()
        lines.append("[[ops]]")
        lines.append('op = "set"')
        lines.append(f'path = "{section}.{env_field}"')
        lines.append(f"value = {value_literal}")
        lines.append("")

    # Monorepo detection: if the user never set source_dirs (still at the
    # framework default `["src"]`) and the project is a detectable monorepo,
    # seed `layout.source_dirs` with the detected paths. Without this, every
    # monorepo trips the Phase 4.5.1 pre-flight on its very first run with no
    # environment.toml yet. The pre-flight remains the hard stop — this is
    # convenience, not a safety layer. Only emits when detection found real
    # `apps/*/src` or `packages/*/src` paths; an empty result falls through
    # and the pre-flight will refuse to launch, which is the correct UX.
    if project_dir is not None and config.source_dirs == defaults.source_dirs:
        detected = detect_candidate_source_dirs(project_dir)
        if detected:
            rendered = tomli_w.dumps({"v": detected}).strip()
            value_literal = rendered.split("=", 1)[1].strip()
            lines.append("[[ops]]")
            lines.append('op = "set"')
            lines.append('path = "layout.source_dirs"')
            lines.append(f"value = {value_literal}")
            lines.append("")

    return "\n".join(lines)


def _bootstrap_is_current(bootstrap_path: Path) -> bool:
    """Is an existing bootstrap delta still on the current schema version?"""
    try:
        delta = load_delta(bootstrap_path)
    except Exception:
        return False
    return delta.schema_version == SCHEMA_VERSION


def ensure_environment_bootstrap(
    config: PipelineConfig, project_dir: Path,
) -> bool:
    """If environment.toml is missing, synthesize a bootstrap delta from config.

    Returns True if a synthesis happened, False if the file already exists
    (and therefore the migration has already run for this project).

    FATAL on failure: raises ``BackcompatError``. The orchestrator must
    abort rather than continue on a half-migrated project.
    """
    arch_dir = project_dir / ".dark-factory" / "architecture"
    env_path = arch_dir / "environment.toml"
    if env_path.exists():
        return False

    deltas_dir = arch_dir / "environment.deltas"
    bootstrap_path = deltas_dir / _BOOTSTRAP_FILENAME

    # Half-migration recovery: bootstrap exists but environment.toml does
    # not. If the existing bootstrap is on the current schema version,
    # honor it (the user may have hand-edited it). If it's stale, delete
    # and re-synthesize — letting an old-schema delta through would defeat
    # the point of having a schema version.
    needs_synthesis = not bootstrap_path.exists()
    if bootstrap_path.exists() and not _bootstrap_is_current(bootstrap_path):
        log.warning(
            "backcompat: existing bootstrap delta at %s is on a stale "
            "schema version; re-synthesizing from config.toml.",
            bootstrap_path,
        )
        bootstrap_path.unlink()
        needs_synthesis = True

    if needs_synthesis:
        try:
            deltas_dir.mkdir(parents=True, exist_ok=True)
            parent = hash_view(EnvironmentContract())
            content = _build_bootstrap_delta(
                config, parent_hash=parent, spec_id=_BOOTSTRAP_SPEC_ID,
                project_dir=project_dir,
            )
            bootstrap_path.write_text(content, encoding="utf-8")
            log.warning(
                "config.toml runtime keys are deprecated — synthesized "
                "bootstrap delta at %s. Future spec runs will read environment.toml.",
                bootstrap_path,
            )
        except OSError as exc:
            raise BackcompatError(
                f"could not write bootstrap delta at {bootstrap_path}: {exc}"
            ) from exc

    try:
        materialize(deltas_dir, env_path)
    except EnvironmentConflict as exc:
        raise BackcompatError(f"materialize failed: {exc}") from exc
    except OSError as exc:
        raise BackcompatError(f"materialize io error: {exc}") from exc

    return True
