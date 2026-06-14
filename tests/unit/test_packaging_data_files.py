"""Pin the ``[tool.setuptools.package-data]`` declaration in
``pyproject.toml`` against an explicit list of known data files.

Without this, ``[tool.setuptools.packages.find]`` ships only ``.py``
files in the wheel — and any non-editable install
(``pip install dark-factory``) crashes on first load of an adapter
TOML / .md / .ts data file. Linus B9b foundation review (2026-04-25)
flagged this as pre-existing; B9b commit 2 made it the third TOML
to depend on the broken pattern. This commit fixes it.

Test strategy: static-pin against the pyproject.toml declaration.
Each known data file must be matched by at least one
``package-data`` glob pattern under the right package key. No wheel
build needed in the test — building a wheel was done once manually
to verify the patterns work; this test is the regression backstop
that catches any future declaration drift.

What this test does NOT pin:

* That the wheel itself contains the file (would require a build
  step in the test suite — too heavy; covered by a manual
  build-verify before each release).
* That ``importlib.resources.files(...).joinpath(...)`` resolves
  the path at runtime — that's the consumer's concern; covered by
  per-adapter loader tests (``test_postgres_adapter.py`` etc.).
* The repo-root ``templates/`` and ``agents/`` directories (the OTHER
  templates: spec templates, agents-md.md, etc.) — these live OUTSIDE
  the ``darkfactory/`` package and are consumed via
  ``Path(darkfactory.__file__).parent.parent / 'templates'`` from
  ``darkfactory.setup.init._copy_framework_files``. Broken in
  non-editable installs. Pre-existing bug; partial fix landed for
  the contracts subset (Phase 4 v0 packaging follow-up: starters
  moved to ``darkfactory/contracts/templates/`` and ship via the
  package-data declaration this test pins). The ``_copy_framework_files``
  side of the same bug remains; tracked as a separate follow-up.
"""
from __future__ import annotations

import fnmatch
import tomllib
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# Known non-Python data files that MUST ship in the wheel. Adding a
# new data file requires both adding it here AND extending the
# pyproject.toml ``package-data`` patterns to match. The
# bidirectional discipline catches both directions of drift:
#
#   * A new data file added under ``darkfactory/`` without a
#     pyproject pattern → ``test_every_expected_file_matches_pattern``
#     fails.
#   * A pyproject pattern that no longer matches any known file →
#     not caught here (acceptable: orphan patterns are noise, not
#     regression).
#
# To extend this list: add the file path (relative to repo root)
# below AND update the corresponding ``[tool.setuptools.package-data]``
# entry in ``pyproject.toml``.
_EXPECTED_DATA_FILES: tuple[str, ...] = (
    # Postgres adapter — addenda + TOMLs.
    "darkfactory/adapters/postgres/architect_addendum.md",
    "darkfactory/adapters/postgres/scenario_architect_addendum.md",
    "darkfactory/adapters/postgres/spec_validator_addendum.md",
    "darkfactory/adapters/postgres/mechanism-tokens.toml",
    "darkfactory/adapters/postgres/substrate-rules.toml",
    "darkfactory/adapters/postgres/retire-after.toml",
    # Postgres contracts/ts/ — staged into the TE container at
    # runtime via stage_adapter_contracts; sourced from the
    # installed package.
    "darkfactory/adapters/postgres/contracts/ts/guc.ts",
    "darkfactory/adapters/postgres/contracts/ts/index.ts",
    # ``isolation.ts`` deleted in B9a v(N+1); convention helpers
    # ship via `darkfactory/contracts/templates/postgres/
    # project-starters/isolation.ts` and bootstrap into projects via
    # `dark-factory contracts init postgres`.
    "darkfactory/adapters/postgres/contracts/ts/rls.ts",
    "darkfactory/adapters/postgres/contracts/ts/tx_guc.ts",
    "darkfactory/adapters/postgres/contracts/ts/package.json",
    "darkfactory/adapters/postgres/contracts/ts/tsconfig.json",
    "darkfactory/adapters/postgres/contracts/ts/README.md",
    # Sprint 28: per-test DB clean-state reset, staged into the gate's
    # vitest setupFiles for postgres-active specs. The .sql is the single
    # source of truth for the TRUNCATE; the .setup.ts reads it at runtime.
    "darkfactory/adapters/postgres/contracts/ts/_df_reset.setup.ts",
    "darkfactory/adapters/postgres/contracts/ts/_df_reset.sql",
    # Other adapter addenda.
    "darkfactory/adapters/typescript/architect_addendum.md",
    "darkfactory/adapters/python_fastapi/architect_addendum.md",
    # Phase 4 v0 — postgres project-starter templates shipped via
    # ``darkfactory.contracts``'s package-data so ``dark-factory
    # contracts init postgres`` works in non-editable wheel installs.
    "darkfactory/contracts/templates/postgres/project-starters/README.md",
    "darkfactory/contracts/templates/postgres/project-starters/index.ts",
    "darkfactory/contracts/templates/postgres/project-starters/app_tx_guc.ts",
    "darkfactory/contracts/templates/postgres/project-starters/isolation.ts",
)


def _load_package_data() -> dict[str, list[str]]:
    """Read the ``[tool.setuptools.package-data]`` table from
    pyproject.toml.

    Loads at call time (not module scope) so a malformed pyproject
    fails the test that needs the data, not import.
    """
    text = (_REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    data = tomllib.loads(text)
    section = data.get("tool", {}).get("setuptools", {}).get(
        "package-data", {},
    )
    return {k: list(v) for k, v in section.items()}


def _matches_pattern(
    file_path: str, package: str, patterns: list[str],
) -> bool:
    """True iff ``file_path`` (repo-relative POSIX path) is matched
    by any glob pattern under ``package``'s install location.

    setuptools' package-data patterns are interpreted as globs
    relative to each package's source directory. This function
    mirrors that semantics.
    """
    package_dir = package.replace(".", "/")
    if not file_path.startswith(package_dir + "/"):
        return False
    rel = file_path[len(package_dir) + 1:]
    for pattern in patterns:
        if fnmatch.fnmatch(rel, pattern):
            return True
    return False


class TestPackageDataDeclaration(unittest.TestCase):
    """Pin the pyproject.toml ``[tool.setuptools.package-data]``
    declaration against the known data-file list.
    """

    def test_section_exists(self) -> None:
        package_data = _load_package_data()
        self.assertGreater(
            len(package_data), 0,
            "pyproject.toml is missing the "
            "[tool.setuptools.package-data] section. Without it, "
            "non-editable installs (`pip install dark-factory`) "
            "ship Python source only and crash on first load of "
            "any adapter TOML / .md / .ts data file. See "
            "Linus B9b foundation review (2026-04-25) for context."
        )

    def test_every_expected_file_matches_a_pattern(self) -> None:
        package_data = _load_package_data()
        for file_path in _EXPECTED_DATA_FILES:
            with self.subTest(file=file_path):
                self.assertTrue(
                    Path(_REPO_ROOT / file_path).is_file(),
                    f"expected data file {file_path} is missing "
                    "from the source tree — either remove it from "
                    "`_EXPECTED_DATA_FILES` (genuinely deleted) or "
                    "restore from git history (accidentally lost)."
                )
                matched_by: list[str] = []
                for package, patterns in package_data.items():
                    if _matches_pattern(file_path, package, patterns):
                        matched_by.append(package)
                self.assertGreater(
                    len(matched_by), 0,
                    f"data file {file_path} is not matched by any "
                    f"package-data pattern. Add it to the "
                    f"corresponding `[tool.setuptools.package-data]` "
                    "entry in pyproject.toml."
                )


class TestKnownAdapterPackagesDeclared(unittest.TestCase):
    """Each adapter package that ships data files must have an
    explicit entry in the package-data section. Catches the case
    where someone adds a new adapter (with addendum / contracts /
    TOMLs) but forgets the pyproject declaration.
    """

    _ADAPTERS_WITH_DATA: tuple[str, ...] = (
        "darkfactory.adapters.postgres",
        "darkfactory.adapters.typescript",
        "darkfactory.adapters.python_fastapi",
    )

    def test_every_data_shipping_adapter_has_a_section(self) -> None:
        package_data = _load_package_data()
        for adapter in self._ADAPTERS_WITH_DATA:
            with self.subTest(adapter=adapter):
                self.assertIn(
                    adapter, package_data,
                    f"adapter package {adapter!r} is in "
                    "_ADAPTERS_WITH_DATA but has no "
                    "`[tool.setuptools.package-data]` entry. Add an "
                    "entry listing the data-file glob patterns that "
                    "ship with this adapter."
                )


if __name__ == "__main__":
    unittest.main()
