"""Pin the A5 adapter registry contract.

The registry is the one place the pipeline asks "which adapters are
registered?" and "instantiate the active ones." If its surface drifts
— a rename, a silent error-message change, an adapter that fails to
register — the failure happens at pipeline startup, before any LLM
call. These tests pin the behaviors that keep that startup stable:

1. Every shipped adapter class is registered under the name it
   advertises on its ``name`` attribute. A rename inside the adapter
   class must propagate here for free.
2. The available-adapter list is sorted for deterministic error
   messages.
3. ``get_adapter_class`` raises ``DarkFactoryError`` with the
   plan-verbatim error text when asked for an unknown adapter.
4. ``load_active_adapters`` fails fast on empty / unknown names, and
   succeeds with instances when the names are valid.
5. ``--no-adapter`` produces an empty list and emits exactly one
   WARNING — the "degraded run" signal the plan requires to be
   explicit.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pytest

from darkfactory.adapters import (
    REGISTRY,
    EcosystemAdapter,
    available_adapter_names,
    get_adapter_class,
    load_active_adapters,
)
from darkfactory.adapters.postgres import PostgresAdapter
from darkfactory.adapters.typescript import TypescriptNodeAdapter
from darkfactory.architecture.project_hints import DetectedFacts, ProjectHints
from darkfactory.errors import DarkFactoryError


# ---------------------------------------------------------------------------
# Registry shape
# ---------------------------------------------------------------------------


class TestRegistryShape:
    def test_registry_contains_shipped_adapters(self) -> None:
        """A rename of ``PostgresAdapter.name`` / ``TypescriptNodeAdapter.name``
        must propagate here — the registry is keyed by the class's own
        ``name`` attribute."""
        assert REGISTRY[TypescriptNodeAdapter.name] is TypescriptNodeAdapter
        assert REGISTRY[PostgresAdapter.name] is PostgresAdapter

    def test_every_registered_class_is_ecosystem_adapter_subclass(self) -> None:
        """Catches a registration mistake (e.g., registering a factory
        function instead of the class) before it breaks dispatch."""
        for name, cls in REGISTRY.items():
            assert issubclass(cls, EcosystemAdapter), (
                f"registered {name!r} is not an EcosystemAdapter subclass"
            )

    def test_registry_key_matches_class_name_attribute(self) -> None:
        """The registration key must equal the class's own ``name``. If
        someone registers ``{"pg": PostgresAdapter}`` by accident,
        dispatch breaks silently — detect.py writes ``"postgres"``
        into active_adapters, and the registry lookup misses."""
        for name, cls in REGISTRY.items():
            assert cls.name == name, (
                f"registry key {name!r} does not match cls.name {cls.name!r}"
            )

    def test_available_names_is_sorted(self) -> None:
        """Deterministic error messages. If this drifts, CI diffs
        start flipping on adapter-list ordering."""
        names = available_adapter_names()
        assert list(names) == sorted(names)


# ---------------------------------------------------------------------------
# get_adapter_class — error formatting
# ---------------------------------------------------------------------------


class TestGetAdapterClass:
    def test_known_name_returns_class(self) -> None:
        assert get_adapter_class("postgres") is PostgresAdapter
        assert get_adapter_class("typescript-node") is TypescriptNodeAdapter

    def test_unknown_name_raises_with_plan_verbatim_text(self) -> None:
        """Plan's acceptance criterion (``A5 > Fail-fast``) names the
        exact error shape. Pinning the phrasing ensures the CLI
        remediation stays discoverable.

        The available-list portion is checked dynamically against
        ``available_adapter_names()`` so adding a third adapter doesn't
        flip this test red for every unrelated PR — the *format* of
        the enumeration is what matters, not the specific names.
        """
        with pytest.raises(DarkFactoryError) as exc:
            get_adapter_class("rust")
        msg = str(exc.value)
        # Adapter name + remediation hint (these are plan-verbatim):
        assert "No adapter for ecosystem 'rust'" in msg
        assert "darkfactory/adapters/rust/" in msg
        # Available set enumerated in sorted order — derived from the
        # live registry so new adapters don't break this assertion:
        expected_available = ", ".join(available_adapter_names())
        assert f"Available: {expected_available}" in msg


# ---------------------------------------------------------------------------
# load_active_adapters — the main pipeline entry
# ---------------------------------------------------------------------------


class TestLoadActiveAdapters:
    def test_valid_names_return_instances_with_project_dir(
        self, tmp_path: Path
    ) -> None:
        """Each adapter gets ``project_dir`` bound so downstream
        dispatch doesn't have to pass the path through every call."""
        hints = ProjectHints(
            detected=DetectedFacts(
                active_adapters=("typescript-node", "postgres"),
            ),
        )
        result = load_active_adapters(hints, tmp_path)

        assert len(result) == 2
        assert [a.name for a in result] == ["typescript-node", "postgres"]
        # project_dir bound:
        assert result[0].project_dir == tmp_path
        assert result[1].project_dir == tmp_path
        # Order preserves declaration (not alphabetical) so architect
        # addendum concatenation is predictable for authors.
        assert isinstance(result[0], TypescriptNodeAdapter)
        assert isinstance(result[1], PostgresAdapter)

    def test_empty_list_raises_with_remediation_hint(
        self, tmp_path: Path
    ) -> None:
        """Plan: `dark-factory run` against a project with no adapter
        declared aborts with the registry error and a remediation hint."""
        hints = ProjectHints.empty()
        with pytest.raises(DarkFactoryError) as exc:
            load_active_adapters(hints, tmp_path)
        msg = str(exc.value)
        assert "No adapters declared" in msg
        # Both remediation paths mentioned so the user can pick:
        assert "dark-factory init" in msg
        assert "--no-adapter" in msg
        # Available set enumerated — derived from the live registry so
        # a new adapter doesn't break this assertion:
        for name in available_adapter_names():
            assert name in msg

    def test_unknown_name_raises_before_instantiation(
        self, tmp_path: Path
    ) -> None:
        """If ``active_adapters`` contains one good name and one bad,
        the unknown name must abort before any instantiation
        side-effects (addendum reads, etc.). Otherwise a partial
        construction could emit misleading errors."""
        hints = ProjectHints(
            detected=DetectedFacts(active_adapters=("postgres", "rust")),
        )
        with pytest.raises(DarkFactoryError) as exc:
            load_active_adapters(hints, tmp_path)
        assert "rust" in str(exc.value)

    def test_no_adapter_flag_returns_empty_list_and_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """``--no-adapter`` is the documented escape hatch. Plan calls
        for "explicitly logged as a degraded run" — a silent empty
        list would let a user enable the flag and forget they did.
        The WARNING level makes it surface in normal log output."""
        hints = ProjectHints.empty()
        with caplog.at_level(logging.WARNING, logger="darkfactory.adapters"):
            result = load_active_adapters(hints, tmp_path, no_adapter=True)

        assert result == []
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        assert any("degraded run" in r.getMessage() for r in warnings), (
            "no_adapter must announce itself — silent degraded runs "
            "are exactly what the plan forbids"
        )

    def test_no_adapter_flag_bypasses_empty_check(
        self, tmp_path: Path
    ) -> None:
        """The empty-list fail-fast must defer to ``no_adapter``. If it
        didn't, ``--no-adapter`` would be useless on the one project
        state where someone actually wants it — a freshly-init'd tree
        with no adapters declared yet."""
        hints = ProjectHints.empty()
        # Must not raise:
        result = load_active_adapters(hints, tmp_path, no_adapter=True)
        assert result == []

    def test_no_adapter_flag_bypasses_unknown_check(
        self, tmp_path: Path
    ) -> None:
        """``--no-adapter`` also bypasses the unknown-name check —
        useful for bisecting a broken registry state without having
        to hand-edit project-hints.toml."""
        hints = ProjectHints(
            detected=DetectedFacts(active_adapters=("rust",)),
        )
        # Must not raise:
        result = load_active_adapters(hints, tmp_path, no_adapter=True)
        assert result == []
