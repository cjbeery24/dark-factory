"""Tests for the Architecture Lock manifest parser and validator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from darkfactory.spec.manifest import (
    ArchitectureManifest,
    Dependency,
    EntryPoint,
    FileEntry,
    RuntimeDecl,
    RuntimeServiceDecl,
    ModuleContract,
    compute_manifest_hash,
    format_manifest_for_sa,
    parse_manifest,
    validate_environment,
    validate_manifest,
    validate_manifest_scope,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_MANIFEST = dedent("""\
    version: 1
    spec: test-feature
    spec_hash: abc123
    language: node

    file_map:
      - path: src/routes/users.ts
        action: create
        purpose: "User CRUD"
        satisfies: [FR-1]
      - path: package.json
        action: modify
        purpose: "Add deps"
        existing_summary: "Has express"
        satisfies: []

    data_models:
      - name: User
        location: src/routes/users.ts
        fields:
          - { name: id, type: serial, constraints: [primary_key] }
          - { name: email, type: varchar, constraints: [unique, not_null] }

    api_contracts:
      - method: POST
        path: /api/users
        request_body: { type: object }
        response: { status: 201 }
        satisfies: [FR-1]

    dependencies:
      - package: drizzle-orm
        version: ">=0.30"
        target: package.json
        dev: false
        satisfies: [FR-1]

    environment:
      - tool: drizzle-kit
        required_by: "Migrations"
        install_via: "npx drizzle-kit"
""")

_SPEC_WITH_FR1 = "## Functional Requirements\n\n### FR-1: User management\nCreate users.\n"


# ---------------------------------------------------------------------------
# Parse tests
# ---------------------------------------------------------------------------


class TestParseManifest(unittest.TestCase):

    def test_valid_manifest(self) -> None:
        result = parse_manifest(_VALID_MANIFEST)
        self.assertEqual(result.errors, [])
        self.assertIsNotNone(result.manifest)
        m = result.manifest
        assert m is not None
        self.assertEqual(m.version, 1)
        self.assertEqual(m.spec, "test-feature")
        self.assertEqual(m.spec_hash, "abc123")
        self.assertEqual(m.language, "node")
        self.assertEqual(len(m.file_map), 2)
        self.assertEqual(m.file_map[0].path, "src/routes/users.ts")
        self.assertEqual(m.file_map[0].action, "create")
        self.assertEqual(m.file_map[0].satisfies, ["FR-1"])
        self.assertEqual(m.file_map[1].existing_summary, "Has express")
        self.assertEqual(len(m.data_models), 1)
        self.assertEqual(m.data_models[0].name, "User")
        self.assertEqual(len(m.data_models[0].fields), 2)
        self.assertEqual(m.data_models[0].fields[0].constraints, ["primary_key"])
        self.assertEqual(len(m.api_contracts), 1)
        self.assertEqual(m.api_contracts[0].method, "POST")
        self.assertEqual(m.api_contracts[0].path, "/api/users")
        self.assertEqual(len(m.dependencies), 1)
        self.assertEqual(m.dependencies[0].package, "drizzle-orm")
        self.assertEqual(len(m.environment), 1)
        self.assertEqual(m.environment[0].tool, "drizzle-kit")

    def test_minimal_manifest(self) -> None:
        yaml_text = dedent("""\
            version: 1
            spec: minimal
            file_map:
              - path: src/index.ts
                action: create
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        self.assertIsNotNone(result.manifest)
        assert result.manifest is not None
        self.assertEqual(len(result.manifest.file_map), 1)
        self.assertEqual(result.manifest.data_models, [])
        self.assertEqual(result.manifest.api_contracts, [])

    def test_malformed_yaml(self) -> None:
        result = parse_manifest("{{invalid yaml")
        self.assertTrue(len(result.errors) > 0)
        self.assertIn("Malformed YAML", result.errors[0])
        self.assertIsNone(result.manifest)

    def test_not_a_mapping(self) -> None:
        result = parse_manifest("- just a list")
        self.assertIn("must be a YAML mapping", result.errors[0])

    def test_file_entry_missing_path(self) -> None:
        result = parse_manifest("file_map:\n  - action: create\n")
        self.assertTrue(any("missing 'path'" in e for e in result.errors))

    def test_file_entry_missing_action(self) -> None:
        result = parse_manifest("file_map:\n  - path: foo.ts\n")
        self.assertTrue(any("missing 'action'" in e for e in result.errors))

    def test_file_entry_invalid_action(self) -> None:
        result = parse_manifest("file_map:\n  - path: foo.ts\n    action: rename\n")
        self.assertTrue(any("create|modify|delete" in e for e in result.errors))

    def test_file_entry_absolute_path_rejected(self) -> None:
        result = parse_manifest("file_map:\n  - path: /etc/passwd\n    action: create\n")
        self.assertTrue(any("must be relative" in e for e in result.errors))

    def test_api_contract_invalid_method(self) -> None:
        yaml_text = dedent("""\
            file_map: []
            api_contracts:
              - method: FETCH
                path: /api/test
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("invalid method" in e for e in result.errors))

    def test_api_contract_satisfies_is_list(self) -> None:
        yaml_text = dedent("""\
            file_map: []
            api_contracts:
              - method: GET
                path: /api/test
                satisfies: FR-1
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        assert result.manifest is not None
        self.assertEqual(result.manifest.api_contracts[0].satisfies, ["FR-1"])

    def test_api_contract_response_must_be_dict_run10_regression(self) -> None:
        """Phase 4.6b.1f regression: Run 10 crashed because the architect
        wrote `response` as a YAML-quoted string containing inline
        JSON-like text, which slipped past parse_manifest and crashed
        format_manifest_for_sa on `.get("status")`.  The parser must
        reject the string shape at parse time with a clear error
        naming the contract.
        """
        yaml_text = dedent('''\
            file_map: []
            api_contracts:
              - method: GET
                path: /health
                response: "{ status: 200, body: { type: object } }"
                satisfies: [FR-4]
        ''')
        result = parse_manifest(yaml_text)
        # Must be rejected with a message that identifies the contract
        # and mentions the expected shape.
        self.assertTrue(
            any("GET /health" in e and "response" in e for e in result.errors),
            f"expected parse error naming GET /health and response, got: "
            f"{result.errors}",
        )

    def test_api_contract_request_body_must_be_dict(self) -> None:
        """Phase 4.6b.1f: same parser guard for request_body.  Same
        failure mode waiting to happen — the dataclass type is `dict`
        with no runtime check before this guard landed.
        """
        yaml_text = dedent('''\
            file_map: []
            api_contracts:
              - method: POST
                path: /users
                request_body: "{ name: string }"
                satisfies: [FR-1]
        ''')
        result = parse_manifest(yaml_text)
        self.assertTrue(
            any(
                "POST /users" in e and "request_body" in e
                for e in result.errors
            ),
            f"expected parse error naming POST /users and request_body, "
            f"got: {result.errors}",
        )

    def test_api_contract_dict_response_still_accepted(self) -> None:
        """Phase 4.6b.1f: positive control — the parser guard must not
        reject legitimate dict-shaped responses (block or flow style).
        """
        yaml_text = dedent("""\
            file_map: []
            api_contracts:
              - method: GET
                path: /health
                response:
                  status: 200
                  body:
                    type: object
                satisfies: [FR-4]
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        assert result.manifest is not None
        contract = result.manifest.api_contracts[0]
        self.assertEqual(contract.response.get("status"), 200)

    def test_api_contract_path_must_start_with_slash(self) -> None:
        yaml_text = dedent("""\
            file_map: []
            api_contracts:
              - method: GET
                path: api/test
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("must start with /" in e for e in result.errors))

    def test_data_model_missing_name(self) -> None:
        yaml_text = dedent("""\
            file_map: []
            data_models:
              - location: src/db.ts
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("missing 'name'" in e for e in result.errors))

    def test_dependency_missing_package(self) -> None:
        yaml_text = dedent("""\
            file_map: []
            dependencies:
              - version: "1.0"
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("missing 'package'" in e for e in result.errors))

    def test_satisfies_string_coerced_to_list(self) -> None:
        yaml_text = dedent("""\
            file_map:
              - path: src/index.ts
                action: create
                satisfies: FR-1
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        assert result.manifest is not None
        self.assertEqual(result.manifest.file_map[0].satisfies, ["FR-1"])

    def test_empty_yaml(self) -> None:
        result = parse_manifest("")
        self.assertIn("must be a YAML mapping", result.errors[0])

    def test_unsupported_version(self) -> None:
        result = parse_manifest("version: 2\nfile_map: []\n")
        self.assertTrue(any("Unsupported manifest version" in e for e in result.errors))

    def test_path_traversal_rejected(self) -> None:
        result = parse_manifest("file_map:\n  - path: ../../../etc/passwd\n    action: create\n")
        self.assertTrue(any(".." in e for e in result.errors))

    def test_file_map_not_a_list(self) -> None:
        result = parse_manifest("file_map: not_a_list\n")
        self.assertTrue(any("must be a list" in e for e in result.errors))

    def test_data_models_not_a_list(self) -> None:
        result = parse_manifest("file_map: []\ndata_models: not_a_list\n")
        self.assertTrue(any("must be a list" in e for e in result.errors))

    def test_environment_missing_tool(self) -> None:
        result = parse_manifest("file_map: []\nenvironment:\n  - required_by: test\n")
        self.assertTrue(any("missing 'tool'" in e for e in result.errors))

    def test_api_method_uppercased(self) -> None:
        yaml_text = dedent("""\
            file_map: []
            api_contracts:
              - method: post
                path: /api/test
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        assert result.manifest is not None
        self.assertEqual(result.manifest.api_contracts[0].method, "POST")


# ---------------------------------------------------------------------------
# Validation tests
# ---------------------------------------------------------------------------


class TestValidateManifest(unittest.TestCase):

    def _make_manifest(self, **overrides: object) -> ArchitectureManifest:
        defaults = {
            "file_map": [FileEntry(path="src/index.ts", action="create", satisfies=["FR-1"])],
        }
        defaults.update(overrides)
        return ArchitectureManifest(**defaults)  # type: ignore[arg-type]

    def test_valid_passes(self) -> None:
        m = self._make_manifest()
        result = validate_manifest(m, _SPEC_WITH_FR1)
        self.assertTrue(result.valid)
        self.assertEqual(result.errors, [])

    def test_fr_not_in_spec(self) -> None:
        m = self._make_manifest(
            file_map=[FileEntry(path="src/x.ts", action="create", satisfies=["FR-99"])],
        )
        result = validate_manifest(m, _SPEC_WITH_FR1)
        self.assertFalse(result.valid)
        self.assertTrue(any("FR-99" in e for e in result.errors))

    def test_duplicate_paths(self) -> None:
        m = self._make_manifest(file_map=[
            FileEntry(path="src/x.ts", action="create", satisfies=["FR-1"]),
            FileEntry(path="src/x.ts", action="modify", satisfies=["FR-1"]),
        ])
        result = validate_manifest(m, _SPEC_WITH_FR1)
        self.assertFalse(result.valid)
        self.assertTrue(any("duplicate path" in e for e in result.errors))

    def test_modify_path_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            m = self._make_manifest(
                file_map=[FileEntry(path="nonexistent.ts", action="modify", satisfies=["FR-1"])],
            )
            result = validate_manifest(m, _SPEC_WITH_FR1, project_dir=project)
            self.assertFalse(result.valid)
            self.assertTrue(any("does not exist" in e for e in result.errors))

    def test_modify_path_exists_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "package.json").write_text("{}")
            m = self._make_manifest(
                file_map=[FileEntry(path="package.json", action="modify", satisfies=["FR-1"])],
            )
            result = validate_manifest(m, _SPEC_WITH_FR1, project_dir=project)
            self.assertTrue(result.valid)

    def test_data_model_location_not_in_file_map(self) -> None:
        from darkfactory.spec.manifest import DataModel
        m = self._make_manifest(
            data_models=[DataModel(name="User", location="src/nowhere.ts")],
        )
        result = validate_manifest(m, _SPEC_WITH_FR1)
        self.assertFalse(result.valid)
        self.assertTrue(any("not found in file_map" in e for e in result.errors))

    def test_dependency_target_not_in_file_map_or_project(self) -> None:
        from darkfactory.spec.manifest import Dependency
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            m = self._make_manifest(
                dependencies=[Dependency(package="foo", target="ghost.json")],
            )
            result = validate_manifest(m, _SPEC_WITH_FR1, project_dir=project)
            self.assertFalse(result.valid)
            self.assertTrue(any("not in file_map" in e for e in result.errors))

    def test_dependency_target_exists_in_project(self) -> None:
        from darkfactory.spec.manifest import Dependency
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "package.json").write_text("{}")
            m = self._make_manifest(
                dependencies=[Dependency(package="foo", target="package.json")],
            )
            result = validate_manifest(m, _SPEC_WITH_FR1, project_dir=project)
            self.assertTrue(result.valid)


# ---------------------------------------------------------------------------
# Environment pre-flight tests
# ---------------------------------------------------------------------------


class TestValidateEnvironment(unittest.TestCase):

    def test_known_tool_no_warning(self) -> None:
        from darkfactory.spec.manifest import EnvironmentReq
        m = ArchitectureManifest(environment=[EnvironmentReq(tool="node")])
        warnings = validate_environment(m, "node")
        self.assertEqual(warnings, [])

    def test_unknown_tool_with_install_via(self) -> None:
        from darkfactory.spec.manifest import EnvironmentReq
        m = ArchitectureManifest(
            environment=[EnvironmentReq(tool="drizzle-kit", install_via="npx drizzle-kit")],
        )
        warnings = validate_environment(m, "node")
        self.assertEqual(len(warnings), 1)
        self.assertIn("install_via", warnings[0])

    def test_unknown_tool_no_install_via(self) -> None:
        from darkfactory.spec.manifest import EnvironmentReq
        m = ArchitectureManifest(
            environment=[EnvironmentReq(tool="obscure-tool")],
        )
        warnings = validate_environment(m, "node")
        self.assertEqual(len(warnings), 1)
        self.assertIn("may fail at runtime", warnings[0])


# ---------------------------------------------------------------------------
# Formatter tests
# ---------------------------------------------------------------------------


class TestFormatters(unittest.TestCase):

    def _make_manifest(self) -> ArchitectureManifest:
        result = parse_manifest(_VALID_MANIFEST)
        assert result.manifest is not None
        return result.manifest

    def test_sa_format_contains_file_map_table(self) -> None:
        output = format_manifest_for_sa(self._make_manifest())
        self.assertIn("### File Map (Structural Context)", output)
        self.assertIn("src/routes/users.ts", output)
        self.assertIn("FR-1", output)

    def test_sa_format_contains_api_contracts(self) -> None:
        output = format_manifest_for_sa(self._make_manifest())
        self.assertIn("### Behavioral Test Targets (HTTP Endpoints)", output)
        self.assertIn("POST /api/users", output)

    def test_sa_format_contains_data_models(self) -> None:
        output = format_manifest_for_sa(self._make_manifest())
        self.assertIn("### Data Models (Reference)", output)
        self.assertIn("User", output)

    def test_sa_format_contains_behavioral_instruction(self) -> None:
        output = format_manifest_for_sa(self._make_manifest())
        self.assertIn("MUST be tested via HTTP behavioral scenarios", output)
        self.assertNotIn("Do NOT write scenarios that test file paths", output)

    def test_sa_format_table_structure(self) -> None:
        output = format_manifest_for_sa(self._make_manifest())
        lines = output.split("\n")
        table_lines = [l for l in lines if l.startswith("|")]
        self.assertGreaterEqual(len(table_lines), 3)  # header + separator + data
        self.assertIn("---", table_lines[1])  # separator row


class TestRuntimeParsing(unittest.TestCase):
    """Tests for runtime: block parsing."""

    _BASE = dedent("""\
        version: 1
        spec: test
        spec_hash: abc
        language: node
        file_map:
          - path: src/server.ts
            action: create
            purpose: server
            satisfies: [FR-1]
    """)

    def test_no_runtime_section(self) -> None:
        """Manifest without runtime: parses with empty RuntimeDecl."""
        result = parse_manifest(self._BASE)
        self.assertIsNotNone(result.manifest)
        self.assertEqual(result.manifest.runtime.services, [])
        self.assertEqual(result.manifest.runtime.server_setup_cmd, "")

    def test_runtime_scalars(self) -> None:
        yaml_text = self._BASE + dedent("""\
            runtime:
              server_setup_cmd: "cd apps/api && npx drizzle-kit push:pg"
              test_server_cmd: "cd apps/api && node dist/server.js"
              test_base_url: "http://localhost:3000"
              server_health_path: "/health"
              server_startup_timeout: 45
        """)
        result = parse_manifest(yaml_text)
        self.assertIsNotNone(result.manifest)
        rt = result.manifest.runtime
        self.assertEqual(rt.server_setup_cmd, "cd apps/api && npx drizzle-kit push:pg")
        self.assertEqual(rt.test_server_cmd, "cd apps/api && node dist/server.js")
        self.assertEqual(rt.test_base_url, "http://localhost:3000")
        self.assertEqual(rt.server_health_path, "/health")
        self.assertEqual(rt.server_startup_timeout, 45)

    def test_runtime_services(self) -> None:
        yaml_text = self._BASE + dedent("""\
            runtime:
              services:
                - name: postgres
                  image: "postgres:16-alpine"
                  env:
                    POSTGRES_PASSWORD: postgres
                  health_cmd: "pg_isready -U postgres"
                  health_timeout: 20
                  inject_env:
                    DATABASE_URL: "postgres://postgres:postgres@postgres:5432/testdb"
                - name: redis
                  image: "redis:7-alpine"
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        self.assertIsNotNone(result.manifest)
        self.assertEqual(len(result.manifest.runtime.services), 2)

        pg = result.manifest.runtime.services[0]
        self.assertEqual(pg.name, "postgres")
        self.assertEqual(pg.image, "postgres:16-alpine")
        self.assertEqual(pg.env, {"POSTGRES_PASSWORD": "postgres"})
        self.assertEqual(pg.health_cmd, "pg_isready -U postgres")
        self.assertEqual(pg.health_timeout, 20)
        self.assertEqual(pg.inject_env["DATABASE_URL"], "postgres://postgres:postgres@postgres:5432/testdb")

        redis = result.manifest.runtime.services[1]
        self.assertEqual(redis.name, "redis")
        self.assertEqual(redis.image, "redis:7-alpine")

    def test_runtime_services_dict_form(self) -> None:
        """Architects sometimes write services as a dict keyed by name."""
        yaml_text = self._BASE + dedent("""\
            runtime:
              services:
                postgres:
                  image: "postgres:16"
                  environment:
                    POSTGRES_DB: testdb
                    POSTGRES_USER: testuser
                  health_cmd: ["pg_isready", "-U", "testuser"]
                  health_timeout: 30
                  inject_env:
                    DATABASE_URL: "postgresql://testuser:pass@postgres:5432/testdb"
                  test_only: true
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [], result.errors)
        self.assertEqual(len(result.manifest.runtime.services), 1)
        pg = result.manifest.runtime.services[0]
        self.assertEqual(pg.name, "postgres")
        self.assertEqual(pg.image, "postgres:16")
        # "environment" alias normalised to env
        self.assertEqual(pg.env["POSTGRES_DB"], "testdb")
        # list-form health_cmd joined into string
        self.assertEqual(pg.health_cmd, "pg_isready -U testuser")
        self.assertTrue(pg.test_only)

    def test_runtime_service_missing_name(self) -> None:
        yaml_text = self._BASE + dedent("""\
            runtime:
              services:
                - image: "postgres:16-alpine"
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("missing 'name'" in e for e in result.errors))

    def test_runtime_service_missing_image(self) -> None:
        yaml_text = self._BASE + dedent("""\
            runtime:
              services:
                - name: postgres
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("missing 'image'" in e for e in result.errors))

    def test_runtime_service_bad_dns_name(self) -> None:
        yaml_text = self._BASE + dedent("""\
            runtime:
              services:
                - name: "Postgres_DB"
                  image: "postgres:16"
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("DNS-safe" in e for e in result.errors))

    def test_runtime_service_duplicate_name(self) -> None:
        yaml_text = self._BASE + dedent("""\
            runtime:
              services:
                - name: postgres
                  image: "postgres:16-alpine"
                - name: postgres
                  image: "postgres:15-alpine"
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("duplicate name" in e for e in result.errors))

    def test_runtime_bad_type(self) -> None:
        yaml_text = self._BASE + dedent("""\
            runtime: "not a mapping"
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("runtime must be a mapping" in e for e in result.errors))

    def test_runtime_bad_startup_timeout(self) -> None:
        yaml_text = self._BASE + dedent("""\
            runtime:
              server_startup_timeout: -5
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("server_startup_timeout" in e for e in result.errors))


class TestRuntimeFormatters(unittest.TestCase):
    """Tests for runtime context in Implementer and SA formatters."""

    def _manifest_with_runtime(self, **kwargs: object) -> ArchitectureManifest:
        raw_services = kwargs.pop("services", [])
        rt = RuntimeDecl(
            services=[RuntimeServiceDecl(**s) for s in raw_services] if raw_services else [],  # type: ignore[arg-type]
            **kwargs,  # type: ignore[arg-type]
        )
        return ArchitectureManifest(
            version=1, spec="test", spec_hash="abc", language="node", runtime=rt,
        )

    def test_sa_runtime_shows_services(self) -> None:
        m = self._manifest_with_runtime(
            test_base_url="http://localhost:3000",
            services=[
                {"name": "redis", "image": "redis:7-alpine",
                 "inject_env": {"REDIS_URL": "redis://redis:6379"}},
            ],
        )
        out = format_manifest_for_sa(m)
        self.assertIn("Runtime Environment", out)
        self.assertIn("redis", out)
        self.assertIn("REDIS_URL", out)
        self.assertIn("http://localhost:3000", out)

    def test_sa_no_runtime_no_section(self) -> None:
        m = self._manifest_with_runtime()
        out = format_manifest_for_sa(m)
        self.assertNotIn("Runtime Environment", out)


class TestConfigFromEnvironmentContract(unittest.TestCase):
    """Tests for environment contract → PipelineConfig service refresh."""

    def test_services_populated_from_contract(self) -> None:
        """refresh_config_from_environment populates config.services from contract."""
        from darkfactory.architecture.environment import EnvironmentContract, ServiceEntry
        from darkfactory.architecture.runtime import refresh_config_from_environment
        from darkfactory.models.config import PipelineConfig

        config = PipelineConfig()
        env = EnvironmentContract(
            services={
                "postgres": ServiceEntry(
                    image="postgres:16-alpine",
                    env={"POSTGRES_DB": "testdb"},
                    inject_env={"DATABASE_URL": "postgres://localhost/test"},
                ),
            },
        )

        refresh_config_from_environment(config, env)

        self.assertEqual(len(config.services), 1)
        self.assertEqual(config.services[0].name, "postgres")
        self.assertEqual(config.services[0].image, "postgres:16-alpine")
        self.assertEqual(config.services[0].inject_env["DATABASE_URL"],
                         "postgres://localhost/test")

    def test_test_base_url_from_contract(self) -> None:
        """refresh_config_from_environment populates test_base_url."""
        from darkfactory.architecture.environment import EnvironmentContract, ServerConfig
        from darkfactory.architecture.runtime import refresh_config_from_environment
        from darkfactory.models.config import PipelineConfig

        config = PipelineConfig()
        env = EnvironmentContract(
            server=ServerConfig(test_base_url="http://localhost:3000"),
        )

        refresh_config_from_environment(config, env)

        self.assertEqual(config.test_base_url, "http://localhost:3000")

    def test_empty_services_no_clobber(self) -> None:
        """Empty services in contract do not overwrite existing config.services."""
        from darkfactory.architecture.environment import EnvironmentContract
        from darkfactory.architecture.runtime import refresh_config_from_environment
        from darkfactory.models.config import PipelineConfig, ServiceDef

        config = PipelineConfig(
            services=[ServiceDef(name="redis", image="redis:7")],
        )
        env = EnvironmentContract()  # no services

        refresh_config_from_environment(config, env)

        self.assertEqual(len(config.services), 1)
        self.assertEqual(config.services[0].name, "redis")

    def test_empty_runtime_no_changes(self) -> None:
        """Empty EnvironmentContract leaves config unchanged."""
        from darkfactory.architecture.environment import EnvironmentContract
        from darkfactory.architecture.runtime import refresh_config_from_environment
        from darkfactory.models.config import PipelineConfig

        config = PipelineConfig()
        original_setup = config.server_setup_cmd
        original_server = config.test_server_cmd

        refresh_config_from_environment(config, EnvironmentContract())

        self.assertEqual(config.server_setup_cmd, original_setup)
        self.assertEqual(config.test_server_cmd, original_server)

    def test_depends_on_threaded_through(self) -> None:
        """Phase 4.3: depends_on flows from ServiceEntry to ServiceDef."""
        from darkfactory.architecture.environment import EnvironmentContract, ServiceEntry
        from darkfactory.architecture.runtime import refresh_config_from_environment
        from darkfactory.models.config import PipelineConfig

        config = PipelineConfig()
        env = EnvironmentContract(
            services={
                "redis": ServiceEntry(image="redis:7", depends_on=["postgres"]),
                "postgres": ServiceEntry(image="postgres:16-alpine"),
            },
        )

        refresh_config_from_environment(config, env)

        svc_by_name = {s.name: s for s in config.services}
        self.assertEqual(svc_by_name["redis"].depends_on, ["postgres"])
        self.assertEqual(svc_by_name["postgres"].depends_on, [])


class TestCwdValidation(unittest.TestCase):
    """Tests for CWD consistency validation warnings."""

    _SPEC_TEXT = "# Spec\n## Functional Requirements\n### FR-1\nDo something\n"

    def _manifest_with_cwd(self, setup_cmd: str, file_paths: list[str]) -> ArchitectureManifest:
        rt = RuntimeDecl(server_setup_cmd=setup_cmd)
        file_map = [
            FileEntry(path=p, action="create", purpose="test", satisfies=["FR-1"])
            for p in file_paths
        ]
        return ArchitectureManifest(
            version=1, spec="test", spec_hash="abc", language="node",
            file_map=file_map, runtime=rt,
        )

    def test_config_at_root_warns(self) -> None:
        m = self._manifest_with_cwd(
            "cd apps/api && npx drizzle-kit push:pg",
            ["drizzle.config.ts", "apps/api/src/index.ts"],
        )
        result = validate_manifest(m, self._SPEC_TEXT)
        self.assertTrue(any("drizzle.config.ts" in w and "apps/api" in w for w in result.warnings))

    def test_config_under_cwd_no_warning(self) -> None:
        m = self._manifest_with_cwd(
            "cd apps/api && npx drizzle-kit push:pg",
            ["apps/api/drizzle.config.ts", "apps/api/src/index.ts"],
        )
        result = validate_manifest(m, self._SPEC_TEXT)
        self.assertFalse(any("drizzle.config.ts" in w for w in result.warnings))

    def test_no_cwd_no_warning(self) -> None:
        m = self._manifest_with_cwd(
            "npx drizzle-kit push:pg",
            ["drizzle.config.ts"],
        )
        result = validate_manifest(m, self._SPEC_TEXT)
        self.assertFalse(any("drizzle.config.ts" in w for w in result.warnings))

    def test_env_file_at_root_warns(self) -> None:
        m = self._manifest_with_cwd(
            "cd apps/api && node dist/server.js",
            [".env", "apps/api/src/server.ts"],
        )
        result = validate_manifest(m, self._SPEC_TEXT)
        self.assertTrue(any(".env" in w and "apps/api" in w for w in result.warnings))

    def test_tsconfig_at_root_no_warning(self) -> None:
        """tsconfig.json at root is normal in monorepos — should not warn."""
        m = self._manifest_with_cwd(
            "cd apps/api && node dist/server.js",
            ["tsconfig.json", "apps/api/src/server.ts"],
        )
        result = validate_manifest(m, self._SPEC_TEXT)
        self.assertFalse(any("tsconfig.json" in w for w in result.warnings))


# ---------------------------------------------------------------------------
# Scope validation
# ---------------------------------------------------------------------------

class TestValidateManifestScope(unittest.TestCase):
    """Tests for validate_manifest_scope()."""

    _SPEC_WITH_SCOPE = dedent("""\
        # Feature: API Bootstrap

        ## Functional Requirements
        - FR-1: Server setup

        ## Out of Scope
        - Database connection — handled by F-1c
        - Redis caching — deferred to phase 2
        - Authentication via NextAuth — handled by F-2a
    """)

    def _manifest(
        self,
        deps: list[Dependency] | None = None,
        services: list[RuntimeServiceDecl] | None = None,
    ) -> ArchitectureManifest:
        m = ArchitectureManifest()
        if deps:
            m.dependencies = deps
        if services:
            m.runtime = RuntimeDecl(services=services)
        return m

    def test_no_scope_section_returns_empty(self) -> None:
        spec = "# Feature\n## Requirements\n- FR-1: Do stuff\n"
        m = self._manifest(deps=[Dependency(package="redis")])
        self.assertEqual(validate_manifest_scope(m, spec), [])

    def test_basic_dependency_match(self) -> None:
        m = self._manifest(deps=[Dependency(package="redis")])
        warnings = validate_manifest_scope(m, self._SPEC_WITH_SCOPE)
        self.assertEqual(len(warnings), 1)
        self.assertIn("redis", warnings[0])

    def test_basic_service_match(self) -> None:
        m = self._manifest(services=[RuntimeServiceDecl(name="redis", image="redis:7")])
        warnings = validate_manifest_scope(m, self._SPEC_WITH_SCOPE)
        self.assertEqual(len(warnings), 1)
        self.assertIn("redis", warnings[0])

    def test_scoped_package_name_matches(self) -> None:
        """@prisma/client should match 'database' in scope text."""
        spec = "# Spec\n## Out of Scope\n- Prisma database ORM\n"
        m = self._manifest(deps=[Dependency(package="@prisma/client")])
        warnings = validate_manifest_scope(m, spec)
        self.assertEqual(len(warnings), 1)
        self.assertIn("prisma", warnings[0])

    def test_hyphenated_package_matches(self) -> None:
        """drizzle-orm should match 'drizzle' in scope text."""
        spec = "# Spec\n## Out of Scope\n- Drizzle ORM — handled by F-1c\n"
        m = self._manifest(deps=[Dependency(package="drizzle-orm")])
        warnings = validate_manifest_scope(m, spec)
        self.assertEqual(len(warnings), 1)
        self.assertIn("drizzle", warnings[0])

    def test_stop_words_filtered(self) -> None:
        """Dependencies matching only stop words should not warn."""
        m = self._manifest(deps=[Dependency(package="configuration")])
        warnings = validate_manifest_scope(m, self._SPEC_WITH_SCOPE)
        self.assertEqual(warnings, [])

    def test_case_insensitive(self) -> None:
        m = self._manifest(deps=[Dependency(package="Redis")])
        warnings = validate_manifest_scope(m, self._SPEC_WITH_SCOPE)
        self.assertEqual(len(warnings), 1)

    def test_heading_boundary_respected(self) -> None:
        """Content after a same-level heading should not be included."""
        spec = dedent("""\
            ## Out of Scope
            - Redis caching

            ## In Scope
            - Postgres database
        """)
        m = self._manifest(deps=[
            Dependency(package="redis"),
            Dependency(package="postgres"),
        ])
        warnings = validate_manifest_scope(m, spec)
        self.assertEqual(len(warnings), 1)
        self.assertIn("redis", warnings[0])

    def test_no_deps_no_services_returns_empty(self) -> None:
        m = self._manifest()
        self.assertEqual(validate_manifest_scope(m, self._SPEC_WITH_SCOPE), [])

    def test_unrelated_dep_no_warning(self) -> None:
        m = self._manifest(deps=[Dependency(package="fastify")])
        self.assertEqual(validate_manifest_scope(m, self._SPEC_WITH_SCOPE), [])

    def test_deferred_heading_variant(self) -> None:
        spec = "# Spec\n## Deferred\n- Redis caching\n"
        m = self._manifest(deps=[Dependency(package="redis")])
        warnings = validate_manifest_scope(m, spec)
        self.assertEqual(len(warnings), 1)


class TestDbServiceMigrationWarning(unittest.TestCase):
    """Phase 8: warn when DB service declared without migration command."""

    _SPEC = "# Spec\n## Functional Requirements\n### FR-1\nDatabase setup\n"

    def _manifest_with_service(
        self, svc_name: str = "postgres", svc_image: str = "postgres:16",
        install_cmd: str = "", setup_cmd: str = "",
    ) -> ArchitectureManifest:
        rt = RuntimeDecl(
            services=[RuntimeServiceDecl(name=svc_name, image=svc_image)],
            amendment_ops={
                k: v for k, v in [
                    ("commands.install", install_cmd),
                    ("commands.setup", setup_cmd),
                ] if v
            },
        )
        return ArchitectureManifest(
            version=1, spec="test", spec_hash="abc", language="node",
            file_map=[FileEntry(path="src/db.ts", action="create", purpose="db", satisfies=["FR-1"])],
            runtime=rt,
        )

    def test_db_service_no_migration_warns(self) -> None:
        m = self._manifest_with_service(install_cmd="cd apps/api && pnpm install")
        result = validate_manifest(m, self._SPEC)
        self.assertTrue(
            any("migration" in w and "postgres" in w for w in result.warnings),
            f"Expected migration warning, got: {result.warnings}",
        )

    def test_db_service_with_setup_cmd_no_warning(self) -> None:
        m = self._manifest_with_service(setup_cmd="cd apps/api && npx drizzle-kit push")
        result = validate_manifest(m, self._SPEC)
        self.assertFalse(any("migration" in w for w in result.warnings))

    def test_db_service_with_install_migration_no_warning(self) -> None:
        m = self._manifest_with_service(
            install_cmd="cd apps/api && pnpm install && npx drizzle-kit push",
        )
        result = validate_manifest(m, self._SPEC)
        self.assertFalse(any("migration" in w for w in result.warnings))

    def test_non_db_service_no_warning(self) -> None:
        m = self._manifest_with_service(svc_name="redis", svc_image="redis:7")
        result = validate_manifest(m, self._SPEC)
        self.assertFalse(any("migration" in w for w in result.warnings))

    def test_db_detected_by_name(self) -> None:
        """Service with non-db image but db-like name should still warn."""
        m = self._manifest_with_service(svc_name="postgres", svc_image="custom-pg:latest")
        result = validate_manifest(m, self._SPEC)
        self.assertTrue(any("migration" in w for w in result.warnings))


class TestCommandsSetupParsing(unittest.TestCase):
    """Phase 8: commands.setup flows through manifest parser."""

    def test_commands_setup_in_amendment_ops(self) -> None:
        yaml_text = dedent("""\
            version: 1
            spec: test-spec
            spec_hash: "abc123"
            language: node

            file_map:
              - path: src/db.ts
                action: create
                purpose: database client
                satisfies: [FR-1]

            runtime:
              commands:
                install: "cd apps/api && pnpm install"
                setup: "cd apps/api && npx drizzle-kit push"
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        self.assertEqual(
            result.manifest.runtime.amendment_ops.get("commands.setup"),
            "cd apps/api && npx drizzle-kit push",
        )


class TestModuleContractParsing(unittest.TestCase):
    """Tests for the test_integration manifest section."""

    def test_parse_full_test_integration(self):
        yaml_text = dedent("""\
            version: 1
            spec: db-foundation
            language: node
            file_map:
              - path: apps/api/src/db/schema/index.ts
                action: create
                purpose: schema
                satisfies: [FR-1]
              - path: apps/api/src/server.ts
                action: create
                purpose: server
                satisfies: [FR-2]
            test_integration:
              module_system: esm
              import_extension: .js
              vitest_config: apps/api/vitest.config.ts
              tsconfig: apps/api/tsconfig.json
              entry_points:
                - path: apps/api/src/db/schema/index.ts
                  specifier: "@api/db/schema/index.js"
                  exports: [usersTable, accountsTable]
                  note: "Drizzle table definitions"
                - path: apps/api/src/server.ts
                  specifier: "@api/server.js"
                  exports: [createServer]
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        ti = result.manifest.test_integration
        self.assertEqual(ti.module_system, "esm")
        self.assertEqual(ti.import_extension, ".js")
        self.assertEqual(ti.vitest_config, "apps/api/vitest.config.ts")
        self.assertEqual(ti.tsconfig, "apps/api/tsconfig.json")
        self.assertEqual(len(ti.entry_points), 2)
        self.assertEqual(ti.entry_points[0].path, "apps/api/src/db/schema/index.ts")
        self.assertEqual(ti.entry_points[0].specifier, "@api/db/schema/index.js")
        self.assertEqual(ti.entry_points[0].exports, ["usersTable", "accountsTable"])
        self.assertEqual(ti.entry_points[0].note, "Drizzle table definitions")
        self.assertEqual(ti.entry_points[1].specifier, "@api/server.js")
        self.assertEqual(ti.entry_points[1].exports, ["createServer"])

    def test_parse_no_test_integration(self):
        """Omitting test_integration is valid — defaults to empty."""
        yaml_text = dedent("""\
            version: 1
            spec: simple
            language: python
            file_map:
              - path: src/main.py
                action: create
                purpose: entry
                satisfies: [FR-1]
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        ti = result.manifest.test_integration
        self.assertEqual(ti.module_system, "")
        self.assertEqual(ti.entry_points, [])

    def test_parse_test_integration_bad_type(self):
        yaml_text = dedent("""\
            version: 1
            spec: bad
            language: node
            file_map:
              - path: src/main.ts
                action: create
                purpose: x
                satisfies: [FR-1]
            test_integration: "not a mapping"
        """)
        result = parse_manifest(yaml_text)
        self.assertIn("test_integration must be a mapping", result.errors)

    def test_parse_entry_point_bad_type(self):
        yaml_text = dedent("""\
            version: 1
            spec: bad
            language: node
            file_map:
              - path: src/main.ts
                action: create
                purpose: x
                satisfies: [FR-1]
            test_integration:
              module_system: esm
              entry_points:
                - "just a string"
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("entry_points[0]" in e for e in result.errors))

    def test_parse_entry_point_empty_path(self):
        yaml_text = dedent("""\
            version: 1
            spec: bad
            language: node
            file_map:
              - path: src/main.ts
                action: create
                purpose: x
                satisfies: [FR-1]
            test_integration:
              module_system: esm
              entry_points:
                - path: ""
                  exports: [foo]
        """)
        result = parse_manifest(yaml_text)
        self.assertTrue(any("missing 'path'" in e for e in result.errors))

    def test_parse_entry_point_empty_exports_accepted(self):
        """Empty exports no longer hard-fail — gate-time verification fills them in."""
        yaml_text = dedent("""\
            version: 1
            spec: ok
            language: node
            file_map:
              - path: src/main.ts
                action: create
                purpose: x
                satisfies: [FR-1]
            test_integration:
              module_system: esm
              entry_points:
                - path: src/main.ts
                  exports: []
        """)
        result = parse_manifest(yaml_text)
        self.assertIsNotNone(result.manifest)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(len(result.manifest.test_integration.entry_points), 1)
        self.assertEqual(result.manifest.test_integration.entry_points[0].exports, [])

    def test_parse_entry_point_null_exports_accepted(self):
        """YAML `exports:` with no value produces None — coerces to empty list, not error."""
        yaml_text = dedent("""\
            version: 1
            spec: t
            language: node
            file_map:
              - path: src/main.ts
                action: create
                purpose: x
                satisfies: [FR-1]
            test_integration:
              module_system: esm
              entry_points:
                - path: src/main.ts
                  exports:
        """)
        result = parse_manifest(yaml_text)
        self.assertIsNotNone(result.manifest)
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(result.manifest.test_integration.entry_points[0].exports, [])

    def test_parse_null_import_extension_coerces_to_empty_string(self):
        """YAML `import_extension:` (no value → None) must coerce to "", not 'None'."""
        yaml_text = dedent("""\
            version: 1
            spec: t
            language: node
            file_map:
              - path: src/server.ts
                action: create
                purpose: server
                satisfies: [FR-1]
            test_integration:
              module_system: cjs
              import_extension:
              entry_points:
                - path: src/server.ts
                  exports: [createServer]
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.manifest.test_integration.import_extension, "")

    def test_parse_exports_as_single_string(self):
        """exports: createServer (single string) should be wrapped in a list."""
        yaml_text = dedent("""\
            version: 1
            spec: t
            language: node
            file_map:
              - path: src/server.ts
                action: create
                purpose: server
                satisfies: [FR-1]
            test_integration:
              module_system: esm
              entry_points:
                - path: src/server.ts
                  exports: createServer
        """)
        result = parse_manifest(yaml_text)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.manifest.test_integration.entry_points[0].exports, ["createServer"])


class TestModuleContractValidation(unittest.TestCase):
    """Tests for test_integration validation rules."""

    _SPEC_TEXT = "# Spec\n## Functional Requirements\n### FR-1\nDo something\n"

    def _make_manifest(self, **ti_kwargs):
        manifest = ArchitectureManifest(
            language="node",
            file_map=[FileEntry(path="src/main.ts", action="create", satisfies=["FR-1"])],
        )
        manifest.test_integration = ModuleContract(**ti_kwargs)
        return manifest

    def test_node_requires_module_system(self):
        manifest = self._make_manifest()  # module_system=""
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertFalse(result.valid)
        self.assertTrue(any("module_system is required" in e for e in result.errors))

    def test_node_valid_module_system(self):
        manifest = self._make_manifest(module_system="esm")
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertTrue(result.valid)

    def test_node_invalid_module_system(self):
        manifest = self._make_manifest(module_system="umd")
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertFalse(result.valid)
        self.assertTrue(any("'esm', 'cjs', or 'dual'" in e for e in result.errors))

    def test_python_no_module_system_ok(self):
        """Python specs don't require module_system."""
        manifest = ArchitectureManifest(
            language="python",
            file_map=[FileEntry(path="src/main.py", action="create", satisfies=["FR-1"])],
        )
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertTrue(result.valid)

    def test_unusual_import_extension_warns(self):
        manifest = self._make_manifest(module_system="esm", import_extension=".mjs")
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertTrue(result.valid)
        self.assertTrue(any("unusual" in w for w in result.warnings))

    def test_empty_string_import_extension_ok(self):
        """An empty string means 'no extension' (e.g. CJS) and is valid."""
        manifest = self._make_manifest(module_system="cjs", import_extension="")
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertTrue(result.valid)
        self.assertFalse(any("import_extension" in w for w in result.warnings))

    def test_literal_none_import_extension_rejected(self):
        """The literal string 'none' is ambiguous in YAML and must be rejected."""
        manifest = self._make_manifest(module_system="esm", import_extension="none")
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("'none'" in e and "empty string" in e for e in result.errors),
            f"expected error rejecting 'none', got: {result.errors}",
        )

    def test_vitest_config_absolute_path_errors(self):
        manifest = self._make_manifest(module_system="esm", vitest_config="/etc/vitest.config.ts")
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertFalse(result.valid)
        self.assertTrue(any("relative path" in e for e in result.errors))

    def test_vitest_config_traversal_errors(self):
        manifest = self._make_manifest(module_system="esm", vitest_config="../vitest.config.ts")
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertFalse(result.valid)

    def test_entry_point_not_in_file_map_warns(self):
        manifest = self._make_manifest(
            module_system="esm",
            entry_points=[EntryPoint(
                path="src/missing.ts",
                specifier="./src/missing.js",
                exports=["foo"],
            )],
        )
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertTrue(result.valid)  # warning, not error
        self.assertTrue(any("not in file_map" in w for w in result.warnings))

    def test_entry_point_in_file_map_no_warning(self):
        manifest = self._make_manifest(
            module_system="esm",
            entry_points=[EntryPoint(
                path="src/main.ts",
                specifier="./src/main.js",
                exports=["app"],
            )],
        )
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertTrue(result.valid)
        self.assertFalse(any("not in file_map" in w for w in result.warnings))

    def test_node_entry_point_missing_specifier_rejected(self):
        """Every Node entry point MUST declare a specifier — path alone is not enough."""
        manifest = self._make_manifest(
            module_system="esm",
            entry_points=[EntryPoint(path="src/main.ts", exports=["app"])],
        )
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertFalse(result.valid)
        self.assertTrue(
            any("missing 'specifier'" in e for e in result.errors),
            f"expected error about missing specifier, got: {result.errors}",
        )

    def test_python_entry_point_no_specifier_ok(self):
        """Python entry points don't use specifiers — the rule is Node-only."""
        manifest = ArchitectureManifest(
            language="python",
            file_map=[FileEntry(path="src/main.py", action="create", satisfies=["FR-1"])],
        )
        manifest.test_integration = ModuleContract(
            entry_points=[EntryPoint(path="src/main.py", exports=["app"])],
        )
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertTrue(result.valid)

    def test_tsconfig_traversal_errors(self):
        manifest = self._make_manifest(module_system="esm", tsconfig="../tsconfig.json")
        result = validate_manifest(manifest, self._SPEC_TEXT)
        self.assertFalse(result.valid)
        self.assertTrue(any("relative path" in e for e in result.errors))

    def test_format_manifest_renders_test_integration(self):
        manifest = ArchitectureManifest(
            language="node",
            file_map=[FileEntry(path="src/server.ts", action="create", satisfies=["FR-1"])],
        )
        manifest.test_integration = ModuleContract(
            module_system="esm",
            import_extension=".js",
            entry_points=[EntryPoint(
                path="src/server.ts",
                specifier="./src/server.js",
                exports=["createServer"],
                note="Factory",
            )],
        )
        output = format_manifest_for_sa(manifest)
        self.assertIn("Module system", output)
        self.assertIn("`esm`", output)
        self.assertIn("Import extension", output)
        self.assertIn("src/server.ts", output)
        self.assertIn("./src/server.js", output)  # specifier rendered
        self.assertIn("`createServer`", output)

    def test_vitest_config_missing_from_disk_warns(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "src").mkdir()
            (project_dir / "src" / "main.ts").touch()
            manifest = self._make_manifest(
                module_system="esm",
                vitest_config="apps/api/vitest.config.ts",
            )
            result = validate_manifest(manifest, self._SPEC_TEXT, project_dir)
            self.assertTrue(result.valid)  # warning, not error
            self.assertTrue(any("does not exist yet" in w for w in result.warnings))

    # -----------------------------------------------------------------
    # Task 42: type-only export rejection
    # -----------------------------------------------------------------
    #
    # validate_manifest must reject Node/TS manifests whose entry_point
    # exports resolve to TypeScript type-only declarations — those get
    # erased at compile time and the Import Contract gate's runtime
    # check (Object.keys(resolvedModule)) cannot possibly satisfy them.
    # This reproduces the 2026-04-21 database-foundation iter-3
    # escalation (DrizzleTx = Parameters<…>).

    def test_type_only_export_rejected(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "src").mkdir()
            (project_dir / "src" / "main.ts").write_text(
                "export const app = 1;\n"
                "export type AppConfig = { port: number };\n",
                encoding="utf-8",
            )
            manifest = self._make_manifest(
                module_system="esm",
                entry_points=[EntryPoint(
                    path="src/main.ts",
                    specifier="./src/main.js",
                    exports=["app", "AppConfig"],
                )],
            )
            result = validate_manifest(manifest, self._SPEC_TEXT, project_dir)
            self.assertFalse(result.valid)
            self.assertTrue(
                any(
                    "type-only" in e and "AppConfig" in e
                    for e in result.errors
                ),
                f"expected type-only rejection error, got: {result.errors}",
            )

    def test_all_runtime_exports_pass(self):
        """Control: a manifest whose exports are all runtime bindings
        must not trip the type-only check."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "src").mkdir()
            (project_dir / "src" / "main.ts").write_text(
                "export const app = 1;\n"
                "export function boot() {}\n",
                encoding="utf-8",
            )
            manifest = self._make_manifest(
                module_system="esm",
                entry_points=[EntryPoint(
                    path="src/main.ts",
                    specifier="./src/main.js",
                    exports=["app", "boot"],
                )],
            )
            result = validate_manifest(manifest, self._SPEC_TEXT, project_dir)
            self.assertTrue(result.valid, f"errors: {result.errors}")

    def test_type_only_check_skipped_without_project_dir(self):
        """The check requires on-disk source; without project_dir the
        manifest still parses fine (precheck may run this way)."""
        manifest = self._make_manifest(
            module_system="esm",
            entry_points=[EntryPoint(
                path="src/main.ts",
                specifier="./src/main.js",
                exports=["AppConfig"],  # would be type-only if we could see it
            )],
        )
        result = validate_manifest(manifest, self._SPEC_TEXT)  # no project_dir
        self.assertTrue(result.valid)

    def test_type_only_check_skipped_before_file_exists(self):
        """The implementer may be about to create the file; skip silently
        when the path doesn't exist yet. The Import Contract gate is the
        backstop for that state."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            manifest = self._make_manifest(
                module_system="esm",
                entry_points=[EntryPoint(
                    path="src/main.ts",
                    specifier="./src/main.js",
                    exports=["app"],
                )],
            )
            result = validate_manifest(manifest, self._SPEC_TEXT, project_dir)
            self.assertTrue(result.valid, f"errors: {result.errors}")

    def test_type_only_check_scoped_to_node(self):
        """Python specs must not even load the TS classifier — it's not
        applicable and would confuse error messages."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            (project_dir / "src").mkdir()
            (project_dir / "src" / "main.py").write_text("x = 1\n", encoding="utf-8")
            manifest = ArchitectureManifest(
                language="python",
                file_map=[FileEntry(
                    path="src/main.py", action="create", satisfies=["FR-1"],
                )],
            )
            manifest.test_integration = ModuleContract(
                entry_points=[EntryPoint(path="src/main.py", exports=["x"])],
            )
            result = validate_manifest(manifest, self._SPEC_TEXT, project_dir)
            self.assertTrue(result.valid)

    def test_drizzle_tx_regression_rejected(self):
        """End-to-end reproduction of the escalated iter-3 failure:
        manifest listed DrizzleTx (= Parameters<…>) — a type alias — and
        the run burned 75 min chasing a structurally unfixable gate."""
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir)
            db_dir = project_dir / "apps" / "api" / "src" / "db"
            db_dir.mkdir(parents=True)
            (db_dir / "index.ts").write_text(
                "export const connection = 1;\n"
                "export const db = 2;\n"
                "export const withTenantDb = 3;\n"
                "export type DrizzleTx = "
                "Parameters<Parameters<typeof db>[0]>[0];\n",
                encoding="utf-8",
            )
            manifest = self._make_manifest(
                module_system="esm",
                entry_points=[EntryPoint(
                    path="apps/api/src/db/index.ts",
                    specifier="../../../apps/api/src/db/index.js",
                    exports=["db", "connection", "withTenantDb", "DrizzleTx"],
                )],
            )
            result = validate_manifest(manifest, self._SPEC_TEXT, project_dir)
            self.assertFalse(result.valid)
            # The error names the offending symbol and gives the architect
            # a concrete remediation (drop it from exports) — not just
            # "something is wrong".
            relevant = [e for e in result.errors if "DrizzleTx" in e]
            self.assertTrue(
                relevant,
                f"expected DrizzleTx in errors, got: {result.errors}",
            )
            self.assertTrue(
                any("type-only" in e for e in relevant),
                f"expected 'type-only' wording, got: {relevant}",
            )


# ---------------------------------------------------------------------------
# Observability-contract validation
# ---------------------------------------------------------------------------

class TestObservabilityContractValidation(unittest.TestCase):
    """Phase 2.3: validate that observable-data FRs have testable entry points."""

    @staticmethod
    def _make_manifest(entry_points: list[EntryPoint] | None = None) -> ArchitectureManifest:
        return ArchitectureManifest(
            language="node",
            spec="test-spec",
            file_map=[FileEntry(path="src/server.ts", action="create", purpose="server")],
            test_integration=ModuleContract(
                module_system="esm",
                import_extension=".js",
                entry_points=entry_points or [],
            ),
        )

    def test_no_observable_frs_passes(self) -> None:
        """Spec without log/metric/event FRs → no warnings."""
        from darkfactory.spec.manifest import validate_observability_contract
        spec = (
            "## Acceptance Criteria\n\n"
            "[AC-1] Server returns 200 on /health\n"
            "[FR-1] User creation endpoint validates email\n"
        )
        manifest = self._make_manifest()
        warnings = validate_observability_contract(manifest, spec)
        self.assertEqual(warnings, [])

    def test_observable_fr_without_entry_point_warns(self) -> None:
        """Spec with logging FR but no logger entry point → warning."""
        from darkfactory.spec.manifest import validate_observability_contract
        spec = (
            "## Acceptance Criteria\n\n"
            "[AC-1] Server returns 200 on /health\n"
            "[FR-2] request log contains requestId, method, url, statusCode\n"
            "[AC-7] Authorization header value is redacted in log output\n"
        )
        manifest = self._make_manifest(entry_points=[
            EntryPoint(path="src/server.ts", specifier="../src/server.js",
                       exports=["createServer"]),
        ])
        warnings = validate_observability_contract(manifest, spec)
        self.assertEqual(len(warnings), 1)
        self.assertIn("observability contract gap", warnings[0])
        self.assertIn("FR-2", warnings[0])

    def test_observable_fr_with_logger_entry_point_passes(self) -> None:
        """Spec with logging FR + logger entry point → no warnings."""
        from darkfactory.spec.manifest import validate_observability_contract
        spec = (
            "## Acceptance Criteria\n\n"
            "[FR-2] request log contains requestId, method, url, statusCode\n"
            "[AC-7] Authorization header value is redacted in log output\n"
        )
        manifest = self._make_manifest(entry_points=[
            EntryPoint(path="src/server.ts", specifier="../src/server.js",
                       exports=["createServer"]),
            EntryPoint(path="src/logger.ts", specifier="../src/logger.js",
                       exports=["createLogger", "logStream"],
                       note="Injectable logger with writable stream destination"),
        ])
        warnings = validate_observability_contract(manifest, spec)
        self.assertEqual(warnings, [])

    def test_domain_log_not_false_positive(self) -> None:
        """'a log of completed tasks' is a domain entity, not observability."""
        from darkfactory.spec.manifest import validate_observability_contract
        spec = (
            "## Acceptance Criteria\n\n"
            "[FR-1] User can view a catalog of available items\n"
            "[AC-2] Dashboard displays total revenue for the period\n"
        )
        manifest = self._make_manifest()
        warnings = validate_observability_contract(manifest, spec)
        self.assertEqual(warnings, [])

    def test_audit_trail_triggers_warning(self) -> None:
        """'audit log' is an observability term → warning if no entry point."""
        from darkfactory.spec.manifest import validate_observability_contract
        spec = (
            "## Acceptance Criteria\n\n"
            "[FR-3] every mutation writes an audit log entry\n"
        )
        manifest = self._make_manifest()
        warnings = validate_observability_contract(manifest, spec)
        self.assertEqual(len(warnings), 1)
        self.assertIn("FR-3", warnings[0])

    def test_entry_point_note_matches(self) -> None:
        """Entry point with 'logger' in note field satisfies the check."""
        from darkfactory.spec.manifest import validate_observability_contract
        spec = (
            "## Acceptance Criteria\n\n"
            "[FR-2] structured logging for all requests\n"
        )
        manifest = self._make_manifest(entry_points=[
            EntryPoint(path="src/observability.ts", specifier="../src/observability.js",
                       exports=["setupTracing"],
                       note="Logger and observability setup"),
        ])
        warnings = validate_observability_contract(manifest, spec)
        self.assertEqual(warnings, [])


class TestComputeManifestHash(unittest.TestCase):
    """Phase 6 I1: ``compute_manifest_hash`` cross-run comparability.

    The hash must be (a) stable on identical content, (b) insensitive to
    orchestrator-added stamp fields (``active_adapters`` /
    ``stamp_integrity``) so runs on the same spec + adapter set
    compare equally, (c) sensitive to semantic architect changes, and
    (d) fail-safe on unreadable / unparseable manifests.
    """

    def _write(self, body: str) -> Path:
        tmp = Path(tempfile.mkdtemp()) / "manifest.yaml"
        tmp.write_text(body, encoding="utf-8")
        return tmp

    def test_hash_is_deterministic(self) -> None:
        mp = self._write("file_map:\n  - path: src/a.ts\n    action: create\n")
        h1, _ = compute_manifest_hash(mp)
        h2, _ = compute_manifest_hash(mp)
        self.assertEqual(h1, h2)
        self.assertEqual(len(h1), 64)

    def test_canonical_json_has_sorted_keys(self) -> None:
        mp = self._write(
            "file_map:\n  - path: src/a.ts\n    action: create\n"
            "api_contracts: []\n"
        )
        _, canon = compute_manifest_hash(mp)
        # sort_keys=True pushes api_contracts before file_map.
        self.assertLess(canon.index("api_contracts"), canon.index("file_map"))

    def test_stamp_fields_stripped(self) -> None:
        """active_adapters and stamp_integrity are orchestrator-added
        and must not affect the hash — otherwise runs with different
        stamps would falsely register as different architect outputs."""
        base = "file_map:\n  - path: src/a.ts\n    action: create\n"
        mp1 = self._write(base + "active_adapters: [typescript]\nstamp_integrity: abc\n")
        mp2 = self._write(base + "active_adapters: [python]\nstamp_integrity: xyz\n")
        h1, c1 = compute_manifest_hash(mp1)
        h2, c2 = compute_manifest_hash(mp2)
        self.assertEqual(h1, h2)
        self.assertEqual(c1, c2)
        self.assertNotIn("active_adapters", c1)
        self.assertNotIn("stamp_integrity", c1)

    def test_semantic_change_changes_hash(self) -> None:
        mp_a = self._write(
            "file_map:\n  - path: src/a.ts\n    action: create\n"
        )
        mp_b = self._write(
            "file_map:\n  - path: src/a.ts\n    action: delete\n"
        )
        h_a, _ = compute_manifest_hash(mp_a)
        h_b, _ = compute_manifest_hash(mp_b)
        self.assertNotEqual(h_a, h_b)

    def test_missing_file_returns_empty_tuple(self) -> None:
        mp = Path(tempfile.mkdtemp()) / "does-not-exist.yaml"
        self.assertEqual(compute_manifest_hash(mp), ("", ""))

    def test_unparseable_yaml_returns_empty_tuple(self) -> None:
        mp = self._write("key: [unclosed\n")
        self.assertEqual(compute_manifest_hash(mp), ("", ""))

    def test_non_mapping_root_returns_empty_tuple(self) -> None:
        # A YAML list at the root is valid YAML but not a valid manifest.
        mp = self._write("- a\n- b\n")
        self.assertEqual(compute_manifest_hash(mp), ("", ""))
