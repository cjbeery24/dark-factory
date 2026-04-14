"""A2 — SQL syntax lint on fenced sql blocks.

Motivated by 2026-04-14 database-foundation escalation. See
``docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md``
(Sprint 1, A2).
"""
from __future__ import annotations

import pytest

pglast = pytest.importorskip("pglast")

from darkfactory.spec.spec_model import parse_spec_model
from darkfactory.spec.sql_lint import (
    SqlLintFinding,
    lint_sql_fences,
    sql_fences_present,
)


def _model_from(markdown: str):
    return parse_spec_model(markdown)


# ---------------------------------------------------------------------------
# Presence detection.
# ---------------------------------------------------------------------------


def test_sql_fences_present_false_when_no_fences():
    model = _model_from("# Spec\n\nNo SQL here.\n")
    assert sql_fences_present(model) is False
    assert lint_sql_fences(model) == []


def test_sql_fences_present_false_for_non_sql_languages():
    markdown = (
        "# Spec\n\n"
        "```python\nprint('hi')\n```\n\n"
        "```yaml\nkey: value\n```\n"
    )
    model = _model_from(markdown)
    assert sql_fences_present(model) is False
    assert lint_sql_fences(model) == []


def test_sql_fences_present_true_for_sql_block():
    markdown = "# Spec\n\n```sql\nSELECT 1;\n```\n"
    model = _model_from(markdown)
    assert sql_fences_present(model) is True


# ---------------------------------------------------------------------------
# Valid SQL produces zero findings.
# ---------------------------------------------------------------------------


def test_valid_select_parses_clean():
    markdown = "# Spec\n\n```sql\nSELECT 1;\n```\n"
    model = _model_from(markdown)
    assert lint_sql_fences(model) == []


def test_valid_create_table_parses_clean():
    markdown = (
        "# Spec\n\n"
        "```sql\n"
        "CREATE TABLE users (\n"
        "  id uuid PRIMARY KEY,\n"
        "  email text NOT NULL UNIQUE\n"
        ");\n"
        "```\n"
    )
    model = _model_from(markdown)
    assert lint_sql_fences(model) == []


def test_alter_database_current_parses_clean():
    """The validator hallucination that motivated A2.

    PG16 accepts ``ALTER DATABASE CURRENT ...``; the validator's
    "invalid SQL" finding on this form was fiction.
    """
    markdown = (
        "# Spec\n\n"
        "```sql\n"
        "ALTER DATABASE CURRENT SET app.tenant_id = '';\n"
        "```\n"
    )
    model = _model_from(markdown)
    findings = lint_sql_fences(model)
    assert findings == [], (
        f"ALTER DATABASE CURRENT must parse clean; got: {findings}"
    )


def test_postmortem_rls_migration_parses_clean():
    """The full SQL fence from 2026-03-28-database-foundation spec."""
    markdown = (
        "## Examples\n\n"
        "```sql\n"
        "-- 0000_baseline_rls_infrastructure.sql\n"
        "\n"
        "-- Configure GUC for tenant isolation\n"
        "ALTER DATABASE CURRENT SET app.tenant_id = '';\n"
        "```\n"
    )
    model = _model_from(markdown)
    assert lint_sql_fences(model) == []


# ---------------------------------------------------------------------------
# Invalid SQL produces findings with useful metadata.
# ---------------------------------------------------------------------------


def test_syntax_error_produces_finding():
    markdown = (
        "## Bad\n\n"
        "```sql\n"
        "SELECT WHERE FROM\n"
        "```\n"
    )
    model = _model_from(markdown)
    [finding] = lint_sql_fences(model)
    assert isinstance(finding, SqlLintFinding)
    assert finding.line_number > 0
    assert finding.end_line >= finding.line_number
    assert finding.error  # non-empty parser message
    assert finding.section_path  # populated from the containing section
    assert "SELECT" in finding.snippet


def test_multiple_fences_each_linted_independently():
    markdown = (
        "## Mixed\n\n"
        "```sql\n"
        "SELECT 1;\n"
        "```\n\n"
        "```sql\n"
        "NOT VALID SQL ATALL\n"
        "```\n\n"
        "```sql\n"
        "CREATE TABLE t (id int);\n"
        "```\n"
    )
    model = _model_from(markdown)
    findings = lint_sql_fences(model)
    assert len(findings) == 1, (
        "only the middle block is invalid; surrounding valid blocks "
        "should not produce findings"
    )


def test_empty_sql_fence_is_skipped():
    markdown = (
        "## Empty\n\n"
        "```sql\n"
        "```\n"
    )
    model = _model_from(markdown)
    assert lint_sql_fences(model) == []


# ---------------------------------------------------------------------------
# Section path is useful for error messages.
# ---------------------------------------------------------------------------


def test_section_path_includes_nested_section():
    markdown = (
        "## Acceptance Criteria\n\n"
        "### AC-1\n\n"
        "```sql\n"
        "SELECT WHERE FROM\n"
        "```\n"
    )
    model = _model_from(markdown)
    [finding] = lint_sql_fences(model)
    assert "Acceptance Criteria" in finding.section_path


# ---------------------------------------------------------------------------
# Graceful degradation and error isolation.
# ---------------------------------------------------------------------------


def test_pglast_unavailable_returns_empty_and_warns(monkeypatch, caplog):
    """When pglast is not installed, the lint must no-op with a warning.

    Precheck must survive deployments without libpg_query. Simulate by
    forcing the availability cache to False — the guarded early-return
    at sql_lint.py should then skip parsing entirely.
    """
    from darkfactory.spec import sql_lint

    monkeypatch.setattr(sql_lint, "_PGLAST_AVAILABLE", False)

    markdown = "# Spec\n\n```sql\nSELECT 1;\n```\n"
    model = _model_from(markdown)
    # Sanity: a real SQL fence is present, so the early-return is the
    # *only* reason findings could be empty.
    assert sql_fences_present(model) is True

    with caplog.at_level("WARNING", logger="darkfactory.spec.sql_lint"):
        findings = lint_sql_fences(model)

    assert findings == []
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert any("pglast not installed" in r.getMessage() for r in warnings), (
        "graceful no-op must announce itself so downstream callers can "
        "distinguish 'no findings because valid' from 'no findings because "
        "lint skipped'"
    )


def test_non_parse_error_is_captured_not_raised(monkeypatch):
    """The broad ``except Exception`` branch must keep precheck alive.

    Simulate a surprise non-ParseError raised by the C parser. The lint
    must convert it into a finding (with the exception type prefixed to
    the error string) rather than propagating.
    """
    import darkfactory.spec.sql_lint as sql_lint

    # Force the cache to True so the import happens and we can swap
    # parse_sql out before it runs.
    monkeypatch.setattr(sql_lint, "_PGLAST_AVAILABLE", True)

    def _exploding_parse_sql(_body):
        raise RuntimeError("simulated C-parser surprise")

    import pglast
    monkeypatch.setattr(pglast, "parse_sql", _exploding_parse_sql)

    markdown = "# Spec\n\n```sql\nSELECT 1;\n```\n"
    model = _model_from(markdown)

    # Must not raise.
    [finding] = lint_sql_fences(model)
    assert finding.error.startswith("RuntimeError: "), (
        f"non-ParseError must be captured with its type prefix; got: "
        f"{finding.error!r}"
    )
    assert "simulated C-parser surprise" in finding.error
