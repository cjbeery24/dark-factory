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
    "layout.source_dirs",
    "layout.test_dirs",
})

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


@dataclass
class Layout:
    source_dirs: list[str] = field(default_factory=list)
    test_dirs: list[str] = field(default_factory=list)


@dataclass
class EnvironmentContract:
    runtime: Runtime = field(default_factory=Runtime)
    commands: Commands = field(default_factory=Commands)
    server: ServerConfig = field(default_factory=ServerConfig)
    layout: Layout = field(default_factory=Layout)


# ----- Validation ------------------------------------------------------------


@dataclass
class ValidationError:
    path: str
    reason: str


def validate_environment(
    amendment: dict[str, Any],
    spec_type: str,
) -> list[ValidationError]:
    """Validate an amendment payload (flat dotted-path dict) against allowlist.

    `spec_type` is one of "bootstrap" / "feature" / "bugfix". Bootstrap may
    write any allowed path; feature/bugfix may not touch BOOTSTRAP_ONLY_PATHS.
    """
    errors: list[ValidationError] = []
    for path, value in amendment.items():
        if path not in ALLOWED_PATHS:
            errors.append(ValidationError(path, "path not in allowlist"))
            continue
        if spec_type != "bootstrap" and path in BOOTSTRAP_ONLY_PATHS:
            errors.append(ValidationError(
                path,
                f"path is bootstrap-only (spec_type={spec_type})",
            ))
            continue
        # Type check. bool is rejected for int slots since bool is a subclass.
        expected = PATH_TYPES[path]
        if expected is int:
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(ValidationError(
                    path, f"expected int, got {type(value).__name__}",
                ))
                continue
        elif expected is list:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                errors.append(ValidationError(
                    path, f"expected list[str], got {type(value).__name__}",
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


def canonical_dump(contract: EnvironmentContract) -> bytes:
    """Hand-rolled deterministic TOML serializer.

    - Lexicographically-sorted keys at every level.
    - ASCII-only.
    - Double-quoted strings with `\\` and `\"` escapes; non-ASCII as `\\uXXXX`.
    - Integers as decimal; booleans lowercase.
    - Lists as `[ "a", "b" ]` with one space after each comma.
    - No inline tables, no comments.
    - LF line endings, trailing newline.
    """
    if not is_dataclass(contract):
        raise TypeError("canonical_dump expects an EnvironmentContract dataclass")

    top = _dataclass_to_dict(contract)
    lines: list[str] = []

    for section_name in sorted(top):
        section = top[section_name]
        if not is_dataclass(section):
            raise TypeError(
                f"canonical_dump: section {section_name!r} is not a dataclass"
            )
        lines.append(f"[{section_name}]")
        section_dict = _dataclass_to_dict(section)
        for key in sorted(section_dict):
            value = section_dict[key]
            if isinstance(value, list):
                rendered = _format_list(value)
            else:
                rendered = _format_scalar(value)
            lines.append(f"{key} = {rendered}")
        lines.append("")  # blank line between sections

    text = "\n".join(lines).rstrip("\n") + "\n"
    return text.encode("ascii")
