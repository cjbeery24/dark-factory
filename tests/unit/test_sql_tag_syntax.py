"""Precheck — malformed ``sql`` tagged-template syntax catch.

Motivated by the 2026-04-21 Phase E run (budget-brain-v2 /
2026-03-28-database-foundation) that revealed LLM validator variance:
the same spec bytes (sha256 03880360…) produced 4 PASS and 1 FAIL
across 5 runs, with the FAIL correctly catching a malformed
``sql'SELECT ...'`` in AC-5. This rule catches that defect class
mechanically so the decision doesn't depend on the validator's
stochastic output.
"""
from __future__ import annotations

from darkfactory.adapters.postgres.sql_tag_syntax import (
    SqlTagSyntaxFinding,
    check_sql_tag_syntax,
)
from darkfactory.spec.precheck import run_precheck
from darkfactory.spec.spec_model import parse_spec_model


def _model(markdown: str):
    return parse_spec_model(markdown)


# ---------------------------------------------------------------------------
# Positive cases — the rule MUST fire.
# ---------------------------------------------------------------------------


def test_catches_single_quoted_sql_tag_with_select():
    """The exact AC-5 defect from budget-brain-v2 2026-03-28-database-foundation."""
    markdown = (
        "# Spec\n\n"
        "```typescript\n"
        "await tx.execute(sql'SELECT current_setting(\\'app.tenant_id\\')');\n"
        "```\n"
    )
    findings = check_sql_tag_syntax(_model(markdown))
    assert len(findings) == 1
    assert findings[0].quote_char == "'"
    assert "SELECT" in findings[0].context


def test_catches_double_quoted_sql_tag():
    markdown = (
        "# Spec\n\n"
        "```ts\n"
        'await db.execute(sql"SET LOCAL app.tenant_id = \'x\'");\n'
        "```\n"
    )
    findings = check_sql_tag_syntax(_model(markdown))
    assert len(findings) == 1
    assert findings[0].quote_char == '"'


def test_catches_multiple_occurrences_in_one_fence():
    markdown = (
        "# Spec\n\n"
        "```typescript\n"
        "execute(sql'BEGIN');\n"
        "execute(sql'SELECT 1');\n"
        "execute(sql'COMMIT');\n"
        "```\n"
    )
    findings = check_sql_tag_syntax(_model(markdown))
    assert len(findings) == 3


def test_catches_across_language_aliases_ts_tsx_js_jsx_mts_cts_mjs_cjs():
    for lang in ("typescript", "ts", "tsx", "mts", "cts",
                 "javascript", "js", "jsx", "mjs", "cjs"):
        markdown = f"# S\n\n```{lang}\nexecute(sql'SELECT 1');\n```\n"
        assert check_sql_tag_syntax(_model(markdown)), (
            f"rule must fire in `{lang}` fence"
        )


def test_line_number_is_absolute_in_spec():
    # Content inside the fence starts at line 4 (lines 1=heading,
    # 2=blank, 3=fence open, 4=the offending line).
    markdown = (
        "# Spec\n"
        "\n"
        "```typescript\n"
        "execute(sql'SELECT 1');\n"
        "```\n"
    )
    findings = check_sql_tag_syntax(_model(markdown))
    assert len(findings) == 1
    assert findings[0].line_number == 4


def test_message_instructs_backtick_fix():
    markdown = "# S\n\n```ts\nexecute(sql'SELECT 1');\n```\n"
    [finding] = check_sql_tag_syntax(_model(markdown))
    assert "backtick" in finding.message.lower()


# ---------------------------------------------------------------------------
# Inline-code-span surface — markdown table cells, prose with backticks.
# ---------------------------------------------------------------------------


def test_catches_defect_in_markdown_table_cell():
    """The real-world defect lives inside a markdown table cell with
    inline backticks — not a fenced JS/TS block. The rule must still
    fire when ``raw_text`` is supplied."""
    markdown = (
        "# Spec\n\n"
        "## Acceptance Criteria\n\n"
        "| ID | Criterion |\n"
        "|----|-----------|\n"
        "| AC-5 | WHEN `tx.execute(sql'SELECT current_setting(\\'x\\')')` is called THEN it returns 'x' |\n"
    )
    findings = check_sql_tag_syntax(_model(markdown), raw_text=markdown)
    assert len(findings) == 1
    assert findings[0].quote_char == "'"


def test_does_not_fire_on_prose_without_backticks():
    """Raw-text scan must only fire inside inline code spans to bound
    false-positive risk on narrative text."""
    markdown = (
        "# Spec\n\n"
        "Imagine someone writing sql'SELECT x' in prose — we should\n"
        "ignore it because it isn't wrapped in backticks.\n"
    )
    assert check_sql_tag_syntax(_model(markdown), raw_text=markdown) == []


def test_dedupes_between_fence_and_inline_scan():
    """A JS/TS fence whose content is NOT in inline backticks is caught
    by the fence surface; the raw-text surface must not double-count."""
    markdown = (
        "# S\n\n```ts\nexecute(sql'SELECT 1');\n```\n"
    )
    findings = check_sql_tag_syntax(_model(markdown), raw_text=markdown)
    assert len(findings) == 1


# ---------------------------------------------------------------------------
# Negative cases — the rule MUST NOT fire (no false positives).
# ---------------------------------------------------------------------------


def test_does_not_fire_on_valid_backtick_tagged_template():
    markdown = (
        "# S\n\n"
        "```ts\n"
        "await tx.execute(sql`SET LOCAL app.tenant_id = ${tenantId}`);\n"
        "```\n"
    )
    assert check_sql_tag_syntax(_model(markdown)) == []


def test_does_not_fire_on_valid_function_call_form():
    """Drizzle also exposes ``sql`` as a function — ``sql(parts, ...)``.

    That form is valid and must not be flagged.
    """
    markdown = (
        "# S\n\n"
        "```ts\n"
        "const q = sql('SELECT 1');  // hypothetical function-call form\n"
        "```\n"
    )
    assert check_sql_tag_syntax(_model(markdown)) == []


def test_does_not_fire_on_sql_fenced_block():
    """Pure SQL (```sql``` fence) is a different check — A2's sql_lint.

    The tagged-template rule only scans TS/JS fences.
    """
    markdown = "# S\n\n```sql\nSELECT current_setting('x');\n```\n"
    assert check_sql_tag_syntax(_model(markdown)) == []


def test_does_not_fire_on_prefixed_identifiers():
    """``mysql'...'`` / ``xsql'...'`` / ``postsql"..."`` aren't the
    Drizzle identifier — the word-boundary anchor keeps them out."""
    for prefixed in ("mysql", "xsql", "postsql"):
        markdown = f"# S\n\n```ts\nconst q = {prefixed}'SELECT 1';\n```\n"
        assert check_sql_tag_syntax(_model(markdown)) == [], (
            f"false positive on `{prefixed}`"
        )


def test_does_not_fire_when_not_followed_by_sql_keyword():
    """Narrow-by-design: a SQL keyword must follow the quote. This
    bounds false-positive risk on arbitrary quoted data that happens
    to contain ``sql`` followed by a quote."""
    markdown = (
        "# S\n\n"
        "```ts\n"
        "const msg = 'sql\\'s broken';  // string ends early\n"
        "```\n"
    )
    assert check_sql_tag_syntax(_model(markdown)) == []


def test_does_not_fire_on_python_fence():
    markdown = (
        "# S\n\n```python\nsql = 'SELECT 1'\n```\n"
    )
    assert check_sql_tag_syntax(_model(markdown)) == []


def test_does_not_fire_when_no_fences():
    markdown = "# S\n\nProse only, no fences.\n"
    assert check_sql_tag_syntax(_model(markdown)) == []


# ---------------------------------------------------------------------------
# Precheck integration — the finding is FATAL.
# ---------------------------------------------------------------------------


def test_precheck_sets_fatal_when_malformed_sql_tag_present(tmp_path):
    """Precheck must set ``fatal=True`` when this rule fires; otherwise
    the orchestrator falls through to the LLM validator (which we've
    empirically shown can pass the same defect stochastically)."""
    # Minimal spec that parses cleanly except for the tagged-template
    # defect. The other checks may emit advisories; what we pin here is
    # the FATAL bit specifically.
    spec_body = (
        "# Spec\n\n"
        "## Interface Contract\n\n"
        "```ts\n"
        "await tx.execute(sql'SELECT 1');\n"
        "```\n"
    )
    spec_file = tmp_path / "test-spec.md"
    spec_file.write_text(spec_body)
    result = run_precheck(spec_file)
    assert result.fatal is True, (
        f"expected fatal=True when sql_tag_syntax fires; summary:\n{result.summary}"
    )
    assert result.passed is False
    assert len(result.sql_tag_syntax) == 1


def test_precheck_does_not_set_fatal_on_clean_spec(tmp_path):
    spec_body = (
        "# Spec\n\n"
        "## Interface Contract\n\n"
        "```ts\n"
        "await tx.execute(sql`SELECT 1`);\n"
        "```\n"
    )
    spec_file = tmp_path / "clean.md"
    spec_file.write_text(spec_body)
    result = run_precheck(spec_file)
    # ``fatal`` reflects the sql_tag_syntax rule specifically; the spec
    # may still be passed=False for other structural reasons (missing
    # required sections, etc.), but fatal must be False.
    assert result.fatal is False
    assert result.sql_tag_syntax == []
