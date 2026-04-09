"""Environment contract dataclass + canonical TOML serializer.

Phase 2.1 of the pipeline resilience rewrite. The contract is a structured
view of the runtime/build/test environment that the Architect amends and the
Implementer reads. It is materialized from an append-only delta log; the
file on disk (`.dark-factory/architecture/environment.toml`) is regenerated
on every fold and never hand-edited.

`canonical_dump` is a hand-rolled deterministic TOML writer (NOT a wrapper
around tomli_w / tomlkit). Determinism is required because the parent_hash
field on each delta is sha256(canonical_dump(view_at_read_time)) — if the
serializer drifts by a single byte, every in-flight delta becomes invalid.

Semantic note: the contract has no notion of "unset". A field that has never
been amended is serialized at its dataclass default (empty string, 0, empty
list). "Unset" and "explicitly set to default" are deliberately
indistinguishable so hash stability is total. Bootstrap amendments must
therefore always emit a complete payload.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass
from typing import Any


# ----- Allowlist -------------------------------------------------------------

# Strict allowlist of dotted paths an amendment may write. Anything else
# (notably test.*, scenario.*, oracle.*) is rejected at validate time so the
# environment contract cannot become a barrier-leak channel.
# Expected value type for each allowed path. Used by validate_environment to
# reject schema-poison inputs (e.g. `server.port = "8000"` as a string) before
# they reach canonical_dump and explode at hash time.
PATH_TYPES: dict[str, type] = {
    "runtime.language": str,
    "runtime.package_manager": str,
    "runtime.language_version": str,
    "commands.install": str,
    "commands.server_start": str,
    "commands.server_health": str,
    "commands.build": str,
    "commands.lint": str,
    "commands.typecheck": str,
    "commands.test": str,
    "server.port": int,
    "server.health_path": str,
    "server.boot_timeout_s": int,
    "server.test_base_url": str,
    "layout.source_dirs": list,
    "layout.test_dirs": list,
}

ALLOWED_PATHS: frozenset[str] = frozenset({
    "runtime.language",
    "runtime.package_manager",
    "runtime.language_version",
    "commands.install",
    "commands.server_start",
    "commands.server_health",
    "commands.build",
    "commands.lint",
    "commands.typecheck",
    "commands.test",
    "server.port",
    "server.health_path",
    "server.boot_timeout_s",
    "server.test_base_url",
    "layout.source_dirs",
    "layout.test_dirs",
})

# Dynamic path pattern for service fields. Service names must be DNS-safe
# (lowercase alphanumeric + hyphens, matching ServiceDef.__post_init__ in
# config.py). Validated fields mirror ServiceEntry dataclass attributes.
_SERVICE_FIELD_PATH_RE = re.compile(
    r"^services\.[a-z0-9]([a-z0-9-]*[a-z0-9])?\."
    r"(image|env|health_cmd|health_timeout|inject_env|test_only|depends_on)$"
)

# 2-level service path: services.<name> (used for remove-entire-service ops).
_SERVICE_ENTRY_PATH_RE = re.compile(
    r"^services\.[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
)

_SERVICE_FIELD_TYPES: dict[str, type] = {
    "image": str,
    "env": dict,
    "health_cmd": str,
    "health_timeout": int,
    "inject_env": dict,
    "test_only": bool,
    "depends_on": list,
}


def is_allowed_path(path: str) -> bool:
    """Check if a dotted path is in the static or dynamic allowlist."""
    if path in ALLOWED_PATHS:
        return True
    return bool(_SERVICE_FIELD_PATH_RE.match(path) or _SERVICE_ENTRY_PATH_RE.match(path))

# Paths only a bootstrap spec may write. Feature/bugfix amendments touching
# these are rejected.
BOOTSTRAP_ONLY_PATHS: frozenset[str] = frozenset({
    "runtime.language",
    "layout.source_dirs",
})


# ----- Dataclasses -----------------------------------------------------------


@dataclass
class Runtime:
    language: str = ""
    package_manager: str = ""
    language_version: str = ""


@dataclass
class Commands:
    install: str = ""
    server_start: str = ""
    server_health: str = ""
    build: str = ""
    lint: str = ""
    typecheck: str = ""
    test: str = ""


@dataclass
class ServerConfig:
    port: int = 0
    health_path: str = ""
    boot_timeout_s: int = 0
    test_base_url: str = ""


@dataclass
class Layout:
    source_dirs: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)


@dataclass
class ServiceEntry:
    """A service declared in the environment contract (e.g. PostgreSQL, Redis).

    ``depends_on`` drives topological startup ordering in quality_gate.py
    (Phase 4.3). ``test_only`` serializes correctly (omitted when False,
    emitted when True) but deployment exclusion is deferred to Phase 6.
    Both fields were included in Phase 4.1 to avoid a second hash-breaking
    schema change.
    """
    image: str = ""
    env: dict[str, str] = field(default_factory=dict)
    health_cmd: str = ""
    health_timeout: int = 30
    inject_env: dict[str, str] = field(default_factory=dict)
    test_only: bool = False
    depends_on: list[str] = field(default_factory=list)


@dataclass
class EnvironmentContract:
    runtime: Runtime = field(default_factory=Runtime)
    commands: Commands = field(default_factory=Commands)
    server: ServerConfig = field(default_factory=ServerConfig)
    layout: Layout = field(default_factory=Layout)
    services: dict[str, ServiceEntry] = field(default_factory=dict)


# ----- Validation ------------------------------------------------------------


@dataclass
class ValidationError:
    path: str
    reason: str


def _expected_type_for_path(path: str) -> type | None:
    """Return the expected value type for a dotted path, or None if unknown."""
    if path in PATH_TYPES:
        return PATH_TYPES[path]
    m = _SERVICE_FIELD_PATH_RE.match(path)
    if m:
        field_name = m.group(2)
        return _SERVICE_FIELD_TYPES.get(field_name)
    return None


def validate_environment(
    amendment: dict[str, Any],
    spec_type: str,
) -> list[ValidationError]:
    """Validate an amendment payload (flat dotted-path dict) against allowlist.

    `spec_type` is one of "bootstrap" / "feature" / "bugfix". Bootstrap may
    write any allowed path; feature/bugfix may not touch BOOTSTRAP_ONLY_PATHS.
    Service paths (``services.<name>.<field>``) are allowed for all spec types.
    """
    errors: list[ValidationError] = []
    for path, value in amendment.items():
        if not is_allowed_path(path):
            errors.append(ValidationError(path, "path not in allowlist"))
            continue
        if spec_type != "bootstrap" and path in BOOTSTRAP_ONLY_PATHS:
            errors.append(ValidationError(
                path,
                f"path is bootstrap-only (spec_type={spec_type})",
            ))
            continue
        # Type check. bool is rejected for int slots since bool is a subclass.
        expected = _expected_type_for_path(path)
        if expected is None:
            errors.append(ValidationError(path, "unknown type for path"))
            continue
        if expected is int:
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(ValidationError(
                    path, f"expected int, got {type(value).__name__}",
                ))
                continue
        elif expected is bool:
            if not isinstance(value, bool):
                errors.append(ValidationError(
                    path, f"expected bool, got {type(value).__name__}",
                ))
                continue
        elif expected is list:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                errors.append(ValidationError(
                    path, f"expected list[str], got {type(value).__name__}",
                ))
                continue
        elif expected is dict:
            if not isinstance(value, dict):
                errors.append(ValidationError(
                    path, f"expected dict, got {type(value).__name__}",
                ))
                continue
            if not all(isinstance(k, str) and isinstance(v, str)
                       for k, v in value.items()):
                errors.append(ValidationError(
                    path, "expected dict[str, str]",
                ))
                continue
        elif not isinstance(value, expected):
            errors.append(ValidationError(
                path, f"expected {expected.__name__}, got {type(value).__name__}",
            ))
    return errors


# ----- Canonical dump --------------------------------------------------------


def _escape_string(value: str) -> str:
    """Escape a string for TOML double-quoted form. ASCII-only output.

    Only `\\` and `"` are escaped via backslash. Control characters and
    non-ASCII are escaped as `\\uXXXX`. Newlines / tabs / carriage returns
    likewise — keeps the writer trivially deterministic with no special
    cases for multiline strings.
    """
    out: list[str] = []
    for ch in value:
        code = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif 0x20 <= code < 0x7F:
            out.append(ch)
        elif code <= 0xFFFF:
            out.append(f"\\u{code:04X}")
        else:
            out.append(f"\\U{code:08X}")
    return '"' + "".join(out) + '"'


def _format_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _escape_string(value)
    raise TypeError(f"unsupported scalar type for canonical_dump: {type(value).__name__}")


def _format_list(values: list[Any]) -> str:
    parts = [_format_scalar(v) for v in values]
    if not parts:
        return "[]"
    return "[ " + ", ".join(parts) + " ]"


def _dataclass_to_dict(obj: Any) -> dict[str, Any]:
    """Convert a dataclass to a plain dict (one level)."""
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


# Fields added to EXISTING dataclass sections after Phase 2.1 that must be
# omitted from canonical_dump when at their default value. Without this,
# adding a field to e.g. ServerConfig would change the output for every
# contract — even empty ones — breaking every delta chain's parent_hash.
#
# INVARIANT: every field added to an existing section after the initial
# schema (Phase 2.1) must be registered here. The `services` dict is handled
# separately (skipped when empty) since it's a new top-level section, not a
# field added to an existing one.
_PHASE4_NEW_FIELDS: frozenset[tuple[str, str]] = frozenset({
    ("server", "test_base_url"),
})


def _is_phase4_default(section_name: str, key: str, value: Any) -> bool:
    """True if a Phase 4 field is at its dataclass default (must not be serialized)."""
    if (section_name, key) not in _PHASE4_NEW_FIELDS:
        return False
    if isinstance(value, str):
        return value == ""
    return False


def _dump_service_entry(
    service_name: str,
    entry: Any,
    lines: list[str],
) -> None:
    """Serialize one ServiceEntry under [services.<name>].

    TOML rule: once a sub-table header (``[services.postgres.env]``) appears,
    subsequent bare keys go under that sub-table. So we must emit ALL scalar
    and list fields first, then dict sub-tables last.
    """
    lines.append(f"[services.{service_name}]")
    entry_dict = _dataclass_to_dict(entry)
    fresh = type(entry)()
    # First pass: scalars and lists (sorted).
    for key in sorted(entry_dict):
        value = entry_dict[key]
        default = getattr(fresh, key)
        if value == default:
            continue
        if isinstance(value, dict):
            continue  # deferred to second pass
        if isinstance(value, list):
            lines.append(f"{key} = {_format_list(value)}")
        elif isinstance(value, bool):
            lines.append(f"{key} = {_format_scalar(value)}")
        elif isinstance(value, (str, int)):
            lines.append(f"{key} = {_format_scalar(value)}")
    # Second pass: dict sub-tables (sorted by key name).
    for key in sorted(entry_dict):
        value = entry_dict[key]
        default = getattr(fresh, key)
        if value == default:
            continue
        if isinstance(value, dict) and value:
            lines.append(f"[services.{service_name}.{key}]")
            for dk in sorted(value):
                lines.append(f"{dk} = {_escape_string(value[dk])}")


def canonical_dump(contract: EnvironmentContract) -> bytes:
    """Hand-rolled deterministic TOML serializer.

    - Lexicographically-sorted keys at every level.
    - ASCII-only.
    - Double-quoted strings with `\\` and `\"` escapes; non-ASCII as `\\uXXXX`.
    - Integers as decimal; booleans lowercase.
    - Lists as `[ "a", "b" ]` with one space after each comma.
    - No inline tables, no comments.
    - LF line endings, trailing newline.

    Hash stability: fields added in Phase 4 (``test_base_url``, ``services``)
    are omitted when at their dataclass default so that
    ``hash_view(EnvironmentContract())`` is identical before and after Phase 4.
    """
    if not is_dataclass(contract):
        raise TypeError("canonical_dump expects an EnvironmentContract dataclass")

    top = _dataclass_to_dict(contract)
    lines: list[str] = []

    for section_name in sorted(top):
        section = top[section_name]

        # services is a dict[str, ServiceEntry], not a flat dataclass section.
        # Skip entirely when empty (hash stability).
        if section_name == "services":
            if not section:
                continue
            for svc_name in sorted(section):
                _dump_service_entry(svc_name, section[svc_name], lines)
            lines.append("")
            continue

        if not is_dataclass(section):
            raise TypeError(
                f"canonical_dump: section {section_name!r} is not a dataclass"
            )
        lines.append(f"[{section_name}]")
        section_dict = _dataclass_to_dict(section)
        for key in sorted(section_dict):
            value = section_dict[key]
            # Phase 4 new fields: skip when at default to preserve hash.
            if _is_phase4_default(section_name, key, value):
                continue
            if isinstance(value, list):
                rendered = _format_list(value)
            else:
                rendered = _format_scalar(value)
            lines.append(f"{key} = {rendered}")
        lines.append("")  # blank line between sections

    text = "\n".join(lines).rstrip("\n") + "\n"
    return text.encode("ascii")
