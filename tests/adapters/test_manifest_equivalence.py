"""Tests for the manifest_equivalence harness.

Unlike the contract harness, these tests prove the diff *itself* behaves
correctly — A4's acceptance is going to lean on this for non-deterministic
LLM output, so the tolerance policy must be precise.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict

import pytest
import yaml

from darkfactory.spec.manifest import (
    ArchitectureManifest,
    DataModel,
    DataModelField,
    Dependency,
    EnvironmentReq,
    FileEntry,
    RuntimeDecl,
    RuntimeServiceDecl,
)

from tests.adapters.manifest_equivalence import (
    MajorityResult,
    majority_equivalent,
    structural_diff,
    structural_diff_yaml,
)


# ---------------------------------------------------------------------------
# Fixture builders — minimal manifests for diff assertions
# ---------------------------------------------------------------------------


def _base_manifest() -> ArchitectureManifest:
    """A small but realistic manifest used as the diff baseline."""
    return ArchitectureManifest(
        version=1,
        spec="2026-03-28-database-foundation",
        spec_hash="abc123",
        language="node",
        file_map=[
            FileEntry(
                path="apps/api/src/db/migrate.ts",
                action="create",
                purpose="run drizzle migrations on startup",
                satisfies=["FR-1"],
            ),
        ],
        data_models=[
            DataModel(
                name="users",
                location="apps/api/src/db/schema.ts",
                fields=[
                    DataModelField(name="id", type="uuid", constraints=["pk"]),
                ],
            ),
        ],
        dependencies=[
            Dependency(package="drizzle-orm", version="0.44.0"),
            Dependency(package="zod", version="3.22.0"),
        ],
        environment=[
            EnvironmentReq(tool="postgres", min_version="15"),
            EnvironmentReq(tool="node", min_version="20"),
        ],
        runtime=RuntimeDecl(
            services=[
                RuntimeServiceDecl(name="postgres", image="postgres:15"),
            ],
            server_setup_cmd="cd apps/api && pnpm db:migrate",
            test_server_cmd="cd apps/api && pnpm dev",
            amendment_ops={
                "commands.setup": "cd apps/api && pnpm db:migrate",
            },
        ),
    )


# ---------------------------------------------------------------------------
# Identity & no-op tolerance
# ---------------------------------------------------------------------------


def test_identical_manifests_are_equivalent():
    a = _base_manifest()
    b = deepcopy(a)
    diff = structural_diff(a, b)
    assert diff.is_equivalent
    assert not diff.load_bearing
    assert not diff.tolerated


def test_purpose_field_change_is_tolerated():
    """``FileEntry.purpose`` is descriptive prose — phrasing changes don't count."""
    a = _base_manifest()
    b = deepcopy(a)
    b.file_map[0].purpose = "different prose for the same migration"
    diff = structural_diff(a, b)
    assert diff.is_equivalent, diff.summary()
    assert any(d.path.endswith(".purpose") for d in diff.tolerated)


def test_dependency_order_change_is_tolerated():
    """``dependencies`` is set-like — reordering produces zero diffs."""
    a = _base_manifest()
    b = deepcopy(a)
    b.dependencies.reverse()
    diff = structural_diff(a, b)
    assert diff.is_equivalent, diff.summary()
    # And no spurious tolerated entries either — set-like sort makes order moot.
    assert not diff.load_bearing
    assert not diff.tolerated


def test_environment_order_change_is_tolerated():
    a = _base_manifest()
    b = deepcopy(a)
    b.environment.reverse()
    diff = structural_diff(a, b)
    assert diff.is_equivalent, diff.summary()


# ---------------------------------------------------------------------------
# Load-bearing changes — must fail equivalence
# ---------------------------------------------------------------------------


def test_command_change_is_load_bearing():
    """The whole reason this harness exists: ``commands.setup`` flipping
    from ``pnpm db:migrate`` to ``drizzle-kit push`` is the failure mode
    the migrate-vs-push consistency rule was built to catch. The diff
    must surface it as load-bearing."""
    a = _base_manifest()
    b = deepcopy(a)
    b.runtime.amendment_ops["commands.setup"] = (
        "cd apps/api && npx drizzle-kit push"
    )
    diff = structural_diff(a, b)
    assert not diff.is_equivalent
    assert any(
        "amendment_ops" in d.path and "commands.setup" in d.path
        for d in diff.load_bearing
    ), diff.summary()


def test_language_change_is_load_bearing():
    a = _base_manifest()
    b = deepcopy(a)
    b.language = "python"
    diff = structural_diff(a, b)
    assert not diff.is_equivalent


def test_file_map_added_is_load_bearing():
    a = _base_manifest()
    b = deepcopy(a)
    b.file_map.append(
        FileEntry(path="apps/api/src/extra.ts", action="create", purpose="x")
    )
    diff = structural_diff(a, b)
    assert not diff.is_equivalent
    # The added file's path is load-bearing; its purpose is tolerated.
    assert any(d.path.startswith("file_map[1].path") for d in diff.load_bearing)
    assert any(
        d.path == "file_map[1].purpose" for d in diff.tolerated
    ), diff.summary()


def test_dependency_version_change_is_load_bearing():
    a = _base_manifest()
    b = deepcopy(a)
    b.dependencies[0] = Dependency(package="drizzle-orm", version="0.45.0")
    diff = structural_diff(a, b)
    assert not diff.is_equivalent


def test_service_image_change_is_load_bearing():
    a = _base_manifest()
    b = deepcopy(a)
    b.runtime.services[0] = RuntimeServiceDecl(name="postgres", image="postgres:16")
    diff = structural_diff(a, b)
    assert not diff.is_equivalent


# ---------------------------------------------------------------------------
# Mixed diffs — both buckets populated correctly
# ---------------------------------------------------------------------------


def test_mixed_diff_separates_buckets():
    a = _base_manifest()
    b = deepcopy(a)
    # One load-bearing change.
    b.runtime.amendment_ops["commands.setup"] = "cd apps/api && drizzle-kit push"
    # One tolerated change.
    b.file_map[0].purpose = "rephrased prose"
    diff = structural_diff(a, b)
    assert not diff.is_equivalent
    assert len(diff.load_bearing) >= 1
    assert len(diff.tolerated) >= 1


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------


def _to_yaml(manifest: ArchitectureManifest) -> str:
    """Round-trip a manifest through ``yaml.safe_dump``.

    Avoids the f-string-templating footgun (unquoted special chars,
    string-vs-int coercion noise) the previous handrolled emitter
    invited.

    NOTE — known asymmetry, not a bug for the symmetric A4 use case:
    ``asdict`` emits both ``runtime.amendment_ops`` *and* the legacy
    flat fields (``server_setup_cmd``, ``test_server_cmd``).
    ``parse_manifest`` ignores the YAML's ``amendment_ops`` and
    reconstructs it from the legacy flat fields via
    ``_LEGACY_FLAT_TO_PATH`` (manifest.py:562-576). So a single
    in-memory→YAML→in-memory round trip is not identity-preserving on
    ``runtime.amendment_ops``. We don't compare a manifest to its own
    round-trip here; we compare two YAML inputs that BOTH go through
    ``parse_manifest`` — i.e. the symmetric path A4 will exercise. If a
    future test wants exact in-memory round-trip, build the YAML with
    the canonical legacy-aligned form instead of dumping asdict().
    """
    return yaml.safe_dump(asdict(manifest), sort_keys=False)


def test_structural_diff_yaml_with_minimal_input():
    a = _base_manifest()
    b = deepcopy(a)
    yaml_a = _to_yaml(a)
    yaml_b = _to_yaml(b)
    diff = structural_diff_yaml(yaml_a, yaml_b)
    assert diff.is_equivalent, diff.summary()


def test_structural_diff_yaml_parse_error_fails_loud():
    diff = structural_diff_yaml("not: valid: yaml: here", "version: 1\nfile_map: []\n")
    assert not diff.is_equivalent
    assert any(d.path.startswith("<parse") for d in diff.load_bearing)


# ---------------------------------------------------------------------------
# N-of-N majority — A4 sampling policy
# ---------------------------------------------------------------------------


def test_majority_3of3_all_equivalent():
    base = _base_manifest()
    result = majority_equivalent([base, deepcopy(base), deepcopy(base)],
                                 [deepcopy(base), deepcopy(base), deepcopy(base)])
    assert result.is_equivalent
    assert result.equivalent_pairs == 3
    assert result.threshold == 2  # strict majority of 3


def test_majority_3of3_two_equivalent_passes():
    base = _base_manifest()
    bad = deepcopy(base)
    bad.runtime.amendment_ops["commands.setup"] = "drizzle-kit push"
    result = majority_equivalent([base, deepcopy(base), deepcopy(base)],
                                 [deepcopy(base), bad, deepcopy(base)])
    assert result.is_equivalent
    assert result.equivalent_pairs == 2


def test_majority_3of3_one_equivalent_fails():
    base = _base_manifest()
    bad = deepcopy(base)
    bad.runtime.amendment_ops["commands.setup"] = "drizzle-kit push"
    result = majority_equivalent([base, deepcopy(base), deepcopy(base)],
                                 [bad, bad, deepcopy(base)])
    assert not result.is_equivalent
    assert result.equivalent_pairs == 1


def test_majority_unequal_lengths_raises():
    base = _base_manifest()
    with pytest.raises(ValueError):
        majority_equivalent([base], [base, base])


def test_majority_empty_raises():
    with pytest.raises(ValueError):
        majority_equivalent([], [])


def test_majority_5of5_with_explicit_threshold():
    """A4 escalation: 5 runs each side, threshold=3."""
    base = _base_manifest()
    bad = deepcopy(base)
    bad.language = "python"
    lefts = [deepcopy(base) for _ in range(5)]
    rights = [deepcopy(base), deepcopy(base), deepcopy(base), bad, bad]
    result = majority_equivalent(lefts, rights, threshold=3)
    assert result.is_equivalent
    assert result.equivalent_pairs == 3


# ---------------------------------------------------------------------------
# Catalog-drift guard — if a new tolerated suffix is added, this test
# should be updated, forcing a deliberate decision.
# ---------------------------------------------------------------------------


def test_tolerated_suffixes_are_documented():
    from tests.adapters.manifest_equivalence import _TOLERATED_SUFFIXES
    expected = {".notes", ".rationale", ".purpose", ".note",
                ".existing_summary"}
    assert set(_TOLERATED_SUFFIXES) == expected, (
        "Tolerated path suffixes changed. Update the docstring at the top of "
        "manifest_equivalence.py and this test together."
    )


def test_set_like_lists_are_documented():
    from tests.adapters.manifest_equivalence import _SET_LIKE_LIST_PATHS
    expected = {
        "dependencies",
        "environment",
        "file_map",
        "data_models",
        "data_models[*].fields",
        "api_contracts",
        "runtime.services",
    }
    assert set(_SET_LIKE_LIST_PATHS) == expected, (
        "Set-like list paths changed. Update the docstring at the top of "
        "manifest_equivalence.py and this test together."
    )
