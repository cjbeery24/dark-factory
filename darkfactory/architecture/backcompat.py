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
import os
import tempfile
import tomllib
from datetime import datetime, timezone
from pathlib import Path

import tomli_w

from darkfactory.architecture.deltas import (
    EnvironmentConflict,
    SCHEMA_VERSION,
    _ACCEPTED_SCHEMA_VERSIONS,
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
    ("setup_cmd", "commands", "setup"),
    ("scenario_test_cmd", "commands", "scenario_test"),
    ("server_health_path", "server", "health_path"),
    ("server_startup_timeout", "server", "boot_timeout_s"),
    ("test_base_url", "server", "test_base_url"),
    ("browser_base_url", "server", "browser_base_url"),
    ("browser_server_cmd", "server", "browser_server_cmd"),
    ("browser_startup_timeout", "server", "browser_startup_timeout"),
    ("language", "runtime", "language"),
    ("package_manager", "runtime", "package_manager"),
)


# Operational fields in config.toml to strip after migration to environment.toml.
# Each entry: (TOML section, TOML key, env contract dotted path for safety guard).
# Safety guard: a field is only stripped if its value exists (non-empty) in
# the materialized environment.toml, preventing data loss on incomplete deltas.
_OPERATIONAL_FIELDS: tuple[tuple[str, str, str], ...] = (
    # [core]
    ("core", "source_dirs", "layout.source_dirs"),
    ("core", "test_dir", "layout.test_dirs"),
    ("core", "test_cmd", "commands.test"),
    ("core", "scenario_test_cmd", "commands.scenario_test"),
    # [gates] — operational commands only; tuning fields are preserved
    ("gates", "build_cmd", "commands.build"),
    ("gates", "lint_cmd", "commands.lint"),
    ("gates", "typecheck_cmd", "commands.typecheck"),
    ("gates", "test_cmd", "commands.test"),
    ("gates", "scenario_test_cmd", "commands.scenario_test"),
    # [test_server] — operational fields; startup_timeout and browser_startup_timeout kept
    ("test_server", "server_cmd", "commands.server_start"),
    ("test_server", "setup_cmd", "commands.install"),
    ("test_server", "health_path", "server.health_path"),
    ("test_server", "env", "server.env"),
    ("test_server", "browser_base_url", "server.browser_base_url"),
    ("test_server", "browser_server_cmd", "server.browser_server_cmd"),
    ("test_server", "base_url", "server.test_base_url"),
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

    ops: list[dict[str, object]] = []
    for config_field, section, env_field in _CONFIG_TO_ENV:
        value = getattr(config, config_field, None)
        default = getattr(defaults, config_field, None)
        if value == default:
            continue  # user didn't set it; don't migrate framework default
        ops.append({"op": "set", "path": f"{section}.{env_field}", "value": value})

    # source_dirs: if user explicitly set it, migrate. Otherwise auto-detect
    # for monorepos so the Phase 4.5.1 pre-flight doesn't trip on the very
    # first run. An empty detection result falls through and the pre-flight
    # will refuse to launch, which is the correct UX.
    if config.source_dirs != defaults.source_dirs:
        ops.append({"op": "set", "path": "layout.source_dirs", "value": config.source_dirs})
    elif project_dir is not None:
        detected = detect_candidate_source_dirs(project_dir)
        if detected:
            ops.append({"op": "set", "path": "layout.source_dirs", "value": detected})

    # test_dir: string in PipelineConfig → list in env contract.
    if config.test_dir and config.test_dir != defaults.test_dir:
        ops.append({"op": "set", "path": "layout.test_dirs", "value": [config.test_dir]})

    # server_env: dict needs proper TOML serialization (not the split trick).
    if config.server_env and config.server_env != defaults.server_env:
        ops.append({"op": "set", "path": "server.env", "value": dict(config.server_env)})

    doc: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "spec_id": spec_id,
        "parent_hash": parent_hash,
        "applied_at": now,
    }
    if ops:
        doc["ops"] = ops
    return tomli_w.dumps(doc)


def _bootstrap_is_current(bootstrap_path: Path) -> bool:
    """Is an existing bootstrap delta on an accepted schema version?

    Accepts any version in ``_ACCEPTED_SCHEMA_VERSIONS`` (not just the
    latest) so a v1 bootstrap created before Phase 4 is not unnecessarily
    re-synthesized when v2 code first runs against the project.
    """
    try:
        delta = load_delta(bootstrap_path)
    except Exception:
        return False
    return delta.schema_version in _ACCEPTED_SCHEMA_VERSIONS


def _strip_operational_fields(project_dir: Path) -> bool:
    """Strip operational fields from config.toml that now live in environment.toml.

    Idempotent: safe to call on every run. Only strips a field if its value
    is present (non-empty) in the materialized environment.toml — this
    prevents data loss when the delta chain is incomplete.

    Returns True if config.toml was modified.
    """
    config_path = project_dir / ".dark-factory" / "config.toml"
    env_path = project_dir / ".dark-factory" / "architecture" / "environment.toml"
    if not config_path.exists() or not env_path.exists():
        return False

    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
        env_raw = tomllib.loads(env_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        log.warning("strip_operational_fields: could not parse files: %s", exc)
        return False

    modified = False
    for toml_section, toml_key, env_dotted_path in _OPERATIONAL_FIELDS:
        section = data.get(toml_section)
        if not isinstance(section, dict) or toml_key not in section:
            continue

        # Safety guard: only strip if the value exists in environment.toml.
        env_section_name, env_field = env_dotted_path.split(".")
        env_section = env_raw.get(env_section_name, {})
        env_value = env_section.get(env_field) if isinstance(env_section, dict) else None
        if env_value is None:
            log.info(
                "strip: keeping [%s].%s — not yet in environment.toml (%s)",
                toml_section, toml_key, env_dotted_path,
            )
            continue

        del section[toml_key]
        modified = True

    if not modified:
        return False

    # Remove sections that became empty after stripping.
    for section_name in list(data.keys()):
        if isinstance(data[section_name], dict) and not data[section_name]:
            del data[section_name]

    # Atomic rewrite: tempfile + os.replace.
    serialized = tomli_w.dumps(data)
    dir_ = str(config_path.parent)
    fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(serialized)
        os.replace(tmp, config_path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    log.warning(
        "Migrated operational fields from config.toml to environment.toml — "
        "config.toml now holds only pipeline tuning."
    )
    return True


def ensure_environment_bootstrap(
    config: PipelineConfig, project_dir: Path,
) -> bool:
    """If environment.toml is missing, synthesize a bootstrap delta from config.

    Returns True if a synthesis happened, False if the file already exists
    (and therefore the migration has already run for this project).

    Also strips operational fields from config.toml (idempotent) so that
    config.toml converges to pipeline-tuning-only regardless of when the
    project was first migrated.

    FATAL on failure: raises ``BackcompatError``. The orchestrator must
    abort rather than continue on a half-migrated project.
    """
    arch_dir = project_dir / ".dark-factory" / "architecture"
    env_path = arch_dir / "environment.toml"
    if env_path.exists():
        _strip_operational_fields(project_dir)
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

    _strip_operational_fields(project_dir)
    return True
