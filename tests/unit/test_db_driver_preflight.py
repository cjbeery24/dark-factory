"""Tests for scripts/lib/db_driver_preflight.py (Sprint 21).

The preflight is a deterministic DB driver<->URL-scheme check that runs in the
gate container (no project mount), so the tests drive the module directly.
A false-PASS (missing the mismatch) reintroduces the silent psycopg2 crash; a
false-FAIL escalates a good run. Both directions are covered.
"""
from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from typing import Any


def _load_module() -> Any:
    spec_path = (
        Path(__file__).resolve().parent.parent.parent
        / "scripts" / "lib" / "db_driver_preflight.py"
    )
    spec = importlib.util.spec_from_file_location("db_driver_preflight", spec_path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


M = _load_module()


def _ok(*present: str):
    """An importer that succeeds only for the named modules."""
    def importer(name: str) -> object:
        if name in present:
            return object()
        raise ImportError(name)
    return importer


class TestRequiredDriver(unittest.TestCase):
    def test_scheme_to_driver_mapping(self) -> None:
        cases = {
            "postgresql://u:p@h:5432/db": "psycopg2",
            "postgres://u:p@h/db": "psycopg2",
            "postgresql+psycopg://u@h/db": "psycopg",
            "postgresql+psycopg2://u@h/db": "psycopg2",
            "postgresql+asyncpg://u@h/db": "asyncpg",
            "postgres+psycopg://u@h/db": "psycopg",
        }
        for url, expected in cases.items():
            self.assertEqual(M.required_driver(url), expected, url)

    def test_driver_explicit_scheme_wins_over_bare(self) -> None:
        # The bare 'postgresql://' prefix must NOT shadow '+psycopg'.
        self.assertEqual(M.required_driver("postgresql+psycopg://h/db"), "psycopg")

    def test_case_insensitive_and_whitespace(self) -> None:
        self.assertEqual(M.required_driver("  POSTGRESQL+PSYCOPG://h/db "), "psycopg")
        self.assertEqual(M.required_driver("POSTGRESQL://h/db"), "psycopg2")
        self.assertEqual(M.required_driver("  postgresql://h/db"), "psycopg2")

    def test_other_explicit_dialects(self) -> None:
        self.assertEqual(M.required_driver("postgresql+pg8000://h/db"), "pg8000")
        self.assertEqual(M.required_driver("postgresql+psycopg2cffi://h/db"), "psycopg2cffi")

    def test_non_postgres_url_is_none(self) -> None:
        for url in ("mysql://h/db", "sqlite:///x.db", "redis://h", "", "   ", "not a url",
                    "cockroachdb://h/db"):
            self.assertIsNone(M.required_driver(url), url)

    def test_none_input_is_none(self) -> None:
        self.assertIsNone(M.required_driver(None))  # type: ignore[arg-type]

    def test_postgresql_only_in_credentials_is_not_a_scheme(self) -> None:
        # 'postgresql' appearing in the userinfo must NOT match — anchored at start.
        self.assertIsNone(M.required_driver("mysql://postgresql:pw@h/db"))


class TestCheck(unittest.TestCase):
    def test_match_passes(self) -> None:
        # bare scheme + psycopg2 installed -> ok
        self.assertIsNone(M.check("postgresql://h/db", importer=_ok("psycopg2")))
        # +psycopg scheme + psycopg installed -> ok
        self.assertIsNone(M.check("postgresql+psycopg://h/db", importer=_ok("psycopg")))

    def test_mismatch_fails_with_actionable_message(self) -> None:
        # The real bug: bare scheme (needs psycopg2) but only psycopg v3 present.
        msg = M.check("postgresql://h/db", importer=_ok("psycopg"))
        self.assertIsNotNone(msg)
        self.assertIn("psycopg2", msg)
        self.assertIn("postgresql://", msg)

    def test_explicit_scheme_mismatch_fails(self) -> None:
        # +psycopg scheme but only psycopg2 present.
        msg = M.check("postgresql+psycopg://h/db", importer=_ok("psycopg2"))
        self.assertIsNotNone(msg)
        self.assertIn("psycopg", msg)

    def test_non_postgres_is_skipped(self) -> None:
        # No driver required -> never fails, even with nothing importable.
        self.assertIsNone(M.check("mysql://h/db", importer=_ok()))
        self.assertIsNone(M.check("", importer=_ok()))

    def test_import_time_error_treated_as_unusable(self) -> None:
        def boom(name: str) -> object:
            raise RuntimeError("broken C extension")
        self.assertIsNotNone(M.check("postgresql://h/db", importer=boom))

    def test_message_names_no_test_content(self) -> None:
        # Authorization guard: the implementer-readable message must stay a
        # driver/scheme contract diagnostic — no scenario/holdout/test strings.
        msg = M.check("postgresql://h/db", importer=_ok("psycopg")) or ""
        for forbidden in ("scenario", "holdout", "test_", ".test.", "assert"):
            self.assertNotIn(forbidden, msg.lower())


class TestMain(unittest.TestCase):
    def test_exit_codes(self) -> None:
        # main() uses the real __import__; psycopg2 is not installed on the host,
        # so a bare postgres URL -> mismatch -> exit 3; a non-postgres URL -> 0.
        self.assertEqual(M.main(["x", "mysql://h/db"]), 0)
        self.assertEqual(M.main(["x"]), 0)  # no URL arg
        rc = M.main(["x", "postgresql://h/db"])
        self.assertIn(rc, (0, 3))  # 0 iff psycopg2 happens to be installed


if __name__ == "__main__":
    unittest.main()
