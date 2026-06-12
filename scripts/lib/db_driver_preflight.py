#!/usr/bin/env python3
"""Deterministic DB driver<->URL-scheme preflight (Sprint 21).

A postgres SQLAlchemy ``DATABASE_URL`` names exactly ONE driver module via its
scheme. If the project didn't install that driver, the Setup-stage migration and
every DB scenario crash with an obscure ``ModuleNotFoundError`` at collection —
a silent failure the implementer can't readily diagnose. This resolves the
required module from the URL scheme (deterministic — it does NOT guess intent
from what happens to be importable) and reports a precise mismatch so the gate
can fail Setup early with an actionable, contract-only message instead.

The mismatch this catches: the Architect injects ``DATABASE_URL`` (env-contract)
and the Implementer declares the driver, independently, across the information
barrier. A bare ``postgresql://`` scheme implies SQLAlchemy's psycopg2 dialect;
if the implementer installed psycopg v3 (``postgresql+psycopg://``) instead, the
injected bare URL wins at runtime and ``import psycopg2`` fails.

Usage (from the quality gate): ``python3 db_driver_preflight.py "$DATABASE_URL"``
Exit 0 = ok / not a recognized postgres SQLAlchemy URL (skip). Exit 3 = mismatch
(precise message on stdout). Standalone (no darkfactory imports) so it runs
inside the gate container against the project's own interpreter + installed deps.
"""
from __future__ import annotations

import sys
from typing import Callable, Optional

# Longest-prefix-first: driver-explicit schemes MUST be tested before the bare
# ``postgresql://`` (which would otherwise prefix-match ``postgresql+psycopg://``).
# An UNRECOGNIZED scheme (a deliberate, exotic driver choice, or a non-postgres
# URL) maps to None and the preflight skips it (fail-open): the silent/confusing
# failure this guards is specifically "bare scheme implies psycopg2 but the impl
# installed something else" — an explicit ``+<driver>`` scheme already fails with
# a message that NAMES its own missing module, so there is nothing to clarify.
_SCHEME_DRIVER: tuple[tuple[str, str], ...] = (
    ("postgresql+psycopg2cffi://", "psycopg2cffi"),
    ("postgres+psycopg2cffi://", "psycopg2cffi"),
    ("postgresql+psycopg2://", "psycopg2"),
    ("postgres+psycopg2://", "psycopg2"),
    ("postgresql+psycopg://", "psycopg"),
    ("postgres+psycopg://", "psycopg"),
    ("postgresql+asyncpg://", "asyncpg"),
    ("postgres+asyncpg://", "asyncpg"),
    ("postgresql+pg8000://", "pg8000"),
    ("postgres+pg8000://", "pg8000"),
    ("postgresql://", "psycopg2"),
    ("postgres://", "psycopg2"),
)


def required_driver(url: str) -> Optional[str]:
    """The driver module a postgres SQLAlchemy URL requires, or None when the
    URL is not a recognized postgres SQLAlchemy URL (preflight is a no-op)."""
    lowered = (url or "").strip().lower()
    for prefix, module in _SCHEME_DRIVER:
        if lowered.startswith(prefix):
            return module
    return None


def check(url: str, importer: Callable[[str], object] = __import__) -> Optional[str]:
    """Return None when ok / not-applicable, else a precise mismatch message.

    ``importer`` is injectable so the mapping + messaging can be unit-tested
    without the real drivers installed on the host.
    """
    module = required_driver(url)
    if module is None:
        return None
    try:
        importer(module)
    except Exception:  # ImportError or any import-time error => unusable driver
        scheme = url.strip().split("://", 1)[0] + "://"
        return (
            f"DATABASE_URL scheme '{scheme}' requires the Python driver "
            f"'{module}', which is not importable in the project environment. "
            f"Declare a matching driver in the project's dependencies (scheme "
            f"'postgresql://' needs 'psycopg2-binary'; 'postgresql+psycopg://' "
            f"needs 'psycopg'), or build on the injected DATABASE_URL's scheme "
            f"as-is and install the driver it names."
        )
    return None


def main(argv: list[str]) -> int:
    message = check(argv[1] if len(argv) > 1 else "")
    if message is None:
        return 0
    print(message)
    return 3


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
