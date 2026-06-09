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

import re

from darkfactory.adapters.base import (
    EcosystemAdapter,
    Finding,
    GateCommands,
    ImportInfo,
    load_packaged_addendum,
    op_str,
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


# Path constant only — the cache + loader live in
# ``darkfactory.adapters.base.load_packaged_addendum`` (single module-
# global cache shared across all adapters). Adapter-specific paths stay
# local; the loading machinery does not.
_ADDENDUM_PATH = Path(__file__).with_name("architect_addendum.md")


@dataclass
class TypescriptNodeAdapter(EcosystemAdapter):
    """The TypeScript + Node adapter.

    Construction:

        adapter = TypescriptNodeAdapter()                 # contract-test instantiation
        adapter = TypescriptNodeAdapter(project_dir=...)  # production instantiation

    Without ``project_dir``, methods that need a real project tree
    either degrade gracefully (``proposal_rehearsal_typecheck``
    returns ``None``) or raise a clear error (``detect`` / ``precheck``
    — see those methods for the "project_dir required" rationale).
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

    def detect(self) -> bool:
        """True iff any TypeScript/Node detection signal exists at the root.

        ``package.json`` alone is sufficient — a pure-JS project still
        belongs to this adapter (the bulk of the precheck logic
        applies). ``tsconfig.json`` without ``package.json`` is
        unusual but legal (e.g. a TS-only doc tree); we accept it.

        Reads ``self.project_dir``; raises if it was not bound at
        construction. See the ABC's ``detect`` docstring for why the
        arg form was removed.
        """
        project_dir = self._require_project_dir("detect")
        for signal in self.detection_signals:
            if (project_dir / signal).exists():
                return True
        return False

    # ---- ABC: precheck --------------------------------------------------

    def precheck(self, spec: SpecModel) -> list[Finding]:
        """Run A3 (library-symbol) and A6 (stub round-trip) checks.

        Both already produce typed diagnostic dataclasses
        (``LibrarySymbolFinding``, ``StubCompileFinding``); this
        method translates them into the ABC's unified ``Finding``
        shape so downstream consumers don't have to know about the
        per-check internal types.
        """
        project_dir = self._require_project_dir("precheck")
        out: list[Finding] = []
        for lf in check_library_symbols(spec, project_dir=project_dir):
            out.append(_lib_finding_to_finding(lf))
        for sf in check_stub_roundtrip(spec, project_dir=project_dir):
            out.append(_stub_finding_to_finding(sf))
        return out

    def _require_project_dir(self, method_name: str) -> Path:
        """Return ``self.project_dir``, or raise if it wasn't bound.

        Centralized so all methods that need a project tree surface
        the same error message — the caller learns which adapter and
        which method tried to read an unbound path, which is more
        useful at debug time than a bare ``AttributeError`` from
        operating on ``None``.
        """
        if self.project_dir is None:
            raise RuntimeError(
                f"{type(self).__name__}.{method_name}() requires "
                "project_dir to be bound at construction. The registry "
                "binds it via load_active_adapters(..., project_dir=...); "
                "tests that exercise this method must construct the "
                "adapter with an explicit project_dir."
            )
        return self.project_dir

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

        A4 migrated MONOREPO COMMAND SCOPING and the TEST INTEGRATION
        CONTRACT sections out of ``agents/architect.md`` into
        ``architect_addendum.md`` next to this module. The file path
        and loader did not change; only the file contents did.
        """
        return load_packaged_addendum(self.name, _ADDENDUM_PATH)

    # ---- ABC: spec validator prompt addendum ---------------------------

    def spec_validator_addendum(self) -> str:
        """No TypeScript-specific validator-time guidance yet.

        Returns ``""`` — the documented opt-out sentinel pinned by the
        contract harness. TypeScript's existing precheck (library-
        symbol + stub round-trip) already runs at spec time and feeds
        Findings into the validator's view; a separate validator
        prompt addendum has no work to do today.
        """
        return ""

    # ---- ABC: scenario architect prompt addendum ----------------------

    def scenario_architect_addendum(self) -> str:
        """No TypeScript-specific scenario-time guidance yet.

        Returns ``""`` — same opt-out sentinel as
        ``spec_validator_addendum``. The Test Engineer's existing
        anchor-aligned generation already produces TS-shaped scenarios
        without needing an ecosystem prompt addendum; substrate-layer
        directives are Postgres-specific (Piece 4 of the
        2026-04-15 substrate verification plan).
        """
        return ""

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
            install=op_str(ops, "commands.install"),
            build=op_str(ops, "commands.build"),
            lint=op_str(ops, "commands.lint"),
            typecheck=op_str(ops, "commands.typecheck"),
            test=op_str(ops, "commands.test"),
            scenario_test=op_str(ops, "commands.scenario_test"),
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

    # ---- ABC: substrate verification ----------------------------------

    def verify_substrate(
        self,
        sidecar_url: str,
        manifest_path: Path,
        spec_text: str,
    ) -> list[Finding] | None:
        """No TypeScript-specific substrate verification.

        Returns ``None`` unconditionally — TS has no substrate taxonomy,
        so ``spec_text`` and ``sidecar_url`` are unused. This is the
        documented opt-out (Piece 5 of the 2026-04-15 substrate
        verification plan); only Postgres ships a substrate manifest
        today, and only Postgres can verify one.
        """
        del sidecar_url, manifest_path, spec_text
        return None

    # ---- M1: workspace detection ------------------------------------------

    def detect_workspaces(self, project_dir: Path) -> list[str]:
        """Discover pnpm/npm/yarn workspace package directories.

        Reads ``pnpm-workspace.yaml`` first (pnpm), then falls back to
        ``package.json#workspaces`` (npm/yarn). Returns workspace-relative
        directory paths (e.g. ``["apps/api", "packages/shared"]``).
        """
        import json as _json

        # pnpm: pnpm-workspace.yaml
        ws_yaml = project_dir / "pnpm-workspace.yaml"
        if ws_yaml.exists():
            try:
                text = ws_yaml.read_text(encoding="utf-8")
                return self._resolve_workspace_globs(
                    project_dir, self._parse_pnpm_workspace_yaml(text),
                )
            except Exception:
                log.debug("failed to parse pnpm-workspace.yaml", exc_info=True)

        # npm/yarn: package.json#workspaces
        pkg_json = project_dir / "package.json"
        if pkg_json.exists():
            try:
                pkg = _json.loads(pkg_json.read_text(encoding="utf-8"))
                ws = pkg.get("workspaces", [])
                # yarn v2+ uses {packages: [...]} form
                if isinstance(ws, dict):
                    ws = ws.get("packages", [])
                if isinstance(ws, list) and ws:
                    return self._resolve_workspace_globs(project_dir, ws)
            except Exception:
                log.debug("failed to parse package.json workspaces", exc_info=True)

        return []

    @staticmethod
    def _parse_pnpm_workspace_yaml(text: str) -> list[str]:
        """Minimal parser for pnpm-workspace.yaml — extracts packages list."""
        globs: list[str] = []
        in_packages = False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("packages:"):
                in_packages = True
                continue
            if in_packages:
                if stripped.startswith("- "):
                    val = stripped[2:].strip().strip("'\"")
                    if val:
                        globs.append(val)
                elif stripped and not stripped.startswith("#"):
                    break  # new top-level key
        return globs

    @staticmethod
    def _resolve_workspace_globs(
        project_dir: Path, globs: list[str],
    ) -> list[str]:
        """Resolve workspace glob patterns to actual directories."""
        import glob as _glob

        dirs: list[str] = []
        for pattern in globs:
            # Expand globs like "apps/*", "packages/*"
            matched = sorted(_glob.glob(
                str(project_dir / pattern), recursive=False,
            ))
            for m in matched:
                mp = Path(m)
                if mp.is_dir():
                    try:
                        dirs.append(str(mp.relative_to(project_dir)))
                    except ValueError:
                        pass
        return dirs

    # ---- M1: command scope validation ------------------------------------

    # Regex: cd <dir> (with optional && or ;)
    _CD_RE = re.compile(r'\bcd\s+([^\s;&|]+)')
    # Regex: pnpm --filter <pkg>
    _PNPM_FILTER_RE = re.compile(r'--filter\s+([^\s;&|]+)')

    def validate_command_scope(
        self,
        command: str,
        source_dirs: list[str],
        *,
        workspaces: list[str] | None = None,
    ) -> list[Finding]:
        """Check that a command targets a scope-consistent directory.

        Extracts target directories from ``cd <dir>`` and ``--filter <pkg>``
        patterns, then verifies each target is consistent.

        Anchor selection (Sprint 5): when ``workspaces`` (real declared
        workspace dirs, e.g. from ``detect_workspaces()``) is non-empty, anchor
        on it — it is machine-authoritative (pnpm-workspace.yaml) and drift-proof,
        and validating against it is external to the manifest (non-circular).
        This avoids the false-positive where a command correctly targets the
        spec's real workspace (``cd packages/shared``) but the configured
        ``source_dirs`` has drifted to a different workspace. Fall back to
        ``source_dirs`` only when no workspaces are detected (non-monorepo repos).
        """
        if not command:
            return []
        anchor_ws = bool(workspaces)
        anchors = workspaces if anchor_ws else source_dirs
        if not anchors:
            return []

        findings: list[Finding] = []
        targets: list[str] = []

        # Extract cd targets
        for m in self._CD_RE.finditer(command):
            targets.append(m.group(1).strip("'\""))

        # Extract pnpm --filter targets (resolve ./path form)
        for m in self._PNPM_FILTER_RE.finditer(command):
            filt = m.group(1).strip("'\"")
            if filt.startswith("./"):
                filt = filt[2:]
            # pnpm filter can be a package name or a path — only validate
            # path-like filters (contain / or .)
            if "/" in filt or filt.startswith("."):
                targets.append(filt)

        for target in targets:
            target_norm = target.rstrip("/")
            # Ancestor match (target is a PARENT of an anchor) is only valid for
            # source_dirs (e.g. `cd apps/api` ⊇ source `apps/api/src`). For
            # workspace anchors it would wrongly pass `cd packages` against
            # workspace `packages/shared` — a non-workspace dir spanning multiple
            # workspaces, exactly the cross-workspace command M1 must catch.
            if not self._is_scope_consistent(
                target_norm, anchors, allow_ancestor=not anchor_ws,
            ):
                if anchor_ws:
                    msg = (
                        f"Command targets '{target_norm}', which is not a "
                        f"declared workspace (workspaces: {anchors}). The "
                        f"command should target a real workspace directory."
                    )
                else:
                    msg = (
                        f"Command targets '{target_norm}' but source_dirs "
                        f"are {source_dirs}. The command should target a "
                        f"directory that contains at least one source_dir."
                    )
                findings.append(Finding(
                    severity="error",
                    code="monorepo-scope-mismatch",
                    message=msg,
                    path="",
                    line=0,
                ))
        return findings

    @staticmethod
    def _is_scope_consistent(
        target: str, anchors: list[str], *, allow_ancestor: bool = True,
    ) -> bool:
        """True if ``target`` is consistent with any anchor.

        ``target`` is consistent when it equals an anchor or is a child of one.
        When ``allow_ancestor`` is True (source_dirs fallback), a target that is
        an ANCESTOR of an anchor also counts (e.g. ``apps/api`` ⊇ source
        ``apps/api/src``). For workspace anchors pass ``allow_ancestor=False``:
        the target must BE a workspace or live under one — a parent dir spanning
        multiple workspaces is not consistent.
        """
        for a in anchors:
            a_norm = a.rstrip("/")
            # target is the anchor itself or a child of it
            if target == a_norm or target.startswith(a_norm + "/"):
                return True
            # target is an ancestor of the anchor (source_dirs only)
            if allow_ancestor and a_norm.startswith(target + "/"):
                return True
        return False

    # ---- G2: test import parsing -----------------------------------------

    # ES import: import { A, B } from 'specifier'
    # Also handles: import A from 'specifier', import * as A from 'specifier'
    _IMPORT_RE = re.compile(
        r"""import\s+"""
        r"""(?:"""
        r"""  \{([^}]*)\}"""             # named: import { A, B } from '...'
        r"""| (\w+)"""                   # default: import A from '...'
        r"""| \*\s+as\s+(\w+)"""        # namespace: import * as A from '...'
        r""")"""
        r"""\s+from\s+['"]([^'"]+)['"]""",
        re.VERBOSE,
    )

    # require: const { A, B } = require('specifier') or const A = require('specifier')
    _REQUIRE_RE = re.compile(
        r"""(?:const|let|var)\s+"""
        r"""(?:"""
        r"""  \{([^}]*)\}"""             # destructured: const { A, B } = require('...')
        r"""| (\w+)"""                   # simple: const A = require('...')
        r""")"""
        r"""\s*=\s*require\s*\(\s*['"]([^'"]+)['"]""",
        re.VERBOSE,
    )

    def parse_test_imports(self, file_path: Path) -> list[ImportInfo]:
        """Parse ES import and CJS require statements from a TS/JS file."""
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []

        imports: list[ImportInfo] = []
        for line_num, line in enumerate(content.splitlines(), start=1):
            stripped = line.strip()
            if not stripped or stripped.startswith("//"):
                continue

            # ES imports
            m = self._IMPORT_RE.search(line)
            if m:
                named, default, namespace, specifier = m.groups()
                symbols: list[str] = []
                if named:
                    symbols = [s.strip().split(" as ")[0].strip()
                               for s in named.split(",") if s.strip()]
                elif default:
                    symbols = [default]
                elif namespace:
                    symbols = [f"* as {namespace}"]
                imports.append(ImportInfo(
                    specifier=specifier,
                    symbols=symbols,
                    file_path=str(file_path),
                    line=line_num,
                ))
                continue

            # CJS require
            m = self._REQUIRE_RE.search(line)
            if m:
                destructured, simple, specifier = m.groups()
                symbols = []
                if destructured:
                    symbols = [s.strip().split(":")[0].strip()
                               for s in destructured.split(",") if s.strip()]
                elif simple:
                    symbols = [simple]
                imports.append(ImportInfo(
                    specifier=specifier,
                    symbols=symbols,
                    file_path=str(file_path),
                    line=line_num,
                ))

        return imports


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
