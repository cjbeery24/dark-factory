"""``dark-factory contracts verify <adapter>`` — Phase 4 Contracts CLI v1.

Walks the project's adapter contracts dir
(``<project>/.dark-factory/contracts/<adapter>/``) and applies the
silent-wrong-helper guard registry from
:mod:`darkfactory.contracts.silent_wrong_registry`. Reports any
helper whose source has lost its safety guard or contains a B5
foot-gun shape.

Closes the helper-source-regression-at-edit-time hole left by v0:
v0 copies starters into the project; the developer hand-edits them
to fit project conventions; if those edits remove a safety guard,
the next pipeline run accepts the broken helper. v1's verifier runs
between v0 and the pipeline, catching the regression at the point
it's introduced.

**Architectural choice (neo's option D, Linus-reviewed):** static
pattern match against helper source, not vitest execution. v1
catches "developer removed the guard" — the actual failure mode the
postgres starters exhibit. Behavioral verification (helper accepts
wrong input) is a v2 concern; today's silent-wrong helpers' guards
are syntactic (`expected === ''` check, `client.begin` wrapper),
detectable by source-text inspection.

**Authorization model** (AUTHORIZATION-MODEL.md):

Trigger #1 (audience escalation): NO. Verifier reads helper sources
host-side and emits violations to stdout/stderr. No string crosses
into a barrier-restricted reader's view.

Trigger #2 (changed-surfaces declaration): NO. ``barriers.py``,
``quality-gate.sh``, ``scenario_lint.py`` untouched. No new
``.dark-factory/`` artifact written.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from darkfactory.contracts.foot_gun_patterns import BY_NAME_AND_DIALECT
from darkfactory.contracts.silent_wrong_registry import (
    FOOT_GUN_BANS,
    GUARDS,
    HELPER_DECL_RE,
    adapters as registered_adapters,
)


log = logging.getLogger(__name__)


ViolationKind = Literal["missing_guard", "foot_gun_in_helper"]


@dataclass(frozen=True)
class Violation:
    """One verification failure.

    ``kind`` distinguishes the two failure modes:

    * ``missing_guard``: a :class:`HelperGuard` regex didn't match
      the file declaring the helper. The helper has lost its safety
      check.
    * ``foot_gun_in_helper``: a :class:`HelperFootGunBan` regex
      matched a helper file's source. The helper itself contains a
      foot-gun shape it was supposed to prevent.

    ``where`` is the relative path of the file that failed (or the
    helper-name for missing_guard cases where the file is the one
    declaring it). ``reason`` is the registry's full prose; the CLI
    surfaces only the first sentence as the headline.
    """

    kind: ViolationKind
    helper_or_file: str
    where: str
    reason: str


@dataclass(frozen=True)
class VerifyResult:
    """Outcome of one ``verify_adapter_helpers`` invocation.

    ``violations`` is empty iff the verification passed. ``helpers_
    skipped`` lists helper names the registry knows about but the
    project didn't declare — surfaced as INFO (Linus v1 review #5)
    so a developer who renames a helper sees the silent skip.
    """

    adapter: str
    contracts_dir: Path
    helpers_checked: list[str] = field(default_factory=list)
    helpers_skipped: list[str] = field(default_factory=list)
    files_scanned: list[str] = field(default_factory=list)
    violations: list[Violation] = field(default_factory=list)


def _find_declaring_file(
    helper_name: str, ts_files: list[Path],
) -> Path | None:
    """Return the file declaring ``export async function <helper_name>``,
    or None if no file in ``ts_files`` declares it.

    Files that fail to read (permission, encoding) are skipped with a
    warning rather than swallowed silently — a file the developer
    cannot read shouldn't disappear from the verifier's scope without
    any signal (Linus v1 review final-pass).
    """
    decl = re.compile(HELPER_DECL_RE.format(name=re.escape(helper_name)))
    for f in ts_files:
        try:
            if decl.search(f.read_text(encoding="utf-8")):
                return f
        except (OSError, UnicodeDecodeError) as exc:
            log.warning(
                "verify: skipping unreadable helper file %s: %s", f, exc,
            )
            continue
    return None


def verify_adapter_helpers(
    adapter: str,
    project_dir: Path,
) -> VerifyResult:
    """Verify the project's helper sources for ``adapter`` against
    the silent-wrong registry.

    Walks ``<project_dir>/.dark-factory/contracts/<adapter>/*.ts``,
    applies :data:`GUARDS[adapter]` per-helper (helper-presence
    check first, then guard-pattern check), and applies
    :data:`FOOT_GUN_BANS[adapter]` per-file (B5 pattern lookup).
    Returns a :class:`VerifyResult` with the violations list, the
    helpers that were checked vs skipped, and the files scanned.

    Raises ``ValueError`` if ``adapter`` has no registry entries.
    """
    if adapter not in registered_adapters():
        raise ValueError(
            f"unknown adapter {adapter!r} — no silent-wrong registry "
            f"entries. Known adapters: {registered_adapters()!r}"
        )

    contracts_dir = project_dir / ".dark-factory" / "contracts" / adapter
    if not contracts_dir.is_dir():
        raise ValueError(
            f"contracts dir does not exist: {contracts_dir}. Run "
            f"`dark-factory contracts init {adapter}` first."
        )

    ts_files = sorted(contracts_dir.glob("*.ts"))
    files_scanned = [str(f.relative_to(project_dir)) for f in ts_files]

    helpers_checked: list[str] = []
    helpers_skipped: list[str] = []
    violations: list[Violation] = []

    # Per-helper guard checks. Each guard names a helper; we find the
    # declaring file (skipping if absent — developer may have removed
    # the helper intentionally), then run the guard's pattern against
    # that file's source.
    for guard in GUARDS.get(adapter, []):
        decl_file = _find_declaring_file(guard.helper_name, ts_files)
        if decl_file is None:
            helpers_skipped.append(guard.helper_name)
            log.info(
                "verify: helper %r not found in %s — skipped",
                guard.helper_name, contracts_dir,
            )
            continue
        helpers_checked.append(guard.helper_name)
        src = decl_file.read_text(encoding="utf-8")
        if not guard.pattern.search(src):
            violations.append(Violation(
                kind="missing_guard",
                helper_or_file=guard.helper_name,
                where=str(decl_file.relative_to(project_dir)),
                reason=guard.reason,
            ))

    # Per-file foot-gun bans. Per Linus v1 review #3, scope is per-
    # file (not concatenated) — accepts a known false-positive risk
    # on multi-function helper files where B5 cross-line patterns
    # could span unrelated functions.
    for ts_file in ts_files:
        try:
            src = ts_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for ban in FOOT_GUN_BANS.get(adapter, []):
            try:
                b5_entry = BY_NAME_AND_DIALECT[(ban.b5_name, ban.b5_dialect)]
            except KeyError:
                # Registry references a missing B5 entry — fail loud
                # rather than silently skip; this is a maintainer-side
                # registry bug.
                raise ValueError(
                    f"silent_wrong_registry references missing B5 "
                    f"pattern ({ban.b5_name!r}, {ban.b5_dialect!r}). "
                    f"Check darkfactory.contracts.foot_gun_patterns."
                )
            if b5_entry.pattern.search(src):
                violations.append(Violation(
                    kind="foot_gun_in_helper",
                    helper_or_file=ban.b5_name,
                    where=str(ts_file.relative_to(project_dir)),
                    reason=ban.reason,
                ))

    return VerifyResult(
        adapter=adapter,
        contracts_dir=contracts_dir,
        helpers_checked=helpers_checked,
        helpers_skipped=helpers_skipped,
        files_scanned=files_scanned,
        violations=violations,
    )
