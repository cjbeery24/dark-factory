"""Postgres adapter — ecosystem-specific behavior for projects with a
Postgres database.

Public surface:

- ``PostgresAdapter`` — implements ``EcosystemAdapter``. Active when
  Postgres dialect signals are present (SQL files, postgres dependency,
  or ``services.postgresql`` in the env contract).
- ``lint_sql_fences`` / ``SqlLintFinding`` / ``sql_fences_present`` —
  SQL fenced-block parser that produces ground-truth findings against
  Postgres (libpg_query via ``pglast``). These were previously in
  ``darkfactory/spec/sql_lint.py``; A3 moved them here because SQL is
  not Node-specific — a Python+SQLAlchemy project would want the same
  check. A thin backwards-compat shim remains at the old path.

The adapter keeps SQL-lint behavior identical to the pre-A3 world; the
change is structural: ``PostgresAdapter.precheck`` wraps the lint
instead of the spec precheck calling it directly. A5's registry will
finish the migration by dispatching precheck through the adapter chain.
"""
from darkfactory.adapters.postgres.adapter import PostgresAdapter
from darkfactory.adapters.postgres.sql_lint import (
    SqlLintFinding,
    lint_sql_fences,
    sql_fences_present,
)

__all__ = [
    "PostgresAdapter",
    "SqlLintFinding",
    "lint_sql_fences",
    "sql_fences_present",
]
