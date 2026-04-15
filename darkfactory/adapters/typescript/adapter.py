"""``TypescriptNodeAdapter`` — concrete ``EcosystemAdapter`` for
TypeScript + Node projects.

This file is the contract surface; the real work lives in the sibling
modules (``library_symbols``, ``stub_compile``, ``proposal_rehearsal``).
The adapter itself is a thin glue layer whose job is to:

1. Tell the registry "I apply when these markers are present"
   (``detection_signals`` / ``detect``).
2. Convert the existing precheck functions' ad-hoc finding dataclasses
   into the unified ``Finding`` shape the ABC contract requires.
3. Emit a ``GateCommands`` from the architect manifest so the gate
   harness can compose commands across active adapters once A5
   wires the registry into ``pipeline/quality_gate.py``.

A2 deliberately keeps the orchestrator's existing call sites intact
(orchestrator still calls the moved modules' top-level functions
directly). A5 will swap those direct calls for adapter dispatch through
the registry. Decoupling the move (A2) from the dispatch swap (A5)
keeps both diffs reviewable.

**Construction context:** the adapter accepts an optional
``project_dir`` because two of the operations it wraps
(``proposal_rehearsal_typecheck`` in particular) need the project's
``tsconfig.json`` and ``node_modules`` to do anything useful. The
contract harness instantiates the adapter with no project context, in
which case the rehearsal hook returns ``None`` (treated as opt-out by
the orchestrator). When A5 lands, the registry will instantiate one
adapter per project run with ``project_dir`` set.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.adapters.base import (
    EcosystemAdapter,
    Finding,
    GateCommands,
)
from darkfactory.adapters.typescript.library_symbols import (
    LibrarySymbolFinding,
    check_library_symbols,
)
from darkfactory.adapters.typescript.proposal_rehearsal import (
    Proposal,
    typecheck_proposals,
)
from darkfactory.adapters.typescript.stub_compile import (
    StubCompileFinding,
    check_stub_roundtrip,
)
from darkfactory.architecture.consistency import ConsistencyRule
from darkfactory.spec.manifest import ArchitectureManifest
from darkfactory.spec.spec_model import SpecModel

log = logging.getLogger(__name__)


# Loaded lazily so the import-time side effect is just the file open,
# not the whole markdown text staying resident if the addendum isn't
# requested. Cached on first read; the file is part of the wheel and
# never changes at runtime.
_ADDENDUM_PATH = Path(__file__).with_name("architect_addendum.md")
_addendum_cache: str | None = None


def _load_addendum() -> str:
    global _addendum_cache
    if _addendum_cache is None:
        try:
            _addendum_cache = _ADDENDUM_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            # The addendum is shipped with the package; missing is a
            # packaging bug, not a runtime condition. Fail loudly so it
            # surfaces in CI rather than silently degrading the
            # architect prompt.
            raise RuntimeError(
                f"TypescriptNodeAdapter addendum missing at {_ADDENDUM_PATH}"
            ) from exc
    return _addendum_cache


@dataclass
class TypescriptNodeAdapter(EcosystemAdapter):
    """The TypeScript + Node adapter.

    Construction:

        adapter = TypescriptNodeAdapter()                 # contract-test instantiation
        adapter = TypescriptNodeAdapter(project_dir=...)  # production instantiation

    Without ``project_dir``, methods that need a real project tree
    degrade gracefully (``proposal_rehearsal_typecheck`` returns
    ``None``); ``detect``/``precheck`` accept ``project_dir`` as an
    argument and are unaffected.
    """

    # Dataclass field defaults shadow the abstract ``name`` /
    # ``detection_signals`` properties on the ABC at class-definition
    # time — the canonical pattern documented in
    # ``darkfactory/adapters/base.py``.
    name: str = "typescript-node"
    detection_signals: tuple[str, ...] = (
        "package.json",
        "tsconfig.json",
        "pnpm-lock.yaml",
    )
    project_dir: Path | None = None
    # Mutable container kept on the instance so a future caller could
    # inject extra rules at construction without subclassing. A5's
    # registry composition will pass project-hints rules through here.
    extra_consistency_rules: list[ConsistencyRule] = field(default_factory=list)

    # ---- ABC: detection -------------------------------------------------

    def detect(self, project_dir: Path) -> bool:
        """True iff any TypeScript/Node detection signal exists at the root.

        ``package.json`` alone is sufficient — a pure-JS project still
        belongs to this adapter (the bulk of the precheck logic
        applies). ``tsconfig.json`` without ``package.json`` is
        unusual but legal (e.g. a TS-only doc tree); we accept it.
        """
        for signal in self.detection_signals:
            if (project_dir / signal).exists():
                return True
        return False

    # ---- ABC: precheck --------------------------------------------------

    def precheck(self, spec: SpecModel, project_dir: Path) -> list[Finding]:
        """Run A3 (library-symbol) and A6 (stub round-trip) checks.

        Both already produce typed diagnostic dataclasses
        (``LibrarySymbolFinding``, ``StubCompileFinding``); this
        method translates them into the ABC's unified ``Finding``
        shape so downstream consumers don't have to know about the
        per-check internal types.
        """
        out: list[Finding] = []
        for lf in check_library_symbols(spec, project_dir=project_dir):
            out.append(_lib_finding_to_finding(lf))
        for sf in check_stub_roundtrip(spec, project_dir=project_dir):
            out.append(_stub_finding_to_finding(sf))
        return out

    # ---- ABC: consistency rules ----------------------------------------

    def consistency_rules(self) -> list[ConsistencyRule]:
        """Adapter-baseline consistency rules.

        A2 returns only rules supplied via ``extra_consistency_rules``.
        Migrating the existing ``DEFAULT_RULES`` (currently in
        ``architecture/consistency.py``) into ecosystem-owned rule sets
        is governed by the plan's "Sprint 3 hard ordering constraint
        for ``DEFAULT_RULES`` migration" — touching it during A2 would
        couple the move to a behavior change the equivalence harness
        can't yet certify. Returning ``[]`` plus injectable extras
        gives A5/Sprint 3 the seam without making A2 a behavior change.
        """
        return list(self.extra_consistency_rules)

    # ---- ABC: architect prompt addendum --------------------------------

    def architect_addendum(self) -> str:
        """Return the markdown addendum injected into the architect prompt.

        Read from ``architect_addendum.md`` next to this module. A4
        replaces the placeholder content with the Node-specific
        guidance currently in ``agents/architect.md``; the file path
        and loader stay the same.
        """
        return _load_addendum()

    # ---- ABC: quality-gate dispatch ------------------------------------

    def quality_gate_dispatch(
        self, manifest: ArchitectureManifest
    ) -> GateCommands:
        """Map the architect's ``commands.*`` ops into ``GateCommands``.

        The architect already emits per-phase commands via
        ``runtime.amendment_ops["commands.*"]`` (existing behavior; see
        ``ArchitectureManifest.runtime``). This method just unpacks
        them into the typed ``GateCommands`` surface so the gate
        harness can compose phase commands across multiple active
        adapters once A5 routes through the registry.

        Empty string for any phase the architect did not declare —
        the documented "skip phase" sentinel pinned by
        ``test_gate_commands_empty_means_skip`` in the contract harness.
        """
        ops = manifest.runtime.amendment_ops
        return GateCommands(
            install=_op_str(ops, "commands.install"),
            build=_op_str(ops, "commands.build"),
            lint=_op_str(ops, "commands.lint"),
            typecheck=_op_str(ops, "commands.typecheck"),
            test=_op_str(ops, "commands.test"),
            scenario_test=_op_str(ops, "commands.scenario_test"),
        )

    # ---- ABC: proposal-rehearsal typecheck -----------------------------

    def proposal_rehearsal_typecheck(
        self, scratch_dir: Path
    ) -> list[Finding] | None:
        """Run ``tsc --noEmit`` against ``*.ts`` files in ``scratch_dir``.

        Contract: ``scratch_dir`` is a directory the orchestrator has
        already populated with proposal test files (one ``*.ts`` per
        anchor). This method walks ``scratch_dir`` for ``*.ts`` files,
        constructs ``Proposal`` objects from them, and delegates to
        ``typecheck_proposals`` — the canonical tsc-rehearsal codepath
        that ``run_proposal_rehearsal`` also uses. Keeping this as a
        delegation (rather than a parallel implementation) means the
        cleanup, error-handling, and tsc-invocation semantics stay in
        one place; A5's registry dispatch will route through this method
        without needing to reconcile two near-identical loops.

        Returns ``None`` (opt-out) when:

        - The adapter was constructed without a ``project_dir``
          (contract-test instantiation; rehearsal cannot resolve
          tsc/tsconfig without project context).
        - ``scratch_dir`` contains no ``*.ts`` files (nothing to check).

        ``None`` is the documented opt-out signal in the ABC contract
        — distinct from "rehearsal ran and found nothing" (empty list).
        ``typecheck_proposals``'s own no-op cases (missing tsc, missing
        tsconfig, missing node_modules) return an empty findings list,
        which we faithfully propagate as ``[]`` rather than ``None`` —
        the upstream context already decided the rehearsal *should* run.
        """
        if self.project_dir is None:
            return None

        ts_files = sorted(p for p in scratch_dir.rglob("*.ts") if p.is_file())
        if not ts_files:
            return None

        # Build Proposal objects from on-disk files. ``typecheck_proposals``
        # writes proposals back into scratch_dir before invoking tsc; for
        # files we just read from there the write is a no-op overwrite,
        # but it keeps the codepath single — no separate "files already
        # on disk" branch to maintain.
        proposals: list[Proposal] = []
        for ts_path in ts_files:
            try:
                rel = str(ts_path.relative_to(scratch_dir))
            except ValueError:
                rel = ts_path.name
            proposals.append(Proposal(
                anchor=ts_path.stem,
                filepath=rel,
                content=ts_path.read_text(encoding="utf-8", errors="replace"),
            ))

        findings, _failing_anchors = typecheck_proposals(
            proposals, self.project_dir, scratch_dir=scratch_dir,
        )
        return [_stub_finding_to_finding(f) for f in findings]


# ---------------------------------------------------------------------------
# Finding adapters — translate per-check dataclasses into the ABC's
# unified ``Finding`` shape. Kept as plain functions (not methods) so
# they're trivially testable without instantiating the adapter.
# ---------------------------------------------------------------------------


def _lib_finding_to_finding(lf: LibrarySymbolFinding) -> Finding:
    """Translate a library-symbol finding into a generic ``Finding``.

    Severity is always ``error`` here: an undeclared import is a
    fail-precheck condition, never a warning.
    """
    return Finding(
        severity="error",
        code="ts-undeclared-import",
        message=lf.message,
        path="",  # LibrarySymbolFinding doesn't carry a path; layer/message has the context
        line=0,
    )


def _stub_finding_to_finding(sf: StubCompileFinding) -> Finding:
    """Translate a tsc diagnostic into a generic ``Finding``.

    The ``code`` carries the tsc error code (e.g. ``TS2339``) so
    downstream consumers can group/filter without parsing the message
    text. ``severity`` is ``error`` because tsc only emits errors
    through this path (``--noEmit`` doesn't produce warnings in our
    invocation).
    """
    return Finding(
        severity="error",
        code=sf.tsc_code,
        message=sf.message,
        path=sf.filepath,
        line=sf.stub_line or 0,
    )


def _op_str(ops: dict[str, object], key: str) -> str:
    """Read a ``commands.*`` value from amendment ops as a string.

    The architect emits commands as scalar strings via
    ``runtime.amendment_ops``; we tolerate either a string value or a
    list-of-strings (the latter joined with `` && ``) so a future
    architect that emits compound commands as lists doesn't silently
    drop them.
    """
    raw = ops.get(key, "")
    if isinstance(raw, list):
        return " && ".join(str(x) for x in raw if x)
    return str(raw) if raw else ""
