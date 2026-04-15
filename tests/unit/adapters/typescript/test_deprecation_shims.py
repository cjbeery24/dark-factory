"""Pin Sprint 1 A2 deprecation-shim behavior.

When ``darkfactory.spec.library_symbols``, ``darkfactory.spec.stub_compile``,
and ``darkfactory.scenarios.proposal_rehearsal`` were extracted into
``darkfactory.adapters.typescript`` (Sprint 1 A2), the original paths
were converted into shims that re-export from the new locations and
emit a single ``DeprecationWarning`` per import.

This file pins three properties so the lifecycle (per
``docs/RELEASE-CADENCE.md`` + ``docs/ADAPTER-GOVERNANCE.md``) doesn't
silently degrade:

1. **Importing the shim emits exactly one ``DeprecationWarning``** that
   names the new module path. Without this, the migration signal can
   disappear on a refactor (e.g. someone replaces ``warnings.warn``
   with a logger call) and downstream callers never see the deprecation.
2. **Re-exported symbols are the same object as the new module's**
   symbols. Re-exporting via copy / wrapper would silently break
   ``isinstance`` checks on shim consumers — a particularly nasty
   class of bug because it survives the test suite.
3. **Removing the shim is a real diff**, not a doc edit. The plan
   commits to "removed two minor releases from now"; that promise is
   only meaningful if the shim files exist on disk and not as
   __getattr__ tricks somewhere else. The presence assertion below is
   the breadcrumb that flags shim removal in a future PR.

Imports are done inside the test bodies (not at module top) so each
test gets a fresh import for warning capture. ``importlib.reload`` is
used so the cached module's import-time warning fires again.
"""
from __future__ import annotations

import importlib
import warnings


_SHIM_MODULES: tuple[tuple[str, str], ...] = (
    (
        "darkfactory.spec.library_symbols",
        "darkfactory.adapters.typescript.library_symbols",
    ),
    (
        "darkfactory.spec.stub_compile",
        "darkfactory.adapters.typescript.stub_compile",
    ),
    (
        "darkfactory.scenarios.proposal_rehearsal",
        "darkfactory.adapters.typescript.proposal_rehearsal",
    ),
    # Sprint 2 A3: Postgres adapter extraction. Mirrors the A2 shims
    # above so the deprecation lifecycle is uniform across adapters.
    (
        "darkfactory.spec.sql_lint",
        "darkfactory.adapters.postgres.sql_lint",
    ),
)


def _reimport(module_name: str):
    """Force-reimport so the shim's import-time ``warn`` fires fresh."""
    import sys
    if module_name in sys.modules:
        return importlib.reload(sys.modules[module_name])
    return importlib.import_module(module_name)


def test_each_shim_emits_one_deprecation_warning_naming_new_path():
    for old_path, new_path in _SHIM_MODULES:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DeprecationWarning)
            _reimport(old_path)
        deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
        assert len(deprecations) == 1, (
            f"{old_path}: expected exactly one DeprecationWarning, "
            f"got {len(deprecations)}"
        )
        message = str(deprecations[0].message)
        assert old_path in message and new_path in message, (
            f"{old_path}: warning message must reference both old and "
            f"new paths so the migration is unambiguous; got: {message!r}"
        )


def test_shim_reexports_are_identity_same_as_new_module():
    """Re-exports must be the SAME object as the new module's symbols.

    Wrapping or copying would silently break ``isinstance`` checks on
    shim consumers — e.g. if a caller does
    ``isinstance(x, library_symbols.LibrarySymbolFinding)`` against the
    shim and the new module emits an instance through a different
    class path, the check returns False and the consumer's diagnostic
    code skips the branch.
    """
    for old_path, new_path in _SHIM_MODULES:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            old_mod = _reimport(old_path)
            new_mod = importlib.import_module(new_path)
        for name in old_mod.__all__:
            assert getattr(old_mod, name) is getattr(new_mod, name), (
                f"{old_path}.{name} is not the same object as "
                f"{new_path}.{name} — the shim is wrapping rather than "
                f"re-exporting and ``isinstance`` will silently break"
            )


def test_shim_files_exist_at_old_paths():
    """Pin that the shim is a real file (not an importlib trick).

    The S0.3 governance commits to "removed two minor releases from
    now"; that removal is a delete-the-file diff. If the shim ever
    becomes a ``__getattr__`` on a parent package or a sys.modules
    entry, this test fails and forces the maintainer to update the
    governance doc with the new mechanism.
    """
    import darkfactory.spec.library_symbols as ls
    import darkfactory.spec.stub_compile as sc
    import darkfactory.scenarios.proposal_rehearsal as pr
    import darkfactory.spec.sql_lint as sl
    for mod, expected_basename in (
        (ls, "library_symbols.py"),
        (sc, "stub_compile.py"),
        (pr, "proposal_rehearsal.py"),
        (sl, "sql_lint.py"),
    ):
        assert mod.__file__ is not None, f"{mod.__name__} has no __file__"
        assert mod.__file__.endswith(expected_basename), (
            f"{mod.__name__} is not a file shim; __file__={mod.__file__}"
        )
