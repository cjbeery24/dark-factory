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
