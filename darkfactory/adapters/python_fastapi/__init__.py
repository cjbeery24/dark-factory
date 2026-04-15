"""Python + FastAPI ecosystem adapter (Sprint 6 #2 stub).

**Status:** intentional stub. Shipped as part of Sprint 6's
"validation" slice to prove the adapter extension point works
end-to-end without pretending the framework has real Python support.

Public surface:

- ``PythonFastApiAdapter`` — implements ``EcosystemAdapter``. Active
  when Python + FastAPI signals are present (``pyproject.toml``,
  ``requirements.txt``, or ``setup.py`` AND a FastAPI mention in the
  dependency manifest).

**What this stub does:**

- Detects Python/FastAPI projects deterministically (no LLM).
- Contributes a minimal architect-prompt addendum (≤80 non-blank
  lines per governance) so the architect emits Python idioms, not
  Node/pnpm.
- Satisfies the ``EcosystemAdapter`` ABC so the pipeline does not
  abort at A5's fail-fast gate.

**What this stub deliberately does not do:**

- Python-specific precheck (import validation against declared
  dependencies, Pydantic model round-trip). ``precheck`` returns
  an empty list; the audit trail records "python-fastapi precheck
  ran, 0 findings." That is honestly better than today's failure
  mode — a TypeScript-style precheck triggering on a Python spec
  and emitting meaningless ``ts-undeclared-import`` errors.
- Baseline consistency rules. The adapter returns ``[]``; project-
  specific rules belong in ``[[policy.consistency_rules]]``.
- Proposal-rehearsal typecheck. Returns ``None`` (opt-out).

Promoting this to a supported adapter per the governance tiers
(``docs/ADAPTER-GOVERNANCE.md``) requires at minimum: an end-to-end
pipeline run on a real Python FastAPI spec, at least one consistency
rule motivated by a real failure, and filling in ``precheck`` with
something that justifies the adapter's existence.
"""
from darkfactory.adapters.python_fastapi.adapter import PythonFastApiAdapter

__all__ = ["PythonFastApiAdapter"]
