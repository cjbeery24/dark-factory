"""In-container entry script for substrate verification.

Invoked inside an ephemeral verifier container that has the host
``darkfactory/`` package mounted read-only at
``/opt/dark-factory/darkfactory`` and ``PYTHONPATH=/opt/dark-factory``.
The verifier is a *peer* to the gate container, not inside it — the
gate container deliberately does not mount the orchestration package
(see ``build_darkfactory_mount`` for the rationale).

Usage::

    python3 -m darkfactory.pipeline.substrate_verifier_entry \\
        --adapter postgres \\
        --manifest /manifest.toml \\
        --spec /spec.md

Reads the sidecar URL from ``DATABASE_URL``. Emits exactly one JSON
object on real stdout::

    {"status": "skip" | "pass" | "fail", "findings": [...]}

**stdout discipline.** Adapter imports and method calls may write to
stdout (logging, deprecation warnings, libpq C-level diagnostics).
We grab a duplicate of stdout *before any darkfactory import*, then
redirect ``sys.stdout`` to ``sys.stderr`` so the only thing that
reaches real stdout is the envelope ``_emit`` writes. The dispatcher
on the orchestrator side parses the entire stdout blob — no
last-line heuristic — which is only safe because of this redirect.

Exit code is always 0 on a normal run (success or adapter-reported
fail). A non-zero exit code means the entry script itself crashed
(import failure, OOM, SIGKILL, timeout). The dispatcher synthesises
a ``substrate-verifier-crashed`` finding in that case.
"""
from __future__ import annotations

# CRITICAL: capture stdout BEFORE importing anything that might write to it.
# Adapter modules can pull in C extensions (psycopg) or heavyweight
# packages whose import-time side effects include stdout writes.
import sys as _sys
_REAL_STDOUT = _sys.stdout
_sys.stdout = _sys.stderr

import argparse  # noqa: E402
import dataclasses  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import sys  # noqa: E402
import traceback  # noqa: E402
from pathlib import Path  # noqa: E402

from darkfactory.adapters import get_adapter_class  # noqa: E402
from darkfactory.adapters.base import Finding  # noqa: E402


class _ForbiddenProjectDir:
    """Sentinel passed to adapters in the verifier container.

    The verifier has no project bind mount — it is an isolated peer
    of the gate. Any adapter method that reads ``self.project_dir``
    is calling out for a directory that doesn't exist in this
    container, and the only safe answer is to fail loudly with a
    message that names the actual problem instead of bottoming out
    in a generic ``TypeError: unsupported operand type(s) for /:
    'NoneType' and 'str'`` deep inside ``verify_substrate``.

    ``verify_substrate`` is documented (ABC docstring at
    ``darkfactory/adapters/base.py``) to take everything it needs
    through method arguments. If a future adapter needs project
    files, the right answer is to add an argument to the ABC
    method, not to bypass this sentinel.
    """

    _MSG = (
        "verify_substrate accessed self.project_dir, but the verifier "
        "container has no project bind mount. Pass needed paths through "
        "the verify_substrate arguments (or extend the ABC) instead."
    )

    def __getattr__(self, name: str) -> object:
        raise RuntimeError(self._MSG)

    def __truediv__(self, other: object) -> object:
        raise RuntimeError(self._MSG)

    def __fspath__(self) -> str:
        raise RuntimeError(self._MSG)

    def __str__(self) -> str:
        raise RuntimeError(self._MSG)

    def __repr__(self) -> str:
        return "<ForbiddenProjectDir: verifier container has no project mount>"


def _emit(status: str, findings: list[Finding] | list[dict]) -> None:
    """Write the result envelope to real stdout. Always succeeds."""
    payload = {
        "status": status,
        "findings": [
            dataclasses.asdict(f) if isinstance(f, Finding) else f
            for f in findings
        ],
    }
    json.dump(payload, _REAL_STDOUT)
    _REAL_STDOUT.write("\n")
    _REAL_STDOUT.flush()


def _status_for(result) -> str:
    """Map a ``verify_substrate`` result to the envelope status.

    - ``None`` → ``"skip"`` (no verification ran).
    - any ERROR-severity finding → ``"fail"``.
    - otherwise → ``"pass"`` — including an empty list AND a list that
      carries only non-error (info/warning) findings.

    The non-error case is the bug this guards against: the verifier used
    to emit ``"fail"`` for ANY non-empty findings list, ignoring severity,
    so the first info/warning finding ever added would spuriously fail the
    gate. Fail-closed default: an item whose ``severity`` can't be read is
    treated as an error.
    """
    if result is None:
        return "skip"
    if any(getattr(f, "severity", "error") == "error" for f in result):
        return "fail"
    return "pass"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="substrate-verifier")
    parser.add_argument("--adapter", required=True,
                        help="adapter name (e.g. 'postgres')")
    parser.add_argument("--manifest", required=True, type=Path,
                        help="path to substrate manifest TOML inside container")
    parser.add_argument("--spec", required=True, type=Path,
                        help="path to spec markdown inside container")
    args = parser.parse_args(argv)

    sidecar_url = os.environ.get("DATABASE_URL", "")

    try:
        adapter_cls = get_adapter_class(args.adapter)
        # See _ForbiddenProjectDir: the verifier has no project mount,
        # so any adapter method that needs project files must take them
        # through arguments. The sentinel turns the "adapter forgot the
        # rule" failure into a loud, specific runtime error.
        adapter = adapter_cls(project_dir=_ForbiddenProjectDir())  # type: ignore[arg-type]
        spec_text = args.spec.read_text(encoding="utf-8") if args.spec.exists() else ""
        result = adapter.verify_substrate(
            sidecar_url=sidecar_url,
            manifest_path=args.manifest,
            spec_text=spec_text,
        )
    except Exception as exc:  # noqa: BLE001
        # Adapter raised — convert to a fail envelope so the dispatcher
        # gets actionable diagnostics instead of just `exit_code != 0`.
        # We include the traceback in the message because the verifier
        # container is ephemeral; once it exits, the only record is the
        # envelope.
        tb = traceback.format_exc(limit=10)
        _emit("fail", [{
            "severity": "error",
            "code": "substrate-verifier-adapter-raised",
            "message": (
                f"adapter {args.adapter!r} verify_substrate raised: "
                f"{type(exc).__name__}: {exc}\n{tb}"
            ),
            "path": str(args.manifest),
        }])
        return 0

    _emit(_status_for(result), [] if not result else result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
