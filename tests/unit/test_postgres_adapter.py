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
                lambda url: _FakeConn({"rowsecurity": None}),
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
                lambda url: _FakeConn({"rowsecurity": (False,)}),
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
                    "rowsecurity": (True,),
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
                    "rowsecurity": (True,),
                    "pg_policies": (1,),
                }),
            )
            result = PostgresAdapter().verify_substrate(
                "postgresql://x/y", manifest, ""
            )
        self.assertEqual(result, [])


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
