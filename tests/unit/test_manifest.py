"""Tests for the Architecture Lock manifest parser and validator."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from textwrap import dedent

from darkfactory.spec.manifest import (
    ArchitectureManifest,
    FileEntry,
    compute_spec_hash,
    format_manifest_for_implementer,
    format_manifest_for_sa,
    parse_manifest,
    validate_environment,
    validate_manifest,
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

    def test_implementer_format_contains_files(self) -> None:
        output = format_manifest_for_implementer(self._make_manifest())
        self.assertIn("### Files to Create/Modify", output)
        self.assertIn("src/routes/users.ts", output)
        self.assertIn("existing: Has express", output)

    def test_implementer_format_contains_dependencies(self) -> None:
        output = format_manifest_for_implementer(self._make_manifest())
        self.assertIn("### Dependencies to Add", output)
        self.assertIn("drizzle-orm", output)

    def test_implementer_format_contains_data_models(self) -> None:
        output = format_manifest_for_implementer(self._make_manifest())
        self.assertIn("### Data Models", output)
        self.assertIn("id", output)
        self.assertIn("serial", output)

    def test_sa_format_table_structure(self) -> None:
        output = format_manifest_for_sa(self._make_manifest())
        lines = output.split("\n")
        table_lines = [l for l in lines if l.startswith("|")]
        self.assertGreaterEqual(len(table_lines), 3)  # header + separator + data
        self.assertIn("---", table_lines[1])  # separator row


# ---------------------------------------------------------------------------
# Spec hash tests
# ---------------------------------------------------------------------------


class TestComputeSpecHash(unittest.TestCase):

    def test_returns_hex_digest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec = Path(tmp) / "spec.md"
            spec.write_text("# Test Spec\n")
            h = compute_spec_hash(spec)
            self.assertEqual(len(h), 64)  # SHA-256 hex
            self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_different_content_different_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            spec1 = Path(tmp) / "spec1.md"
            spec2 = Path(tmp) / "spec2.md"
            spec1.write_text("content A")
            spec2.write_text("content B")
            self.assertNotEqual(compute_spec_hash(spec1), compute_spec_hash(spec2))
