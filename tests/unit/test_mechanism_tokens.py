"""Tests for the Postgres mechanism-vs-observable token scanner (Proposal 4)."""
from __future__ import annotations

import textwrap
import unittest

from darkfactory.adapters.postgres.mechanism_tokens import (
    MechanismTokenFinding,
    check_mechanism_tokens,
    has_block_finding,
    load_mechanism_tokens,
    load_mechanism_tokens_from_text,
)


class TestLoader(unittest.TestCase):
    def test_default_blocklist_loads(self) -> None:
        tokens = load_mechanism_tokens()
        self.assertGreater(len(tokens), 0)
        # Each token has the required fields + a compiled pattern.
        for t in tokens:
            self.assertTrue(t.name)
            self.assertIn(t.severity, ("block", "warn"))
            self.assertTrue(t.reason)
            self.assertTrue(t.suggest)
            self.assertTrue(hasattr(t.pattern, "finditer"))

    def test_load_from_text_minimal(self) -> None:
        text = textwrap.dedent("""
            [[token]]
            pattern = '\\\\bfoo\\\\b'
            name = "foo_sample"
            severity = "block"
            reason = "sample"
            suggest = "use bar instead"
        """)
        tokens = load_mechanism_tokens_from_text(text)
        self.assertEqual(len(tokens), 1)
        self.assertEqual(tokens[0].name, "foo_sample")
        self.assertEqual(tokens[0].severity, "block")

    def test_empty_file_returns_empty_list(self) -> None:
        self.assertEqual(load_mechanism_tokens_from_text(""), [])

    def test_missing_field_raises(self) -> None:
        text = textwrap.dedent("""
            [[token]]
            pattern = '\\\\bfoo\\\\b'
            name = "x"
            # missing reason / suggest / severity
        """)
        with self.assertRaises(ValueError) as cm:
            load_mechanism_tokens_from_text(text)
        self.assertIn("missing required field", str(cm.exception))

    def test_bad_severity_raises(self) -> None:
        text = textwrap.dedent("""
            [[token]]
            pattern = '\\\\bfoo\\\\b'
            name = "x"
            severity = "maybe"
            reason = "x"
            suggest = "x"
        """)
        with self.assertRaises(ValueError) as cm:
            load_mechanism_tokens_from_text(text)
        self.assertIn("severity", str(cm.exception))

    def test_bad_regex_raises(self) -> None:
        text = textwrap.dedent("""
            [[token]]
            pattern = '[unclosed'
            name = "x"
            severity = "block"
            reason = "x"
            suggest = "x"
        """)
        with self.assertRaises(ValueError) as cm:
            load_mechanism_tokens_from_text(text)
        self.assertIn("invalid regex", str(cm.exception))


class TestCheckOnRealBlocklist(unittest.TestCase):
    """Exercise the shipped Postgres blocklist end-to-end.

    These tests pin the behavior of the curated token list so a spec-
    writer editing the TOML knows which semantics they're changing.
    """

    def test_plain_prose_no_findings(self) -> None:
        text = (
            "Every request executes within a tenant context. "
            "The app enforces access control so that tenant X's data "
            "cannot be observed by tenant Y's queries."
        )
        self.assertEqual(check_mechanism_tokens(text), [])

    def test_set_local_is_blocked(self) -> None:
        text = "The application must issue SET LOCAL before each query."
        findings = check_mechanism_tokens(text)
        names = {f.token_name for f in findings}
        self.assertIn("set_local", names)
        self.assertTrue(
            any(f.severity == "block" for f in findings if f.token_name == "set_local")
        )

    def test_app_guc_identifier_blocked(self) -> None:
        text = "The app sets `app.tenant_id` on each request."
        findings = check_mechanism_tokens(text)
        self.assertTrue(any(f.token_name == "app_guc_identifier" for f in findings))

    def test_app_guc_identifier_skips_framework_method_and_property_refs(self) -> None:
        r"""Regression: Fastify/Express/Koa ``app.<method>`` false positives.

        GUC names are multi-word snake_case (``app.tenant_id``); framework
        method and property names are single-word (``app.get``,
        ``app.close``, ``app.config``, ``app.hook``). Matching requires
        at least one underscore in the identifier body, so no
        framework-shape reference — call syntax, prose, or property
        chain — flags as a GUC.

        Linus review flagged that an earlier negative-lookahead fix only
        rescued call-syntax cases: prose mentions
        (``the \`app.get\` handler``) and property chains
        (``app.config.database``) still misfired. The underscore
        requirement closes all three shapes.
        """
        samples = [
            # Call syntax (the originally-reported false positive).
            "app.get('/users', handler);",
            "app.post('/users', { schema }, handler);",
            "await app.listen({ port: 8000 });",
            "app.register(require('@fastify/sensible'));",
            "app.use(express.json())",
            "app.delete('/users/:id', handler);",
            "app.patch('/users/:id', handler);",
            "await app.close()",
            "app.listen (port)",
            # Prose mentions of method names (Linus BUG-1 specifically).
            "the `app.get` handler dispatches the request",
            "we register `app.register` before `app.ready`",
            # Property-chain access — chain does not have ``_`` in the
            # first segment after ``app.``, so no match.
            "app.config.database returns the pool",
            "this.app.config = {}",
            "app.fastify.version",
        ]
        for text in samples:
            with self.subTest(text=text):
                hits = [
                    f for f in check_mechanism_tokens(text)
                    if f.token_name == "app_guc_identifier"
                ]
                self.assertEqual(
                    hits, [],
                    f"framework-shape reference falsely flagged: {text!r}",
                )

    def test_app_guc_identifier_flags_snake_case_value_references(self) -> None:
        # Multi-word snake_case identifiers match in every position:
        # prose, SQL value, assignment, call-form property access, EOL.
        samples = [
            "`app.tenant_id`",                          # inline code
            "the app.tenant_id GUC holds the tenant",   # prose
            "SELECT current_setting('app.tenant_id')",  # SQL value
            "app.tenant_id = 'x'",                      # assignment
            "app.tenant_id,",                           # followed by comma
            "app.tenant_id.",                           # end of sentence
            "app.tenant_id\n",                          # EOL
            # The identifier itself matches even when the spec then
            # chains a method on it — the regex matches ``app.tenant_id``
            # and does not care what follows.
            "app.tenant_id.split('-')",
            "app.tenant_id.split()",
            # Alternate GUC names, all snake_case with underscore.
            "app.user_id was undefined",
            "app.correlation_id carries the trace id",
            "app.audit_mode toggles write-logging",
        ]
        for text in samples:
            with self.subTest(text=text):
                hits = [
                    f for f in check_mechanism_tokens(text)
                    if f.token_name == "app_guc_identifier"
                ]
                self.assertEqual(
                    len(hits), 1,
                    f"snake_case GUC reference should fire exactly once: {text!r}",
                )

    def test_current_setting_blocked(self) -> None:
        text = "Verify with current_setting('app.tenant_id', true)."
        findings = check_mechanism_tokens(text)
        names = {f.token_name for f in findings}
        # Both the call name AND the app.* namespace trip.
        self.assertIn("current_setting_call", names)
        self.assertIn("app_guc_identifier", names)

    def test_row_level_security_blocked(self) -> None:
        text = "Tables must ENABLE ROW LEVEL SECURITY."
        findings = check_mechanism_tokens(text)
        self.assertIn(
            "row_level_security_statement",
            {f.token_name for f in findings},
        )

    def test_pg_catalog_reference_blocked(self) -> None:
        text = "Check pg_policies to verify policy attachment."
        findings = check_mechanism_tokens(text)
        self.assertIn("pg_catalog_reference", {f.token_name for f in findings})

    def test_drizzle_library_warn_only(self) -> None:
        text = "Use drizzle-orm for schema definition."
        findings = check_mechanism_tokens(text)
        drizzle = [f for f in findings if f.token_name == "drizzle_library"]
        self.assertEqual(len(drizzle), 1)
        self.assertEqual(drizzle[0].severity, "warn")
        # Warn-only findings must NOT trip the block gate.
        self.assertFalse(has_block_finding(drizzle))

    def test_guc_term_blocked(self) -> None:
        text = "The pipeline writes to a custom GUC per request."
        findings = check_mechanism_tokens(text)
        self.assertIn("guc_term", {f.token_name for f in findings})

    def test_tx_local_blocked(self) -> None:
        text = "The value is tx-local so peer connections don't observe it."
        findings = check_mechanism_tokens(text)
        self.assertIn("tx_local_scope", {f.token_name for f in findings})

    def test_findings_are_line_addressable(self) -> None:
        text = (
            "# Spec\n"
            "\n"
            "Some prose.\n"
            "The app must `SET LOCAL app.tenant_id = X`.\n"
            "Another line.\n"
        )
        findings = check_mechanism_tokens(text)
        set_local = [f for f in findings if f.token_name == "set_local"]
        self.assertEqual(len(set_local), 1)
        self.assertEqual(set_local[0].line_number, 4)
        self.assertIn("SET LOCAL", set_local[0].matched_text)

    def test_findings_deterministic_order(self) -> None:
        # Two different tokens matched multiple times — stable order by
        # (token_name, line_number).
        text = (
            "Use SET LOCAL here.\n"
            "And pg_policies there.\n"
            "Then SET LOCAL again.\n"
        )
        findings = check_mechanism_tokens(text)
        keys = [(f.token_name, f.line_number) for f in findings]
        self.assertEqual(sorted(keys), keys)


class TestHasBlockFinding(unittest.TestCase):
    def test_empty_list_is_false(self) -> None:
        self.assertFalse(has_block_finding([]))

    def test_only_warn_is_false(self) -> None:
        findings = [MechanismTokenFinding(
            token_name="w", severity="warn", line_number=1,
            matched_text="x", reason="r", suggest="s",
        )]
        self.assertFalse(has_block_finding(findings))

    def test_any_block_is_true(self) -> None:
        findings = [
            MechanismTokenFinding(
                token_name="w", severity="warn", line_number=1,
                matched_text="x", reason="r", suggest="s",
            ),
            MechanismTokenFinding(
                token_name="b", severity="block", line_number=2,
                matched_text="y", reason="r", suggest="s",
            ),
        ]
        self.assertTrue(has_block_finding(findings))


class TestTruncation(unittest.TestCase):
    def test_long_match_is_truncated(self) -> None:
        # Construct a token whose match spans >120 chars, ensure truncation
        # keeps the field bounded without silently swallowing the fact.
        tokens = load_mechanism_tokens_from_text(textwrap.dedent("""
            [[token]]
            pattern = 'aaaa+'
            name = "long_match"
            severity = "block"
            reason = "long"
            suggest = "short"
        """))
        text = "a" * 500
        findings = check_mechanism_tokens(text, tokens=tuple(tokens))
        self.assertEqual(len(findings), 1)
        self.assertTrue(findings[0].matched_text.endswith("..."))
        self.assertLessEqual(len(findings[0].matched_text), 120)


if __name__ == "__main__":
    unittest.main()
