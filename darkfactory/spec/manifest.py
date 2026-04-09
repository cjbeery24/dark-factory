"""Parse, validate, and format Architecture Lock manifests.

The Architecture Lock phase produces a structured YAML manifest that both the
Scenario Architect and Implementer consume as a shared structural contract.
This module handles parsing, validation, and formatting of those manifests.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

_CD_RE = re.compile(r'\bcd\s+["\']?([^"\';&\s]+)["\']?')

# Config files that should live under the server CWD, not project root.
_CWD_CONFIG_RE = re.compile(
    r'(?:drizzle\.config|vitest\.config|jest\.config|webpack\.config|vite\.config|\.env)(?:\.|$)'
)


def _extract_cwd(rt: "RuntimeDecl") -> str | None:
    """Extract CWD directory from runtime server commands, if any ``cd <dir>`` is present."""
    for cmd in (rt.server_setup_cmd, rt.test_server_cmd):
        if cmd:
            m = _CD_RE.search(cmd)
            if m:
                return m.group(1).rstrip("/")
    return None


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

VALID_ACTIONS = frozenset({"create", "modify", "delete"})
VALID_HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_FR_RE = re.compile(r"FR-\d+")


@dataclass
class FileEntry:
    path: str
    action: str  # create | modify | delete
    purpose: str = ""
    satisfies: list[str] = field(default_factory=list)
    existing_summary: str = ""  # context for modify actions


@dataclass
class DataModelField:
    name: str
    type: str
    constraints: list[str] = field(default_factory=list)


@dataclass
class DataModel:
    name: str
    location: str
    fields: list[DataModelField] = field(default_factory=list)
    relationships: list[str] = field(default_factory=list)


@dataclass
class ApiContract:
    method: str
    path: str
    request_body: dict = field(default_factory=dict)
    response: dict = field(default_factory=dict)
    satisfies: list[str] = field(default_factory=list)


@dataclass
class Dependency:
    package: str
    version: str = ""
    target: str = ""
    purpose: str = ""
    dev: bool = False
    satisfies: list[str] = field(default_factory=list)


@dataclass
class EnvironmentReq:
    tool: str
    min_version: str = ""
    required_by: str = ""
    install_via: str = ""


@dataclass
class RuntimeServiceDecl:
    """A service declared in the manifest's runtime section."""
    name: str
    image: str
    env: dict[str, str] = field(default_factory=dict)
    health_cmd: str = ""
    health_timeout: int = 30
    inject_env: dict[str, str] = field(default_factory=dict)


@dataclass
class RuntimeDecl:
    """Runtime environment declared by the Architect.

    Phase 3.3: this is a *projection* of an env-contract-shaped amendment
    payload (``amendment_ops``). The flat scalar fields remain for legacy
    consumers (``_extract_cwd``, the prompt renderer) but the source of
    truth for what gets written to the environment delta is
    ``amendment_ops`` — a dotted-path dict whose keys are members of
    ``ALLOWED_PATHS`` in environment.py.

    Phase 4: services and ``test_base_url`` are expanded into service
    delta ops (``services.<name>.<field>``) and ``server.test_base_url``
    by ``amend._build_ops``. They remain as fields here because the
    manifest YAML parser populates them and other consumers (scenario
    architect prompt, scope validation) still read them.
    """
    services: list[RuntimeServiceDecl] = field(default_factory=list)
    server_setup_cmd: str = ""
    test_server_cmd: str = ""
    test_base_url: str = ""
    server_health_path: str = ""
    server_startup_timeout: int = 0  # 0 = not set
    # Phase 3.3 fix: env-contract-shaped amendment payload. Keys are
    # dotted paths from ALLOWED_PATHS (e.g. "commands.server_start").
    # Empty dict means "no amendment" (the typical feature spec).
    amendment_ops: dict[str, object] = field(default_factory=dict)


@dataclass
class ArchitectureManifest:
    version: int = 1
    spec: str = ""
    spec_hash: str = ""
    language: str = ""
    file_map: list[FileEntry] = field(default_factory=list)
    data_models: list[DataModel] = field(default_factory=list)
    api_contracts: list[ApiContract] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    environment: list[EnvironmentReq] = field(default_factory=list)
    runtime: RuntimeDecl = field(default_factory=RuntimeDecl)


@dataclass
class ManifestParseResult:
    manifest: ArchitectureManifest | None = None
    errors: list[str] = field(default_factory=list)


@dataclass
class ManifestValidationResult:
    valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def parse_manifest(yaml_text: str) -> ManifestParseResult:
    """Parse a YAML manifest into an ArchitectureManifest."""
    result = ManifestParseResult()
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        result.errors.append(f"Malformed YAML: {exc}")
        return result

    if not isinstance(data, dict):
        result.errors.append("Manifest must be a YAML mapping")
        return result

    manifest = ArchitectureManifest()
    version = data.get("version", 1)
    if version != 1:
        result.errors.append(f"Unsupported manifest version: {version} (only version 1 is supported)")
        return result
    manifest.version = version
    manifest.spec = data.get("spec", "")
    manifest.spec_hash = data.get("spec_hash", "")
    manifest.language = data.get("language", "")

    # file_map (required)
    raw_files = data.get("file_map", [])
    if not isinstance(raw_files, list):
        result.errors.append("file_map must be a list")
        return result
    for idx, raw in enumerate(raw_files):
        entry, error = _parse_file_entry(raw, idx)
        if error:
            result.errors.append(error)
        elif entry:
            manifest.file_map.append(entry)

    # data_models (optional)
    raw_models = data.get("data_models") or []
    if raw_models and not isinstance(raw_models, list):
        result.errors.append("data_models must be a list")
        return result
    for idx, raw in enumerate(raw_models):
        model, error = _parse_data_model(raw, idx)
        if error:
            result.errors.append(error)
        elif model:
            manifest.data_models.append(model)

    # api_contracts (optional)
    raw_contracts = data.get("api_contracts") or []
    if raw_contracts and not isinstance(raw_contracts, list):
        result.errors.append("api_contracts must be a list")
        return result
    for idx, raw in enumerate(raw_contracts):
        contract, error = _parse_api_contract(raw, idx)
        if error:
            result.errors.append(error)
        elif contract:
            manifest.api_contracts.append(contract)

    # dependencies (optional)
    raw_deps = data.get("dependencies") or []
    if raw_deps and not isinstance(raw_deps, list):
        result.errors.append("dependencies must be a list")
        return result
    for idx, raw in enumerate(raw_deps):
        dep, error = _parse_dependency(raw, idx)
        if error:
            result.errors.append(error)
        elif dep:
            manifest.dependencies.append(dep)

    # environment (optional)
    raw_env = data.get("environment") or []
    if raw_env and not isinstance(raw_env, list):
        result.errors.append("environment must be a list")
        return result
    for idx, raw in enumerate(raw_env):
        req, error = _parse_environment_req(raw, idx)
        if error:
            result.errors.append(error)
        elif req:
            manifest.environment.append(req)

    # runtime (optional)
    raw_runtime = data.get("runtime")
    if raw_runtime:
        if not isinstance(raw_runtime, dict):
            result.errors.append("runtime must be a mapping")
        else:
            rt, rt_errors = _parse_runtime(raw_runtime)
            result.errors.extend(rt_errors)
            if rt:
                manifest.runtime = rt

    if not result.errors:
        result.manifest = manifest
    return result


def _parse_file_entry(raw: object, idx: int) -> tuple[FileEntry | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"file_map[{idx}]: must be a mapping"
    path = raw.get("path", "")
    action = raw.get("action", "")
    if not path:
        return None, f"file_map[{idx}]: missing 'path'"
    if not action:
        return None, f"file_map[{idx}]: missing 'action'"
    if action not in VALID_ACTIONS:
        return None, f"file_map[{idx}]: action must be create|modify|delete, got '{action}'"
    if path.startswith("/"):
        return None, f"file_map[{idx}]: path must be relative, got '{path}'"
    if ".." in path.split("/"):
        return None, f"file_map[{idx}]: path must not contain '..', got '{path}'"
    satisfies = raw.get("satisfies", [])
    if isinstance(satisfies, str):
        satisfies = [satisfies]
    return FileEntry(
        path=path,
        action=action,
        purpose=raw.get("purpose", ""),
        satisfies=satisfies or [],
        existing_summary=raw.get("existing_summary", ""),
    ), None


def _parse_data_model(raw: object, idx: int) -> tuple[DataModel | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"data_models[{idx}]: must be a mapping"
    name = raw.get("name", "")
    location = raw.get("location", "")
    if not name:
        return None, f"data_models[{idx}]: missing 'name'"
    if not location:
        return None, f"data_models[{idx}]: missing 'location'"
    fields = []
    for fidx, fraw in enumerate(raw.get("fields", []) or []):
        if not isinstance(fraw, dict):
            return None, f"data_models[{idx}].fields[{fidx}]: must be a mapping"
        fname = fraw.get("name", "")
        ftype = fraw.get("type", "")
        if not fname or not ftype:
            return None, f"data_models[{idx}].fields[{fidx}]: missing 'name' or 'type'"
        fields.append(DataModelField(
            name=fname,
            type=ftype,
            constraints=fraw.get("constraints", []) or [],
        ))
    return DataModel(
        name=name,
        location=location,
        fields=fields,
        relationships=raw.get("relationships", []) or [],
    ), None


def _parse_api_contract(raw: object, idx: int) -> tuple[ApiContract | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"api_contracts[{idx}]: must be a mapping"
    method = raw.get("method", "")
    path = raw.get("path", "")
    if not method:
        return None, f"api_contracts[{idx}]: missing 'method'"
    if not path:
        return None, f"api_contracts[{idx}]: missing 'path'"
    if method.upper() not in VALID_HTTP_METHODS:
        return None, f"api_contracts[{idx}]: invalid method '{method}'"
    if not path.startswith("/"):
        return None, f"api_contracts[{idx}]: path must start with /, got '{path}'"
    satisfies = raw.get("satisfies", [])
    if isinstance(satisfies, str):
        satisfies = [satisfies]
    # Phase 4.6b.1f: enforce dict shape on request_body/response.  Run 10
    # crashed because the architect wrote `response` as a quoted YAML string
    # containing inline JSON-like text; the type was `dict` on the dataclass
    # but parse_manifest accepted any truthy value, letting the bad shape
    # propagate into format_manifest_for_sa where `.get` crashed.  Fail loud
    # here with the specific contract identity.
    contract_id = f"{method.upper()} {path}"
    request_body = raw.get("request_body", {}) or {}
    if not isinstance(request_body, dict):
        return None, (
            f"api_contracts[{idx}] ({contract_id}): 'request_body' must be a "
            f"YAML mapping, got {type(request_body).__name__}. Write it as a "
            f"nested dict (e.g. `request_body:\\n  field: type`), not as a "
            f"quoted string."
        )
    response = raw.get("response", {}) or {}
    if not isinstance(response, dict):
        return None, (
            f"api_contracts[{idx}] ({contract_id}): 'response' must be a "
            f"YAML mapping, got {type(response).__name__}. Write it as a "
            f"nested dict (e.g. `response:\\n  status: 200\\n  body: ...`), "
            f"not as a quoted string."
        )
    return ApiContract(
        method=method.upper(),
        path=path,
        request_body=request_body,
        response=response,
        satisfies=satisfies or [],
    ), None


def _parse_dependency(raw: object, idx: int) -> tuple[Dependency | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"dependencies[{idx}]: must be a mapping"
    package = raw.get("package", "")
    if not package:
        return None, f"dependencies[{idx}]: missing 'package'"
    satisfies = raw.get("satisfies", [])
    if isinstance(satisfies, str):
        satisfies = [satisfies]
    return Dependency(
        package=package,
        version=str(raw.get("version", "")),
        target=raw.get("target", ""),
        purpose=raw.get("purpose", ""),
        dev=bool(raw.get("dev", False)),
        satisfies=satisfies or [],
    ), None


def _parse_environment_req(raw: object, idx: int) -> tuple[EnvironmentReq | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"environment[{idx}]: must be a mapping"
    tool = raw.get("tool", "")
    if not tool:
        return None, f"environment[{idx}]: missing 'tool'"
    return EnvironmentReq(
        tool=tool,
        min_version=str(raw.get("min_version", "")),
        required_by=raw.get("required_by", ""),
        install_via=raw.get("install_via", ""),
    ), None


_DNS_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")


def _parse_runtime_service(raw: object, idx: int) -> tuple[RuntimeServiceDecl | None, str | None]:
    if not isinstance(raw, dict):
        return None, f"runtime.services[{idx}]: must be a mapping"
    name = raw.get("name", "")
    if not name:
        return None, f"runtime.services[{idx}]: missing 'name'"
    if not _DNS_LABEL_RE.match(name):
        return None, (
            f"runtime.services[{idx}]: name '{name}' must be DNS-safe "
            "(lowercase alphanumeric + hyphens)"
        )
    image = raw.get("image", "")
    if not image:
        return None, f"runtime.services[{idx}]: missing 'image'"
    env = raw.get("env") or {}
    if not isinstance(env, dict):
        return None, f"runtime.services[{idx}]: env must be a mapping"
    inject_env = raw.get("inject_env") or {}
    if not isinstance(inject_env, dict):
        return None, f"runtime.services[{idx}]: inject_env must be a mapping"
    health_timeout = raw.get("health_timeout", 30)
    if not isinstance(health_timeout, int) or health_timeout < 1:
        return None, f"runtime.services[{idx}]: health_timeout must be a positive integer"
    return RuntimeServiceDecl(
        name=name,
        image=image,
        env={str(k): str(v) for k, v in env.items()},
        health_cmd=raw.get("health_cmd", ""),
        health_timeout=health_timeout,
        inject_env={str(k): str(v) for k, v in inject_env.items()},
    ), None


_NESTED_KEYS = frozenset({"language", "package_manager", "language_version",
                          "commands", "server", "layout"})
_LEGACY_FLAT_KEYS = frozenset({"server_setup_cmd", "test_server_cmd",
                               "server_health_path", "server_startup_timeout"})
_LEGACY_FLAT_TO_PATH: dict[str, str] = {
    "server_setup_cmd":      "commands.install",
    "test_server_cmd":       "commands.server_start",
    "server_health_path":    "server.health_path",
    "server_startup_timeout": "server.boot_timeout_s",
}
# Projection: env-contract path -> RuntimeDecl flat field, for the
# legacy consumers (_extract_cwd, prompt renderer) that still read
# flat scalars.
_PATH_TO_LEGACY_FLAT: dict[str, str] = {
    "commands.install":      "server_setup_cmd",
    "commands.server_start": "test_server_cmd",
    "server.health_path":    "server_health_path",
    "server.boot_timeout_s": "server_startup_timeout",
}


def _parse_runtime(raw: dict) -> tuple[RuntimeDecl | None, list[str]]:
    """Parse the runtime: block from the manifest YAML.

    Accepts the nested env-contract-shaped form (canonical, post-Phase 3.3):

        runtime:
          language: node
          package_manager: pnpm
          commands: {build: ..., server_start: ..., ...}
          server: {health_path: ..., port: ..., boot_timeout_s: ...}
          layout: {source_dirs: [...], test_dirs: [...]}

    Plus the flat top-level keys that have no env-contract counterpart and
    flow through the legacy patch path: ``services``, ``test_base_url``.

    Also accepts legacy flat keys (server_setup_cmd, test_server_cmd,
    server_health_path, server_startup_timeout) as a transitional fallback —
    they get folded into ``amendment_ops`` under their canonical paths.
    Once architect.md is fully cleaned up these can be rejected.
    """
    from darkfactory.architecture.environment import ALLOWED_PATHS, PATH_TYPES

    errors: list[str] = []
    rt = RuntimeDecl()

    # services (optional list, top-level under runtime)
    raw_services = raw.get("services") or []
    if raw_services and not isinstance(raw_services, list):
        errors.append("runtime.services must be a list")
    else:
        seen_names: set[str] = set()
        for idx, raw_svc in enumerate(raw_services):
            svc, err = _parse_runtime_service(raw_svc, idx)
            if err:
                errors.append(err)
            elif svc:
                if svc.name in seen_names:
                    errors.append(f"runtime.services: duplicate name '{svc.name}'")
                else:
                    seen_names.add(svc.name)
                    rt.services.append(svc)

    # test_base_url remains flat — no env contract slot yet (Phase 4).
    rt.test_base_url = str(raw.get("test_base_url", "") or "")

    ops: dict[str, object] = {}

    # Nested env-shaped form: language, package_manager, language_version
    # are top-level scalars under runtime: but their canonical paths are
    # runtime.language etc. Map them explicitly.
    for top_scalar, env_path in (
        ("language",         "runtime.language"),
        ("package_manager",  "runtime.package_manager"),
        ("language_version", "runtime.language_version"),
    ):
        if top_scalar in raw:
            value = raw[top_scalar]
            if value not in (None, "", 0):
                ops[env_path] = value

    # Nested tables: commands, server, layout. Each key under them
    # becomes <table>.<key>.
    for table_name in ("commands", "server", "layout"):
        sub = raw.get(table_name)
        if sub is None:
            continue
        if not isinstance(sub, dict):
            errors.append(f"runtime.{table_name} must be a mapping")
            continue
        for key, value in sub.items():
            path = f"{table_name}.{key}"
            if value in (None, "", 0, []):
                continue
            ops[path] = value

    # Legacy flat fallback: accept the old flat keys, fold into ops.
    for legacy_key, env_path in _LEGACY_FLAT_TO_PATH.items():
        if legacy_key in raw and env_path not in ops:
            value = raw[legacy_key]
            if value in (None, "", 0):
                continue
            if legacy_key == "server_startup_timeout":
                if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                    errors.append(
                        "runtime.server_startup_timeout must be a positive integer"
                    )
                    continue
            ops[env_path] = value

    # Validate every path against the env contract allowlist + types.
    for path, value in list(ops.items()):
        if path not in ALLOWED_PATHS:
            errors.append(f"runtime amendment path not allowed: {path!r}")
            ops.pop(path, None)
            continue
        expected = PATH_TYPES[path]
        if expected is int:
            if not isinstance(value, int) or isinstance(value, bool):
                errors.append(
                    f"runtime amendment {path}: expected int, got {type(value).__name__}"
                )
                ops.pop(path, None)
                continue
        elif expected is list:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                errors.append(
                    f"runtime amendment {path}: expected list[str], got {type(value).__name__}"
                )
                ops.pop(path, None)
                continue
        elif not isinstance(value, expected):
            errors.append(
                f"runtime amendment {path}: expected {expected.__name__}, got {type(value).__name__}"
            )
            ops.pop(path, None)
            continue

    rt.amendment_ops = ops

    # Project to legacy flat fields for consumers that still read them.
    for env_path, flat_field in _PATH_TO_LEGACY_FLAT.items():
        if env_path in ops:
            setattr(rt, flat_field, ops[env_path])

    return (rt if not errors else None), errors


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate_manifest(
    manifest: ArchitectureManifest,
    spec_text: str,
    project_dir: Path | None = None,
) -> ManifestValidationResult:
    """Cross-validate a parsed manifest against the spec and project."""
    result = ManifestValidationResult()

    # Collect FR-N references from the spec (uppercase input so FR-1/fr-1 both match).
    spec_frs = set(_FR_RE.findall(spec_text.upper()))

    if not manifest.file_map:
        result.warnings.append("file_map is empty — manifest has no structural guidance")

    # Rules match the plan at .claude/plans/bright-stargazing-clock.md.
    # Rules 3-4 (relative paths, valid actions) are enforced in the parser.
    # Rule 6 (API paths start with /) is enforced in the parser.

    # Rule 1: Every FR-N in satisfies must exist in spec.
    for entry in manifest.file_map:
        for fr in entry.satisfies:
            if fr.upper() not in spec_frs:
                result.errors.append(
                    f"file_map '{entry.path}': references {fr} which does not exist in spec"
                )
    for contract in manifest.api_contracts:
        for fr in contract.satisfies:
            if fr.upper() not in spec_frs:
                result.errors.append(
                    f"api_contracts '{contract.method} {contract.path}': "
                    f"references {fr} which does not exist in spec"
                )
    for dep in manifest.dependencies:
        for fr in dep.satisfies:
            if fr.upper() not in spec_frs:
                result.errors.append(
                    f"dependencies '{dep.package}': references {fr} which does not exist in spec"
                )

    # Rule 2: No duplicate paths in file_map.
    seen_paths: set[str] = set()
    for entry in manifest.file_map:
        if entry.path in seen_paths:
            result.errors.append(f"file_map: duplicate path '{entry.path}'")
        seen_paths.add(entry.path)

    # Rule 5: action:modify paths must exist in the project.
    if project_dir:
        for entry in manifest.file_map:
            if entry.action == "modify":
                full_path = project_dir / entry.path
                if not full_path.exists():
                    result.errors.append(
                        f"file_map '{entry.path}': action is 'modify' but file "
                        f"does not exist in project"
                    )

    # Rule 7: data_models.location must appear in file_map.
    file_map_paths = {e.path for e in manifest.file_map}
    for model in manifest.data_models:
        if model.location not in file_map_paths:
            result.errors.append(
                f"data_models '{model.name}': location '{model.location}' "
                f"not found in file_map"
            )

    # Rule 8: dependencies.target must appear in file_map or exist in project.
    for dep in manifest.dependencies:
        if dep.target and dep.target not in file_map_paths:
            if project_dir and not (project_dir / dep.target).exists():
                result.errors.append(
                    f"dependencies '{dep.package}': target '{dep.target}' "
                    f"not in file_map and not found in project"
                )

    # CWD consistency: if runtime commands cd into a subdirectory,
    # config files should be placed there, not at project root.
    cwd = _extract_cwd(manifest.runtime)
    if cwd:
        for entry in manifest.file_map:
            if (
                _CWD_CONFIG_RE.search(entry.path)
                and not entry.path.startswith(cwd + "/")
            ):
                result.warnings.append(
                    f"file_map '{entry.path}': config file may need to be under "
                    f"'{cwd}/' (server commands cd into that directory)"
                )

    result.valid = len(result.errors) == 0
    return result


# ---------------------------------------------------------------------------
# Scope validation
# ---------------------------------------------------------------------------

# Supported heading names: "Out of Scope", "Not in Scope", "Excluded", "Deferred".
_OUT_OF_SCOPE_RE = re.compile(
    r"(?:^|\n)(#+)\s*(?:out\s+of\s+scope|not\s+in\s+scope|excluded|deferred)\b",
    re.IGNORECASE,
)

# Common English and generic software terms filtered from scope section
# tokens to reduce false positives. This is a best-effort heuristic.
_SCOPE_STOP_WORDS = frozenset({
    "the", "and", "for", "not", "this", "that", "with", "from",
    "will", "are", "but", "has", "have", "been", "was", "were",
    "handled", "later", "future", "phase", "scope", "out",
    "deferred", "excluded", "separately", "another", "spec",
    "connection", "migration", "tooling", "setup", "config",
    "configuration", "integration", "implementation",
    "system", "module", "feature", "component", "layer",
    "by", "in", "of", "to", "a", "an", "or", "is", "it",
})


def _split_package_tokens(name: str) -> set[str]:
    """Split a package name into component tokens for fuzzy matching.

    Splits on [-_@/.] so that "@prisma/client" yields {"prisma", "client"},
    "drizzle-orm" yields {"drizzle", "orm"}, etc.
    """
    parts = re.split(r'[-_@/.]', name.lower())
    return {p for p in parts if p and p not in _SCOPE_STOP_WORDS}


def validate_manifest_scope(
    manifest: ArchitectureManifest,
    spec_text: str,
) -> list[str]:
    """Check manifest deps/services against spec Out of Scope section.

    Returns list of warnings (empty = no scope violations detected).
    This is a best-effort keyword check, not a hard blocker.

    Supported Out of Scope heading names: "Out of Scope", "Not in Scope",
    "Excluded", "Deferred".
    """
    warnings: list[str] = []

    match = _OUT_OF_SCOPE_RE.search(spec_text)
    if not match:
        return warnings

    # Extract text from the heading to the next heading of same or higher level.
    section_start = match.end()
    heading_level = len(match.group(1))  # capture group 1 = the '#' characters
    next_heading = re.search(
        rf"(?:^|\n)#{{{1},{heading_level}}}\s+\S",
        spec_text[section_start:],
    )
    section_end = section_start + next_heading.start() if next_heading else len(spec_text)
    scope_text = spec_text[section_start:section_end].lower()

    # Tokenize scope section text. The regex matches lowercase words possibly
    # containing hyphens/digits (e.g., "redis", "drizzle-orm", "redis-7").
    scope_tokens = set(re.findall(r'\b[a-z][\w-]*\b', scope_text))
    tech_tokens = scope_tokens - _SCOPE_STOP_WORDS

    # For each dependency/service, split the name into component tokens and
    # check if any overlap with the scope section tokens.
    for dep in manifest.dependencies:
        dep_tokens = _split_package_tokens(dep.package)
        overlap = dep_tokens & tech_tokens
        if overlap:
            warnings.append(
                f"dependency '{dep.package}' may be out of scope "
                f"(matched: {', '.join(sorted(overlap))})"
            )

    if manifest.runtime and manifest.runtime.services:
        for svc in manifest.runtime.services:
            svc_tokens = _split_package_tokens(svc.name)
            overlap = svc_tokens & tech_tokens
            if overlap:
                warnings.append(
                    f"service '{svc.name}' may be out of scope "
                    f"(matched: {', '.join(sorted(overlap))})"
                )

    return warnings


# ---------------------------------------------------------------------------
# Environment pre-flight
# ---------------------------------------------------------------------------

# Known tools available per language image (installed in Docker).
_CONTAINER_TOOLS: dict[str, frozenset[str]] = {
    "node": frozenset({"node", "npm", "npx", "pnpm", "tsc", "vitest"}),
    "python": frozenset({"python", "python3", "pip", "uv", "pytest", "mypy", "ruff"}),
    "go": frozenset({"go", "gofmt"}),
    "rust": frozenset({"cargo", "rustc", "rustfmt", "clippy"}),
}


def validate_environment(
    manifest: ArchitectureManifest,
    language: str,
) -> list[str]:
    """Check declared environment requirements against container capabilities.

    Returns list of warning strings. Empty = all clear.
    """
    warnings: list[str] = []
    known = _CONTAINER_TOOLS.get(language, frozenset())

    for req in manifest.environment:
        tool = req.tool.lower()
        if tool not in known:
            if req.install_via:
                warnings.append(
                    f"environment: '{req.tool}' not in base {language} image "
                    f"— will use install_via: {req.install_via}"
                )
            else:
                warnings.append(
                    f"environment: '{req.tool}' not in base {language} image "
                    f"and no install_via specified — may fail at runtime"
                )
    return warnings


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def format_manifest_for_sa(manifest: ArchitectureManifest) -> str:
    """Produce a markdown context block for SA prompt injection.

    Emits behavioral test targets (not alignment constraints) so the SA
    knows which endpoints to exercise via HTTP .scenarios.md files.
    """
    lines = [
        "",
        "## Architecture Manifest (Structural Context)",
        "",
        "The Architect has locked the following structural decisions.",
        "Use these as context for writing behavioral test scenarios.",
        "",
    ]

    # File map — structural context only
    if manifest.file_map:
        lines.append("### File Map (Structural Context)")
        lines.append("| Path | Action | Purpose | FRs |")
        lines.append("|------|--------|---------|-----|")
        for entry in manifest.file_map:
            frs = ", ".join(entry.satisfies) if entry.satisfies else "—"
            lines.append(
                f"| `{entry.path}` | {entry.action} | {entry.purpose} | {frs} |"
            )
        lines.append("")

    # API contracts — framed as behavioral test targets
    if manifest.api_contracts:
        lines.append("### Behavioral Test Targets (HTTP Endpoints)")
        lines.append("")
        lines.append(
            "The following endpoints MUST be tested via HTTP behavioral "
            "scenarios (`.scenarios.md` format). Exercise each endpoint "
            "with valid requests, invalid inputs, and edge cases."
        )
        lines.append("")
        for c in manifest.api_contracts:
            status = c.response.get("status", "???") if c.response else "???"
            frs = ", ".join(c.satisfies) if c.satisfies else "—"
            body_hint = ""
            if c.request_body:
                fields = ", ".join(c.request_body.keys())
                body_hint = f" | Body fields: {fields}"
            lines.append(
                f"- **`{c.method} {c.path}`** → expected {status} "
                f"({frs}){body_hint}"
            )
        lines.append("")
        lines.append(
            "For each endpoint above, generate scenarios that cover: "
            "happy path, authentication/authorization failures, "
            "validation errors, and edge cases."
        )
        lines.append("")

    # Data models — structural context
    if manifest.data_models:
        lines.append("### Data Models (Reference)")
        for m in manifest.data_models:
            field_names = ", ".join(f.name for f in m.fields)
            lines.append(f"- **{m.name}** (`{m.location}`): {field_names}")
        lines.append("")

    # Runtime services — so SA knows what infrastructure is available
    rt = manifest.runtime
    if rt.services or rt.test_base_url:
        lines.append("### Runtime Environment")
        if rt.test_base_url:
            lines.append(f"- **Test server base URL:** `{rt.test_base_url}`")
        if rt.services:
            for s in rt.services:
                lines.append(f"- **Service:** `{s.name}` ({s.image})")
                if s.inject_env:
                    for k, v in s.inject_env.items():
                        lines.append(f"  - `{k}={v}`")
        lines.append("")

    return "\n".join(lines)


