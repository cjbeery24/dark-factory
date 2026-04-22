"""Unit tests for ``darkfactory.adapters.postgres.substrate_rules``.

The TOML file is the public-surface taxonomy that drives substrate
verification end-to-end. These tests pin three things:

1. The shipped ``substrate-rules.toml`` parses cleanly and exposes the
   triggers callers depend on (custom_guc, rls_policy, tenant_isolation).
2. Trigger pattern matching fires for the prose shapes the substrate
   verification plan (`docs/dev/2026-04-15-substrate-verification-plan.md`)
   uses as worked examples — and does NOT fire on adjacent-but-unrelated
   prose.
3. The loader rejects malformed inputs loudly (bad regex, missing
   trigger, schema-version drift) instead of degrading silently.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from darkfactory.adapters.postgres import substrate_rules as sr


class TestLoadShippedRules(unittest.TestCase):
    """The committed TOML file must load and expose the documented rules."""

    def setUp(self) -> None:
        # Each test gets a fresh load; the module-level cache would
        # otherwise let order-dependent state leak between tests.
        sr._rules_cache = None

    def test_load_returns_three_rules(self) -> None:
        rules = sr.load_substrate_rules()
        self.assertEqual(
            len(rules), 3,
            "shipped substrate-rules.toml should declare exactly 3 rules "
            "(custom_guc, rls_policy, tenant_isolation); update this test "
            "and the plan together if a new rule is added."
        )

    def test_triggers_are_documented_set(self) -> None:
        triggers = {r.trigger for r in sr.load_substrate_rules()}
        self.assertEqual(
            triggers,
            {"custom_guc", "rls_policy", "tenant_isolation"},
        )

    def test_each_rule_has_compiled_patterns(self) -> None:
        for rule in sr.load_substrate_rules():
            self.assertGreater(
                len(rule.match_patterns), 0,
                f"rule {rule.trigger!r} has no match_patterns",
            )
            for p in rule.match_patterns:
                self.assertIsInstance(p, re.Pattern)

    def test_required_primitives_non_empty_per_rule(self) -> None:
        for rule in sr.load_substrate_rules():
            self.assertGreater(
                len(rule.required_primitives), 0,
                f"rule {rule.trigger!r} declares no required_primitives — "
                f"a rule with no primitives can't constrain spec authoring",
            )

    def test_tenant_isolation_exposes_required_scenarios(self) -> None:
        """``required_scenarios`` is the field Piece 4 (scenario architect
        addendum) reads to decide what positive/negative pairs to demand;
        if the dataclass quietly drops it the SA addendum silently degrades."""
        rules = {r.trigger: r for r in sr.load_substrate_rules()}
        self.assertEqual(
            rules["tenant_isolation"].required_scenarios,
            ("positive_own_tenant_visible", "negative_other_tenant_invisible"),
        )

    def test_rls_policy_exposes_optional_force_primitive(self) -> None:
        """Task 43: ``rls_policy`` rule must expose ``force`` as an
        optional primitive so the validator addendum can tell spec
        authors it's available without flagging the absence of a
        ``force = true`` entry as a completeness error."""
        rules = {r.trigger: r for r in sr.load_substrate_rules()}
        self.assertIn("force", rules["rls_policy"].optional_primitives)

    def test_required_primitives_and_optional_primitives_are_disjoint(
        self,
    ) -> None:
        """An entry listed as both required and optional is a TOML
        authoring bug — the rule author meant one or the other. Catch
        it at load time rather than in a validator's completeness
        evaluation where the downstream message would be incoherent."""
        for rule in sr.load_substrate_rules():
            overlap = set(rule.required_primitives) & set(
                rule.optional_primitives
            )
            self.assertEqual(
                overlap, set(),
                f"rule {rule.trigger!r} lists {overlap!r} in BOTH "
                f"required_primitives and optional_primitives",
            )

    def test_load_returns_cached_instance(self) -> None:
        """Pin the cache contract — second call returns the same list, not
        a re-parsed copy. Callers that hold references across pipeline
        steps depend on the identity to detect taxonomy reloads."""
        first = sr.load_substrate_rules()
        second = sr.load_substrate_rules()
        self.assertIs(first, second)


class TestTriggerMatching(unittest.TestCase):
    """Pin the prose shapes each rule fires on. These are the worked
    examples the validator addendum points at; if matching drifts, spec
    authors get unhelpful or silent feedback."""

    def setUp(self) -> None:
        sr._rules_cache = None

    def test_custom_guc_fires_on_set_local_app_dot_pattern(self) -> None:
        text = "The handler issues `SET LOCAL app.tenant_id = $1` per request."
        triggered = {r.trigger for r in sr.triggered_rules(text)}
        self.assertIn("custom_guc", triggered)

    def test_custom_guc_fires_on_current_setting_lookup(self) -> None:
        text = "RLS policies read `current_setting('app.tenant_id', true)`."
        triggered = {r.trigger for r in sr.triggered_rules(text)}
        self.assertIn("custom_guc", triggered)

    def test_custom_guc_does_not_fire_on_unrelated_setting(self) -> None:
        # The rule targets the `app.*` GUC namespace deliberately;
        # `SET LOCAL statement_timeout` is a Postgres builtin, not a
        # custom GUC, and must not trigger substrate primitives.
        text = "The handler does `SET LOCAL statement_timeout = '5s'`."
        triggered = {r.trigger for r in sr.triggered_rules(text)}
        self.assertNotIn("custom_guc", triggered)

    def test_rls_policy_fires_on_enable_rls(self) -> None:
        text = "The migration runs `ALTER TABLE users ENABLE ROW LEVEL SECURITY`."
        triggered = {r.trigger for r in sr.triggered_rules(text)}
        self.assertIn("rls_policy", triggered)

    def test_rls_policy_fires_on_create_policy(self) -> None:
        text = "The migration emits `CREATE POLICY users_isolation ON users ...`."
        triggered = {r.trigger for r in sr.triggered_rules(text)}
        self.assertIn("rls_policy", triggered)

    def test_tenant_isolation_fires_on_isolation_prose(self) -> None:
        text = "Multi-tenant isolation: tenant A must not read tenant B's rows."
        triggered = {r.trigger for r in sr.triggered_rules(text)}
        self.assertIn("tenant_isolation", triggered)

    def test_tenant_isolation_fires_on_multi_tenant_phrase(self) -> None:
        text = "This is a multi-tenant API with strict row scoping."
        triggered = {r.trigger for r in sr.triggered_rules(text)}
        self.assertIn("tenant_isolation", triggered)

    def test_no_rules_fire_on_unrelated_prose(self) -> None:
        text = "The endpoint returns a JSON object with the user's display name."
        self.assertEqual(sr.triggered_rules(text), [])

    def test_no_rules_fire_on_empty_string(self) -> None:
        """A spec scan early in the pipeline can hit empty text (cleared
        section, in-progress draft). Empty input must return [], not raise."""
        self.assertEqual(sr.triggered_rules(""), [])


class TestLoaderValidation(unittest.TestCase):
    """Malformed TOML must fail loudly at load time, not at scan time."""

    def setUp(self) -> None:
        sr._rules_cache = None
        # Stash the real path so each test can swap it without races.
        self._real_path = sr._RULES_PATH

    def tearDown(self) -> None:
        sr._RULES_PATH = self._real_path
        sr._rules_cache = None

    def _write_and_load(self, content: str, tmp_path: Path) -> list[sr.SubstrateRule]:
        target = tmp_path / "substrate-rules.toml"
        target.write_text(content)
        sr._RULES_PATH = target
        sr._rules_cache = None
        return sr.load_substrate_rules()

    def test_missing_file_raises_runtime_error(self) -> None:
        sr._RULES_PATH = Path("/nonexistent/substrate-rules.toml")
        sr._rules_cache = None
        with self.assertRaisesRegex(RuntimeError, "missing"):
            sr.load_substrate_rules()

    def test_schema_version_mismatch_raises(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            content = "schema_version = 999\n"
            with self.assertRaisesRegex(RuntimeError, "schema_version"):
                self._write_and_load(content, tmp_path)

    def test_missing_trigger_field_raises(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            content = (
                "schema_version = 1\n"
                "[[rule]]\n"
                'match_patterns = ["foo"]\n'
            )
            with self.assertRaisesRegex(RuntimeError, "trigger"):
                self._write_and_load(content, tmp_path)

    def test_empty_match_patterns_raises(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            content = (
                "schema_version = 1\n"
                "[[rule]]\n"
                'trigger = "x"\n'
                "match_patterns = []\n"
            )
            with self.assertRaisesRegex(RuntimeError, "match_patterns"):
                self._write_and_load(content, tmp_path)

    def test_bad_regex_raises_at_load_time(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            content = (
                "schema_version = 1\n"
                "[[rule]]\n"
                'trigger = "x"\n'
                'match_patterns = ["[unclosed"]\n'
            )
            with self.assertRaisesRegex(RuntimeError, "invalid regex"):
                self._write_and_load(content, tmp_path)


class TestPackagingHygiene(unittest.TestCase):
    """The TOML file must ship next to the loader. A wheel that drops it
    breaks substrate verification silently — these checks make that
    breakage a unit-test failure."""

    def test_rules_file_exists_next_to_loader(self) -> None:
        self.assertTrue(
            sr._RULES_PATH.exists(),
            f"substrate-rules.toml missing at {sr._RULES_PATH} — "
            "packaging bug, not a runtime condition",
        )

    def test_rules_file_is_under_postgres_adapter(self) -> None:
        self.assertEqual(sr._RULES_PATH.parent.name, "postgres")


if __name__ == "__main__":
    unittest.main()
