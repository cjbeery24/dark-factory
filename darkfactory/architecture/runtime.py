"""Step-boundary environment freshening.

Phase 2.3. The pipeline reads `PipelineConfig` fields like
``config.test_server_cmd`` from many call sites. Rather than rewrite every
reader, this module re-hydrates the relevant `PipelineConfig` fields from
the materialized `environment.toml` at every step boundary via
``step_begin``. Field-level reads stay unchanged.

Three responsibilities:

1. ``load_environment(project_dir)`` — read + parse
   ``.dark-factory/architecture/environment.toml``. Returns the default
   ``EnvironmentContract`` if the file does not exist (Phase 2.4
   backcompat: a synthetic delta from ``config.toml`` will be the
   bootstrap path; until then the empty contract is harmless because
   ``refresh_config_from_environment`` only overwrites with non-empty
   values).
2. ``refresh_config_from_environment(config, env)`` — copy non-empty env
   values into the corresponding ``PipelineConfig`` fields. Empty env
   fields leave the existing config alone (so Phase 2.3 is a strict
   superset of current behavior — config.toml stays authoritative until
   a delta overrides it).
3. ``step_begin(spec_id, step_id, config, project_dir)`` — load + refresh
   + record (spec_id, step_id) in a per-process registry. Resets prior
   entry for the same key first so a re-entry inside the same iteration
   cycle doesn't silently inherit the prior cycle's state.

Known sharp edges (documented, not bugs):

- **Config is mutated in place.** ``step_begin`` writes onto the same
  ``PipelineConfig`` instance the caller passes. This is intentional —
  the pipeline is single-threaded inside one step and the wiring in
  Phase 2.3b assumes step boundaries are sequential. Do NOT capture a
  ``PipelineConfig`` reference across step boundaries (e.g. into a thread
  pool or async task). If a future step needs config-as-of-now, snapshot
  it explicitly with ``copy.replace`` at that point.

- **No "unset" semantics in Phase 2.3.** ``refresh_config_from_environment``
  treats empty strings and zero ints as "env didn't set this; keep config
  as-is." The corollary: if a delta sets ``commands.test = "pnpm test"``
  and a later delta tries to clear it, the materialized view emits the
  empty-string default and the original ``config.toml`` value resurrects.
  Until ``EnvironmentContract`` grows a tri-state (set/unset/explicit-empty)
  the right way to "clear" is to set the field back to the previous value
  the bootstrap delta installed. Phase 4 will revisit when ``spec_type``
  semantics arrive. Tracked.
"""

from __future__ import annotations

import logging
import threading
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from darkfactory.architecture.environment import (
    Commands,
    EnvironmentContract,
    Layout,
    Runtime,
    ServerConfig,
    ServiceEntry,
)
from darkfactory.models.config import PipelineConfig

log = logging.getLogger(__name__)


# ----- Cache -----------------------------------------------------------------

# Per-process cache. Key = (project_dir, mtime_ns). The mtime keying means a
# fold during a run invalidates the cache automatically — load_environment is
# both fast and fresh.
_cache_lock = threading.Lock()
_env_cache: dict[tuple[str, int], EnvironmentContract] = {}


def _env_path(project_dir: Path) -> Path:
    return project_dir / ".dark-factory" / "architecture" / "environment.toml"


def load_environment(project_dir: Path) -> EnvironmentContract:
    """Read and parse the materialized environment.toml. Cached per (path, mtime).

    Returns ``EnvironmentContract()`` (all defaults) if the file is missing.
    Parse failures degrade to defaults with a WARN — env contract failure
    must not crash the pipeline before the migration is complete.
    """
    path = _env_path(project_dir)
    try:
        mtime = path.stat().st_mtime_ns
    except FileNotFoundError:
        return EnvironmentContract()
    except OSError as exc:
        log.warning("environment.toml stat failed: %s", exc)
        return EnvironmentContract()

    key = (str(path), mtime)
    # Parse under the lock so concurrent first-reads don't both parse and
    # fight over the cache slot. Same input → same output, but holding the
    # lock keeps `a is b` identity stable for callers and avoids wasted CPU.
    with _cache_lock:
        cached = _env_cache.get(key)
        if cached is not None:
            return cached
        try:
            raw = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            log.warning("environment.toml parse failed: %s", exc)
            return EnvironmentContract()
        # Evict prior entries for this same path so the cache stays bounded
        # by the number of distinct projects, not by mtime history.
        for existing in [k for k in _env_cache if k[0] == str(path)]:
            del _env_cache[existing]
        contract = _contract_from_toml(raw)
        _env_cache[key] = contract
        return contract


def _contract_from_toml(raw: dict) -> EnvironmentContract:
    """Build an EnvironmentContract from a parsed TOML dict.

    Tolerant of missing sections/keys — anything absent stays at the
    dataclass default. Type-mismatched values are dropped with a WARN
    rather than raising; the contract is best-effort at read time so a
    bad on-disk file doesn't take the pipeline down.
    """

    def _section(section_cls, raw_section):
        if not isinstance(raw_section, dict):
            return section_cls()
        kwargs = {}
        proto = section_cls()  # one instance per section, not one per field
        for f in fields(section_cls):
            if f.name not in raw_section:
                continue
            value = raw_section[f.name]
            default = getattr(proto, f.name)
            if isinstance(default, bool):
                expected_type: type = bool
            elif isinstance(default, int):
                expected_type = int
            elif isinstance(default, str):
                expected_type = str
            elif isinstance(default, list):
                expected_type = list
            elif isinstance(default, dict):
                expected_type = dict
            else:
                continue
            if expected_type is int:
                if not isinstance(value, int) or isinstance(value, bool):
                    log.warning("environment.toml: %s.%s expected int", section_cls.__name__, f.name)
                    continue
            elif expected_type is list:
                if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                    log.warning("environment.toml: %s.%s expected list[str]", section_cls.__name__, f.name)
                    continue
            elif expected_type is dict:
                if not isinstance(value, dict):
                    log.warning("environment.toml: %s.%s expected dict", section_cls.__name__, f.name)
                    continue
                if not all(isinstance(k, str) and isinstance(v, str)
                           for k, v in value.items()):
                    log.warning("environment.toml: %s.%s expected dict[str,str]", section_cls.__name__, f.name)
                    continue
            elif not isinstance(value, expected_type):
                log.warning("environment.toml: %s.%s expected %s",
                            section_cls.__name__, f.name, expected_type.__name__)
                continue
            kwargs[f.name] = value
        return section_cls(**kwargs)

    # Parse services: each key under [services] is a service name mapping
    # to a ServiceEntry-shaped sub-table.
    services: dict[str, ServiceEntry] = {}
    raw_services = raw.get("services", {})
    if isinstance(raw_services, dict):
        for svc_name, svc_raw in raw_services.items():
            if isinstance(svc_raw, dict):
                services[svc_name] = _section(ServiceEntry, svc_raw)
            else:
                log.warning("environment.toml: services.%s expected table", svc_name)

    return EnvironmentContract(
        runtime=_section(Runtime, raw.get("runtime", {})),
        commands=_section(Commands, raw.get("commands", {})),
        server=_section(ServerConfig, raw.get("server", {})),
        layout=_section(Layout, raw.get("layout", {})),
        services=services,
    )


# ----- Refresh ---------------------------------------------------------------

# Map: (env section, env field) -> PipelineConfig field name. Empty env values
# are skipped so config.toml remains authoritative until a delta sets a value.
_ENV_TO_CONFIG: tuple[tuple[str, str, str], ...] = (
    ("commands", "build", "build_cmd"),
    ("commands", "lint", "lint_cmd"),
    ("commands", "typecheck", "typecheck_cmd"),
    ("commands", "test", "test_cmd"),
    ("commands", "server_start", "test_server_cmd"),
    ("commands", "install", "server_setup_cmd"),
    ("server", "health_path", "server_health_path"),
    ("server", "boot_timeout_s", "server_startup_timeout"),
    ("server", "test_base_url", "test_base_url"),
    ("runtime", "language", "language"),
)


def refresh_config_from_environment(
    config: PipelineConfig, env: EnvironmentContract,
) -> PipelineConfig:
    """Overlay env values onto config in place. Returns the same config.

    Only non-empty env values overwrite. layout.source_dirs is handled
    separately because the empty list is the dataclass default and must
    not clobber config.source_dirs. Services are populated from the
    environment contract when present.
    """
    from darkfactory.models.config import ServiceDef

    for section_name, env_field, config_field in _ENV_TO_CONFIG:
        section = getattr(env, section_name)
        value = getattr(section, env_field)
        if isinstance(value, str) and value:
            setattr(config, config_field, value)
        elif isinstance(value, int) and not isinstance(value, bool) and value:
            setattr(config, config_field, value)
    if env.layout.source_dirs:
        config.source_dirs = list(env.layout.source_dirs)
    if env.services:
        config.services = [
            ServiceDef(
                name=name,
                image=entry.image,
                env=dict(entry.env),
                health_cmd=entry.health_cmd,
                health_timeout=entry.health_timeout,
                inject_env=dict(entry.inject_env),
                depends_on=list(entry.depends_on),
            )
            for name, entry in sorted(env.services.items())
            if entry.image  # skip entries with no image (removed services)
        ]
    return config


# ----- step_begin registry ---------------------------------------------------


@dataclass(frozen=True)
class _StepKey:
    spec_id: str
    step_id: str


_registry_lock = threading.Lock()
_step_registry: dict[_StepKey, int] = {}  # value = monotonic call counter


def step_begin(
    spec_id: str,
    step_id: str,
    config: PipelineConfig,
    project_dir: Path,
) -> PipelineConfig:
    """First call of every pipeline step. Refreshes config from environment.

    Records ``(spec_id, step_id)`` in the per-process registry, resetting
    any prior entry for the same key first so cycle 2 doesn't silently
    inherit cycle 1's record. The registry is read by the Phase 2.6
    enforcement test (dynamic side) to confirm every environment-sourced
    field read happened after a ``step_begin`` call in the same call stack.
    """
    key = _StepKey(spec_id=spec_id, step_id=step_id)
    with _registry_lock:
        # Always reset to 1 — re-entry under the same key means a new cycle
        # of the step, not a continuation. The registry records "the most
        # recent step_begin call for this (spec, step)", not a call count.
        _step_registry[key] = 1
    env = load_environment(project_dir)
    return refresh_config_from_environment(config, env)


def _registry_snapshot() -> dict[_StepKey, int]:
    """Test/debug helper. Returns a copy of the registry."""
    with _registry_lock:
        return dict(_step_registry)


def _reset_registry() -> None:
    """Test helper. Clears the registry."""
    with _registry_lock:
        _step_registry.clear()


def _reset_cache() -> None:
    """Test helper. Clears the load_environment cache."""
    with _cache_lock:
        _env_cache.clear()
