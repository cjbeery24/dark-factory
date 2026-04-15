"""Per-ecosystem adapters.

The pipeline core dispatches ecosystem-specific behavior — TypeScript
build commands, Postgres SQL linting, pnpm dependency installation,
etc. — through ``EcosystemAdapter`` implementations defined under this
package. The core knows nothing about any specific ecosystem; that
knowledge lives behind the ABC at ``base.py``.

**Registry (A5):** ``REGISTRY`` is the single map from adapter name
to class. ``load_active_adapters`` is the one function the pipeline
calls at startup to resolve the ``[detected].active_adapters`` list
from ``project-hints.toml`` into concrete adapter instances, failing
fast before any LLM call on empty or unregistered names. New adapters
add one line to ``REGISTRY`` and immediately become available to
``dark-factory init``'s detection loop and the pipeline's dispatch.

Public surface and lifecycle policy: ``docs/ADAPTER-GOVERNANCE.md``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from darkfactory.adapters.base import (
    GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP,
    SEVERITIES,
    EcosystemAdapter,
    Finding,
    GateCommands,
    Severity,
)
from darkfactory.errors import DarkFactoryError

if TYPE_CHECKING:
    from darkfactory.architecture.project_hints import ProjectHints

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
#
# Adapter classes — not instances — keyed by the ``name`` each class
# advertises. Instantiation happens at ``load_active_adapters`` time
# with the project directory bound, so the registry itself stays
# stateless and import-cheap.
#
# Keeping this a module-level dict (not a class, not a context-manager)
# is deliberate. Callers already need to import from
# ``darkfactory.adapters``; adding a singleton registry class would be
# ceremony. A single dict is the smallest thing that correctly answers
# the only two questions the pipeline asks: "is NAME registered?" and
# "instantiate it for PROJECT_DIR."
#
# Lazy import of the concrete classes so this module stays cheap to
# import (e.g. from test fixtures that don't need any adapter).


def _build_registry() -> dict[str, type[EcosystemAdapter]]:
    """Import and return the canonical {name: class} mapping.

    Done in a function (not at module top) so importing
    ``darkfactory.adapters`` doesn't drag in every adapter's optional
    dependencies just to, e.g., type-hint ``EcosystemAdapter``. The
    result is cached into ``REGISTRY`` at module load.

    Invariant: every entry's key must equal ``cls.name``. A class
    registered under the wrong key would silently break dispatch —
    ``setup/init.py`` writes ``active_adapters`` using the *class's*
    ``name`` attribute, and a registry keyed under a different string
    would miss on lookup. The assertion here fails at import time if
    anyone registers an adapter under a mismatched alias, which is
    cheaper than discovering the bug at first dispatch.
    """
    from darkfactory.adapters.postgres import PostgresAdapter
    from darkfactory.adapters.python_fastapi import PythonFastApiAdapter
    from darkfactory.adapters.typescript import TypescriptNodeAdapter

    registry: dict[str, type[EcosystemAdapter]] = {
        TypescriptNodeAdapter.name: TypescriptNodeAdapter,
        PostgresAdapter.name: PostgresAdapter,
        PythonFastApiAdapter.name: PythonFastApiAdapter,
    }
    for key, cls in registry.items():
        assert key == cls.name, (
            f"adapter registry invariant violated: key {key!r} != "
            f"cls.name {cls.name!r}"
        )
    return registry


REGISTRY: dict[str, type[EcosystemAdapter]] = _build_registry()


def available_adapter_names() -> tuple[str, ...]:
    """Sorted tuple of registered adapter names.

    Sorted so error messages are deterministic — an unknown-adapter
    error that lists ``typescript-node, postgres`` on one run and
    ``postgres, typescript-node`` on the next is a minor but real
    annoyance in diffs and CI logs.
    """
    return tuple(sorted(REGISTRY))


def get_adapter_class(name: str) -> type[EcosystemAdapter]:
    """Look up an adapter class by name.

    Raises ``DarkFactoryError`` with the plan-mandated error text when
    the name is unregistered — the caller doesn't need to format its
    own message. All error paths go through this one function so the
    hint to "implement an adapter at darkfactory/adapters/<name>/"
    stays consistent.
    """
    try:
        return REGISTRY[name]
    except KeyError:
        available = ", ".join(available_adapter_names())
        raise DarkFactoryError(
            f"No adapter for ecosystem {name!r}. Available: {available}. "
            f"Install or implement an adapter at darkfactory/adapters/{name}/."
        ) from None


def load_active_adapters(
    hints: "ProjectHints",
    project_dir: Path,
    *,
    no_adapter: bool = False,
) -> list[EcosystemAdapter]:
    """Resolve ``hints.detected.active_adapters`` into adapter instances.

    Fails fast on empty or unregistered names unless ``no_adapter`` is
    True. Every adapter is instantiated with ``project_dir`` bound so
    downstream dispatch doesn't have to pass the path through every
    call. Preserves declaration order (not alphabetical) so adapter
    authors can reason about ordering effects in the architect prompt
    (addenda concatenate in list order).

    Args:
        hints: Loaded project-hints (the detected block carries the
               active_adapters tuple written at init time).
        project_dir: Project root, passed to each adapter's ``__init__``.
        no_adapter: Escape hatch for debugging. When True, returns ``[]``
                    and logs a single WARNING. Documented in the plan
                    as "degraded run — LLM agents only, no precheck,
                    no consistency rules"; not a production path.

    Raises:
        DarkFactoryError: if ``active_adapters`` is empty or contains a
                          name not in ``REGISTRY`` (and ``no_adapter``
                          is False). The error text names the missing
                          adapter and the available set, matching the
                          plan's acceptance criterion.
    """
    if no_adapter:
        log.warning(
            "--no-adapter: degraded run — precheck, consistency rules, "
            "and gate dispatch will be adapter-less. Not recommended "
            "for production; pipeline will skip all ecosystem-specific "
            "behavior."
        )
        return []

    names = hints.detected.active_adapters
    if not names:
        available = ", ".join(available_adapter_names())
        raise DarkFactoryError(
            f"No adapters declared for this project "
            f"([detected].active_adapters is empty). Run "
            f"`dark-factory init` to populate it, or pass "
            f"`--no-adapter` for a degraded run. "
            f"Available adapters: {available}."
        )

    # Resolve first so an unknown name aborts before any instantiation
    # side-effects (addendum file reads, etc.).
    resolved: list[type[EcosystemAdapter]] = [
        get_adapter_class(name) for name in names
    ]
    return [cls(project_dir=project_dir) for cls in resolved]


__all__ = [
    "EcosystemAdapter",
    "Finding",
    "GateCommands",
    "GOVERNANCE_ADDENDUM_NON_BLANK_LINE_CAP",
    "REGISTRY",
    "SEVERITIES",
    "Severity",
    "available_adapter_names",
    "get_adapter_class",
    "load_active_adapters",
]
