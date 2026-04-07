"""Tests for the Architecture Lock manifest parser and validator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from darkfactory.spec.manifest import (
    ArchitectureManifest,
    Dependency,
    FileEntry,
    RuntimeDecl,
    RuntimeServiceDecl,
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


class TestConfigPatching(unittest.TestCase):
    """Tests for manifest runtime → PipelineConfig fallback patching."""

    def test_manifest_runtime_patches_empty_config(self) -> None:
        """Manifest runtime fills in services + test_base_url on empty config.

        Phase 2.3: server_setup_cmd / test_server_cmd / server_health_path /
        server_startup_timeout now come from EnvironmentContract via
        step_begin, NOT from bridge_unmodeled_runtime_fields. This function
        survives only as a bridge for fields the env contract does not
        yet model (services, test_base_url) — Phase 4 removes it.
        """
        from darkfactory.models.config import PipelineConfig
        from darkfactory.pipeline.orchestrator import bridge_unmodeled_runtime_fields

        config = PipelineConfig()
        rt = RuntimeDecl(
            test_base_url="http://localhost:3000",
            services=[
                RuntimeServiceDecl(
                    name="postgres", image="postgres:16-alpine",
                    env={"POSTGRES_DB": "testdb"},
                    inject_env={"DATABASE_URL": "postgres://localhost/test"},
                ),
            ],
        )

        bridge_unmodeled_runtime_fields(config, rt)

        self.assertEqual(config.test_base_url, "http://localhost:3000")
        self.assertEqual(len(config.services), 1)
        self.assertEqual(config.services[0].name, "postgres")
        self.assertEqual(config.services[0].inject_env["DATABASE_URL"], "postgres://localhost/test")

    def test_config_toml_takes_precedence(self) -> None:
        """config.toml values are never overridden by manifest runtime."""
        from darkfactory.models.config import PipelineConfig
        from darkfactory.pipeline.orchestrator import bridge_unmodeled_runtime_fields

        config = PipelineConfig(test_base_url="http://original:8080")
        rt = RuntimeDecl(test_base_url="http://manifest:3000")

        bridge_unmodeled_runtime_fields(config, rt)

        self.assertEqual(config.test_base_url, "http://original:8080")

    def test_services_not_merged(self) -> None:
        """When config already has services, manifest services are ignored entirely."""
        from darkfactory.models.config import PipelineConfig, ServiceDef
        from darkfactory.pipeline.orchestrator import bridge_unmodeled_runtime_fields

        config = PipelineConfig(
            services=[ServiceDef(name="redis", image="redis:7")],
        )
        rt = RuntimeDecl(
            services=[
                RuntimeServiceDecl(name="postgres", image="postgres:16-alpine"),
            ],
        )

        bridge_unmodeled_runtime_fields(config, rt)

        self.assertEqual(len(config.services), 1)
        self.assertEqual(config.services[0].name, "redis")

    def test_empty_runtime_no_changes(self) -> None:
        """Empty RuntimeDecl leaves config unchanged."""
        from darkfactory.models.config import PipelineConfig
        from darkfactory.pipeline.orchestrator import bridge_unmodeled_runtime_fields

        config = PipelineConfig()
        original_setup = config.server_setup_cmd
        original_server = config.test_server_cmd

        bridge_unmodeled_runtime_fields(config, RuntimeDecl())

        self.assertEqual(config.server_setup_cmd, original_setup)
        self.assertEqual(config.test_server_cmd, original_server)


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
