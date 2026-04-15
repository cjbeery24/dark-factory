"""Project-hints loader — Sprint 1 / B1.

Reads ``.dark-factory/project-hints.toml`` — the standing policy layer that
sits between a project's specs (per-feature) and the ecosystem adapter layer
(universal per language/tool). This module shapes the on-disk TOML into
typed, frozen dataclasses; it does NOT interpret the policy. Downstream:

* B5 will feed ``PolicySection.consistency_rules`` into
  ``architecture/consistency.py``.
* A4 will feed the policy into the architect prompt composition.
* C1/C2 will read/write ``[policy.consistency_rules]`` from the failure
  event → remediation flow.

Design notes (read before editing):

* **Strict structure, lenient forward-compat.** Wrong *types* in known
  fields raise ``ConfigError`` — typos cost money when the architect burns
  an iteration trusting a malformed hint. Unknown top-level sections or
  unknown keys inside a known section log a WARN (a future `dark-factory`
  minor may add fields; old clients should not crash).

  ``ConfigError`` messages carry the dotted path of the offending field
  (e.g. ``policy.consistency_rules[0].name``). ``tomllib``-level parse
  errors additionally carry line/column from the parser. Structural
  errors do NOT carry a line number — ``tomllib`` discards positions
  once a value is constructed, and tracking them ourselves would double
  the parser. The dotted path plus a minimal TOML is in practice enough
  to locate the offending entry.

* **Backward-compat synthesis.** If ``project-hints.toml`` is missing but
  the legacy pair ``environment.toml`` + ``hints.json`` exists, we
  synthesize a ``[detected]`` section from them and warn the caller to
  run ``dark-factory init --migrate-hints``. Only ``[detected]`` is
  synthesizable — ``[policy]`` is always human-authored. Synthesis is a
  pure pass-through of ``hints.json`` facts; it does NOT re-derive
  ``typescript`` / ``test_runner`` from ``environment.toml`` commands
  (that kind of heuristic sniffing belongs in ``darkfactory/setup/``,
  not here). ``synthesized_from_legacy=True`` on the returned instance
  signals to any write-back path (B3 ``dark-factory hints init``) that
  the object must not be silently round-tripped to disk — consumers
  that persist MUST check this flag and refuse.

* **No regex compilation here.** ``consistency_rules[].spec_patterns`` and
  ``forbidden_env`` stay as raw strings. Compilation happens in the
  adapter / consistency layer so regex errors surface with the rule name
  in context (B5). Keeping this module regex-free also means an
  unrelated regex bug can't poison project-hint loading.

* **Frozen dataclasses throughout.** Callers treat ``ProjectHints`` as a
  read-only snapshot; mutation is a layering bug.
"""

from __future__ import annotations

import json
import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from darkfactory.errors import ConfigError

log = logging.getLogger(__name__)


# ----- Schema ----------------------------------------------------------------

# Schema version of the on-disk TOML. Bumped when a field is renamed or
# semantics change. Adding a new optional field does NOT require a bump.
CURRENT_SCHEMA_VERSION = 1

# Name of the artifact at the repo root of a project.
PROJECT_HINTS_FILENAME = "project-hints.toml"

# Keys recognized at each level of the TOML. Anything outside these triggers
# a forward-compat warning. Listed here so adding a field is one edit.
_DETECTED_KEYS: frozenset[str] = frozenset({
    "schema_version",
    "language",
    "package_manager",
    "workspace_layout",
    "typescript",
    "test_runner",
    "active_adapters",
    "lockfiles",
})

_POLICY_TOP_KEYS: frozenset[str] = frozenset({
    "workspace",
    "migrations",
    "consistency_rules",
})

_WORKSPACE_KEYS: frozenset[str] = frozenset({"scope", "banned_recursive"})
_MIGRATIONS_KEYS: frozenset[str] = frozenset({"mode", "command"})
_CONSISTENCY_RULE_KEYS: frozenset[str] = frozenset({
    "name",
    "spec_patterns",
    "forbidden_env",
    "env_paths",
    "description",
})

_TOP_LEVEL_KEYS: frozenset[str] = frozenset({"detected", "policy"})


# ----- Dataclasses -----------------------------------------------------------


@dataclass(frozen=True)
class ConsistencyRuleSpec:
    """TOML-shaped consistency rule.

    Raw strings — regex compilation is deferred to the consumer so that
    rule authoring errors (bad regex) fail at check-time with the rule's
    ``name`` in the error message, not here during load.
    """

    name: str
    spec_patterns: tuple[str, ...] = ()
    forbidden_env: tuple[str, ...] = ()
    env_paths: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True)
class WorkspacePolicy:
    """Monorepo / package-manager workspace scoping rules.

    ``scope`` is a directory-relative path the project considers the
    primary workspace (e.g. ``apps/api``); ``banned_recursive`` lists
    command prefixes that must not appear in amendment ops — used by B5
    to reject commands like ``pnpm -r build`` that would walk every
    package in a monorepo when the spec scopes work to one.
    """

    scope: str = ""
    banned_recursive: tuple[str, ...] = ()


@dataclass(frozen=True)
class MigrationsPolicy:
    """Migration-discipline policy for the project's DB layer.

    ``mode`` is free-form short-form (e.g. ``"cli-only"``, ``"on-start"``);
    ``command`` is the canonical CLI the architect should place in
    ``commands.setup`` when ``mode`` indicates CLI-driven migrations.
    """

    mode: str = ""
    command: str = ""


@dataclass(frozen=True)
class PolicySection:
    workspace: WorkspacePolicy = field(default_factory=WorkspacePolicy)
    migrations: MigrationsPolicy = field(default_factory=MigrationsPolicy)
    consistency_rules: tuple[ConsistencyRuleSpec, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True iff every policy field is at its default.

        Implemented via dataclass value equality so a new policy field
        automatically participates (no second place to keep in sync).
        """
        return self == PolicySection()


@dataclass(frozen=True)
class DetectedFacts:
    """Auto-written facts about the project. Refreshed by
    ``dark-factory init --update``; never hand-edited in practice.

    ``active_adapters`` holds **adapter registry names** (e.g.
    ``"typescript-node"``, ``"postgres"``) — not language names and not
    file paths. A5 resolves each entry against the adapter registry and
    fails fast if any name is unregistered.
    """

    schema_version: int = CURRENT_SCHEMA_VERSION
    language: str = ""
    package_manager: str = ""
    workspace_layout: str = ""
    typescript: bool = False
    test_runner: str = ""
    active_adapters: tuple[str, ...] = ()
    lockfiles: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProjectHints:
    """Top-level typed view of ``.dark-factory/project-hints.toml``.

    An ``empty`` instance (no file present and no legacy synthesis) is a
    valid return value — callers must handle it, not assume presence.
    """

    detected: DetectedFacts = field(default_factory=DetectedFacts)
    policy: PolicySection = field(default_factory=PolicySection)
    source_path: Path | None = None
    # True when this instance was synthesized from legacy
    # environment.toml + hints.json rather than read from project-hints.toml.
    # Consumers that persist back to disk MUST check this before writing —
    # a synthesized instance should be written via `dark-factory init
    # --migrate-hints`, not silently round-tripped.
    synthesized_from_legacy: bool = False

    @classmethod
    def empty(cls) -> "ProjectHints":
        return cls()


# ----- Loader ----------------------------------------------------------------


def load_project_hints(project_dir: Path) -> ProjectHints:
    """Load project-hints for a given project directory.

    Resolution order:
      1. ``<project_dir>/.dark-factory/project-hints.toml`` — parse + validate.
      2. Legacy fallback: synthesize ``[detected]`` from
         ``.dark-factory/architecture/environment.toml`` +
         ``.dark-factory/architecture/hints.json`` if either exists.
      3. Empty ``ProjectHints``.

    Raises:
        ConfigError: on TOML parse failure or structural validation failure
            (wrong type, malformed array-of-tables, missing required field
            on a consistency rule, etc.).

    Logging:
        * WARN on unknown top-level section or unknown key inside a known
          section (forward-compat).
        * WARN once when synthesizing from legacy files, pointing the user
          at the migration command.
    """
    path = _project_hints_path(project_dir)
    if path.is_file():
        return _load_from_toml(path)

    # Legacy fallback — synthesize [detected] only.
    legacy = _synthesize_from_legacy(project_dir)
    if legacy is not None:
        log.warning(
            "no %s found; synthesized [detected] from legacy environment.toml "
            "+ hints.json. run `dark-factory init --migrate-hints` to "
            "consolidate; [policy] will be empty until then.",
            PROJECT_HINTS_FILENAME,
        )
        return legacy

    return ProjectHints.empty()


def _project_hints_path(project_dir: Path) -> Path:
    return project_dir / ".dark-factory" / PROJECT_HINTS_FILENAME


def _load_from_toml(path: Path) -> ProjectHints:
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except OSError as e:
        raise ConfigError(f"{path}: read failed: {e}") from e
    except tomllib.TOMLDecodeError as e:
        # tomllib error messages include a position — surface them verbatim.
        raise ConfigError(f"{path}: invalid TOML: {e}") from e

    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: top-level must be a table")

    _warn_unknown_keys(path, "", raw.keys(), _TOP_LEVEL_KEYS)

    detected = _parse_detected(path, raw.get("detected", {}))
    policy = _parse_policy(path, raw.get("policy", {}))
    return ProjectHints(detected=detected, policy=policy, source_path=path)


def _parse_detected(path: Path, raw: Any) -> DetectedFacts:
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: [detected] must be a table, got {_typename(raw)}")
    _warn_unknown_keys(path, "detected", raw.keys(), _DETECTED_KEYS)

    schema_version = _require_type(
        path, "detected.schema_version",
        raw.get("schema_version", CURRENT_SCHEMA_VERSION), int,
    )
    if schema_version > CURRENT_SCHEMA_VERSION:
        log.warning(
            "%s: detected.schema_version=%d is newer than supported=%d; "
            "some fields may be ignored",
            path, schema_version, CURRENT_SCHEMA_VERSION,
        )

    return DetectedFacts(
        schema_version=schema_version,
        language=_require_type(
            path, "detected.language", raw.get("language", ""), str,
        ),
        package_manager=_require_type(
            path, "detected.package_manager",
            raw.get("package_manager", ""), str,
        ),
        workspace_layout=_require_type(
            path, "detected.workspace_layout",
            raw.get("workspace_layout", ""), str,
        ),
        typescript=_require_type(
            path, "detected.typescript",
            raw.get("typescript", False), bool,
        ),
        test_runner=_require_type(
            path, "detected.test_runner", raw.get("test_runner", ""), str,
        ),
        active_adapters=_require_str_tuple(
            path, "detected.active_adapters", raw.get("active_adapters", []),
        ),
        lockfiles=_require_str_tuple(
            path, "detected.lockfiles", raw.get("lockfiles", []),
        ),
    )


def _parse_policy(path: Path, raw: Any) -> PolicySection:
    # Default to empty section when the user omits [policy] entirely.
    # Distinction: "missing" is {}, "wrong type" is non-dict — the latter
    # must raise. ``if not raw:`` would swallow a bogus ``policy = []`` so
    # we test dict-ness first and let an empty dict fall through.
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path}: [policy] must be a table, got {_typename(raw)}"
        )
    if not raw:
        return PolicySection()
    _warn_unknown_keys(path, "policy", raw.keys(), _POLICY_TOP_KEYS)

    workspace = _parse_workspace(path, raw.get("workspace", {}))
    migrations = _parse_migrations(path, raw.get("migrations", {}))
    rules = _parse_consistency_rules(path, raw.get("consistency_rules", []))
    return PolicySection(
        workspace=workspace,
        migrations=migrations,
        consistency_rules=rules,
    )


def _parse_workspace(path: Path, raw: Any) -> WorkspacePolicy:
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path}: [policy.workspace] must be a table, got {_typename(raw)}"
        )
    if not raw:
        return WorkspacePolicy()
    _warn_unknown_keys(path, "policy.workspace", raw.keys(), _WORKSPACE_KEYS)
    return WorkspacePolicy(
        scope=_require_type(
            path, "policy.workspace.scope", raw.get("scope", ""), str,
        ),
        banned_recursive=_require_str_tuple(
            path, "policy.workspace.banned_recursive",
            raw.get("banned_recursive", []),
        ),
    )


def _parse_migrations(path: Path, raw: Any) -> MigrationsPolicy:
    if not isinstance(raw, dict):
        raise ConfigError(
            f"{path}: [policy.migrations] must be a table, got {_typename(raw)}"
        )
    if not raw:
        return MigrationsPolicy()
    _warn_unknown_keys(path, "policy.migrations", raw.keys(), _MIGRATIONS_KEYS)
    return MigrationsPolicy(
        mode=_require_type(
            path, "policy.migrations.mode", raw.get("mode", ""), str,
        ),
        command=_require_type(
            path, "policy.migrations.command", raw.get("command", ""), str,
        ),
    )


def _parse_consistency_rules(
    path: Path, raw: Any,
) -> tuple[ConsistencyRuleSpec, ...]:
    if not raw:
        return ()
    if not isinstance(raw, list):
        raise ConfigError(
            f"{path}: [[policy.consistency_rules]] must be an array of "
            f"tables, got {_typename(raw)}"
        )
    rules: list[ConsistencyRuleSpec] = []
    seen_names: set[str] = set()
    for i, entry in enumerate(raw):
        if not isinstance(entry, dict):
            raise ConfigError(
                f"{path}: policy.consistency_rules[{i}] must be a table, "
                f"got {_typename(entry)}"
            )
        _warn_unknown_keys(
            path, f"policy.consistency_rules[{i}]",
            entry.keys(), _CONSISTENCY_RULE_KEYS,
        )
        # Name is required and uniqueness-enforced: two rules with the same
        # name are almost always a copy-paste bug, and downstream the name
        # is used as a dedup key in C1 events.
        if "name" not in entry:
            raise ConfigError(
                f"{path}: policy.consistency_rules[{i}] missing required "
                f"field 'name'"
            )
        name = _require_type(
            path, f"policy.consistency_rules[{i}].name",
            entry["name"], str,
        )
        if not name:
            raise ConfigError(
                f"{path}: policy.consistency_rules[{i}].name must be "
                f"non-empty"
            )
        if name in seen_names:
            raise ConfigError(
                f"{path}: duplicate consistency rule name {name!r} "
                f"(index {i})"
            )
        seen_names.add(name)

        rules.append(ConsistencyRuleSpec(
            name=name,
            spec_patterns=_require_str_tuple(
                path, f"policy.consistency_rules[{i}].spec_patterns",
                entry.get("spec_patterns", []),
            ),
            forbidden_env=_require_str_tuple(
                path, f"policy.consistency_rules[{i}].forbidden_env",
                entry.get("forbidden_env", []),
            ),
            env_paths=_require_str_tuple(
                path, f"policy.consistency_rules[{i}].env_paths",
                entry.get("env_paths", []),
            ),
            description=_require_type(
                path, f"policy.consistency_rules[{i}].description",
                entry.get("description", ""), str,
            ),
        ))
    return tuple(rules)


# ----- Legacy synthesis ------------------------------------------------------


def _synthesize_from_legacy(project_dir: Path) -> ProjectHints | None:
    """Synthesize a ProjectHints.detected from the legacy pair.

    Returns None if neither legacy file exists. Only reads
    ``hints.json`` (the structured detection artifact); does NOT sniff
    ``environment.toml`` commands for ``typescript`` / ``test_runner``
    signals — that kind of heuristic ("does ``commands.test`` contain
    the substring ``jest``?") is exactly how detection-by-substring
    bugs ship. Migration CLI (B2) owns authoritative re-detection.
    Transitional callers of this synthesized instance see empty strings
    for those fields; the WARN in ``load_project_hints`` tells them
    why.

    ``source_path`` is left None so callers can detect synthesized
    instances via the dedicated ``synthesized_from_legacy`` flag.
    """
    arch = project_dir / ".dark-factory" / "architecture"
    env_toml = arch / "environment.toml"
    hints_json = arch / "hints.json"
    if not env_toml.is_file() and not hints_json.is_file():
        return None

    language = ""
    package_manager = ""
    workspace_layout = ""
    lockfiles: tuple[str, ...] = ()

    # NOTE: ``environment.toml`` presence alone triggers synthesis (the
    # outer ``is_file`` guard above), but we intentionally do not read it
    # here. Its command fields would only be usable via substring
    # heuristics (did ``commands.test`` contain ``pytest``?) which is
    # exactly the kind of brittle logic that belongs in the migration
    # detector, not the loader. When only environment.toml exists and
    # hints.json does not, this function returns a ProjectHints with
    # empty DetectedFacts — the migration CLI owns re-detection.
    if hints_json.is_file():
        try:
            hints_data = json.loads(hints_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            log.warning("hints.json unreadable during legacy synthesis: %s", e)
            hints_data = {}
        if isinstance(hints_data, dict):
            language = _coerce_str("hints.json.language", hints_data.get("language"))
            package_manager = _coerce_str(
                "hints.json.package_manager", hints_data.get("package_manager"),
            )
            workspace_layout = _coerce_str(
                "hints.json.workspace_layout", hints_data.get("workspace_layout"),
            )
            locks_raw = hints_data.get("lockfiles", [])
            if isinstance(locks_raw, list):
                lockfiles = tuple(
                    str(x) for x in locks_raw if isinstance(x, str) and x
                )

    detected = DetectedFacts(
        language=language,
        package_manager=package_manager,
        workspace_layout=workspace_layout,
        # typescript / test_runner are intentionally left at defaults —
        # migration CLI re-detects them from the filesystem.
        lockfiles=lockfiles,
        # active_adapters stays empty — legacy files didn't declare them;
        # migration CLI fills this from the registry.
        active_adapters=(),
    )
    return ProjectHints(
        detected=detected,
        policy=PolicySection(),
        source_path=None,
        synthesized_from_legacy=True,
    )


# ----- Helpers ---------------------------------------------------------------


def _warn_unknown_keys(
    path: Path, section: str, keys: Any, known: frozenset[str],
) -> None:
    """Log unknown keys under a section. Forward-compat; never raises."""
    unknown = [k for k in keys if k not in known]
    if not unknown:
        return
    where = f"[{section}]" if section else "top level"
    log.warning(
        "%s: unknown keys under %s: %s (ignored; consider upgrading "
        "dark-factory or removing the keys)",
        path, where, ", ".join(sorted(unknown)),
    )


def _typename(value: Any) -> str:
    return type(value).__name__


def _require_type(
    path: Path,
    dotted: str,
    value: Any,
    expected: type,
) -> Any:
    """Assert ``value`` is of ``expected`` type; else raise ConfigError.

    TOML has no ``null``, so every call site resolves the absence case
    via ``raw.get(key, <python_default>)`` before reaching here — this
    helper is strict: the value is always present and always checked.
    """
    # bool is a subclass of int; TOML distinguishes them. Refuse bool where int
    # is required (and vice-versa) so `schema_version = true` doesn't coerce.
    if expected is int and isinstance(value, bool):
        raise ConfigError(
            f"{path}: {dotted} expected int, got bool"
        )
    if expected is bool and not isinstance(value, bool):
        raise ConfigError(
            f"{path}: {dotted} expected bool, got {_typename(value)}"
        )
    if not isinstance(value, expected):
        raise ConfigError(
            f"{path}: {dotted} expected {expected.__name__}, "
            f"got {_typename(value)}"
        )
    return value


def _require_str_tuple(
    path: Path, dotted: str, value: Any,
) -> tuple[str, ...]:
    """Accept a TOML list of non-empty strings; return as an immutable tuple."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(
            f"{path}: {dotted} expected array, got {_typename(value)}"
        )
    out: list[str] = []
    for i, item in enumerate(value):
        if not isinstance(item, str):
            raise ConfigError(
                f"{path}: {dotted}[{i}] expected string, got {_typename(item)}"
            )
        if not item:
            raise ConfigError(
                f"{path}: {dotted}[{i}] must be non-empty"
            )
        out.append(item)
    return tuple(out)


def _coerce_str(field_label: str, value: Any) -> str:
    """Best-effort string coerce used only during legacy synthesis, where
    malformed values should not raise.

    Logs at DEBUG when a non-string is dropped so a 2-AM debugger can
    find the culprit without adding `print`s.
    """
    if isinstance(value, str):
        return value
    if value is not None:
        log.debug(
            "legacy synthesis: dropped non-string %s=%r (%s)",
            field_label, value, _typename(value),
        )
    return ""
