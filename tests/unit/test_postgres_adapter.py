"""Unit tests for the A3 Postgres adapter — concrete behavior beyond
what the contract harness covers.

The shared adapter-contract battery at ``tests/adapters/test_adapter_contract.py``
already exercises every ABC-method return-type and value-constraint
rule against the adapter, via the parametrized ``adapter`` fixture in
``tests/adapters/conftest.py``. Those tests catch "does it satisfy the
ABC at all?" regressions. This file covers what the harness can't:

- Detection predicates (SQL files, docker-compose, connection strings).
- Finding translation from ``SqlLintFinding`` to the generic ``Finding``
  (severity, code, path breadcrumbs).
- Graceful degradation on missing/unreadable files.
"""
from __future__ import annotations

import unittest
from pathlib import Path

from darkfactory.adapters.postgres import PostgresAdapter, SqlLintFinding
from darkfactory.adapters.postgres.adapter import _sql_finding_to_finding
from darkfactory.spec.spec_model import parse_spec_model
from tests.helpers import TempDirTestCase

import darkfactory.adapters.postgres.adapter as _adapter_module


class TestDetectionSqlFiles(TempDirTestCase):
    def test_detects_root_sql_file(self) -> None:
        (self.tmp_dir / "schema.sql").write_text("CREATE TABLE t ();")
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_detects_nested_sql_file(self) -> None:
        migrations = self.tmp_dir / "db" / "migrations"
        migrations.mkdir(parents=True)
        (migrations / "0001_init.sql").write_text("-- noop")
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_ignores_sql_in_node_modules(self) -> None:
        """node_modules is vendored; a ``.sql`` file in a transitive dep
        must not trip detection on a non-Postgres project."""
        nm = self.tmp_dir / "node_modules" / "some-pkg"
        nm.mkdir(parents=True)
        (nm / "fixture.sql").write_text("-- noop")
        self.assertFalse(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_ignores_sql_in_dist_build_venv(self) -> None:
        for skip in ("dist", "build", ".venv", "target", "__pycache__"):
            path = self.tmp_dir / skip / "artifact"
            path.mkdir(parents=True, exist_ok=True)
            (path / "leftover.sql").write_text("-- noop")
        self.assertFalse(PostgresAdapter(project_dir=self.tmp_dir).detect())


class TestDetectionDockerCompose(TempDirTestCase):
    def test_detects_postgres_in_compose_yml(self) -> None:
        (self.tmp_dir / "docker-compose.yml").write_text(
            "services:\n  db:\n    image: postgres:15\n"
        )
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_detects_postgres_in_compose_yaml_variant(self) -> None:
        (self.tmp_dir / "docker-compose.yaml").write_text(
            "services:\n  database:\n    image: postgres:16-alpine\n"
        )
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_detects_postgresql_substring(self) -> None:
        (self.tmp_dir / "docker-compose.yml").write_text(
            "services:\n  db:\n    image: docker.io/postgresql/postgres:15\n"
        )
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_no_postgres_in_compose_does_not_trigger(self) -> None:
        (self.tmp_dir / "docker-compose.yml").write_text(
            "services:\n  redis:\n    image: redis:7\n"
        )
        self.assertFalse(PostgresAdapter(project_dir=self.tmp_dir).detect())


class TestDetectionConnectionStrings(TempDirTestCase):
    def test_detects_postgres_url_in_env_file(self) -> None:
        (self.tmp_dir / ".env").write_text(
            "DATABASE_URL=postgres://user:pw@localhost:5432/db\n"
        )
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_detects_postgresql_url_in_toml_config(self) -> None:
        (self.tmp_dir / "app.toml").write_text(
            'database = "postgresql://localhost/app"\n'
        )
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_detects_in_typescript_config(self) -> None:
        (self.tmp_dir / "drizzle.config.ts").write_text(
            'export default { dbCredentials: { url: "postgres://localhost/db" } };\n'
        )
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_ignores_connection_string_in_skip_dir(self) -> None:
        nm = self.tmp_dir / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "config.toml").write_text('url = "postgres://host/db"\n')
        self.assertFalse(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_ignores_non_config_file_extensions(self) -> None:
        """A ``.log`` file with a connection string is not a deliberate
        project config — skipping it keeps detection cheap."""
        (self.tmp_dir / "app.log").write_text(
            'Connecting to postgres://localhost/db\n'
        )
        self.assertFalse(PostgresAdapter(project_dir=self.tmp_dir).detect())


class TestDetectionPrunesSkipDirs(TempDirTestCase):
    """Pin that ``detect()`` does not descend into ``_SKIP_DIRS``.

    The prior ``Path.rglob`` implementation filtered skip-dir entries
    after yielding them — on a monorepo with a large ``node_modules``
    that meant every file got stat'd before being discarded. The
    ``os.walk`` + ``dirnames[:]`` mutation is the only mechanism that
    actually prunes descent; this test instruments ``os.walk`` to
    prove the mutation works.
    """

    def test_walk_does_not_descend_into_skip_dirs(self) -> None:
        """Instrument the os.walk that detect() uses. Skip dirs must
        appear in ``dirnames`` (so the mutation can remove them) but
        must never show up as a walked ``dirpath`` afterward."""
        import os

        # Create a project with legitimate content plus a node_modules
        # tree that should be pruned.
        (self.tmp_dir / "real.sql").unlink(missing_ok=True)  # defensive
        (self.tmp_dir / "app.py").write_text("# no signals here\n")
        nm = self.tmp_dir / "node_modules"
        (nm / "pkg-a").mkdir(parents=True)
        (nm / "pkg-b").mkdir(parents=True)
        (nm / "pkg-a" / "file.sql").write_text("-- decoy\n")
        (nm / "pkg-b" / "config.toml").write_text('url = "postgres://x/y"\n')

        walked_dirs: list[str] = []
        original_walk = os.walk

        def tracing_walk(path, *args, **kwargs):
            for dirpath, dirnames, filenames in original_walk(
                path, *args, **kwargs
            ):
                walked_dirs.append(dirpath)
                yield dirpath, dirnames, filenames

        # Patch the os.walk that the adapter module sees.
        _adapter_module.os.walk = tracing_walk  # type: ignore[attr-defined]
        try:
            result = PostgresAdapter(project_dir=self.tmp_dir).detect()
        finally:
            _adapter_module.os.walk = original_walk  # type: ignore[attr-defined]

        # No signals survived pruning, so detection is negative.
        self.assertFalse(result)

        # The critical assertion: node_modules subdirectories were
        # never walked. If the mutation ever regresses, this catches
        # it immediately.
        for walked in walked_dirs:
            self.assertNotIn(
                "node_modules",
                walked,
                f"os.walk descended into a skip dir: {walked!r}",
            )


class TestDetectionDeclarativeEnvironment(TempDirTestCase):
    """Move 1 (wire-contracts gap G1): detect Postgres from the
    ``.dark-factory/architecture/environment.toml`` contract when no
    filesystem signal is present.

    The original walk explicitly skips ``.dark-factory/`` (for good
    reason — runtime artifacts must not be re-scanned), which leaves
    a blind spot: specs whose implementation hasn't landed yet declare
    a postgres substrate service only in the environment contract, with
    no ``.sql`` files, no ``docker-compose.yml``, and no committed
    connection strings. The 2026-04-16 budget-brain-v2 run hit exactly
    this state — postgres never activated, so the wire-contract fix
    was dark.

    Detection remains monotonic (OR across signals, short-circuit on
    first hit); the declarative check is cheap (one file read) and
    runs before the walk.
    """

    def _env(self) -> Path:
        env_path = self.tmp_dir / ".dark-factory" / "architecture" / "environment.toml"
        env_path.parent.mkdir(parents=True, exist_ok=True)
        return env_path

    def test_detects_postgres_service_by_name(self) -> None:
        self._env().write_text(
            "[services.postgres]\nimage = \"postgres:16-alpine\"\n"
        )
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_detects_service_named_db_with_postgres_image(self) -> None:
        """The service key is operator-chosen; ``db`` is common. Match
        on the image field, not just the service name."""
        self._env().write_text(
            "[services.db]\nimage = \"postgres:15\"\n"
        )
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_detects_connection_string_in_inject_env(self) -> None:
        """This is the exact budget-brain-v2 shape — ``DATABASE_URL`` in
        the service's inject_env block."""
        self._env().write_text(
            "[services.postgres.inject_env]\n"
            "DATABASE_URL = \"postgresql://u:p@postgres:5432/db\"\n"
        )
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_non_postgres_service_does_not_trigger(self) -> None:
        self._env().write_text(
            "[services.redis]\nimage = \"redis:7\"\n"
        )
        self.assertFalse(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_missing_environment_toml_falls_through_to_walk(self) -> None:
        """The env-contract check is additive, not replacing — absence
        of the file must not short-circuit the existing walk."""
        # No env file, but a .sql file exists → walk still fires.
        (self.tmp_dir / "schema.sql").write_text("CREATE TABLE t ();")
        self.assertTrue(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_malformed_environment_toml_does_not_raise(self) -> None:
        """A broken env.toml must not crash detection — we fall through
        to the walk. The env-contract failure mode should surface at the
        architect or orchestrator layer, not here."""
        self._env().write_text("this is not valid { toml\n")
        # No other signals → False, no raise.
        self.assertFalse(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_empty_environment_toml_does_not_trigger(self) -> None:
        """``tomllib.loads("")`` returns ``{}``. The subsequent signal
        probes must all come up empty and return False — not crash."""
        self._env().write_text("")
        self.assertFalse(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_services_as_scalar_does_not_crash(self) -> None:
        """Mis-authored TOML could make ``services`` a scalar rather
        than a table. The ``isinstance(services, dict)`` guard is what
        keeps iteration from raising; this pins that guard."""
        self._env().write_text('services = "postgres"\n')
        # No crash. The scalar doesn't contain ``postgres://``, so
        # ``_any_pg_url`` also returns False.
        self.assertFalse(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_env_signal_preempts_walk_cost(self) -> None:
        """Cheap signal first: when env.toml declares postgres, the walk
        should not even be invoked. Instrument ``os.walk`` to pin this
        invariant — a future refactor that reorders the OR must update
        this test deliberately."""
        import os
        self._env().write_text(
            "[services.postgres]\nimage = \"postgres:16-alpine\"\n"
        )
        walk_calls: list[str] = []
        original_walk = os.walk

        def tracing_walk(path, *args, **kwargs):
            walk_calls.append(str(path))
            yield from original_walk(path, *args, **kwargs)

        _adapter_module.os.walk = tracing_walk  # type: ignore[attr-defined]
        try:
            result = PostgresAdapter(project_dir=self.tmp_dir).detect()
        finally:
            _adapter_module.os.walk = original_walk  # type: ignore[attr-defined]

        self.assertTrue(result)
        self.assertEqual(
            walk_calls, [],
            "env-contract signal must short-circuit before os.walk",
        )


class TestDetectionEdgeCases(TempDirTestCase):
    def test_nonexistent_dir_returns_false(self) -> None:
        missing = self.tmp_dir / "does-not-exist"
        self.assertFalse(PostgresAdapter(project_dir=missing).detect())

    def test_empty_dir_returns_false(self) -> None:
        self.assertFalse(PostgresAdapter(project_dir=self.tmp_dir).detect())

    def test_file_instead_of_dir_returns_false(self) -> None:
        some_file = self.tmp_dir / "file.txt"
        some_file.write_text("x")
        self.assertFalse(PostgresAdapter(project_dir=some_file).detect())

    def test_detect_without_project_dir_raises(self) -> None:
        """``self.project_dir`` is the single source of truth now; a None
        value reaching detect() is a caller wiring bug and must raise
        rather than silently returning False."""
        with self.assertRaises(RuntimeError):
            PostgresAdapter().detect()


class TestSpecTriggers(unittest.TestCase):
    """``PostgresAdapter.spec_triggers`` delegates to the substrate
    taxonomy at ``substrate-rules.toml``. It is the G1 backstop against
    a project-scan that misses postgres but a spec that demands it (the
    2026-04-16 database-foundation run failure mode — declarative
    evidence in environment.toml made it into the project but the
    test-engineer still hand-rolled wire-unaware predicates because the
    adapter was project-dark).

    The trigger patterns live in ``substrate-rules.toml``; this test
    pins the delegation, not the specific patterns (which are tested in
    ``test_substrate_rules.py``)."""

    def test_custom_guc_pattern_activates_adapter(self) -> None:
        """``SET LOCAL app.tenant_id`` matches the ``custom_guc`` rule."""
        adapter = PostgresAdapter()
        self.assertTrue(adapter.spec_triggers("SET LOCAL app.tenant_id = 'a'"))

    def test_rls_pattern_activates_adapter(self) -> None:
        """``ENABLE ROW LEVEL SECURITY`` matches the ``rls_policy`` rule."""
        adapter = PostgresAdapter()
        self.assertTrue(
            adapter.spec_triggers("ALTER TABLE users ENABLE ROW LEVEL SECURITY;")
        )

    def test_tenant_isolation_pattern_activates_adapter(self) -> None:
        """``tenant isolation`` / ``multi-tenant`` matches the
        ``tenant_isolation`` rule."""
        adapter = PostgresAdapter()
        self.assertTrue(
            adapter.spec_triggers("The app enforces tenant isolation via RLS.")
        )

    def test_text_without_postgres_triggers_returns_false(self) -> None:
        """Spec text that doesn't mention any postgres substrate concept
        does not activate the adapter. Without this, the adapter would
        activate on every spec — defeating the entire per-spec
        discrimination this hook exists to provide."""
        adapter = PostgresAdapter()
        self.assertFalse(
            adapter.spec_triggers("Add a health-check endpoint at /healthz.")
        )

    def test_empty_spec_text_returns_false(self) -> None:
        """Empty string is never a trigger match — a degenerate input
        must not false-positive."""
        adapter = PostgresAdapter()
        self.assertFalse(adapter.spec_triggers(""))


class TestPrecheckFindingTranslation(unittest.TestCase):
    """``PostgresAdapter.precheck`` must map ``SqlLintFinding`` into the
    unified ``Finding`` shape — severity, code, section breadcrumb."""

    def test_section_path_becomes_slash_breadcrumb(self) -> None:
        sf = SqlLintFinding(
            line_number=42,
            end_line=48,
            section_path=("Spec", "Acceptance Criteria"),
            error="syntax error at or near FOO",
            snippet="INVALID SQL...",
        )
        f = _sql_finding_to_finding(sf)
        self.assertEqual(f.severity, "warning")
        self.assertEqual(f.code, "pg-sql-parse-error")
        self.assertEqual(f.path, "Spec/Acceptance Criteria")
        self.assertEqual(f.line, 42)
        self.assertIn("syntax error", f.message)
        self.assertIn("INVALID SQL", f.message)

    def test_root_level_fence_has_empty_path(self) -> None:
        sf = SqlLintFinding(
            line_number=1,
            end_line=3,
            section_path=(),
            error="unexpected token",
            snippet="BAD",
        )
        f = _sql_finding_to_finding(sf)
        self.assertEqual(f.path, "")
        self.assertEqual(f.severity, "warning")


class TestPrecheckIntegration(TempDirTestCase):
    """``precheck`` delegates to ``lint_sql_fences``; confirm end-to-end
    the findings propagate with correct shape. Skips when pglast is
    unavailable (matches ``lint_sql_fences``'s own no-op contract)."""

    def test_valid_sql_produces_no_findings(self) -> None:
        try:
            import pglast  # noqa: F401
        except ImportError:
            self.skipTest("pglast not installed")

        spec = parse_spec_model("# Spec\n\n```sql\nSELECT 1;\n```\n")
        findings = PostgresAdapter(project_dir=self.tmp_dir).precheck(spec)
        self.assertEqual(findings, [])

    def test_invalid_sql_produces_finding(self) -> None:
        try:
            import pglast  # noqa: F401
        except ImportError:
            self.skipTest("pglast not installed")

        spec = parse_spec_model(
            "# Spec\n\n```sql\nTHIS IS NOT SQL AT ALL;\n```\n"
        )
        findings = PostgresAdapter(project_dir=self.tmp_dir).precheck(spec)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, "warning")
        self.assertEqual(findings[0].code, "pg-sql-parse-error")

    def test_no_sql_fences_produces_no_findings(self) -> None:
        spec = parse_spec_model("# Spec\n\nNo SQL here.\n")
        findings = PostgresAdapter(project_dir=self.tmp_dir).precheck(spec)
        self.assertEqual(findings, [])


class TestSpecValidatorAddendumWireLanguage(unittest.TestCase):
    """The validator addendum teaches the LLM to reject ACs that state
    SQL-semantic predicates ("returns NULL") over library surfaces that
    wire-surface differently (postgres.js returns ''), and to reject
    specs that mix drizzle-orm and postgres.js ``sql`` tags in one
    ``execute`` call. Pinned here so a future prose edit that drops
    either rule breaks this test instead of the pipeline.

    Motivation: 2026-04-16 database-foundation escalation — the
    test-engineer faithfully encoded AC-12's "returns NULL" as
    ``toBeNull()``, which postgres.js never satisfies; and mixed the
    two ``sql`` tags inside ``tx.execute(...)`` because the spec's
    Wire Contract block warned only the implementer.
    """

    def test_rendered_addendum_names_both_wire_traps(self) -> None:
        rendered = PostgresAdapter().spec_validator_addendum()
        # Trap 1: the nullish rule must name current_setting, the
        # empty-string wire behavior, and the word "nullish" so the
        # verdict instruction is grounded in the specific call.
        self.assertIn("current_setting", rendered)
        self.assertIn("nullish", rendered)
        self.assertIn("empty string", rendered)
        # Trap 2: the sql-tag identity rule must name both libraries
        # and the specific API (tx.execute) that fails at runtime when
        # the tags are mixed.
        self.assertIn("drizzle-orm", rendered)
        self.assertIn("postgres.js", rendered)
        self.assertIn("tx.execute", rendered)

    def test_rendered_addendum_under_governance_cap(self) -> None:
        """Governance cap is enforced by the contract harness on the
        rendered composition; pin it here too so a wire-language-only
        edit that busts the budget is caught in a test named for what
        it regulates, not just the shared harness."""
        from darkfactory.adapters.base import (
            GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP,
        )

        rendered = PostgresAdapter().spec_validator_addendum()
        non_blank = [ln for ln in rendered.splitlines() if ln.strip()]
        self.assertLessEqual(
            len(non_blank),
            GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP,
            f"rendered addendum has {len(non_blank)} non-blank lines "
            f"(cap = {GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP})",
        )

    def test_rule1_critical_issue_is_proximate_to_current_setting(self) -> None:
        """A raw CRITICAL_ISSUE count is a tautology: any edit that
        sprinkles the token anywhere passes. Pin the verdict by
        proximity — `CRITICAL_ISSUE` must appear within the same
        window as `current_setting` so a future edit that deletes the
        nullish verdict but adds a third unrelated `CRITICAL_ISSUE`
        breaks this test.

        Window of 400 chars is generous enough to span the verdict
        block + its rule body without matching across the whole file.
        """
        rendered = PostgresAdapter().spec_validator_addendum()
        self._assert_token_near(rendered, "current_setting", "CRITICAL_ISSUE", 400)

    def test_rule2_critical_issue_is_proximate_to_drizzle(self) -> None:
        """Same proximity pin for Rule 2 — the ambiguous-`sql` trap.
        Deleting the drizzle/postgres.js verdict breaks this test even
        if a third unrelated `CRITICAL_ISSUE` is added.
        """
        rendered = PostgresAdapter().spec_validator_addendum()
        self._assert_token_near(rendered, "drizzle-orm", "CRITICAL_ISSUE", 400)

    def _assert_token_near(
        self,
        text: str,
        anchor: str,
        target: str,
        window: int,
    ) -> None:
        """Fail unless ``target`` appears within ``window`` chars of
        some occurrence of ``anchor`` in ``text`` (either direction)."""
        idx = 0
        while True:
            pos = text.find(anchor, idx)
            if pos < 0:
                break
            start = max(0, pos - window)
            end = min(len(text), pos + len(anchor) + window)
            if target in text[start:end]:
                return
            idx = pos + 1
        self.fail(
            f"no occurrence of {target!r} within {window} chars of "
            f"{anchor!r} in rendered addendum"
        )


class TestContractsDir(unittest.TestCase):
    def test_points_to_packaged_ts_contracts(self) -> None:
        """PostgresAdapter overrides the ABC default (None) and points at
        the adjacent ``contracts/ts`` directory. The orchestrator reads
        this at scenario-gen time to stage wire-contract helpers under
        ``.dark-factory/scenarios/<spec>/_contracts/postgres/``.
        """
        contracts = PostgresAdapter().contracts_dir()
        self.assertIsNotNone(contracts)
        assert contracts is not None  # narrow for mypy
        self.assertTrue(contracts.is_dir(), f"{contracts} must be a directory")
        # Pin the expected filesystem layout so a refactor that moves
        # the directory must update this assertion deliberately.
        self.assertEqual(contracts.name, "ts")
        self.assertEqual(contracts.parent.name, "contracts")

    def test_staged_layout_contains_index(self) -> None:
        """The directory must contain at least the index.ts barrel; the
        orchestrator copies the whole tree verbatim."""
        contracts = PostgresAdapter().contracts_dir()
        assert contracts is not None
        self.assertTrue((contracts / "index.ts").is_file())

    def test_ships_per_test_reset_files(self) -> None:
        """Sprint 28/29: the per-test DB clean-state reset (setupFile + its SQL)
        live in the dedicated db_reset_setup_dir() (setup/ts), DECOUPLED from
        contracts/ts so they stage on a DATABASE-active signal, not the
        substrate-manifest gate."""
        setup = PostgresAdapter().db_reset_setup_dir()
        assert setup is not None
        self.assertTrue((setup / "_df_reset.setup.ts").is_file())
        self.assertTrue((setup / "_df_reset.sql").is_file())

    def test_reset_files_not_in_contracts_dir(self) -> None:
        """Sprint 29 decoupling invariant: the reset must NOT live under
        contracts_dir() — staging it into _contracts/ would trip Check 18
        (a .ts no test imports) and re-couple it to the substrate-manifest gate."""
        contracts = PostgresAdapter().contracts_dir()
        assert contracts is not None
        self.assertFalse((contracts / "_df_reset.setup.ts").exists())
        self.assertFalse((contracts / "_df_reset.sql").exists())

    def test_reset_sql_invariants(self) -> None:
        """The reset SQL must reset identity (so id==1 holds per test), CASCADE
        (FK cycles), be catalog-driven, exclude migration-tracking tables (else
        the next migration state re-breaks), and bound lock acquisition (so a
        stray open tx surfaces an error instead of hanging the gate)."""
        setup = PostgresAdapter().db_reset_setup_dir()
        assert setup is not None
        sql = (setup / "_df_reset.sql").read_text()
        self.assertIn("RESTART IDENTITY", sql)
        self.assertIn("CASCADE", sql)
        self.assertIn("pg_tables", sql)            # catalog-driven, not hardcoded
        self.assertIn("alembic_version", sql)      # migration table excluded (py)
        self.assertIn("\\_\\_%", sql)              # __drizzle_migrations excluded
        self.assertIn("lock_timeout", sql)

    def test_reset_setup_is_driver_adaptive(self) -> None:
        """The setupFile must resolve whichever driver the impl installed
        (postgres.js OR pg) from the gate-provided base, and read the SQL at
        runtime (single source) — not hardcode one driver."""
        setup = PostgresAdapter().db_reset_setup_dir()
        assert setup is not None
        ts = (setup / "_df_reset.setup.ts").read_text()
        self.assertIn("'postgres'", ts)
        self.assertIn("'pg'", ts)
        self.assertIn("DF_RESET_RESOLVE_BASE", ts)
        self.assertIn("_df_reset.sql", ts)
        self.assertIn("beforeEach", ts)


class TestScenarioArchitectAddendumWireContracts(unittest.TestCase):
    """The scenario-architect addendum must advertise the staged
    wire-contract helpers by name and by import path. Without this,
    the test-engineer sees the helpers on disk (post-staging) but has
    no prompt-level pointer to prefer them over hand-rolled predicates
    — which is precisely the 2026-04-16 escalation failure mode. If
    Step-4 ever renames the ``_contracts/<adapter>`` stage path, this
    test fails loudly instead of silently leaving the addendum pointing
    at a path that no longer exists."""

    def test_addendum_names_all_helpers(self) -> None:
        text = PostgresAdapter().scenario_architect_addendum()
        for helper in (
            "assertUnsetGucIsNullish",
            "assertAppSetsTxGuc",
            "assertTxGucRoundTrips",
            "assertRlsEnabled",
            "assertTenantSeesOwnRows",
            "assertTenantCannotSeeOtherRows",
        ):
            self.assertIn(helper, text, (
                f"scenario-architect addendum must mention {helper} so "
                f"the test-engineer imports the staged helper instead "
                f"of hand-rolling the wire predicate"
            ))

    def test_addendum_references_stage_path(self) -> None:
        """Must literally contain ``_contracts/postgres`` — that is the
        path ``stage_adapter_contracts`` writes to. If staging moves,
        one of these two ends must move; this test ensures the addendum
        is not silently orphaned."""
        text = PostgresAdapter().scenario_architect_addendum()
        self.assertIn("_contracts/postgres", text)


class TestQualityGateDispatch(unittest.TestCase):
    def test_returns_empty_gate_commands(self) -> None:
        """Postgres contributes no build/test commands — language
        adapter owns the toolchain. All phases must be empty."""
        from darkfactory.spec.manifest import ArchitectureManifest

        manifest = ArchitectureManifest()
        gc = PostgresAdapter().quality_gate_dispatch(manifest)
        for phase, cmd in gc.phases():
            self.assertEqual(cmd, "", f"phase {phase} must be empty for Postgres")


class TestProposalRehearsalOptOut(TempDirTestCase):
    def test_returns_none(self) -> None:
        """Postgres has no separate typecheck pass; rehearsal opts out
        with ``None`` (not an empty list — the distinction is
        documented in the ABC)."""
        result = PostgresAdapter().proposal_rehearsal_typecheck(self.tmp_dir)
        self.assertIsNone(result)


class _FakeCursor:
    """Context-manager cursor with substring-keyed canned rows.

    Each ``execute(sql)`` matches its argument against a dict of
    ``{sql_substring: row}`` entries and the first matching key wins.
    Substring keying makes tests readable (the test author writes the
    substring they care about) and resilient to internal call-order
    refactors in helpers like ``_check_rls_tables`` — a positional
    queue would couple every test to the helper's internal sequence.

    A ``None`` row (vs a one-tuple containing ``None``) models "zero
    rows returned" — exactly what ``_scalar``'s ``row_present`` half
    consumes to distinguish "no such table" from "row has NULL value".
    """

    def __init__(self, rows_by_substring: dict) -> None:
        # Disjointness guard (Linus pass 1 Q5): a key that is a
        # substring of another key silently steals its dispatch — the
        # exact bug that bit Task 43 authoring when ``"rowsecurity"``
        # matched ``"relforcerowsecurity"`` in the force-check query.
        # Catching it at construction turns the silent miss-dispatch
        # into a loud test-authoring failure.
        keys = list(rows_by_substring.keys())
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if a in b or b in a:
                    raise AssertionError(
                        f"_FakeCursor keys must be mutually "
                        f"non-substring-containing: {a!r} vs {b!r} "
                        "(use disjoint catalog names like 'pg_tables' "
                        "/ 'pg_class' / 'pg_policies' as keys)"
                    )
        self._rows = rows_by_substring
        self._last_row: object = None
        self._fetched = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql: str):
        # First-match-wins iteration over an insertion-ordered dict.
        # Callers MUST keep substring keys mutually disjoint within a
        # single ``_FakeConn`` — overlapping keys (e.g. ``"users"`` and
        # ``"pg_policies"`` if a future policy_count_check named the
        # table ``users``) would silently dispatch to whichever was
        # inserted first. Today's keys are disjoint by construction
        # (each probes a different system catalog); a new probe that
        # threatens disjointness should be expressed as a longer, more
        # specific substring rather than relying on insertion order.
        for needle, row in self._rows.items():
            if needle in sql:
                self._last_row = row
                self._fetched = False
                return
        raise RuntimeError(
            f"_FakeCursor: no canned row matched SQL: {sql!r}; "
            f"available keys: {list(self._rows)}"
        )

    def fetchone(self):
        # Mirror real psycopg semantics: a second fetchone after the
        # row has been consumed returns None, not the same row again.
        if self._fetched:
            return None
        self._fetched = True
        return self._last_row


class _FakeConn:
    """Mock ``psycopg.Connection`` with substring → row dispatch."""

    def __init__(self, rows_by_substring: dict | None = None) -> None:
        self._rows = rows_by_substring or {}
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True
        return False

    def cursor(self):
        return _FakeCursor(self._rows)


class _FakeConnRaisingExecute(_FakeConn):
    """A conn whose cursor.execute raises ``psycopg.Error`` — exercises
    the per-check exception-to-Finding translation path. The exception
    type is taken from the fake psycopg module installed by
    ``_install_fake_psycopg`` so the adapter's narrowed
    ``except psycopg.Error`` block catches it and routes through
    ``substrate-*-check-failed``."""

    def __init__(self) -> None:
        super().__init__({})

    def cursor(self):
        import sys
        psycopg_module = sys.modules.get("psycopg")
        exc_type = getattr(psycopg_module, "Error", RuntimeError)

        class _Raising:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *args):
                return False

            def execute(self_inner, sql):
                raise exc_type("simulated query failure")

            def fetchone(self_inner):
                return None

        return _Raising()


def _install_fake_psycopg(monkey, conn_factory):
    """Install a fake psycopg module so ``import psycopg`` succeeds,
    ``psycopg.connect(url)`` returns a mock conn produced by
    ``conn_factory``, and ``psycopg.Error`` is a real exception class
    the adapter's narrowed ``except`` block can catch.
    """
    import sys
    import types

    fake = types.ModuleType("psycopg")
    fake.Error = type("Error", (Exception,), {})  # type: ignore[attr-defined]
    fake.connect = lambda url: conn_factory(url)  # type: ignore[attr-defined]
    monkey.setitem(sys.modules, "psycopg", fake)
    return fake


class TestVerifySubstrateMissingManifest(TempDirTestCase):
    """When the manifest doesn't exist, the return is one of two states
    distinguishable by whether the spec triggers any Postgres rules."""

    def test_no_manifest_no_triggers_returns_none(self) -> None:
        adapter = PostgresAdapter()
        result = adapter.verify_substrate(
            "postgresql://x/y",
            self.tmp_dir / "absent.toml",
            "completely unrelated spec text",
        )
        self.assertIsNone(result)

    def test_no_manifest_with_triggers_returns_missing_finding(self) -> None:
        adapter = PostgresAdapter()
        spec_text = "The handler runs `SET LOCAL app.tenant_id = $1`."
        result = adapter.verify_substrate(
            "postgresql://x/y",
            self.tmp_dir / "absent.toml",
            spec_text,
        )
        self.assertIsNotNone(result)
        assert result is not None  # narrow for the type checker
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].severity, "error")
        self.assertEqual(result[0].code, "substrate-manifest-missing")


class TestVerifySubstrateGucChecks(TempDirTestCase):
    """The empty-migration failure mode: GUC referenced in the AC but
    never registered. The substrate-layer ``current_setting()`` probe is
    the deterministic-iter-1 signal Piece 5 exists to provide."""

    def _write_manifest(self, body: str) -> Path:
        manifest = self.tmp_dir / "spec.toml"
        manifest.write_text(body)
        return manifest

    def test_unregistered_guc_returns_finding(self) -> None:
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[guc]]\n"
            'name = "app.tenant_id"\n'
            'default = ""\n'
            "registration_check = "
            "\"SELECT current_setting('app.tenant_id', true)\"\n"
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"current_setting": (None,)}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, "(spec text irrelevant here)"
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-guc-unregistered")
        self.assertIn("app.tenant_id", result[0].message)

    def test_empty_string_default_passes(self) -> None:
        """``current_setting('x', true)`` returns NULL when the GUC is
        unregistered and the registered value otherwise. An empty string
        is a *legitimate* registered default (e.g. ``ALTER DATABASE x SET
        app.tenant_id = ''``) and must not be conflated with NULL — see
        the rationale in ``_check_gucs``'s docstring (Linus review of
        Piece 5)."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[guc]]\n"
            'name = "app.tenant_id"\n'
            'default = ""\n'
            "registration_check = "
            "\"SELECT current_setting('app.tenant_id', true)\"\n"
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"current_setting": ("",)}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])

    def test_guc_with_real_value_passes(self) -> None:
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[guc]]\n"
            'name = "app.tenant_id"\n'
            'default = "default-tenant"\n'
            "registration_check = "
            "\"SELECT current_setting('app.tenant_id', true)\"\n"
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"current_setting": ("default-tenant",)}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])

    def test_missing_check_field_returns_malformed_finding(self) -> None:
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[guc]]\n"
            'name = "app.tenant_id"\n'
            'default = ""\n'
            # registration_check deliberately absent
        )
        with _pytest.MonkeyPatch.context() as m:
            # No SQL substring matches needed — the helper short-circuits
            # to a "no_check" finding before opening a cursor.
            _install_fake_psycopg(m, lambda url: _FakeConn({}))
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-guc-no-check")

    def test_check_raises_returns_failed_finding(self) -> None:
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[guc]]\n"
            'name = "app.x"\n'
            'default = ""\n'
            "registration_check = \"SELECT 1\"\n"
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(m, lambda url: _FakeConnRaisingExecute())
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-guc-check-failed")


class TestVerifySubstrateRlsChecks(TempDirTestCase):
    """The other empty-migration symptom: RLS declared in the AC but
    never enabled on the table. ``pg_tables.rowsecurity`` is the
    decisive substrate-layer signal."""

    def _write_manifest(self, body: str) -> Path:
        manifest = self.tmp_dir / "spec.toml"
        manifest.write_text(body)
        return manifest

    _RLS_MANIFEST_BODY = (
        'spec_id = "x"\n'
        "[[rls_table]]\n"
        'name = "users"\n'
        "expected_policies = [\"users_isolation\"]\n"
        'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'users\'"\n'
        'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'users\'"\n'
    )

    def test_rls_table_missing_returns_finding(self) -> None:
        """Zero rows from ``pg_tables`` means the table doesn't exist —
        an empty migration that never ran ``CREATE TABLE`` lands here.
        Disambiguated from ``substrate-rls-disabled`` by ``_scalar``'s
        ``row_present`` flag (Linus review of Piece 5)."""
        import pytest as _pytest
        manifest = self._write_manifest(self._RLS_MANIFEST_BODY)
        with _pytest.MonkeyPatch.context() as m:
            # ``None`` row from the rowsecurity probe — pg_tables has no
            # entry for ``users``. The policy_count probe should never
            # be reached, so no key for it.
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"pg_tables": None}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-rls-table-missing")
        self.assertIn("users", result[0].message)

    def test_rls_disabled_returns_finding(self) -> None:
        """Table exists, ``rowsecurity = false``: migration is missing
        ``ALTER TABLE ... ENABLE ROW LEVEL SECURITY``."""
        import pytest as _pytest
        manifest = self._write_manifest(self._RLS_MANIFEST_BODY)
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"pg_tables": (False,)}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-rls-disabled")
        self.assertIn("users", result[0].message)

    def test_rls_enabled_no_policies_returns_finding(self) -> None:
        import pytest as _pytest
        manifest = self._write_manifest(self._RLS_MANIFEST_BODY)
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "pg_tables": (True,),
                    "pg_policies": (0,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-rls-no-policies")

    def test_rls_enabled_with_policies_passes(self) -> None:
        import pytest as _pytest
        manifest = self._write_manifest(self._RLS_MANIFEST_BODY)
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "pg_tables": (True,),
                    "pg_policies": (1,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])


class TestVerifySubstrateRlsForceCheck(TempDirTestCase):
    """Task 43 primary path: ``force = true`` on ``[[rls_table]]`` entries.

    When the spec author opts into forcing RLS against the table owner
    (the 2026-04-22 documented footgun), the architect emits
    ``force_check`` and the gate verifies ``pg_class.relforcerowsecurity``.
    Backward compat: legacy manifests without ``force`` skip the check.
    """

    def _write_manifest(self, body: str) -> Path:
        manifest = self.tmp_dir / "spec.toml"
        manifest.write_text(body)
        return manifest

    _RLS_MANIFEST_WITH_FORCE = (
        'spec_id = "x"\n'
        "[[rls_table]]\n"
        'name = "users"\n'
        "expected_policies = [\"users_isolation\"]\n"
        "force = true\n"
        'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'users\'"\n'
        'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'users\'"\n'
        'force_check = "SELECT relforcerowsecurity FROM pg_class WHERE relname = \'users\'"\n'
    )

    def test_force_true_and_forced_passes(self) -> None:
        """Happy path: force=true and the migration issued
        ``ALTER TABLE ... FORCE ROW LEVEL SECURITY``, so
        ``pg_class.relforcerowsecurity`` is true."""
        import pytest as _pytest
        manifest = self._write_manifest(self._RLS_MANIFEST_WITH_FORCE)
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                # Keys MUST be disjoint substrings (per _FakeCursor
                # docstring). "rowsecurity" collides with
                # "relforcerowsecurity" — using catalog names
                # ("pg_tables", "pg_policies", "pg_class") guarantees
                # disjointness.
                lambda url: _FakeConn({
                    "pg_tables": (True,),
                    "pg_policies": (1,),
                    "pg_class": (True,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])

    def test_force_true_but_not_forced_returns_finding(self) -> None:
        """The footgun: force=true declared but the migration forgot
        ``FORCE``. Owner-role connections bypass every policy; the gate
        must surface this distinguishably from rls-disabled/no-policies."""
        import pytest as _pytest
        manifest = self._write_manifest(self._RLS_MANIFEST_WITH_FORCE)
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "pg_tables": (True,),
                    "pg_policies": (1,),
                    "pg_class": (False,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-rls-not-forced")
        self.assertIn("users", result[0].message)
        self.assertIn("FORCE ROW LEVEL SECURITY", result[0].message)

    def test_force_true_without_force_check_returns_finding(self) -> None:
        """Architect bug: spec carried force=true but the architect's
        manifest entry didn't emit ``force_check``. Surfaces as a
        distinct code so the fix target is the architect, not the
        migration."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[rls_table]]\n"
            'name = "users"\n'
            "expected_policies = [\"users_isolation\"]\n"
            "force = true\n"
            'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'users\'"\n'
            'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'users\'"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "pg_tables": (True,),
                    "pg_policies": (1,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-rls-no-force-check")

    def test_legacy_manifest_without_force_key_passes(self) -> None:
        """Backward compat: a manifest from before Task 43 — no ``force``
        key on ``[[rls_table]]`` — runs only rowsecurity and policy
        checks. Forcing older specs to opt in would break every existing
        pipeline run; make omission the safe default."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[rls_table]]\n"
            'name = "users"\n'
            "expected_policies = [\"users_isolation\"]\n"
            'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'users\'"\n'
            'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'users\'"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            # No canned row for relforcerowsecurity — if the helper
            # tried to run the force check, _FakeCursor would raise
            # "no needle matched". That would manifest as a
            # RuntimeError propagating past ``except psycopg.Error``
            # and failing the test loudly, which is what we want.
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "pg_tables": (True,),
                    "pg_policies": (1,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])

    def test_force_false_explicit_skips_check(self) -> None:
        """Explicit opt-out: ``force = false`` in the manifest skips the
        force check the same as omitting the key. This is the escape
        hatch for specs where the schema owner is already ``NOBYPASSRLS``
        at the role level and FORCE is redundant."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[rls_table]]\n"
            'name = "users"\n'
            "expected_policies = [\"users_isolation\"]\n"
            "force = false\n"
            'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'users\'"\n'
            'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'users\'"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "pg_tables": (True,),
                    "pg_policies": (1,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])

    def test_force_check_raises_returns_check_failed(self) -> None:
        """A raised psycopg.Error from the force_check SQL routes
        through ``substrate-rls-check-failed`` (consistent with the
        other per-check error paths). Distinguishes "sidecar ran the
        query and the answer was False" from "sidecar couldn't run the
        query at all."""
        import pytest as _pytest

        manifest = self._write_manifest(self._RLS_MANIFEST_WITH_FORCE)
        # Per-execute cursor that raises only on the force_check probe
        # so we reach force_check with a clean row/policy path.
        def _force_raising_conn(_url):
            import sys
            psycopg_module = sys.modules.get("psycopg")
            exc_type = getattr(psycopg_module, "Error", RuntimeError)

            class _Conn:
                closed = False

                def __enter__(self_inner): return self_inner
                def __exit__(self_inner, *a): self_inner.closed = True; return False

                def cursor(self_inner):
                    class _Cur:
                        _row = None
                        _fetched = False
                        def __enter__(s): return s
                        def __exit__(s, *a): return False
                        def execute(s, sql):
                            # Order matters: "rowsecurity" is a substring
                            # of "relforcerowsecurity", so probe the most
                            # specific patterns first. Using the system
                            # catalog name ("pg_tables", "pg_policies",
                            # "pg_class") as the dispatch key is disjoint
                            # by construction.
                            if "pg_class" in sql:
                                raise exc_type("simulated force probe failure")
                            if "pg_policies" in sql:
                                s._row = (1,); s._fetched = False; return
                            if "pg_tables" in sql:
                                s._row = (True,); s._fetched = False; return
                            raise RuntimeError(f"unexpected sql: {sql!r}")
                        def fetchone(s):
                            if s._fetched: return None
                            s._fetched = True; return s._row
                    return _Cur()
            return _Conn()

        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(m, _force_raising_conn)
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        # The helper's ``except psycopg.Error`` block at the per-check
        # level turns this into a -check-failed finding; the outer
        # ``except psycopg.Error`` around psycopg.connect would turn
        # a connection failure into -sidecar-unreachable, which is not
        # what happens here.
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-rls-check-failed")
        self.assertIn("force_check", result[0].message)

    def test_force_non_bool_string_returns_finding(self) -> None:
        """``force = "yes"`` parses as a string, not a TOML boolean.
        Under naive truthy coercion (``if rls.get("force"):``) the
        string would silently opt into the force check and the
        implementer-facing diagnosis would land as
        ``substrate-rls-not-forced`` — wrong audience (migration
        author instead of spec/validator). Gate identity on ``is
        True`` / ``is False`` and surface non-booleans as their own
        finding so the fix target is the spec, not the migration."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[rls_table]]\n"
            'name = "users"\n'
            "expected_policies = [\"users_isolation\"]\n"
            'force = "yes"\n'
            'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'users\'"\n'
            'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'users\'"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "pg_tables": (True,),
                    "pg_policies": (1,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-rls-force-not-bool")
        # Message carries the actual value and its type so the
        # spec author can see what the validator accepted.
        self.assertIn("yes", result[0].message)
        self.assertIn("str", result[0].message)

    def test_force_non_bool_int_returns_finding(self) -> None:
        """``force = 1`` is truthy but not a TOML boolean. Surfaces
        under the same finding code as ``force = "yes"`` — both are
        validator bugs, not migration bugs."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[rls_table]]\n"
            'name = "users"\n'
            "expected_policies = [\"users_isolation\"]\n"
            "force = 1\n"
            'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'users\'"\n'
            'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'users\'"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "pg_tables": (True,),
                    "pg_policies": (1,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-rls-force-not-bool")
        self.assertIn("int", result[0].message)


class TestVerifySubstrateRlsVerifiable(TempDirTestCase):
    """Task 41: preserve ``verifiable`` end-to-end on ``[[rls_table]]``.

    Failure mode that motivated this: a spec carried ``verifiable =
    false`` on a table the gate container cannot populate (seeded from
    an external system, or a view whose base tables live in a different
    spec). The architect's TOML-to-TOML transform dropped the field;
    the gate then demanded the table be verifiable, failing the spec
    against an impossible check. The fix is to carry ``verifiable``
    through the emitted manifest and have the gate skip entries where
    it's ``False``.

    Gated on TOML boolean identity (same lesson as ``force`` from
    Task 43): truthy coercion on a string like ``"maybe"`` would
    silently route through the wrong branch.
    """

    def _write_manifest(self, body: str) -> Path:
        manifest = self.tmp_dir / "spec.toml"
        manifest.write_text(body)
        return manifest

    def test_verifiable_false_skips_all_checks(self) -> None:
        """``verifiable = false`` bypasses every downstream check. If
        the helper executed any SQL, the ``_FakeConn({})`` empty dict
        would raise ``_FakeCursor: no canned row matched`` and surface
        as a test failure. The empty result confirms no probe ran."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[rls_table]]\n"
            'name = "external_seed"\n'
            "expected_policies = [\"external_seed_isolation\"]\n"
            "verifiable = false\n"
            'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'external_seed\'"\n'
            'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'external_seed\'"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(m, lambda url: _FakeConn({}))
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])

    def test_verifiable_true_explicit_still_verifies(self) -> None:
        """``verifiable = true`` is semantically identical to omitting
        the field — the downstream checks still run. Pin the identity
        so ``is True`` stays the opt-in branch and authors can be
        explicit without changing behavior."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[rls_table]]\n"
            'name = "users"\n'
            "expected_policies = [\"users_isolation\"]\n"
            "verifiable = true\n"
            'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'users\'"\n'
            'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'users\'"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "pg_tables": (True,),
                    "pg_policies": (1,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])

    def test_verifiable_absent_defaults_to_verify(self) -> None:
        """Backward compatibility: a manifest from before Task 41
        (no ``verifiable`` key) must still run all checks. Forcing
        older specs to opt in would break every existing pipeline
        run; omission defaults to verify."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[rls_table]]\n"
            'name = "users"\n'
            "expected_policies = [\"users_isolation\"]\n"
            'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'users\'"\n'
            'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'users\'"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "pg_tables": (False,),  # deliberately fail rowsec
                    "pg_policies": (1,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        # rowsecurity=false would be skipped under verifiable=false,
        # but absent ``verifiable`` must still run the check and
        # surface the disabled-RLS finding.
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-rls-disabled")

    def test_verifiable_non_bool_returns_finding(self) -> None:
        """``verifiable = "maybe"`` is truthy under a naive check but
        is not a TOML boolean. Surface the spec/validator bug distinctly
        so the fix target is the spec author, not the migration."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[rls_table]]\n"
            'name = "users"\n'
            "expected_policies = [\"users_isolation\"]\n"
            'verifiable = "maybe"\n'
            'rowsecurity_check = "SELECT rowsecurity FROM pg_tables WHERE tablename = \'users\'"\n'
            'policy_count_check = "SELECT count(*) FROM pg_policies WHERE tablename = \'users\'"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(m, lambda url: _FakeConn({}))
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-rls-verifiable-not-bool")
        self.assertIn("maybe", result[0].message)
        self.assertIn("str", result[0].message)


class TestVerifySubstrateIsolationPairsSkipped(TempDirTestCase):
    """``[[isolation_pair]]`` entries are scenario-layer (Piece 4), not
    gate-layer. ``verify_substrate`` must not attempt to execute them
    against the sidecar — they describe behavior the application stack
    enforces, not SQL the gate runs."""

    def test_isolation_pair_entries_are_ignored(self) -> None:
        import pytest as _pytest
        manifest = self.tmp_dir / "spec.toml"
        manifest.write_text(
            'spec_id = "x"\n'
            "[[isolation_pair]]\n"
            'table = "users"\n'
            'positive = "as A, A\'s rows visible"\n'
            'negative = "as A, B\'s rows not visible"\n'
        )
        # If the helper tried to run isolation_pair entries, it would
        # ask the fake conn for a row that has no canned match and the
        # _FakeCursor's "no needle matched" RuntimeError would surface.
        # The empty dict here proves no SQL was executed against this
        # manifest. (Plus the early-return short-circuit in
        # ``verify_substrate`` skips the psycopg import entirely when
        # there are no [[guc]]/[[rls_table]] entries.)
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(m, lambda url: _FakeConn({}))
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])


class TestVerifySubstrateBootstrapChecks(TempDirTestCase):
    """The Setup-phase post-condition gap: a migration command that
    exits 0 without satisfying its expected post-condition leaves
    the database in an unexpected state. Setup PASSes on exit code,
    scenarios crash on the missing infrastructure, Oracle blames the
    implementer for an environment failure they cannot fix.

    ``[[bootstrap_check]]`` is the post-condition the gate observes
    AFTER Setup. The architect agent emits the literal tracking-table
    name from the project's actual migration-tool config; the adapter
    holds zero migration-tool knowledge — the failure-mode prose
    here describes the CONTRACT shape ("Setup exit 0 vs unsatisfied
    post-condition"), not any specific tool's behavior. Phase 7
    rewrote the adapter message to stay tool-agnostic; this docstring
    follows the same discipline.
    """

    def _write_manifest(self, body: str) -> Path:
        manifest = self.tmp_dir / "spec.toml"
        manifest.write_text(body)
        return manifest

    _DRIZZLE_MANIFEST_BODY = (
        'spec_id = "x"\n'
        "[[bootstrap_check]]\n"
        'name = "drizzle-migrations-tracking-table"\n'
        "check_sql = \"SELECT 1 FROM information_schema.tables "
        "WHERE table_name='__drizzle_migrations'\"\n"
        'expect = "row_present"\n'
    )

    def test_tracking_table_present_passes(self) -> None:
        """Row returned from ``information_schema.tables`` → bootstrap
        succeeded → no findings. The canonical happy path: drizzle-kit
        migrate applied at least one statement, ``__drizzle_migrations``
        exists."""
        import pytest as _pytest
        manifest = self._write_manifest(self._DRIZZLE_MANIFEST_BODY)
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"information_schema": (1,)}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])

    def test_tracking_table_absent_returns_not_applied_finding(self) -> None:
        """Zero rows from ``information_schema.tables`` → table doesn't
        exist → Setup gate produced exit 0 without satisfying the
        post-condition. Surfaces the precise diagnosis the implementer
        needs: the contract divergence (Setup PASSed, post-condition
        unsatisfied), not a tool-specific fix mechanism.

        The fake conn returns ``None`` (signaling fetchone → None →
        ``_scalar`` returns ``(False, None)`` → row absent), mirroring
        real psycopg behavior when the SELECT matches no rows."""
        import pytest as _pytest
        manifest = self._write_manifest(self._DRIZZLE_MANIFEST_BODY)
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"information_schema": None}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].code, "substrate-bootstrap-not-applied"
        )
        # Manifest's `name` field flows verbatim — this is the
        # disambiguator the implementer relies on when N
        # bootstrap_checks are declared (Neo Phase 7 design pass
        # Finding 4: pin that {name!r} survives unchanged).
        self.assertIn(
            "'drizzle-migrations-tracking-table'", result[0].message
        )
        # Phase 7 canary anchors (replaces the old "comment-only"
        # assertion that locked in the wrong fix mechanism). Two
        # anchors:
        # - post-condition framing (the contract divergence the
        #   adapter actually observed)
        # - check_sql verbatim in backticks (the load-bearing detail
        #   the classifier paraphrases away — see canonical-sourcing
        #   tests in test_iteration.py)
        self.assertIn(
            "did not observe expected post-condition", result[0].message
        )
        self.assertIn(
            "`SELECT 1 FROM information_schema.tables "
            "WHERE table_name='__drizzle_migrations'`",
            result[0].message,
        )

    def test_not_applied_finding_is_tool_agnostic(self) -> None:
        """Phase 7 (2026-04-29) — the bootstrap_check failure message
        MUST NOT name any specific migration tool, mechanism, or
        imperative. Earlier wording said "drizzle-kit migrate against
        a comment-only baseline never creates the tracking table" —
        that mechanism was mechanistically wrong (drizzle's
        `ensureMigrationsTable` runs only when applying journal
        entries; the trigger is "journal entry exists", not
        "non-comment SQL exists"). The Phase 5 implementer read this
        hint, added `SELECT 1;` to baseline.sql, and produced a
        syntactically-correct semantically-useless fix.

        The adapter file's docstring at line 1091 reads "Adapter
        Python deliberately holds zero migration-tool knowledge."
        This test pins that contract: the rendered message stays
        tool-agnostic regardless of which migration tool the spec
        targets.

        Allowlist: ``tracking table`` and ``baseline`` are NOT
        forbidden — those are manifest-level vocabulary the
        architect uses legitimately.
        """
        import pytest as _pytest
        manifest = self._write_manifest(self._DRIZZLE_MANIFEST_BODY)
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"information_schema": None}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        message_lower = result[0].message.lower()

        # The message has two structural halves:
        # (1) the data-echo preamble: ``bootstrap_check '<name>' did
        #     not observe expected post-condition: SQL `<check>`
        #     returned no rows.`` — this echoes the manifest's `name`
        #     and `check_sql` verbatim; tool-specific tokens here
        #     (``'drizzle-migrations-tracking-table'``,
        #     ``__drizzle_migrations``) are the architect's contract,
        #     not the adapter editorializing.
        # (2) the editorial half: everything AFTER ``returned no
        #     rows.`` — this is where the adapter speculates (or
        #     refuses to). Phase 7's whole point: the editorial half
        #     MUST be tool-agnostic.
        # Splitting on ``returned no rows.`` partitions cleanly: the
        # token cannot appear anywhere else in either half by the
        # message's structure.
        _, sep, editorial = message_lower.partition("returned no rows.")
        self.assertTrue(
            sep,
            msg=(
                f"Could not find ``returned no rows.`` boundary in "
                f"message: {result[0].message!r}. The structural "
                f"split this test relies on is broken."
            ),
        )

        # Tool names — no migration-tool brand may appear in the
        # adapter's editorial half.
        forbidden_tools = [
            "drizzle-kit", "drizzle", "alembic", "sqitch", "flyway",
            "liquibase", "prisma", "knex",
        ]
        for token in forbidden_tools:
            self.assertNotIn(
                token, editorial,
                msg=(
                    f"Adapter prose names migration tool {token!r}; "
                    f"violates tool-agnostic-by-design contract "
                    f"(adapter.py:1091)."
                ),
            )

        # Mechanism prose — strings that imply a fix mechanism are
        # forbidden in the editorial half. ``tracking table`` and
        # ``baseline`` are explicitly allowed (manifest-level
        # vocabulary, also could appear in `check_sql`). The
        # ``silently`` / ``silent no-op`` pin is a regression guard
        # against re-introducing the evocative-but-misleading framing
        # from the old docstring (Neo Phase 7 design pass Finding 3).
        forbidden_mechanism = [
            "comment-only",
            "ensuremigrationstable",
            "journal entry",
            "journal",
            "silently",
            "silent no-op",
        ]
        for token in forbidden_mechanism:
            self.assertNotIn(
                token, editorial,
                msg=(
                    f"Adapter editorial prose contains mechanism "
                    f"token {token!r}; this is a re-encoding of "
                    f"tool-specific knowledge that the adapter must "
                    f"NOT speculate about."
                ),
            )

        # Imperatives that prescribe a fix lever. Adapter must state
        # the observed gap and refuse to speculate; specific fixes
        # belong in the implementer's iteration on the spec, not in
        # the substrate verifier's diagnostic.
        forbidden_imperative = [
            "add a migration",
            "re-run",
            "regenerate",
            "apply your migrations",
        ]
        for token in forbidden_imperative:
            self.assertNotIn(
                token, editorial,
                msg=(
                    f"Adapter editorial prose contains imperative "
                    f"{token!r}; the substrate verifier states "
                    f"observed gaps, not prescribed fixes."
                ),
            )

    def test_missing_check_sql_returns_malformed_finding(self) -> None:
        """An entry without ``check_sql`` is a manifest-author bug
        (the architect emitted a malformed entry); surface it under
        ``substrate-bootstrap-no-check`` so the fix target is the
        architect, not the implementer."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[bootstrap_check]]\n"
            'name = "no-sql"\n'
            'expect = "row_present"\n'
            # check_sql deliberately absent
        )
        with _pytest.MonkeyPatch.context() as m:
            # The helper short-circuits to a malformed finding before
            # opening a cursor — empty dict is sufficient.
            _install_fake_psycopg(m, lambda url: _FakeConn({}))
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-bootstrap-no-check")

    def test_unsupported_expect_returns_finding(self) -> None:
        """``expect`` exists for self-documentation today and forward-
        compat tomorrow. Only ``"row_present"`` is supported in this
        cut; anything else is a manifest-author error so the gate
        fails with a clear "not yet supported" diagnostic instead of
        silently treating it like the default."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[bootstrap_check]]\n"
            'name = "future-shape"\n'
            'check_sql = "SELECT 1"\n'
            'expect = "value_truthy"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(m, lambda url: _FakeConn({}))
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].code, "substrate-bootstrap-expect-unsupported"
        )

    def test_check_sql_raises_returns_failed_finding(self) -> None:
        """Per-check exception isolation: one bad ``check_sql`` (typo,
        missing schema, etc.) shouldn't abort the gate's verification
        of the rest of the manifest. The exception is translated into
        a ``substrate-bootstrap-check-failed`` finding; verification
        continues with the next entry."""
        import pytest as _pytest
        manifest = self._write_manifest(self._DRIZZLE_MANIFEST_BODY)
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(m, lambda url: _FakeConnRaisingExecute())
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].code, "substrate-bootstrap-check-failed"
        )

    def test_default_expect_is_row_present(self) -> None:
        """Omitting ``expect`` in the manifest must default to
        ``"row_present"`` — the canonical interpretation. This pins
        the default so a future maintainer who flips it (e.g. to a
        permissive ``"any"`` mode) breaks loudly."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[bootstrap_check]]\n"
            'name = "default-expect"\n'
            'check_sql = "SELECT 1 FROM information_schema.tables"\n'
            # expect deliberately omitted
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"information_schema": None}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].code, "substrate-bootstrap-not-applied"
        )

    def test_first_entry_raises_second_entry_still_runs(self) -> None:
        """Per-entry exception isolation: a typo'd ``check_sql`` in the
        first entry must not abort verification of subsequent entries.
        Pins the ``continue`` discipline in the per-entry loop — the
        load-bearing invariant that keeps one bad entry from masking
        problems in the others (same shape as ``_check_gucs``'s loop)."""
        import pytest as _pytest
        # Deliberately ordered: a check that doesn't match the
        # _FakeConn dispatch table raises a RuntimeError; a check that
        # does match returns row-absent → not_applied. Two findings is
        # the only correct outcome — one finding means we aborted at
        # the first failure and missed the second issue.
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[bootstrap_check]]\n"
            'name = "first-raises"\n'
            'check_sql = "SELECT 1 FROM no_dispatch_match_here"\n'
            "[[bootstrap_check]]\n"
            'name = "second-row-absent"\n'
            'check_sql = "SELECT 1 FROM information_schema.tables"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"information_schema": None}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 2)
        self.assertEqual(
            result[0].code, "substrate-bootstrap-check-failed"
        )
        self.assertEqual(
            result[1].code, "substrate-bootstrap-not-applied"
        )

    def test_unnamed_entry_renders_with_placeholder(self) -> None:
        """An entry without ``name`` falls back to ``"<unnamed>"`` —
        matches ``_check_gucs`` / ``_check_rls_tables`` placeholder
        convention. The fallback is preserved here because a missing
        name is a manifest-author bug worth surfacing in the
        diagnostic message verbatim, not silently elided."""
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[bootstrap_check]]\n"
            # name deliberately absent
            'check_sql = "SELECT 1 FROM information_schema.tables"\n'
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({"information_schema": None}),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(
            result[0].code, "substrate-bootstrap-not-applied"
        )
        self.assertIn("<unnamed>", result[0].message)

    def test_multi_primitive_manifest_dispatches_all(self) -> None:
        """Integration: a manifest carrying [[guc]] + [[rls_table]] +
        [[bootstrap_check]] runs all three checks on the same psycopg
        connection. Pins the dispatch wiring so a refactor that drops
        any of the three breaks loudly here.

        Each fake-conn substring key targets one of the three checks:
        ``current_setting`` (guc), ``pg_tables`` / ``pg_policies``
        (rls), ``information_schema`` (bootstrap). All resolve to
        successful values so the result is empty.
        """
        import pytest as _pytest
        manifest = self._write_manifest(
            'spec_id = "x"\n'
            "[[guc]]\n"
            'name = "app.tenant_id"\n'
            'default = ""\n'
            "registration_check = "
            "\"SELECT current_setting('app.tenant_id', true)\"\n"
            "[[rls_table]]\n"
            'name = "users"\n'
            'expected_policies = ["users_isolation"]\n'
            'rowsecurity_check = "SELECT rowsecurity FROM pg_tables '
            "WHERE tablename = 'users'\"\n"
            'policy_count_check = "SELECT count(*) FROM pg_policies '
            "WHERE tablename = 'users'\"\n"
            "[[bootstrap_check]]\n"
            'name = "drizzle-migrations-tracking-table"\n'
            "check_sql = \"SELECT 1 FROM information_schema.tables "
            "WHERE table_name='__drizzle_migrations'\"\n"
        )
        with _pytest.MonkeyPatch.context() as m:
            _install_fake_psycopg(
                m,
                lambda url: _FakeConn({
                    "current_setting": ("default-tenant",),
                    "pg_tables": (True,),
                    "pg_policies": (1,),
                    "information_schema": (1,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])


class TestVerifySubstrateInfraFailures(TempDirTestCase):
    def test_unparseable_manifest_returns_finding(self) -> None:
        manifest = self.tmp_dir / "spec.toml"
        manifest.write_text("[[ this is not valid toml")
        result = PostgresAdapter().verify_substrate(
            "postgresql://x/y", manifest, ""
        )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-manifest-unreadable")

    def test_sidecar_unreachable_returns_finding(self) -> None:
        import pytest as _pytest
        import sys
        manifest = self.tmp_dir / "spec.toml"
        manifest.write_text(
            'spec_id = "x"\n'
            "[[guc]]\n"
            'name = "app.x"\n'
            'default = ""\n'
            "registration_check = \"SELECT 1\"\n"
        )

        with _pytest.MonkeyPatch.context() as m:
            # Install the fake module first, then capture its Error
            # class — the adapter narrows ``except psycopg.Error``, so
            # the fake connect() must raise that exact class for the
            # finding-translation path to fire.
            def _bind_then_raise(url):
                exc_type = sys.modules["psycopg"].Error
                raise exc_type("connection refused")

            _install_fake_psycopg(m, _bind_then_raise)
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        assert result is not None
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "substrate-sidecar-unreachable")


class TestArchitectAddendumSubstrateCheckRequirements(unittest.TestCase):
    """The architect addendum must instruct the architect to emit the
    gate-verification SQL fields on every substrate primitive, or the
    gate emits ``substrate-guc-no-check`` / ``substrate-rls-no-check``
    and fails the iteration before any real behavior is verified.

    Run ``20260421-190235-2026-03-28-database-foundation-e614a176``
    (and its predecessor ``e5634bc6``) aborted iter-2 on byte-identical
    malformed-manifest errors because the addendum's example omitted
    these fields. This test pins the prose-level requirement so the
    next time the addendum is trimmed, the check-field requirement
    cannot be lost silently.
    """

    def test_addendum_names_required_check_fields(self) -> None:
        """Addendum text must name each gate-required check field so the
        architect knows it owns them. Missing any one here is the exact
        regression that killed e614a176 / e5634bc6."""
        text = PostgresAdapter().architect_addendum()
        for field in (
            "registration_check",
            "rowsecurity_check",
            "policy_count_check",
        ):
            self.assertIn(field, text, (
                f"architect_addendum must name {field!r} so the architect "
                f"emits it on every substrate primitive; without it the "
                f"gate fails with substrate-*-no-check"
            ))

    def test_addendum_example_shows_check_fields_on_primitives(self) -> None:
        """A spec author or future editor trimming prose might delete
        the requirement paragraph but keep the example; ensure the
        example itself carries the fields so it functions as a
        self-contained template."""
        text = PostgresAdapter().architect_addendum()
        # The example block is the canonical template copy-pasted by
        # an architect under time pressure. Each check field must
        # appear as an ``= "..."`` assignment inside a fenced TOML
        # block.
        self.assertRegex(
            text,
            r'registration_check\s*=\s*"',
            "example TOML must show registration_check as an assignment",
        )
        self.assertRegex(
            text,
            r'rowsecurity_check\s*=\s*"',
            "example TOML must show rowsecurity_check as an assignment",
        )
        self.assertRegex(
            text,
            r'policy_count_check\s*=\s*"',
            "example TOML must show policy_count_check as an assignment",
        )

    def test_addendum_warns_about_malformed_manifest_error_codes(self) -> None:
        """Naming the error codes by which the gate will fail gives the
        architect a searchable pointer from prompt to gate output. An
        architect that sees ``substrate-guc-no-check`` in its own
        addendum can grep the codebase and understand the contract."""
        text = PostgresAdapter().architect_addendum()
        self.assertIn("substrate-guc-no-check", text)
        self.assertIn("substrate-rls-no-check", text)

    def test_bootstrap_check_guidance_is_tool_agnostic(self) -> None:
        """Phase 7 (Linus code review 3d, 2026-04-29): the addendum's
        bootstrap_check guidance must NOT speculate on tool-specific
        failure mechanisms. Earlier wording said "silent no-op Setup
        (e.g. drizzle-kit against a comment-only baseline) PASSes the
        gate" — that mechanism was mechanistically wrong AND violated
        the "Adapter Python deliberately holds zero migration-tool
        knowledge" contract.

        The addendum DOES legitimately name migration tools elsewhere
        (e.g. ``drizzle.config.ts``, ``alembic.ini`` as worked examples
        for where to read the tracking-table name from). What this
        test pins is that the FAILURE-MODE prose for bootstrap_check —
        the sentence explaining why bootstrap_check matters — stays
        tool-agnostic. Tool-specific text in worked examples is fine;
        tool-specific causal speculation is not.
        """
        text = PostgresAdapter().architect_addendum()
        # Find every line that contains both `bootstrap_check` and
        # the contract-divergence phrasing the new framing uses. There
        # should be at least one (the Phase 7 rewrite of line 87 in
        # the source addendum); the test fails if the rewrite is
        # reverted or the guidance line is removed.
        bootstrap_lines = [
            ln for ln in text.lower().splitlines()
            if "bootstrap_check" in ln and "post-condition" in ln
        ]
        self.assertTrue(
            bootstrap_lines,
            msg=(
                "addendum is missing the bootstrap_check guidance "
                "line that names 'post-condition' — the Phase 7 "
                "tool-agnostic framing. Either the line was removed "
                "or the rewrite was reverted."
            ),
        )
        forbidden = [
            "comment-only", "silent no-op", "silently",
            "ensuremigrationstable", "journal entry",
        ]
        for ln in bootstrap_lines:
            for token in forbidden:
                self.assertNotIn(
                    token, ln,
                    msg=(
                        f"addendum bootstrap_check guidance line "
                        f"contains tool-specific mechanism token "
                        f"{token!r}; restore tool-agnostic framing."
                    ),
                )


class TestBackwardsCompatShim(unittest.TestCase):
    """``darkfactory.spec.sql_lint`` must continue to re-export the
    public API so existing importers keep working until A5 migrates
    them. Internal state (``_PGLAST_AVAILABLE``) is deliberately not
    re-exported — the shim is BC for the API, not implementation
    details."""

    def test_shim_exports_public_api(self) -> None:
        from darkfactory.spec import sql_lint as shim
        from darkfactory.adapters.postgres import sql_lint as real

        self.assertIs(shim.lint_sql_fences, real.lint_sql_fences)
        self.assertIs(shim.sql_fences_present, real.sql_fences_present)
        self.assertIs(shim.SqlLintFinding, real.SqlLintFinding)

    def test_shim_does_not_leak_internal_state(self) -> None:
        """Internal state like ``_PGLAST_AVAILABLE`` is not re-exported;
        tests and callers that need it must import the real module."""
        from darkfactory.spec import sql_lint as shim
        self.assertFalse(hasattr(shim, "_PGLAST_AVAILABLE"))


if __name__ == "__main__":
    unittest.main()
