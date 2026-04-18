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
    test_only: bool = False
    depends_on: list[str] = field(default_factory=list)


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
class EntryPoint:
    """A public import contract between the Implementer and the Test Engineer.

    ``path`` is the filesystem location where the Implementer must create the file.
    ``specifier`` is the literal string the Test Engineer writes in an ``import``
    statement. The Architect is responsible for choosing a ``specifier`` that
    resolves at runtime under the declared ``module_system`` + ``import_extension``
    (plus whatever ``tsconfig.paths`` / vitest alias config the architect declares
    through the test_integration block).
    """
    path: str = ""
    specifier: str = ""
    exports: list[str] = field(default_factory=list)
    note: str = ""


@dataclass
class ModuleContract:
    """Runtime module resolution contract shared between Implementer and Test Engineer."""
    module_system: str = ""       # "esm" | "cjs" | "dual"
    import_extension: str = ""    # ".js" | ".ts" | "" (empty = no extension, e.g. CJS)
    vitest_config: str = ""       # relative path to vitest/jest config
    tsconfig: str = ""            # relative path to tsconfig.json
    entry_points: list[EntryPoint] = field(default_factory=list)


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
    test_integration: ModuleContract = field(default_factory=ModuleContract)
    # anchor_id -> list of forbidden patterns (literal substrings or
    # regex). Populated from the architect's YAML manifest; consumed by
    # the deterministic post-implementer forbidden-patterns preflight.
    # Empty dict = no anchor declares forbidden patterns.
    forbidden_patterns: dict[str, list[str]] = field(default_factory=dict)


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

    # test_integration (optional)
    raw_ti = data.get("test_integration")
    if raw_ti:
        if not isinstance(raw_ti, dict):
            result.errors.append("test_integration must be a mapping")
        else:
            entry_points: list[EntryPoint] = []
            for idx, ep in enumerate(raw_ti.get("entry_points") or []):
                if not isinstance(ep, dict):
                    result.errors.append(f"test_integration.entry_points[{idx}]: must be a mapping")
                    continue
                raw_exports = ep.get("exports") or []
                if isinstance(raw_exports, str):
                    raw_exports = [raw_exports]
                ep_path = str(ep.get("path", ""))
                ep_exports = [str(e) for e in raw_exports]
                if not ep_path:
                    result.errors.append(
                        f"test_integration.entry_points[{idx}]: missing 'path'"
                    )
                    continue
                if not ep_exports:
                    log.warning(
                        "test_integration.entry_points[%d] (path=%r): exports is empty "
                        "— gate-time export verification will extract actual exports "
                        "from the implemented file", idx, ep_path,
                    )
                ep_specifier_raw = ep.get("specifier")
                ep_specifier = "" if ep_specifier_raw is None else str(ep_specifier_raw)
                entry_points.append(EntryPoint(
                    path=ep_path,
                    specifier=ep_specifier,
                    exports=ep_exports,
                    note=str(ep.get("note", "")),
                ))
            # Coerce YAML null/missing to "" (not the string "None"), since
            # YAML authors commonly write `import_extension:` meaning "no value".
            def _ti_str(key: str) -> str:
                v = raw_ti.get(key)
                return "" if v is None else str(v)

            manifest.test_integration = ModuleContract(
                module_system=_ti_str("module_system"),
                import_extension=_ti_str("import_extension"),
                vitest_config=_ti_str("vitest_config"),
                tsconfig=_ti_str("tsconfig"),
                entry_points=entry_points,
            )

    # forbidden_patterns (optional). anchor_id -> list[str] of literal
    # substrings or regex patterns. Silently drops malformed entries
    # rather than failing manifest parsing — a garbled forbidden-patterns
    # block should degrade gracefully, not abort the run. The preflight
    # check that consumes this is additive / opt-in per-anchor.
    raw_fp = data.get("forbidden_patterns")
    if raw_fp:
        if not isinstance(raw_fp, dict):
            result.errors.append("forbidden_patterns must be a mapping")
        else:
            fp_parsed: dict[str, list[str]] = {}
            for key, value in raw_fp.items():
                anchor_id = str(key).strip()
                if not anchor_id:
                    continue
                if not isinstance(value, list):
                    result.errors.append(
                        f"forbidden_patterns.{anchor_id}: must be a list of strings"
                    )
                    continue
                cleaned = [str(p) for p in value if isinstance(p, str) and p]
                if cleaned:
                    fp_parsed[anchor_id] = cleaned
            manifest.forbidden_patterns = fp_parsed

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
    # Accept "environment" as alias for "env" (architects use both).
    env = raw.get("env") or raw.get("environment") or {}
    if not isinstance(env, dict):
        return None, f"runtime.services[{idx}]: env must be a mapping"
    inject_env = raw.get("inject_env") or {}
    if not isinstance(inject_env, dict):
        return None, f"runtime.services[{idx}]: inject_env must be a mapping"
    health_timeout = raw.get("health_timeout", 30)
    if not isinstance(health_timeout, int) or health_timeout < 1:
        return None, f"runtime.services[{idx}]: health_timeout must be a positive integer"
    test_only = raw.get("test_only", False)
    if not isinstance(test_only, bool):
        return None, f"runtime.services[{idx}]: test_only must be a boolean"
    depends_on = raw.get("depends_on") or []
    if not isinstance(depends_on, list) or not all(isinstance(d, str) for d in depends_on):
        return None, f"runtime.services[{idx}]: depends_on must be a list of strings"
    # health_cmd: accept list form and join into a shell string.
    raw_health = raw.get("health_cmd", "")
    if isinstance(raw_health, list):
        raw_health = " ".join(str(p) for p in raw_health)
    return RuntimeServiceDecl(
        name=name,
        image=image,
        env={str(k): str(v) for k, v in env.items()},
        health_cmd=str(raw_health),
        health_timeout=health_timeout,
        inject_env={str(k): str(v) for k, v in inject_env.items()},
        test_only=test_only,
        depends_on=depends_on,
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
    # Architects sometimes write services as a dict keyed by name rather than
    # a list with explicit name fields — normalise the dict form to a list.
    raw_services = raw.get("services") or []
    if isinstance(raw_services, dict):
        raw_services = [
            {"name": k, **(v if isinstance(v, dict) else {})}
            for k, v in raw_services.items()
        ]
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

    # test_base_url: parsed flat here, mapped to server.test_base_url delta op by amend.py.
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

    # Advisory: warn if precheck reported service advisories but manifest
    # has no services. Deterministic detection — no model dependency.
    if project_dir is not None:
        from darkfactory.spec.advisory import precheck_advisory_path
        _adv_path = precheck_advisory_path(project_dir, manifest.spec)
        if _adv_path.exists():
            try:
                import json as _json
                _adv = _json.loads(_adv_path.read_text(encoding="utf-8"))
                _svc_advs = _adv.get("service_advisories", [])
                _declared = (
                    [s.name for s in manifest.runtime.services]
                    if manifest.runtime else []
                )
                if _svc_advs and not _declared:
                    _names = ", ".join(
                        sa["service_keyword"] for sa in _svc_advs
                    )
                    result.warnings.append(
                        f"precheck reported service advisory for [{_names}] "
                        f"but manifest declares no runtime.services — tests "
                        f"may fail without a local sidecar"
                    )
            except Exception:
                log.debug("failed to read precheck advisory for service check", exc_info=True)

    # Advisory: database service declared but no migration/schema command in
    # commands.install or commands.setup. Likely means tests will skip.
    _DB_SVC_TYPES = {"postgresql", "postgres", "mysql", "mariadb", "sqlite"}
    _MIGRATION_KW = {"migrate", "push", "db:", "drizzle", "prisma", "alembic", "knex", "sequelize", "typeorm"}
    if manifest.runtime and manifest.runtime.services:
        db_services = [
            s for s in manifest.runtime.services
            if any(t in (s.image or "").lower() for t in _DB_SVC_TYPES)
            or any(t in s.name.lower() for t in _DB_SVC_TYPES)
        ]
        if db_services:
            install_cmd = str(manifest.runtime.amendment_ops.get("commands.install", ""))
            setup_cmd = str(manifest.runtime.amendment_ops.get("commands.setup", ""))
            combined = f"{install_cmd} {setup_cmd}".lower()
            has_migration = any(kw in combined for kw in _MIGRATION_KW)
            if not has_migration:
                db_names = ", ".join(s.name for s in db_services)
                result.warnings.append(
                    f"database service(s) [{db_names}] declared but no migration/"
                    f"schema command found in commands.install or commands.setup "
                    f"— tests that depend on database tables will silently skip"
                )

    # Test integration contract: Node/TS specs must declare module_system.
    ti = manifest.test_integration
    if manifest.language == "node":
        if not ti.module_system:
            result.errors.append(
                "test_integration.module_system is required for Node/TypeScript specs. "
                "Set to 'esm' or 'cjs' based on the project's tsconfig.json module setting."
            )
        elif ti.module_system not in ("esm", "cjs", "dual"):
            result.errors.append(
                f"test_integration.module_system must be 'esm', 'cjs', or 'dual', "
                f"got '{ti.module_system}'"
            )
        # import_extension: "" (empty = no extension, for CJS), ".js" (ESM NodeNext),
        # or ".ts" (unusual but legal). Explicitly reject the string "none" — it is
        # ambiguous in YAML (null vs. string "none") and was removed from the contract.
        if ti.import_extension == "none":
            result.errors.append(
                "test_integration.import_extension: use the empty string \"\" to mean "
                "'no extension', not the literal string 'none'. The word 'none' is "
                "ambiguous in YAML and is no longer accepted."
            )
        elif ti.import_extension and ti.import_extension not in (".js", ".ts"):
            result.warnings.append(
                f"test_integration.import_extension '{ti.import_extension}' is unusual — "
                f"expected '.js', '.ts', or \"\" (empty for no extension)"
            )
    if ti.vitest_config:
        if ti.vitest_config.startswith("/") or ".." in ti.vitest_config.split("/"):
            result.errors.append(
                f"test_integration.vitest_config must be a relative path, "
                f"got '{ti.vitest_config}'"
            )
        elif project_dir and not (project_dir / ti.vitest_config).exists():
            # Warning, not error: the Implementer may need to create it.
            result.warnings.append(
                f"test_integration.vitest_config points to '{ti.vitest_config}' "
                "which does not exist yet — the Implementer must create it"
            )
    if ti.tsconfig:
        if ti.tsconfig.startswith("/") or ".." in ti.tsconfig.split("/"):
            result.errors.append(
                f"test_integration.tsconfig must be a relative path, "
                f"got '{ti.tsconfig}'"
            )
    # Validate entry_points.
    for idx, ep in enumerate(ti.entry_points):
        if ep.path and ep.path not in file_map_paths:
            result.warnings.append(
                f"test_integration.entry_points path '{ep.path}' is not in file_map — "
                "tests will import from this path, ensure it exists"
            )
        # For Node specs, every entry point must declare a specifier — the literal
        # string the Test Engineer writes in an `import` statement. Leaving this
        # implicit (deriving from `path`) is the #1 cause of import-time crashes:
        # a path like `apps/api/src/server.ts` is NOT a valid bare specifier and
        # does not resolve in NodeNext without a matching tsconfig.paths entry.
        # The Architect must pick a specifier and guarantee it resolves.
        if manifest.language == "node" and not ep.specifier:
            result.errors.append(
                f"test_integration.entry_points[{idx}] (path='{ep.path}'): "
                "missing 'specifier'. The specifier is the literal string the "
                "Test Engineer will write in an import statement — it MUST be "
                "chosen by the Architect and MUST resolve at runtime under the "
                "declared module_system + import_extension + vitest/tsconfig "
                "path aliases. Examples: a relative path from the test file "
                "location ('../../../apps/api/src/server.js'), a tsconfig path "
                "alias ('@api/server.js'), or a package bare specifier "
                "('@myproj/api/server.js' if the package exposes that export)."
            )

    result.valid = len(result.errors) == 0
    return result


# ---------------------------------------------------------------------------
# M1: Monorepo command scoping validation
# ---------------------------------------------------------------------------


def validate_command_scoping(
    manifest: ArchitectureManifest,
    source_dirs: list[str],
    adapters: "list[Any] | None" = None,
) -> list[dict[str, str]]:
    """Check that ``commands.*`` amendment ops target source_dirs-consistent directories.

    Returns a list of violation dicts, each with ``command_key``,
    ``command_value``, and ``reason``. Empty list = no violations.

    When adapters are provided, delegates to their
    ``validate_command_scope()`` methods for ecosystem-specific command
    parsing. When no adapters are provided, falls back to a generic
    ``cd <dir>`` heuristic.
    """
    if not source_dirs:
        return []

    ops = manifest.runtime.amendment_ops if manifest.runtime else {}
    cmd_ops = {
        k: str(v) for k, v in ops.items()
        if k.startswith("commands.") and v
    }
    if not cmd_ops:
        return []

    violations: list[dict[str, str]] = []
    for key, cmd_val in cmd_ops.items():
        # Collect findings from all adapters
        found = False
        if adapters:
            for adapter in adapters:
                for f in adapter.validate_command_scope(cmd_val, source_dirs):
                    violations.append({
                        "command_key": key,
                        "command_value": cmd_val,
                        "reason": f.message,
                    })
                    found = True

        # Generic fallback when no adapters provided
        if not adapters and not found:
            cd_match = re.search(r'\bcd\s+([^\s;&|]+)', cmd_val)
            if cd_match:
                target = cd_match.group(1).strip("'\"").rstrip("/")
                is_consistent = any(
                    sd.rstrip("/").startswith(target + "/")
                    or sd.rstrip("/") == target
                    or target.startswith(sd.rstrip("/") + "/")
                    for sd in source_dirs
                )
                if not is_consistent:
                    violations.append({
                        "command_key": key,
                        "command_value": cmd_val,
                        "reason": (
                            f"Command targets '{target}' (via cd) but "
                            f"source_dirs are {source_dirs}"
                        ),
                    })

    return violations


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
# Observability-contract validation
# ---------------------------------------------------------------------------

# Keywords that indicate an FR/AC requires observable emitted data (logs,
# metrics, events) — output that must be captured by test scenarios.
# These match in the context of spec section headings and AC descriptions,
# NOT as standalone words (to avoid false positives on domain entities like
# "a log of completed tasks").
_OBSERVABILITY_KEYWORDS = re.compile(
    r'\blog(?:s|ged|ging|ger)?\b'       # log, logs, logged, logging, logger
    r'|\bemit(?:s|ted|ting|ter)?\b'     # emit, emits, emitted, emitting, emitter
    r'|\b(?:write|output)\s+to\s+std(?:out|err)\b'  # write/output to stdout/stderr
    r'|\bmetric(?:s)?\b'               # metric, metrics
    r'|\baudit\s+(?:log|trail|record|event)\b'  # audit log/trail/record/event
    r'|\btrace(?:s|d)?\b'              # trace, traces, traced
    r'|\brequest\s+log\b'              # request log
    r'|\bstructured\s+log\b',          # structured log
    re.IGNORECASE,
)

# Patterns in entry_point exports or notes that suggest a testable
# observation mechanism (injectable logger, stream, writer, etc.).
_OBSERVATION_ENTRY_POINT_RE = re.compile(
    r'log(?:ger|Stream|Writer|Destination|Transport)?\b'
    r'|\bstream\b'
    r'|\bwriter\b'
    r'|\bemitter\b'
    r'|\bobserv',
    re.IGNORECASE,
)

# Section-anchor pattern: [AC-N], [FR-N], or [EC-N] followed by descriptive text.
_ANCHOR_SECTION_RE = re.compile(
    r'\[((?:AC|FR|EC)-\d+)\]\s*(.*?)(?:\n|$)',
    re.IGNORECASE,
)


def validate_observability_contract(
    manifest: ArchitectureManifest,
    spec_text: str,
) -> list[str]:
    """Check that FRs/ACs referencing observable data have testable entry points.

    When a spec's AC or FR mentions logging, metrics, events, or other
    emitted data, the architect manifest must expose a test_integration
    entry_point with an injectable logger/stream/writer — otherwise the
    Test Engineer has no API to capture the output and will resort to
    brittle interception (e.g., console.log monkey-patching).

    Returns list of warning strings. Empty = no observability gaps detected.
    """
    warnings: list[str] = []

    # Find spec anchors (AC-N, FR-N, EC-N) whose description mentions
    # observable data.
    observable_anchors: list[tuple[str, str]] = []
    for match in _ANCHOR_SECTION_RE.finditer(spec_text):
        anchor_id = match.group(1).upper()
        description = match.group(2)
        if _OBSERVABILITY_KEYWORDS.search(description):
            observable_anchors.append((anchor_id, description.strip()))

    if not observable_anchors:
        return warnings

    # Check if any entry_point looks like a testable observation mechanism.
    has_observation_entry = False
    for ep in manifest.test_integration.entry_points:
        # Check exports list and note field for observation-related names.
        exports_str = " ".join(ep.exports)
        searchable = f"{exports_str} {ep.note} {ep.path}"
        if _OBSERVATION_ENTRY_POINT_RE.search(searchable):
            has_observation_entry = True
            break

    if not has_observation_entry:
        anchor_list = ", ".join(
            f"{aid} ({desc[:60]})" for aid, desc in observable_anchors[:5]
        )
        warnings.append(
            f"observability contract gap: spec anchors reference observable "
            f"data ({anchor_list}) but no test_integration.entry_points "
            f"exposes a testable logger/stream/writer. The Test Engineer "
            f"will have no API to capture log output and will likely resort "
            f"to brittle interception (console.log monkey-patching, "
            f"process.stdout spying). Add an entry point that exports an "
            f"injectable logger destination — e.g., a module that accepts a "
            f"writable stream and routes structured log output through it."
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

    # Test integration contract — so SA writes imports from the right paths
    ti = manifest.test_integration
    if ti.module_system or ti.entry_points:
        lines.append("### Test Integration Contract")
        if ti.module_system:
            lines.append(f"- **Module system:** `{ti.module_system}`")
        if ti.import_extension:
            lines.append(f"- **Import extension:** `{ti.import_extension}`")
        if ti.vitest_config:
            lines.append(f"- **Vitest config:** `{ti.vitest_config}`")
        if ti.entry_points:
            lines.append("")
            lines.append(
                "**Entry points — import ONLY from these, using the exact `specifier` string:**"
            )
            for ep in ti.entry_points:
                exports_str = ", ".join(f"`{e}`" for e in ep.exports)
                note_str = f" — {ep.note}" if ep.note else ""
                spec_str = (
                    f"`{ep.specifier}`" if ep.specifier else "(no specifier declared)"
                )
                lines.append(
                    f"- file: `{ep.path}` | import as: {spec_str} | exports: {exports_str}{note_str}"
                )
        lines.append("")

    return "\n".join(lines)


