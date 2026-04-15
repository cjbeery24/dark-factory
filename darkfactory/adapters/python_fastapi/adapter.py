"""``PythonFastApiAdapter`` — concrete ``EcosystemAdapter`` for Python
FastAPI projects.

Scope, as of Sprint 6 #2 (stub):

- **Detection** is a deterministic filesystem probe. Signals:
  ``pyproject.toml``, ``requirements.txt``, or ``setup.py`` at the
  project root, AND a FastAPI mention in that file. The FastAPI
  requirement is deliberate — a plain Python project without FastAPI
  is a different shape (CLI, library, Django, data-science); this
  adapter claims only the FastAPI slice so a future
  ``python-django`` / ``python-library`` adapter can coexist without
  detection conflict.
- **Precheck** returns ``[]``. This is the honest stub: Python
  precheck work (import validation, Pydantic stub round-trip) is
  follow-up. Returning ``[]`` is meaningfully better than today's
  pre-adapter failure mode, where a Python project entered the TS
  precheck and emitted ``ts-undeclared-import`` findings that had
  no bearing on Python code.
- **Consistency rules, quality gate, proposal rehearsal:** all return
  empty / None. ``quality_gate_dispatch`` still reads ``commands.*``
  from the architect manifest — the architect is what knows Python
  command shapes via the addendum; the adapter just threads the
  manifest value through.

**Why an adapter at all, if it's this minimal?** The pipeline core's
A5 fail-fast gate aborts a project with no matching adapter. That is
the correct default — a Python project running through the TypeScript
adapter produces misleading output (success criterion #7 in the plan).
This adapter is the minimal thing that lets the pipeline run over a
Python project without pretending to be TypeScript.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

from darkfactory.adapters.base import (
    EcosystemAdapter,
    Finding,
    GateCommands,
    op_str,
)
from darkfactory.architecture.consistency import ConsistencyRule
from darkfactory.spec.manifest import ArchitectureManifest
from darkfactory.spec.spec_model import SpecModel

log = logging.getLogger(__name__)


# The addendum file is part of the packaged wheel; missing it is a
# packaging bug. Cached on first read; lazily so imports stay cheap.
_ADDENDUM_PATH = Path(__file__).with_name("architect_addendum.md")
_addendum_cache: str | None = None


def _load_addendum() -> str:
    global _addendum_cache
    if _addendum_cache is None:
        try:
            _addendum_cache = _ADDENDUM_PATH.read_text(encoding="utf-8")
        except OSError as exc:
            raise RuntimeError(
                f"PythonFastApiAdapter addendum missing at {_ADDENDUM_PATH}"
            ) from exc
    return _addendum_cache


# Pattern matches `fastapi` as a package name on a requirement line.
# Deliberately narrow — we want to accept ``fastapi``,
# ``fastapi>=0.100``, ``fastapi[all]``, and ``"fastapi"`` (pyproject
# dependency array), but reject ``not-fastapi`` / ``my-fastapi-plugin``
# (hyphenated names). The regex requires a dependency-delimiter
# character (start of line, whitespace, quote, comma, or bracket)
# before ``fastapi`` and a dependency-shape character (EOL, version
# specifier, extras bracket, whitespace, quote, or comma) after it.
#
# Comments (``# ...`` lines) are stripped *before* matching in
# ``_has_fastapi_mention`` rather than encoded here — comment syntax
# differs across formats (shell-style ``#`` in requirements.txt and
# pyproject.toml; none in setup.py Python strings), and stripping is
# cleaner than a single regex trying to express all three.
_FASTAPI_MENTION = re.compile(
    r"(?im)(?:^|[\s,'\"\[])fastapi(?:$|[\s=<>!~\[\],'\"])"
)


_INLINE_COMMENT = re.compile(r"(?:^|\s)#.*$")


def _has_fastapi_mention(text: str) -> bool:
    """True iff ``text`` references the ``fastapi`` package outside of
    comments.

    Strips both full-line comments (``#`` as the first non-whitespace
    character) and inline comments (``#`` preceded by whitespace or
    start-of-line) before matching. Works for requirements.txt and
    pyproject.toml, which share ``#`` comment syntax. setup.py is
    Python source — string literals with ``"fastapi"`` + a later ``#``
    are stripped after the string, so the mention survives; a ``#
    fastapi`` comment is correctly dropped.

    Caveat: a ``#`` inside a TOML string literal (``name = "has # in
    it"``) would be mangled by this pass. That case doesn't appear
    in FastAPI-mention grammar (package names don't contain ``#``),
    so the false-negative risk is zero for our detection purpose.
    """
    stripped_lines: list[str] = []
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("#"):
            continue
        # Drop trailing inline comments too — a Django project with
        # ``requests # installs fastapi as transitive`` must not trip
        # detection.
        stripped_lines.append(_INLINE_COMMENT.sub("", line))
    body = "\n".join(stripped_lines)
    return bool(_FASTAPI_MENTION.search(body))


@dataclass
class PythonFastApiAdapter(EcosystemAdapter):
    """The Python + FastAPI adapter.

    Construction mirrors the other adapters:

        adapter = PythonFastApiAdapter()                 # contract-test
        adapter = PythonFastApiAdapter(project_dir=...)  # production

    ``project_dir`` is optional so the S0.2 contract harness can
    instantiate without a real tree. Production (A5 registry) always
    passes one.
    """

    # Dataclass field defaults shadow the abstract ``name`` /
    # ``detection_signals`` properties on the ABC at class-definition
    # time (documented pattern in ``darkfactory/adapters/base.py``).
    name: str = "python-fastapi"
    detection_signals: tuple[str, ...] = (
        "pyproject.toml",
        "requirements.txt",
        "setup.py",
    )
    project_dir: Path | None = None
    # Mutable container for project-hints-supplied rules. Mirrors the
    # TS/Postgres pattern so Sprint 3-style rule injection works the
    # same way here.
    extra_consistency_rules: list[ConsistencyRule] = field(default_factory=list)

    # ---- ABC: detection -------------------------------------------------

    def detect(self, project_dir: Path) -> bool:
        """True iff a Python manifest at the root mentions FastAPI.

        The manifest-mention requirement is deliberate. A generic
        ``*.py`` file or an empty ``pyproject.toml`` could belong to
        any Python flavor; activating on those would collide with a
        future ``python-django`` or ``python-library`` adapter.
        Requiring a FastAPI mention gives this adapter a precise
        scope.

        Returns ``False`` on any OS error (unreadable file, broken
        symlink). Detection must never raise into the registry loop —
        failing closed is the right default.
        """
        if not project_dir.is_dir():
            return False

        for signal in self.detection_signals:
            candidate = project_dir / signal
            if not candidate.is_file():
                continue
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                log.debug(
                    "python-fastapi: failed to read %s (%s); skipping",
                    candidate,
                    exc,
                )
                continue
            if _has_fastapi_mention(text):
                return True
        return False

    # ---- ABC: precheck --------------------------------------------------

    def precheck(self, spec: SpecModel, project_dir: Path) -> list[Finding]:
        """Stub precheck — returns no findings.

        The point of the stub is to not lie. A Python-specific
        precheck is follow-up; claiming to run one and silently
        passing everything would mask real bugs.

        ``spec`` and ``project_dir`` are accepted for signature
        symmetry with the other adapters so a future implementation
        doesn't break callers. Explicit ``del`` names them as
        intentionally unused so linters and future refactors don't
        silently drop parameters.
        """
        del spec, project_dir
        return []

    # ---- ABC: consistency rules ----------------------------------------

    def consistency_rules(self) -> list[ConsistencyRule]:
        """No baseline rules; project-hints-supplied rules pass through.

        Adapter-baseline rules should be motivated by a real
        post-mortem (a failure class seen across multiple Python
        FastAPI projects). Shipping speculative rules at stub time
        wastes the audit budget when they misfire.
        """
        return list(self.extra_consistency_rules)

    # ---- ABC: architect prompt addendum --------------------------------

    def architect_addendum(self) -> str:
        """Return the markdown addendum for Python + FastAPI guidance."""
        return _load_addendum()

    # ---- ABC: quality-gate dispatch ------------------------------------

    def quality_gate_dispatch(
        self, manifest: ArchitectureManifest
    ) -> GateCommands:
        """Thread ``commands.*`` from the architect manifest into GateCommands.

        Identical pattern to the TypeScript adapter. The adapter does
        not know Python command shapes directly — the architect does
        (via the addendum) and emits them into ``runtime.amendment_ops``.
        The adapter just unpacks them.

        Empty strings for phases the architect did not declare — the
        documented "skip phase" sentinel pinned by
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
        """No Python proposal-rehearsal typecheck yet.

        ``None`` is the documented opt-out signal — distinct from
        "rehearsal ran and found nothing" (empty list). mypy/pyright
        on untyped projects would produce too much noise to be useful
        without project-specific configuration; the right rehearsal
        shape for Python is deferred.
        """
        del scratch_dir
        return None
