"""Tests for _extract_free_variables in orchestrator.py."""

import unittest

from tests.helpers import TempDirTestCase, make_project
from darkfactory.pipeline.orchestrator import _extract_free_variables


class TestExtractFreeVariables(TempDirTestCase):
    """Test Free Variables extraction from spec and validation report."""

    def setUp(self) -> None:
        super().setUp()
        make_project(self.tmp_dir)

    def _write_spec(self, spec_name: str, content: str) -> None:
        path = self.tmp_dir / ".dark-factory" / "specs" / f"{spec_name}.md"
        path.write_text(content, encoding="utf-8")

    def _write_validation(self, spec_name: str, content: str) -> None:
        path = self.tmp_dir / ".dark-factory" / "reviews" / f"{spec_name}-validation.md"
        path.write_text(content, encoding="utf-8")

    # --- Spec extraction (primary source) ---

    def test_spec_with_fv_entries(self) -> None:
        """Spec file with FV-N entries is extracted; content outside section excluded."""
        self._write_spec("test-spec", (
            "## 1. Problem\nSome problem.\n\n"
            "## 12. Free Variables (implementation choices)\n\n"
            "| ID | Variable | What Is Constrained | What Is Free |\n"
            "|----|----------|---------------------|--------------|\n"
            "| FV-1 | Config schema | Must contain build cmd | Key nesting |\n"
            "| FV-2 | Package format | Must use pnpm | workspace config |\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FV-1", result)
        self.assertIn("FV-2", result)
        self.assertIn("Config schema", result)
        self.assertNotIn("Some problem", result)

    def test_spec_with_unnumbered_heading(self) -> None:
        """Spec with ## Free Variables (no section number) is extracted."""
        self._write_spec("test-spec", (
            "## Problem\nSome problem.\n\n"
            "## Free Variables\n\n"
            "| ID | Variable | What Is Constrained | What Is Free |\n"
            "|----|----------|---------------------|--------------|\n"
            "| FV-1 | Config format | Must be JSON | Nesting depth |\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FV-1", result)

    def test_spec_with_h3_heading(self) -> None:
        """Spec with ### Free Variables (h3) is extracted."""
        self._write_spec("test-spec", (
            "## Problem\nSome problem.\n\n"
            "### Free Variables\n\n"
            "| ID | Variable | What Is Constrained | What Is Free |\n"
            "|----|----------|---------------------|--------------|\n"
            "| FV-1 | Config format | Must be JSON | Nesting depth |\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FV-1", result)

    def test_spec_with_fr_identifiers(self) -> None:
        """FR-N identifiers in spec are also accepted (not just FV-N)."""
        self._write_spec("test-spec", (
            "## Free Variables\n\n"
            "| Requirement | What Is Constrained | What Is Free |\n"
            "|-------------|---------------------|--------------|\n"
            "| FR-3 | Must have start script | Exact command string |\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FR-3", result)

    # --- Validation report fallback ---

    def test_fallback_to_validation_report(self) -> None:
        """When spec has no Free Variables, falls back to validation report."""
        self._write_spec("test-spec", "## Problem\nNo free variables section here.\n")
        self._write_validation("test-spec", (
            "## Validation Report\nScore: 85/100\n\n"
            "## Free Variables\n\n"
            "| Requirement | What Is Constrained | What Is Free |\n"
            "|-------------|---------------------|--------------|\n"
            "| FR-7 | Must create railway.json | Schema nesting |\n"
            "| FR-3 | Must have start script | Exact command string |\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FR-7", result)
        self.assertIn("FR-3", result)

    def test_spec_missing_validation_has_entries(self) -> None:
        """When spec file doesn't exist at all, validation report is used."""
        self._write_validation("test-spec", (
            "## Free Variables\n\n"
            "| Requirement | What Is Constrained | What Is Free |\n"
            "|-------------|---------------------|--------------|\n"
            "| FR-2 | Must create config | Schema format |\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FR-2", result)

    def test_spec_no_identifiers_falls_through_to_validation(self) -> None:
        """Spec with FV section but no FV-N/FR-N entries falls through to validation."""
        self._write_spec("test-spec", (
            "## Free Variables\n\n"
            "None — all observable behaviors are fully constrained.\n"
        ))
        self._write_validation("test-spec", (
            "## Free Variables\n\n"
            "| Requirement | What Is Constrained | What Is Free |\n"
            "|-------------|---------------------|--------------|\n"
            "| FR-5 | Must create tsconfig | Compiler options |\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FR-5", result)

    def test_spec_takes_precedence_over_validation(self) -> None:
        """Spec Free Variables are returned even when validation also has them."""
        self._write_spec("test-spec", (
            "## 5. Free Variables\n\n"
            "| ID | Variable | What Is Constrained | What Is Free |\n"
            "|----|----------|---------------------|--------------|\n"
            "| FV-1 | FROM SPEC | constrained | free |\n"
        ))
        self._write_validation("test-spec", (
            "## Free Variables\n\n"
            "| Requirement | What Is Constrained | What Is Free |\n"
            "|-------------|---------------------|--------------|\n"
            "| FR-1 | FROM VALIDATION | constrained | free |\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FV-1", result)
        self.assertIn("FROM SPEC", result)
        self.assertNotIn("FROM VALIDATION", result)

    # --- Empty / missing cases ---

    def test_neither_has_free_variables(self) -> None:
        """Returns empty when neither file has a Free Variables section."""
        self._write_spec("test-spec", "## Problem\nJust a problem.\n")
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertEqual(result, "")

    def test_no_files_exist(self) -> None:
        """Returns empty when spec and validation files don't exist."""
        result = _extract_free_variables(self.tmp_dir, "nonexistent-spec")
        self.assertEqual(result, "")

    def test_empty_spec_file(self) -> None:
        """Returns empty when spec file is empty (0 bytes)."""
        self._write_spec("test-spec", "")
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertEqual(result, "")

    def test_body_without_identifiers_returns_empty(self) -> None:
        """Returns empty when FV section body has no FV-N or FR-N identifiers."""
        self._write_spec("test-spec", (
            "## 12. Free Variables (implementation choices)\n\n"
            "None — all observable behaviors are fully constrained.\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertEqual(result, "")

    def test_headers_only_returns_empty(self) -> None:
        """Returns empty when FV section has table headers but no data rows."""
        self._write_spec("test-spec", (
            "## 12. Free Variables\n\n"
            "| ID | Variable | What Is Constrained | What Is Free |\n"
            "|----|----------|---------------------|--------------|\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertEqual(result, "")

    def test_prose_without_identifiers_returns_empty(self) -> None:
        """Returns empty when FV section has prose but no FV-N/FR-N identifiers."""
        self._write_spec("test-spec", (
            "## Free Variables\n\n"
            "The implementer may choose any config format they prefer.\n"
            "No specific constraints apply.\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertEqual(result, "")

    # --- Section boundary handling ---

    def test_free_variables_as_last_section(self) -> None:
        """Extraction works when Free Variables is the last section (no trailing ##)."""
        self._write_spec("test-spec", (
            "## 1. Problem\nSome problem.\n\n"
            "## 2. Free Variables\n\n"
            "| ID | Variable | What Is Constrained | What Is Free |\n"
            "|----|----------|---------------------|--------------|\n"
            "| FV-1 | Nesting | Must have key | Depth |\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FV-1", result)

    def test_free_variables_mid_document(self) -> None:
        """Extraction stops at the next heading; following content excluded."""
        self._write_spec("test-spec", (
            "## 10. Free Variables\n\n"
            "| ID | Variable | What Is Constrained | What Is Free |\n"
            "|----|----------|---------------------|--------------|\n"
            "| FV-1 | Config | Must be JSON | Nesting |\n\n"
            "## 11. Out of Scope\n\n"
            "- Not this content\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FV-1", result)
        self.assertNotIn("Not this content", result)

    def test_case_insensitive(self) -> None:
        """Heading match is case-insensitive."""
        self._write_spec("test-spec", (
            "## FREE VARIABLES\n\n"
            "| ID | Variable | What Is Constrained | What Is Free |\n"
            "|----|----------|---------------------|--------------|\n"
            "| FV-1 | Config | Must be JSON | Nesting |\n"
        ))
        result = _extract_free_variables(self.tmp_dir, "test-spec")
        self.assertIn("FV-1", result)


if __name__ == "__main__":
    unittest.main()
