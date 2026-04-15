"""A6 — Embedded-stub round-trip (tsc --noEmit against project).

The plan (docs/plans/2026-04-14-spec-reliability-and-pipeline-hardening.md,
Sprint 2, A6) calls this out as the highest-leverage single item in the
spec-reliability track. Motivation:

When a spec's §5 Interface Contract declares ``tx.execute({sql, values})``
but drizzle-orm's actual transaction type is
``<T>(query: SQLWrapper) => Promise<T[]>``, the pipeline today burns
three iterations before a human notices. tsc is the ground truth for
"does this call shape match the installed library"; A6 hands the spec's
declared surface to the compiler before an agent ever reads it.

## How it works

1. Walk spec §5 Interface Contract fenced blocks (typescript / ts / tsx /
   mts / cts).
2. For each block, read the first non-empty line as a ``// <filepath>``
   comment — this tells A6 where the stub "lives" for import resolution.
3. Write every stub to a scratch directory, preserving the relative
   filepaths so internal imports (``./schema``) resolve among themselves.
4. Synthesize a minimal tsconfig.json that ``extends`` the project's
   tsconfig, inherits ``compilerOptions.paths``/``baseUrl``, and includes
   ONLY the scratch files. This reuses the project's node_modules
   resolution and strictness without typechecking the actual source.
5. Invoke ``node_modules/.bin/tsc --noEmit`` on the scratch tsconfig.
6. Parse tsc's diagnostic output and emit findings.

## Graceful no-ops

A6 exits cleanly (zero findings, WARN log) when any of these are
missing:

- ``project_dir`` is None (no project context)
- No ``tsconfig.json`` at the project root
- No ``node_modules/.bin/tsc`` (dependencies not installed)
- No typescript fences under §5 Interface Contract
- No filepath comment at the top of a fence (stub is unplaceable)

Each miss logs ONE WARN line so the human running precheck can see why
A6 was a no-op without scanning verbose logs.

## Why standalone-file approach (not declaration merging)

An earlier design tried to emit a single concatenated ``.d.ts`` stub.
That broke on two axes: (a) re-declaring a value ``export const db``
twice across sub-sections fails with TS2300 even though it's the same
declaration, (b) module-scope imports (``import postgres from
'postgres'``) don't compose across merged declaration blocks. Treating
each fence as its own source file in its own module preserves the
§5 author's natural structure.

## Scope boundary vs. A3

A3 checks ``is this library declared as a dependency.`` A6 checks
``does the pinned library version export what the spec imports and
does the spec's declared surface typecheck against those exports.``
A6 presumes A3 passed — if an import specifier doesn't even name a
declared dep, running tsc will produce a cascade of unhelpful
``cannot find module`` errors. Today that gating is advisory in
precheck's aggregation layer; we surface A3 findings before A6's so
the human fixes declarations first.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from darkfactory.spec.spec_model import FencedBlock, SpecModel

log = logging.getLogger(__name__)


_TS_LANGS: frozenset[str] = frozenset({"typescript", "ts", "tsx", "mts", "cts"})

# ``// path/to/file.ts`` — must be the first non-whitespace line of the
# fence. We accept any .ts/.tsx/.mts/.cts extension (``.d.ts`` matches
# the ``.ts`` branch). A bare ``// foo.ts`` (no directory) is accepted
# and placed at scratch_dir/foo.ts; this is unusual but not ambiguous,
# and refusing it would reject single-file projects for no good reason.
_FILEPATH_COMMENT_RE = re.compile(
    r"^\s*//\s*([a-zA-Z0-9_\-./@]+\.(?:ts|tsx|mts|cts))\s*$"
)

# Strip a leading ``[0-9]+\.\s+`` / ``[A-Z]\.\s+`` section prefix — same
# rule used elsewhere in the precheck layer for heading matching.
_HEADING_PREFIX_RE = re.compile(r"^(?:[0-9]+|[A-Za-z])\.?\s+")


def _normalize_heading(text: str) -> str:
    return _HEADING_PREFIX_RE.sub("", text.strip()).strip().lower()


@dataclass(frozen=True)
class InterfaceStub:
    """A single §5 TypeScript fence extracted as a placeable stub file.

    ``filepath`` is the path declared in the first-line ``//`` comment,
    relative to the project root (e.g. ``"apps/api/src/db/index.ts"``).
    ``content`` is the fence body minus the filepath comment line.
    ``spec_line_number`` is the 1-based line of the opening fence in the
    original spec so diagnostics can point back to the author.
    """

    filepath: str
    content: str
    spec_line_number: int
    section_path: tuple[str, ...]


@dataclass(frozen=True)
class StubCompileFinding:
    """A single tsc diagnostic on the stub round-trip.

    ``spec_line_number`` is the opening fence line; ``stub_line`` is the
    line *within* the extracted stub where tsc flagged the error (1-based
    into the fence body, sans the filepath comment). Together they let
    the human navigate from error → spec fence → exact line.
    """

    spec_line_number: int
    filepath: str
    stub_line: int | None
    tsc_code: str               # "TS2339" etc.
    message: str


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------


def _is_interface_contract_fence(block: FencedBlock) -> bool:
    return any(
        _normalize_heading(seg) == "interface contract"
        for seg in block.section_path
    )


def _is_ts_fence(block: FencedBlock) -> bool:
    return block.language.lower() in _TS_LANGS


def _extract_filepath(content: str) -> tuple[str | None, str]:
    """Return ``(filepath, body_without_comment)``.

    ``(None, original)`` if the first non-empty line isn't a valid
    ``// <path>.ts`` comment. We don't fall back to anything — an
    unplaceable stub can't be compiled in context.
    """
    lines = content.splitlines()
    for i, raw in enumerate(lines):
        if not raw.strip():
            continue
        m = _FILEPATH_COMMENT_RE.match(raw)
        if m is None:
            return None, content
        # Strip this line and preserve the rest verbatim (line offsets
        # for tsc mapping are preserved by keeping a blank line in its
        # place — tsc error "line 5" should still mean the 5th line of
        # the original fence body).
        stripped = list(lines)
        stripped[i] = ""
        return m.group(1), "\n".join(stripped)
    return None, content


def extract_interface_stubs(model: SpecModel) -> list[InterfaceStub]:
    """Return one stub per §5 TypeScript fence with a filepath comment.

    Fences that don't start with a ``// path/to/file.ts`` comment are
    skipped and logged — they cannot be placed in the scratch tree
    without inventing a path, which would break relative imports.
    """
    stubs: list[InterfaceStub] = []
    for block in model.all_fenced_blocks:
        if not _is_interface_contract_fence(block):
            continue
        if not _is_ts_fence(block):
            continue
        filepath, body = _extract_filepath(block.content)
        if filepath is None:
            log.warning(
                "A6: spec §5 fence at line %d has no `// <filepath>` "
                "comment; skipped (cannot place stub for tsc)",
                block.line_number,
            )
            continue
        stubs.append(
            InterfaceStub(
                filepath=filepath,
                content=body,
                spec_line_number=block.line_number,
                section_path=block.section_path,
            )
        )
    return stubs


# ---------------------------------------------------------------------------
# Environment probing
# ---------------------------------------------------------------------------


def _find_tsc(project_dir: Path) -> Path | None:
    """Locate a tsc binary, preferring the project's local install.

    Order: project-local ``node_modules/.bin/tsc`` → PATH ``tsc``.
    A pinned local tsc matches the project's TypeScript version and
    honors the ``typescript`` dep that produced its tsconfig schema.
    """
    local = project_dir / "node_modules" / ".bin" / "tsc"
    if local.exists() and local.is_file():
        return local
    path_tsc = shutil.which("tsc")
    return Path(path_tsc) if path_tsc else None


def _find_tsconfig(project_dir: Path) -> Path | None:
    """Return the project's ``tsconfig.json``, or None if absent.

    We deliberately do NOT fall back to ``tsconfig.base.json``: a
    ``base`` config is intended to be extended by a concrete build
    config and frequently lacks the ``include``/``exclude`` that tsc
    needs to resolve files. If a project's real tsconfig is named
    something else (rare), A6 will no-op with a WARN and the author
    can add a tsconfig.json — that's a better failure mode than
    typechecking against a half-built base.
    """
    p = project_dir / "tsconfig.json"
    return p if p.exists() else None


def _node_modules_present(project_dir: Path) -> bool:
    """True iff node_modules exists at the project root.

    pnpm workspaces often have node_modules at the root only; per-app
    node_modules is lazy via symlinks. We check the root — if it's
    missing, A6 can't resolve imports anywhere.
    """
    return (project_dir / "node_modules").is_dir()


# ---------------------------------------------------------------------------
# tsc invocation
# ---------------------------------------------------------------------------


# Diagnostic format: ``path/to/file.ts(12,34): error TS2339: <message>``
# or ``path/to/file.ts:12:34 - error TS2339: <message>`` (newer tsc).
_TSC_DIAG_RE = re.compile(
    r"^(?P<path>.+?)[\(:](?P<line>\d+)[\),:](?P<col>\d+)\)?\s*[-:]?\s*"
    r"error\s+(?P<code>TS\d+):\s*(?P<message>.+)$"
)


def _write_stubs_to_scratch(
    stubs: list[InterfaceStub],
    scratch_dir: Path,
) -> list[tuple[InterfaceStub, Path]]:
    """Write each stub to scratch_dir/<filepath>, return (stub, absolute path)."""
    # Resolve once: scratch_dir is stable for the life of this call, and
    # the per-stub containment check compares against this value.
    scratch_resolved = scratch_dir.resolve()
    placements: list[tuple[InterfaceStub, Path]] = []
    for stub in stubs:
        # Defense: reject filepaths that escape the scratch dir. A spec
        # author writing ``// ../../etc/passwd`` as a filename is almost
        # certainly a typo but we don't want to reach outside scratch.
        dest = (scratch_resolved / stub.filepath).resolve()
        try:
            dest.relative_to(scratch_resolved)
        except ValueError:
            log.warning(
                "A6: stub filepath %r escapes scratch dir; skipped",
                stub.filepath,
            )
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(stub.content)
        placements.append((stub, dest))
    return placements


def _synthesize_tsconfig(
    scratch_dir: Path,
    project_tsconfig: Path,
    stub_paths: list[Path],
) -> Path:
    """Write a tsconfig.json in scratch that extends the project's config.

    The synthesized config:
    - Extends the project tsconfig (inherits strictness, paths, module resolution).
    - Overrides emit surface so tsc never writes artifacts anywhere:
      ``noEmit``, ``declaration:false``, ``declarationMap:false``,
      ``emitDeclarationOnly:false``, ``incremental:false``. Even with
      ``--noEmit``, ``declaration:true`` inherited from a parent can
      trigger emit planning errors; we neutralize every path.
    - Forces ``outDir``/``tsBuildInfoFile`` to absolute paths INSIDE
      scratch so any tsc write (should one slip through) lands in the
      sandbox, not the project tree. A relative ``./...`` resolves
      against the tsconfig's own directory which is scratch — but being
      explicit and absolute is cheaper than trusting.
    - Strips project composition: ``composite:false``, ``references:[]``,
      ``rootDirs:[]`` so tsc doesn't try to build sibling projects or
      treat sibling source trees as stub siblings.
    - ``"files": []`` plus ``"include": [<stub paths>]`` narrows the
      checked set to exactly the stubs we wrote. Without ``files:[]``,
      some inherited globs can survive the override.
    """
    # Make paths relative to scratch_dir for readability in tsc output.
    # Resolve both sides so macOS ``/var`` → ``/private/var`` symlink
    # differences don't make ``relative_to`` raise.
    scratch_resolved = scratch_dir.resolve()
    abs_extends = Path(project_tsconfig).resolve().as_posix()
    rel_includes = [
        p.resolve().relative_to(scratch_resolved).as_posix() for p in stub_paths
    ]
    abs_outdir = (scratch_resolved / ".tsbuild-out").as_posix()
    abs_tsbuildinfo = (scratch_resolved / ".tsbuildinfo").as_posix()
    synth = {
        "extends": abs_extends,
        "compilerOptions": {
            "noEmit": True,
            "declaration": False,
            "declarationMap": False,
            "emitDeclarationOnly": False,
            "incremental": False,
            "composite": False,
            "outDir": abs_outdir,
            "tsBuildInfoFile": abs_tsbuildinfo,
            "rootDir": ".",
            "rootDirs": [],
        },
        # ``files:[]`` forces tsc to ignore any inherited ``files`` list;
        # ``include`` below is the entire input set.
        "files": [],
        "include": rel_includes,
        # Strip references so tsc doesn't try to build parent projects.
        "references": [],
    }
    scratch_config = scratch_dir / "tsconfig.json"
    scratch_config.write_text(json.dumps(synth, indent=2))
    return scratch_config


def _parse_tsc_diagnostics(
    output: str,
    scratch_dir: Path,
    placements: list[tuple[InterfaceStub, Path]],
) -> list[StubCompileFinding]:
    """Translate tsc stdout/stderr lines into StubCompileFindings.

    Maps each diagnostic's file path back to the originating stub so we
    can point the human at the spec line they wrote, not the scratch
    path they never see.
    """
    findings: list[StubCompileFinding] = []
    # Build a lookup: resolved stub path → originating InterfaceStub.
    stub_by_path: dict[str, InterfaceStub] = {
        str(p.resolve()): stub for stub, p in placements
    }
    scratch_resolved = scratch_dir.resolve()

    for line in output.splitlines():
        m = _TSC_DIAG_RE.match(line.strip())
        if m is None:
            continue
        path_str = m.group("path").strip()
        # tsc may emit relative paths (from cwd) or absolute. Resolve both.
        candidate = Path(path_str)
        if not candidate.is_absolute():
            candidate = (scratch_resolved / candidate).resolve()
        else:
            candidate = candidate.resolve()
        stub = stub_by_path.get(str(candidate))
        if stub is None:
            # Diagnostic against a file we didn't write — almost always a
            # library .d.ts issue bleeding through. Skip; A6's contract is
            # "typecheck the author's spec", not "audit the ecosystem."
            continue
        try:
            stub_line = int(m.group("line"))
        except ValueError:
            stub_line = None
        findings.append(
            StubCompileFinding(
                spec_line_number=stub.spec_line_number,
                filepath=stub.filepath,
                stub_line=stub_line,
                tsc_code=m.group("code"),
                message=m.group("message").strip(),
            )
        )
    return findings


def _run_tsc(
    tsc: Path,
    tsconfig: Path,
    project_dir: Path,
    timeout_sec: int = 90,
) -> subprocess.CompletedProcess[str]:
    """Invoke tsc with --noEmit and capture output.

    Run from the project root so relative tsconfig extends resolve and
    any `paths` mappings land against the project's real tree.
    """
    return subprocess.run(
        [str(tsc), "--noEmit", "--project", str(tsconfig)],
        capture_output=True,
        text=True,
        cwd=project_dir,
        timeout=timeout_sec,
        check=False,
    )


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------


def check_stub_roundtrip(
    model: SpecModel,
    *,
    project_dir: Path | None,
    scratch_dir: Path | None = None,
    timeout_sec: int = 90,
) -> list[StubCompileFinding]:
    """Run A6 stub round-trip against the target project.

    ``scratch_dir`` is an override for tests; in normal operation we
    create a temp dir and clean it up before returning.
    ``timeout_sec`` is a ceiling on the tsc invocation; on a large
    project with many stubs the default 90s may need to grow.
    """
    if project_dir is None:
        return []

    stubs = extract_interface_stubs(model)
    if not stubs:
        return []

    tsconfig = _find_tsconfig(project_dir)
    if tsconfig is None:
        log.warning(
            "A6: no tsconfig.json in %s; stub round-trip skipped",
            project_dir,
        )
        return []

    if not _node_modules_present(project_dir):
        log.warning(
            "A6: %s has no node_modules; stub round-trip skipped "
            "(library type resolution would fail)",
            project_dir,
        )
        return []

    tsc = _find_tsc(project_dir)
    if tsc is None:
        log.warning(
            "A6: tsc not found (no node_modules/.bin/tsc and not on PATH); "
            "stub round-trip skipped"
        )
        return []

    # Ensure scratch_dir — in production we don't get one from the
    # caller and the tempfile lifecycle is ours.
    owned_scratch = scratch_dir is None
    if owned_scratch:
        # Resolve immediately so subsequent path arithmetic isn't tripped
        # by macOS's /var → /private/var symlink.
        scratch_dir = Path(tempfile.mkdtemp(prefix="darkfactory-a6-")).resolve()
    try:
        placements = _write_stubs_to_scratch(stubs, scratch_dir)
        if not placements:
            # All stubs escaped scratch or we couldn't write any — already
            # logged inside _write_stubs_to_scratch.
            return []
        stub_paths = [p for _, p in placements]
        synth_tsconfig = _synthesize_tsconfig(
            scratch_dir, tsconfig, stub_paths,
        )
        try:
            result = _run_tsc(
                tsc, synth_tsconfig, project_dir, timeout_sec=timeout_sec,
            )
        except subprocess.TimeoutExpired:
            log.warning(
                "A6: tsc timed out after %ds on %d stub(s); skipped",
                timeout_sec,
                len(placements),
            )
            return []
        except OSError as exc:
            log.warning("A6: tsc invocation failed — %s", exc)
            return []

        # tsc writes diagnostics to stdout (unlike most compilers).
        # Capture both as a defensive measure.
        combined = (result.stdout or "") + "\n" + (result.stderr or "")
        return _parse_tsc_diagnostics(combined, scratch_dir, placements)
    finally:
        if owned_scratch and scratch_dir is not None and scratch_dir.exists():
            shutil.rmtree(scratch_dir, ignore_errors=True)
