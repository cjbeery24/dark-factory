"""``dark-factory contracts init <adapter>`` — copy framework
project-starter templates into a target project's
``<project>/.dark-factory/contracts/<adapter>/`` (Phase 4 Contracts
CLI v0).

The starter set ships with the framework under
``templates/contracts/<adapter>/project-starters/`` (B9a v(N) for the
postgres adapter; future adapters add their own starter dirs there).
This module is the bootstrap helper that brings those starters into
the developer's project so the developer can edit the
project-convention helpers (``app.tenant_id`` GUC names, RLS column
choices, etc.) before running the pipeline.

Refuses to overwrite existing destination files unless ``force=True``.
``dry_run=True`` returns the would-be result without touching the
filesystem.

**Editable-install limitation (documented framework-wide):** the
starter directory is resolved via
``Path(darkfactory.__file__).parent.parent / "templates" / ...`` — the
same pattern used by ``darkfactory.setup.init._copy_framework_files``.
This works for ``pip install -e .`` developer installs but fails for
non-editable wheel installs because ``templates/`` lives outside the
``darkfactory/`` package (separate pre-existing bug; tracked at
``tests/unit/test_packaging_data_files.py``'s docstring). When that
bug is fixed (move ``templates/`` into ``darkfactory/templates/`` +
extend ``[tool.setuptools.package-data]``), this module's path
resolution flips automatically.

**Authorization model** (AUTHORIZATION-MODEL.md):

Trigger #1 (audience escalation): NO. Files copied are TS source
helpers and a README — no test-shape information. The destination
``.dark-factory/contracts/<adapter>/`` is intentionally readable by
agent containers (the orchestrator stages it under
``_contracts/<adapter>/project/`` per B9d).

Trigger #2 (changed-surfaces declaration): NO. ``barriers.py``
untouched, ``quality-gate.sh`` untouched, ``scenario_lint.py``
untouched. New artifact under ``.dark-factory/contracts/<adapter>/``
is the documented project-helpers home (B9d) — not a new mask
surface.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path

import darkfactory


@dataclass
class InitResult:
    """Outcome of one ``init_adapter_contracts`` invocation.

    ``files_copied`` is relative paths that did NOT exist at the
    destination before this invocation and were freshly written.
    ``force_overwritten`` is relative paths that DID exist and were
    overwritten because ``force=True``. ``skipped_existing`` is
    relative paths that DID exist and were NOT overwritten because
    ``force=False``. The three lists are mutually exclusive.

    The split between ``files_copied`` and ``force_overwritten`` is
    load-bearing: a maintainer running ``init --force`` after editing
    project-convention helpers in place must SEE that those edits are
    being overwritten (Linus B9b/Phase-4 v0 review). The CLI handler
    surfaces ``force_overwritten`` separately so the output is not
    "we wrote N files" with no signal that K of them stomped local
    work.

    ``dry_run`` is a faithful record of the flag passed in — callers
    may want to message differently when describing planned vs actual
    work.
    """

    adapter: str
    src_dir: Path
    dst_dir: Path
    files_copied: list[str] = field(default_factory=list)
    force_overwritten: list[str] = field(default_factory=list)
    skipped_existing: list[str] = field(default_factory=list)
    dry_run: bool = False


def _framework_root() -> Path:
    """Locate the framework's repo root via ``darkfactory.__file__``.

    Editable-install assumption: the parent of the ``darkfactory/``
    package directory IS the repo root with ``templates/``. Fails on
    non-editable wheel installs (see module docstring).

    TODO (before v1 / first non-editable wheel release): move
    ``templates/`` under ``darkfactory/contracts/templates/`` and
    switch this resolver to
    ``importlib.resources.files("darkfactory.contracts") / "templates"``.
    Concurrent fix needed in
    ``darkfactory.setup.init._copy_framework_files`` (same broken
    pattern). Linus Phase-4-v0 review flagged this as
    "not-a-blocker-but-must-fix-before-v1".
    """
    return Path(darkfactory.__file__).resolve().parent.parent


def starters_dir(adapter: str) -> Path:
    """Path to the framework's project-starters directory for
    ``adapter`` — the *source* of files an ``init`` invocation will
    copy. May or may not exist; callers handle the missing case
    explicitly via ``init_adapter_contracts``'s ValueError path.
    """
    return _framework_root() / "templates" / "contracts" / adapter / "project-starters"


def list_available_adapters() -> list[str]:
    """Adapters with a project-starters dir shipped by the framework.

    Returns adapter names sorted alphabetically. An empty list means
    the framework's ``templates/contracts/`` tree is missing — most
    likely a non-editable wheel install (see editable-install
    limitation in the module docstring).
    """
    contracts_root = _framework_root() / "templates" / "contracts"
    if not contracts_root.is_dir():
        return []
    return sorted(
        d.name for d in contracts_root.iterdir()
        if d.is_dir() and (d / "project-starters").is_dir()
    )


def init_adapter_contracts(
    adapter: str,
    project_dir: Path,
    *,
    force: bool = False,
    dry_run: bool = False,
) -> InitResult:
    """Copy starter helpers for ``adapter`` from the framework into
    ``<project_dir>/.dark-factory/contracts/<adapter>/``.

    Recursive: subdirectories under the starters dir are preserved.
    Per-file overwrite policy: a file already present at the
    destination is skipped (recorded in ``skipped_existing``) unless
    ``force=True``, in which case it is overwritten. ``dry_run=True``
    suppresses the filesystem write but populates ``files_copied`` /
    ``skipped_existing`` exactly as the live run would.

    Raises ``ValueError`` if the framework has no starter dir for
    ``adapter`` — error message lists every adapter the framework
    does ship starters for, so the caller's typo is diagnosable
    without grepping the framework tree.
    """
    src_dir = starters_dir(adapter)
    if not src_dir.is_dir():
        available = list_available_adapters()
        raise ValueError(
            f"unknown adapter {adapter!r} — no starter dir at "
            f"{src_dir}. Available adapters: {available!r}. "
            "(If the available list is empty, you may be running from "
            "a non-editable wheel install — see "
            "darkfactory.contracts.init module docstring.)"
        )

    dst_dir = project_dir / ".dark-factory" / "contracts" / adapter

    files_copied: list[str] = []
    force_overwritten: list[str] = []
    skipped_existing: list[str] = []

    # Sorted relative paths give a deterministic copy order — useful
    # in tests asserting on the result's lists.
    for src_file in sorted(src_dir.rglob("*")):
        if not src_file.is_file():
            continue
        rel_path = str(src_file.relative_to(src_dir))
        dst_file = dst_dir / rel_path
        existed_before = dst_file.exists()
        if existed_before and not force:
            skipped_existing.append(rel_path)
            continue
        if not dry_run:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            # ``shutil.copy`` (not ``copy2``) — these are project-
            # source files being introduced into the developer's
            # working tree. Fresh ``mtime``s let editors and
            # ``git status`` see them as just-created (Linus
            # Phase-4-v0 review). Mode preservation is also wrong:
            # TS / Markdown source files don't need framework-source
            # permission bits.
            shutil.copy(src_file, dst_file)
        if existed_before:
            force_overwritten.append(rel_path)
        else:
            files_copied.append(rel_path)

    return InitResult(
        adapter=adapter,
        src_dir=src_dir,
        dst_dir=dst_dir,
        files_copied=files_copied,
        force_overwritten=force_overwritten,
        skipped_existing=skipped_existing,
        dry_run=dry_run,
    )
