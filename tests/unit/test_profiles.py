"""Tests for config/profiles.py — Per-spec environment profile resolution."""

import unittest

from tests.helpers import TempDirTestCase
from darkfactory.config.profiles import resolve_profile, parse_spec_metadata
from darkfactory.models.config import PipelineConfig
from darkfactory.errors import ConfigError


class TestParseSpecMetadata(TempDirTestCase):
    """Tests for parse_spec_metadata() function."""

    def test_empty_file(self) -> None:
        """Empty file returns empty dict."""
        spec = self.tmp_dir / "empty.md"
        spec.write_text("")
        result = parse_spec_metadata(spec)
        self.assertEqual(result, {})

    def test_no_metadata_block(self) -> None:
        """File without metadata block returns empty dict."""
        spec = self.tmp_dir / "no-meta.md"
        spec.write_text("""# Feature Spec

This is just content without metadata.
""")
        result = parse_spec_metadata(spec)
        self.assertEqual(result, {})

    def test_missing_file(self) -> None:
        """Non-existent file returns empty dict."""
        result = parse_spec_metadata(self.tmp_dir / "missing.md")
        self.assertEqual(result, {})

    def test_basic_metadata_parsing(self) -> None:
        """Parse basic metadata fields."""
        spec = self.tmp_dir / "basic.md"
        spec.write_text("""# Feature Spec

<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
language: python
source-dirs: backend/src
test-cmd: pytest backend/tests -v
-->

Content goes here.
""")
        result = parse_spec_metadata(spec)
        expected = {
            "profile": "backend",
            "language": "python",
            "source-dirs": "backend/src",
            "test-cmd": "pytest backend/tests -v",
        }
        self.assertEqual(result, expected)

    def test_empty_values_excluded(self) -> None:
        """Empty field values are excluded from result."""
        spec = self.tmp_dir / "empty-fields.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
language:
source-dirs: backend/src
test-cmd:
lint-cmd:
-->
""")
        result = parse_spec_metadata(spec)
        expected = {
            "profile": "backend",
            "source-dirs": "backend/src",
        }
        self.assertEqual(result, expected)

    def test_all_recognized_fields(self) -> None:
        """All recognized profile fields are parsed."""
        spec = self.tmp_dir / "all-fields.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
language: python
source-dirs: backend/src:backend/lib
test-dir: backend/tests
test-cmd: pytest backend/tests -v
lint-cmd: ruff check backend/
build-cmd: python -m py_compile backend/src/**/*.py
typecheck-cmd: mypy backend/src
scenario-test-cmd: pytest scenarios/ -v
-->
""")
        result = parse_spec_metadata(spec)
        expected = {
            "profile": "backend",
            "language": "python",
            "source-dirs": "backend/src:backend/lib",
            "test-dir": "backend/tests",
            "test-cmd": "pytest backend/tests -v",
            "lint-cmd": "ruff check backend/",
            "build-cmd": "python -m py_compile backend/src/**/*.py",
            "typecheck-cmd": "mypy backend/src",
            "scenario-test-cmd": "pytest scenarios/ -v",
        }
        self.assertEqual(result, expected)

    def test_unrecognized_fields_ignored(self) -> None:
        """Unrecognized fields are silently ignored."""
        spec = self.tmp_dir / "unknown-fields.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
unknown-field: should-be-ignored
custom-setting: also-ignored
language: python
-->
""")
        result = parse_spec_metadata(spec)
        expected = {
            "profile": "backend",
            "language": "python",
        }
        self.assertEqual(result, expected)

    def test_malformed_metadata_block(self) -> None:
        """Malformed metadata blocks are handled gracefully."""
        spec = self.tmp_dir / "malformed.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
missing closing tag
""")
        result = parse_spec_metadata(spec)
        expected = {
            "profile": "backend",
        }
        self.assertEqual(result, expected)

    def test_multiple_metadata_blocks(self) -> None:
        """Only the first metadata block is parsed."""
        spec = self.tmp_dir / "multiple.md"
        spec.write_text("""# First block
<!-- dark-factory-metadata
profile: backend
language: python
-->

# Second block (should be ignored)
<!-- dark-factory-metadata
profile: frontend
language: node
-->
""")
        result = parse_spec_metadata(spec)
        expected = {
            "profile": "backend",
            "language": "python",
        }
        self.assertEqual(result, expected)

    def test_file_read_error_returns_empty(self) -> None:
        """If file can't be read, return empty dict gracefully."""
        # Create a directory instead of a file to trigger read error
        bad_file = self.tmp_dir / "directory"
        bad_file.mkdir()
        result = parse_spec_metadata(bad_file)
        self.assertEqual(result, {})


class TestResolveProfile(TempDirTestCase):
    """Tests for resolve_profile() function."""

    def setUp(self) -> None:
        super().setUp()
        self.base_config = PipelineConfig(
            language="node",
            source_dirs=["src"],
            test_cmd="npm test",
            lint_cmd="npx eslint .",
            profiles={
                "backend": {
                    "language": "python",
                    "source_dirs": ["backend/src"],
                    "test_cmd": "pytest backend/tests -v",
                    "lint_cmd": "ruff check backend/",
                    "build_cmd": "python -m py_compile backend/src/**/*.py",
                    "typecheck_cmd": "mypy backend/src",
                },
                "frontend": {
                    "language": "node",
                    "source_dirs": ["frontend/src"],
                    "test_cmd": "npx vitest run",
                    "lint_cmd": "npx eslint frontend/src",
                    "build_cmd": "npm run build",
                    "typecheck_cmd": "npx tsc --noEmit",
                },
                "empty": {
                    # Empty profile to test no-op behavior
                },
            },
        )

    def test_ec1_no_profile_no_overrides(self) -> None:
        """EC-1: Returns original config unchanged when no profile or overrides."""
        spec = self.tmp_dir / "no-profile.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
-->
""")
        result = resolve_profile(spec, self.base_config)
        self.assertIs(result, self.base_config)

    def test_ec2_profile_not_found(self) -> None:
        """EC-2: Raise ConfigError when profile doesn't exist."""
        spec = self.tmp_dir / "missing-profile.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: nonexistent
-->
""")
        with self.assertRaises(ConfigError) as cm:
            resolve_profile(spec, self.base_config)
        self.assertIn("profile 'nonexistent'", str(cm.exception))
        self.assertIn("no [profile.nonexistent] table found", str(cm.exception))

    def test_ec3_spec_override_wins(self) -> None:
        """EC-3: Per-spec override wins over profile value."""
        spec = self.tmp_dir / "override-wins.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
language: node
-->
""")
        result = resolve_profile(spec, self.base_config)
        self.assertEqual(result.language, "node")  # Spec override wins
        self.assertEqual(result.source_dirs, ["backend/src"])  # Profile applies

    def test_ec4_empty_spec_override_ignored(self) -> None:
        """EC-4: Empty per-spec override is treated as 'not set'."""
        spec = self.tmp_dir / "empty-override.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
lint-cmd:
-->
""")
        result = resolve_profile(spec, self.base_config)
        self.assertEqual(result.lint_cmd, "ruff check backend/")  # Profile value applies

    def test_ec5_profile_wins_over_global(self) -> None:
        """EC-5: Profile value wins over global config value."""
        spec = self.tmp_dir / "profile-wins.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
-->
""")
        result = resolve_profile(spec, self.base_config)
        self.assertEqual(result.language, "python")  # Profile wins over global "node"

    def test_ec6_colon_delimited_source_dirs(self) -> None:
        """EC-6: Colon-delimited source-dirs splits into list."""
        spec = self.tmp_dir / "colon-dirs.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
source-dirs: backend/src:backend/lib
-->
""")
        result = resolve_profile(spec, self.base_config)
        self.assertEqual(result.source_dirs, ["backend/src", "backend/lib"])

    def test_ec7_single_source_dir(self) -> None:
        """EC-7: Single directory becomes single-element list."""
        spec = self.tmp_dir / "single-dir.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
source-dirs: backend/src
-->
""")
        result = resolve_profile(spec, self.base_config)
        self.assertEqual(result.source_dirs, ["backend/src"])

    def test_ec8_empty_profile(self) -> None:
        """EC-8: Empty profile is effectively a no-op."""
        spec = self.tmp_dir / "empty-profile.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: empty
-->
""")
        result = resolve_profile(spec, self.base_config)
        # Should be unchanged since empty profile has no fields
        self.assertEqual(result.language, self.base_config.language)
        self.assertEqual(result.source_dirs, self.base_config.source_dirs)

    def test_ec10_malformed_metadata(self) -> None:
        """EC-10: Malformed metadata returns original config."""
        spec = self.tmp_dir / "malformed.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
missing closing tag
""")
        result = resolve_profile(spec, self.base_config)
        # Should still apply the profile that was parsed
        self.assertEqual(result.language, "python")

    def test_ec11_non_mutating(self) -> None:
        """EC-11: Original config is never mutated."""
        spec = self.tmp_dir / "non-mutating.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
-->
""")
        original_language = self.base_config.language
        result = resolve_profile(spec, self.base_config)

        # Original config should be unchanged
        self.assertEqual(self.base_config.language, original_language)
        # Result should be different
        self.assertEqual(result.language, "python")
        self.assertIsNot(result, self.base_config)

    def test_ec12_spec_override_wins_over_profile(self) -> None:
        """EC-12: Per-spec override wins when profile also defines same field."""
        spec = self.tmp_dir / "override-vs-profile.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
source-dirs: frontend/src
-->
""")
        result = resolve_profile(spec, self.base_config)
        self.assertEqual(result.source_dirs, ["frontend/src"])  # Spec override wins

    def test_profile_precedence_full(self) -> None:
        """Test complete precedence: spec > profile > global."""
        spec = self.tmp_dir / "precedence.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: backend
test-cmd: pytest backend/tests -k "not slow" -v
-->
""")
        result = resolve_profile(spec, self.base_config)

        # Spec override wins
        self.assertEqual(result.test_cmd, 'pytest backend/tests -k "not slow" -v')
        # Profile value wins over global
        self.assertEqual(result.language, "python")
        self.assertEqual(result.source_dirs, ["backend/src"])
        # Unchanged global values
        self.assertEqual(result.lint_cmd, "ruff check backend/")

    def test_multiple_profile_fields(self) -> None:
        """Test profile with multiple fields applied correctly."""
        spec = self.tmp_dir / "multiple-fields.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: frontend
-->
""")
        result = resolve_profile(spec, self.base_config)

        self.assertEqual(result.language, "node")
        self.assertEqual(result.source_dirs, ["frontend/src"])
        self.assertEqual(result.test_cmd, "npx vitest run")
        self.assertEqual(result.lint_cmd, "npx eslint frontend/src")
        self.assertEqual(result.build_cmd, "npm run build")
        self.assertEqual(result.typecheck_cmd, "npx tsc --noEmit")

    def test_empty_string_in_profile_treated_as_unset(self) -> None:
        """Empty string values in profiles are treated as 'not set'."""
        config_with_empty = PipelineConfig(
            language="node",
            source_dirs=["src"],
            lint_cmd="global-lint",
            profiles={
                "empty_lint": {
                    "language": "python",
                    "lint_cmd": "",  # Empty string should be ignored
                }
            }
        )

        spec = self.tmp_dir / "empty-profile-field.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: empty_lint
-->
""")
        result = resolve_profile(spec, config_with_empty)

        self.assertEqual(result.language, "python")  # Profile applies
        self.assertEqual(result.lint_cmd, "global-lint")  # Global fallback (empty ignored)

    def test_source_dirs_with_empty_segments(self) -> None:
        """Source-dirs with empty segments (e.g. 'a::b') are filtered."""
        spec = self.tmp_dir / "empty-segments.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
source-dirs: backend/src::backend/lib:
-->
""")
        result = resolve_profile(spec, self.base_config)
        self.assertEqual(result.source_dirs, ["backend/src", "backend/lib"])

    def test_no_changes_returns_original(self) -> None:
        """If no fields change, return original config object."""
        # Profile with identical values to base config
        config_same = PipelineConfig(
            language="node",
            source_dirs=["src"],
            profiles={
                "same": {
                    "language": "node",
                    "source_dirs": ["src"],
                }
            }
        )

        spec = self.tmp_dir / "no-changes.md"
        spec.write_text("""<!-- dark-factory-metadata
feature-id: F-1
depends-on: []
plan:
profile: same
-->
""")
        result = resolve_profile(spec, config_same)
        self.assertIs(result, config_same)  # Should return exact same object

    def test_spec_file_not_exists(self) -> None:
        """Non-existent spec file returns original config."""
        result = resolve_profile(self.tmp_dir / "missing.md", self.base_config)
        self.assertIs(result, self.base_config)


if __name__ == "__main__":
    unittest.main()