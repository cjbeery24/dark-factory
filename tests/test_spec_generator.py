"""Tests for spec generation and template classification."""

from __future__ import annotations

from pathlib import Path

from darkfactory.spec.generator import classify_template, generate_spec, _slugify


class TestClassifyTemplate:
    def test_bug_fix_keywords(self) -> None:
        assert classify_template("Fix login crash on empty password") == "bug-fix"
        assert classify_template("There's a bug in the auth flow") == "bug-fix"
        assert classify_template("Broken pagination on search results") == "bug-fix"

    def test_refactor_keywords(self) -> None:
        assert classify_template("Refactor the user service") == "refactor"
        assert classify_template("Extract validation logic") == "refactor"
        assert classify_template("Rename UserManager to AccountService") == "refactor"

    def test_api_keywords(self) -> None:
        assert classify_template("Add rate limiting to the /api/upload endpoint") == "api"
        assert classify_template("New REST endpoint for user profiles") == "api"
        assert classify_template("Add webhook for order notifications") == "api"

    def test_db_migration(self) -> None:
        assert classify_template("Add migration for user preferences table") == "db-migration"
        assert classify_template("Database schema update for orders") == "db-migration"

    def test_security(self) -> None:
        assert classify_template("Fix XSS vulnerability in comments") == "security-fix"
        assert classify_template("CVE-2024-1234 patch") == "security-fix"

    def test_performance(self) -> None:
        assert classify_template("Optimize slow query in reports page") == "performance"
        assert classify_template("Reduce latency on dashboard load") == "performance"

    def test_integration(self) -> None:
        assert classify_template("Integrate Stripe SDK for payments") == "integration"
        assert classify_template("Third-party analytics provider support") == "integration"

    def test_default_is_feature(self) -> None:
        assert classify_template("Add user login page") == "feature"
        assert classify_template("Implement dark mode") == "feature"
        assert classify_template("Support multiple languages") == "feature"


class TestSlugify:
    def test_basic(self) -> None:
        assert _slugify("Add user authentication") == "add-user-authentication"

    def test_special_chars(self) -> None:
        assert _slugify("Fix /api/users endpoint!") == "fix-api-users-endpoint"

    def test_truncation(self) -> None:
        long = "a" * 100
        assert len(_slugify(long)) <= 60


class TestGenerateSpec:
    def test_generates_spec_file(self, tmp_path: Path) -> None:
        # Create templates directory with a feature template.
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "feature-spec.md").write_text("## Feature Template\n\n- [ ] FR-1:\n")

        spec_path = generate_spec(
            "Add dark mode toggle",
            tmp_path,
            framework_dir=tmp_path,
        )

        assert spec_path.exists()
        assert spec_path.name == "add-dark-mode-toggle.md"
        content = spec_path.read_text()
        assert "# Add dark mode toggle" in content
        assert "Template: feature" in content
        assert "## Feature Template" in content

    def test_template_override(self, tmp_path: Path) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "bug-fix-spec.md").write_text("## Bug Fix Template\n")

        spec_path = generate_spec(
            "Add user authentication",
            tmp_path,
            template_override="bug-fix",
            framework_dir=tmp_path,
        )

        content = spec_path.read_text()
        assert "Template: bug-fix" in content
        assert "## Bug Fix Template" in content

    def test_creates_specs_directory(self, tmp_path: Path) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "feature-spec.md").write_text("template content")

        spec_path = generate_spec("New feature", tmp_path, framework_dir=tmp_path)
        assert (tmp_path / ".dark-factory" / "specs").is_dir()

    def test_missing_template_raises(self, tmp_path: Path) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        # No template files created.
        import pytest
        with pytest.raises(FileNotFoundError):
            generate_spec("Add something", tmp_path, framework_dir=tmp_path)

    def test_api_template_auto_selection(self, tmp_path: Path) -> None:
        templates_dir = tmp_path / "templates"
        templates_dir.mkdir()
        (templates_dir / "api-spec.md").write_text("## API Template\n")

        spec_path = generate_spec(
            "Add rate limiting to the /api/upload endpoint",
            tmp_path,
            framework_dir=tmp_path,
        )

        content = spec_path.read_text()
        assert "Template: api" in content
